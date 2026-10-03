"""Whole-context prose translation with exact literal restoration and no truncation."""
import hashlib
import json
import re
from dataclasses import asdict
from .translation_types import (MODEL_VERSION, PROTECTION_VERSION, TranslationItem,
                                TranslationPart, TranslationResult)

_TECH = r'(?:StarTrail|GitHub Radar|GitHub|README|Star|stars|CPU|GPU|API|AI|LLM|RAG|MCP|SQLite|Python|PyPI|Apache|Codex|OpenAI|OpenClaw|OpenShell|PageIndex|TypeScript|JavaScript|Windows|Linux|macOS|Rust|Go|Docker|Podman|Kubernetes|Apple Silicon|WSL|Helm|npm|pip|JSON|MIT|CLI|SDK|2N|N)'
_LITERALS = (r'(?P<fence>`{3,}|~{3,})[^\n]*\n[\s\S]*?(?P=fence)|'
             r'`+[^`\n]+`+|[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]+|https?://[^\s<>\[\]()"\'，。！？；：]+|'
             r'(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![A-Za-z0-9_.-])|'
             r'(?<![A-Za-z0-9_])' + _TECH + r'(?![A-Za-z0-9_])|'
             r'(?<![A-Za-z0-9_])\d+(?:[-/:.,]\d+)*%?(?![A-Za-z0-9_])')
_GLOSSARY = {
    'zh': ((r'daily\s+star\s+growth', '每日新增 Star'),
           (r'(?<![A-Za-z])content-curation(?![A-Za-z])', '内容精选'),
           (r'(?<![A-Za-z])agentic(?![A-Za-z])', '智能体'),
           (r'(?<![A-Za-z])non-autoregressive(?![A-Za-z])', '非自回归'),
           (r'(?<![A-Za-z])agents?(?![A-Za-z])', '智能体'),
           (r'(?<![A-Za-z])runtime(?![A-Za-z])', '运行环境'),
           (r'(?<![A-Za-z])credentials?(?![A-Za-z])', '凭据'),
           (r'(?<![A-Za-z])kernel(?![A-Za-z])', '内核'),
           (r'(?<![A-Za-z])formal\s+verification(?![A-Za-z])', '形式化验证'),
           (r'(?<![A-Za-z])bug\s+reports?(?![A-Za-z])', '缺陷报告'),
           (r'(?<![A-Za-z])reasoning-based(?![A-Za-z])', '基于推理'),
           (r'star\s+growth', 'Star 增长'),
           (r'(?:the\s+)?projects\s+you\s+follow', '关注的项目'),
           (r'followed\s+projects', '关注的项目')),
    'en': ((r'每日新增\s*Star', 'daily Star growth'),
           (r'关注的项目', 'followed projects'),),
}


def translation_cache_key(item: TranslationItem, target: str) -> str:
    value = json.dumps({'parts': [asdict(p) for p in item.parts], 'target': target,
                       'model': MODEL_VERSION, 'protection': PROTECTION_VERSION},
                      sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _chunks(text, target, encode):
    """Greedy whole-word chunks; oversized unbreakable input is explicitly rejected."""
    words = re.findall(r'\S+\s*|\s+', text)
    if ''.join(words) != text:
        raise ValueError('无法完整分段')
    output, current = [], ''
    for word in words:
        if len(encode(word, target)) > 256:
            raise ValueError('长文本无法安全分段')
        if current and len(encode(current + word, target)) > 256:
            output.append(current)
            current = ''
        current += word
    if current:
        output.append(current)
    return output


def translate_item(item: TranslationItem, target: str, infer, encode) -> TranslationResult:
    if target not in ('zh', 'en'):
        raise ValueError('翻译语言无效')
    source = ''.join(p.text for p in item.parts)
    prefix = next((p for p in ('X', 'Y', 'Z') if not re.search(p + r'\d+', source)), None)
    if prefix is None:
        return TranslationResult(item.id, 'original', item.parts, '原文标记冲突，显示原文')
    protected, runs, foreign = [], [], False
    glosses = _GLOSSARY[target]
    pattern = re.compile('|'.join('(?:' + p + ')' for p, _ in glosses) + '|' + _LITERALS
                         + (r'|[\u3400-\u9fff][\u3400-\u9fff，。；：！？、]*' if target == 'zh'
                            else r'|[A-Za-z][A-Za-z0-9 ,.;:!?()\-\n]*'), re.I)

    def protect(part):
        protected.append(part)
        return prefix + str(len(protected)-1)

    def replace_match(match):
        nonlocal foreign
        value = match[0]
        for regex, translation in glosses:
            if re.fullmatch(regex, value, re.I):
                foreign = True
                value = translation
                break
        return protect(TranslationPart('text', value))

    for part in item.parts:
        if part.kind == 'literal':
            runs.append(protect(part))
        else:
            masked = pattern.sub(replace_match, part.text)
            remaining = re.sub(prefix + r'\d+', '', masked)
            foreign |= bool(re.search(r'[A-Za-z]' if target == 'zh' else r'[\u3400-\u9fff]', remaining))
            runs.append(masked)
    if not foreign:
        return TranslationResult(item.id, 'original', item.parts)
    try:
        translated = []
        masked_text=''.join(runs)
        remainder=re.sub(prefix+r'\d+','',masked_text)
        needs_inference=bool(re.search(r'[A-Za-z]' if target=='zh' else r'[\u3400-\u9fff]',remainder))
        if not needs_inference:
            translated.append(masked_text)
        for chunk in _chunks(masked_text, target, encode) if needs_inference else ():
            value = infer(chunk, target)
            if not isinstance(value, str) or not value.strip() or '▁' in value:
                raise ValueError('译文为空或分词未完成')
            leading = chunk[:len(chunk)-len(chunk.lstrip())]
            trailing = chunk[len(chunk.rstrip()):]
            translated.append(leading + value.strip() + trailing)
        text = ''.join(translated)
        marker = re.compile(prefix + r'\d+')
        expected = [prefix + str(i) for i in range(len(protected))]
        if marker.findall(text) != expected:
            # The model is never trusted to repair or reorder protected values.
            # Retry only the intervening prose, with every protected part local.
            retried=[]
            for segment in re.split('('+prefix+r'\d+)',masked_text):
                if not segment:continue
                if marker.fullmatch(segment):
                    retried.append(protected[int(segment[1:])]);continue
                foreign_segment=re.search(r'[A-Za-z]' if target=='zh' else r'[\u3400-\u9fff]',segment)
                values=[]
                for chunk in _chunks(segment,target,encode) if foreign_segment else (segment,):
                    value=infer(chunk,target) if foreign_segment else chunk
                    if not isinstance(value,str) or not value.strip() and chunk.strip() or '▁' in value or marker.search(value):
                        raise ValueError('正文重试未完成')
                    values.append(chunk[:len(chunk)-len(chunk.lstrip())]+value.strip()+chunk[len(chunk.rstrip()):] if chunk.strip() else chunk)
                retried.append(TranslationPart('text',''.join(values)))
            return TranslationResult(item.id,'translated',tuple(retried))
        parts, end = [], 0
        for match in marker.finditer(text):
            if match.start() > end:
                parts.append(TranslationPart('text', text[end:match.start()]))
            parts.append(protected[int(match[0][1:])])
            end = match.end()
        if end < len(text):
            parts.append(TranslationPart('text', text[end:]))
        if not parts:
            raise ValueError('译文为空')
        return TranslationResult(item.id, 'translated', tuple(parts))
    except Exception:
        # No guessed restoration and no exception text containing model paths/content.
        return TranslationResult(item.id, 'original', item.parts, '翻译未完成，显示原文')
