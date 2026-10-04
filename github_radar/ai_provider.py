"""Restricted Codex calls for on-demand public-repository analysis."""

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from .ai_types import AIRepositoryInput, InsightText, ProjectExplanation, ProjectKind, RelevanceVerdict
from .codex_connection import CodexConnection


class AIOutputError(Exception):
    """A model call failed or produced data outside the agreed shape."""


_VERDICTS = ("relevant", "irrelevant", "uncertain")
_TEXT_FIELDS = ("summary", "purpose", "scenarios", "users", "problem", "prerequisites")


def _closed_object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


_INSIGHT_SCHEMA = _closed_object(
    {**{field: {"type": "string", "minLength":1, "maxLength":500} for field in _TEXT_FIELDS},
     "highlights": {"type": "array", "maxItems":3, "items": {"type": "string","minLength":1,"maxLength":300}}},
    [*_TEXT_FIELDS, "highlights"],
)
_VERDICT_SCHEMA = _closed_object({"repo_id": {"type": "integer"},
                                   "verdict": {"type": "string", "enum": list(_VERDICTS)},
                                   "reason": {"type": "string"}},
                                  ["repo_id", "verdict", "reason"])
_BATCH_SCHEMA = _closed_object(
    {"verdicts": {"type": "array", "items": _VERDICT_SCHEMA}}, ["verdicts"])
_PROJECT_KINDS=('software','skill','algorithm','model','library','framework','dataset',
                'documentation','curated_list','plugin','service','other','unknown')
_KIND_SCHEMA=_closed_object({
    'primary':{'type':'string','enum':list(_PROJECT_KINDS)},
    'secondary':{'type':'array','maxItems':2,'items':{'type':'string','enum':list(_PROJECT_KINDS)}},
    'zh':{'type':'string','minLength':1,'maxLength':300},
    'en':{'type':'string','minLength':1,'maxLength':300},
    'evidence':{'type':'array','maxItems':3,'items':{'type':'string','minLength':1,'maxLength':300}},
    'uncertain':{'type':'boolean'}},['primary','secondary','zh','en','evidence','uncertain'])
_EXPLANATION_SCHEMA = _closed_object(
    {"zh": _INSIGHT_SCHEMA, "en": _INSIGHT_SCHEMA,
     "relevance": {"type": ["string", "null"], "enum": [*_VERDICTS, None]},
     "evidence": {"type": "array", "maxItems":5, "items": {"type": "string","minLength":1,"maxLength":300}},
     "source_limited": {"type": "boolean"}, "project_kind":_KIND_SCHEMA},
    ["zh", "en", "relevance", "evidence", "source_limited", "project_kind"],
)


class CodexProvider:
    TIMEOUT_SECONDS = 120

    def __init__(self, connection: CodexConnection):
        self.connection = connection
        from .codex_runner import CodexRunner
        self._runner=CodexRunner(connection)
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def _selected_model(self, model_id: str | None) -> str | None:
        state = self.connection.probe()
        if not state.ready:
            raise AIOutputError(state.reason)
        if model_id is not None and model_id not in {
                model.id for model in self.connection.list_models()}:
            raise AIOutputError("所选 Codex 模型目前不可用，请重新选择")
        return model_id

    @staticmethod
    def _repository_data(item: AIRepositoryInput) -> dict:
        repo = item.repo
        excerpt = item.readme_excerpt
        capped_excerpt = excerpt[:6000] if excerpt is not None else None
        return {
            "repo_id": repo.id,
            "full_name": repo.full_name[:240],
            "description": repo.description[:1200],
            "topics": [topic[:80] for topic in repo.topics[:20]],
            "language": repo.language[:80] if repo.language else None,
            "stars": repo.stars,
            "readme_excerpt": capped_excerpt,
            "source_limited": item.source_limited or excerpt is None or (
                excerpt is not None and len(excerpt) > 6000),
        }

    def _run(self,instruction,data,schema,model_id):
        chosen=self._selected_model(model_id)
        return self._runner.run(instruction,data,schema,chosen,timeout=self.TIMEOUT_SECONDS)

    def cancel(self):
        self._cancelled.set();self._runner.cancel()

    def judge_batch(self, inputs: tuple[AIRepositoryInput, ...], keyword: str,
                    model_id: str | None) -> tuple[RelevanceVerdict, ...]:
        if not inputs or len(inputs) > 20 or not keyword.strip() or len(keyword) > 120:
            raise AIOutputError("AI 候选数量或关键词无效")
        ids = [item.repo.id for item in inputs]
        if len(set(ids)) != len(ids):
            raise AIOutputError("AI 候选仓库重复")
        result = self._run(
            "For each repository, decide if it is genuinely related to the keyword. "
            "Use 'uncertain' when evidence is insufficient. Reply with one verdict and short "
            "reason per repo_id. Never include Star counts or URLs in the answer.",
            {"keyword": keyword, "repositories": [self._repository_data(item) for item in inputs]},
            _BATCH_SCHEMA, model_id,
        )
        if set(result) != {"verdicts"} or not isinstance(result["verdicts"], list):
            raise AIOutputError("Codex 相关性结果格式有误")
        found: dict[int, RelevanceVerdict] = {}
        for row in result["verdicts"]:
            if not isinstance(row, dict) or set(row) != {"repo_id", "verdict", "reason"}:
                raise AIOutputError("Codex 相关性结果格式有误")
            repo_id, verdict, reason = row["repo_id"], row["verdict"], row["reason"]
            if (isinstance(repo_id, bool) or not isinstance(repo_id, int) or repo_id not in ids
                    or repo_id in found or verdict not in _VERDICTS
                    or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300):
                raise AIOutputError("Codex 相关性结果无效")
            found[repo_id] = RelevanceVerdict(repo_id, verdict, reason.strip())
        if set(found) != set(ids):
            raise AIOutputError("Codex 相关性结果缺少仓库")
        return tuple(found[repo_id] for repo_id in ids)

    @staticmethod
    def _insight(value: object) -> InsightText:
        if not isinstance(value, dict) or set(value) != {*_TEXT_FIELDS, "highlights"}:
            raise AIOutputError("Codex 项目解读格式有误")
        texts = {}
        for field in _TEXT_FIELDS:
            part = value[field]
            if not isinstance(part, str) or not 1 <= len(part.strip()) <= 500:
                raise AIOutputError("Codex 项目解读文字无效")
            texts[field] = part.strip()
        highlights = value["highlights"]
        if (not isinstance(highlights, list) or len(highlights) > 3
                or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 300
                       for item in highlights)):
            raise AIOutputError("Codex 项目特点格式有误")
        return InsightText(**texts, highlights=tuple(item.strip() for item in highlights))

    @staticmethod
    def _project_kind(value):
        if not isinstance(value,dict) or set(value)!={'primary','secondary','zh','en','evidence','uncertain'}:
            raise AIOutputError('Codex 项目类型格式有误')
        if value['primary'] not in _PROJECT_KINDS or type(value['uncertain']) is not bool:
            raise AIOutputError('Codex 项目类型无效')
        secondary=value['secondary'];evidence=value['evidence']
        if not isinstance(secondary,list) or len(secondary)>2 or any(k not in _PROJECT_KINDS for k in secondary) or len(set(secondary))!=len(secondary):
            raise AIOutputError('Codex 次要类型无效')
        if not isinstance(evidence,list) or len(evidence)>3 or any(not isinstance(t,str) or not 1<=len(t.strip())<=300 for t in evidence):
            raise AIOutputError('Codex 类型依据无效')
        if any(not isinstance(value[k],str) or not 1<=len(value[k].strip())<=300 for k in ('zh','en')):
            raise AIOutputError('Codex 类型说明无效')
        if value['primary']=='unknown' and not value['uncertain']:
            raise AIOutputError('资料不足不能确定项目类型')
        return ProjectKind(value['primary'],tuple(secondary),value['zh'].strip(),value['en'].strip(),tuple(evidence),value['uncertain'])

    def explain(self, item: AIRepositoryInput, keyword: str | None,
                model_id: str | None) -> ProjectExplanation:
        if keyword is not None and len(keyword) > 120:
            raise AIOutputError("关键词过长")
        source = self._repository_data(item)
        result = self._run(
            "Explain this project in Chinese and English for non-technical ordinary users. "
            "Use plain everyday language; explain any necessary technical term briefly in the same sentence. "
            "Apply this to every field, especially prerequisites: briefly explain named operating systems, "
            "abbreviations and tools by their role, rather than listing unfamiliar names alone. "
            "Start with what the user can do and the practical benefit, not jargon. "
            "project_kind must identify what this repository IS: software, skill, algorithm, model, library, "
            "framework, dataset, documentation, curated_list, plugin, service, other or unknown. "
            "Provide a full plain-language classification sentence in zh and en for the FIRST line of purpose, "
            "with at most two secondary types and three README evidence phrases. A skill is a reusable set of "
            "AI instructions, not automatically standalone software. When evidence is insufficient use unknown, "
            "uncertain true and say the type cannot yet be determined. Do not infer a type solely from its name. "
            "problem describes the concrete problem solved; "
            "users the audience; scenarios typical use; prerequisites required skills, runtime and setup. "
            "Use one short paragraph per field and at most three core highlights. Keep each field brief and based "
            "only on supplied facts. If evidence is insufficient, state that explicitly; "
            "set source_limited true. For a missing keyword set relevance null. List short "
            "evidence phrases, at most five, each at most 300 characters. Do not return Star counts or URLs.",
            {"keyword": keyword, "repository": source}, _EXPLANATION_SCHEMA, model_id,
        )
        if set(result) != {"zh", "en", "relevance", "evidence", "source_limited", "project_kind"}:
            raise AIOutputError("Codex 项目解读格式有误")
        relevance = result["relevance"]
        if ((keyword is None and relevance is not None)
                or (keyword is not None and relevance not in _VERDICTS)):
            raise AIOutputError("Codex 项目相关性无效")
        evidence = result["evidence"]
        if (not isinstance(evidence, list)
                or any(not isinstance(part, str) or not part.strip()
                       for part in evidence)):
            raise AIOutputError("AI 返回的项目依据格式不正确，原有结果已保留，请手动重新生成")
        limited = result["source_limited"]
        if not isinstance(limited, bool):
            raise AIOutputError("Codex 项目证据标识无效")
        return ProjectExplanation(
            self._insight(result["zh"]), self._insight(result["en"]), relevance,
            tuple(part.strip()[:300] for part in evidence[:5]), limited or source["source_limited"],schema_version=3,
            project_kind=self._project_kind(result["project_kind"]),
        )
