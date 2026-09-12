from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Literal

from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.database.schema import connect
from question_bank.services.duplicate_analysis_copy_service import exact_question_key, exact_identity_map, exam_original_key
from question_bank.recommendation.recommendation_engine import text_similarity
from question_bank.mastery.current import (
    CURRENT_MASTERY_PARAMETERS,
    CurrentMasteryCalculator,
)
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_volume,
    eligible_curriculum_knowledge_nodes,
    load_curriculum_catalog,
)
from question_bank.training_criteria import (
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
    usable_training_criterion,
)


ENGINE_VERSION = "personalized-recommendation-v8-printed-duplicate-exclusion"
GROUPING_VERSION = "chapter-union-coverage-v3"
GROUP_MIN_SIMILARITY = 0.58
RECENT_WINDOW_DAYS = 90
Stage = Literal["direct", "prerequisite", "transfer"]
Action = Literal["lock", "unlock", "exclude", "replace"]
_PRACTICE_TAG_KINDS = ("method", "model", "thought")


class PersonalizedRecommendationError(RuntimeError):
    pass


class RecommendationRequestConflict(PersonalizedRecommendationError):
    pass


class RecommendationDraftNotFound(PersonalizedRecommendationError):
    pass


class RecommendationRevisionConflict(PersonalizedRecommendationError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__("recommendation draft revision is stale")


class RecommendationSourceChanged(PersonalizedRecommendationError):
    pass


class RecommendationEditInvalid(PersonalizedRecommendationError):
    pass


@dataclass(frozen=True, slots=True)
class PersonalizedRecommendationConfig:
    paper_mode: Literal["individual", "shared"] = "individual"
    question_count: int = 10
    expected_minutes: int = 45
    difficulty_min: int = 1
    difficulty_max: int = 7
    direct_ratio: float = 0.6
    prerequisite_ratio: float = 0.3
    transfer_ratio: float = 0.1
    target_keys: tuple[str, ...] = ()
    scope_keys: tuple[str, ...] = ()
    exclude_current_exam_originals: bool = True
    curriculum_volume_id: str = ""
    group_scope_keys: tuple[str, ...] = ()
    group_source_version: str = ""
    training_intent: Literal["remediation", "challenge"] = "remediation"
    teaching_progress_chapter_id: str = ""

    def __post_init__(self) -> None:
        if self.paper_mode not in {"individual", "shared"}:
            raise ValueError("paper_mode is invalid")
        if self.training_intent not in {"remediation", "challenge"}:
            raise ValueError("training_intent is invalid")
        if not 8 <= int(self.question_count) <= 12:
            raise ValueError("question_count must be between 8 and 12")
        if (
            not 1 <= int(self.difficulty_min) <= 10
            or not 1 <= int(self.difficulty_max) <= 10
        ):
            raise ValueError("difficulty range is invalid")
        normalized_targets = _identity_keys(self.target_keys, field="target_keys")
        normalized_scope = _identity_keys(self.scope_keys, field="scope_keys")
        normalized_volume = str(self.curriculum_volume_id or "").strip()
        if normalized_volume and curriculum_volume(
            volume_id=normalized_volume
        ) is None:
            raise ValueError("curriculum_volume_id is not in the bundled catalog")
        object.__setattr__(self, "question_count", int(self.question_count))
        object.__setattr__(
            self, "expected_minutes", int(self.expected_minutes)
        )
        object.__setattr__(self, "difficulty_min", 1)
        object.__setattr__(self, "difficulty_max", int(self.difficulty_max))
        object.__setattr__(self, "target_keys", normalized_targets)
        object.__setattr__(self, "scope_keys", normalized_scope)
        object.__setattr__(self, "group_scope_keys", _identity_keys(self.group_scope_keys, field="group_scope_keys"))
        object.__setattr__(self, "curriculum_volume_id", normalized_volume)
        progress = str(self.teaching_progress_chapter_id or "").strip()
        if progress and not any(
            chapter["id"] == progress
            for volume in load_curriculum_catalog()["volumes"]
            if not normalized_volume or volume["id"] == normalized_volume
            for chapter in volume["chapters"]
        ):
            raise ValueError("teaching_progress_chapter_id must be a chapter in the selected volume")
        object.__setattr__(self, "teaching_progress_chapter_id", progress)
        if (
            self.paper_mode == "shared"
            and not normalized_targets
            and not normalized_scope
        ):
            raise ValueError(
                "shared paper mode requires teacher-selected targets"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            **{key: value for key, value in asdict(self).items()
               if key not in {"expected_minutes", "difficulty_min", "direct_ratio", "prerequisite_ratio", "transfer_ratio", "training_intent"}},
            "target_keys": list(self.target_keys),
            "scope_keys": list(self.scope_keys),
            "group_scope_keys": list(self.group_scope_keys),
            "recent_window_days": RECENT_WINDOW_DAYS,
        }


def _allowed_keys_for_volume(volume_id: str) -> frozenset[str] | None:
    """Knowledge keys in the selected volume and every earlier volume.

    Returns None when no volume is selected, keeping candidate selection
    unbounded for legacy drafts and requests.
    """
    clean = str(volume_id or "").strip()
    if not clean:
        return None
    return frozenset(
        str(node["id"]) for node in eligible_curriculum_knowledge_nodes(clean)
    )


@lru_cache(maxsize=128)
def _progress_chapter(config: PersonalizedRecommendationConfig) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    matches = []
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            if config.teaching_progress_chapter_id:
                if chapter["id"] == config.teaching_progress_chapter_id:
                    return volume, chapter
            elif any(key == chapter["knowledge_id"] or key.startswith(chapter["knowledge_id"] + "_")
                     for key in (*config.scope_keys, *config.target_keys)):
                matches.append((volume, chapter))
    return max(matches, key=lambda item: (int(item[0]["order"]), int(item[1]["order"]))) if matches else None


@lru_cache(maxsize=128)
def _allowed_keys_for_config(config: PersonalizedRecommendationConfig) -> frozenset[str] | None:
    progress = _progress_chapter(config)
    if progress is None:
        return _allowed_keys_for_volume(config.curriculum_volume_id)
    volume, chapter = progress
    chapter_keys = [item["knowledge_id"] for item in volume["chapters"] if int(item["order"]) <= int(chapter["order"])]
    keys = frozenset(str(node["id"]) for node in eligible_curriculum_knowledge_nodes(volume["id"])
                     if node["volume_id"] != volume["id"] or any(
                         node["id"] == key or node["id"].startswith(key + "_") for key in chapter_keys))
    volume_keys = _allowed_keys_for_volume(config.curriculum_volume_id)
    return keys if volume_keys is None else keys.intersection(volume_keys)


def _question_scope_allowed(candidate: Mapping[str, Any], config: PersonalizedRecommendationConfig,
                            allowed_keys: frozenset[str] | None) -> bool:
    if allowed_keys is None:
        return True
    required = set(candidate.get("required_keys", candidate["stable_keys"]))
    return bool(required) and required.issubset(allowed_keys) and (
        _progress_chapter(config) is None or bool(candidate.get("scope_complete")))


def _question_evidence_metadata(evidence: Mapping[str, Any], resolver: CurrentKnowledgeResolver) -> dict[str, Any]:
    """Keep roles and response modes tied to each small part of the printed question."""
    direct: set[str] = set()
    required: set[str] = set()
    modes: dict[str, set[str]] = {}
    observations: dict[str, list[dict[str, Any]]] = {}
    parts = evidence.get("parts") or []
    complete = bool(parts)
    for part in parts:
        part_direct: set[str] = set()
        for point in part.get("evidence_points", []):
            for link in point.get("fine_term_links", []):
                resolution = link.get("core_resolution") or {}
                raw_keys = resolution.get("stable_keys") or []
                if resolution.get("status") != "resolved" or not raw_keys:
                    complete = False
                    continue
                for raw_key in raw_keys:
                    resolved = resolver.resolve(raw_key)
                    if not resolved:
                        complete = False
                    for identity in resolved:
                        required.add(identity.stable_key)
                        if link.get("role") == "direct":
                            part_direct.add(identity.stable_key)
        if not part_direct:
            complete = False
        direct.update(part_direct)
        for key in part_direct:
            modes.setdefault(key, set()).add(str(part.get("response_mode") or "unknown"))
            relevant_points = [point for point in part.get("evidence_points", []) if any(
                link.get("role") == "direct" and key in (link.get("core_resolution") or {}).get("stable_keys", [])
                for link in point.get("fine_term_links", []))]
            observations.setdefault(key, []).append({
                "part_id": str(part.get("part_id") or ""),
                "response_mode": str(part.get("response_mode") or "unknown"),
                "observable": "；".join(str(point.get(field) or "") for point in relevant_points
                                        for field in ("target", "observable_evidence", "justification")),
                "fine_terms": sorted({str(link.get("fine_term_id")) for point in relevant_points
                                      for link in point.get("fine_term_links", [])
                                      if link.get("role") == "direct" and link.get("fine_term_id")}),
            })
    return {"stable_keys": sorted(direct), "required_keys": sorted(required), "scope_complete": complete,
            "response_modes_by_key": {key: sorted(values) for key, values in sorted(modes.items())},
            "practice_observations_by_key": observations}


def _training_tasks(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Translate attributable losses into practice requirements, not new diagnoses."""
    references = [ref for ref in evidence.get("source_question_refs", []) if isinstance(ref, Mapping)]
    current = [ref for ref in references if ref.get("source_kind") == "current_exam"]
    tasks: dict[str, dict[str, Any]] = {}
    for ref in current or references:
        assessment = ref.get("assessment") or {}
        if (assessment.get("eligible") is False or assessment.get("granularity") not in {"part", "step"}
                or float(assessment.get("evidence_weight", 1.0)) < .999
                or float(ref.get("full_score") or 0) <= 0
                or float(ref.get("score_awarded") or 0) >= float(ref["full_score"])):
            continue
        reason = "；".join([str(ref.get("deduction_reason") or ""), str(ref.get("error_summary") or ""),
                           *(str(item.get("summary") or "") for item in ref.get("secondary_errors", []) if isinstance(item, Mapping))])
        if not reason.strip("； "):
            continue
        if any(word in reason for word in ("待复核", "无法判断", "未独立观察", "证据不足")):
            continue
        # 扣分说明常同时记录做对与缺失的步骤，任务只使用其中的问题片段。
        error_clauses = []
        for clause in re.split(r"[，,；;。\n]|但是|然而|但|却|而", reason):
            if (any(word in clause for word in ("正确", "无误", "完整", "齐全", "已写出"))
                    and not any(word in clause for word in ("不正确", "不完整", "未", "缺", "漏", "错误", "算错", "混淆", "跳步", "不足"))):
                continue
            error_clauses.append(clause)
        problem = "；".join(error_clauses)
        codes: list[tuple[str, str]] = []
        if any(word in problem for word in ("依据", "理由", "证明", "推理", "过程不完整", "跳步")):
            codes.append(("written_reasoning", "书写依据与推理步骤"))
        if any(word in problem for word in ("混淆", "辨析", "对应量", "单位错误", "数量关系")):
            codes.append(("quantity_discrimination", "辨析量及其对应关系"))
        if any(word in problem for word in ("计算", "运算", "符号错误", "算错", "漏负", "验算", "移项错误")):
            codes.append(("calculation_check", "列出计算步骤并核对结果"))
        blank_mentioned = any(word in reason for word in ("未作答", "空白", "未答"))
        observed_error = any(word in problem for word in ("错误", "算错", "混淆", "有误", "漏负", "跳步"))
        wholly_blank = bool(re.match(r"^[；\s]*(?:本题|本问|整题|整问|全题|全问)?[，\s]*(?:完全|全部)?(?:未作答|空白|未答)", reason))
        if blank_mentioned and (wholly_blank or not observed_error):
            # “空白，未见计算/依据”只说明没有作答，不能反推计算或推理错误。
            codes = [("diagnostic_check", "用短题确认起点（空白原因未知）")]
        elif not codes and "未完成" in reason:
            codes.append(("diagnostic_check", "用短题确认起点（空白原因未知）"))
        for code, label in codes:
            task = tasks.setdefault(code, {"code": code, "label": label, "source_refs": []})
            source = {key: deepcopy(ref.get(key)) for key in ("session_id", "question_id", "bank_question_id", "assessment")}
            if source not in task["source_refs"]:
                task["source_refs"].append(source)
    return [tasks[key] for key in sorted(tasks)]


def _practice_matches(candidate: Mapping[str, Any], key: str, tasks: Sequence[Mapping[str, Any]]) -> bool:
    requirements = [task for task in tasks if task["code"] != "diagnostic_check"]
    if not requirements:
        return True
    observations = candidate.get("practice_observations_by_key", {}).get(key, [])
    for task in requirements:
        compatible = [part for part in observations if part.get("response_mode") in {"short_answer_points", "process_required"}]
        if task["code"] == "written_reasoning":
            compatible = [part for part in compatible if part.get("response_mode") == "process_required"
                          and any(word in str(part.get("observable") or "") for word in ("依据", "理由", "证明", "推理", "说明", "定理"))]
        if task["code"] == "quantity_discrimination":
            compatible = [part for part in compatible if any(word in str(part.get("observable") or "")
                          for word in ("量", "关系", "单位", "边", "长", "面积", "对应", "区别", "辨", "概念"))]
        if task["code"] == "calculation_check":
            compatible = [part for part in compatible if any(word in str(part.get("observable") or "")
                          for word in ("计算", "运算", "代入", "平方", "根", "式", "等式", "求值", "验算"))]
        if not compatible:
            return False
    return True


def _loss_refs(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    all_refs = [ref for ref in target.get("source_question_refs", []) if isinstance(ref, Mapping)]
    has_current = any(ref.get("source_kind") == "current_exam" for ref in all_refs)
    refs = [dict(ref) for ref in all_refs
            if (ref.get("assessment") or {}).get("eligible") is not False
            and float((ref.get("assessment") or {}).get("evidence_weight", 1)) >= .999
            and float(ref.get("full_score") or 0) > 0
            and float(ref.get("score_awarded") or 0) < float(ref["full_score"])]
    current = [ref for ref in refs if ref.get("source_kind") == "current_exam"]
    return current if has_current else refs


def _loss_difficulty(ref: Mapping[str, Any], score_rate: object, cap: int) -> float | None:
    assessment = ref.get("assessment") or {}
    original = assessment.get("part_difficulty")
    if original is None:
        original = ref.get("question_difficulty")
    if original is None:
        return None
    original = float(original)
    if not math.isfinite(original) or not 1 <= original <= 10:
        return None
    local_rate = _rate(ref.get("score_rate"))
    if local_rate is None:
        local_rate = float(ref.get("score_awarded") or 0) / float(ref["full_score"])
    overall = _rate(score_rate)
    # Overall attainment contributes 80%; this particular loss contributes 20%.
    # Missing overall evidence uses only the observed response, never a fixed grade.
    readiness = local_rate if overall is None else .8 * overall + .2 * local_rate
    reduction = min(1.0, max(0.0, (.85 - readiness) / .45))
    return min(float(cap), max(1.0, original - reduction))


def _loss_difficulty_fits(candidate: Mapping[str, Any], ref: Mapping[str, Any], cap: int) -> bool:
    original = (ref.get("assessment") or {}).get("part_difficulty")
    if original is None:
        original = ref.get("question_difficulty")
    if original is None or candidate.get("difficulty") is None:
        return False
    return min(cap, max(1.0, float(original) - 1)) <= float(candidate["difficulty"]) <= min(cap, float(original))


def _direct_fit(candidate: Mapping[str, Any], key: str, ref: Mapping[str, Any]) -> bool:
    """Direct knowledge is required; method, model and response mode only rank."""
    return key in candidate.get("stable_keys", [])


def _direct_preference(candidate: Mapping[str, Any], key: str, ref: Mapping[str, Any]) -> float:
    source_keys = set(ref.get("direct_keys") or [key])
    candidate_keys = set(candidate.get("stable_keys") or [])
    # Extra in-scope knowledge does not make a question less useful for the
    # source loss. Reward the source knowledge covered, not set equality.
    overlap = len(source_keys & candidate_keys) / max(1, len(source_keys))
    source_tags = ref.get("practice_tags") or {}
    profile_tags = (candidate.get("similarity_profile") or {}).get("tags", [])
    affinity = 0.0
    for kind in _PRACTICE_TAG_KINDS:
        expected = set(source_tags.get(kind) or [])
        actual = {tag["tag_value"] for tag in profile_tags if tag.get("tag_type") == kind}
        affinity += len(expected & actual) / max(1, len(expected))
    tasks = _training_tasks({"source_question_refs": [ref]})
    response_fit = bool(tasks) and _practice_matches(candidate, key, tasks)
    type_fit = bool(ref.get("question_type")) and ref["question_type"] == candidate.get("question_type")
    return 4 * overlap + affinity + .5 * type_fit + .5 * response_fit


@lru_cache(maxsize=8192)
def _practice_template(text: str) -> str:
    """A paper-local comparison only; it never changes stored question tags."""
    value = re.sub(r"\[\[IMAGE:.*?\]\]|!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", "", text, flags=re.I)
    value = re.sub(r"^\s*(?:第\s*)?\d+\s*[.．、题]\s*", "", value)
    stem = re.split(r"(?:^|\s|[。？?）)])(?:[A-D][.．、:：]|[（(][A-D][）)])", value, maxsplit=1)[0]
    # When the options contain the actual statements, keep them in comparison.
    if len(re.sub(r"\W", "", stem)) >= 16:
        value = stem
    value = re.sub(r"\d+(?:\.\d+)?", "数", value)
    value = re.sub(r"\b[A-Z]{1,3}\b", "字母", value)
    return re.sub(r"\s+", "", value)


def _basic_judgement_family(candidate: Mapping[str, Any]) -> str:
    if float(candidate.get("difficulty") or 10) > 4:
        return ""
    text = str(candidate.get("question_text") or "")
    if "勾股数" in text or ("直角三角形" in text and any(word in text for word in (
            "判断", "判定", "构成", "组成", "是直角", "为直角"))):
        return "pythagorean_number_judgement"
    tags = (candidate.get("similarity_profile") or {}).get("tags", [])
    judgements = sorted(tag["tag_value"] for tag in tags if tag.get("tag_type") in {"method", "model"}
                        and any(word in tag["tag_value"] for word in ("判定", "判断", "辨认")))
    return _json([sorted(candidate.get("stable_keys", [])), judgements]) if judgements else ""


def _paper_diversity_allowed(candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> bool:
    family = _basic_judgement_family(candidate)
    if family and sum(_basic_judgement_family(other) == family for other in selected) >= 2:
        return False
    template = _practice_template(str(candidate.get("question_text") or ""))
    for other in selected:
        practice_identity = candidate.get("practice_identity")
        if practice_identity and practice_identity == other.get("practice_identity"):
            return False
        if candidate.get("duplicate_identity", candidate["question_id"]) == other.get("duplicate_identity", other["question_id"]):
            return False
        if not set(candidate.get("stable_keys", [])).intersection(other.get("stable_keys", [])):
            continue
        # Distinct drawings may encode distinct conditions. Compare their existing
        # content identities instead of removing drawings and treating them as equal.
        if candidate.get("image_identity", ()) != other.get("image_identity", ()):
            continue
        other_template = _practice_template(str(other.get("question_text") or ""))
        if len(template) >= 12 and len(other_template) >= 12 and text_similarity(template, other_template) >= .9:
            return False
    return True


def _loss_need_id(entry: Mapping[str, Any]) -> tuple[str, ...]:
    ref = _loss_refs(entry["target"])[0]
    return tuple(str(value or "") for value in (entry["student_id"], entry["key"], ref.get("session_id"),
                 ref.get("question_id"), ref.get("bank_question_id"), (ref.get("assessment") or {}).get("part_id")))


def _refresh_supplement_warnings(draft: dict[str, Any]) -> None:
    for student in draft["students"]:
        warnings = [warning for warning in student["warnings"]
                    if not warning.startswith("直接练习不足，已用选定范围内难度相近的")]
        count = sum(item.get("selection_kind") == "supplement" for item in student["items"])
        if count:
            warnings.append(f"直接练习不足，已用选定范围内难度相近的 {count} 道补充练习补足；补充题不表示新增薄弱点。")
        student["warnings"] = warnings
    draft["warnings"] = list(dict.fromkeys(warning for student in draft["students"] for warning in student["warnings"]))


def _order_practice_items(items: list[dict[str, Any]]) -> None:
    """Present selected whole questions from easy to hard, keeping tied order.

    Item ids and slots remain stable for editing; item_order is the displayed
    order also used when the draft becomes a paper snapshot.
    """
    items.sort(key=lambda item: _difficulty(item.get("difficulty")) or 11)
    for order, item in enumerate(items, 1):
        item["item_order"] = order


def _choose_practice_entries(entries: Sequence[dict[str, Any]], question_count: int) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    selected: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    coverage: dict[tuple[str, ...], int] = {}
    member_coverage: set[str] = set()
    remaining = list(entries)
    while len(selected) < question_count:
        grouped: dict[Any, list[dict[str, Any]]] = {}
        printed = [entry["candidate"] for entry, _ in selected]
        accepted: dict[int, bool] = {}
        for entry in remaining:
            candidate = entry["candidate"]
            qid = candidate["question_id"]
            if qid not in accepted:
                accepted[qid] = _paper_diversity_allowed(candidate, printed)
            if accepted[qid]:
                grouped.setdefault(candidate.get("duplicate_identity") or qid, []).append(entry)
        if not grouped:
            break
        groups = []
        for group in grouped.values():
            direct = [entry for entry in group if entry["selection_kind"] == "direct"]
            groups.append(direct or group)

        def rank(group):
            is_direct = group[0]["selection_kind"] == "direct"
            needs = {_loss_need_id(entry) for entry in group} if is_direct else set()
            members = {entry["student_id"] for entry in group} if is_direct else set()
            return (not is_direct,
                    -len(needs - coverage.keys()),
                    -len(members - member_coverage),
                    -sum(1 / (1 + coverage.get(need, 0)) for need in needs),
                    median(entry["distance"] for entry in group),
                    -max(entry["preference"] for entry in group),
                    -max(entry["loss"] for entry in group),
                    min(entry["candidate"]["question_id"] for entry in group))

        group = min(groups, key=rank)
        best = min(group, key=lambda entry: (entry["distance"], -entry["preference"], -entry["loss"],
                                            entry["student_id"], _loss_need_id(entry)))
        selected.append((best, group))
        if best["selection_kind"] == "direct":
            for need in {_loss_need_id(entry) for entry in group}:
                coverage[need] = coverage.get(need, 0) + 1
            member_coverage.update(entry["student_id"] for entry in group)
        remaining = [entry for values in grouped.values() for entry in values
                     if entry["candidate"]["question_id"] != best["candidate"]["question_id"]]
    return selected


_GRADE_RANK = {"七年级": 7, "八年级": 8, "九年级": 9}
_SEMESTER_RANK = {"上学期": 1, "下学期": 2}


def _paper_level_rank(grade: object, semester: object) -> int | None:
    """Order key for a source paper's grade and semester; None if unknown."""
    grade_rank = _GRADE_RANK.get(str(grade or "").strip())
    semester_rank = _SEMESTER_RANK.get(str(semester or "").strip())
    if grade_rank is None or semester_rank is None:
        return None
    return grade_rank * 10 + semester_rank


def _paper_level_limit_for_volume(volume_id: str) -> int | None:
    """Latest allowed source-paper level for the selected volume."""
    clean = str(volume_id or "").strip()
    if not clean:
        return None
    volume = curriculum_volume(volume_id=clean)
    if volume is None:
        return None
    return _paper_level_rank(volume.get("grade"), volume.get("semester"))


@dataclass(frozen=True, slots=True)
class RecommendationEditCommand:
    request_token: str
    expected_revision: int
    action: Action
    student_id: str
    item_id: str
    actor_ref: str
    reason: str
    replacement_question_id: int | None = None

    def __post_init__(self) -> None:
        _request_token(self.request_token)
        if int(self.expected_revision) < 1:
            raise ValueError("expected_revision must be positive")
        if self.action not in {"lock", "unlock", "exclude", "replace"}:
            raise ValueError("action is invalid")
        _required_text(self.student_id, "student_id")
        _required_text(self.item_id, "item_id")
        _required_text(self.actor_ref, "actor_ref")
        _required_text(self.reason, "reason")
        if (
            self.replacement_question_id is not None
            and int(self.replacement_question_id) <= 0
        ):
            raise ValueError("replacement_question_id must be positive")


class PersonalizedRecommendationModule:
    """Own deterministic recommendation, persistence and teacher adjustments."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.clock = clock or (lambda: datetime.now(UTC))
        try:
            self.current_knowledge = (
                CurrentKnowledgeResolver.from_active_database(self.db_path)
            )
        except CurrentKnowledgeUnavailable as exc:
            raise PersonalizedRecommendationError(
                "current knowledge standard is unavailable"
            ) from exc

    def chapter_groups(
        self, *, diagnosis: Mapping[str, Any], config: PersonalizedRecommendationConfig,
        member_ids: Sequence[str] = (), target_keys: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Read-only preview using the same source and eligibility rules as drafts."""
        students = diagnosis.get("students", [])
        if not students:
            return {"version": GROUPING_VERSION, "groups": [], "unassigned": [], "selection": None,
                    "scope_keys": list(config.group_scope_keys), "warnings": ["当前范围没有学生。"]}
        normalized = _normalize_diagnosis(diagnosis)
        excluded = self._current_exam_question_ids(normalized) if config.exclude_current_exam_originals else set()
        relations = tuple({"relation_type": relation.relation_type, "source_key": relation.source_key,
                           "target_key": relation.target_key} for relation in self.current_knowledge.relations)
        leaves = _scope_leaves(config.group_scope_keys, diagnosis=normalized, relations=relations)
        governed = {node.stable_key for node in self.current_knowledge.nodes}
        if not leaves or not set(leaves) <= governed:
            raise ValueError("grouping requires a current chapter or section")
        needs = _group_needs(normalized, leaves)
        grouped_members = _chapter_group_members(needs)
        candidates, source_version, recent = (), "", {}
        # Read question bodies only after a usable group exists, and only for
        # the selected chapter. A coarse-only diagnosis needs no question pool.
        if grouped_members or member_ids:
            candidates, relations, source_version = self._source_snapshot(
                excluded_question_ids=excluded, knowledge_keys=leaves,
            )
            recent = self._recent_question_ids(tuple(student["student_id"] for student in students))
        groups = [self._chapter_group_summary(
            diagnosis=normalized, members=members, targets=(), needs=needs, config=config,
            candidates=candidates, relations=relations, recent=recent, excluded=excluded, source_version=source_version,
        ) for members in grouped_members]
        groups.sort(key=lambda group: (not group["ready"], -len(group["targets"]),
                                      -group["compatibility"], -len(group["members"]), group["group_id"]))
        covered = {member["student_id"] for group in groups for member in group["members"]}
        selection = None
        if member_ids:
            if not set(member_ids) <= set(needs):
                raise ValueError("group members are outside the selected student scope")
            selection = self._chapter_group_summary(
                diagnosis=normalized, members=tuple(sorted(set(member_ids))), targets=target_keys, needs=needs, config=config,
                candidates=candidates, relations=relations, recent=recent, excluded=excluded, source_version=source_version,
            )
        return {"version": GROUPING_VERSION, "scope_keys": list(config.group_scope_keys),
                "source_scope_revision": str(diagnosis.get("scope", {}).get("scope_revision") or ""),
                "mastery_parameter_version": CURRENT_MASTERY_PARAMETERS.version,
                "groups": groups, "selection": selection,
                "unassigned": [{"student_id": student["student_id"], "student_name": student["student_name"],
                                "class_id": student["class_id"],
                                "reason": ("有明确训练需求，但暂未找到整体水平接近的同伴，可使用一人一卷。"
                                           if needs[student["student_id"]] else "暂无足够的直接薄弱证据；无证据、整题或多目标综合失分不按不会处理。")}
                               for student in normalized["students"] if student["student_id"] not in covered],
                "warnings": ["按实际失分识别训练需求；无证据不推断薄弱，公共卷按整卷覆盖成员。"]}

    def _chapter_group_summary(
        self, *, diagnosis: Mapping[str, Any], members: Sequence[str], targets: Sequence[str],
        needs: Mapping[str, Mapping[str, Any]], config: PersonalizedRecommendationConfig,
        candidates: Sequence[dict[str, Any]], relations: Sequence[Mapping[str, Any]],
        recent: Mapping[str, set[int]], excluded: set[int], source_version: str,
    ) -> dict[str, Any]:
        profiles = {student["student_id"]: student for student in diagnosis["students"]}
        union = set().union(*(set(needs[sid]) for sid in members)) if members else set()
        keys = tuple(sorted(set(targets) if targets else union))
        issues = []
        warnings = []
        if len(members) < 2:
            issues.append("群体训练至少需要两名学生；单人请使用按学生训练。")
        if not keys or not set(keys) <= union:
            issues.append("所选目标缺少成员的直接训练证据，请调整目标。")
        if any(not set(needs[sid]).intersection(keys) for sid in members):
            issues.append("部分成员在所选目标上没有训练需求，请调整名单或目标。")
        overall_rates = [_rate(profiles[sid].get("score_rate")) for sid in members]
        known_rates = [rate for rate in overall_rates if rate is not None]
        if known_rates and max(known_rates) - min(known_rates) > .25 + 1e-9:
            issues.append("成员整体考试水平差异较大，建议拆组；明显不同需求可安排个人训练。")
        metadata = self._source_practice_metadata(diagnosis)
        rows = []
        pools: dict[str, set[int]] = {}
        removed: set[int] = set()
        shared_recent = set().union(*(recent.get(sid, set()) for sid in members)) if members else set()
        allowed = _allowed_keys_for_config(config)
        level = _paper_level_limit_for_volume(config.curriculum_volume_id)
        for key in keys:
            available = [needs[sid][key] for sid in members if key in needs[sid]]
            if not available:
                continue
            values = [item["mastery"] for item in available]
            pools[key] = set()
            aims = []
            eligible_members = set()
            for sid in members:
                for raw_ref in _loss_refs(needs.get(sid, {}).get(key, {})):
                    ref = self._enrich_source_ref(raw_ref, metadata)
                    aim = _loss_difficulty(ref, profiles[sid].get("score_rate"), config.difficulty_max)
                    if aim is None:
                        warnings.append("部分来源题难度证据不足，未用统一低难度补位。")
                        continue
                    aims.append(aim)
                    eligible = self._eligible_candidates(candidates, stage="direct", target_keys=(key,), maintenance=False,
                        used=set(), recent=set(), excluded=excluded, config=config, allowed_keys=allowed,
                        paper_level_max=level, difficulty_targets={key: aim})
                    for candidate in eligible:
                        if not _loss_difficulty_fits(candidate, ref, config.difficulty_max) or not _direct_fit(candidate, key, ref):
                            continue
                        qid = candidate["question_id"]
                        if qid in recent.get(sid, set()):
                            removed.add(qid)
                            continue
                        pools[key].add(qid)
                        eligible_members.add(sid)
            rows.append({"knowledge_key": key, "knowledge_point": available[0]["knowledge_point"],
                         "min_mastery": min(values), "max_mastery": max(values), "median_mastery": median(values),
                         "evidence_count": sum(item["evidence_count"] for item in available),
                         "sparse_member_count": sum(item["evidence_count"] == 1 for item in available),
                         "affected_student_count": len(available), "eligible_member_ids": sorted(eligible_members),
                         "difficulty_unknown": not aims or any(item["difficulty_unknown"] for item in available),
                         "target_difficulty": round(median(aims), 1) if aims else None,
                         "available_question_count": len(pools[key])})
            if not pools[key]:
                warnings.append("部分训练目标暂无适用题目，将如实保留缺口，可补充题库或安排个人训练。")
        preview_entries = []
        for sid in members:
            entries, entry_warnings = self._candidate_entries(
                profile=profiles[sid], targets=[needs[sid][key] for key in keys if key in needs[sid]],
                candidates=candidates, metadata=metadata, config=config,
                supplement_keys=_scope_leaves(config.group_scope_keys or config.scope_keys or keys,
                                              diagnosis=diagnosis, relations=relations),
                recent=recent.get(sid, set()), excluded=excluded)
            preview_entries.extend(entries)
            warnings.extend(entry_warnings)
        preview = _choose_practice_entries(preview_entries, config.question_count)
        count = len(preview)
        if not count:
            issues.append("当前成员需求暂无适用题目，请补充题库或调整目标。")
        if any(row["sparse_member_count"] for row in rows):
            warnings.append("部分成员仅有一次清晰作答，证据较少，请核对名单。")
        if count < config.question_count:
            warnings.append("去除卷内高度重复题并尝试范围内补充后，仍不足设定题量；正式草稿将如实列出缺口。")
        if any(entry["selection_kind"] == "supplement" for entry, _ in preview):
            warnings.append("直接练习不足，将使用选定范围内难度相近的补充练习，补充题不计作薄弱目标覆盖。")
        similarities = [_group_similarity(needs[left], needs[right])
                        for index, left in enumerate(members) for right in members[index + 1:]]
        selected = deepcopy({**diagnosis, "students": [profiles[sid] for sid in sorted(members)]})
        # Scope-specific diagnoses can be calculated seconds apart. The existing
        # source revision covers raw scores, training records and parameters;
        # natural time decay is not a teacher/source edit.
        for profile in selected["students"]:
            for point in profile["weak_points"]:
                point.pop("mastery", None)
                point.pop("effective_weight", None)
        settings = config.to_dict()
        settings.pop("group_source_version", None)
        settings["target_keys"] = list(keys)
        settings["scope_keys"] = []
        selected_source_ids = {int(ref.get("bank_question_id") or 0) for student in selected["students"] for point in student["weak_points"] for ref in _loss_refs(point)}
        version = _hash_payload({"version": GROUPING_VERSION, "source": source_version,
                                 "loss_sources": {qid: metadata[qid] for qid in sorted(selected_source_ids) if qid in metadata},
                                 "diagnosis": selected, "settings": settings,
                                 "recent": sorted(shared_recent), "day": _day_clock(self.clock()).isoformat()})
        return {"group_id": _hash_payload({"members": sorted(members), "targets": keys})[:20],
                "source_version": version, "members": [
                    {"student_id": sid, "student_name": profiles[sid]["student_name"],
                     "student_code": profiles[sid]["student_code"], "class_id": profiles[sid]["class_id"],
                     "evidence_count": sum(needs[sid][key]["evidence_count"] for key in keys if key in needs[sid]),
                     "targets": [needs[sid][key] for key in keys if key in needs[sid]]}
                    for sid in sorted(members)], "targets": rows,
                "compatibility": round(min(similarities, default=0), 4),
                "ready": not issues, "issues": list(dict.fromkeys(issues)), "warnings": warnings,
                "available_question_count": count, "recent_excluded_count": len(removed),
                "reason": "先覆盖尚未练到的成员失分内容，再增加练习；直接练习不足时从选定范围内补充相近难度题。"}

    def create(
        self,
        *,
        request_token: str,
        diagnosis: Mapping[str, Any],
        config: PersonalizedRecommendationConfig,
        actor_ref: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        actor = _required_text(actor_ref, "actor_ref")
        normalized_diagnosis = _normalize_diagnosis(diagnosis)
        request = {
            "diagnosis": normalized_diagnosis,
            "config": config.to_dict(),
        }
        input_fingerprint = _hash_payload(request)
        existing = self._by_request_token(token)
        if existing is not None:
            existing_fingerprint = str(existing.pop("_input_fingerprint"))
            if existing_fingerprint != input_fingerprint:
                raise RecommendationRequestConflict(
                    "recommendation request token was reused"
                )
            return existing

        if config.group_scope_keys:
            checked = self.chapter_groups(diagnosis=normalized_diagnosis, config=config,
                                          member_ids=[item["student_id"] for item in normalized_diagnosis["students"]],
                                          target_keys=config.target_keys)["selection"]
            if not checked or not checked["ready"]:
                raise ValueError("selected group no longer has compatible common targets")
            if not config.group_source_version or checked["source_version"] != config.group_source_version:
                raise RecommendationSourceChanged("group evidence or source has changed; refresh the group")

        excluded = (
            self._current_exam_question_ids(normalized_diagnosis)
            if config.exclude_current_exam_originals
            else set()
        )
        candidates, relations, base_source_version = self._source_snapshot(
            excluded_question_ids=excluded,
            prepare_refinements=True,
        )
        base_source_version = _hash_payload({"bank": base_source_version, "loss_sources": self._source_practice_metadata(normalized_diagnosis)})
        mastery = self._mastery_snapshot(normalized_diagnosis)
        recent = self._recent_question_ids(
            tuple(
                str(item["student_id"])
                for item in normalized_diagnosis["students"]
            )
        )
        source_version = _context_source_version(
            base_source_version,
            as_of=_day_clock(self.clock()),
            recent=recent,
            excluded_question_ids=excluded,
        )
        draft = self._build_draft(
            diagnosis=normalized_diagnosis,
            config=config,
            candidates=candidates,
            relations=relations,
            mastery=mastery,
            recent=recent,
            excluded_question_ids=excluded,
        )
        result_version = _hash_payload(
            {"source_version": source_version, "draft": draft}
        )
        draft_id = _hash_payload(
            {
                "request_token": token,
                "input_fingerprint": input_fingerprint,
            }
        )
        stored = {
            "draft_id": draft_id,
            "status": "draft",
            "revision": 1,
            "result_version": result_version,
            "engine_version": ENGINE_VERSION,
            "source_version": source_version,
            **draft,
            "history": [
                {
                    "action": "created",
                    "actor_ref": actor,
                    "reason": "教师生成个性化推荐草稿",
                    "revision": 1,
                }
            ],
        }
        encoded_request = _json(request)
        encoded_draft = _json(stored)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_fingerprint, draft_json
                FROM personalized_recommendation_drafts
                WHERE request_token = ?
                """,
                (token,),
            ).fetchone()
            if row is not None:
                if str(row["input_fingerprint"]) != input_fingerprint:
                    raise RecommendationRequestConflict(
                        "recommendation request token was reused"
                    )
                return json.loads(str(row["draft_json"]))
            connection.execute(
                """
                INSERT INTO personalized_recommendation_drafts (
                    draft_id, request_token, input_fingerprint,
                    result_version, engine_version, source_version,
                    request_json, draft_json, status, revision, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?)
                """,
                (
                    draft_id,
                    token,
                    input_fingerprint,
                    result_version,
                    ENGINE_VERSION,
                    source_version,
                    encoded_request,
                    encoded_draft,
                    actor,
                ),
            )
            connection.execute(
                """
                INSERT INTO personalized_recommendation_events (
                    draft_id, request_token, command_hash, action,
                    actor_ref, reason, expected_revision,
                    resulting_revision, before_json, after_json,
                    resulting_draft_json
                ) VALUES (?, ?, ?, 'created', ?, ?, 0, 1, '{}', ?, ?)
                """,
                (
                    draft_id,
                    token,
                    _hash_payload(request),
                    actor,
                    "教师生成个性化推荐草稿",
                    encoded_draft,
                    encoded_draft,
                ),
            )
        return stored

    def resolve_target_names(
        self, target_names: Sequence[str]
    ) -> tuple[str, ...]:
        names = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in target_names
                if str(value or "").strip()
            )
        )
        if not names:
            return ()
        result: list[str] = []
        for name in names:
            matches = self.current_knowledge.resolve(name)
            if not matches:
                raise ValueError(
                    f"training target is not uniquely governed: {name}"
                )
            for match in matches:
                if match.stable_key not in result:
                    result.append(match.stable_key)
        return tuple(result)

    def get(self, draft_id: str) -> dict[str, Any]:
        clean_id = _draft_id(draft_id)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT draft_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None:
            raise RecommendationDraftNotFound(clean_id)
        return json.loads(str(row["draft_json"]))

    def ensure_current(self, draft_id: str) -> dict[str, Any]:
        """Return a draft only when every recommendation source is unchanged."""

        clean_id = _draft_id(draft_id)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT request_json, source_version, draft_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None:
            raise RecommendationDraftNotFound(clean_id)
        request = json.loads(str(row["request_json"]))
        _candidates, current_source = self._current_source_for_request(
            request,
            draft_id=clean_id,
        )
        if current_source != str(row["source_version"]):
            raise RecommendationSourceChanged(
                "recommendation sources changed"
            )
        return json.loads(str(row["draft_json"]))

    def edit(
        self,
        draft_id: str,
        command: RecommendationEditCommand,
    ) -> dict[str, Any]:
        clean_id = _draft_id(draft_id)
        command_payload = {
            **asdict(command),
            "replacement_question_id": command.replacement_question_id,
        }
        command_hash = _hash_payload(command_payload)
        with connect(self.db_path) as connection:
            repeated = connection.execute(
                """
                SELECT command_hash, resulting_draft_json
                FROM personalized_recommendation_events
                WHERE draft_id = ? AND request_token = ?
                """,
                (clean_id, command.request_token),
            ).fetchone()
        if repeated is not None:
            if str(repeated["command_hash"]) != command_hash:
                raise RecommendationRequestConflict(
                    "recommendation edit token was reused"
                )
            return json.loads(str(repeated["resulting_draft_json"]))

        with connect(self.db_path) as connection:
            source_row = connection.execute(
                """
                SELECT request_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if source_row is None:
            raise RecommendationDraftNotFound(clean_id)
        source_request = json.loads(str(source_row["request_json"]))
        if (
            source_request.get("config", {}).get("paper_mode") == "shared"
        ):
            raise RecommendationEditInvalid(
                "shared paper questions cannot be edited per student"
            )
        source_candidates, current_source = (
            self._current_source_for_request(
                source_request,
                draft_id=clean_id,
            )
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = connection.execute(
                """
                SELECT command_hash, resulting_draft_json
                FROM personalized_recommendation_events
                WHERE draft_id = ? AND request_token = ?
                """,
                (clean_id, command.request_token),
            ).fetchone()
            if repeated is not None:
                if str(repeated["command_hash"]) != command_hash:
                    raise RecommendationRequestConflict(
                        "recommendation edit token was reused"
                    )
                return json.loads(str(repeated["resulting_draft_json"]))

            row = connection.execute(
                """
                SELECT *
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
            if row is None:
                raise RecommendationDraftNotFound(clean_id)
            current_revision = int(row["revision"])
            if int(command.expected_revision) != current_revision:
                raise RecommendationRevisionConflict(
                    command.expected_revision,
                    current_revision,
                )
            if str(row["status"]) != "draft":
                raise RecommendationEditInvalid(
                    "reviewed recommendation cannot be changed"
                )
            if current_source != str(row["source_version"]):
                raise RecommendationSourceChanged(
                    "recommendation sources changed"
                )

            request = json.loads(str(row["request_json"]))
            draft = json.loads(str(row["draft_json"]))
            before, after = self._apply_edit(
                draft,
                draft_id=clean_id,
                request=request,
                command=command,
                candidates=source_candidates,
            )
            next_revision = current_revision + 1
            draft["revision"] = next_revision
            draft["history"].append(
                {
                    "action": _past_action(command.action),
                    "actor_ref": command.actor_ref.strip(),
                    "reason": command.reason.strip(),
                    "student_id": command.student_id.strip(),
                    "item_id": command.item_id.strip(),
                    "revision": next_revision,
                    "before_question_id": before.get("question_id"),
                    "after_question_id": after.get("question_id"),
                }
            )
            result_version = _hash_payload(
                {
                    key: value
                    for key, value in draft.items()
                    if key not in {"history", "revision", "result_version"}
                }
            )
            draft["result_version"] = result_version
            encoded = _json(draft)
            cursor = connection.execute(
                """
                UPDATE personalized_recommendation_drafts
                SET draft_json = ?,
                    result_version = ?,
                    revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE draft_id = ? AND revision = ?
                """,
                (
                    encoded,
                    result_version,
                    next_revision,
                    clean_id,
                    current_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RecommendationRevisionConflict(
                    command.expected_revision,
                    current_revision + 1,
                )
            connection.execute(
                """
                INSERT INTO personalized_recommendation_events (
                    draft_id, request_token, command_hash, action,
                    actor_ref, reason, expected_revision,
                    resulting_revision, before_json, after_json,
                    resulting_draft_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_id,
                    command.request_token,
                    command_hash,
                    _past_action(command.action),
                    command.actor_ref.strip(),
                    command.reason.strip(),
                    current_revision,
                    next_revision,
                    _json(before),
                    _json(after),
                    encoded,
                ),
            )
        return draft

    def _current_source_for_request(
        self,
        request: Mapping[str, Any],
        *,
        draft_id: str | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], str]:
        diagnosis = request["diagnosis"]
        config = request["config"]
        student_ids = tuple(
            str(item["student_id"])
            for item in diagnosis["students"]
        )
        excluded = (
            self._current_exam_question_ids(diagnosis)
            if bool(config.get("exclude_current_exam_originals", True))
            else set()
        )
        candidates, _relations, base_source_version = self._source_snapshot(
            excluded_question_ids=excluded,
        )
        base_source_version = _hash_payload({"bank": base_source_version, "loss_sources": self._source_practice_metadata(diagnosis)})
        recent = self._recent_question_ids(
            student_ids,
            exclude_draft_id=draft_id,
        )
        return (
            candidates,
            _context_source_version(
                base_source_version,
                as_of=_day_clock(self.clock()),
                recent=recent,
                excluded_question_ids=excluded,
            ),
        )

    def _source_practice_metadata(self, diagnosis: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
        """Reuse saved source tags and small-part evidence, including excluded originals."""
        ids = sorted({int(ref.get("bank_question_id") or 0) for student in diagnosis.get("students", [])
                      for point in student.get("weak_points", []) for ref in _loss_refs(point)
                      if int(ref.get("bank_question_id") or 0) > 0})
        result: dict[int, dict[str, Any]] = {}
        from question_bank.solution_evidence.part_assessments import load_profiles
        for start in range(0, len(ids), 64):
            batch = ids[start:start + 64]
            marks = ",".join("?" for _ in batch)
            with connect(self.db_path) as conn:
                rows = conn.execute(f"SELECT id, difficulty, question_type FROM questions WHERE id IN ({marks}) AND is_deleted=0", batch).fetchall()
                tags = conn.execute(f"SELECT question_id, tag_type, tag_value FROM question_tags WHERE question_id IN ({marks})", batch).fetchall()
            profiles = load_profiles(self.db_path, batch, data_root=self.data_root)
            from question_bank.solution_evidence import SolutionEvidenceRepository
            from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
            evidence_repository = SolutionEvidenceRepository(self.db_path)
            source_inputs = {item.question_id: item for item in QuestionAnalysisInputLoader(
                db_path=self.db_path, data_root=self.data_root).load([int(row["id"]) for row in rows])}

            for row in rows:
                qid = int(row["id"])
                values: dict[str, list[str]] = {}
                for tag in tags:
                    if int(tag["question_id"]) == qid:
                        values.setdefault(str(tag["tag_type"]), []).append(str(tag["tag_value"]))
                keys = sorted({identity.stable_key for kind in ("knowledge_point", "canonical_knowledge_id")
                               for value in values.get(kind, []) for identity in self.current_knowledge.resolve(value)})
                profile = profiles.get(qid) or {}
                source_parts = (profile.get("evidence") or {}).get("parts", []) if profile.get("available") else []
                if not source_parts:
                    latest = evidence_repository.latest(qid)
                    current_input = source_inputs.get(qid)
                    if latest and current_input and latest.get("status") in {"proposed", "approved"} and latest.get("source_content_hash") == solution_evidence_source_content_hash(current_input):
                        source_parts = (latest.get("evidence") or {}).get("parts", [])
                result[qid] = {"question_difficulty": _difficulty(row["difficulty"]), "direct_keys": keys,
                               "question_type": str(row["question_type"] or ""),
                               "practice_tags": {kind: values.get(kind, []) for kind in _PRACTICE_TAG_KINDS},
                               "parts": source_parts}
        return result

    def _enrich_source_ref(self, ref: Mapping[str, Any], metadata: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
        source = metadata.get(int(ref.get("bank_question_id") or 0), {})
        enriched = {**deepcopy(ref), **{key: deepcopy(source[key]) for key in
                    ("question_difficulty", "direct_keys", "practice_tags", "question_type") if source.get(key)}}
        part_id = (ref.get("assessment") or {}).get("part_id")
        parts = [part for part in source.get("parts", []) if not part_id or part.get("part_id") == part_id]
        if parts:
            part_metadata = _question_evidence_metadata({"parts": parts}, self.current_knowledge)
            enriched["direct_keys"] = part_metadata["stable_keys"]
            enriched["direct_fine_terms"] = sorted({term for observations in part_metadata["practice_observations_by_key"].values()
                                                     for part in observations for term in part.get("fine_terms", [])})
        return enriched

    def _candidate_entries(
        self, *, profile: Mapping[str, Any], targets: Sequence[Mapping[str, Any]],
        candidates: Sequence[dict[str, Any]], metadata: Mapping[int, Mapping[str, Any]],
        config: PersonalizedRecommendationConfig, supplement_keys: Sequence[str],
        recent: set[int], excluded: set[int],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Reuse the same pool for preview, generation and replacement."""
        eligible = self._eligible_candidates(
            candidates, stage="direct", target_keys=(), maintenance=True, used=set(),
            recent=recent, excluded=excluded, config=config,
            allowed_keys=_allowed_keys_for_config(config),
            paper_level_max=_paper_level_limit_for_volume(config.curriculum_volume_id))
        entries, warnings = [], []
        scope = set(supplement_keys)
        for target in targets:
            key = str(target.get("stable_key") or target.get("knowledge_key"))
            for raw_ref in _loss_refs(target):
                ref = self._enrich_source_ref(raw_ref, metadata)
                aim = _loss_difficulty(ref, profile.get("score_rate"), config.difficulty_max)
                if aim is None:
                    warnings.append(f"来源题 {ref.get('question_id', '')} 难度证据不足，未用统一低难度补位。")
                    continue
                selection_target = {**target, "stable_key": key, "source_question_refs": [ref],
                                    "target_difficulty": aim, "overall_score_rate": _rate(profile.get("score_rate")),
                                    "training_tasks": _training_tasks({"source_question_refs": [ref]})}
                for candidate in eligible:
                    if not _loss_difficulty_fits(candidate, ref, config.difficulty_max):
                        continue
                    direct = _direct_fit(candidate, key, ref)
                    if not direct and not scope.intersection(candidate["stable_keys"]):
                        continue
                    entries.append({"candidate": candidate, "target": selection_target, "key": key,
                                    "matched_key": key if direct else _matched_key(candidate["stable_keys"], tuple(sorted(scope))),
                                    "selection_kind": "direct" if direct else "supplement",
                                    "student_id": str(profile["student_id"]), "distance": abs(candidate["difficulty"] - aim),
                                    "preference": _direct_preference(candidate, key, ref) if direct else 0,
                                    "loss": 1 - float(ref.get("score_awarded") or 0) / float(ref["full_score"])})
        return entries, warnings

    def _build_draft(
        self, *, diagnosis: dict[str, Any], config: PersonalizedRecommendationConfig,
        candidates: tuple[dict[str, Any], ...], relations: tuple[dict[str, Any], ...],
        mastery: dict[tuple[str, str], dict[str, Any]], recent: dict[str, set[int]],
        excluded_question_ids: set[int],
    ) -> dict[str, Any]:
        leaves = _scope_leaves(config.scope_keys, diagnosis=diagnosis, relations=relations)
        explicit = set(config.target_keys or leaves)
        supplement_keys = _scope_leaves(config.group_scope_keys or config.scope_keys or config.target_keys,
                                       diagnosis=diagnosis, relations=relations)
        profiles = diagnosis["students"]
        source_metadata = self._source_practice_metadata(diagnosis)
        targets_by_student = {}
        pools = {}
        warnings_by_student = {}
        for profile in profiles:
            sid = str(profile["student_id"])
            targets = [deepcopy(value) for (owner, key), value in mastery.items()
                       if owner == sid and (not explicit or key in explicit) and _loss_refs(value)]
            targets_by_student[sid] = targets
            entries, warnings = self._candidate_entries(
                profile=profile, targets=targets, candidates=candidates, metadata=source_metadata, config=config,
                supplement_keys=supplement_keys or tuple(target["stable_key"] for target in targets),
                recent=recent.get(sid, set()), excluded=excluded_question_ids)
            if not targets:
                warnings.append("当前范围没有可确认的实际失分来源，未从满分题或知识点关系推断补弱需求。")
            pools[sid] = entries
            warnings_by_student[sid] = warnings

        shared = _choose_practice_entries([entry for entries in pools.values() for entry in entries], config.question_count) if config.paper_mode == "shared" else None
        students = []
        for profile in profiles:
            sid = str(profile["student_id"])
            selected = shared if shared is not None else _choose_practice_entries(pools[sid], config.question_count)
            items = []
            for order, (entry, group) in enumerate(selected, 1):
                beneficiaries = sorted({member["student_id"] for member in group})
                own = [member for member in group if member["student_id"] == sid]
                if own:
                    entry = min(own, key=lambda member: (member["distance"], -member["preference"], _loss_need_id(member)))
                item = _draft_item(entry["candidate"], stage="direct", slot=order, student_id=sid,
                                   target=entry["target"], matched_key=entry["matched_key"], maintenance=False,
                                   selection_kind=entry["selection_kind"])
                item.update(item_order=order, beneficiary_student_ids=beneficiaries)
                if shared is not None:
                    names = "、".join(str(member.get("student_name") or member["student_id"]) for member in profiles if str(member["student_id"]) in beneficiaries)
                    item["reason"] = (f"公共卷：本题主要对应 {names} 的训练需求。" if entry["selection_kind"] == "direct"
                                      else f"公共卷：本题难度适合 {names}，用于范围内补充练习。") + item["reason"]
                items.append(item)
            _order_practice_items(items)
            missing = config.question_count - len(items)
            warnings = warnings_by_student[sid]
            if missing:
                warnings.append(f"选定范围内符合相近难度且不高度重复的题目不足，保留 {missing} 道缺口。")
            covered = {_loss_need_id(member) for _, group in selected for member in group
                       if member["selection_kind"] == "direct" and member["student_id"] == sid}
            missing_targets = [target for target in targets_by_student[sid]
                               if not any(need[1] == target["stable_key"] for need in covered)]
            if missing_targets:
                names = "、".join(str(target.get("display_name") or target["stable_key"]) for target in missing_targets)
                warnings.append(f"以下失分知识点尚未获得直接练习：{names}。补充练习不计作这些目标的覆盖。")
            if shared is not None and not covered:
                warnings.append("本张公共卷暂无对应此成员的适用练习，请安排个人训练或补充题库。")
            students.append({"student_id": sid, "student_code": str(profile.get("student_code") or ""),
                             "student_name": str(profile.get("student_name") or ""), "class_id": str(profile.get("class_id") or ""),
                             "selection_mode": "mastery_targeted", "targets": targets_by_student[sid], "items": items,
                             "shortages": ([{"stage": "direct", "requested_count": config.question_count,
                                             "selected_count": len(items), "missing_count": missing,
                                             "reason_code": "approved_candidate_shortage"}] if missing else []),
                             "warnings": list(dict.fromkeys(warnings))})
        draft = {"config": config.to_dict(), "students": students,
                 "group_basis": {"method": "member_candidate_union_coverage"} if shared is not None else None}
        _refresh_supplement_warnings(draft)
        return draft

    def _eligible_candidates(
        self,
        candidates: Sequence[dict[str, Any]],
        *,
        stage: Stage,
        target_keys: tuple[str, ...],
        maintenance: bool,
        used: set[int],
        recent: set[int],
        excluded: set[int],
        config: PersonalizedRecommendationConfig,
        difficulty_targets: Mapping[str, float] | None = None,
        allowed_keys: frozenset[str] | None = None,
        paper_level_max: int | None = None,
        practice_tasks: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        center = float(config.difficulty_max)

        def aim_for(item: dict[str, Any]) -> float:
            if difficulty_targets is None:
                return center
            matched = _matched_key(item["stable_keys"], target_keys)
            return difficulty_targets.get(matched, center)

        result = []
        for candidate in candidates:
            question_id = int(candidate["question_id"])
            difficulty = candidate["difficulty"]
            if (
                question_id in used
                or question_id in recent
                or question_id in excluded
                or difficulty is None
                or not config.difficulty_min
                <= int(difficulty)
                <= config.difficulty_max
            ):
                continue
            if not _question_scope_allowed(candidate, config, allowed_keys):
                continue
            paper_rank = candidate.get("paper_level_rank")
            if (
                paper_level_max is not None
                and paper_rank is not None
                and int(paper_rank) > paper_level_max
            ):
                continue
            if not maintenance and not set(candidate["stable_keys"]).intersection(
                target_keys
            ):
                continue
            if (
                not maintenance
                and difficulty_targets is not None
                and abs(float(difficulty) - aim_for(candidate)) > 1.0
            ):
                # 天花板：高出瞄准值太多的题不硬塞给薄弱学生，宁可如实报缺口。
                continue
            result.append(candidate)
        if maintenance:
            result.sort(key=lambda item: int(item["question_id"]))
            return result
        # 按目标分组后轮流取题：每个目标先各拿一道最接近瞄准难度的题，
        # 避免直接巩固被排名最靠前的细点独占。
        groups: dict[int, list[dict[str, Any]]] = {}
        for item in result:
            rank = _target_rank(item["stable_keys"], target_keys)
            groups.setdefault(rank, []).append(item)
        for group in groups.values():
            group.sort(
                key=lambda item: (
                    abs(float(item["difficulty"]) - aim_for(item)),
                    int(item["question_id"]),
                )
            )
        interleaved: list[dict[str, Any]] = []
        while True:
            progressed = False
            for rank in sorted(groups):
                group = groups[rank]
                if group:
                    interleaved.append(group.pop(0))
                    progressed = True
            if not progressed:
                break
        return interleaved

    def _source_snapshot(
        self,
        *,
        excluded_question_ids: set[int] | None = None,
        prepare_refinements: bool = False,
        knowledge_keys: Sequence[str] = (),
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        str,
    ]:
        excluded_ids = excluded_question_ids or set()
        pool_keys = set(knowledge_keys)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT q.id, q.question_number, q.question_text, q.answer_text, q.image_paths, q.has_images,
                       q.question_type, q.difficulty, q.updated_at,
                       p.title AS paper_title, p.grade AS paper_grade,
                       p.semester AS paper_semester
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                  AND EXISTS (
                      SELECT 1 FROM training_criterion_heads h
                      WHERE h.question_id = q.id
                  )
                ORDER BY q.id
                """
            ).fetchall()
            knowledge_rows = connection.execute(
                """
                SELECT qt.question_id, qt.tag_value
                FROM question_tags qt
                WHERE qt.tag_type IN (
                    'knowledge_point', 'canonical_knowledge_id'
                )
                  AND TRIM(COALESCE(qt.tag_value, '')) <> ''
                ORDER BY qt.question_id, qt.id
                """
            ).fetchall()
            skill_rows = connection.execute(
                """
                SELECT qt.question_id, qt.tag_type, qt.tag_value
                FROM question_tags qt
                WHERE qt.tag_type IN ('method', 'model', 'thought', 'special_type')
                  AND TRIM(COALESCE(qt.tag_value, '')) <> ''
                ORDER BY qt.question_id, qt.id
                """
            ).fetchall()
            # Cheap identity-only preselection includes every stored criterion
            # version. Live source and approved/current precedence are still
            # checked below; a stale version can widen a read, never admit a题.
            evidence_rows = connection.execute(
                "SELECT question_id, criteria_json FROM training_criterion_versions "
                "WHERE status IN ('approved', 'proposed')"
            ).fetchall() if pool_keys else []
        stable_by_question: dict[int, list[dict[str, str]]] = {}
        source_ids = {int(row["id"]) for row in rows}
        from question_bank.solution_evidence.part_assessments import load_profiles, direct_targets
        profiles = {}
        profile_ids = sorted(source_ids)
        for start in range(0, len(profile_ids), 64):
            profiles.update(load_profiles(self.db_path, profile_ids[start:start + 64],
                                          verify_source=not pool_keys, data_root=self.data_root))
        resolved_values: dict[str, tuple[Any, ...]] = {}
        for row in knowledge_rows:
            if int(row["question_id"]) not in source_ids:
                continue
            bucket = stable_by_question.setdefault(int(row["question_id"]), [])
            value = str(row["tag_value"])
            if value not in resolved_values:
                resolved_values[value] = self.current_knowledge.resolve(value)
            for resolved in resolved_values[value]:
                item = {
                    "stable_key": resolved.stable_key,
                    "display_name": resolved.display_name,
                }
                if item not in bucket:
                    bucket.append(item)
        if pool_keys:
            # Stored identities cheaply narrow the pool. Full source checking
            # still runs for every retained question before it can be used.
            evidence_keys: dict[int, set[str]] = {}
            for stored in evidence_rows:
                try:
                    payload = json.loads(stored["criteria_json"])
                    evidence = payload.get("solution_evidence") or {}
                    keys = _question_evidence_metadata(evidence, self.current_knowledge)["stable_keys"]
                    evidence_keys.setdefault(int(stored["question_id"]), set()).update(keys)
                except (ValueError, TypeError, AttributeError):
                    continue
            matching_ids = {
                question_id for question_id in source_ids - excluded_ids
                if pool_keys.intersection(
                    {item["stable_key"] for item in stable_by_question.get(question_id, [])}
                    | {key for part in profiles.get(question_id, {}).get("evidence", {}).get("parts", [])
                       for key in direct_targets(part)}
                    | evidence_keys.get(question_id, set())
                )
            }
            source_ids = matching_ids
            rows = [row for row in rows if int(row["id"]) in matching_ids]
            profiles = {}
            profile_ids = sorted(matching_ids)
            for start in range(0, len(profile_ids), 64):
                profiles.update(load_profiles(self.db_path, profile_ids[start:start + 64], data_root=self.data_root))
        source_tag_keys = {question_id: sorted(item["stable_key"] for item in identities)
                           for question_id, identities in stable_by_question.items()}
        for question_id, profile in profiles.items():
            # Refined questions only target the knowledge directly demonstrated
            # by a small part. Prerequisites are not independent test targets.
            identities = []
            if profile["available"]:
                for part in profile["evidence"]["parts"]:
                    for key in direct_targets(part):
                        for resolved in self.current_knowledge.resolve(key):
                            item = {"stable_key": resolved.stable_key, "display_name": resolved.display_name}
                            if item not in identities:
                                identities.append(item)
            stable_by_question[question_id] = identities
        skill_by_question: dict[int, dict[str, list[str]]] = {}
        for row in skill_rows:
            if int(row["question_id"]) not in source_ids:
                continue
            bucket = skill_by_question.setdefault(
                int(row["question_id"]), {}
            ).setdefault(str(row["tag_type"]), [])
            value = str(row["tag_value"]).strip()
            if value not in bucket:
                bucket.append(value)
        loader = QuestionAnalysisInputLoader(
            db_path=self.db_path,
            data_root=self.data_root,
        )
        criteria = TrainingCriterionModule(self.db_path, data_root=self.data_root)
        candidate_rows: dict[int, Any] = {}
        for row in rows:
            question_id = int(row["id"])
            if question_id in excluded_ids:
                continue
            candidate_rows[question_id] = row
        candidate_ids = list(candidate_rows)
        candidates: list[dict[str, Any]] = []
        image_cache: dict[str, str] = {}
        # Keep the full usable source snapshot so existing drafts retain their
        # source versions. Volume/difficulty gates still run during selection.
        # Bound image memory and isolate bad sources as in the single reader.
        for start in range(0, len(candidate_ids), 64):
            ids = candidate_ids[start:start + 64]
            try:
                questions = loader.load(ids)
            except (KeyError, OSError, ValueError):
                recovered = []
                for question_id in ids:
                    try:
                        recovered.extend(loader.load((question_id,)))
                    except (KeyError, OSError, ValueError):
                        continue
                questions = tuple(recovered)
            try:
                if prepare_refinements:
                    for question in questions:
                        profile = profiles.get(question.question_id)
                        if profile:
                            try:
                                criteria.prepare_part_refinement(question, profile)
                            except (KeyError, OSError, ValueError):
                                continue
                workspaces = criteria.read_many(questions)
            except (KeyError, OSError, ValueError):
                workspaces = {}
                for question in questions:
                    try:
                        workspaces[question.question_id] = criteria.read(question)
                    except (KeyError, OSError, ValueError):
                        continue
            for question in questions:
                question_id = question.question_id
                workspace = workspaces.get(question_id)
                if workspace is None:
                    continue
                row = candidate_rows[question_id]
                identities = stable_by_question.get(question_id, [])
                usable = usable_training_criterion(workspace)
                if usable is None:
                    continue
                difficulty = _difficulty(row["difficulty"])
                profile = profiles.get(question_id)
                assessment = None
                if profile and profile["available"]:
                    evidence_parts = {part["part_id"]: part for part in profile["evidence"]["parts"]}
                    parts = [
                        {**part, "label": evidence_parts[part["part_id"]].get("label") or part["part_id"],
                         "direct_keys": list(direct_targets(evidence_parts[part["part_id"]]))}
                        for part in profile["parts"]
                    ]
                    # A recommendation prints the whole question. Its hardest
                    # part must respect the student's difficulty ceiling.
                    if any(part["difficulty"] is None for part in parts):
                        continue
                    difficulty = math.ceil(max(part["difficulty"] for part in parts))
                    assessment = {"profile_revision": profile["revision"],
                                  "evidence_version_id": profile["evidence_version_id"],
                                  "selection_basis": "hardest_part", "parts": parts}
                criterion = usable.get("criteria")
                evidence = criterion.get("solution_evidence") if isinstance(criterion, Mapping) else None
                if not isinstance(evidence, Mapping) and profile and profile["available"]:
                    evidence = profile["evidence"]
                metadata = (_question_evidence_metadata(evidence, self.current_knowledge)
                            if isinstance(evidence, Mapping) else {
                                "stable_keys": [item["stable_key"] for item in identities],
                                "required_keys": [item["stable_key"] for item in identities],
                                "scope_complete": False, "response_modes_by_key": {}, "practice_observations_by_key": {}})
                identities = [{"stable_key": key, "display_name": self.current_knowledge.node(key).display_name}
                              for key in metadata["stable_keys"] if self.current_knowledge.node(key) is not None]
                if not identities or (pool_keys and not pool_keys.intersection(metadata["stable_keys"])):
                    continue
                points = (
                    criterion.get("points")
                    if isinstance(criterion, Mapping)
                    else None
                )
                stable_keys = [item["stable_key"] for item in identities]
                skill_tags = skill_by_question.get(question_id, {})
                method_tags = skill_tags.get("method", [])
                model_tags = skill_tags.get("model", [])
                candidates.append(
                    {
                        "question_id": question_id,
                        "duplicate_identity": exact_question_key(dict(row), data_root=self.data_root, image_cache=image_cache) or str(question_id),
                        "practice_identity": exam_original_key(dict(row), data_root=self.data_root, image_cache=image_cache),
                        "question_number": str(
                            row["question_number"] or question_id
                        ),
                        "question_type": str(row["question_type"] or ""),
                        "special_types": list(skill_tags.get("special_type", [])),
                        "question_text": str(row["question_text"] or ""),
                        "image_identity": tuple(sorted(image.sha256 for image in question.images)),
                        "source_paper": str(row["paper_title"] or ""),
                        "paper_level_rank": _paper_level_rank(
                            row["paper_grade"], row["paper_semester"]
                        ),
                        "difficulty": difficulty,
                        "part_assessment": assessment,
                        "stable_keys": stable_keys,
                        "required_keys": metadata["required_keys"],
                        "scope_complete": metadata["scope_complete"],
                        "response_modes_by_key": metadata["response_modes_by_key"],
                        "practice_observations_by_key": metadata["practice_observations_by_key"],
                        "stable_names": {
                            item["stable_key"]: item["display_name"]
                            for item in identities
                        },
                        "text_fingerprint": _text_fingerprint(
                            row["question_text"]
                        ),
                        "similarity_profile": _similarity_profile(
                            difficulty=difficulty,
                            stable_keys=stable_keys,
                            method_tags=method_tags,
                            model_tags=model_tags,
                            thought_tags=skill_tags.get("thought", []),
                        ),
                        "criterion_version_id": str(usable["version_id"]),
                        "criterion_point_count": (
                            len(points) if isinstance(points, list) else 0
                        ),
                        "question_revision": str(row["updated_at"] or ""),
                        "source_tag_keys": source_tag_keys.get(question_id, []),
                    }
                )
        relations = tuple(
            {
                "relation_id": relation.relation_key,
                "relation_key": relation.relation_key,
                "source_key": relation.source_key,
                "target_key": relation.target_key,
                "relation_type": relation.relation_type,
                "rationale": relation.rationale,
                "basis_kind": relation.basis_kind,
                "strength": relation.strength,
                "evidence_source_ids": list(
                    relation.evidence_source_ids
                ),
                "source_locator": relation.source_locator,
                "revision": 1,
            }
            for relation in self.current_knowledge.relations
        )
        distinct: dict[str, dict[str, Any]] = {}
        for candidate in sorted(candidates, key=lambda item: int(item["question_id"])):
            distinct.setdefault(candidate["duplicate_identity"], candidate)
        normalized_candidates = tuple(distinct.values())
        source_version = _hash_payload(
            {
                "engine_version": ENGINE_VERSION,
                "candidates": normalized_candidates,
                "source_questions": [dict(row) for row in rows],
                "relations": relations,
                "current_mastery": self._current_mastery_version(),
            }
        )
        return normalized_candidates, relations, source_version

    def _mastery_snapshot(
        self,
        diagnosis: dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        calculated = CurrentMasteryCalculator(
            self.db_path,
            self.current_knowledge,
            clock=self.clock,
        ).calculate(diagnosis)
        snapshot = {
            identity: {
                "stable_key": item.stable_key,
                "display_name": item.display_name,
                "mode": "current",
                "status": item.status,
                "value": item.value,
                "evidence_count": item.evidence_count,
                "parameter_version": item.parameter_version,
                "source_question_refs": [],
                "explanations": (
                    [] if item.reason is None else [item.reason]
                ),
            }
            for identity, item in calculated.items()
        }
        _apply_diagnosis_mastery(
            snapshot,
            diagnosis,
            resolver=self.current_knowledge,
        )
        return snapshot

    def _current_mastery_version(self) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            profile_revisions = [tuple(row) for row in connection.execute(
                "SELECT question_id,revision,status,current_source_content_hash FROM question_part_assessment_profiles ORDER BY question_id,revision"
            )] if connection.execute("SELECT 1 FROM sqlite_master WHERE name='question_part_assessment_profiles'").fetchone() else []
            evidence_rows = [
                (
                    str(row["evidence_id"]),
                    str(row["payload_hash"]),
                    str(row["status"]),
                    int(row["source_review_revision"]),
                )
                for row in connection.execute(
                    """
                    SELECT evidence_id, payload_hash, status,
                           source_review_revision
                    FROM training_evidence_records
                    ORDER BY evidence_id
                    """
                ).fetchall()
            ]
        return {
            "formula": CURRENT_MASTERY_PARAMETERS.formula_version,
            "parameter_version": CURRENT_MASTERY_PARAMETERS.version,
            "graph_release_id": self.current_knowledge.release_id,
            "training_evidence_version": _hash_payload(evidence_rows),
            "part_assessment_version": _hash_payload(profile_revisions),
        }

    def _recent_question_ids(
        self,
        student_ids: tuple[str, ...],
        *,
        exclude_draft_id: str | None = None,
    ) -> dict[str, set[int]]:
        if not student_ids:
            return {}
        cutoff = (
            _day_clock(self.clock()) - timedelta(days=RECENT_WINDOW_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in student_ids)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT students.student_id, items.bank_question_id
                FROM variant_students students
                JOIN training_variants variants
                  ON variants.id = students.variant_id
                JOIN training_tasks tasks
                  ON tasks.id = variants.task_id
                JOIN training_task_items items
                  ON items.variant_id = variants.id
                WHERE students.student_id IN ({placeholders})
                  AND items.bank_question_id IS NOT NULL
                  AND tasks.status IN ('ready', 'exporting', 'completed')
                  AND tasks.created_at >= ?
                """,
                (*student_ids, cutoff),
            ).fetchall()
            # 个性化卷的"已练"只统计真正回流过已发布训练证据的题；
            # 仅生成/冻结但未批改回流的卷不占去重名额，
            # 否则教师反复调整草稿时候选会越调越少。
            personalized_rows = connection.execute(
                f"""
                SELECT evidence.student_id, items.bank_question_id
                FROM training_evidence_records evidence
                JOIN personalized_paper_items items
                  ON items.task_item_code = evidence.task_item_code
                WHERE evidence.student_id IN ({placeholders})
                  AND items.bank_question_id IS NOT NULL
                  AND evidence.created_at >= ?
                """,
                (*student_ids, cutoff),
            ).fetchall()
        result: dict[str, set[int]] = {}
        for row in (*rows, *personalized_rows):
            result.setdefault(str(row["student_id"]), set()).add(
                int(row["bank_question_id"])
            )
        if result:
            with connect(self.db_path) as connection:
                identities = exact_identity_map(connection, data_root=self.data_root)
            for sid, ids in result.items():
                keys = {identities[qid] for qid in ids if identities.get(qid)}
                result[sid] = ids | {qid for qid, key in identities.items() if key and key in keys}
        return result

    def _current_exam_question_ids(
        self, diagnosis: Mapping[str, Any]
    ) -> set[int]:
        exam_scope = diagnosis.get("exam_scope")
        if not isinstance(exam_scope, Mapping):
            return set()
        session_ids = tuple(
            str(value)
            for value in exam_scope.get("session_ids", ())
            if str(value).strip()
        )
        if not session_ids:
            return set()
        placeholders = ",".join("?" for _ in session_ids)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT bank_question_id
                FROM grading_question_links
                WHERE grading_session_id IN ({placeholders})
                  AND status = 'confirmed'
                ORDER BY bank_question_id
                """,
                session_ids,
            ).fetchall()
        ids = {int(row["bank_question_id"]) for row in rows}
        if not ids:
            return set()
        with connect(self.db_path) as connection:
            questions = connection.execute(
                "SELECT q.* FROM questions q LEFT JOIN papers p ON p.id=q.paper_id "
                "WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted' ORDER BY q.id"
            ).fetchall()
        image_cache: dict[str, str] = {}
        identities = {int(row["id"]): exam_original_key(dict(row), data_root=self.data_root,
                                                       image_cache=image_cache) for row in questions}
        keys = {identities[qid] for qid in ids if identities.get(qid)}
        return ids | {qid for qid, key in identities.items() if key and key in keys}

    def _by_request_token(self, token: str) -> dict[str, Any] | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT input_fingerprint, draft_json
                FROM personalized_recommendation_drafts
                WHERE request_token = ?
                """,
                (token,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(str(row["draft_json"]))
        result["_input_fingerprint"] = str(row["input_fingerprint"])
        return result

    def _apply_edit(
        self,
        draft: dict[str, Any],
        *,
        draft_id: str,
        request: dict[str, Any],
        command: RecommendationEditCommand,
        candidates: Sequence[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        student = next(
            (
                item
                for item in draft["students"]
                if item["student_id"] == command.student_id.strip()
            ),
            None,
        )
        if student is None:
            raise RecommendationEditInvalid("student is not in the draft")
        item = next(
            (
                value
                for value in student["items"]
                if value["item_id"] == command.item_id.strip()
            ),
            None,
        )
        if item is None:
            raise RecommendationEditInvalid("item is not in the draft")
        before = deepcopy(item)
        if command.action == "lock":
            item["locked"] = True
        elif command.action == "unlock":
            item["locked"] = False
        elif command.action == "exclude":
            if item["locked"]:
                raise RecommendationEditInvalid(
                    "locked item must be unlocked before exclusion"
                )
            student["items"].remove(item)
            _order_practice_items(student["items"])
            _add_edit_shortage(student, str(item["stage"]))
            after = {
                "item_id": item["item_id"],
                "question_id": None,
                "excluded": True,
            }
            _refresh_supplement_warnings(draft)
            return before, after
        else:
            if item["locked"]:
                raise RecommendationEditInvalid(
                    "locked item must be unlocked before replacement"
                )
            config = PersonalizedRecommendationConfig(
                **_config_constructor(request["config"])
            )
            source_profile = next(profile for profile in request["diagnosis"]["students"]
                                  if profile["student_id"] == student["student_id"])
            relations = tuple({"relation_type": relation.relation_type, "source_key": relation.source_key,
                               "target_key": relation.target_key} for relation in self.current_knowledge.relations)
            scope = _scope_leaves(config.group_scope_keys or config.scope_keys or config.target_keys,
                                  diagnosis=request["diagnosis"], relations=relations)
            entries, _ = self._candidate_entries(
                profile=source_profile, targets=(item["target"],), candidates=candidates, metadata={},
                config=config, supplement_keys=scope or (str(item["matched_key"]),),
                recent=self._recent_question_ids((str(student["student_id"]),), exclude_draft_id=draft_id)
                           .get(str(student["student_id"]), set()),
                excluded=self._current_exam_question_ids(request["diagnosis"]) if config.exclude_current_exam_originals else set())
            by_id = {candidate["question_id"]: candidate for candidate in candidates}
            printed = [by_id[value["question_id"]] for value in student["items"] if value is not item]
            eligible = [entry for entry in entries
                        if entry["candidate"]["question_id"] != item["question_id"]
                        and (item.get("selection_kind") == "supplement" or entry["selection_kind"] == "direct")
                        and _paper_diversity_allowed(entry["candidate"], printed)
                        and (command.replacement_question_id is None
                             or entry["candidate"]["question_id"] == command.replacement_question_id)]
            if not eligible:
                raise RecommendationEditInvalid("no approved replacement is available")
            selected = min(eligible, key=lambda entry: (entry["selection_kind"] != "direct", entry["distance"],
                                                       -entry["preference"], entry["candidate"]["question_id"]))
            replacement = _draft_item(
                selected["candidate"], stage=item["stage"], slot=int(item["slot"]), student_id=student["student_id"],
                target=selected["target"], matched_key=selected["matched_key"], maintenance=False,
                selection_kind=selected["selection_kind"])
            replacement["beneficiary_student_ids"] = [student["student_id"]]
            replacement["item_id"] = item["item_id"]
            replacement["item_order"] = item["item_order"]
            # 保留原失分来源作为难度依据；补充／直接匹配说明随替换结果更新。
            replacement["replacement_history"] = [
                *item.get("replacement_history", []),
                {
                    "question_id": item["question_id"],
                    "reason": command.reason.strip(),
                    "actor_ref": command.actor_ref.strip(),
                    "revision": int(draft["revision"]) + 1,
                },
            ]
            student["items"][student["items"].index(item)] = replacement
            _order_practice_items(student["items"])
            item = replacement
            _refresh_supplement_warnings(draft)
        after = deepcopy(item)
        return before, after


def _normalize_diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    students = value.get("students")
    if not isinstance(students, list) or not students:
        raise ValueError("diagnosis must contain students")
    normalized_students: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in students:
        if not isinstance(raw, Mapping):
            raise ValueError("diagnosis student is invalid")
        student_id = _required_text(raw.get("student_id"), "student_id")
        if student_id in seen:
            raise ValueError("diagnosis contains duplicate students")
        seen.add(student_id)
        weak_points = raw.get("weak_points")
        normalized_students.append(
            {
                "student_id": student_id,
                "student_code": str(raw.get("student_code") or "").strip(),
                "student_name": str(raw.get("student_name") or "").strip(),
                "class_id": str(raw.get("class_id") or "").strip(),
                "score_rate": _rate(raw.get("score_rate")),
                "score_rate_source": str(raw.get("score_rate_source") or "none"),
                "weak_points": [
                    dict(item)
                    for item in (
                        weak_points if isinstance(weak_points, list) else []
                    )
                    if isinstance(item, Mapping)
                ],
            }
        )
    exam_scope = value.get("exam_scope")
    return {
        "students": sorted(
            normalized_students, key=lambda item: item["student_id"]
        ),
        "exam_scope": (
            {
                "mode": str(exam_scope.get("mode") or ""),
                "session_ids": sorted(
                    {
                        int(item)
                        for item in exam_scope.get("session_ids", [])
                        if int(item) > 0
                    }
                ),
            }
            if isinstance(exam_scope, Mapping)
            else {"mode": "", "session_ids": []}
        ),
        "_mastery_session_times": dict(
            value.get("_mastery_session_times")
            if isinstance(value.get("_mastery_session_times"), Mapping)
            else {}
        ),
        "knowledge_catalog": [
            dict(item)
            for item in (
                value.get("knowledge_catalog")
                if isinstance(value.get("knowledge_catalog"), list)
                else []
            )
            if isinstance(item, Mapping) and str(item.get("knowledge_key") or "").strip()
        ],
    }


def _group_needs(diagnosis: Mapping[str, Any], leaves: Sequence[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Only supported direct losses form a need; absent/coarse evidence stays unknown."""
    allowed = set(leaves)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for student in diagnosis.get("students", []):
        needs: dict[str, dict[str, Any]] = {}
        for point in student.get("weak_points", []):
            key = str(point.get("knowledge_key") or "")
            value = _rate(point.get("mastery"))
            if key not in allowed or value is None or not point.get("evidence_count"):
                continue
            direct: dict[tuple[Any, ...], Mapping[str, Any]] = {}
            for ref in _loss_refs(point):
                assessment = ref.get("assessment") or {}
                if (assessment.get("granularity") not in {"part", "step"}
                        or assessment.get("eligible") is False
                        or float(assessment.get("evidence_weight", 1.0)) < .999
                        or float(ref.get("full_score") or 0) <= 0):
                    continue
                direct[(ref.get("session_id"), ref.get("question_id"), ref.get("bank_question_id"))] = ref
            losses = [ref for ref in direct.values() if float(ref.get("score_awarded") or 0) < float(ref["full_score"])]
            training_count = int(point.get("precise_training_evidence_count") or 0)
            if not losses:
                continue
            difficulties = [float(ref["assessment"]["part_difficulty"]) for ref in losses
                            if ref["assessment"].get("part_difficulty") is not None]
            kind = ("basic" if any(d <= 4 for d in difficulties)
                    else "application" if difficulties and len(difficulties) == len(losses) and min(difficulties) >= 7
                    else "unspecified")
            count = len(direct) + training_count
            needs[key] = {"knowledge_key": key, "knowledge_point": str(point.get("knowledge_point") or key),
                          "mastery": value, "score_rate": _rate(student.get("score_rate")), "evidence_count": count, "performance_kind": kind,
                          "weight": (1.0 - value) * (.75 + .25 * min(3, count) / 3),
                          "source_question_refs": list(direct.values()),
                          "difficulty_unknown": any(ref["assessment"].get("part_difficulty") is None for ref in losses)}
        result[str(student["student_id"])] = needs
    return result


def _group_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    if not left or not right:
        return 0.0
    def level(needs):
        overall = [_rate(item.get("score_rate")) for item in needs.values()]
        known = [value for value in overall if value is not None]
        return median(known) if known else median(item["mastery"] for item in needs.values())
    distance = abs(level(left) - level(right))
    if distance > .25 + 1e-9:
        return 0.0
    # Similar overall levels can share a paper even when their weak points differ.
    return 1.0 - distance


def _chapter_group_members(needs: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """Group comparable overall levels; target sets may overlap or be disjoint."""
    def level(sid):
        values = [_rate(item.get("score_rate")) for item in needs[sid].values()]
        known = [value for value in values if value is not None]
        return median(known) if known else median(item["mastery"] for item in needs[sid].values())
    remaining = sorted((sid for sid in needs if needs[sid]), key=lambda sid: (level(sid), sid))
    groups = []
    while remaining:
        members = [remaining.pop(0)]
        for sid in list(remaining):
            if all(_group_similarity(needs[sid], needs[member]) >= GROUP_MIN_SIMILARITY for member in members):
                members.append(sid)
                remaining.remove(sid)
        if len(members) >= 2:
            groups.append(tuple(sorted(members)))
    return sorted(groups)


_LEADING_ENUM = re.compile(r"^\s*\d+\s*[.、．)]\s*")
_WHITESPACE = re.compile(r"[\s　]+")


def _text_fingerprint(text: object) -> str:
    """题干规范化指纹：去题号、去空白、小写化，识别跨试卷引用的同一题。"""
    value = _LEADING_ENUM.sub("", str(text or ""), count=1)
    return _WHITESPACE.sub("", value).casefold()


def _similarity_profile(
    *,
    difficulty: int | None,
    stable_keys: Sequence[str],
    method_tags: Sequence[str],
    model_tags: Sequence[str],
    thought_tags: Sequence[str] = (),
) -> dict[str, Any]:
    """候选题相似度画像，形状对齐考频服务的标签加权相似度口径。"""
    tags = [
        {"tag_type": "current_knowledge_key", "tag_value": key}
        for key in stable_keys
    ]
    tags.extend(
        {"tag_type": "method", "tag_value": value} for value in method_tags
    )
    tags.extend(
        {"tag_type": "model", "tag_value": value} for value in model_tags
    )
    tags.extend(
        {"tag_type": "thought", "tag_value": value} for value in thought_tags
    )
    return {"difficulty": difficulty, "tags": tags}


def _draft_item(
    candidate: Mapping[str, Any],
    *,
    stage: Stage,
    slot: int,
    student_id: str,
    target: Mapping[str, Any],
    matched_key: str,
    maintenance: bool,
    fallback: bool = False,
    enrichment: bool = False,
    practice_tasks: Sequence[Mapping[str, Any]] | None = None,
    selection_kind: Literal["direct", "supplement"] = "direct",
) -> dict[str, Any]:
    item_id = _hash_payload(
        {
            "student_id": student_id,
            "stage": stage,
            "slot": slot,
        }
    )[:20]
    relation = target.get("relation")
    matched_name = candidate["stable_names"].get(matched_key, matched_key)
    sources = _loss_refs(target)
    source = sources[0] if sources else {}
    question_number = str(source.get("question_id") or "未知")
    original = (source.get("assessment") or {}).get("part_difficulty")
    basis = "失分小问"
    if original is None:
        original = source.get("question_difficulty")
        basis = "原题整体"
    aim = target.get("target_difficulty")
    reason = (f"补充练习：选定范围内的 {matched_name}，按原错题难度补足题量，不作为此知识点薄弱的证据。"
              if selection_kind == "supplement" else f"对应错题 {question_number} 的知识点，练习 {matched_name}。")
    if original is not None and aim is not None:
        score_basis = "结合整体成绩与该题失分" if target.get("overall_score_rate") is not None else "整体成绩缺失，仅依据该题作答"
        reason += f" {basis}难度 {float(original):g} 级，{score_basis}，目标 {float(aim):.1f} 级；本题 {candidate['difficulty']} 级（受难度上限约束）。"
    else:
        reason += " 来源难度证据不足，请重新生成以核对适用难度。"
    tasks = [task for task in (practice_tasks if practice_tasks is not None else target.get("training_tasks") or [])
             if selection_kind == "direct" and _practice_matches(candidate, matched_key, [task])]
    if tasks:
        reason += " 本题训练任务：" + "；".join(str(task["label"]) for task in tasks) + "。"
    return {
        "item_id": item_id,
        "item_order": 0,
        "slot": slot,
        "question_id": int(candidate["question_id"]),
        "question_number": str(candidate["question_number"]),
        "question_text": str(candidate["question_text"]),
        "stage": stage,
        "selection_kind": selection_kind,
        "target": {
            key: deepcopy(value)
            for key, value in target.items()
            if key != "relation"
        },
        "matched_key": matched_key,
        "matched_name": candidate["stable_names"].get(
            matched_key, matched_key
        ),
        "relation": deepcopy(relation),
        "criterion_version_id": candidate["criterion_version_id"],
        "criterion_point_count": candidate["criterion_point_count"],
        "difficulty": candidate["difficulty"],
        "part_assessment": deepcopy(candidate.get("part_assessment")),
        "practice_tasks": deepcopy(tasks),
        "response_modes": list(candidate.get("response_modes_by_key", {}).get(matched_key, [])),
        "source_paper": candidate["source_paper"],
        "reason": reason,
        "locked": False,
        "replacement_history": [],
    }


def _difficulty(value: object) -> int | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 1 <= parsed <= 10:
        return None
    return int(round(parsed))


def _rate(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return min(max(parsed, 0.0), 1.0)


def _target_rank(
    stable_keys: Sequence[str], target_keys: Sequence[str]
) -> int:
    positions = {
        value: index for index, value in enumerate(target_keys)
    }
    return min(
        (positions[value] for value in stable_keys if value in positions),
        default=len(positions),
    )


def _matched_key(
    stable_keys: Sequence[str], target_keys: Sequence[str]
) -> str:
    positions = {value: index for index, value in enumerate(target_keys)}
    matched = sorted(
        (value for value in stable_keys if value in positions),
        key=lambda value: (positions[value], value),
    )
    return matched[0] if matched else str(stable_keys[0])


def _relation_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "relation_id": str(value["relation_id"]),
        "relation_type": str(value["relation_type"]),
        "source_key": str(value["source_key"]),
        "target_key": str(value["target_key"]),
        "rationale": str(value["rationale"]),
        "revision": int(value["revision"]),
    }


def _add_edit_shortage(student: dict[str, Any], stage: str) -> None:
    selected_count = sum(
        1 for item in student["items"] if item["stage"] == stage
    )
    shortage = next(
        (
            item
            for item in student["shortages"]
            if item["stage"] == stage
        ),
        None,
    )
    if shortage is None:
        student["shortages"].append(
            {
                "stage": stage,
                "requested_count": selected_count + 1,
                "selected_count": selected_count,
                "missing_count": 1,
                "reason_code": "teacher_excluded",
            }
        )
    else:
        shortage["selected_count"] = max(
            0, int(shortage["selected_count"]) - 1
        )
        shortage["missing_count"] += 1
        shortage["reason_code"] = "teacher_excluded"
    student["warnings"].append("教师排除了一道题，草稿保留空缺，不自动模糊补题。")
    student["warnings"] = list(dict.fromkeys(student["warnings"]))


def _config_constructor(value: Mapping[str, Any]) -> dict[str, Any]:
    ratios = value.get("stage_ratios")
    return {
        "paper_mode": value.get("paper_mode", "individual"),
        "question_count": value["question_count"],
        "expected_minutes": value.get("expected_minutes", 45),
        "difficulty_min": value.get("difficulty_min", 1),
        "difficulty_max": value.get("difficulty_max", 7),
        "direct_ratio": (
            ratios["direct"]
            if isinstance(ratios, Mapping)
            else value.get("direct_ratio", 1)
        ),
        "prerequisite_ratio": (
            ratios["prerequisite"]
            if isinstance(ratios, Mapping)
            else value.get("prerequisite_ratio", 0)
        ),
        "transfer_ratio": (
            ratios["transfer"]
            if isinstance(ratios, Mapping)
            else value.get("transfer_ratio", 0)
        ),
        "target_keys": tuple(value.get("target_keys") or ()),
        "scope_keys": tuple(value.get("scope_keys") or ()),
        "exclude_current_exam_originals": bool(
            value.get("exclude_current_exam_originals", True)
        ),
        "curriculum_volume_id": str(
            value.get("curriculum_volume_id") or ""
        ),
        "group_scope_keys": tuple(value.get("group_scope_keys") or ()),
        "group_source_version": str(value.get("group_source_version") or ""),
        "training_intent": str(value.get("training_intent") or "remediation"),
        "teaching_progress_chapter_id": str(value.get("teaching_progress_chapter_id") or ""),
    }


def _past_action(action: Action) -> str:
    return {
        "lock": "locked",
        "unlock": "unlocked",
        "exclude": "excluded",
        "replace": "replaced",
    }[action]


def _context_source_version(
    base_source_version: str,
    *,
    as_of: datetime,
    recent: Mapping[str, set[int]],
    excluded_question_ids: set[int],
) -> str:
    return _hash_payload(
        {
            "base_source_version": base_source_version,
            "as_of_day": as_of.date().isoformat(),
            "recent_question_ids": {
                student_id: sorted(question_ids)
                for student_id, question_ids in sorted(recent.items())
            },
            "excluded_question_ids": sorted(excluded_question_ids),
        }
    )


def _identity_keys(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(
            str(value or "").strip().casefold()
            for value in values
            if str(value or "").strip()
        )
    )
    if len(normalized) > 50:
        raise ValueError(f"{field} contains too many values")
    if any(
        not (value.startswith("kp_") or value.startswith("ki_"))
        for value in normalized
    ):
        raise ValueError(f"{field} must use governed stable identities")
    return normalized


def _scope_leaves(
    scope_keys: Sequence[str],
    *,
    diagnosis: Mapping[str, Any],
    relations: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    if not scope_keys:
        return ()
    children: dict[str, list[str]] = {}

    def _add_child(parent: str, child: str) -> None:
        if not parent or not child or parent == child:
            return
        bucket = children.setdefault(parent, [])
        if child not in bucket:
            bucket.append(child)

    for relation in relations:
        if str(relation.get("relation_type") or "") != "parent":
            continue
        _add_child(str(relation.get("target_key") or ""), str(relation.get("source_key") or ""))
    catalog = diagnosis.get("knowledge_catalog")
    if isinstance(catalog, list):
        for item in catalog:
            if not isinstance(item, Mapping):
                continue
            _add_child(
                str(item.get("parent_knowledge_key") or "").strip().casefold(),
                str(item.get("knowledge_key") or "").strip().casefold(),
            )
    leaves: list[str] = []
    seen: set[str] = set()

    def walk(node: str, trail: frozenset[str]) -> None:
        if not node or node in trail:
            return
        kids = children.get(node, ())
        if not kids:
            if node not in seen:
                seen.add(node)
                leaves.append(node)
            return
        next_trail = trail | {node}
        for kid in kids:
            walk(kid, next_trail)

    for key in scope_keys:
        walk(str(key), frozenset())
    return tuple(leaves)


def _apply_diagnosis_mastery(
    snapshot: dict[tuple[str, str], dict[str, Any]],
    diagnosis: Mapping[str, Any],
    *,
    resolver: CurrentKnowledgeResolver,
) -> None:
    """Prefer mastery already shown on the diagnosis page over a second pass."""

    students = diagnosis.get("students")
    if not isinstance(students, list):
        return
    for profile in students:
        if not isinstance(profile, Mapping):
            continue
        student_id = str(profile.get("student_id") or "").strip()
        if not student_id:
            continue
        weak_points = profile.get("weak_points")
        if not isinstance(weak_points, list):
            continue
        for item in weak_points:
            if not isinstance(item, Mapping):
                continue
            raw_value = item.get("mastery")
            if raw_value is None:
                continue
            try:
                value = float(raw_value)
                count = int(item.get("evidence_count") or 0)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            raw_key = str(
                item.get("knowledge_key") or item.get("knowledge_point") or ""
            ).strip()
            if not raw_key:
                continue
            resolved = resolver.resolve(raw_key)
            keys = [match.stable_key for match in resolved]
            if not keys and raw_key.casefold().startswith(("kp_", "ki_")):
                keys = [raw_key.casefold()]
            display = str(item.get("knowledge_point") or "").strip()
            for key in keys:
                node = resolver.node(key)
                existing = snapshot.get((student_id, key), {})
                snapshot[(student_id, key)] = {
                    "stable_key": key,
                    "display_name": (
                        node.display_name if node is not None else display or key
                    ),
                    "mode": "current",
                    "status": "available",
                    "value": value,
                    "evidence_count": count,
                    "parameter_version": str(
                        existing.get("parameter_version") or ""
                    ),
                    "source_question_refs": deepcopy(item.get("source_question_refs") or []),
                    "explanations": deepcopy(item.get("actionable_reasons") or []),
                    "actionable_reasons": deepcopy(item.get("actionable_reasons") or []),
                    "error_counts": deepcopy(item.get("error_counts") or {}),
                    "tag_context": deepcopy(item.get("tag_context") or {}),
                    "training_tasks": _training_tasks(item),
                }


def _day_clock(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clock must include timezone")
    current = value.astimezone(UTC)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if len(token) != 32 or any(
        character not in "0123456789abcdef" for character in token
    ):
        raise ValueError("request_token must be 32 hexadecimal characters")
    return token


def _draft_id(value: object) -> str:
    draft_id = str(value or "").strip().casefold()
    if len(draft_id) != 64 or any(
        character not in "0123456789abcdef" for character in draft_id
    ):
        raise ValueError("draft_id must be a 64-character hash")
    return draft_id


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be blank")
    return text


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "ENGINE_VERSION",
    "PersonalizedRecommendationConfig",
    "PersonalizedRecommendationError",
    "PersonalizedRecommendationModule",
    "RecommendationDraftNotFound",
    "RecommendationEditCommand",
    "RecommendationEditInvalid",
    "RecommendationRequestConflict",
    "RecommendationRevisionConflict",
    "RecommendationSourceChanged",
]
