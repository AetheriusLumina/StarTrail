"""Prepare selected repositories with bounded CPU batches and persistent text cache."""
import re
import threading
from collections import deque
from urllib.parse import urljoin, urlsplit
from .translation_types import TranslationItem, TranslationPart


def _js_length(text):
    return len(text.encode('utf-16-le')) // 2


def _chunks(parts):
    current, size = [], 0
    for part in parts:
        texts = [part.text]
        if part.kind == 'text':
            texts = []
            text = part.text
            while _js_length(text) > 4096:
                prefix = text.encode('utf-16-le')[:8194].decode('utf-16-le', errors='ignore')
                space = prefix.rfind(' ')
                if space >= 0 and _js_length(prefix[:space]) >= 2048: piece = prefix[:space+1]
                else: piece = text.encode('utf-16-le')[:8192].decode('utf-16-le', errors='ignore')
                texts.append(piece); text = text[len(piece):]
            if text: texts.append(text)
        for text in texts:
            if size + _js_length(text) > 8192 and current:
                yield tuple(current); current, size = [], 0
            current.append(TranslationPart(part.kind, text)); size += _js_length(text)
    if current: yield tuple(current)


def _inline(text, base, target):
    text = re.sub(r'\[!\[([^\]]*)\]\([^\s)]+\)\]\(([^\s)]+)\)', r'[\1](\2)', text)
    pattern = re.compile(r'(?<!`)(`+)([\s\S]*?)(?<!`)\1(?!`)|!?\[([^\]]+)\]\(([^\s)]+)\)')
    parts, links, end = [], [], 0
    for match in pattern.finditer(text):
        if match.start() > end: parts.append(TranslationPart('text', text[end:match.start()]))
        if match[1]:
            value = match[2]
            if value.startswith(' ') and value.endswith(' ') and value.strip(): value = value[1:-1]
            parts.append(TranslationPart('literal', value))
        else:
            value, url = match[3], urlsplit(urljoin(base or '', match[4]))
            if url.scheme in ('http', 'https') and url.netloc and not url.username and not url.password:
                arrow = (' (图片链接)' if target == 'zh' else ' (Image link)') if match[0].startswith('!') else ' ↗'
                parts.append(TranslationPart('literal', value + arrow))
                links.append((TranslationPart('text', value), TranslationPart('literal', arrow)))
            else: parts.append(TranslationPart('text', value))
        end = match.end()
    if end < len(text): parts.append(TranslationPart('text', text[end:]))
    # The DOM snapshots very large protected nodes as stable placeholders.
    literals = 0
    for index, part in enumerate(parts):
        if part.kind == 'literal':
            if _js_length(part.text) > 4096: parts[index] = TranslationPart('literal', f'⟦protected-{literals}⟧')
            literals += 1
    return [tuple(parts), *links]


def _cells(line):
    result, cell, ticks, index = [], '', 0, 0
    while index < len(line):
        char = line[index]
        if char == '\\' and line[index:index+2] == '\\|': cell += '|'; index += 2; continue
        if char == '`':
            run = 1
            while line[index+run:index+run+1] == '`': run += 1
            ticks = 0 if ticks == run else ticks or run
            cell += '`' * run; index += run; continue
        if char == '|' and not ticks: result.append(cell.strip()); cell = ''
        else: cell += char
        index += 1
    result.append(cell.strip())
    if result and not result[0]: result.pop(0)
    if result and not result[-1]: result.pop()
    return result


def markdown_items(text, base, target):
    """Mirror the supported DOM Markdown blocks, excluding fenced code."""
    lines, paragraph, fence, index, identity = text.split('\n'), [], None, 0, 0
    def inline(value):
        nonlocal identity
        for parts in _inline(value, base, target):
            for chunk in _chunks(parts):
                if any(p.kind == 'text' and p.text.strip() for p in chunk):
                    identity += 1
                    yield TranslationItem(f'm{identity}', chunk)
    while index < len(lines):
        line = lines[index]; index += 1
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence): fence = None
            continue
        if marker:
            if paragraph: yield from inline('\n'.join(paragraph)); paragraph = []
            fence = marker[1]; continue
        separators = _cells(lines[index]) if index < len(lines) else []
        if '|' in line and len(separators) >= 2 and all(re.fullmatch(r':?-{3,}:?', c) for c in separators):
            if paragraph: yield from inline('\n'.join(paragraph)); paragraph = []
            for cell in _cells(line): yield from inline(cell)
            index += 1
            while index < len(lines) and lines[index].strip() and '|' in lines[index]:
                for cell in _cells(lines[index]): yield from inline(cell)
                index += 1
            continue
        heading = re.match(r'^#{1,6}\s+(.+)', line)
        bullet = re.match(r'^\s*([-*+]|\d+[.)])\s+(.+)', line)
        if heading or bullet or not line.strip():
            if paragraph: yield from inline('\n'.join(paragraph)); paragraph = []
            if heading or bullet: yield from inline(heading[1] if heading else bullet[2])
        else: paragraph.append(line)
    if paragraph: yield from inline('\n'.join(paragraph))


def project_items(store, view, repo_id, target):
    repository = store.repositories_for_ids([repo_id]).get(repo_id)
    if repository is None: return
    identity = 0
    texts = [repository.description or '', *repository.topics]
    selected = store.load_ai_model() or store.load_ai_active_model()
    recommendation = store.latest_recommendation_for_repo(repo_id)
    saved = None
    if recommendation:
        match = store.latest_explanation(repo_id, recommendation.keyword_id, selected)
        if match: saved = (*match, selected)
        else: saved = store.latest_explanation_any_model(repo_id, recommendation.keyword_id)
    if saved is None: saved = store.latest_explanation_for_repo(repo_id, selected)
    if saved:
        insight = getattr(saved[0], target, None) or saved[0].zh
        texts += [getattr(insight, name, '') or '' for name in ('problem', 'prerequisites', 'scenarios', 'users')]
        texts += list(insight.highlights[:3])
    for text in texts:
        if text.strip():
            for parts in _chunks((TranslationPart('text', text),)):
                identity += 1; yield TranslationItem(f'p{identity}', parts)
    base = view.document.source_url if view.document else None
    if base and re.fullmatch(r'https://github\.com/[^/]+/[^/#]+(?:#readme)?', base):
        base = base.removesuffix('#readme') + '/blob/HEAD/README.md'
    purpose = next((s.text for s in view.sections if s.kind == 'purpose'), '')
    # Full author Markdown is also cached; the compact purpose can differ in segmentation.
    for source in (purpose, view.full_markdown):
        for item in markdown_items(source, base, target):
            identity += 1; yield TranslationItem(f'p{identity}', item.parts)


class ProjectPretranslator:
    def __init__(self, store, readme, translation):
        self.store, self.readme, self.translation = store, readme, translation
        self._stop, self._wake = threading.Event(), threading.Event()
        self._lock, self._queue = threading.Lock(), deque()
        self._thread, self._job = None, None

    def enqueue(self, ids):
        with self._lock:
            if self._stop.is_set(): return
            for repo_id in dict.fromkeys(ids):
                if repo_id not in self._queue: self._queue.append(repo_id)
            if self._queue and self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True); self._thread.start()
            self._wake.set()

    def _work(self):
        while not self._stop.is_set():
            self._wake.wait(.1)
            with self._lock:
                repo_id = self._queue.popleft() if self._queue else None
                if repo_id is None: self._wake.clear()
            if repo_id is not None: self.run([repo_id])

    def _batch(self, target, items):
        if not items or self._stop.is_set(): return
        from .translation_service import TranslationBusy
        while not self._stop.is_set():
            try: job = self.translation.submit(target, tuple(items)); break
            except TranslationBusy:
                if self._stop.wait(.1): return
        else: return
        self._job = job['job_id']
        while job['status'] in ('queued', 'running') and not self._stop.wait(.05):
            job = self.translation.get(job['job_id']) or {'status': 'error'}
        if self._stop.is_set(): self.translation.cancel(self._job)
        self._job = None

    def run(self, ids):
        for repo_id in dict.fromkeys(ids):
            if self._stop.is_set(): break
            try:
                target = self.store.load_preferences()[0]
                try: view = self.readme.load(repo_id)
                except Exception: view = self.readme.cached(repo_id)
                batch, size = [], 0
                for item in project_items(self.store, view, repo_id, target):
                    if self._stop.is_set(): break
                    length = sum(_js_length(p.text) for p in item.parts)
                    if batch and (len(batch) == 128 or size + length > 16384):
                        self._batch(target, batch); batch, size = [], 0
                    batch.append(item); size += length
                self._batch(target, batch)
            except Exception as exc:
                # Translation is optional preparation, never a failed GitHub refresh.
                from .diagnostic_log import record_error
                record_error(self.store.data_dir, 'pretranslation', str(exc))
                continue

    def close(self):
        self._stop.set(); self._wake.set()
        if self._job: self.translation.cancel(self._job)
        if self._thread: self._thread.join(timeout=1.5)


def warm_selected(store, client, *, worker=None):
    ids = list(dict.fromkeys(r.repo_id for r in store.latest_recommendations()))
    if not ids: return
    if worker is not None: worker.run(ids); return
    from .readme_service import ReadmeService
    from .translation_service import TranslationService
    translation = TranslationService(store)
    readme = ReadmeService(client, store)
    worker = ProjectPretranslator(store, readme, translation)
    try: worker.run(ids)
    finally: worker.close(); translation.close(); readme.cancel()
