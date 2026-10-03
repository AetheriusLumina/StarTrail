"""Conservative Markdown extraction: quote author sections, never invent claims."""
import re
from html.parser import HTMLParser
from .readme_types import ReadmeDocument, ReadmeSection, ReadmeView

_CATEGORIES = (
    ('purpose', '用途', ('overview', 'introduction', 'about', 'description', '项目介绍', '简介', '概述', '介绍')),
    ('problem', '解决的问题', ('motivation', 'problem', 'why', '解决的问题', '背景', '动机')),
    ('users', '适合谁', ('audience', 'who is this for', 'who should use', '适合谁', '适用人群', '目标用户')),
    ('features', '核心功能', ('features', 'key features', 'highlights', '功能', '核心功能', '特性', '主要功能')),
    ('scenarios', '典型场景', ('use cases', 'usage', 'examples', '应用场景', '使用场景', '典型场景', '使用示例')),
    ('prerequisites', '使用门槛', ('requirements', 'prerequisites', 'installation', 'getting started', 'quick start', '安装', '快速开始', '开始使用', '环境要求', '使用要求')),
)
_IGNORE = re.compile(r'^(table of contents|contents|toc|sponsors?|sponsorship|contributing|license|目录|赞助|贡献|许可)(\b|$)', re.I)


class _AuthorHTML(HTMLParser):
    """Convert author formatting into text/Markdown, never executable DOM."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.links = [], []
        self.pre_parts, self.code_parts = None, None
        self.table_rows=0;self.table_cells=0;self.cell_start=None

    def handle_starttag(self, tag, attrs):
        attributes=dict(attrs)
        if tag=='table':self.output.append('\n\n');self.table_rows=0
        elif tag=='tr':self.output.append('\n| ');self.table_cells=0
        elif tag in ('th','td'):self.cell_start=len(self.output);self.table_cells+=1
        elif tag=='a':
            href=attributes.get('href') or ''
            safe=not re.search(r'[\s<>)]',href) and not re.match(r'(?i)(javascript|data|vbscript):',href)
            self.links.append(href if safe else None)
            if safe: self.output.append('[')
        elif tag=='code':
            if self.pre_parts is None: self.code_parts=[]
        elif tag=='pre': self.pre_parts=[]
        elif tag=='br': self.output.append('\n')
        elif tag in ('p','div','section','ul','ol'): self.output.append('\n\n')
        elif tag=='li': self.output.append('\n- ')
        elif re.fullmatch(r'h[1-6]',tag): self.output.append('\n\n'+'#'*int(tag[1])+' ')
        elif tag=='img':
            # Keep alt text, without loading third-party images or inventing links.
            self.output.append(attributes.get('alt') or '')
        elif tag not in ('strong','b','em','i','span','s','details','summary','kbd','table','tr','td','th','tbody','thead','hr','picture','source','figure','figcaption'):
            self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in ('th','td') and self.cell_start is not None:
            text=''.join(self.output[self.cell_start:]).strip().replace('|','\\|').replace('\n',' ')
            self.output[self.cell_start:]=[text,' | '];self.cell_start=None
        elif tag=='tr':
            self.output.append('\n')
            if self.table_rows==0:self.output.append('| '+' | '.join(['---']*self.table_cells)+' |\n')
            self.table_rows+=1
        elif tag=='table':self.output.append('\n\n')
        elif tag=='a' and self.links:
            href=self.links.pop()
            if href is not None: self.output.append(']('+href+')')
        elif tag=='code' and self.pre_parts is None and self.code_parts is not None:
            body=''.join(self.code_parts)
            delimiter='`'*(max((len(run) for run in re.findall(r'`+',body)),default=0)+1)
            padding=' ' if body.startswith('`') or body.endswith('`') or (body.startswith(' ') and body.endswith(' ')) else ''
            self.output.append(delimiter+padding+body+padding+delimiter)
            self.code_parts=None
        elif tag=='pre' and self.pre_parts is not None:
            body=''.join(self.pre_parts)
            fence='`'*max(3,max((len(run) for run in re.findall(r'`+',body)),default=0)+1)
            self.output.append('\n\n'+fence+'\n'+body+'\n'+fence+'\n\n')
            self.pre_parts=None
        elif tag in ('p','div','section','ul','ol') or re.fullmatch(r'h[1-6]',tag): self.output.append('\n\n')
        elif tag=='br': self.output.append('\n')

    def handle_data(self, data):
        target=self.pre_parts if self.pre_parts is not None else self.code_parts if self.code_parts is not None else self.output
        target.append(data)


def _author_html(text):
    parser=_AuthorHTML()
    # Backtick examples are author code, not formatting to interpret.
    for part in re.split(r'(`+[^`\n]+`+)',text):
        if part.startswith('`'): parser.handle_data(part)
        else: parser.feed(part)
    parser.close()
    return ''.join(parser.output)


def _blocks(text):
    """Keep paragraphs and fenced code intact, including internal blank lines."""
    result, lines, fence = [], [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        if not line.strip() and fence is None:
            if lines:
                result.append('\n'.join(lines).strip()); lines = []
        else:
            lines.append(line)
    if lines and fence is None:
        result.append('\n'.join(lines).strip())
    return result


def _bounded(text, limit):
    kept = []
    for block in _blocks(text):
        if len('\n\n'.join(kept + [block])) > limit:
            continue  # Skip oversize blocks rather than cut an instruction in half.
        kept.append(block)
    return '\n\n'.join(kept)


def _clean_non_code(text):
    """Remove embedded HTML outside code; fenced examples remain author text."""
    result, outside, fence = [], [], None
    def flush():
        prose = '\n'.join(outside)
        prose = re.sub(r'<!--.*?-->', '', prose, flags=re.S)
        prose = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', prose, flags=re.I | re.S)
        result.append(_author_html(prose))
        outside.clear()
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence is None and marker:
            flush()
            fence = marker[1]
            result.append(line)
        elif fence is not None:
            result.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        else:
            outside.append(line)
    flush()
    return '\n'.join(result)


def _navigation_only(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(re.fullmatch(r'\s*(?:[-*+]|\d+[.)])\s+\[[^\]]+\]\([^\n]+\)\s*', line) for line in lines)


def _sections(text):
    text = _clean_non_code(text)
    sections, heading, lines, fence = [], None, [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None: fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence): fence = None
        title = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$', line) if fence is None else None
        if title:
            sections.append((heading, '\n'.join(lines))); heading, lines = title[1], []
        elif fence is not None or not (re.search(r'!\[', line) or re.match(r'^\s*<[^>]+>\s*$', line)):
            lines.append(line)
    sections.append((heading, '\n'.join(lines)))
    return [(title, body.strip()) for title, body in sections
            if body.strip() and not _IGNORE.match(title or '')]


def extract_readme(document: ReadmeDocument) -> ReadmeView:
    sections = _sections(document.text)
    output, selected = [], []
    for kind, label, names in _CATEGORIES:
        match = next(((heading, text) for heading, text in sections
                      if heading and heading.strip().casefold().rstrip(':：') in names), None)
        if match is None and kind == 'purpose':
            # Only introductory prose, before the first named subsection.
            intro = re.split(r'^ {0,3}#{2,6}\s', document.text, maxsplit=1, flags=re.M)[0]
            match = next(((heading, text) for heading, text in _sections(intro)
                          if not _navigation_only(text)), None)
        heading, text = match if match else (None, '')
        output.append(ReadmeSection(kind, label, _bounded(text, 800), heading if text else None))
        if match and match not in selected: selected.append(match)
    chunks = []
    for heading, text in selected:
        body = _bounded(text, 3900)
        block = (f'## {heading}\n\n' if heading else '') + body
        if body and len('\n\n'.join(chunks + [block])) <= 12000: chunks.append(block)
    return ReadmeView(document, tuple(output), '\n\n'.join(chunks), 'ready',
                      'README 超出读取上限，仅展示已读取部分。' if document.truncated else None,
                      _clean_non_code(document.text))
