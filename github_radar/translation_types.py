"""Validated, text-only messages for the private offline translation service."""
from dataclasses import dataclass

MODEL_VERSION = 'argos-1.9-ct2-4.8.2-sp-0.2.2'
PROTECTION_VERSION = '7'


@dataclass(frozen=True, slots=True)
class TranslationPart:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class TranslationItem:
    id: str
    parts: tuple[TranslationPart, ...]


@dataclass(frozen=True, slots=True)
class TranslationResult:
    id: str
    status: str
    parts: tuple[TranslationPart, ...]
    reason: str | None = None


def validate_request(payload: dict) -> tuple[str, tuple[TranslationItem, ...]]:
    if (not isinstance(payload, dict) or set(payload) != {'target', 'items'}
            or payload['target'] not in ('zh', 'en')
            or not isinstance(payload['items'], list) or len(payload['items']) > 128):
        raise ValueError('翻译请求无效')
    items, ids, total = [], set(), 0
    for value in payload['items']:
        if not isinstance(value, dict) or set(value) != {'id', 'parts'}:
            raise ValueError('翻译条目无效')
        identity = value['id']
        if (not isinstance(identity, str) or not identity.strip() or len(identity) > 64
                or any(ord(c) < 32 for c in identity) or identity in ids
                or not isinstance(value['parts'], list)):
            raise ValueError('翻译条目无效')
        parts, length = [], 0
        for part in value['parts']:
            if (not isinstance(part, dict) or set(part) != {'kind', 'text'}
                    or part['kind'] not in ('text', 'literal') or not isinstance(part['text'], str)):
                raise ValueError('翻译片段无效')
            length += len(part['text'])
            parts.append(TranslationPart(part['kind'], part['text']))
        total += length
        if length > 16384 or total > 65536:
            raise ValueError('翻译内容太长，请分批读取')
        ids.add(identity)
        items.append(TranslationItem(identity, tuple(parts)))
    return payload['target'], tuple(items)
