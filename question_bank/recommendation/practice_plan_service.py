from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
import sqlite3
from typing import Any

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.recommendation_engine import (
    practice_gradient_fit,
    text_similarity,
)
from question_bank.recommendation.scoring import (
    DEFAULT_WEIGHTS,
    frequency_fit_score,
    score_candidate,
)
from question_bank.recommendation.training_plan import (
    DEFAULT_STAGE_RATIOS,
    allocate_stage_counts,
)
from question_bank.services.question_frequency_service import (
    QuestionFrequencyService,
    build_question_fingerprint,
)
from question_bank.services.source_question_link_service import SourceQuestionLinkService


STAGE_ORDER = ("direct", "prerequisite", "transfer")
RELATED_FILL_POLICIES = {"ask", "exact_only", "allow_neighbors"}


class PracticePlanService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        external_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.external_connection = external_connection

    def generate(
        self,
        profile_set: Mapping[str, Any],
        *,
        variant_mode: str = "individual",
        teacher_groups: Mapping[str, Iterable[str]] | None = None,
        question_count: int = 10,
        stage_ratios: Mapping[str, object] | None = None,
        weights: Mapping[str, object] | None = None,
        exclude_current_exam_originals: bool = True,
        include_historical_wrong_questions: bool = False,
        similarity_threshold: float = 0.92,
        allow_broad_fallback: bool = False,
        related_fill_policy: str = "ask",
    ) -> dict[str, Any]:
        related_fill_policy = _validate_related_fill_policy(related_fill_policy)
        profiles = [
            dict(item)
            for item in profile_set.get("students", [])
            if isinstance(item, Mapping) and item.get("student_id") is not None
        ]
        if variant_mode not in {"individual", "auto_group"}:
            raise ValueError(f"unsupported variant mode: {variant_mode}")
        question_tag_diagnosis = _is_question_tag_diagnosis(profile_set)
        if not question_tag_diagnosis:
            raise ValueError("only question_tag diagnosis is supported")
        normalized_overrides = _normalized_teacher_groups(teacher_groups, profiles)
        if normalized_overrides:
            grouped_profiles, ungrouped_students = _teacher_override_groups(
                profiles,
                normalized_overrides,
            )
        elif variant_mode == "auto_group":
            grouped_profiles, ungrouped_students = _automatic_question_tag_groups(
                profiles
            )
        else:
            grouped_profiles = [
                (
                    [profile],
                    {
                        "rule": "individual",
                        "covered_knowledge_points": sorted(
                            _question_tag_targets({"students": [profile]})
                        ),
                    },
                )
                for profile in profiles
            ]
            ungrouped_students = []

        variants: list[dict[str, Any]] = []
        exam_scope = dict(profile_set.get("exam_scope") or {})
        for index, (members, grouping_reason) in enumerate(grouped_profiles, start=1):
            member_ids = [str(item["student_id"]) for item in members]
            variant_type = "group" if len(members) > 1 else "individual"
            variant_key = (
                f"student-{member_ids[0]}"
                if variant_type == "individual"
                else f"group-{index}"
            )
            diagnosis_snapshot = {
                "diagnosis_identity": profile_set.get("diagnosis_identity"),
                "scope": {"mode": "selected", "student_ids": member_ids},
                "exam_scope": exam_scope,
                "students": members,
            }
            generated = self.generate_variant(
                diagnosis_snapshot,
                question_count=question_count,
                stage_ratios=stage_ratios,
                weights=weights,
                exclude_question_ids=None if exclude_current_exam_originals else set(),
                include_historical_wrong_questions=include_historical_wrong_questions,
                similarity_threshold=similarity_threshold,
                allow_broad_fallback=allow_broad_fallback,
                related_fill_policy=related_fill_policy,
            )
            variants.append(
                {
                    "variant_key": variant_key,
                    "variant_type": variant_type,
                    "student_ids": member_ids,
                    "grouping_reason": grouping_reason,
                    "diagnosis_snapshot": diagnosis_snapshot,
                    **generated,
                }
            )
        resolved_stage_ratios = dict(DEFAULT_STAGE_RATIOS if stage_ratios is None else stage_ratios)
        resolved_weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        return {
            "scope_snapshot": dict(profile_set.get("scope") or {}),
            "exam_scope": exam_scope,
            "diagnosis_snapshot": dict(profile_set),
            "generation_config": {
                "question_count": int(question_count),
                "stage_ratios": resolved_stage_ratios,
                "weights": resolved_weights,
                "exclude_current_exam_originals": bool(exclude_current_exam_originals),
                "include_historical_wrong_questions": bool(include_historical_wrong_questions),
                "similarity_threshold": float(similarity_threshold),
                "allow_broad_fallback": bool(allow_broad_fallback),
                "related_fill_policy": related_fill_policy,
            },
            "variant_mode": variant_mode,
            "variants": variants,
            "warnings": _unique_text(
                warning
                for variant in variants
                for warning in variant.get("warnings", [])
            ),
            "ungrouped_students": ungrouped_students,
            "teacher_override": {
                "allowed": True,
                "applied": bool(normalized_overrides),
                "assignments": normalized_overrides,
            },
        }

    def generate_variant(
        self,
        diagnosis_profile: Mapping[str, Any],
        *,
        question_count: int = 10,
        stage_ratios: Mapping[str, object] | None = None,
        weights: Mapping[str, object] | None = None,
        exclude_question_ids: Iterable[int] | None = None,
        include_historical_wrong_questions: bool = False,
        similarity_threshold: float = 0.92,
        allow_broad_fallback: bool = False,
        related_fill_policy: str = "ask",
    ) -> dict[str, Any]:
        count = int(question_count)
        if not 8 <= count <= 12:
            raise ValueError("question_count must be between 8 and 12")
        related_fill_policy = _validate_related_fill_policy(related_fill_policy)
        stage_counts = allocate_stage_counts(count, stage_ratios)
        resolved_weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        if not _is_question_tag_diagnosis(diagnosis_profile):
            raise ValueError("only question_tag diagnosis is supported")
        return self._generate_question_tag_variant(
            diagnosis_profile,
            count=count,
            stage_counts=stage_counts,
            stage_ratios=stage_ratios,
            weights=resolved_weights,
            exclude_question_ids=exclude_question_ids,
            include_historical_wrong_questions=include_historical_wrong_questions,
            similarity_threshold=float(similarity_threshold),
            related_fill_policy=related_fill_policy,
        )

    def _generate_question_tag_variant(
        self,
        diagnosis_profile: Mapping[str, Any],
        *,
        count: int,
        stage_counts: Mapping[str, int],
        stage_ratios: Mapping[str, object] | None,
        weights: Mapping[str, object],
        exclude_question_ids: Iterable[int] | None,
        include_historical_wrong_questions: bool,
        similarity_threshold: float,
        related_fill_policy: str,
    ) -> dict[str, Any]:
        if self.external_connection is None:
            initialize_database(self.db_path)
        targets = _question_tag_targets(diagnosis_profile)
        generation_config = {
            "question_count": count,
            "stage_ratios": dict(
                DEFAULT_STAGE_RATIOS if stage_ratios is None else stage_ratios
            ),
            "weights": dict(weights),
            "include_historical_wrong_questions": bool(
                include_historical_wrong_questions
            ),
            "related_fill_policy": related_fill_policy,
        }
        if not targets:
            return {
                "student_ids": _student_ids(diagnosis_profile),
                "items": [],
                "stage_counts": dict(stage_counts),
                "shortages": [
                    {
                        "stage": stage,
                        "requested_count": requested,
                        "selected_count": 0,
                        "missing_count": requested,
                        "decision_required": False,
                    }
                    for stage, requested in stage_counts.items()
                ],
                "warnings": ["没有可用于推荐的题库知识点标签。"],
                "dedupe_summary": {"removed_count": 0, "reason_counts": {}},
                "generation_config": generation_config,
            }

        resolved_exclusions, source_link_warnings = self._resolved_exclusions(
            diagnosis_profile,
            exclude_question_ids,
            external_connection=self.external_connection,
        )
        candidates = self._load_question_tag_candidates()
        eligible: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_knowledge = {
                str(value).strip()
                for value in candidate.get("tags", {}).get("knowledge_point", [])
                if str(value).strip()
            }
            matched_points = sorted(candidate_knowledge.intersection(targets))
            if not matched_points:
                continue
            target_context: dict[str, set[str]] = {
                tag_type: set()
                for tag_type in ("sub_skill", "method", "model", "prerequisite")
            }
            for point in matched_points:
                for tag_type, values in targets[point].items():
                    target_context[tag_type].update(values)
            tag_matches: dict[str, list[str]] = {}
            matched_value_count = 0
            target_value_count = 0
            for tag_type, values in target_context.items():
                target_value_count += len(values)
                candidate_values = {
                    str(value).strip()
                    for value in candidate.get("tags", {}).get(tag_type, [])
                    if str(value).strip()
                }
                overlap = sorted(values.intersection(candidate_values))
                if overlap:
                    tag_matches[tag_type] = overlap
                    matched_value_count += len(overlap)
            tag_overlap = (
                matched_value_count / target_value_count
                if target_value_count
                else 0.0
            )
            eligible.append(
                {
                    **candidate,
                    "tag_match": {
                        "knowledge_points": matched_points,
                        "tag_matches": tag_matches,
                        "tag_overlap": round(tag_overlap, 4),
                    },
                }
            )

        deduped, dedupe_summary = _dedupe_candidates(
            eligible,
            exclude_question_ids=resolved_exclusions,
            similarity_threshold=similarity_threshold,
            use_fingerprint=False,
        )
        frequency_by_id = QuestionFrequencyService(
            self.db_path,
            external_connection=self.external_connection,
        ).metrics_for_questions(
            [int(item["id"]) for item in deduped]
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[int] = set()
        paper_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        shortages: list[dict[str, Any]] = []
        warnings: list[str] = list(source_link_warnings)
        for stage in STAGE_ORDER:
            requested = int(stage_counts[stage])
            stage_selected = self._select_question_tags_for_stage(
                stage=stage,
                requested=requested,
                candidates=deduped,
                selected_ids=selected_ids,
                paper_counts=paper_counts,
                method_counts=method_counts,
                frequency_by_id=frequency_by_id,
                weights=weights,
            )
            selected.extend(stage_selected)
            if len(stage_selected) < requested:
                missing = requested - len(stage_selected)
                shortages.append(
                    {
                        "stage": stage,
                        "requested_count": requested,
                        "selected_count": len(stage_selected),
                        "missing_count": missing,
                        "knowledge_points": sorted(targets),
                        "decision_required": False,
                    }
                )
                warnings.append(
                    f"{stage} 阶段缺少 {missing} 道知识点完全相同的候选题，未补入近义或相邻标签题。"
                )
        for item_order, item in enumerate(selected, start=1):
            item["item_order"] = item_order
        return {
            "student_ids": _student_ids(diagnosis_profile),
            "items": selected,
            "stage_counts": dict(stage_counts),
            "shortages": shortages,
            "warnings": _unique_text(warnings),
            "dedupe_summary": dedupe_summary,
            "generation_config": generation_config,
        }

    def _load_question_tag_candidates(self) -> list[dict[str, Any]]:
        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
            rows = conn.execute(
                """
                SELECT
                    q.*,
                    p.title AS paper_title,
                    p.source_file AS paper_source_file,
                    p.exam_type,
                    p.grade,
                    p.city
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.id
                """
            ).fetchall()
            candidates = [dict(row) for row in rows]
            tags_by_question = _load_tags(
                conn,
                [int(item["id"]) for item in candidates],
            )
        for candidate in candidates:
            candidate["tags"] = tags_by_question.get(int(candidate["id"]), {})
            candidate["fingerprint"] = _candidate_fingerprint(candidate)
        return candidates

    def _select_question_tags_for_stage(
        self,
        *,
        stage: str,
        requested: int,
        candidates: list[dict[str, Any]],
        selected_ids: set[int],
        paper_counts: Counter[str],
        method_counts: Counter[str],
        frequency_by_id: Mapping[int, Any],
        weights: Mapping[str, object],
    ) -> list[dict[str, Any]]:
        scored: list[tuple[tuple[float, ...], int, dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            question_id = int(candidate["id"])
            if question_id in selected_ids:
                continue
            metrics = frequency_by_id.get(question_id)
            gradient = float(practice_gradient_fit(stage, candidate.get("difficulty")) or 0.5)
            frequency = (
                float(frequency_fit_score(metrics))
                if getattr(metrics, "available", False)
                else 0.5
            )
            paper_key = _paper_key(candidate)
            method_key = _method_key(candidate)
            diversity = _diversity_fit(
                paper_counts[paper_key],
                method_counts[method_key] if method_key else 0,
            )
            tag_overlap = float(candidate["tag_match"]["tag_overlap"])
            result = score_candidate(
                concept_match=1.0,
                mapping_status="confirmed",
                fine_skill_match=tag_overlap,
                frequency_fit=frequency,
                gradient_fit=gradient,
                diversity_fit=diversity,
                weights=weights,
                sub_skill_boost=tag_overlap,
                allow_broad_fallback=True,
            )
            components = dict(result.components)
            components["knowledge_match"] = components.pop("concept")
            components["tag_overlap"] = components.pop("fine_skill")
            ranking = (
                -tag_overlap,
                -gradient,
                -frequency,
                -diversity,
                -float(result.total_score),
            )
            payload = _question_tag_item_payload(
                candidate,
                stage,
                result.total_score,
                components,
                metrics,
                warnings=result.warnings,
            )
            scored.append((ranking, question_id, payload, candidate))
        scored.sort(key=lambda item: (item[0], item[1]))

        selected: list[dict[str, Any]] = []
        for _ranking, question_id, payload, candidate in scored:
            if len(selected) >= requested:
                break
            paper_key = _paper_key(candidate)
            method_key = _method_key(candidate)
            if paper_counts[paper_key] >= 2:
                continue
            if method_key and method_counts[method_key] >= 2:
                continue
            selected.append(payload)
            selected_ids.add(question_id)
            paper_counts[paper_key] += 1
            if method_key:
                method_counts[method_key] += 1
        return selected

    def _resolved_exclusions(
        self,
        diagnosis_profile: Mapping[str, Any],
        explicit_question_ids: Iterable[int] | None,
        *,
        external_connection: sqlite3.Connection | None = None,
    ) -> tuple[set[int], list[str]]:
        if explicit_question_ids is not None:
            return {int(value) for value in explicit_question_ids}, []
        exam_scope = diagnosis_profile.get("exam_scope")
        if not isinstance(exam_scope, Mapping):
            return set(), []
        link_service = SourceQuestionLinkService(
            self.db_path,
            external_connection=external_connection,
        )
        exclusions: set[int] = set()
        warnings: list[str] = []
        for session_id in exam_scope.get("session_ids", []):
            exclusions.update(link_service.confirmed_bank_question_ids(session_id))
            warning = link_service.exclusion_warning(session_id)
            if warning and warning not in warnings:
                warnings.append(warning)
        return exclusions, warnings

def _validate_related_fill_policy(value: object) -> str:
    policy = str(value or "ask").strip()
    if policy not in RELATED_FILL_POLICIES:
        raise ValueError(f"unsupported related fill policy: {policy}")
    return policy


def _is_question_tag_diagnosis(profile: Mapping[str, Any]) -> bool:
    return str(profile.get("diagnosis_identity") or "").strip() == "question_tag"


def _question_tag_targets(
    profile: Mapping[str, Any],
) -> dict[str, dict[str, set[str]]]:
    if isinstance(profile.get("students"), list):
        weak_points = [
            weak
            for student in profile["students"]
            if isinstance(student, Mapping)
            for weak in student.get("weak_points", [])
            if isinstance(weak, Mapping)
        ]
    else:
        weak_points = [
            weak
            for weak in profile.get("weak_points", [])
            if isinstance(weak, Mapping)
        ]

    targets: dict[str, dict[str, set[str]]] = {}
    for weak in weak_points:
        if weak.get("eligible_for_recommendation") is False:
            continue
        knowledge_point = str(weak.get("knowledge_point") or "").strip()
        if not knowledge_point:
            continue
        context = targets.setdefault(
            knowledge_point,
            {
                "sub_skill": set(),
                "method": set(),
                "model": set(),
                "prerequisite": set(),
            },
        )
        raw_context = weak.get("tag_context")
        if not isinstance(raw_context, Mapping):
            continue
        for tag_type in context:
            values = raw_context.get(tag_type, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, Iterable):
                continue
            context[tag_type].update(
                text
                for value in values
                if (text := str(value or "").strip())
            )
    return targets


def _question_tag_item_payload(
    candidate: Mapping[str, Any],
    stage: str,
    total_score: float,
    components: Mapping[str, float],
    metrics: Any,
    *,
    warnings: Iterable[object] = (),
) -> dict[str, Any]:
    match = candidate["tag_match"]
    knowledge_points = list(match["knowledge_points"])
    knowledge_point = str(knowledge_points[0])
    item_warnings = _unique_text(warnings)
    if _difficulty(candidate.get("difficulty")) is None and "题目难度缺失" not in item_warnings:
        item_warnings.append("题目难度缺失")
    return {
        "question_id": int(candidate["id"]),
        "stage": stage,
        "knowledge_key": f"knowledge_point:{knowledge_point}",
        "knowledge_point": knowledge_point,
        "match_kind": "exact",
        "reason": "与薄弱知识点标签完全相同",
        "recommend_score": float(total_score),
        "score_components": dict(components),
        "tag_matches": dict(match["tag_matches"]),
        "tags": {
            str(tag_type): list(values)
            for tag_type, values in candidate.get("tags", {}).items()
        },
        "warnings": item_warnings,
        "question_fingerprint": str(candidate.get("fingerprint") or ""),
        "question_text": str(candidate.get("question_text") or ""),
        "question_number": str(candidate.get("question_number") or ""),
        "difficulty": candidate.get("difficulty"),
        "source_paper": str(
            candidate.get("paper_title") or candidate.get("paper_source_file") or ""
        ),
        "frequency": metrics.to_dict() if metrics is not None else {},
    }


def _automatic_question_tag_groups(
    profiles: list[dict[str, Any]],
) -> tuple[list[tuple[list[dict[str, Any]], dict[str, Any]]], list[str]]:
    buckets: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    ungrouped: list[str] = []
    groups: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for profile in profiles:
        knowledge_points = tuple(sorted(_question_tag_targets({"students": [profile]})))
        score_band = _score_rate_band(profile.get("score_rate"))
        if not knowledge_points or score_band == "unknown":
            student_id = str(profile["student_id"])
            ungrouped.append(student_id)
            groups.append(
                (
                    [profile],
                    {
                        "rule": "individual_missing_tag_or_score",
                        "covered_knowledge_points": list(knowledge_points),
                        "score_rate_band": score_band,
                    },
                )
            )
            continue
        buckets.setdefault((score_band, knowledge_points), []).append(profile)
    for (score_band, knowledge_points), members in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], item[0][1], str(item[1][0]["student_id"])),
    ):
        groups.append(
            (
                members,
                {
                    "rule": "same_score_band_and_exact_knowledge_tags",
                    "covered_knowledge_points": list(knowledge_points),
                    "score_rate_band": score_band,
                },
            )
        )
    groups.sort(key=lambda item: min(str(profile["student_id"]) for profile in item[0]))
    return groups, sorted(ungrouped)


def _score_rate_band(value: object) -> str:
    try:
        score_rate = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if score_rate > 1:
        score_rate /= 100
    if score_rate < 0.4:
        return "low"
    if score_rate < 0.7:
        return "middle"
    return "high"


def _normalized_teacher_groups(
    teacher_groups: Mapping[str, Iterable[str]] | None,
    profiles: list[dict[str, Any]],
) -> dict[str, list[str]]:
    if not teacher_groups:
        return {}
    valid_ids = {str(item["student_id"]) for item in profiles}
    assigned: set[str] = set()
    result: dict[str, list[str]] = {}
    for group_name, raw_ids in teacher_groups.items():
        member_ids: list[str] = []
        for value in raw_ids:
            student_id = str(value)
            if student_id in valid_ids and student_id not in assigned:
                member_ids.append(student_id)
                assigned.add(student_id)
        if member_ids:
            result[str(group_name)] = member_ids
    return result


def _teacher_override_groups(
    profiles: list[dict[str, Any]],
    assignments: Mapping[str, list[str]],
) -> tuple[list[tuple[list[dict[str, Any]], dict[str, Any]]], list[str]]:
    by_id = {str(item["student_id"]): item for item in profiles}
    assigned: set[str] = set()
    groups: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for group_name, member_ids in assignments.items():
        members = [by_id[student_id] for student_id in member_ids]
        assigned.update(member_ids)
        groups.append(
            (
                members,
                {
                    "rule": "teacher_override",
                    "group_name": group_name,
                    "covered_knowledge_points": sorted(
                        {
                            point
                            for member in members
                            for point in _question_tag_targets(
                                {"students": [member]}
                            )
                        }
                    ),
                },
            )
        )
    for profile in profiles:
        student_id = str(profile["student_id"])
        if student_id not in assigned:
            groups.append(
                (
                    [profile],
                    {
                        "rule": "individual_not_assigned_by_teacher",
                        "covered_knowledge_points": sorted(
                            _question_tag_targets({"students": [profile]})
                        ),
                    },
                )
            )
    return groups, []


def _dedupe_candidates(
    candidates: list[dict[str, Any]],
    *,
    exclude_question_ids: set[int],
    similarity_threshold: float,
    use_fingerprint: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    excluded = [item for item in candidates if int(item["id"]) in exclude_question_ids]
    reason_counts: Counter[str] = Counter()
    remaining: list[dict[str, Any]] = []
    for candidate in candidates:
        question_id = int(candidate["id"])
        if question_id in exclude_question_ids:
            reason_counts["confirmed_current_exam_original"] += 1
            continue
        original_similarity_threshold = max(0.82, similarity_threshold - 0.08)
        if any(
            _is_duplicate(
                candidate,
                original,
                original_similarity_threshold,
                use_fingerprint=use_fingerprint,
            )
            for original in excluded
        ):
            reason_counts["near_current_exam_original"] += 1
            continue
        remaining.append(candidate)

    kept: list[dict[str, Any]] = []
    for candidate in remaining:
        if any(
            _is_duplicate(
                candidate,
                existing,
                similarity_threshold,
                use_fingerprint=use_fingerprint,
            )
            for existing in kept
        ):
            reason_counts["candidate_duplicate"] += 1
            continue
        kept.append(candidate)
    return kept, {
        "removed_count": sum(reason_counts.values()),
        "reason_counts": dict(reason_counts),
    }


def _is_duplicate(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    threshold: float,
    *,
    use_fingerprint: bool = True,
) -> bool:
    if int(left["id"]) == int(right["id"]):
        return True
    left_fingerprint = str(left.get("fingerprint") or "")
    right_fingerprint = str(right.get("fingerprint") or "")
    if use_fingerprint and left_fingerprint and left_fingerprint == right_fingerprint:
        return True
    return text_similarity(left.get("question_text"), right.get("question_text")) >= threshold


def _load_tags(conn, question_ids: list[int]) -> dict[int, dict[str, list[str]]]:
    if not question_ids:
        return {}
    placeholders = ", ".join("?" for _ in question_ids)
    result: dict[int, dict[str, list[str]]] = {question_id: {} for question_id in question_ids}
    for row in conn.execute(
        f"""
        SELECT question_id, tag_type, tag_value
        FROM question_tags
        WHERE question_id IN ({placeholders})
        ORDER BY id
        """,
        question_ids,
    ).fetchall():
        values = result[int(row["question_id"])].setdefault(str(row["tag_type"]), [])
        value = str(row["tag_value"])
        if value not in values:
            values.append(value)
    return result


def _candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    tags = [
        {"tag_type": tag_type, "tag_value": value}
        for tag_type, values in candidate.get("tags", {}).items()
        for value in values
    ]
    return build_question_fingerprint({**dict(candidate), "tags": tags})


def _paper_key(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("paper_id") or candidate.get("paper_source_file") or candidate["id"])


def _method_key(candidate: Mapping[str, Any]) -> str:
    methods = candidate.get("tags", {}).get("method", [])
    return str(methods[0]) if methods else ""


def _diversity_fit(paper_count: int, method_count: int) -> float:
    if paper_count == 0 and method_count == 0:
        return 1.0
    if paper_count <= 1 and method_count <= 1:
        return 0.5
    return 0.2


def _difficulty(value: object) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 10 else None


def _student_ids(profile: Mapping[str, Any]) -> list[str]:
    if isinstance(profile.get("students"), list):
        return [
            str(item.get("student_id"))
            for item in profile["students"]
            if isinstance(item, Mapping) and item.get("student_id") is not None
        ]
    return [str(profile["student_id"])] if profile.get("student_id") is not None else []


def _unique_text(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


__all__ = ["PracticePlanService"]
