"""Replaceable project explanation boundary; no model service is assumed."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import Repository
from .ranking import _words


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    term: str
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectInsight:
    summary: str
    relevance: str
    evidence: tuple[MatchEvidence, ...]
    provider: str
    ai_enabled: bool


@runtime_checkable
class ProjectInterpreter(Protocol):
    def interpret(self, repository: Repository, keyword: str | None) -> ProjectInsight:
        """Explain a repository without changing its facts or ranking."""


class BasicInterpreter:
    """Transparent local word matching over public GitHub fields."""

    def interpret(self, repository: Repository, keyword: str | None) -> ProjectInsight:
        wanted = list(dict.fromkeys(_words(keyword or "")))
        sources = (
            ("名称", set(_words(repository.full_name))),
            ("简介", set(_words(repository.description))),
            ("标签", set(_words(" ".join(repository.topics)))),
        )
        evidence = tuple(
            MatchEvidence(word, tuple(name for name, words in sources if word in words))
            for word in wanted
            if any(word in words for _, words in sources)
        )
        relevance = (
            "未设置关键词" if not wanted else
            "基础匹配" if len(evidence) == len(wanted) else
            "基础匹配不足"
        )
        return ProjectInsight(
            summary=repository.description.strip() or "GitHub 暂无项目简介",
            relevance=relevance,
            evidence=evidence,
            provider="basic",
            ai_enabled=False,
        )
