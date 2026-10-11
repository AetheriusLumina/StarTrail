"""On-demand AI candidate selection and analysis orchestration."""

import hashlib
import json
import threading
from datetime import datetime
from dataclasses import replace
from urllib.parse import quote

from .ai_types import AIProgress, AIRepositoryInput, CandidateBatch
from .github_client import GitHubClient, GitHubRequestError
from .readme_service import ReadmeService
from .storage import RadarStore
from .ranking import keyword_excluded_ids


class AIService:
    def __init__(self, client: GitHubClient, store: RadarStore, provider):
        self.client = client
        self.store = store
        self.provider = provider
        self._readme_service = ReadmeService(client, store)
        self._cancelled = threading.Event()
        self._commit_lock = threading.Lock()

    def cancel(self) -> None:
        with self._commit_lock:
            self._cancelled.set()
        self._readme_service.cancel()
        self.provider.cancel()

    def _ensure_active(self) -> None:
        if self._cancelled.is_set():
            raise RuntimeError("AI 分析已取消")

    def candidate_batch(self, local_date: str, keyword_id: int,
                        model_id: str | None, limit: int = 20, *, observed_at: str | None = None) -> CandidateBatch:
        if not 1 <= limit <= 20:
            raise ValueError("每批 AI 候选必须在 1 到 20 个之间")
        rule = next((rule for rule in self.store.list_keywords()
                     if rule.id == keyword_id and rule.enabled), None)
        if rule is None:
            raise ValueError("关键词不存在或已停用")
        current = self.store.daily_recommendations(local_date)
        own_ids = [item.repo_id for item in current
                   if item.section == "keyword" and item.keyword_id == keyword_id]
        occupied = {item.repo_id for item in current}
        historical = keyword_excluded_ids(self.store.seen_repo_ids(),occupied,own_ids)
        checked = self.store.ai_verdicts(local_date, keyword_id, model_id)
        progress: AIProgress = self.store.ai_progress(local_date, keyword_id, model_id)
        selected = []
        selected_ids = set()

        def include(repository, rank=None, observed_at=None):
            self._ensure_active()
            if (repository.id in selected_ids or repository.id in checked
                    or repository.archived or repository.stars < rule.min_stars):
                return
            try:
                excerpt = self.client.readme_excerpt(repository.full_name)
            except GitHubRequestError:
                excerpt = None
            self._ensure_active()
            # An excerpt is bounded, so it cannot establish that the whole README was read.
            selected.append(AIRepositoryInput(repository, excerpt, True, rank, observed_at))
            selected_ids.add(repository.id)

        own_repos = self.store.repositories_for_ids(own_ids)
        for repo_id in own_ids:
            if repo_id in own_repos:
                include(own_repos[repo_id], next((item.rank for item in current if item.repo_id == repo_id), None))
            if len(selected) >= limit:
                return CandidateBatch(tuple(selected), progress.next_page, progress.next_offset)

        page, offset = progress.next_page, progress.next_offset
        query = f"{rule.term} stars:>={rule.min_stars} archived:false"
        while len(selected) < limit and page <= 10:
            self._ensure_active()
            candidates = self.client.search(query, page=page, per_page=100, sort="stars")
            self._ensure_active()
            if offset >= len(candidates):
                if len(candidates) < 100:
                    page += 1
                    offset = 0
                    break
                page += 1
                offset = 0
                continue
            while offset < len(candidates) and len(selected) < limit:
                repository = candidates[offset]
                offset += 1
                if repository.id in historical or repository.id in occupied:
                    continue
                include(repository, (page - 1) * 100 + offset, observed_at or datetime.now().astimezone().isoformat())
            if offset >= len(candidates):
                page += 1
                offset = 0
                if len(candidates) < 100:
                    break
        return CandidateBatch(tuple(selected), page, offset)

    def refine_keyword(self, local_date: str, keyword_id: int,
                       model_id: str | None, checked_at: str) -> AIProgress:
        rule = next((rule for rule in self.store.list_keywords()
                     if rule.id == keyword_id and rule.enabled), None)
        if rule is None:
            raise ValueError("关键词不存在或已停用")
        current = [item.repo_id for item in self.store.daily_recommendations(local_date)
                   if item.section == "keyword" and item.keyword_id == keyword_id]
        known = self.store.ai_verdicts(local_date, keyword_id, model_id)
        if len(current) >= 5 and all(
                repo_id in known and known[repo_id].verdict == "relevant"
                for repo_id in current):
            return self.store.ai_progress(local_date, keyword_id, model_id)
        batch = self.candidate_batch(local_date, keyword_id, model_id, observed_at=checked_at)
        if not batch.inputs:
            return self.store.ai_progress(local_date, keyword_id, model_id)
        self._ensure_active()
        verdicts = self.provider.judge_batch(batch.inputs, rule.term, model_id)
        with self._commit_lock:
            self._ensure_active()
            self.store.commit_ai_batch(local_date, keyword_id, model_id,
                                       batch, verdicts, checked_at)
        return self.store.ai_progress(local_date, keyword_id, model_id)

    def explain_project(self, repo_id: int, model_id: str | None,
                        checked_at: str, force: bool = False, context_date: str | None = None):
        self._ensure_active()
        item=(self.store.recommendation_on(context_date,repo_id) if context_date
              else self.store.latest_recommendation_for_repo(repo_id))
        if item is None and (context_date or not self.store.is_followed(repo_id)):
            raise ValueError("项目不在当前推荐中")
        repository = self.store.repositories_for_ids([repo_id]).get(repo_id)
        if repository is None:
            raise ValueError("找不到项目资料")
        keyword = next((rule.term for rule in self.store.list_keywords()
                        if item and rule.id == item.keyword_id and rule.enabled), None)
        keyword_id=item.keyword_id if item else None
        if keyword_id is not None and keyword is None:
            raise ValueError("关键词不存在或已停用")
        # Manual understanding requires author content, not a metadata-only guess.
        # Reuse the same stable-ID reader/cache as the README view; never silently
        # swallow rate limits or submit a 6,000-character prefix as the whole file.
        view = self._readme_service.load(repo_id)
        self._ensure_active()
        document = view.document
        if document is None or not document.text.strip():
            raise GitHubRequestError(view.reason or 'README 正文不可用，未调用 AI，原有解释已保留。')
        if document.truncated or len(document.text.encode('utf-8')) > 524288:
            raise GitHubRequestError('README 超过完整读取大小限制，未调用 AI，原有解释已保留。')
        repository = replace(repository, full_name=document.full_name,
                             html_url='https://github.com/' + '/'.join(
                                 quote(part, safe='') for part in document.full_name.split('/')))
        excerpt = document.text
        source = AIRepositoryInput(repository, excerpt, False,
                                   readme_source_url=document.source_url)
        version = hashlib.sha256(json.dumps({
            "repo_id": repo_id, "keyword": keyword,
            "description": repository.description, "topics": repository.topics,
            "language": repository.language, "readme_excerpt": excerpt,
            "schema_version":3,"input_version":4,"full_name":repository.full_name,
            "readme_source_url":document.source_url,"readme_hash":document.content_hash,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        cached = self.store.load_explanation(repo_id, keyword_id, model_id, version)
        if cached is not None and not force:
            return cached
        self._ensure_active()
        explanation = self.provider.explain(source, keyword, model_id)
        explanation = replace(explanation,readme_hash=document.content_hash if document else None)
        with self._commit_lock:
            self._ensure_active()
            self.store.save_explanation(repo_id, keyword_id, model_id,
                                        version, explanation)
        return explanation
