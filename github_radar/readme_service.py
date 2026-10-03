"""On-demand public author documentation, separate from ranking and AI."""
import hashlib
import sqlite3
import copy
import queue
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace
from urllib.parse import quote
from .readme_content import extract_readme
from .readme_types import ReadmeDocument, ReadmeView
from .github_client import GitHubClient, GitHubRequestError, GitHubRateLimitError


class ReadmeService:
    def __init__(self, client, store):
        self.client, self.store = client, store
        self.timeout = 20
        self._cancelled = threading.Event()
        self._commit_lock = threading.Lock()
        self._network_lock = threading.Lock()

    def cancel(self):
        # Serialize cancellation with the final save; late transports never own the store.
        with self._commit_lock:
            self._cancelled.set()

    def _check(self, deadline):
        if self._cancelled.is_set():
            raise GitHubRequestError('README 读取已取消。')
        if time.monotonic() >= deadline:
            raise GitHubRequestError('README 读取超过总计 20 秒，已停止等待。')

    def _fetch(self, repo, cached, deadline):
        self._check(deadline)
        if not self._network_lock.acquire(blocking=False):
            raise GitHubRequestError('上次 README 网络读取尚未结束，请稍后再试。')
        # An abandoned transport cannot mutate the daily worker's quota/budget state.
        client = copy.copy(self.client) if isinstance(self.client, GitHubClient) else self.client
        if isinstance(client, GitHubClient):
            client.budget = copy.deepcopy(client.budget)
        results = queue.Queue(maxsize=1)
        def network():
            try:
                current = client.get_repository_by_id(repo.id)
                self._check(deadline)
                if current.id != repo.id:
                    raise GitHubRequestError('仓库名称已对应其他项目，拒绝覆盖原项目 README。')
                etag = cached.document.etag if cached.document else None
                # Upgrade legacy root-only caches with a full metadata response.
                if isinstance(client, GitHubClient) and cached.document and '/blob/' not in cached.document.source_url:
                    etag = None
                result = client.fetch_readme_by_id(repo.id, etag)
                results.put((current, result, None))
            except Exception as exc:
                results.put((None, None, exc))
            finally:
                self._network_lock.release()
        threading.Thread(target=network, daemon=True).start()
        while True:
            self._check(deadline)
            try:
                current, result, error = results.get(timeout=min(.05, max(.001, deadline-time.monotonic())))
                break
            except queue.Empty:
                continue
        with self._commit_lock:
            self._check(deadline)
            if isinstance(client, GitHubClient):
                for name in ('remaining', 'core_remaining', 'search_remaining', 'reset_at',
                             'core_reset_at', 'search_reset_at', '_last_headers'):
                    setattr(self.client, name, getattr(client, name))
        if error: raise error
        return current, result

    def cached(self, repo_id):
        try:
            doc = self.store.load_readme(repo_id)
            return extract_readme(doc) if doc else ReadmeView(None, (), '', 'empty', None)
        except (ValueError, sqlite3.Error, OSError):
            return ReadmeView(None, (), '', 'error', 'README 本地缓存无法读取，请重新读取。')

    def _quota(self):
        # Some offline test clients intentionally expose no network methods.
        if isinstance(self.client, GitHubClient):
            self.client.renew_expired_quotas()
            if self.client.core_remaining == 0:
                raise GitHubRateLimitError(self.client.core_reset_at)

    def load(self, repo_id: int, refresh: bool = False) -> ReadmeView:
        deadline = time.monotonic() + self.timeout
        cached = self.cached(repo_id)
        if cached.document is not None and not refresh: return cached
        try:
            repo = self.store.repositories_for_ids([repo_id]).get(repo_id)
            if repo is None: return ReadmeView(None, (), '', 'not_found', '本机没有这个项目记录。')
            self._quota()
            current, result = self._fetch(repo, cached, deadline)
            if result.not_modified:
                if cached.document is None: raise GitHubRequestError('GitHub 返回未修改，但本机没有 README 副本。')
                old_prefix = 'https://github.com/' + '/'.join(quote(p, safe='') for p in cached.document.full_name.split('/'))
                new_prefix = 'https://github.com/' + '/'.join(quote(p, safe='') for p in current.full_name.split('/'))
                source_url = cached.document.source_url
                if source_url.startswith(old_prefix + '/') or source_url.startswith(old_prefix + '#'):
                    source_url = new_prefix + source_url[len(old_prefix):]
                doc = replace(cached.document, full_name=current.full_name, source_url=source_url,
                              etag=result.etag or cached.document.etag)
                with self._commit_lock:
                    self._check(deadline)
                    self.store.save_readme(doc)
                return extract_readme(doc)
            if result.text is None:
                if cached.document: raise GitHubRequestError('GitHub 当前没有 README，保留上次副本。')
                return ReadmeView(None, (), '', 'missing', '该项目没有可用的 README。')
            source_url = result.source_url or f'https://github.com/{current.full_name}#readme'
            if result.source_url and not result.source_url.startswith(f'https://github.com/{current.full_name}/blob/'):
                raise GitHubRequestError('仓库在读取期间改名，请重新更新 README。')
            doc = ReadmeDocument(repo_id, current.full_name, result.text,
                source_url, datetime.now(timezone.utc).isoformat(),
                result.etag, hashlib.sha256(result.text.encode()).hexdigest(), result.truncated)
            with self._commit_lock:
                self._check(deadline)
                self.store.save_readme(doc)
            return extract_readme(doc)
        except (GitHubRequestError, ValueError, OSError, sqlite3.Error) as exc:
            return ReadmeView(cached.document, cached.sections, cached.selected_markdown,
                              'stale' if cached.document else 'error', str(exc),cached.full_markdown)
