"""Immutable author content and its derived, source-attributed reading view."""
from dataclasses import dataclass

EXTRACTION_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReadmeDocument:
    repo_id: int
    full_name: str
    text: str
    source_url: str
    observed_at: str
    etag: str | None
    content_hash: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class ReadmeSection:
    kind: str
    title: str
    text: str
    source_heading: str | None


@dataclass(frozen=True, slots=True)
class ReadmeView:
    document: ReadmeDocument | None
    sections: tuple[ReadmeSection, ...]
    selected_markdown: str
    status: str
    reason: str | None
    full_markdown: str = ''


@dataclass(frozen=True, slots=True)
class ReadmeFetch:
    text: str | None
    etag: str | None
    not_modified: bool
    truncated: bool
    source_url: str | None = None
