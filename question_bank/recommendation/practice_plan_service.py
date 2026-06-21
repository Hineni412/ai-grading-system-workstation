from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.recommendation_engine import (
    practice_gradient_fit,
    text_similarity,
)
from question_bank.recommendation.fine_skill_matching import fine_skill_match_score
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


class PracticePlanService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

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
    ) -> dict[str, Any]:
        profiles = [
            dict(item)
            for item in profile_set.get("students", [])
            if isinstance(item, Mapping) and item.get("student_id") is not None
        ]
        if variant_mode not in {"individual", "auto_group"}:
            raise ValueError(f"unsupported variant mode: {variant_mode}")
        normalized_overrides = _normalized_teacher_groups(teacher_groups, profiles)
        if normalized_overrides:
            grouped_profiles, ungrouped_students = _teacher_override_groups(profiles, normalized_overrides)
        elif variant_mode == "auto_group":
            grouped_profiles, ungrouped_students = _automatic_groups(profiles)
        else:
            grouped_profiles = [
                ([profile], {"rule": "individual", "covered_concept_ids": _confirmed_concept_ids(profile)})
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
    ) -> dict[str, Any]:
        count = int(question_count)
        if not 8 <= count <= 12:
            raise ValueError("question_count must be between 8 and 12")
        stage_counts = allocate_stage_counts(count, stage_ratios)
        resolved_weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        weak_points = _eligible_weak_points(diagnosis_profile)
        if not weak_points:
            return {
                "items": [],
                "stage_counts": stage_counts,
                "shortages": [
                    {"stage": stage, "requested_count": requested, "selected_count": 0, "missing_count": requested}
                    for stage, requested in stage_counts.items()
                ],
                "warnings": ["没有已确认且可用于推荐的薄弱知识点。"],
                "dedupe_summary": {"removed_count": 0, "reason_counts": {}},
            }

        initialize_database(self.db_path)
        resolved_exclusions, source_link_warnings = self._resolved_exclusions(
            diagnosis_profile,
            exclude_question_ids,
        )
        candidates, relations = self._load_candidates_and_relations()
        role_candidates = _assign_candidate_roles(candidates, weak_points, relations)
        deduped, dedupe_summary = _dedupe_candidates(
            role_candidates,
            exclude_question_ids=resolved_exclusions,
            similarity_threshold=float(similarity_threshold),
        )
        frequency_by_id = QuestionFrequencyService(self.db_path).metrics_for_questions(
            [int(item["id"]) for item in deduped]
        )

        selected: list[dict[str, Any]] = []
        selected_ids: set[int] = set()
        paper_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        shortages: list[dict[str, Any]] = []
        warnings: list[str] = list(source_link_warnings)
        for stage in STAGE_ORDER:
            requested = stage_counts[stage]
            stage_selected = self._select_for_stage(
                stage=stage,
                requested=requested,
                candidates=deduped,
                selected_ids=selected_ids,
                paper_counts=paper_counts,
                method_counts=method_counts,
                frequency_by_id=frequency_by_id,
                weights=resolved_weights,
                allow_broad_fallback=allow_broad_fallback,
            )
            selected.extend(stage_selected)
            if len(stage_selected) < requested:
                shortage = {
                    "stage": stage,
                    "requested_count": requested,
                    "selected_count": len(stage_selected),
                    "missing_count": requested - len(stage_selected),
                    "concept_ids": sorted({int(item["concept_id"]) for item in weak_points}),
                }
                shortages.append(shortage)
                warnings.append(
                    f"{stage} 阶段缺少 {shortage['missing_count']} 道已对齐候选题，未使用弱相关题目补足。"
                )

        for item_order, item in enumerate(selected, start=1):
            item["item_order"] = item_order
        return {
            "student_ids": _student_ids(diagnosis_profile),
            "items": selected,
            "stage_counts": stage_counts,
            "shortages": shortages,
            "warnings": warnings,
            "dedupe_summary": dedupe_summary,
            "generation_config": {
                "question_count": count,
                "stage_ratios": dict(DEFAULT_STAGE_RATIOS if stage_ratios is None else stage_ratios),
                "weights": resolved_weights,
                "include_historical_wrong_questions": bool(include_historical_wrong_questions),
                "allow_broad_fallback": bool(allow_broad_fallback),
            },
        }

    def _resolved_exclusions(
        self,
        diagnosis_profile: Mapping[str, Any],
        explicit_question_ids: Iterable[int] | None,
    ) -> tuple[set[int], list[str]]:
        if explicit_question_ids is not None:
            return {int(value) for value in explicit_question_ids}, []
        exam_scope = diagnosis_profile.get("exam_scope")
        if not isinstance(exam_scope, Mapping):
            return set(), []
        link_service = SourceQuestionLinkService(self.db_path)
        exclusions: set[int] = set()
        warnings: list[str] = []
        for session_id in exam_scope.get("session_ids", []):
            exclusions.update(link_service.confirmed_bank_question_ids(session_id))
            warning = link_service.exclusion_warning(session_id)
            if warning and warning not in warnings:
                warnings.append(warning)
        return exclusions, warnings

    def _load_candidates_and_relations(
        self,
    ) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
        with connect(self.db_path) as conn:
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
            tags_by_question = _load_tags(conn, [int(item["id"]) for item in candidates])
            concept_by_source = {
                (str(row["source_namespace"]), str(row["source_value"])): int(row["concept_id"])
                for row in conn.execute(
                    """
                    SELECT source_namespace, source_value, concept_id
                    FROM knowledge_source_mappings
                    WHERE status = 'confirmed' AND concept_id IS NOT NULL
                      AND source_namespace IN ('question_tag', 'canonical_knowledge_id')
                    """
                ).fetchall()
            }
            relations: dict[int, list[dict[str, Any]]] = {}
            for row in conn.execute(
                "SELECT * FROM knowledge_relations ORDER BY source_concept_id, id"
            ).fetchall():
                relations.setdefault(int(row["source_concept_id"]), []).append(dict(row))

        for candidate in candidates:
            tags = tags_by_question.get(int(candidate["id"]), {})
            candidate["tags"] = tags
            concept_ids: set[int] = set()
            for tag_type, values in tags.items():
                namespace = "canonical_knowledge_id" if tag_type == "canonical_knowledge_id" else "question_tag"
                for value in values:
                    concept_id = concept_by_source.get((namespace, value))
                    if concept_id is not None:
                        concept_ids.add(concept_id)
            candidate["confirmed_concept_ids"] = sorted(concept_ids)
            candidate["fingerprint"] = _candidate_fingerprint(candidate)
        return candidates, relations

    def _select_for_stage(
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
        allow_broad_fallback: bool,
    ) -> list[dict[str, Any]]:
        scored: list[tuple[tuple[float, ...], int, dict[str, Any]]] = []
        for candidate in candidates:
            question_id = int(candidate["id"])
            if question_id in selected_ids or stage not in candidate["stage_roles"]:
                continue
            paper_key = _paper_key(candidate)
            method_key = _method_key(candidate)
            diversity = _diversity_fit(paper_counts[paper_key], method_counts[method_key] if method_key else 0)
            metrics = frequency_by_id.get(question_id)
            role = candidate["stage_roles"][stage]
            result = score_candidate(
                concept_match=role["concept_match"],
                mapping_status="confirmed",
                fine_skill_match=role.get("fine_skill_match", 0.0),
                frequency_fit=frequency_fit_score(metrics) if getattr(metrics, "available", False) else None,
                gradient_fit=practice_gradient_fit(stage, candidate.get("difficulty")),
                diversity_fit=diversity,
                weights=weights,
                sub_skill_boost=role.get("sub_skill_boost", 0.0),
                allow_broad_fallback=allow_broad_fallback,
            )
            if result.eligible:
                ranking = (
                    -float(role.get("fine_skill_match", 0.0)),
                    -float(result.components["gradient"]),
                    -float(result.components["frequency"]),
                    -float(result.components["diversity"]),
                    -float(result.total_score),
                )
                scored.append((ranking, question_id, _item_payload(candidate, stage, result, metrics)))
        scored.sort(key=lambda item: (item[0], item[1]))

        selected: list[dict[str, Any]] = []
        for _, question_id, payload in scored:
            if len(selected) >= requested:
                break
            candidate = next(item for item in candidates if int(item["id"]) == question_id)
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


def _automatic_groups(
    profiles: list[dict[str, Any]],
) -> tuple[list[tuple[list[dict[str, Any]], dict[str, Any]]], list[str]]:
    buckets: dict[tuple[str, tuple[int, ...]], list[dict[str, Any]]] = {}
    ungrouped: list[str] = []
    groups: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for profile in profiles:
        concept_ids = _confirmed_concept_ids(profile)
        score_band = _score_rate_band(profile.get("score_rate"))
        if not concept_ids or score_band == "unknown":
            student_id = str(profile["student_id"])
            ungrouped.append(student_id)
            groups.append(
                (
                    [profile],
                    {
                        "rule": "individual_unconfirmed_or_missing_score",
                        "covered_concept_ids": concept_ids,
                        "score_rate_band": score_band,
                    },
                )
            )
            continue
        buckets.setdefault((score_band, tuple(concept_ids)), []).append(profile)
    for (score_band, concept_ids), members in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], item[0][1], str(item[1][0]["student_id"])),
    ):
        groups.append(
            (
                members,
                {
                    "rule": "confirmed_concept_vector_and_score_band",
                    "covered_concept_ids": list(concept_ids),
                    "score_rate_band": score_band,
                    "weakness_vector": _average_weakness_vector(members, concept_ids),
                },
            )
        )
    groups.sort(key=lambda item: min(str(profile["student_id"]) for profile in item[0]))
    return groups, sorted(ungrouped)


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
                    "covered_concept_ids": sorted(
                        {
                            concept_id
                            for member in members
                            for concept_id in _confirmed_concept_ids(member)
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
                        "covered_concept_ids": _confirmed_concept_ids(profile),
                    },
                )
            )
    return groups, []


def _confirmed_concept_ids(profile: Mapping[str, Any]) -> list[int]:
    return sorted(
        {
            int(item["concept_id"])
            for item in profile.get("weak_points", [])
            if isinstance(item, Mapping)
            and item.get("concept_id") is not None
            and str(item.get("mapping_status") or "") == "confirmed"
            and item.get("eligible_for_recommendation") is not False
        }
    )


def _average_weakness_vector(
    profiles: list[dict[str, Any]],
    concept_ids: Iterable[int],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for concept_id in concept_ids:
        values = [
            1.0 - min(1.0, max(0.0, float(item.get("mastery") or 0.0)))
            for profile in profiles
            for item in profile.get("weak_points", [])
            if isinstance(item, Mapping)
            and item.get("concept_id") is not None
            and int(item["concept_id"]) == int(concept_id)
            and str(item.get("mapping_status") or "") == "confirmed"
            and item.get("eligible_for_recommendation") is not False
        ]
        result[str(concept_id)] = round(sum(values) / len(values), 4) if values else 0.0
    return result


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


def _eligible_weak_points(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(profile.get("students"), list):
        raw = [
            item
            for student in profile["students"]
            if isinstance(student, Mapping)
            for item in student.get("weak_points", [])
            if isinstance(item, Mapping)
        ]
    else:
        raw = [item for item in profile.get("weak_points", []) if isinstance(item, Mapping)]
    result: list[dict[str, Any]] = []
    for item in raw:
        try:
            concept_id = int(item.get("concept_id"))
        except (TypeError, ValueError):
            continue
        if item.get("eligible_for_recommendation") is False:
            continue
        if str(item.get("mapping_status") or "") != "confirmed":
            continue
        result.append({**dict(item), "concept_id": concept_id})
    return result


def _assign_candidate_roles(
    candidates: list[dict[str, Any]],
    weak_points: list[dict[str, Any]],
    relations: Mapping[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        roles: dict[str, dict[str, Any]] = {}
        concept_ids = set(candidate.get("confirmed_concept_ids", []))
        if not concept_ids:
            continue
        difficulty = _difficulty(candidate.get("difficulty"))
        for weak in weak_points:
            target_id = int(weak["concept_id"])
            target_skills = [
                str(value).strip()
                for value in weak.get("sub_skill_tags", [])
                if str(value).strip()
            ]
            if not target_skills:
                source_skill = str(
                    weak.get("source_term")
                    or weak.get("source_display")
                    or weak.get("concept_name")
                    or ""
                ).strip()
                if source_skill:
                    target_skills = [source_skill]
            if target_id in concept_ids:
                weak_sub_skills = [s.strip().casefold() for s in weak.get("sub_skill_tags", []) if s]
                sub_skill_boost = 0.0
                matched_skills = []
                candidate_skill_tags = [
                    str(value).strip()
                    for tag_type in ("knowledge_point", "prerequisite", "method", "model")
                    for value in candidate.get("tags", {}).get(tag_type, [])
                    if str(value).strip()
                ]
                fine_skill_match = fine_skill_match_score(
                    target_skills,
                    candidate_skill_tags,
                )
                if weak_sub_skills:
                    candidate_tag_values = set()
                    fold_to_orig = {}
                    for tag_type, vals in candidate.get("tags", {}).items():
                        for val in vals:
                            cleaned = val.strip()
                            candidate_tag_values.add(cleaned.casefold())
                            fold_to_orig[cleaned.casefold()] = cleaned
                    intersection = candidate_tag_values.intersection(weak_sub_skills)
                    sub_skill_boost = len(intersection) / len(weak_sub_skills)
                    matched_skills = [fold_to_orig[s] for s in intersection]

                _set_role(
                    roles,
                    "direct",
                    1.0,
                    target_id,
                    target_id,
                    "direct",
                    sub_skill_boost=sub_skill_boost,
                    sub_skill_match=matched_skills,
                    fine_skill_match=fine_skill_match,
                    target_skills=target_skills,
                )
                if difficulty is not None and difficulty <= 3:
                    _set_role(
                        roles,
                        "prerequisite",
                        0.75,
                        target_id,
                        target_id,
                        "difficulty_scaffold",
                        fine_skill_match=fine_skill_match,
                        target_skills=target_skills,
                    )
                if (difficulty is not None and difficulty >= 7) or candidate["tags"].get("model"):
                    _set_role(
                        roles,
                        "transfer",
                        0.8,
                        target_id,
                        target_id,
                        "direct_transfer",
                        fine_skill_match=fine_skill_match,
                        target_skills=target_skills,
                    )
            for relation in relations.get(target_id, []):
                related_id = int(relation["target_concept_id"])
                if related_id not in concept_ids:
                    continue
                relation_type = str(relation["relation_type"])
                strength = float(relation.get("weight") or 0.0)
                if relation_type in {"prerequisite", "parent"}:
                    _set_role(
                        roles,
                        "prerequisite",
                        strength,
                        target_id,
                        related_id,
                        relation_type,
                        fine_skill_match=1.0,
                        target_skills=target_skills,
                        match_basis="confirmed_relation",
                    )
                elif relation_type == "related":
                    _set_role(
                        roles,
                        "transfer",
                        strength,
                        target_id,
                        related_id,
                        relation_type,
                        fine_skill_match=1.0,
                        target_skills=target_skills,
                        match_basis="confirmed_relation",
                    )
        if roles:
            result.append({**candidate, "stage_roles": roles})
    return result


def _set_role(
    roles: dict[str, dict[str, Any]],
    stage: str,
    strength: float,
    target_concept_id: int,
    matched_concept_id: int,
    relation_type: str,
    *,
    sub_skill_boost: float = 0.0,
    sub_skill_match: list[str] | None = None,
    fine_skill_match: float = 0.0,
    target_skills: list[str] | None = None,
    match_basis: str = "fine_skill",
) -> None:
    existing = roles.get(stage)
    if existing is None or float(existing["concept_match"]) < strength:
        roles[stage] = {
            "concept_match": round(min(1.0, max(0.0, strength)), 4),
            "target_concept_id": target_concept_id,
            "matched_concept_id": matched_concept_id,
            "relation_type": relation_type,
            "sub_skill_boost": round(sub_skill_boost, 4),
            "sub_skill_match": sub_skill_match or [],
            "fine_skill_match": round(min(1.0, max(0.0, fine_skill_match)), 4),
            "target_skills": list(target_skills or []),
            "match_basis": match_basis,
        }


def _dedupe_candidates(
    candidates: list[dict[str, Any]],
    *,
    exclude_question_ids: set[int],
    similarity_threshold: float,
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
        if any(_is_duplicate(candidate, original, original_similarity_threshold) for original in excluded):
            reason_counts["near_current_exam_original"] += 1
            continue
        remaining.append(candidate)

    kept: list[dict[str, Any]] = []
    for candidate in remaining:
        if any(_is_duplicate(candidate, existing, similarity_threshold) for existing in kept):
            reason_counts["candidate_duplicate"] += 1
            continue
        kept.append(candidate)
    return kept, {
        "removed_count": sum(reason_counts.values()),
        "reason_counts": dict(reason_counts),
    }


def _is_duplicate(left: Mapping[str, Any], right: Mapping[str, Any], threshold: float) -> bool:
    if int(left["id"]) == int(right["id"]):
        return True
    left_fingerprint = str(left.get("fingerprint") or "")
    right_fingerprint = str(right.get("fingerprint") or "")
    if left_fingerprint and left_fingerprint == right_fingerprint:
        return True
    return text_similarity(left.get("question_text"), right.get("question_text")) >= threshold


def _item_payload(candidate: Mapping[str, Any], stage: str, result: Any, metrics: Any) -> dict[str, Any]:
    role = candidate["stage_roles"][stage]
    return {
        "question_id": int(candidate["id"]),
        "stage": stage,
        "target_concept_id": int(role["target_concept_id"]),
        "matched_concept_id": int(role["matched_concept_id"]),
        "relation_type": role["relation_type"],
        "recommend_score": result.total_score,
        "score_components": dict(result.components),
        "warnings": list(result.warnings),
        "question_fingerprint": str(candidate.get("fingerprint") or ""),
        "question_text": str(candidate.get("question_text") or ""),
        "question_number": str(candidate.get("question_number") or ""),
        "difficulty": candidate.get("difficulty"),
        "source_paper": str(candidate.get("paper_title") or candidate.get("paper_source_file") or ""),
        "method_tags": list(candidate["tags"].get("method", [])),
        "model_tags": list(candidate["tags"].get("model", [])),
        "sub_skill_match": list(role.get("sub_skill_match", [])),
        "fine_skill_match": float(role.get("fine_skill_match", 0.0)),
        "target_skills": list(role.get("target_skills", [])),
        "match_basis": str(role.get("match_basis") or "fine_skill"),
        "frequency": metrics.to_dict() if metrics is not None else {},
    }


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
