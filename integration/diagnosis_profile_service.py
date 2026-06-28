from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from db_manager import DBManager
from integration.knowledge_term_identity import build_grading_knowledge_term
from question_bank.models.knowledge_alignment import AlignmentStatus
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.skill_link_service import SkillLinkService


GENERIC_ERROR_REASONS = {
    "未作答",
    "未选择正确答案",
    "答案不等价",
    "答案不正确",
}


class DiagnosisProfileService:
    def __init__(
        self,
        grading_db_path: str | Path,
        question_bank_db_path: str | Path,
    ) -> None:
        self.db = DBManager(Path(grading_db_path))
        self.question_bank_db_path = Path(question_bank_db_path)
        self.alignment = ConceptAlignmentService(self.question_bank_db_path)
        self.skill_links = SkillLinkService(self.question_bank_db_path)

    def build_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        read_mode = self._skill_read_mode()
        if read_mode == "skill":
            return self._build_skill_profiles(scope=scope, exam_scope=exam_scope)
        if read_mode == "shadow":
            skill_profile = self._build_skill_profiles(scope=scope, exam_scope=exam_scope)
            legacy_profile = self._build_legacy_profiles(scope=scope, exam_scope=exam_scope)
            skill_profile["legacy_comparison"] = {
                "student_count": len(legacy_profile.get("students") or []),
                "weak_point_count": sum(
                    len(item.get("weak_points") or [])
                    for item in legacy_profile.get("students") or []
                ),
                "confirmed_concept_count": len(legacy_profile.get("confirmed_concept_ids") or []),
            }
            return skill_profile
        return self._build_legacy_profiles(scope=scope, exam_scope=exam_scope)

    def build_skill_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._build_skill_profiles(scope=scope, exam_scope=exam_scope)

    def skill_evidence(
        self,
        *,
        skill_id: int,
        student_ids: list[str] | tuple[str, ...] = (),
        session_ids: list[int] | tuple[int, ...] = (),
    ) -> list[dict[str, Any]]:
        normalized_skill_id = int(skill_id)
        if normalized_skill_id <= 0:
            raise ValueError("skill_id must be positive")
        selected_sessions = _int_list(session_ids)
        if not selected_sessions:
            selected_sessions = [
                int(item["id"])
                for item in self.db.list_grading_sessions()
                if not item.get("is_deleted")
            ]
        if not selected_sessions:
            return []
        source_keys = {
            (
                int(link.get("grading_session_id") or 0),
                str(link.get("source_question_id") or ""),
            )
            for link in self.skill_links.assessment_links_for_sessions(
                [str(value) for value in selected_sessions]
            )
            if str(link.get("role")) == "measured"
            and int(link.get("skill_id") or 0) == normalized_skill_id
        }
        if not source_keys:
            return []
        rows = self.db.get_active_assessment_evidence(
            student_ids=_text_list(student_ids),
            session_ids=selected_sessions,
        )
        return sorted(
            [
                row
                for row in rows
                if (
                    int(row.get("session_id") or 0),
                    str(row.get("question_id") or ""),
                )
                in source_keys
            ],
            key=lambda row: (
                int(row.get("session_id") or 0),
                int(row.get("student_id") or 0),
                str(row.get("question_id") or ""),
                int(row.get("result_id") or 0),
            ),
        )

    def _build_legacy_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        migration_report = self.alignment.migrate_legacy_grading_mappings()
        if migration_report.conflicts:
            warnings.append(
                "以下旧知识点确认与当前确认冲突，已保留当前教师选择："
                + "、".join(migration_report.conflicts)
            )
        sessions = self._resolve_sessions(exam_scope, warnings)
        students = self._resolve_students(scope, warnings)
        session_ids = [int(item["id"]) for item in sessions]
        score_rates = self._score_rates(students, session_ids)
        suggested_terms: set[str] = set()
        unmapped_terms: set[str] = set()
        confirmed_concept_ids: set[int] = set()
        student_profiles: list[dict[str, Any]] = []

        for student in students:
            student_id = int(student["id"])
            weak_rows = self.db.get_active_global_weak_points(
                student_id=student_id,
                session_ids=session_ids,
            )
            weak_points: list[dict[str, Any]] = []
            for row in weak_rows:
                term = build_grading_knowledge_term(
                    row.get("knowledge_id"),
                    row.get("knowledge_label"),
                )
                source_term = term.source_value
                resolution = self.alignment.resolve_for_training(
                    "grading_weak_point",
                    source_term,
                )
                references = self._source_question_refs(
                    student_id=student_id,
                    knowledge_id=str(row.get("knowledge_id") or source_term),
                    session_ids=session_ids,
                )
                score_sum, full_score_sum = _score_totals(row, references)
                mastery = round(score_sum / full_score_sum, 4) if full_score_sum > 0 else 1.0
                actionable_reasons = _actionable_reasons(row.get("sample_reasons"))
                concept = resolution.concept
                eligible = resolution.eligible_for_recommendation
                if eligible and concept is not None:
                    confirmed_concept_ids.add(concept.id)
                elif resolution.status is AlignmentStatus.SUGGESTED:
                    suggested_terms.add(source_term)
                else:
                    unmapped_terms.add(source_term)
                weak_points.append(
                    {
                        "source_term": source_term,
                        "source_display": term.display_value,
                        "source_knowledge_id": str(row.get("knowledge_id") or ""),
                        "concept_id": concept.id if concept is not None else None,
                        "concept_name": concept.name if concept is not None else "",
                        "mapping_status": resolution.status.value,
                        "mapping_confidence": resolution.confidence,
                        "eligible_for_recommendation": eligible,
                        "sub_skill_tags": list(resolution.sub_skill_tags) if hasattr(resolution, "sub_skill_tags") and resolution.sub_skill_tags else [],
                        "mastery": mastery,
                        "score_sum": round(score_sum, 4),
                        "full_score_sum": round(full_score_sum, 4),
                        "evidence_count": len(references) or int(row.get("item_count") or 0),
                        "exam_count": len({item["session_id"] for item in references})
                        or int(row.get("exam_count") or 0),
                        "deduction_count": int(row.get("deduction_count") or 0),
                        "actionable_reasons": actionable_reasons,
                        "source_question_refs": references,
                    }
                )
            weak_points.sort(
                key=lambda item: (
                    item["mastery"],
                    0 if item["eligible_for_recommendation"] else 1,
                    item["source_term"],
                )
            )
            student_profiles.append(
                {
                    "student_id": str(student_id),
                    "student_code": str(student.get("student_code") or ""),
                    "student_name": str(student.get("name") or ""),
                    "class_id": str(student.get("class_name") or ""),
                    "score_rate": score_rates.get(str(student_id)),
                    "weak_points": weak_points,
                }
            )

        if student_profiles and not confirmed_concept_ids:
            warnings.append("所选学生的薄弱知识点尚无已确认映射，暂不能生成可靠推荐。")
        if not any(item["weak_points"] for item in student_profiles):
            warnings.append("所选范围内没有可用的知识点诊断证据。")

        normalized_scope = {
            "mode": str(scope.get("mode") or "student"),
            "student_ids": [item["student_id"] for item in student_profiles],
        }
        if scope.get("class_id") or scope.get("class_name"):
            normalized_scope["class_id"] = str(scope.get("class_id") or scope.get("class_name"))
        return {
            "scope": normalized_scope,
            "exam_scope": {
                "mode": str(exam_scope.get("mode") or "current"),
                "session_ids": session_ids,
                "sessions": [
                    {"session_id": int(item["id"]), "session_name": str(item.get("session_name") or "")}
                    for item in sessions
                ],
            },
            "students": student_profiles,
            "confirmed_concept_ids": sorted(confirmed_concept_ids),
            "suggested_terms": sorted(suggested_terms),
            "unmapped_terms": sorted(unmapped_terms),
            "warnings": _unique(warnings),
        }

    def _skill_read_mode(self) -> str:
        initialize_database(self.question_bank_db_path)
        with connect(self.question_bank_db_path) as conn:
            row = conn.execute(
                """
                SELECT value FROM skill_system_settings
                WHERE key = 'recommendation_read_mode'
                """
            ).fetchone()
        value = str(row["value"] if row is not None else "legacy").strip().casefold()
        return value if value in {"legacy", "shadow", "skill"} else "legacy"

    def _build_skill_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        sessions = self._resolve_sessions(exam_scope, warnings)
        students = self._resolve_students(scope, warnings)
        session_ids = [int(item["id"]) for item in sessions]
        student_ids = [str(item["id"]) for item in students]
        score_rates = self._score_rates(students, session_ids)
        evidence_rows = self.db.get_active_assessment_evidence(
            student_ids=student_ids,
            session_ids=session_ids,
        )
        measured_by_item: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for link in self.skill_links.assessment_links_for_sessions(
            [str(value) for value in session_ids]
        ):
            if str(link.get("role")) != "measured":
                continue
            measured_by_item[
                (int(link.get("grading_session_id") or 0), str(link.get("source_question_id") or ""))
            ].append(link)

        grouped_by_student: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        confirmed_skill_ids: set[int] = set()
        for row in evidence_rows:
            item_key = (int(row.get("session_id") or 0), str(row.get("question_id") or ""))
            links = measured_by_item.get(item_key, [])
            if not links:
                continue
            student_id = str(row.get("student_id") or "")
            awarded = max(_number(row.get("score_awarded")), 0.0)
            full_score = max(_number(row.get("full_score")), 0.0)
            score_for_rate = min(awarded, full_score) if full_score > 0 else awarded
            for link in links:
                skill_id = int(link["skill_id"])
                confirmed_skill_ids.add(skill_id)
                item = grouped_by_student[student_id].setdefault(
                    skill_id,
                    {
                        "skill_id": skill_id,
                        "skill_name": str(link.get("skill_name") or ""),
                        "topic_id": int(link.get("topic_id") or 0),
                        "topic_name": str(link.get("topic_name") or ""),
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "deduction_count": 0,
                        "source_question_refs": [],
                        "actionable_reasons": [],
                        "error_types": [],
                    },
                )
                item["score_sum"] += score_for_rate
                item["full_score_sum"] += full_score
                if row.get("is_deducted"):
                    item["deduction_count"] += 1
                reference = {
                    "session_id": int(row.get("session_id") or 0),
                    "question_id": str(row.get("question_id") or ""),
                    "score_awarded": awarded,
                    "full_score": full_score,
                    "score_rate": round(awarded / full_score, 4) if full_score > 0 else None,
                }
                if reference not in item["source_question_refs"]:
                    item["source_question_refs"].append(reference)
                reasons = _actionable_reasons(
                    ";".join(
                        value
                        for value in (
                            str(row.get("deduction_reason") or "").strip(),
                            str(row.get("error_summary") or "").strip(),
                        )
                        if value
                    )
                )
                item["actionable_reasons"] = _unique([*item["actionable_reasons"], *reasons])
                error_type = str(row.get("error_category") or "").strip()
                if error_type:
                    item["error_types"] = _unique([*item["error_types"], error_type])

        student_profiles: list[dict[str, Any]] = []
        for student in students:
            student_id = str(student["id"])
            weak_points: list[dict[str, Any]] = []
            for item in grouped_by_student.get(student_id, {}).values():
                full_score_sum = float(item.pop("full_score_sum"))
                score_sum = float(item.pop("score_sum"))
                references = sorted(
                    item["source_question_refs"],
                    key=lambda ref: (ref["session_id"], ref["question_id"]),
                )
                weak_points.append(
                    {
                        **item,
                        "source_term": item["skill_name"],
                        "eligible_for_recommendation": True,
                        "mastery": round(score_sum / full_score_sum, 4) if full_score_sum > 0 else 1.0,
                        "score_sum": round(score_sum, 4),
                        "full_score_sum": round(full_score_sum, 4),
                        "evidence_count": len(references),
                        "exam_count": len({ref["session_id"] for ref in references}),
                        "source_question_refs": references,
                    }
                )
            weak_points.sort(key=lambda item: (item["mastery"], item["skill_name"]))
            student_profiles.append(
                {
                    "student_id": student_id,
                    "student_code": str(student.get("student_code") or ""),
                    "student_name": str(student.get("name") or ""),
                    "class_id": str(student.get("class_name") or ""),
                    "score_rate": score_rates.get(student_id),
                    "weak_points": weak_points,
                }
            )

        unresolved_count = self._open_assessment_conflict_count(session_ids)
        if unresolved_count:
            warnings.append(f"有 {unresolved_count} 个评分题技能名称尚未确定，已从训练推荐中排除。")
        if not any(item["weak_points"] for item in student_profiles):
            warnings.append("所选范围内没有已绑定具体技能的诊断证据。")
        normalized_scope = {
            "mode": str(scope.get("mode") or "student"),
            "student_ids": [item["student_id"] for item in student_profiles],
        }
        if scope.get("class_id") or scope.get("class_name"):
            normalized_scope["class_id"] = str(scope.get("class_id") or scope.get("class_name"))
        return {
            "scope": normalized_scope,
            "exam_scope": {
                "mode": str(exam_scope.get("mode") or "current"),
                "session_ids": session_ids,
                "sessions": [
                    {"session_id": int(item["id"]), "session_name": str(item.get("session_name") or "")}
                    for item in sessions
                ],
            },
            "students": student_profiles,
            "confirmed_skill_ids": sorted(confirmed_skill_ids),
            "confirmed_concept_ids": [],
            "suggested_terms": [],
            "unmapped_terms": [],
            "unresolved_count": unresolved_count,
            "warnings": _unique(warnings),
            "diagnosis_identity": "skill",
        }

    def _open_assessment_conflict_count(self, session_ids: list[int]) -> int:
        if not session_ids:
            return 0
        resolved = {
            (
                int(link["grading_session_id"]),
                str(link["source_question_id"]),
            )
            for link in self.skill_links.assessment_links_for_sessions(
                [str(value) for value in session_ids]
            )
            if str(link.get("role")) == "measured"
            and str(link.get("status")) == "resolved"
        }
        initialize_database(self.question_bank_db_path)
        with connect(self.question_bank_db_path) as conn:
            refs = {
                str(row["source_ref"])
                for row in conn.execute(
                    """
                    SELECT DISTINCT source_ref FROM skill_resolution_conflicts
                    WHERE source_type = 'assessment_item' AND state = 'open'
                    """
                ).fetchall()
            }
        prefixes = tuple(f"{session_id}:" for session_id in session_ids)
        blocking: set[tuple[int, str]] = set()
        for source_ref in refs:
            if not source_ref.startswith(prefixes):
                continue
            session_text, item_ref = source_ref.split(":", 1)
            key = (int(session_text), item_ref)
            if key not in resolved:
                blocking.add(key)
        return len(blocking)

    def _resolve_sessions(
        self,
        exam_scope: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        active = {
            int(item["id"]): item
            for item in self.db.list_grading_sessions()
            if not item.get("is_deleted")
        }
        mode = str(exam_scope.get("mode") or "current")
        requested = _int_list(exam_scope.get("session_ids"))
        if mode == "cross_exam":
            ids = sorted(active)
        elif mode == "manual":
            ids = [session_id for session_id in requested if session_id in active]
        elif mode == "current":
            if requested:
                ids = [requested[0]] if requested[0] in active else []
            else:
                ids = [max(active)] if active else []
        else:
            raise ValueError(f"unsupported exam scope mode: {mode}")
        missing = [session_id for session_id in requested if session_id not in active]
        if missing:
            warnings.append(f"已忽略不存在或已删除的考试：{', '.join(map(str, missing))}")
        if not ids:
            warnings.append("未选择可用考试。")
        return [active[session_id] for session_id in ids]

    def _resolve_students(
        self,
        scope: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        all_students = self.db.list_students()
        by_id = {str(item["id"]): item for item in all_students}
        mode = str(scope.get("mode") or "student")
        requested = _text_list(scope.get("student_ids"))
        if mode in {"student", "selected"}:
            selected = [by_id[student_id] for student_id in requested if student_id in by_id]
            missing = [student_id for student_id in requested if student_id not in by_id]
            if missing:
                warnings.append(f"已忽略不存在的学生：{', '.join(missing)}")
        elif mode == "class":
            class_id = str(scope.get("class_id") or scope.get("class_name") or "").strip()
            selected = [
                item
                for item in all_students
                if not class_id or str(item.get("class_name") or "") == class_id
            ]
            if requested:
                requested_set = set(requested)
                selected = [item for item in selected if str(item["id"]) in requested_set]
        else:
            raise ValueError(f"unsupported student scope mode: {mode}")
        if not selected:
            warnings.append("未选择可用学生。")
        return selected

    def _score_rates(
        self,
        students: list[dict[str, Any]],
        session_ids: list[int],
    ) -> dict[str, float | None]:
        student_id_by_code = {
            str(item.get("student_code") or ""): str(item["id"])
            for item in students
        }
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for session_id in session_ids:
            for result in self.db.get_session_results(session_id):
                student_id = student_id_by_code.get(str(result.get("student_code") or ""))
                if student_id is None:
                    continue
                totals[student_id][0] += _number(result.get("student_score"))
                totals[student_id][1] += _number(result.get("total_score"))
        fallback = {
            str(item["student_id"]): _number(item.get("avg_score_rate")) / 100
            for item in self.db.get_active_student_score_rates()
        }
        return {
            str(student["id"]): (
                round(totals[str(student["id"])][0] / totals[str(student["id"])][1], 4)
                if totals[str(student["id"])][1] > 0
                else fallback.get(str(student["id"]))
            )
            for student in students
        }

    def _source_question_refs(
        self,
        *,
        student_id: int,
        knowledge_id: str,
        session_ids: list[int],
    ) -> list[dict[str, Any]]:
        allowed_sessions = set(session_ids)
        references: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for item in self.db.get_active_items_for_knowledge(student_id, knowledge_id):
            session_id = int(item.get("session_id") or 0)
            question_id = str(item.get("question_id") or "")
            identity = (session_id, question_id)
            if session_id not in allowed_sessions or identity in seen:
                continue
            seen.add(identity)
            full_score = _number(item.get("max_score"))
            awarded = _number(item.get("score_awarded"))
            references.append(
                {
                    "session_id": session_id,
                    "question_id": question_id,
                    "score_awarded": awarded,
                    "full_score": full_score,
                    "score_rate": round(awarded / full_score, 4) if full_score > 0 else None,
                }
            )
        return sorted(references, key=lambda item: (item["session_id"], item["question_id"]))


def _score_totals(
    weak_row: Mapping[str, Any],
    references: list[dict[str, Any]],
) -> tuple[float, float]:
    if references:
        score_sum = sum(
            min(max(_number(item.get("score_awarded")), 0.0), _number(item.get("full_score")))
            if _number(item.get("full_score")) > 0
            else max(_number(item.get("score_awarded")), 0.0)
            for item in references
        )
        full_score_sum = sum(max(_number(item.get("full_score")), 0.0) for item in references)
        if full_score_sum > 0:
            return score_sum, full_score_sum
    return _number(weak_row.get("score_sum")), _number(weak_row.get("full_score_sum"))


def _source_term(row: Mapping[str, Any]) -> str:
    return build_grading_knowledge_term(
        row.get("knowledge_id"),
        row.get("knowledge_label"),
    ).source_value


def _actionable_reasons(value: object) -> list[str]:
    text = str(value or "").replace("；", ";")
    return [
        item
        for item in _unique(part.strip() for part in text.split(";"))
        if item and item not in GENERIC_ERROR_REASONS
    ]


def _int_list(value: object) -> list[int]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    result: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _text_list(value: object) -> list[str]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    return _unique(str(item or "").strip() for item in values if str(item or "").strip())


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["DiagnosisProfileService"]
