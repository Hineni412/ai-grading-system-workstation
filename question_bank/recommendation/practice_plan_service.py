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
from question_bank.services.skill_catalog_service import SkillCatalogService
from question_bank.services.source_question_link_service import SourceQuestionLinkService


STAGE_ORDER = ("direct", "prerequisite", "transfer")
RELATED_FILL_POLICIES = {"ask", "exact_only", "allow_neighbors"}


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
                "diagnosis_identity": profile_set.get("diagnosis_identity"),
                "scope": {"mode": "selected", "student_ids": member_ids},
                "exam_scope": exam_scope,
                "confirmed_skill_ids": list(profile_set.get("confirmed_skill_ids") or []),
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
        if _is_question_tag_diagnosis(diagnosis_profile):
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
        if _is_skill_diagnosis(diagnosis_profile):
            return self._generate_skill_variant(
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
                "related_fill_policy": related_fill_policy,
            },
        }

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

    def _generate_skill_variant(
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
        initialize_database(self.db_path)
        catalog = SkillCatalogService(self.db_path)
        weak_points = _eligible_skill_weak_points(diagnosis_profile, catalog)
        generation_config = {
            "question_count": count,
            "stage_ratios": dict(DEFAULT_STAGE_RATIOS if stage_ratios is None else stage_ratios),
            "weights": dict(weights),
            "include_historical_wrong_questions": bool(include_historical_wrong_questions),
            "related_fill_policy": related_fill_policy,
        }
        if not weak_points:
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
                "warnings": ["没有可用于推荐的已确认具体技能。"],
                "dedupe_summary": {"removed_count": 0, "reason_counts": {}},
                "generation_config": generation_config,
            }

        resolved_exclusions, source_link_warnings = self._resolved_exclusions(
            diagnosis_profile,
            exclude_question_ids,
        )
        candidates, neighbors = self._load_skill_candidates_and_neighbors(catalog)
        role_candidates = _assign_skill_candidate_roles(
            candidates,
            weak_points,
            neighbors,
            include_neighbors=related_fill_policy == "allow_neighbors",
        )
        deduped, dedupe_summary = _dedupe_candidates(
            role_candidates,
            exclude_question_ids=resolved_exclusions,
            similarity_threshold=similarity_threshold,
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
        primary_weak = weak_points[0]
        available_neighbor_count = _available_neighbor_question_count(candidates, weak_points, neighbors)
        for stage in STAGE_ORDER:
            requested = int(stage_counts[stage])
            stage_selected = self._select_skill_for_stage(
                stage=stage,
                requested=requested,
                candidates=deduped,
                selected_ids=selected_ids,
                paper_counts=paper_counts,
                method_counts=method_counts,
                frequency_by_id=frequency_by_id,
            )
            selected.extend(stage_selected)
            if len(stage_selected) < requested:
                missing = requested - len(stage_selected)
                shortage = {
                    "stage": stage,
                    "requested_count": requested,
                    "selected_count": len(stage_selected),
                    "missing_count": missing,
                    "skill_id": int(primary_weak["skill_id"]),
                    "skill_name": str(primary_weak.get("skill_name") or ""),
                    "decision_required": related_fill_policy == "ask" and available_neighbor_count > 0,
                    "available_neighbor_count": available_neighbor_count,
                }
                shortages.append(shortage)
                if related_fill_policy == "ask" and available_neighbor_count > 0:
                    warnings.append(
                        f"还差 {missing} 道精确技能题；发现相近技能题，请由教师决定是否补入。"
                    )
                else:
                    warnings.append(f"还差 {missing} 道精确技能题，当前未静默补入其他技能。")

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

    def _load_skill_candidates_and_neighbors(
        self,
        catalog: SkillCatalogService,
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
            question_ids = [int(item["id"]) for item in candidates]
            tags_by_question = _load_tags(conn, question_ids)
            measured_rows = conn.execute(
                """
                SELECT question_id, skill_id
                FROM question_skill_links
                WHERE status = 'resolved' AND role = 'measured'
                ORDER BY question_id, id
                """
            ).fetchall()
            neighbor_rows = conn.execute(
                """
                SELECT source_skill_id, target_skill_id, kind, weight
                FROM skill_neighbors
                WHERE enabled = 1
                ORDER BY source_skill_id, weight DESC, id
                """
            ).fetchall()

        skill_cache: dict[int, dict[str, Any] | None] = {}

        def active_skill(skill_id: int) -> dict[str, Any] | None:
            stored_id = int(skill_id)
            if stored_id not in skill_cache:
                skill = catalog.get_skill(stored_id)
                skill_cache[stored_id] = skill if skill and skill.get("status") == "active" else None
            return skill_cache[stored_id]

        measured_by_question: dict[int, dict[int, dict[str, Any]]] = {}
        for row in measured_rows:
            skill = active_skill(int(row["skill_id"]))
            if skill is None:
                continue
            measured_by_question.setdefault(int(row["question_id"]), {})[int(skill["id"])] = {
                "skill_id": int(skill["id"]),
                "skill_name": str(skill["name"]),
                "topic_id": int(skill["topic_id"]),
                "topic_name": str(skill["topic_name"]),
            }

        neighbors: dict[int, list[dict[str, Any]]] = {}
        seen_neighbors: set[tuple[int, int, str]] = set()
        for row in neighbor_rows:
            source = active_skill(int(row["source_skill_id"]))
            target = active_skill(int(row["target_skill_id"]))
            if source is None or target is None or int(source["id"]) == int(target["id"]):
                continue
            identity = (int(source["id"]), int(target["id"]), str(row["kind"]))
            if identity in seen_neighbors:
                continue
            seen_neighbors.add(identity)
            neighbors.setdefault(int(source["id"]), []).append(
                {
                    "skill_id": int(target["id"]),
                    "skill_name": str(target["name"]),
                    "topic_id": int(target["topic_id"]),
                    "topic_name": str(target["topic_name"]),
                    "kind": str(row["kind"]),
                    "weight": float(row["weight"]),
                }
            )

        for candidate in candidates:
            question_id = int(candidate["id"])
            candidate["tags"] = tags_by_question.get(question_id, {})
            candidate["measured_skills"] = list(measured_by_question.get(question_id, {}).values())
            candidate["fingerprint"] = _candidate_fingerprint(candidate)
        return candidates, neighbors

    def _select_skill_for_stage(
        self,
        *,
        stage: str,
        requested: int,
        candidates: list[dict[str, Any]],
        selected_ids: set[int],
        paper_counts: Counter[str],
        method_counts: Counter[str],
        frequency_by_id: Mapping[int, Any],
    ) -> list[dict[str, Any]]:
        scored: list[tuple[tuple[float, ...], int, dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            question_id = int(candidate["id"])
            if question_id in selected_ids:
                continue
            match = candidate["skill_match"]
            metrics = frequency_by_id.get(question_id)
            gradient = practice_gradient_fit(stage, candidate.get("difficulty"))
            frequency = frequency_fit_score(metrics) if getattr(metrics, "available", False) else 0.5
            paper_key = _paper_key(candidate)
            method_key = _method_key(candidate)
            diversity = _diversity_fit(
                paper_counts[paper_key],
                method_counts[method_key] if method_key else 0,
            )
            match_score = 1.0 if match["match_kind"] == "exact" else float(match["neighbor_weight"])
            total = round(0.55 * match_score + 0.25 * float(gradient or 0.5) + 0.1 * frequency + 0.1 * diversity, 4)
            ranking = (
                0.0 if match["match_kind"] == "exact" else 1.0,
                -float(gradient or 0.5),
                -float(frequency),
                -float(diversity),
                -total,
            )
            payload = _skill_item_payload(
                candidate,
                stage,
                total,
                {
                    "skill_match": match_score,
                    "gradient": float(gradient or 0.5),
                    "frequency": float(frequency),
                    "diversity": float(diversity),
                },
                metrics,
            )
            scored.append((ranking, question_id, payload, candidate))
        scored.sort(key=lambda item: (item[0], item[1]))

        selected: list[dict[str, Any]] = []
        for _, question_id, payload, candidate in scored:
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


def _is_skill_diagnosis(profile: Mapping[str, Any]) -> bool:
    if str(profile.get("diagnosis_identity") or "") == "skill":
        return True
    if isinstance(profile.get("students"), list):
        weak_points = [
            weak
            for student in profile["students"]
            if isinstance(student, Mapping)
            for weak in student.get("weak_points", [])
            if isinstance(weak, Mapping)
        ]
    else:
        weak_points = [weak for weak in profile.get("weak_points", []) if isinstance(weak, Mapping)]
    return any(weak.get("skill_id") is not None for weak in weak_points)


def _eligible_skill_weak_points(
    profile: Mapping[str, Any],
    catalog: SkillCatalogService,
) -> list[dict[str, Any]]:
    if isinstance(profile.get("students"), list):
        raw = [
            weak
            for student in profile["students"]
            if isinstance(student, Mapping)
            for weak in student.get("weak_points", [])
            if isinstance(weak, Mapping)
        ]
    else:
        raw = [weak for weak in profile.get("weak_points", []) if isinstance(weak, Mapping)]
    result_by_id: dict[int, dict[str, Any]] = {}
    for weak in raw:
        if weak.get("eligible_for_recommendation") is False:
            continue
        try:
            stored_skill_id = int(weak.get("skill_id"))
        except (TypeError, ValueError):
            continue
        skill = catalog.get_skill(stored_skill_id)
        if skill is None or skill.get("status") != "active":
            continue
        active_id = int(skill["id"])
        normalized = {
            **dict(weak),
            "stored_skill_id": stored_skill_id,
            "skill_id": active_id,
            "skill_name": str(skill["name"]),
            "topic_id": int(skill["topic_id"]),
            "topic_name": str(skill["topic_name"]),
        }
        existing = result_by_id.get(active_id)
        if existing is None or _mastery(normalized) < _mastery(existing):
            result_by_id[active_id] = normalized
    return sorted(result_by_id.values(), key=lambda item: (_mastery(item), int(item["skill_id"])))


def _mastery(item: Mapping[str, Any]) -> float:
    try:
        return min(1.0, max(0.0, float(item.get("mastery") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _assign_skill_candidate_roles(
    candidates: list[dict[str, Any]],
    weak_points: list[dict[str, Any]],
    neighbors: Mapping[int, list[dict[str, Any]]],
    *,
    include_neighbors: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        measured = {
            int(skill["skill_id"]): skill
            for skill in candidate.get("measured_skills", [])
            if isinstance(skill, Mapping) and skill.get("skill_id") is not None
        }
        if not measured:
            continue
        matches: list[tuple[tuple[float, float, int], dict[str, Any]]] = []
        for weak in weak_points:
            target_id = int(weak["skill_id"])
            if target_id in measured:
                matched = measured[target_id]
                matches.append(
                    (
                        (0.0, _mastery(weak), target_id),
                        {
                            "match_kind": "exact",
                            "target_skill_id": target_id,
                            "matched_skill_id": target_id,
                            "target_skill_name": str(weak.get("skill_name") or ""),
                            "matched_skill_name": str(matched.get("skill_name") or ""),
                            "neighbor_kind": None,
                            "neighbor_weight": 1.0,
                            "reason": "exact measured skill / 精确技能匹配",
                        },
                    )
                )
            if not include_neighbors:
                continue
            for neighbor in neighbors.get(target_id, []):
                matched_id = int(neighbor["skill_id"])
                if matched_id not in measured:
                    continue
                matches.append(
                    (
                        (1.0, -float(neighbor["weight"]), matched_id),
                        {
                            "match_kind": "neighbor",
                            "target_skill_id": target_id,
                            "matched_skill_id": matched_id,
                            "target_skill_name": str(weak.get("skill_name") or ""),
                            "matched_skill_name": str(neighbor.get("skill_name") or ""),
                            "neighbor_kind": str(neighbor["kind"]),
                            "neighbor_weight": float(neighbor["weight"]),
                            "reason": f"related fill / 相近补入 ({neighbor['kind']})",
                        },
                    )
                )
        if matches:
            matches.sort(key=lambda item: item[0])
            result.append({**candidate, "skill_match": matches[0][1]})
    return result


def _available_neighbor_question_count(
    candidates: list[dict[str, Any]],
    weak_points: list[dict[str, Any]],
    neighbors: Mapping[int, list[dict[str, Any]]],
) -> int:
    neighbor_ids = {
        int(neighbor["skill_id"])
        for weak in weak_points
        for neighbor in neighbors.get(int(weak["skill_id"]), [])
    }
    return sum(
        1
        for candidate in candidates
        if neighbor_ids.intersection(
            int(skill["skill_id"])
            for skill in candidate.get("measured_skills", [])
            if isinstance(skill, Mapping) and skill.get("skill_id") is not None
        )
    )


def _skill_item_payload(
    candidate: Mapping[str, Any],
    stage: str,
    total_score: float,
    components: Mapping[str, float],
    metrics: Any,
) -> dict[str, Any]:
    match = candidate["skill_match"]
    warnings = [] if _difficulty(candidate.get("difficulty")) is not None else ["题目难度缺失"]
    return {
        "question_id": int(candidate["id"]),
        "stage": stage,
        "target_skill_id": int(match["target_skill_id"]),
        "matched_skill_id": int(match["matched_skill_id"]),
        "target_skill_name": str(match["target_skill_name"]),
        "matched_skill_name": str(match["matched_skill_name"]),
        "match_kind": str(match["match_kind"]),
        "neighbor_kind": match.get("neighbor_kind"),
        "reason": str(match["reason"]),
        "recommend_score": float(total_score),
        "score_components": dict(components),
        "warnings": warnings,
        "question_fingerprint": str(candidate.get("fingerprint") or ""),
        "question_text": str(candidate.get("question_text") or ""),
        "question_number": str(candidate.get("question_number") or ""),
        "difficulty": candidate.get("difficulty"),
        "source_paper": str(candidate.get("paper_title") or candidate.get("paper_source_file") or ""),
        "method_tags": list(candidate.get("tags", {}).get("method", [])),
        "model_tags": list(candidate.get("tags", {}).get("model", [])),
        "frequency": metrics.to_dict() if metrics is not None else {},
    }


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
