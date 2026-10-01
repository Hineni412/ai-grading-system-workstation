from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Literal

from integration.data_generation import commit_generation
from integration.result_cache import ResultCache
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.database.schema import connect
from question_bank.mastery.current import (
    CURRENT_MASTERY_PARAMETERS,
    CurrentMasteryCalculator,
)
from question_bank.recommendation.recommendation_engine import text_similarity
from question_bank.recommendation.target_matching import (
    MATCH_LABELS,
    _question_evidence_metadata,
    match_target,
    part_facets,
    target_index,
    topic_keys,
)
from question_bank.services import standard_difficulty
from question_bank.services.duplicate_analysis_copy_service import (
    exact_question_key,
    exam_original_key,
    exam_original_text_key,
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

ENGINE_VERSION = "personalized-recommendation-v17-evidence-priority"
GROUPING_VERSION = "chapter-skill-coverage-v7-coverage"
GROUP_MIN_SIMILARITY = 0.58
RECENT_ACTIVITY_COUNT = 3
MAX_QUESTIONS_PER_SKILL = 1
# Read-only _source_snapshot results, keyed on the question-bank commit
# generation + release + full request inputs; pickle bytes with single-flight.
_SOURCE_SNAPSHOT_CACHE = ResultCache(limit=8)
Stage = Literal["direct", "prerequisite", "transfer"]
Action = Literal["lock", "unlock", "exclude", "replace"]
_PRACTICE_TAG_KINDS = ("method", "model", "thought", "ability", "special_type")


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
    difficulty_max: float = 8
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
        object.__setattr__(self, "difficulty_max", min(8., float(self.difficulty_max)))
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
               if key not in {"expected_minutes", "difficulty_min", "training_intent"}},
            "target_keys": list(self.target_keys),
            "scope_keys": list(self.scope_keys),
            "group_scope_keys": list(self.group_scope_keys),
            "recent_activity_count": RECENT_ACTIVITY_COUNT,
        }


def _allowed_keys_for_volume(volume_id: str, resolver: CurrentKnowledgeResolver | None = None) -> frozenset[str] | None:
    """Knowledge keys in the selected volume and every earlier volume.

    Returns None when no volume is selected, keeping candidate selection
    unbounded for legacy drafts and requests.
    """
    clean = str(volume_id or "").strip()
    if not clean:
        return None
    keys = frozenset(
        str(node["id"]) for node in eligible_curriculum_knowledge_nodes(clean)
    )
    return _include_published_skills(keys, resolver)


def _include_published_skills(keys: frozenset[str], resolver: CurrentKnowledgeResolver | None) -> frozenset[str]:
    if resolver is None:
        return keys
    return keys | frozenset(relation.source_key for relation in resolver.relations
                            if relation.relation_type == "parent"
                            and relation.source_key.startswith("sk_")
                            and relation.target_key in keys)


@lru_cache(maxsize=128)
def _progress_chapter(config: PersonalizedRecommendationConfig, resolver: CurrentKnowledgeResolver | None = None) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    matches = []
    selected = set((*config.scope_keys, *config.target_keys))
    if resolver is not None:
        selected.update(relation.target_key for relation in resolver.relations
                        if relation.relation_type == "parent" and relation.source_key in selected)
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            if config.teaching_progress_chapter_id:
                if chapter["id"] == config.teaching_progress_chapter_id:
                    return volume, chapter
            elif any(key == chapter["knowledge_id"] or key.startswith(chapter["knowledge_id"] + "_")
                     for key in selected):
                matches.append((volume, chapter))
    return max(matches, key=lambda item: (int(item[0]["order"]), int(item[1]["order"]))) if matches else None


@lru_cache(maxsize=128)
def _allowed_keys_for_config(config: PersonalizedRecommendationConfig, resolver: CurrentKnowledgeResolver | None = None) -> frozenset[str] | None:
    progress = _progress_chapter(config, resolver)
    if progress is None:
        return _allowed_keys_for_volume(config.curriculum_volume_id, resolver)
    volume, chapter = progress
    chapter_keys = [item["knowledge_id"] for item in volume["chapters"] if int(item["order"]) <= int(chapter["order"])]
    keys = frozenset(str(node["id"]) for node in eligible_curriculum_knowledge_nodes(volume["id"])
                     if node["volume_id"] != volume["id"] or any(
                         node["id"] == key or node["id"].startswith(key + "_") for key in chapter_keys))
    volume_keys = _allowed_keys_for_volume(config.curriculum_volume_id)
    return _include_published_skills(keys if volume_keys is None else keys.intersection(volume_keys), resolver)


def resolve_practice_scope(config: PersonalizedRecommendationConfig, diagnosis: Mapping[str, Any],
                           resolver: CurrentKnowledgeResolver) -> PersonalizedRecommendationConfig:
    """An explicit scope is a focused exercise; otherwise cover learned chapters."""
    if config.scope_keys or config.group_scope_keys or config.target_keys or not config.curriculum_volume_id:
        return config
    evidence_keys = {str(p['knowledge_key']) for student in diagnosis.get('students', ())
        for p in student.get('weak_points', ()) if p.get('source_question_refs') or p.get('evidence_count', 0) > 0}
    evidence_keys.update(r.target_key for r in resolver.relations
                         if r.relation_type == 'parent' and r.source_key in evidence_keys)
    volume = curriculum_volume(volume_id=config.curriculum_volume_id)
    # Evidence can contain thousands of skill observations. Reduce to chapters
    # before constructing a request config; its target limit is for UI input.
    observed_chapters = tuple(c['knowledge_id'] for c in volume['chapters'] if any(
        k == c['knowledge_id'] or k.startswith(c['knowledge_id'] + '_') for k in evidence_keys))
    progress = _progress_chapter(replace(config, target_keys=observed_chapters), resolver)
    if progress is None or progress[0]['id'] != config.curriculum_volume_id:
        raise ValueError('请选择已学到的章节，再生成综合训练')
    volume, chapter = progress
    return replace(config, teaching_progress_chapter_id=chapter['id'],
                   scope_keys=tuple(c['knowledge_id'] for c in volume['chapters'] if int(c['order']) <= int(chapter['order'])))


@lru_cache(maxsize=64)
def _scope_descendants(scope_keys: frozenset[str], resolver: CurrentKnowledgeResolver) -> frozenset[str]:
    """Selected scope keys plus every descendant via parent relations."""
    selected = set(scope_keys)
    while True:
        children = {r.source_key for r in resolver.relations
                    if r.relation_type == "parent" and r.target_key in selected}
        expanded = selected | children
        if expanded == selected:
            return frozenset(selected)
        selected = expanded


@lru_cache(maxsize=32)
def _current_volume_keys(volume_id: str, resolver: CurrentKnowledgeResolver | None = None) -> frozenset[str]:
    """Knowledge keys that belong to the selected volume itself."""
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        return frozenset()
    keys = frozenset(str(n["id"]) for n in eligible_curriculum_knowledge_nodes(volume["id"])
                     if n["volume_id"] == volume["id"])
    return _include_published_skills(keys, resolver)


def _question_scope_allowed(candidate: Mapping[str, Any], config: PersonalizedRecommendationConfig,
                            allowed_keys: frozenset[str] | None,
                            resolver: CurrentKnowledgeResolver | None = None) -> bool:
    required = set(candidate.get("required_keys", candidate["stable_keys"]))
    facets = candidate.get("target_facets", ())
    context_keys = {key for part in facets for key in part.get("topic_keys", ())}
    required.update(context_keys)
    required.update(candidate.get("supporting_keys", ()))
    if allowed_keys is not None and (not required or not required.issubset(allowed_keys)
            or (_progress_chapter(config, resolver) is not None and not candidate.get("scope_complete"))):
        return False
    # A selected chapter/section bounds all direct targets, independently of
    # the cumulative "already learned" check above. Auxiliary prerequisites
    # may come from earlier volumes only under this strict scope.
    if config.scope_keys and resolver is not None:
        selected = _scope_descendants(frozenset(config.scope_keys), resolver)
        # Known same-part topics locate the exercise in the teacher's scope.
        # A reusable, already-learned skill can have its catalog home elsewhere.
        direct_scope = context_keys if facets and all(part.get("topic_keys") for part in facets) else set(candidate["stable_keys"])
        if not direct_scope.issubset(selected):
            return False
    return True


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
        key = str(evidence.get("stable_key") or evidence.get("knowledge_key") or "")
        source_parts = ref.get("practice_observations_by_key", {}).get(key, [])
        # A task is a demand on the new response, not an inferred cause of failure.
        # Frozen step observations take priority; unmatched ids never borrow a
        # newer step's meaning. Without them retain only the source part's demand.
        observed = {str(p.get("point_id") or ""): p for p in assessment.get("point_observations", [])}
        points = [point for part in source_parts for point in part.get("evidence_points", [])
                  if str(point.get("evidence_point_id") or "") in observed
                  and ref.get("task_evidence_version_matches", False)]
        failed = [p for p in points if float(observed[str(p["evidence_point_id"])].get("achieved", 1)) < 1]
        reason = "；".join([str(ref.get("deduction_reason") or ""), str(ref.get("error_summary") or ""),
                           *(str(item.get("summary") or "") for item in ref.get("secondary_errors", []) if isinstance(item, Mapping))])
        if any(word in reason for word in ("待复核", "无法判断", "未独立观察", "证据不足")):
            continue
        # 来源引用上的 causes 已经通过整场输入及学生证据校验。
        # 有具体判定点时仍以冻结的失分点为准，错因只补充原小问的练习要求。
        causes = [cause for cause in ref.get("causes", []) if isinstance(cause, Mapping)
                  and cause.get("kind") in {"error", "process", "response_state"}
                  and cause.get("pattern_status") not in {"rejected", "merged"}]
        if causes and not points:
            reason = "；".join(str(cause.get("pattern") or "") for cause in causes)
        # 扣分说明常同时记录做对与缺失的步骤，任务只使用其中的问题片段。
        error_clauses = []
        for clause in re.split(r"[，,；;。\n]|但是|然而|但|却|而", reason):
            if (any(word in clause for word in ("正确", "无误", "完整", "齐全", "已写出"))
                    and not any(word in clause for word in ("不正确", "不完整", "未", "缺", "漏", "错误", "算错", "混淆", "跳步", "不足"))):
                continue
            error_clauses.append(clause)
        problem = "；".join(str(p.get("target") or "") for p in failed) if points else "；".join(error_clauses)
        codes: list[tuple[str, str]] = []
        if any(part.get("response_mode") == "process_required" for part in source_parts):
            codes.append(("process_practice", "写出原小问所需的列式或推导过程"))
        if any(word in problem for word in ("依据", "理由", "证明", "推理", "过程不完整", "跳步", "未明确", "未说明")):
            codes.append(("written_reasoning", "书写依据与推理步骤"))
        if any(word in problem for word in ("混淆", "辨析", "对应量", "单位错误", "数量关系")):
            codes.append(("quantity_discrimination", "辨析量及其对应关系"))
        if any(word in problem for word in ("计算", "运算", "符号错误", "算错", "漏负", "验算", "移项错误", "解方程错误", "求解步骤")):
            codes.append(("calculation_check", "列出计算步骤并核对结果"))
        blank_mentioned = any(word in reason for word in ("未作答", "空白", "未答"))
        observed_error = any(word in problem for word in ("错误", "算错", "混淆", "有误", "漏负", "跳步"))
        wholly_blank = bool(re.match(r"^[；\s]*(?:本题|本问|整题|整问|全题|全问)?[，\s]*(?:完全|全部)?(?:未作答|空白|未答)", reason))
        if not points and blank_mentioned and (wholly_blank or not observed_error):
            # “空白，未见计算/依据”只说明没有作答，不能反推计算或推理错误。
            codes = [code for code in codes if code[0] == "process_practice"]
            codes.append(("diagnostic_check", "确认原小问作答起点（空白原因未知）"))
        elif not codes and "未完成" in reason:
            codes.append(("diagnostic_check", "用短题确认起点（空白原因未知）"))
        for code, label in codes:
            task = tasks.setdefault(code, {"code": code, "label": label, "source_refs": [],
                                          "basis": "observed_step" if failed else "source_part" if code == "process_practice" else "classified_cause" if causes else "grading_note"})
            source = {key: deepcopy(ref.get(key)) for key in ("session_id", "question_id", "bank_question_id", "assessment")}
            if source not in task["source_refs"]:
                task["source_refs"].append(source)
    return [tasks[key] for key in sorted(tasks)]


def _practice_part_fits(part: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]]) -> bool:
    requirements = [task for task in tasks if task["code"] != "diagnostic_check"]
    if not requirements:
        return True
    mode = part.get("response_mode")
    text = str(part.get("part_observable") or part.get("observable") or "")
    if mode not in {"short_answer_points", "process_required"}:
        return False
    for task in requirements:
        code = task["code"]
        if code in {"process_practice", "written_reasoning"} and mode != "process_required":
            return False
        words = {"written_reasoning": ("依据", "理由", "证明", "推理", "说明", "定理", "垂直的定义"),
                 "quantity_discrimination": ("量", "关系", "单位", "边", "长", "面积", "对应", "区别", "辨", "概念"),
                 "calculation_check": ("计算", "运算", "代入", "平方", "根", "式", "等式", "求值", "验算", "²", "=")}.get(code)
        if words and not any(word in text for word in words):
            return False
    return True


def _practice_matches(candidate: Mapping[str, Any], key: str, tasks: Sequence[Mapping[str, Any]],
                      *, part_id: str | None = None) -> bool:
    if not any(task["code"] != "diagnostic_check" for task in tasks):
        return True
    return any(_practice_part_fits(part, tasks)
               for part in candidate.get("practice_observations_by_key", {}).get(key, [])
               if not part_id or part.get("part_id") == part_id)


def _observable_operations(text: str) -> set[str]:
    """Conservative, local recognition of explicit operations in existing criteria.

    Unknown operations have no cross-skill fallback. This does not add tags or
    claim that a student's error has been classified.
    """
    result = set()
    square = "²" in text or bool(re.search(r"\^\s*\{?\s*2\s*\}?|<sup>\s*2\s*</sup>", text, re.I))
    area_relation = "正方形" in text and "面积" in text and bool(re.search(r"面积.{0,45}[＝=].{0,45}[+＋﹣-]", text))
    if "勾股" in text and "逆定理" not in text and (square or "平方" in text or "列式" in text or "等式" in text or area_relation):
        result.add("pythagorean_equation")
    if "逆定理" in text and ("直角" in text or "垂直" in text):
        result.add("pythagorean_converse")
    return result


def _task_matched_part(candidate: Mapping[str, Any], key: str, ref: Mapping[str, Any],
                       tasks: Sequence[Mapping[str, Any]], allowed_parts: set[str]) -> dict[str, Any] | None:
    source = ref.get("practice_observations_by_key", {}).get(key, [])
    operations = set().union(*(_observable_operations(str(p.get("observable") or "")) for p in source)) if source else set()
    if not operations or not any(t["code"] == "process_practice" for t in tasks):
        return None
    for candidate_key, observations in sorted(candidate.get("practice_observations_by_key", {}).items()):
        for part in observations:
            if str(part.get("part_id") or "") not in allowed_parts:
                continue
            observed = str(part.get("observable") or "")
            # Objective rubrics often contain only "answer B". The existing
            # worked solution can establish a component operation for a single
            # part; never borrow the whole solution for a multipart question.
            if len(candidate.get("target_facets", [])) == 1:
                observed += "；" + str(candidate.get("solution_observable") or "")
            if operations <= _observable_operations(observed):
                return {"task_matched_key": candidate_key, "candidate_part_id": part["part_id"],
                        "practice_role": "full_response" if _practice_part_fits(part, tasks) else "step_practice",
                        "task_operations": sorted(operations), "task_match_evidence": observed}
    return None


def _is_core(entry: Mapping[str, Any]) -> bool:
    return entry.get("selection_kind") in {"direct", "task_matched"}


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


def _difficulty_plan(ref: Mapping[str, Any], score_rate: object, cap: int,
                     target: Mapping[str, Any] | None = None,
                     profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One skill history, including successes; an explainable range, not probability.

    Each distinct part response contributes once. Median demonstrated levels
    keep an isolated trap from lowering a repeatedly demonstrated skill.
    """
    def observations(refs):
        unique = {}
        for row in refs:
            assessment = row.get("assessment") or {}
            if assessment.get("eligible") is False or float(assessment.get("evidence_weight", 1)) < .999:
                continue
            if assessment.get("granularity") not in {None, "part", "step"}:
                continue
            full = float(row.get("full_score") or 0)
            difficulty = _difficulty(assessment.get("part_difficulty") or row.get("question_difficulty"))
            if full <= 0 or difficulty is None:
                continue
            identity = (row.get("activity_id") or row.get("session_id"), row.get("bank_question_id") or row.get("question_id"),
                        assessment.get("part_id"), row.get("observation_id"))
            # Repeated tag references are the same response. Distinct scored
            # steps within one part form one observation, not several attempts.
            piece = (assessment.get("granularity") or "part", assessment.get("step_id") or row.get("step_id") or "")
            unique.setdefault(identity, {}).setdefault(piece, set()).add((difficulty, min(full, max(0., float(row.get("score_awarded") or 0))), full))
        result = []
        for pieces in unique.values():
            whole = [values for (kind, _), values in pieces.items() if kind == "part"]
            values = [sorted(rows)[0] for rows in whole or list(pieces.values())]
            result.append((max(v[0] for v in values), sum(v[1] for v in values)/sum(v[2] for v in values)))
        return result
    rows = observations((target or {}).get("source_question_refs", []) or ([ref] if ref else []))
    own_count = len(rows)
    correct_count = sum(rate >= 1.0 for _, rate in rows)
    basis = "同技能多次作答（含正确与失分）" if own_count > 1 else "同技能一次作答，证据较少"
    if not rows:
        key = str((target or {}).get("stable_key") or (target or {}).get("knowledge_key") or "")
        chapter = "_".join(key.split("_")[:6]) if key.startswith(("sk_bnu", "kp_bnu")) else ""
        related = [r for point in (profile or {}).get("weak_points", [])
                   if chapter and str(point.get("knowledge_key") or point.get("stable_key") or "").replace("sk_", "kp_", 1).startswith(chapter.replace("sk_", "kp_", 1)+'_')
                   for r in point.get("source_question_refs", [])]
        rows = observations(related)
        basis = "目标暂无直接证据，相关章节作答辅助" if rows else "目标暂无直接证据，整体表现辅助"
    if rows:
        levels = [d if rate >= .75 else max(1., d-1) if rate >= .4 else max(1., d*.5) for d, rate in rows]
        aim = median(levels)
        readiness = sum(rate for _, rate in rows) / len(rows)
    else:
        rate = _rate(score_rate)
        aim = 2. if rate is None else 2. + 3. * rate
        readiness = rate
        if rate is None:
            basis = "暂无难度作答依据，仅提供范围内基础新练习"
    aim = min(float(cap), 8., max(1., aim))
    maximum = min(float(cap), 8., aim + 1)
    return {"level": "foundation" if aim < 3 else "developing" if aim < 6 else "secure",
            "readiness": readiness, "basis": basis, "evidence_count": own_count, "correct_count": correct_count,
            "confidence": "repeated" if own_count > 1 else "sparse" if own_count else "auxiliary" if rows or _rate(score_rate) is not None else "unknown",
            "minimum": max(1., aim-2), "starter": max(1., aim-1),
            "consolidation": aim, "maximum": maximum, "aim": aim}


def _loss_difficulty(ref: Mapping[str, Any], score_rate: object, cap: int) -> float | None:
    plan = _difficulty_plan(ref, score_rate, cap)
    return float(plan["aim"]) if plan else None


def _loss_difficulty_fits(candidate: Mapping[str, Any], ref: Mapping[str, Any], cap: int,
                          score_rate: object = None) -> bool:
    plan = _difficulty_plan(ref, score_rate, cap)
    level = standard_difficulty.difficulty_level(candidate.get("difficulty"))
    return bool(plan and level is not None
                and plan["minimum"] <= level <= plan["maximum"])


def _direct_fit(candidate: Mapping[str, Any], key: str, ref: Mapping[str, Any]) -> bool:
    # Same-skill short exercises can practise a component without certifying
    # complete written reasoning. The response role remains explicit below.
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
        actual = set(candidate.get("practice_tags", {}).get(kind, [])) or {tag["tag_value"] for tag in profile_tags if tag.get("tag_type") == kind}
        affinity += len(expected & actual) / max(1, len(expected))
    tasks = _training_tasks({"stable_key": key, "source_question_refs": [ref]})
    response_fit = bool(tasks) and _practice_matches(candidate, key, tasks)
    type_fit = bool(ref.get("question_type")) and ref["question_type"] == candidate.get("question_type")
    observed = {str(cause.get("pattern") or "") for cause in ref.get("causes", [])
                if cause.get("pattern_status") not in {"rejected", "merged"}}
    predicted = {str(pattern.get("pattern") or "") for pattern in candidate.get("error_patterns", [])}
    cause_fit = len((observed - {""}) & predicted)
    source_features = [part.get("features", {}) for part in ref.get("difficulty_features", [])]
    candidate_features = [part.get("features", {}) for part in candidate.get("difficulty_features", [])]
    feature_fit = max((sum(left.get(k) == right.get(k) for k in left.keys() & right.keys()) / max(1, len(left.keys() | right.keys()))
                       for left in source_features for right in candidate_features if left and right), default=0.)
    return 4 * overlap + affinity + .5 * type_fit + .5 * response_fit + 2 * cause_fit + feature_fit


def _valid_difficulty_features(question: Mapping[str, Any], rows: Sequence[Any]) -> list[dict[str, Any]]:
    from question_bank.models.tag_schema import PART_FEATURE_ORDER
    fingerprint = standard_difficulty.question_content_fingerprint(question)
    result = []
    for row in rows:
        if row["source_content_hash"] != fingerprint:
            continue
        payload = json.loads(row["features_json"])
        result.append({"part_id": row["part_id"],
                       "features": {name: payload[name] for name in PART_FEATURE_ORDER if name in payload}})
    return result


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
    if (standard_difficulty.difficulty_level(candidate.get("difficulty")) or 10) > 4:
        return ""
    text = str(candidate.get("question_text") or "")
    if "勾股数" in text or ("直角三角形" in text and any(word in text for word in (
            "判断", "判定", "构成", "组成", "摆成", "是直角", "为直角"))):
        return "pythagorean_number_judgement"
    tags = (candidate.get("similarity_profile") or {}).get("tags", [])
    judgements = sorted(tag["tag_value"] for tag in tags if tag.get("tag_type") in {"method", "model"}
                        and any(word in tag["tag_value"] for word in ("判定", "判断", "辨认")))
    return _json([sorted(candidate.get("stable_keys", [])), judgements]) if judgements else ""


@lru_cache(maxsize=8192)
def _practice_literal(text: str) -> str:
    """Keep all numbers/options when comparing reprinted question and answer."""
    value = re.sub(r"\[\[IMAGE:.*?\]\]|!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", "", text, flags=re.I)
    value = re.sub(r"^\s*[（(]\d+(?:\.\d+)?分[）)]", "", value)
    return re.sub(r"\s+", "", value).replace("．", ".").replace("•", "·")


def _paper_skill_limit_exceeded(candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> set[str]:
    """Count whole questions by actual direct skills, independently of recommendation reasons."""
    skills = {key for key in candidate.get("stable_keys", ()) if str(key).startswith("sk_")}
    return {key for key in skills
            if sum(key in other.get("stable_keys", ()) for other in selected) >= MAX_QUESTIONS_PER_SKILL}


def _paper_diversity_allowed(candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> bool:
    if _paper_skill_limit_exceeded(candidate, selected):
        return False
    if _is_written_question(candidate) and sum(_is_written_question(other) for other in selected) >= 2:
        return False
    return paper_similarity_allowed(candidate, selected)


def paper_similarity_allowed(candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> bool:
    """Compare question content only; candidate folding must not apply paper quotas."""
    template = _practice_template(str(candidate.get("question_text") or ""))
    for other in selected:
        practice_identity = candidate.get("practice_identity")
        if practice_identity and practice_identity == other.get("practice_identity"):
            return False
        if candidate.get("duplicate_identity", candidate["question_id"]) == other.get("duplicate_identity", other["question_id"]):
            return False
        if not set(candidate.get("stable_keys", [])).intersection(other.get("stable_keys", [])):
            continue
        # Different image files do not exempt numeric variants. Require almost
        # identical stems AND matching solution structure and method/model tags;
        # drawing-only changes without numeric changes remain distinct.
        if candidate.get("image_identity", ()) != other.get("image_identity", ()):
            literal = _practice_literal(str(candidate.get("question_text") or ""))
            answer = _practice_literal(str(candidate.get("solution_observable") or ""))
            # Identical substantial text, all options and the full worked
            # answer are enough to avoid a reprint in one practice paper even
            # when the extracted picture files have different encodings.
            if (len(literal) >= 80 and len(answer) >= 60
                    and literal == _practice_literal(str(other.get("question_text") or ""))
                    and answer == _practice_literal(str(other.get("solution_observable") or ""))):
                return False
            left_tags = {(tag["tag_type"], tag["tag_value"]) for tag in
                         candidate.get("similarity_profile", {}).get("tags", []) if tag["tag_type"] in {"method", "model"}}
            right_tags = {(tag["tag_type"], tag["tag_value"]) for tag in
                          other.get("similarity_profile", {}).get("tags", []) if tag["tag_type"] in {"method", "model"}}
            left_solution, right_solution = candidate.get("solution_template", ""), other.get("solution_template", "")
            if not (left_tags and left_tags == right_tags and len(left_solution) >= 40 and len(right_solution) >= 40
                    and set(candidate.get("stable_keys", [])) == set(other.get("stable_keys", []))
                    and re.findall(r"\d+(?:\.\d+)?", str(candidate.get("question_text") or ""))
                        != re.findall(r"\d+(?:\.\d+)?", str(other.get("question_text") or ""))
                    and text_similarity(left_solution, right_solution) >= .85
                    and text_similarity(template, _practice_template(str(other.get("question_text") or ""))) >= .98):
                continue
        other_template = _practice_template(str(other.get("question_text") or ""))
        if len(template) >= 12 and len(other_template) >= 12 and text_similarity(template, other_template) >= .9:
            return False
    return True


def _is_written_question(candidate: Mapping[str, Any]) -> bool:
    kind = str(candidate.get("question_type") or "")
    return any(word in kind.casefold() for word in ("解答", "简答", "证明", "计算", "作图", "画图", "主观", "综合", "essay", "proof", "construction", "calculation")) or (
        not kind and any("process_required" in modes for modes in candidate.get("response_modes_by_key", {}).values()))


def _difficulty_band(entry: Mapping[str, Any]) -> str:
    plan = entry["target"].get("difficulty_plan")
    if not plan:
        return "consolidation"
    difficulty = standard_difficulty.difficulty_level(entry["candidate"]["difficulty"])
    if difficulty is None:
        return "consolidation"
    if difficulty <= plan["starter"] and plan["starter"] < plan["consolidation"]:
        return "starter"
    return "consolidation" if difficulty <= plan["consolidation"] else "stretch"


def _need_id(entry: Mapping[str, Any], memo: dict | None = None) -> tuple[str, ...]:
    if memo is None:
        return _loss_need_id(entry)
    stored = memo.get(id(entry))
    if stored is not None and stored[0] is entry:
        return stored[1]
    need_id = _loss_need_id(entry)
    memo[id(entry)] = (entry, need_id)
    return need_id


def repeated_consolidation_only(entries: Sequence[Mapping[str, Any]]) -> bool:
    """Lower priority only when every useful matched need is repeatedly correct."""
    relevant = [e for e in entries if _is_core(e)] or list(entries)
    return bool(relevant) and all(
        e.get("practice_purpose") == "consolidation"
        and (plan := e.get("target", {}).get("difficulty_plan", {})).get("evidence_count", 0) >= 2
        and plan.get("correct_count", 0) == plan["evidence_count"]
        for e in relevant
    )


def _practice_reason_summary(entries: Sequence[Mapping[str, Any]]) -> str:
    """Explain only this student's evaluated matches, deduplicated by target."""
    by_key = {}
    for entry in sorted(entries, key=lambda e: (not _is_core(e), e.get("practice_purpose") != "remediation", e["key"])):
        by_key.setdefault(entry["key"] if _is_core(entry) else entry["matched_key"], entry)
    reasons = []
    for key, entry in by_key.items():
        name = str(entry["candidate"].get("stable_names", {}).get(key)
                   or entry.get("target", {}).get("display_name") or key).split("｜")[-1].removeprefix("技能·")
        plan = entry.get("target", {}).get("difficulty_plan", {})
        if entry.get("selection_kind") == "task_matched":
            # The candidate's difficulty plan can belong to a different skill;
            # counts in this explanation must describe the source need instead.
            plan = _difficulty_plan({}, None, 8, entry.get("target", {}))
        count, correct = plan.get("evidence_count", 0), plan.get("correct_count", 0)
        purpose = entry.get("practice_purpose")
        if purpose == "new":
            detail = "暂无直接作答证据，按适合难度安排，不认定为薄弱"
            label = "新练习"
        else:
            label = "补弱" if purpose == "remediation" else "巩固"
            detail = (f"{count}次有效作答中{correct}次满分、{count-correct}次失分" if count else "已有作答依据，暂无可用的难度作答统计")
            if count == 1:
                detail += "，证据较少"
            if purpose == "consolidation" and count >= 2 and correct == count:
                detail += "，已多次答对，降低巩固优先级"
        if entry.get("selection_kind") == "task_matched":
            detail += "；本题练习对应任务环节，不表示同技能命中"
        reasons.append(f"{label}·{name}：{detail}。")
    return " ".join(reasons)


def _member_entries(
    group: Sequence[dict[str, Any]], memo: dict | None = None,
) -> dict[str, dict[str, Any]]:
    if memo is not None:
        stored = memo.get(("members", id(group)))
        if stored is not None and stored[0] is group:
            return stored[1]
    members: dict[str, dict[str, Any]] = {}
    for entry in sorted(group, key=lambda e: (not _is_core(e), e.get("practice_purpose") != "remediation", e["distance"], e.get("match_level", 2), _need_id(e, memo))):
        members.setdefault(entry["student_id"], entry)
    if memo is not None:
        memo[("members", id(group))] = (group, members)
    return members


def _common_entries(entries: Sequence[dict[str, Any]], members: Sequence[str]) -> list[dict[str, Any]]:
    """Shared difficulty suitability, allowing different purposes per member."""
    groups = {}
    for entry in entries:
        groups.setdefault(entry["candidate"]["question_id"], []).append(entry)
    expected = set(members)
    return [entry for group in groups.values()
            if {e["student_id"] for e in group} >= expected for entry in group]


def _loss_need_id(entry: Mapping[str, Any]) -> tuple[str, ...]:
    # Coverage is of the current skill/task need, not the count of old errors.
    return (str(entry["student_id"]), str(entry["key"]))


def _refresh_supplement_warnings(draft: dict[str, Any]) -> None:
    for student in draft["students"]:
        items = student["items"]
        student["structure"] = {
            "core_count": sum(_is_core(item) for item in items),
            "supplement_count": sum(item.get("selection_kind") == "supplement" for item in items),
            "task_matched_count": sum(item.get("selection_kind") == "task_matched" for item in items),
            "new_practice_count": sum(item.get("practice_purpose") == "new" for item in items),
            "written_count": sum(_is_written_question(item) for item in items), "written_limit": 2,
            "step_practice_count": sum(item.get("practice_role") == "step_practice" for item in items),
            "difficulty_bands": {band: sum(item.get("difficulty_band") == band for item in items)
                                 for band in ("starter", "consolidation", "stretch")}}
    draft["warnings"] = list(dict.fromkeys(w for student in draft["students"] for w in student["warnings"]))


def _order_practice_items(items: list[dict[str, Any]]) -> None:
    """Present selected whole questions from easy to hard, keeping tied order.

    Item ids and slots remain stable for editing; item_order is the displayed
    order also used when the draft becomes a paper snapshot.
    """
    items.sort(key=lambda item: _difficulty(item.get("difficulty")) or 11)
    for order, item in enumerate(items, 1):
        item["item_order"] = order


def _choose_practice_entries(entries: Sequence[dict[str, Any]], question_count: int) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    groups = {}
    for entry in entries:
        groups.setdefault(entry["candidate"].get("duplicate_identity") or entry["candidate"]["question_id"], []).append(entry)
    selected, covered, members, practiced = [], set(), set(), set()
    while groups and len(selected) < question_count:
        printed = [e["candidate"] for e, _ in selected]
        usable = [group for group in groups.values() if _paper_diversity_allowed(group[0]["candidate"], printed)]
        if not usable:
            break
        def rank(group):
            core = [e for e in group if _is_core(e) and e.get("practice_purpose", "remediation") == "remediation"]
            needs = {_loss_need_id(e) for e in core}
            beneficiaries = {e["student_id"] for e in core}
            practice_needs = {(e['student_id'], e.get('matched_key', e['key'])) for e in group}
            return (-len(beneficiaries - members), -len(needs - covered), not bool(core),
                    repeated_consolidation_only(group), -len(practice_needs - practiced), _pattern_count(group[0]["candidate"], printed),
                    min(e.get("match_level", 4) for e in group),
                    median(e["distance"] for e in group), -max(e["preference"] for e in group),
                    group[0]["candidate"]["question_id"])
        group = min(usable, key=rank)
        best = min(group, key=lambda e: (not _is_core(e), e["distance"], -e["preference"], e["student_id"], e["key"]))
        selected.append((best, group))
        covered.update(_loss_need_id(e) for e in group if _is_core(e))
        practiced.update((e['student_id'], e.get('matched_key', e['key'])) for e in group)
        members.update(e["student_id"] for e in group if _is_core(e) and e.get("practice_purpose") == "remediation")
        groups.pop(best["candidate"].get("duplicate_identity") or best["candidate"]["question_id"])
    return selected


def _pattern_count(candidate: Mapping[str, Any], printed: Sequence[Mapping[str, Any]]) -> int:
    """Soft variety preference after covering needs; no new question taxonomy."""
    def tags(q):
        return {(t['tag_type'], t['tag_value']) for t in q.get('similarity_profile', {}).get('tags', ())
                if t['tag_type'] in {'model', 'method'}}
    own = tags(candidate)
    family = _basic_judgement_family(candidate)
    solution = candidate.get('solution_template', '')
    return sum(bool((family and family == _basic_judgement_family(q))
        or (own and own == tags(q))
        or (len(solution) >= 40 and len(q.get('solution_template', '')) >= 40
            and text_similarity(solution, q['solution_template']) >= .85)) for q in printed)


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
        semester_mastery: Callable | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.semester_mastery = semester_mastery
        try:
            self.current_knowledge = (
                CurrentKnowledgeResolver.from_active_database(self.db_path)
            )
        except CurrentKnowledgeUnavailable as exc:
            raise PersonalizedRecommendationError(
                "current knowledge standard is unavailable"
            ) from exc

    def validate_paper_questions(self, question_ids: Sequence[int]) -> None:
        """Apply the same whole-paper limits when a teacher assembles a practice."""
        if not question_ids:
            return
        candidates, _, _ = self._source_snapshot(question_ids=question_ids)
        by_id = {item["question_id"]: item for item in candidates}
        selected = []
        for qid in dict.fromkeys(question_ids):
            candidate = by_id.get(qid)
            if candidate is None:
                raise ValueError(f"第 {qid} 题缺少有效的训练资料，请移出本次学情卷。")
            if candidate["difficulty"] is None or not 1 <= candidate["difficulty"] <= 8:
                raise ValueError("学情卷的题目难度须在 1–8 级内，请调整选题。")
            if _is_written_question(candidate) and sum(_is_written_question(q) for q in selected) >= 2:
                raise ValueError("学情卷最多选 2 道解答题，请先移除一道再添加。")
            exceeded = _paper_skill_limit_exceeded(candidate, selected)
            if exceeded:
                names = [candidate.get("stable_names", {}).get(key) or "该技能" for key in sorted(exceeded)]
                raise ValueError(f"同一技能最多选 1 道题；{'、'.join(dict.fromkeys(names))}已达上限，请先移除相关题目再添加。")
            if not _paper_diversity_allowed(candidate, selected):
                raise ValueError("这道题与已选题目重复或高度相似，请选用其他练习。")
            selected.append(candidate)

    def chapter_groups(
        self, *, diagnosis: Mapping[str, Any], config: PersonalizedRecommendationConfig,
        member_ids: Sequence[str] = (), target_keys: Sequence[str] = (),
        graded_activities: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Read-only preview using the same source and eligibility rules as drafts."""
        students = diagnosis.get("students", [])
        if not students:
            return {"version": GROUPING_VERSION, "groups": [], "unassigned": [], "selection": None,
                    "scope_keys": list(config.group_scope_keys), "warnings": ["当前范围没有学生。"],
                    "summary": {"student_count": 0, "students_with_needs": 0,
                                "grouped_student_count": 0, "group_count": 0}}
        normalized = _normalize_diagnosis(diagnosis)
        if graded_activities is None:
            graded_activities = diagnosis.get("_graded_activities")
        excluded = set()
        relations = tuple({"relation_type": relation.relation_type, "source_key": relation.source_key,
                           "target_key": relation.target_key} for relation in self.current_knowledge.relations)
        leaves = _scope_leaves(config.group_scope_keys, diagnosis=normalized, relations=relations)
        governed = {node.stable_key for node in self.current_knowledge.nodes}
        if not leaves or not set(leaves) <= governed:
            raise ValueError("grouping requires a current chapter or section")
        mastery = self._mastery_snapshot(normalized)
        for profile in normalized["students"]:
            by_key = {p["knowledge_key"]: p for p in profile["weak_points"]}
            for (sid, key), point in mastery.items():
                if sid != profile["student_id"] or key not in leaves:
                    continue
                if key not in by_key:
                    by_key[key] = {"knowledge_key": key, "knowledge_point": point["display_name"],
                                   "mastery": point.get("value"), "evidence_count": point.get("evidence_count", 0)}
                    profile["weak_points"].append(by_key[key])
                by_key[key]["source_question_refs"] = point.get("source_question_refs", [])
        needs = _group_needs(normalized, leaves, cap=config.difficulty_max)
        grouped_members = _chapter_group_members(needs)
        candidates, source_version, recent = (), "", {}
        metadata = {}
        # Read question bodies only after a usable group exists, and only for
        # the selected chapter. A coarse-only diagnosis needs no question pool.
        if grouped_members or member_ids:
            candidates, relations, source_version = self._source_snapshot(
                excluded_question_ids=excluded, knowledge_keys=leaves, candidate_config=config,
            )
            recent = self._recent_question_ids(tuple(str(student["student_id"]) for student in students), diagnosis=normalized, graded_activities=graded_activities)
            metadata = self._source_practice_metadata(normalized)
        groups = [self._chapter_group_summary(
            diagnosis=normalized, members=members, targets=(), needs=needs, config=config,
            candidates=candidates, relations=relations, recent=recent, excluded=excluded, source_version=source_version, metadata=metadata,
            mastery=mastery, graded_activities=graded_activities,
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
                candidates=candidates, relations=relations, recent=recent, excluded=excluded, source_version=source_version, metadata=metadata,
                mastery=mastery, graded_activities=graded_activities,
            )
        return {"version": GROUPING_VERSION, "scope_keys": list(config.group_scope_keys),
                "source_scope_revision": str(diagnosis.get("scope", {}).get("scope_revision") or ""),
                "mastery_parameter_version": CURRENT_MASTERY_PARAMETERS.version,
                "groups": groups, "selection": selection,
                "summary": {"student_count": len(normalized["students"]),
                            "students_with_needs": len(needs),
                            "grouped_student_count": len(covered),
                            "group_count": len(groups)},
                "unassigned": [{"student_id": student["student_id"], "student_name": student["student_name"],
                                "class_id": student["class_id"],
                                "reason_kind": ("no_group_fit" if needs[student["student_id"]] else "no_direct_evidence"),
                                "reason": ("当前没有匹配到技能需要和适合难度相近的群体，可调整分组或使用一人一卷。"
                                           if needs[student["student_id"]] else "暂无足够的直接薄弱证据；无证据、整题或多目标综合失分不按不会处理。")}
                               for student in normalized["students"] if student["student_id"] not in covered],
                "warnings": ["按同技能多次作答形成的适合难度范围分组，整卷覆盖成员需求；无证据不推断薄弱。"]}

    def _chapter_group_summary(
        self, *, diagnosis: Mapping[str, Any], members: Sequence[str], targets: Sequence[str],
        needs: Mapping[str, Mapping[str, Any]], config: PersonalizedRecommendationConfig,
        candidates: Sequence[dict[str, Any]], relations: Sequence[Mapping[str, Any]],
        recent: Mapping[str, set[int]], excluded: set[int], source_version: str,
        metadata: Mapping[int, Mapping[str, Any]],
        mastery: Mapping[tuple[str, str], Mapping[str, Any]],
        graded_activities: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profiles = {student["student_id"]: student for student in diagnosis["students"]}
        union = set().union(*(set(needs[sid]) for sid in members)) if members else set()
        keys = tuple(sorted(set(targets) if targets else union))
        issues = []
        warnings = []
        if len(members) < 2:
            issues.append("群体训练至少需要两名学生；单人请使用按学生训练。")
        allowed_targets = set(_scope_leaves(config.group_scope_keys or config.scope_keys, diagnosis=diagnosis, relations=relations))
        if not keys or not set(keys) <= allowed_targets:
            issues.append("所选目标不在当前教学范围内，请调整目标。")
        rows = []
        pools: dict[str, set[int]] = {}
        removed: set[int] = set()
        shared_recent = set().union(*(recent.get(sid, set()) for sid in members)) if members else set()
        for key in keys:
            available = [needs[sid][key] for sid in members if key in needs[sid]]
            values = [item["mastery"] for item in available if item.get("mastery") is not None]
            rows.append({"knowledge_key": key, "knowledge_point": available[0]["knowledge_point"] if available else key,
                         "min_mastery": min(values, default=0), "max_mastery": max(values, default=0),
                         "median_mastery": median(values) if values else 0,
                         "evidence_count": sum(item["evidence_count"] for item in available),
                         "sparse_member_count": sum(item["evidence_count"] == 1 for item in available),
                         "affected_student_count": len(available), "eligible_member_ids": [],
                         "difficulty_unknown": not values, "target_difficulty": None, "available_question_count": 0})
        scoped_diagnosis = {**diagnosis, "students": [profiles[sid] for sid in members]}
        config = replace(config, paper_mode="shared", target_keys=keys)
        evaluated = self.evaluate_candidates(diagnosis=scoped_diagnosis, config=config, candidates=candidates,
                                            mastery=mastery,
                                            recent={sid: recent.get(sid, set()) for sid in members}, excluded=excluded)
        preview_entries = []
        for sid in members:
            preview_entries.extend(evaluated["pools"][sid])
            warnings.extend(evaluated["warnings"][sid])
        preview_entries = _common_entries(preview_entries, members)
        for row in rows:
            matching = [entry for entry in preview_entries if entry["key"] == row["knowledge_key"]]
            row["available_question_count"] = len({entry["candidate"]["question_id"] for entry in matching})
            row["eligible_member_ids"] = sorted({entry["student_id"] for entry in matching})
            row["target_difficulty"] = median(entry["target"]["target_difficulty"] for entry in matching) if matching else None
            if not matching:
                warnings.append("部分训练目标暂无符合原小问任务的核心题，将如实保留缺口，可补充题库或安排个人训练。")
        preview = _choose_practice_entries(preview_entries, config.question_count)
        count = len(preview)
        if not count:
            issues.append("当前成员需求暂无适用题目，请补充题库或调整目标。")
        if any(row["sparse_member_count"] for row in rows):
            warnings.append("部分成员仅有一次清晰作答，证据较少，请核对名单。")
        if count < config.question_count:
            warnings.append("符合范围、适合难度及整卷限制的题目不足；正式草稿将如实列出缺口。")
        if any(entry["selection_kind"] == "supplement" for entry, _ in preview):
            warnings.append("范围内新练习不计作已证实的薄弱目标覆盖。")
        similarities = [_group_similarity(needs[left], needs[right])
                        for index, left in enumerate(members) for right in members[index + 1:]]
        # Scope-specific diagnoses can be calculated seconds apart. The existing
        # source revision covers raw scores, training records and parameters;
        # natural time decay is not a teacher/source edit. Build the hashed
        # subset without mutating the shared diagnosis: each weak_points entry
        # drops the volatile mastery/effective_weight keys during construction.
        selected = {
            **diagnosis,
            **({"_graded_activities": graded_activities} if graded_activities is not None else {}),
            "students": [
                {
                    **profiles[sid],
                    "weak_points": [
                        {
                            key: value
                            for key, value in point.items()
                            if key not in ("mastery", "effective_weight")
                        }
                        for point in profiles[sid]["weak_points"]
                    ],
                }
                for sid in sorted(members)
            ],
        }
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
        graded_activities: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        actor = _required_text(actor_ref, "actor_ref")
        normalized_diagnosis = _normalize_diagnosis(diagnosis)
        if graded_activities is None:
            graded_activities = diagnosis.get("_graded_activities")
        config = resolve_practice_scope(config, normalized_diagnosis, self.current_knowledge)
        request = {
            "diagnosis": normalized_diagnosis,
            "config": config.to_dict(),
        }
        if graded_activities is not None:
            request["graded_activities"] = graded_activities
        # Old stored requests embedded the activities inside the diagnosis; the
        # fingerprint keeps hashing that payload so a retry maps to its draft.
        input_fingerprint = _hash_payload(
            {
                "diagnosis": (
                    {**normalized_diagnosis, "_graded_activities": graded_activities}
                    if graded_activities is not None
                    else normalized_diagnosis
                ),
                "config": request["config"],
            }
        )
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
                                          target_keys=config.target_keys, graded_activities=graded_activities)["selection"]
            if not checked or not checked["ready"]:
                raise ValueError("selected group no longer has compatible common targets")
            if not config.group_source_version or checked["source_version"] != config.group_source_version:
                raise RecommendationSourceChanged("group evidence or source has changed; refresh the group")

        excluded = set()
        snapshot_generation = commit_generation(self.db_path)
        candidate_scope = self._candidate_scope(normalized_diagnosis, config)
        snapshot = self._source_snapshot(
            excluded_question_ids=excluded,
            prepare_refinements=True,
            knowledge_keys=candidate_scope,
            candidate_config=config,
        )
        candidates, relations, base_source_version = snapshot
        base_source_version = _hash_payload({"bank": base_source_version, "loss_sources": self._source_practice_metadata(normalized_diagnosis)})
        mastery = self._mastery_snapshot(normalized_diagnosis)
        recent = self._recent_question_ids(
            tuple(
                str(item["student_id"])
                for item in normalized_diagnosis["students"]
            ), diagnosis=normalized_diagnosis, graded_activities=graded_activities
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
        # g0 is the generation the snapshot key was computed under; any
        # foreign commit before or during the draft write invalidates it.
        g0 = commit_generation(self.db_path)
        with connect(self.db_path) as write_connection:
            data_version_before = write_connection.execute(
                "PRAGMA data_version"
            ).fetchone()[0]
            write_connection.execute("BEGIN IMMEDIATE")
            row = write_connection.execute(
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
            write_connection.execute(
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
            write_connection.execute(
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
            write_connection.commit()
            data_version_after = write_connection.execute(
                "PRAGMA data_version"
            ).fetchone()[0]
        g1 = commit_generation(self.db_path)
        # Re-store the snapshot under the new generation only when this call's
        # own commit is the only change: the snapshot key was built at g0,
        # no other writer touched the file during the transaction (our own
        # commit does not change this connection's data_version), and the
        # generation advanced by exactly one.
        if (
            snapshot_generation == g0
            and data_version_before == data_version_after
            and g1 == g0 + 1
        ):
            _SOURCE_SNAPSHOT_CACHE.put(
                self._source_snapshot_cache_key(
                    prepare_refinements=True,
                    excluded_question_ids=excluded,
                    knowledge_keys=candidate_scope,
                    candidate_config=config,
                ),
                snapshot,
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

    def get_by_request_token(self, request_token: str) -> dict[str, Any]:
        result = self._by_request_token(_request_token(request_token))
        if result is None:
            raise RecommendationDraftNotFound(request_token)
        result.pop("_input_fingerprint", None)
        return result

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
        excluded = set()
        candidate_config = PersonalizedRecommendationConfig(**_config_constructor(config))
        candidates, _relations, base_source_version = self._source_snapshot(
            excluded_question_ids=excluded,
            knowledge_keys=self._candidate_scope(diagnosis, candidate_config),
            candidate_config=candidate_config,
        )
        base_source_version = _hash_payload({"bank": base_source_version, "loss_sources": self._source_practice_metadata(diagnosis)})
        recent = self._recent_question_ids(
            student_ids,
            exclude_draft_id=draft_id, diagnosis=diagnosis,
            graded_activities=request.get("graded_activities"),
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
                      for point in student.get("weak_points", []) for ref in point.get("source_question_refs", [])
                      if int(ref.get("bank_question_id") or 0) > 0})
        result: dict[int, dict[str, Any]] = {}
        facets_index = target_index(self.current_knowledge)
        from question_bank.solution_evidence.part_assessments import load_profiles
        for start in range(0, len(ids), 64):
            batch = ids[start:start + 64]
            marks = ",".join("?" for _ in batch)
            with connect(self.db_path) as conn:
                rows = conn.execute(f"SELECT * FROM questions WHERE id IN ({marks}) AND is_deleted=0", batch).fetchall()
                tags = conn.execute(f"SELECT question_id, tag_type, tag_value FROM question_tags WHERE question_id IN ({marks})", batch).fetchall()
                features = conn.execute(f"SELECT * FROM question_part_difficulty_features WHERE is_active=1 AND question_id IN ({marks})", batch).fetchall()
            profiles = load_profiles(self.db_path, batch, data_root=self.data_root)
            from question_bank.solution_evidence import SolutionEvidenceRepository
            from question_bank.training_criteria.analysis import (
                solution_evidence_source_content_hash,
            )
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
                source_version_id = str(profile.get("evidence_version_id") or "")
                if not source_parts:
                    latest = evidence_repository.latest(qid)
                    current_input = source_inputs.get(qid)
                    if latest and current_input and latest.get("status") in {"proposed", "approved"} and latest.get("source_content_hash") == solution_evidence_source_content_hash(current_input):
                        source_parts = (latest.get("evidence") or {}).get("parts", [])
                        source_version_id = str(latest.get("evidence_version_id") or source_version_id)
                result[qid] = {"question_difficulty": _difficulty(row["difficulty"]), "direct_keys": keys,
                               "topic_keys": topic_keys(values.get("knowledge_point", []), self.current_knowledge, facets_index),
                               "question_type": str(row["question_type"] or ""),
                               "practice_tags": values,
                               "difficulty_features": _valid_difficulty_features(dict(row), [f for f in features if f["question_id"] == qid]),
                               "parts": source_parts,
                               "evidence_version_id": source_version_id}
        return result

    def _links_for_metadata(
        self, metadata: Mapping[int, Mapping[str, Any]],
    ) -> dict[str, Any]:
        from question_bank.solution_evidence.knowledge_links import load_point_links
        version_ids = [
            str(item.get("evidence_version_id") or "")
            for item in metadata.values()
            if item.get("evidence_version_id")
        ]
        db_path = getattr(self, "db_path", None)
        if not version_ids or db_path is None:
            return {}
        return load_point_links(db_path, version_ids, self.current_knowledge.release_id)

    def _enrich_source_ref(
        self,
        ref: Mapping[str, Any],
        metadata: Mapping[int, Mapping[str, Any]],
        links_by_version: Mapping[str, Mapping[str, Sequence[Any]]] | None = None,
    ) -> dict[str, Any]:
        source = metadata.get(int(ref.get("bank_question_id") or 0), {})
        enriched = {**deepcopy(ref), **{key: deepcopy(source[key]) for key in
                    ("question_difficulty", "direct_keys", "practice_tags", "question_type", "difficulty_features") if source.get(key)}}
        assessment = ref.get("assessment") or {}
        part_id = assessment.get("evidence_part_id") or assessment.get("part_id")
        parts = [part for part in source.get("parts", []) if not part_id or part.get("part_id") == part_id]
        if parts:
            version_links = (
                (links_by_version or {}).get(str(source.get("evidence_version_id") or ""))
                if source.get("evidence_version_id")
                else None
            )
            part_metadata = _question_evidence_metadata(
                {"parts": parts}, self.current_knowledge,
                links=version_links)
            enriched["direct_keys"] = part_metadata["stable_keys"]
            enriched["practice_observations_by_key"] = part_metadata["practice_observations_by_key"]
            enriched["task_evidence_version_matches"] = bool(assessment.get("evidence_version_id")) and (
                str(assessment["evidence_version_id"]) == str(source.get("evidence_version_id") or ""))
            enriched["direct_fine_terms"] = sorted({term for observations in part_metadata["practice_observations_by_key"].values()
                                                     for part in observations for term in part.get("fine_terms", [])})
            facets = part_facets({"parts": source.get("parts", [])}, version_links, self.current_knowledge,
                                 target_index(self.current_knowledge), source.get("topic_keys", []))
            enriched["target_facets"] = [item for item in facets if not part_id or item["part_id"] == part_id]
        return enriched

    def _candidate_entries(
        self, *, profile: Mapping[str, Any], targets: Sequence[Mapping[str, Any]],
        candidates: Sequence[dict[str, Any]], metadata: Mapping[int, Mapping[str, Any]],
        config: PersonalizedRecommendationConfig, supplement_keys: Sequence[str],
        recent: set[int], excluded: set[int],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Shared per-student evaluation for draft, group, teacher shortlist and edit."""
        eligible = self._eligible_candidates(
            candidates, stage="direct", target_keys=(), maintenance=True, used=set(),
            recent=recent, excluded=excluded, config=config,
            allowed_keys=_allowed_keys_for_config(config, self.current_knowledge),
            paper_level_max=_paper_level_limit_for_volume(config.curriculum_volume_id))
        entries, warnings = [], []
        scope = set(supplement_keys)
        index = target_index(self.current_knowledge)
        links = self._links_for_metadata(metadata)
        auxiliary_plans = {}
        matched_ids, suitable_ids = set(), set()
        for target in targets:
            key = str(target.get("stable_key") or target.get("knowledge_key"))
            refs = [self._enrich_source_ref(r, metadata, links) for r in target.get("source_question_refs", [])]
            enriched = {**target, "stable_key": key, "source_question_refs": refs}
            losses = _loss_refs(enriched)
            ref = (losses or refs or [{}])[0]
            plan = _difficulty_plan({}, profile.get("score_rate"), config.difficulty_max, enriched, profile)
            tasks = _training_tasks(enriched)
            sources = [part for r in (losses or refs) for part in r.get("target_facets", [])]
            # A new exercise uses the selected target, never a fictional wrong answer.
            if not sources and key in index and not any(r.get("assessment", {}).get("evidence_version_id") for r in refs):
                anchor = index[key]
                sources = [{"direct_keys": [key], "skill_keys": [key] if key.startswith("sk_") else [],
                            "topic_keys": [key] if anchor["kind"] == "topic" else [],
                            "section_keys": [anchor["section"]] if anchor["section"] else [],
                            "chapter_keys": [anchor["chapter"]] if anchor["chapter"] else []}]
            valid_refs = [r for r in refs if (r.get("assessment") or {}).get("eligible") is not False
                          and float((r.get("assessment") or {}).get("evidence_weight", 1)) >= .999 and float(r.get("full_score") or 0) > 0]
            purpose = "remediation" if losses else "consolidation" if valid_refs else "new"
            selected_target = {**enriched, "target_difficulty": plan["aim"], "difficulty_plan": plan,
                               "overall_score_rate": _rate(profile.get("score_rate")), "training_tasks": tasks}
            for candidate in eligible:
                if sources and candidate.get("target_facets"):
                    matches = [match_target(key, sources, [part], index) for part in candidate["target_facets"]]
                    matches = [m for m in matches if m]
                    if not matches:
                        continue
                    direct_matches = [m for m in matches if (m["match_level"] <= 2 or not key.startswith("sk_"))
                                      and key in candidate["stable_keys"]]
                    direct = bool(direct_matches)
                    match = min(direct_matches or matches, key=lambda m: (m["match_level"], m["candidate_part_id"]))
                else:
                    direct = key in candidate["stable_keys"] and not any(r.get("assessment", {}).get("evidence_version_id") for r in refs)
                    if not direct and not scope.intersection(candidate["stable_keys"]):
                        continue
                    match = {"match_level": 3 if direct else 4,
                             "match_label": "同知识目标练习" if direct else MATCH_LABELS[4]}
                task_match = None
                if not direct and losses and sources:
                    allowed_parts = {str(part["part_id"]) for part in candidate.get("target_facets", [])
                                     if any(set(source.get("chapter_keys", [])) & set(part.get("chapter_keys", []))
                                            and (not source.get("topic_keys") or set(source["topic_keys"]) <= set(part.get("topic_keys", [])))
                                            for source in sources if key in source.get("direct_keys", []))}
                    task_match = _task_matched_part(candidate, key, ref, tasks, allowed_parts)
                    if task_match:
                        match = {**match, **task_match, "match_label": "原小问任务匹配（已有解题步骤）"}
                if direct:
                    parts = [part for part in candidate.get("practice_observations_by_key", {}).get(key, [])
                             if not match.get("candidate_part_id") or part.get("part_id") == match["candidate_part_id"]]
                    match["practice_role"] = "full_response" if not tasks or any(_practice_part_fits(part, tasks) for part in parts) else "step_practice"
                matched_key = key if direct else task_match["task_matched_key"] if task_match else _matched_key(candidate["stable_keys"], tuple(sorted(scope)))
                matched_ids.add(candidate["question_id"])
                candidate_plan = plan
                if not direct and matched_key != key:
                    if matched_key not in auxiliary_plans:
                        point = next((p for p in profile.get("weak_points", []) if p.get("knowledge_key") == matched_key), {})
                        auxiliary_plans[matched_key] = _difficulty_plan({}, profile.get("score_rate"), config.difficulty_max,
                            {**point, "stable_key": matched_key, "source_question_refs": [self._enrich_source_ref(r, metadata, links)
                             for r in point.get("source_question_refs", [])]}, profile)
                    candidate_plan = auxiliary_plans[matched_key]
                if not candidate_plan["minimum"] <= candidate["difficulty"] <= candidate_plan["maximum"]:
                    continue
                suitable_ids.add(candidate["question_id"])
                entries.append({"candidate": candidate, "key": key, **match,
                    "target": {**selected_target, "target_difficulty": candidate_plan["aim"], "difficulty_plan": candidate_plan},
                    "matched_key": matched_key, "selection_kind": "direct" if direct else "task_matched" if task_match else "supplement",
                    "practice_purpose": purpose if direct or task_match else "new",
                    "difficulty_basis": candidate_plan["basis"], "evidence_confidence": candidate_plan["confidence"],
                    "student_id": str(profile["student_id"]), "distance": abs(candidate["difficulty"] - candidate_plan["aim"]),
                    "preference": max((_direct_preference(candidate, key, r) for r in losses or refs), default=0.),
                    "loss": max((1-float(r.get("score_awarded") or 0)/float(r["full_score"]) for r in losses), default=0.)})
        if targets and not entries:
            warnings.append("当前目标没有同时符合范围、适合难度、近期原题和有效资料要求的题目。")
        if targets and len(suitable_ids) < config.question_count:
            recent_count = sum(c["question_id"] in recent | excluded for c in candidates)
            warnings.append(f"当前已具备有效训练资料的候选中：近期原题排除 {recent_count} 道；范围和难度上限检查后 {len(eligible)} 道；目标匹配后 {len(matched_ids)} 道；学生适合难度检查后 {len(suitable_ids)} 道。卷内同技能最多1道、相似题和解答题限制另行检查。")
        return entries, warnings

    def _candidate_scope(self, diagnosis: Mapping[str, Any], config: PersonalizedRecommendationConfig) -> tuple[str, ...]:
        relations = tuple({"relation_type": r.relation_type, "source_key": r.source_key, "target_key": r.target_key}
                          for r in self.current_knowledge.relations)
        return _scope_leaves(config.scope_keys or config.group_scope_keys or config.target_keys,
                             diagnosis=diagnosis, relations=relations)

    def evaluate_candidates(self, *, diagnosis: Mapping[str, Any], config: PersonalizedRecommendationConfig,
                            candidates: Sequence[dict[str, Any]] | None = None,
                            mastery: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
                            recent: Mapping[str, set[int]] | None = None,
                            excluded: set[int] | None = None,
                            graded_activities: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """One public read-only matching operation for all three selection flows."""
        config = resolve_practice_scope(config, diagnosis, self.current_knowledge)
        scope = self._candidate_scope(diagnosis, config)
        if candidates is None:
            candidates, _, _ = self._source_snapshot(knowledge_keys=scope, candidate_config=config)
        if mastery is None:
            mastery = self._mastery_snapshot(dict(diagnosis))
        if recent is None:
            recent = self._recent_question_ids(tuple(str(p["student_id"]) for p in diagnosis["students"]), diagnosis=diagnosis, graded_activities=graded_activities)
        excluded = set(excluded or ())
        if config.paper_mode == "shared":
            excluded.update(q for ids in recent.values() for q in ids)
        explicit = set(config.target_keys or scope)
        metadata = self._source_practice_metadata(diagnosis)
        pools, targets_by_student, warnings = {}, {}, {}
        for profile in diagnosis["students"]:
            sid = str(profile["student_id"])
            known = {key: deepcopy(value) for (owner, key), value in mastery.items() if owner == sid and (not explicit or key in explicit)}
            for key in sorted(explicit):
                node = self.current_knowledge.node(key)
                known.setdefault(key, {"stable_key": key, "display_name": node.display_name if node else key,
                                       "source_question_refs": [], "value": None, "evidence_count": 0})
            targets = [known[key] for key in sorted(known)]
            targets_by_student[sid] = targets
            pools[sid], warnings[sid] = self._candidate_entries(profile=profile, targets=targets, candidates=candidates,
                metadata=metadata, config=config, supplement_keys=scope or tuple(known),
                recent=recent.get(sid, set()), excluded=excluded)
        return {"pools": pools, "targets": targets_by_student, "warnings": warnings, "candidates": candidates}

    def _build_draft(
        self, *, diagnosis: dict[str, Any], config: PersonalizedRecommendationConfig,
        candidates: tuple[dict[str, Any], ...], relations: tuple[dict[str, Any], ...],
        mastery: dict[tuple[str, str], dict[str, Any]], recent: dict[str, set[int]],
        excluded_question_ids: set[int],
    ) -> dict[str, Any]:
        profiles = diagnosis["students"]
        evaluated = self.evaluate_candidates(diagnosis=diagnosis, config=config, candidates=candidates,
                                            mastery=mastery, recent=recent, excluded=excluded_question_ids)
        targets_by_student, pools, warnings_by_student = evaluated["targets"], evaluated["pools"], evaluated["warnings"]

        shared = _choose_practice_entries(_common_entries([entry for entries in pools.values() for entry in entries],
                                          [str(profile["student_id"]) for profile in profiles]), config.question_count) if config.paper_mode == "shared" else None
        students = []
        for profile in profiles:
            sid = str(profile["student_id"])
            selected = shared if shared is not None else _choose_practice_entries(pools[sid], config.question_count)
            items = []
            for order, (entry, group) in enumerate(selected, 1):
                beneficiaries = sorted({member["student_id"] for member in group})
                own = [member for member in group if member["student_id"] == sid]
                if own:
                    entry = _member_entries(own)[sid]
                item = _draft_item(entry["candidate"], stage="direct", slot=order, student_id=sid,
                                   target=entry["target"], matched_key=entry["matched_key"], maintenance=False,
                                   selection_kind=entry["selection_kind"], match_details=entry, practice_entries=own)
                item.update(item_order=order, beneficiary_student_ids=beneficiaries)
                if shared is not None:
                    names = "、".join(str(member.get("student_name") or member["student_id"]) for member in profiles if str(member["student_id"]) in beneficiaries)
                    item["reason"] = (f"公共卷：本题主要对应 {names} 的训练需求。" if _is_core(entry)
                                      else f"公共卷：本题难度适合 {names}，用于范围内补充练习。") + item["reason"]
                items.append(item)
            _order_practice_items(items)
            missing = config.question_count - len(items)
            warnings = warnings_by_student[sid]
            if missing:
                warnings.append(f"符合范围、适合难度、近期原题排除与整卷限制的题目不足；同技能最多1道、解答题最多2道，相似题受限，保留 {missing} 道缺口。")
            covered = {_loss_need_id(member) for _, group in selected for member in group
                       if _is_core(member) and member["student_id"] == sid}
            missing_targets = [target for target in targets_by_student[sid]
                               if _loss_refs(target) and not any(need[1] == target["stable_key"] for need in covered)]
            if missing_targets:
                names = "、".join(str(target.get("display_name") or target["stable_key"]) for target in missing_targets)
                warnings.append(f"以下失分知识点尚未获得直接练习或任务匹配练习：{names}。补充练习不计作这些目标的覆盖。")
            students.append({"student_id": sid, "student_code": str(profile.get("student_code") or ""),
                             "student_name": str(profile.get("student_name") or ""), "class_id": str(profile.get("class_id") or ""),
                             "selection_mode": "mastery_targeted", "targets": targets_by_student[sid], "items": items,
                             "shortages": ([{"stage": "direct", "requested_count": config.question_count,
                                             "selected_count": len(items), "missing_count": missing,
                                             "reason_code": "approved_candidate_shortage"}] if missing else []),
                             "warnings": list(dict.fromkeys(warnings))})
        draft = {"config": config.to_dict(), "students": students,
                 "group_basis": {"method": "whole_paper_member_coverage"} if shared is not None else None}
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
                <= float(difficulty)
                <= config.difficulty_max
            ):
                continue
            if not _question_scope_allowed(candidate, config, allowed_keys, self.current_knowledge):
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

    def _source_snapshot_cache_key(
        self,
        *,
        prepare_refinements: bool,
        excluded_question_ids: set[int] | None,
        knowledge_keys: Sequence[str],
        candidate_config: PersonalizedRecommendationConfig | None,
        question_ids: Sequence[int] = (),
    ) -> tuple:
        return (
            "source-snapshot-v1",
            str(Path(self.db_path).resolve(strict=False)),
            str(Path(self.data_root).resolve(strict=False)),
            str(self.current_knowledge.release_id),
            f"commits:{commit_generation(self.db_path)}",
            bool(prepare_refinements),
            tuple(sorted(int(qid) for qid in (excluded_question_ids or ()))),
            tuple(sorted(str(item) for item in knowledge_keys)),
            tuple(sorted(set(question_ids))),
            json.dumps(
                candidate_config.to_dict(), sort_keys=True, default=str
            )
            if candidate_config is not None
            else "",
        )

    def _source_snapshot(
        self,
        *,
        excluded_question_ids: set[int] | None = None,
        prepare_refinements: bool = False,
        knowledge_keys: Sequence[str] = (),
        candidate_config: PersonalizedRecommendationConfig | None = None,
        question_ids: Sequence[int] = (),
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        str,
    ]:
        key = self._source_snapshot_cache_key(
            prepare_refinements=prepare_refinements,
            excluded_question_ids=excluded_question_ids,
            knowledge_keys=knowledge_keys,
            candidate_config=candidate_config,
            question_ids=question_ids,
        )
        return _SOURCE_SNAPSHOT_CACHE.get_or_compute(
            key,
            lambda: self._source_snapshot_uncached(
                excluded_question_ids=excluded_question_ids,
                prepare_refinements=prepare_refinements,
                knowledge_keys=knowledge_keys,
                candidate_config=candidate_config,
                question_ids=question_ids,
            ),
        )

    def _source_snapshot_uncached(
        self,
        *,
        excluded_question_ids: set[int] | None = None,
        prepare_refinements: bool = False,
        knowledge_keys: Sequence[str] = (),
        candidate_config: PersonalizedRecommendationConfig | None = None,
        question_ids: Sequence[int] = (),
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
                ORDER BY q.id
                """
            ).fetchall()
            if question_ids:
                selected_ids = set(question_ids)
                rows = [row for row in rows if int(row["id"]) in selected_ids]
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
                WHERE TRIM(COALESCE(qt.tag_value, '')) <> ''
                ORDER BY qt.question_id, qt.id
                """
            ).fetchall()
            feature_by_question = {}
            for feature in connection.execute("SELECT * FROM question_part_difficulty_features WHERE is_active=1"):
                feature_by_question.setdefault(int(feature["question_id"]), []).append(feature)
            # Cheap identity-only preselection includes every stored criterion
            # version. Live source and approved/current precedence are still
            # checked below; a stale version can widen a read, never admit a题.
            evidence_rows = connection.execute(
                "SELECT question_id, criteria_json FROM training_criterion_versions "
                "WHERE status IN ('approved', 'proposed')"
            ).fetchall() if pool_keys else []
            # Stored evidence IDs may include the graph release, while criteria
            # embed the semantic version ID. Resolve that identity independently
            # of optional difficulty profiles, without borrowing another version.
            evidence_version_rows = connection.execute(
                """
                SELECT e.question_id, e.evidence_version_id,
                       json_extract(e.evidence_json, '$.version_id') AS semantic_version_id
                FROM question_solution_evidence_versions e
                LEFT JOIN question_scope_summary s ON s.question_id=e.question_id
                WHERE e.status IN ('approved', 'proposed')
                ORDER BY COALESCE(e.graph_release_id = ?, 0) DESC,
                         COALESCE(e.evidence_version_id = s.evidence_version_id, 0) DESC,
                         e.created_at DESC, e.evidence_version_id DESC
                """, (self.current_knowledge.release_id,),
            ).fetchall()
        stable_by_question: dict[int, list[dict[str, str]]] = {}
        source_ids = {int(row["id"]) for row in rows}
        from question_bank.solution_evidence.knowledge_links import load_point_links
        from question_bank.solution_evidence.part_assessments import (
            direct_targets,
            load_profiles,
        )
        profiles = {}
        profile_ids = sorted(source_ids)
        for start in range(0, len(profile_ids), 64):
            profiles.update(load_profiles(self.db_path, profile_ids[start:start + 64],
                                          verify_source=not pool_keys, data_root=self.data_root))
        evidence_versions: dict[tuple[int, str], str] = {}
        for row in evidence_version_rows:
            if int(row["question_id"]) not in source_ids:
                continue
            for version_id in (row["evidence_version_id"], row["semantic_version_id"]):
                if version_id:
                    evidence_versions.setdefault((int(row["question_id"]), str(version_id)),
                                                 str(row["evidence_version_id"]))
        point_links = load_point_links(
            self.db_path,
            list(dict.fromkeys([*evidence_versions.values(),
                *(str(profile["evidence_version_id"]) for profile in profiles.values()
                  if profile.get("evidence_version_id"))])),
            self.current_knowledge.release_id,
        )

        def links_for_evidence(question_id: int, evidence: Mapping[str, Any]) -> Mapping[str, Sequence[Any]] | None:
            version_id = evidence_versions.get((question_id, str(evidence.get("version_id") or "")))
            profile = profiles.get(question_id)
            if not version_id and profile and profile.get("available"):
                if evidence.get("parts") == profile["evidence"].get("parts"):
                    version_id = str(profile["evidence_version_id"])
            # Only evidence without a stored counterpart uses embedded legacy
            # links. An explicitly empty table mapping must stay empty.
            return point_links.get(version_id, {}) if version_id else None
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
                    keys = _question_evidence_metadata(evidence, self.current_knowledge,
                        links=links_for_evidence(int(stored["question_id"]), evidence))["stable_keys"]
                    evidence_keys.setdefault(int(stored["question_id"]), set()).update(keys)
                except (ValueError, TypeError, AttributeError):
                    continue
            matching_ids = {
                question_id for question_id in source_ids - excluded_ids
                if pool_keys.intersection(
                    {item["stable_key"] for item in stable_by_question.get(question_id, [])}
                    | {key for part in profiles.get(question_id, {}).get("evidence", {}).get("parts", [])
                       for key in direct_targets(part, point_links.get(str(profiles[question_id].get("evidence_version_id") or ""), {}))}
                    | evidence_keys.get(question_id, set())
                )
            }
            source_ids = matching_ids
            rows = [row for row in rows if int(row["id"]) in matching_ids]
            profiles = {qid: profile for qid, profile in profiles.items() if qid in matching_ids}
        source_tag_keys = {question_id: sorted(item["stable_key"] for item in identities)
                           for question_id, identities in stable_by_question.items()}
        facets_index = target_index(self.current_knowledge)
        for question_id, profile in profiles.items():
            # Refined questions only target the knowledge directly demonstrated
            # by a small part. Prerequisites are not independent test targets.
            identities = []
            if profile["available"]:
                version_links = point_links.get(str(profile.get("evidence_version_id") or ""), {})
                for part in profile["evidence"]["parts"]:
                    for key in direct_targets(part, version_links):
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
        from question_bank.services.error_pattern_service import (
            list_patterns,
            preferred_active_patterns,
        )
        with connect(self.db_path) as connection:
            patterns = list_patterns(connection, candidate_ids, include_predicted=True, statuses=("candidate", "confirmed"))
        candidates: list[dict[str, Any]] = []
        image_cache: dict[str, str] = {}
        allowed = _allowed_keys_for_config(candidate_config, self.current_knowledge) if candidate_config else None
        # Drafts keep the full usable snapshot and their existing source versions.
        # Group previews can skip identity comparisons for ineligible questions.
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
            if pool_keys:
                # Reuse this batch's image bodies for the live source check.
                # Stored identities above only narrowed the read, not eligibility.
                profiles.update(load_profiles(self.db_path, [q.question_id for q in questions],
                    data_root=self.data_root, question_inputs={q.question_id: q for q in questions}))
                point_links.update(load_point_links(
                    self.db_path,
                    [str(p["evidence_version_id"]) for p in profiles.values()
                     if p.get("evidence_version_id") and str(p["evidence_version_id"]) not in point_links],
                    self.current_knowledge.release_id,
                ))
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
                # Answer diagrams cannot stand in for a figure referenced by
                # the printed question. Keep incomplete sources out of practice.
                if (re.search(r"如图|图中|图示", str(row["question_text"] or ""))
                        and not any(image.role == "question" for image in question.images)):
                    continue
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
                         "direct_keys": list(direct_targets(
                             evidence_parts[part["part_id"]],
                             point_links.get(str(profile.get("evidence_version_id") or ""), {})))}
                        for part in profile["parts"]
                    ]
                    # A recommendation prints the whole question. Its hardest
                    # part must respect the student's difficulty ceiling.
                    if parts and not any(part["difficulty"] is None for part in parts):
                        difficulty = float(max(part["difficulty"] for part in parts))
                    elif any(part["difficulty"] is not None for part in parts):
                        continue
                    # else: 没有任何有效逐小问特征时回退到整题难度，
                    # parts 里的 None 让前端仍显示"需重评"。
                    assessment = {"profile_revision": profile["revision"],
                                  "evidence_version_id": profile["evidence_version_id"],
                                  "selection_basis": "hardest_part", "parts": parts}
                criterion = usable.get("criteria")
                evidence = criterion.get("solution_evidence") if isinstance(criterion, Mapping) else None
                if not isinstance(evidence, Mapping) and profile and profile["available"]:
                    evidence = profile["evidence"]
                version_links = links_for_evidence(question_id, evidence) if isinstance(evidence, Mapping) else None
                metadata = (_question_evidence_metadata(evidence, self.current_knowledge,
                                                        links=version_links)
                            if isinstance(evidence, Mapping) else {
                                "stable_keys": [item["stable_key"] for item in identities],
                                "required_keys": [item["stable_key"] for item in identities],
                                "scope_complete": False, "response_modes_by_key": {}, "practice_observations_by_key": {}})
                metadata["target_facets"] = part_facets(
                    evidence or {}, version_links, self.current_knowledge, facets_index,
                    [key for key in source_tag_keys.get(question_id, []) if facets_index.get(key, {}).get("kind") == "topic"])
                # Whole-question topics still constrain what must have been
                # taught. Do not assign them to individual parts or count them
                # as direct targets when the refined evidence contains skills.
                metadata["required_keys"] = sorted(set(metadata["required_keys"]) | {
                    key for key in source_tag_keys.get(question_id, [])
                    if facets_index.get(key, {}).get("kind") == "topic"
                })
                identities = [{"stable_key": key, "display_name": self.current_knowledge.node(key).display_name}
                              for key in metadata["stable_keys"] if self.current_knowledge.node(key) is not None]
                context_keys = {key for part in metadata["target_facets"] for key in part["topic_keys"]}
                if not identities or (pool_keys and not pool_keys.intersection(set(metadata["stable_keys"]) | context_keys)):
                    continue
                points = (
                    criterion.get("points")
                    if isinstance(criterion, Mapping)
                    else None
                )
                if candidate_config is not None:
                    if (difficulty is None or not candidate_config.difficulty_min <= difficulty <= candidate_config.difficulty_max
                            or not _question_scope_allowed(metadata, candidate_config, allowed, self.current_knowledge)):
                        continue
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
                        "practice_tags": skill_tags,
                        "difficulty_features": _valid_difficulty_features(dict(row), feature_by_question.get(question_id, [])),
                        "error_patterns": preferred_active_patterns(patterns.get(question_id, [])),
                        "question_text": str(row["question_text"] or ""),
                        "solution_template": _practice_template(re.split(r"【解答】", str(row["answer_text"] or ""))[-1].split("【点评】")[0])
                                             if "【解答】" in str(row["answer_text"] or "") else "",
                        "solution_observable": re.split(r"【分析】|【解答】", str(row["answer_text"] or ""), maxsplit=1)[-1]
                                               if any(mark in str(row["answer_text"] or "") for mark in ("【分析】", "【解答】")) else "",
                        "image_identity": tuple(sorted(image.sha256 for image in question.images)),
                        "source_paper": str(row["paper_title"] or ""),
                        "paper_level_rank": _paper_level_rank(
                            row["paper_grade"], row["paper_semester"]
                        ),
                        "difficulty": difficulty,
                        "part_assessment": assessment,
                        "stable_keys": stable_keys,
                        "target_facets": part_facets(
                            evidence or {}, version_links, self.current_knowledge, facets_index,
                            [key for key in source_tag_keys.get(question_id, [])
                             if facets_index.get(key, {}).get("kind") == "topic"],
                        ),
                        "required_keys": metadata["required_keys"],
                        "supporting_keys": sorted(set(metadata.get("supporting_keys", [])) | {
                            identity.stable_key for value in skill_tags.get("prerequisite", [])
                            for identity in self.current_knowledge.resolve(value)}),
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
        calculator = CurrentMasteryCalculator(
            self.db_path,
            self.current_knowledge,
            clock=self.clock,
            data_root=self.data_root,
            semester_mastery=self.semester_mastery,
        )
        calculated = calculator.calculate(diagnosis)
        snapshot = {
            identity: {
                **item.to_dict(),
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
        observed = calculator.training_observations(exclude_evidence_ids=frozenset(),
            allowed_student_ids=frozenset(str(p["student_id"]) for p in diagnosis["students"]),
            curriculum_volume_id=(str(diagnosis.get("exam_scope", {}).get("curriculum_volume_id") or "")
                if diagnosis.get("exam_scope", {}).get("mode") == "semester" else None))
        for identity, records in observed.items():
            parts = {}
            for observation in records:
                if observation.part_difficulty is None:
                    continue
                part_id = observation.evidence_id.split(":target:")[0]
                part = parts.setdefault(part_id, {"achieved": 0., "total": 0., "record": observation})
                part["achieved"] += observation.achieved_points
                part["total"] += observation.total_points
            if identity not in snapshot:
                continue
            for part_id, part in parts.items():
                observation = part["record"]
                snapshot[identity]["source_question_refs"].append({
                    "source_kind": "training", "activity_id": part_id, "observation_id": part_id,
                    "question_id": part_id, "occurred_at": observation.occurred_at.isoformat(),
                    "score_awarded": part["achieved"], "full_score": part["total"],
                    "assessment": {"eligible": True, "granularity": "part", "part_id": part_id,
                                   "part_difficulty": observation.part_difficulty, "evidence_weight": 1.}})
        return snapshot

    def _current_mastery_version(self) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            feature_rows = [tuple(row) for row in connection.execute(
                "SELECT question_id,part_id,formula_difficulty,source_content_hash FROM question_part_difficulty_features WHERE is_active=1 ORDER BY question_id,part_id"
            )] if connection.execute("SELECT 1 FROM sqlite_master WHERE name='question_part_difficulty_features'").fetchone() else []
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
            "part_assessment_version": _hash_payload(feature_rows),
        }

    def _recent_question_ids(self, student_ids: tuple[str, ...], *,
                             diagnosis: Mapping[str, Any] | None = None,
                             graded_activities: Sequence[Mapping[str, Any]] | None = None,
                             exclude_draft_id: str | None = None) -> dict[str, set[int]]:
        """Latest three *graded* activities per student, exam and training merged."""
        if not student_ids:
            return {}
        diagnosis = diagnosis or {}
        if graded_activities is None:
            graded_activities = diagnosis.get("_graded_activities")
        activities = {}
        def add(sid, identity, when, ids):
            sid = str(sid)
            if sid not in student_ids:
                return
            key = (sid, str(identity))
            try:
                date = datetime.fromisoformat(str(when).replace('Z', '+00:00'))
                if date.tzinfo is None:
                    from datetime import timezone
                    date = date.replace(tzinfo=timezone(timedelta(hours=8)))
                time = date.timestamp()
            except (TypeError, ValueError):
                time = 0.
            previous = activities.setdefault(key, {"time": time, "ids": set()})
            # Review/publication updates do not move an old activity forward.
            previous["time"] = min(previous["time"], time) if previous["time"] and time else previous["time"] or time
            previous["ids"].update(int(q) for q in ids if q)
        marks = ','.join('?' for _ in student_ids)
        with connect(self.db_path) as conn:
            exam_links = {}
            for row in conn.execute("SELECT grading_session_id,bank_question_id FROM grading_question_links WHERE status='confirmed'"):
                exam_links.setdefault(str(row["grading_session_id"]), set()).add(int(row["bank_question_id"]))
            for row in graded_activities or ():
                session = str(row["session_id"])
                add(row["student_id"], 'exam:'+session, row.get("occurred_at"), exam_links.get(session, ()))
            if graded_activities is None:
                # In-memory callers can supply their observed exam references.
                for student in diagnosis.get("students", []):
                    for point in student.get("weak_points", []):
                        for ref in point.get("source_question_refs", []):
                            session = str(ref.get("session_id") or '')
                            if session:
                                add(student["student_id"], 'exam:'+session,
                                    diagnosis.get("_mastery_session_times", {}).get(session, ref.get("occurred_at")),
                                    {*exam_links.get(session, ()), int(ref.get("bank_question_id") or 0)})
            rows = conn.execute(f"""SELECT e.student_id,
                COALESCE((SELECT MIN(s.created_at) FROM training_submissions s
                          WHERE s.paper_instance_id=i.paper_instance_id AND s.student_id=e.student_id
                          AND s.status<>'cancelled'), e.occurred_at) AS occurred_at,
                i.paper_instance_id,e.source_json
                FROM training_evidence_records e JOIN personalized_paper_items i ON i.task_item_code=e.task_item_code
                WHERE e.status='active' AND e.student_id IN ({marks})""", student_ids).fetchall()
            paper_items = {}
            paper_events = {}
            for row in rows:
                paper = row["paper_instance_id"]
                if paper not in paper_items:
                    paper_items[paper] = [r[0] for r in conn.execute(
                        "SELECT bank_question_id FROM personalized_paper_items WHERE paper_instance_id=?", (paper,))]
                source = json.loads(row["source_json"])
                identity = 'exam:'+str(source["grading_session_id"]) if source.get("grading_session_id") else 'training:'+str(paper)
                paper_events[(str(row["student_id"]), str(paper))] = identity
                add(row["student_id"], identity, row["occurred_at"], paper_items[paper])
            # A completed marking counts even before the teacher publishes its
            # mastery evidence. A generated/exported but unmarked paper does not.
            for row in conn.execute(f"""SELECT DISTINCT s.student_id,s.paper_instance_id,s.created_at,i.bank_question_id
                FROM training_submissions s
                JOIN training_assessment_runs r ON r.submission_id=s.submission_id AND r.submission_revision=s.revision
                JOIN personalized_paper_items i ON i.paper_instance_id=s.paper_instance_id
                WHERE s.student_id IN ({marks}) AND s.status<>'cancelled'
                  AND r.status IN ('succeeded','manual_review')
                  AND EXISTS (SELECT 1 FROM training_question_results q WHERE q.run_id=r.run_id
                              AND q.met_count+q.not_met_count>0)""", student_ids):
                identity = paper_events.get((str(row['student_id']), str(row['paper_instance_id'])), 'training:'+str(row['paper_instance_id']))
                add(row['student_id'], identity, row['created_at'], [row['bank_question_id']])
            # Legacy training counts only actual scored attempts, never exports.
            for row in conn.execute(f"""SELECT a.student_id,a.grading_session_id,a.created_at,i.variant_id,i.bank_question_id
                FROM training_attempts a JOIN training_task_items i ON i.task_item_code=a.task_item_code
                WHERE a.student_id IN ({marks}) AND a.score_awarded IS NOT NULL AND a.full_score>0""", student_ids):
                identity = 'exam:'+str(row['grading_session_id']) if row['grading_session_id'] else 'legacy:'+str(row['variant_id'])
                ids = [item[0] for item in conn.execute("SELECT bank_question_id FROM training_task_items WHERE variant_id=?", (row['variant_id'],))]
                add(row['student_id'], identity, row['created_at'], ids)
            result = {}
            for sid in student_ids:
                events = sorted(((key, value) for key, value in activities.items() if key[0] == sid),
                                key=lambda pair: (pair[1]['time'], pair[0][1]), reverse=True)[:RECENT_ACTIVITY_COUNT]
                result[sid] = {qid for _, event in events for qid in event['ids']}
            ids = set().union(*result.values()) if result else set()
            if ids:
                questions = conn.execute("SELECT * FROM questions WHERE is_deleted=0").fetchall()
                texts = {exam_original_text_key(dict(row)) for row in questions if int(row['id']) in ids}
                cache = {}
                identities = {int(row['id']): exam_original_key(dict(row), data_root=self.data_root, image_cache=cache)
                              for row in questions if exam_original_text_key(dict(row)) in texts}
                for sid, used in result.items():
                    keys = {identities[q] for q in used if identities.get(q)}
                    result[sid] = used | {q for q, key in identities.items() if key and key in keys}
        return result

    def current_exam_question_ids(self, diagnosis: Mapping[str, Any], *,
                                  graded_activities: Sequence[Mapping[str, Any]] | None = None) -> set[int]:
        """Compatibility name: shared union of the unified graded-activity window."""
        recent = self._recent_question_ids(tuple(str(p['student_id']) for p in diagnosis.get('students', [])), diagnosis=diagnosis, graded_activities=graded_activities)
        return {q for ids in recent.values() for q in ids}

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
                recent=self._recent_question_ids((str(student["student_id"]),), exclude_draft_id=draft_id, diagnosis=request["diagnosis"],
                                                graded_activities=request.get("graded_activities"))
                           .get(str(student["student_id"]), set()),
                excluded=set())
            by_id = {candidate["question_id"]: candidate for candidate in candidates}
            printed = [by_id[value["question_id"]] for value in student["items"] if value is not item]
            eligible = [entry for entry in entries
                        if entry["candidate"]["question_id"] != item["question_id"]
                        and (item.get("selection_kind") == "supplement" or _is_core(entry))
                        and _paper_diversity_allowed(entry["candidate"], printed)
                        and (command.replacement_question_id is None
                             or entry["candidate"]["question_id"] == command.replacement_question_id)]
            if not eligible:
                raise RecommendationEditInvalid("no approved replacement is available")
            selected = min(eligible, key=lambda entry: (not _is_core(entry), entry.get("match_level", 2), entry["distance"],
                                                       -entry["preference"], entry["candidate"]["question_id"]))
            matched_entries = self.evaluate_candidates(
                diagnosis={**request["diagnosis"], "students": [source_profile]}, config=config,
                candidates=(selected["candidate"],), graded_activities=request.get("graded_activities"))['pools'].get(str(student['student_id']), [])
            if matched_entries:
                selected = _member_entries(matched_entries)[str(student['student_id'])]
            replacement = _draft_item(
                selected["candidate"], stage=item["stage"], slot=int(item["slot"]), student_id=student["student_id"],
                target=selected["target"], matched_key=selected["matched_key"], maintenance=False,
                selection_kind=selected["selection_kind"], match_details=selected,
                practice_entries=matched_entries)
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
        "knowledge_associations": [dict(item) for item in value.get("knowledge_associations", ())
                                   if isinstance(item, Mapping)],
        "exam_scope": (
            {
                "mode": str(exam_scope.get("mode") or ""),
                **({"curriculum_volume_id": str(exam_scope.get("curriculum_volume_id") or "")}
                   if exam_scope.get("mode") == "semester" else {}),
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


def _group_needs(diagnosis: Mapping[str, Any], leaves: Sequence[str], *, cap: float = 8) -> dict[str, dict[str, dict[str, Any]]]:
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
            levels = [standard_difficulty.difficulty_level(d) for d in difficulties]
            known_levels = [level for level in levels if level is not None]
            kind = ("basic" if any(level is not None and level <= 4 for level in levels)
                    else "application" if known_levels and len(known_levels) == len(losses) and min(known_levels) >= 7
                    else "unspecified")
            count = len(direct) + training_count
            needs[key] = {"knowledge_key": key, "knowledge_point": str(point.get("knowledge_point") or key),
                          "mastery": value, "score_rate": _rate(student.get("score_rate")), "evidence_count": count, "performance_kind": kind,
                          "weight": (1.0 - value) * (.75 + .25 * min(3, count) / 3),
                          "source_question_refs": deepcopy(point.get("source_question_refs", [])),
                          "difficulty_plan": _difficulty_plan({}, student.get("score_rate"), cap, point, student),
                          "difficulty_unknown": any(ref["assessment"].get("part_difficulty") is None for ref in losses)}
        result[str(student["student_id"])] = needs
    return result


def _group_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    """Same-skill supported ranges determine homogeneity, not total exam marks."""
    common = set(left) & set(right)
    if not common:
        return 0.
    fits = []
    for key in common:
        plans = [item[key].get("difficulty_plan") or _difficulty_plan(
            {}, item[key].get("score_rate"), 8, item[key]) for item in (left, right)]
        low, high = max(p["minimum"] for p in plans), min(p["maximum"] for p in plans)
        if low > high:
            return 0.
        fits.append(1. - abs(plans[0]["aim"]-plans[1]["aim"])/8.)
    return min(fits)


def _chapter_group_members(needs: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """Build stable groups with compatible skill ranges; verify actual pool later."""
    def level(sid):
        return median((item.get("difficulty_plan") or _difficulty_plan({}, item.get("score_rate"), 8, item))["aim"]
                      for item in needs[sid].values())
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
    assigned = {sid for group in groups for sid in group}
    for sid in sorted((sid for sid in needs if needs[sid] and sid not in assigned), key=lambda sid: (level(sid), sid)):
        candidates = [(-_group_similarity(needs[sid], needs[member]), -len(group), member, index)
                      for index, group in enumerate(groups) if len(group) >= 3
                      for member in group if _group_similarity(needs[sid], needs[member]) >= GROUP_MIN_SIMILARITY]
        if candidates:
            _, _, member, index = min(candidates)
            groups[index] = tuple(value for value in groups[index] if value != member)
            groups.append(tuple(sorted((sid, member))))
    return sorted(groups)


_LEADING_ENUM = re.compile(r"^\s*\d+\s*[.、．)]\s*")
_WHITESPACE = re.compile(r"[\s　]+")


def _text_fingerprint(text: object) -> str:
    """题干规范化指纹：去题号、去空白、小写化，识别跨试卷引用的同一题。"""
    value = _LEADING_ENUM.sub("", str(text or ""), count=1)
    return _WHITESPACE.sub("", value).casefold()


def _similarity_profile(
    *,
    difficulty: float | None,
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
    selection_kind: Literal["direct", "task_matched", "supplement"] = "direct",
    match_details: Mapping[str, Any] | None = None,
    practice_entries: Sequence[Mapping[str, Any]] = (),
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
    reason = (f"补充练习：选定范围内的 {matched_name}，按本次能力范围安排，不作为此知识点薄弱的证据。"
              if selection_kind == "supplement" else
              f"对应错题 {question_number} 的原小问任务；本题已有解题步骤包含所需操作，技能标签为 {matched_name}，不声称同技能命中。"
              if selection_kind == "task_matched" else f"对应错题 {question_number} 的知识点，练习 {matched_name}。")
    purpose = (match_details or {}).get("practice_purpose", "remediation")
    if purpose == "new":
        reason = f"新练习：练习 {matched_name}，该目标暂无直接作答证据，不认定为薄弱。"
    elif purpose == "consolidation":
        reason = f"巩固练习：结合已有作答，练习 {matched_name}。"
    if practice_entries:
        reason = _practice_reason_summary(practice_entries)
    if match_details and match_details.get("match_level"):
        reason = (f"{match_details['match_label']}。" if selection_kind == "task_matched" else
                  f"第{match_details['match_level']}级·{match_details['match_label']}。") + reason
    if original is not None and aim is not None:
        score_basis = (target.get("difficulty_plan") or {}).get("basis") or ("结合整体成绩与该题失分" if target.get("overall_score_rate") is not None else "整体成绩缺失，仅依据该题作答")
        reason += f" {basis}难度 {float(original):g} 级，{score_basis}，目标 {float(aim):.1f} 级；本题 {candidate['difficulty']} 级（受难度上限约束）。"
    elif aim is not None:
        reason += f" {(target.get('difficulty_plan') or {}).get('basis', '')}，目标难度 {float(aim):g} 级，本题 {candidate['difficulty']} 级。"
    tasks = [task for task in (practice_tasks if practice_tasks is not None else target.get("training_tasks") or [])
             if selection_kind in {"direct", "task_matched"} and _practice_matches(
                 candidate, matched_key, [task], part_id=(match_details or {}).get("candidate_part_id"))]
    if tasks:
        reason += " 本题训练任务：" + "；".join(str(task["label"]) for task in tasks) + "。"
    if (match_details or {}).get("practice_role") == "step_practice":
        reason += " 本题作为目标环节练习；完成选择或填空不等同于已能完整书写原题过程。"
    return {
        "item_id": item_id,
        "item_order": 0,
        "slot": slot,
        "question_id": int(candidate["question_id"]),
        "question_number": str(candidate["question_number"]),
        "question_text": str(candidate["question_text"]),
        "stage": stage,
        "selection_kind": selection_kind,
        **({key: deepcopy(match_details[key]) for key in (
            "match_level", "match_label", "matched_topic_keys", "matched_skill_keys", "source_part_id", "candidate_part_id",
            "task_matched_key", "task_operations", "task_match_evidence", "practice_purpose", "difficulty_basis", "evidence_confidence"
        ) if key in match_details} if match_details else {}),
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
        "question_type": str(candidate.get("question_type") or ""),
        "practice_role": (match_details or {}).get("practice_role", "supplement"),
        "difficulty_band": _difficulty_band({"candidate": candidate, "target": target}),
        "part_assessment": deepcopy(candidate.get("part_assessment")),
        "practice_tasks": deepcopy(tasks),
        "response_modes": list(candidate.get("response_modes_by_key", {}).get(matched_key, [])),
        "source_paper": candidate["source_paper"],
        "reason": reason,
        "locked": False,
        "replacement_history": [],
    }


def _difficulty(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 1 <= parsed <= 10:
        return None
    # 原始小数难度原样保留；归入哪一档由 standard_difficulty.difficulty_level 决定。
    return parsed


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
    return {
        "paper_mode": value.get("paper_mode", "individual"),
        "question_count": value["question_count"],
        "expected_minutes": value.get("expected_minutes", 45),
        "difficulty_min": value.get("difficulty_min", 1),
        "difficulty_max": value.get("difficulty_max", 8),
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
        not value.startswith(("kp_", "ki_", "sk_"))
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
    context_leaves = set(leaves)
    for edge in diagnosis.get("knowledge_associations", ()):
        skill = str(edge.get("skill_key") or "")
        if edge.get("topic_key") in context_leaves and edge.get("same_part_question_count", 0) > 0 and skill and skill not in seen:
            seen.add(skill)
            leaves.append(skill)
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
            if not keys and raw_key.casefold().startswith(("kp_", "ki_", "sk_")):
                keys = [raw_key.casefold()]
            display = str(item.get("knowledge_point") or "").strip()
            for key in keys:
                node = resolver.node(key)
                existing = snapshot.get((student_id, key), {})
                has_current = bool(existing) and existing.get("value") is not None
                frozen_current = item.get("parameter_version") == CURRENT_MASTERY_PARAMETERS.version
                snapshot[(student_id, key)] = {
                    **existing,
                    **({field: item.get(field) for field in ("interval_low", "interval_high", "tier", "observation_count", "full_correct_count", "recent_trend", "parameter_version")} if frozen_current and not has_current else {}),
                    "stable_key": key,
                    "display_name": (
                        node.display_name if node is not None else display or key
                    ),
                    "mode": "current",
                    "status": "available",
                    "value": existing["value"] if has_current else value,
                    "evidence_count": existing.get("evidence_count", count) if has_current else count,
                    "parameter_version": str(
                        existing.get("parameter_version") or item.get("parameter_version") or ""
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
