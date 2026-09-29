from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.domain_models import SecondaryError
from llm_client import LLMClient
from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonicalize_grading_config_payload,
)


def _without_embedded_image_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_embedded_image_data(item)
            for key, item in value.items()
            if not str(key).endswith("_base64")
        }
    if isinstance(value, list):
        return [_without_embedded_image_data(item) for item in value]
    return value


_LEGACY_SEMANTIC_KEYS = {
    "knowledge_id",
    "knowledge_ids",
    "knowledge_name",
    "knowledge_points",
    "measured_skills",
    "supporting_skills",
    "skill_id",
    "skills",
}
_QUESTION_TAG_CONTEXT_TYPES = {
    "knowledge_point",
    "sub_skill",
    "method",
    "ability",
    "model",
    "error_type",
    "prerequisite",
}


def _without_legacy_knowledge_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_legacy_knowledge_fields(item)
            for key, item in value.items()
            if str(key) not in _LEGACY_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_without_legacy_knowledge_fields(item) for item in value]
    return value


def _normalize_question_tag_context(
    raw: Mapping[str, Mapping[str, Sequence[str]]] | None,
) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for raw_question_id, raw_tags in (raw or {}).items():
        question_id = str(raw_question_id or "").strip()
        if not question_id or not isinstance(raw_tags, Mapping):
            continue
        tags: dict[str, list[str]] = {}
        for raw_tag_type, raw_values in raw_tags.items():
            tag_type = str(raw_tag_type or "").strip()
            if tag_type not in _QUESTION_TAG_CONTEXT_TYPES:
                continue
            values = [raw_values] if isinstance(raw_values, str) else list(raw_values or [])
            cleaned: list[str] = []
            for value in values:
                text = str(value or "").strip()
                if text and text not in cleaned:
                    cleaned.append(text)
            if cleaned:
                tags[tag_type] = cleaned
        if tags:
            result[question_id] = tags
    return result


class AIGrader:
    def __init__(
        self,
        rubric_path: Path,
        llm_client: LLMClient,
        answer_key_path: Path | None = None,
        grading_model: str | None = None,
        target_question_ids: list[str] | None = None,
        answer_regions: list[dict[str, Any]] | None = None,
        question_tag_context: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    ) -> None:
        self.rubric_path = rubric_path
        self.answer_key_path = answer_key_path
        self.llm_client = llm_client
        self.grading_model = grading_model
        self.answer_regions = answer_regions or []
        self.question_tag_context = _normalize_question_tag_context(question_tag_context)
        loaded = canonicalize_grading_config_payload(
            {
                "rubric": self._load_json_file(self.rubric_path, "评分细则"),
                "answer_key": (
                    self._load_json_file(self.answer_key_path, "标准答案")
                    if self.answer_key_path
                    else {}
                ),
            }
        )
        self.rubric = dict(loaded.get("rubric") or {})
        self.answer_key = dict(loaded.get("answer_key") or {})
        raw_target_ids = _unique_texts(target_question_ids or [])
        try:
            catalog = QuestionIdCatalog.from_document(self.rubric)
            self.target_question_ids = _unique_texts(
                [
                    catalog.resolve(question_id) or question_id
                    for question_id in raw_target_ids
                ]
            )
        except QuestionIdContractError:
            self.target_question_ids = raw_target_ids
        self._question_type_map = self._build_question_type_map()

    def get_question_type(self, qid: str) -> str:
        return self._question_type_map.get(str(qid).strip(), "unknown")

    def _build_question_type_map(self) -> dict[str, str]:
        q_map = {}
        for q in self.rubric.get("questions", []):
            if not isinstance(q, dict): continue
            q_id = str(q.get("question_id") or "").strip()
            q_type = str(q.get("question_type") or "unknown").strip()
            if q_id:
                q_map[q_id] = q_type
            for part in q.get("parts", []):
                if not isinstance(part, dict): continue
                p_id = str(part.get("part_id") or "").strip()
                if p_id:
                    q_map[p_id] = q_type
        return q_map

    def _load_json_file(self, path: Path | None, label: str) -> dict[str, Any]:
        if path is None:
            return {}
        if not path.exists():
            raise FileNotFoundError(f"{label}文件不存在: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

def _clean_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "正确", "无", "无扣分", "未扣分"}:
        return None
    return text


_PROTECTED_ERROR_CATEGORIES = {
    "未作答",
    "多选失分",
    "作废答案",
    "提示注入",
    "答案不等价",
    "需复核",
}


def _normalize_grading_errors(
    item: Mapping[str, Any],
    *,
    clear_errors: bool,
    error_candidates: Sequence[str],
) -> tuple[str | None, str | None, list[SecondaryError]]:
    if clear_errors:
        return None, None, []

    from backend.error_causes import GRADING_CATEGORY_MAP

    valid_categories = set(GRADING_CATEGORY_MAP) | _PROTECTED_ERROR_CATEGORIES | {"其他"}
    category = _clean_optional_text(item.get("error_category")) or "其他"
    summary = (
        _clean_optional_text(item.get("error_summary"))
        or _clean_optional_text(item.get("deduction_reason"))
        or category
    )
    if category not in valid_categories:
        category = "其他"

    secondary_errors: list[SecondaryError] = []
    seen_summaries = {summary}
    raw_secondary = item.get("secondary_errors")
    for raw_error in raw_secondary if isinstance(raw_secondary, list) else []:
        if not isinstance(raw_error, Mapping):
            continue
        secondary_summary = _clean_optional_text(raw_error.get("summary"))
        if not secondary_summary or secondary_summary in seen_summaries:
            continue
        secondary_category = _clean_optional_text(raw_error.get("category")) or "其他"
        if secondary_category not in valid_categories:
            secondary_category = "其他"
        secondary_errors.append(
            SecondaryError(
                category=secondary_category,
                summary=secondary_summary,
                evidence=_clean_optional_text(raw_error.get("evidence")) or "",
            )
        )
        seen_summaries.add(secondary_summary)
        if len(secondary_errors) == 2:
            break
    return category, summary, secondary_errors


def _unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


