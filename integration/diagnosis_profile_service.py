from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.compat import open_grading_repositories
from integration.question_tag_projection_service import (
    QuestionTagProjection,
    QuestionTagProjectionService,
)


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
        *,
        grading_db: GradingRepositoryAccess | None = None,
        question_bank_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db = (
            as_grading_repositories(grading_db)
            if grading_db is not None
            else open_grading_repositories(Path(grading_db_path))
        )
        self.question_bank_db_path = Path(question_bank_db_path)
        self.question_bank_connection = question_bank_connection

    def build_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.build_tag_profiles(scope=scope, exam_scope=exam_scope)

    def build_tag_profiles(
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
        projection_by_session = self._tag_projections(session_ids)
        evidence_rows = self._projected_tag_evidence(
            student_ids=student_ids,
            session_ids=session_ids,
            projection_by_session=projection_by_session,
        )

        grouped_by_student: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in evidence_rows:
            student_id = str(row.get("student_id") or "")
            awarded = max(_number(row.get("score_awarded")), 0.0)
            full_score = max(_number(row.get("full_score")), 0.0)
            score_for_rate = min(awarded, full_score) if full_score > 0 else awarded
            tags = row.get("question_tags") if isinstance(row.get("question_tags"), Mapping) else {}
            for knowledge_point in tags.get("knowledge_point", []):
                point = str(knowledge_point or "").strip()
                if not point:
                    continue
                item = grouped_by_student[student_id].setdefault(
                    point,
                    {
                        "knowledge_key": f"knowledge_point:{point}",
                        "knowledge_point": point,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "deduction_count": 0,
                        "source_question_refs": [],
                        "actionable_reasons": [],
                        "tag_context": defaultdict(list),
                        "primary_errors": Counter(),
                        "secondary_errors": Counter(),
                    },
                )
                item["score_sum"] += score_for_rate
                item["full_score_sum"] += full_score
                if full_score > 0 and awarded < full_score - 1e-6:
                    item["deduction_count"] += 1
                reference = {
                    "session_id": int(row.get("session_id") or 0),
                    "session_name": str(row.get("session_name") or ""),
                    "question_id": str(row.get("question_id") or ""),
                    "bank_question_id": int(row.get("bank_question_id") or 0),
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
                item["actionable_reasons"] = _unique(
                    [*item["actionable_reasons"], *reasons]
                )
                for tag_type in ("sub_skill", "method", "ability", "model", "prerequisite"):
                    for tag_value in tags.get(tag_type, []):
                        text = str(tag_value or "").strip()
                        if text and text not in item["tag_context"][tag_type]:
                            item["tag_context"][tag_type].append(text)
                primary_summary = str(row.get("error_summary") or "").strip()
                if primary_summary:
                    item["primary_errors"][primary_summary] += 1
                for secondary in row.get("secondary_errors") or []:
                    if not isinstance(secondary, Mapping):
                        continue
                    secondary_summary = str(secondary.get("summary") or "").strip()
                    if secondary_summary:
                        item["secondary_errors"][secondary_summary] += 1

        student_profiles: list[dict[str, Any]] = []
        for student in students:
            student_id = str(student["id"])
            weak_points: list[dict[str, Any]] = []
            for raw_item in grouped_by_student.get(student_id, {}).values():
                item = dict(raw_item)
                score_sum = float(item.pop("score_sum"))
                full_score_sum = float(item.pop("full_score_sum"))
                tag_context = {
                    tag_type: list(values)
                    for tag_type, values in item.pop("tag_context").items()
                    if values
                }
                primary_errors = dict(sorted(item.pop("primary_errors").items()))
                secondary_errors = dict(sorted(item.pop("secondary_errors").items()))
                references = sorted(
                    item["source_question_refs"],
                    key=lambda ref: (ref["session_id"], ref["question_id"]),
                )
                weak_points.append(
                    {
                        **item,
                        "mastery": round(score_sum / full_score_sum, 4)
                        if full_score_sum > 0
                        else 1.0,
                        "score_sum": round(score_sum, 4),
                        "full_score_sum": round(full_score_sum, 4),
                        "evidence_count": len(references),
                        "exam_count": len({ref["session_id"] for ref in references}),
                        "source_question_refs": references,
                        "tag_context": tag_context,
                        "error_counts": {
                            "primary": primary_errors,
                            "secondary": secondary_errors,
                        },
                    }
                )
            weak_points.sort(key=lambda item: (item["mastery"], item["knowledge_point"]))
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

        coverage_missing: dict[str, str] = {}
        covered_items = 0
        total_items = 0
        for projection in projection_by_session.values():
            covered_items += projection.covered_items
            total_items += projection.total_items
            coverage_missing.update(projection.missing_items)
        if coverage_missing:
            warnings.append(
                f"知识图谱仅覆盖 {covered_items}/{total_items} 个评分题；缺失题目已列出。"
            )
        if not any(item["weak_points"] for item in student_profiles):
            warnings.append("所选范围内没有已关联且带知识点标签的诊断证据。")

        normalized_scope = {
            "mode": str(scope.get("mode") or "student"),
            "student_ids": [item["student_id"] for item in student_profiles],
        }
        if scope.get("class_id") or scope.get("class_name"):
            normalized_scope["class_id"] = str(
                scope.get("class_id") or scope.get("class_name")
            )
        return {
            "scope": normalized_scope,
            "exam_scope": {
                "mode": str(exam_scope.get("mode") or "current"),
                "session_ids": session_ids,
                "sessions": [
                    {
                        "session_id": int(item["id"]),
                        "session_name": str(item.get("session_name") or ""),
                    }
                    for item in sessions
                ],
            },
            "students": student_profiles,
            "coverage": {
                "covered_items": covered_items,
                "total_items": total_items,
                "missing_items": coverage_missing,
            },
            "confirmed_concept_ids": [],
            "suggested_terms": [],
            "unmapped_terms": [],
            "warnings": _unique(warnings),
            "diagnosis_identity": "question_tag",
        }

    def mastery_session_times(
        self,
        *,
        exam_scope: Mapping[str, Any],
    ) -> dict[str, str]:
        """Expose evidence time only to the internal mastery adapter."""

        sessions = self._resolve_sessions(exam_scope, [])
        return {
            str(int(item["id"])): str(item.get("created_at") or "")
            for item in sessions
        }

    def tag_evidence(
        self,
        *,
        knowledge_point: str,
        student_ids: list[str] | tuple[str, ...] = (),
        session_ids: list[int] | tuple[int, ...] = (),
    ) -> list[dict[str, Any]]:
        target = str(knowledge_point or "").strip()
        if not target:
            raise ValueError("knowledge_point is required")
        selected_sessions = _int_list(session_ids)
        if not selected_sessions:
            selected_sessions = [
                int(item["id"])
                for item in self.db.list_grading_sessions()
                if not item.get("is_deleted")
            ]
        rows = self._projected_tag_evidence(
            student_ids=_text_list(student_ids),
            session_ids=selected_sessions,
            projection_by_session=self._tag_projections(selected_sessions),
        )
        return [
            row
            for row in rows
            if target in row.get("question_tags", {}).get("knowledge_point", [])
        ]

    def _tag_projections(
        self,
        session_ids: Iterable[int],
    ) -> dict[int, QuestionTagProjection]:
        service = QuestionTagProjectionService(
            self.question_bank_db_path,
            external_connection=self.question_bank_connection,
        )
        projections: dict[int, QuestionTagProjection] = {}
        for session_id in session_ids:
            rubric = self.db._load_session_rubric(int(session_id))
            projections[int(session_id)] = service.project_session(
                grading_session_id=int(session_id),
                rubric=rubric,
            )
        return projections

    def _projected_tag_evidence(
        self,
        *,
        student_ids: Iterable[str],
        session_ids: Iterable[int],
        projection_by_session: Mapping[int, QuestionTagProjection],
    ) -> list[dict[str, Any]]:
        projected_by_item = {
            (session_id, item.item_ref): item
            for session_id, projection in projection_by_session.items()
            for item in projection.items
        }
        rows = self.db.get_active_assessment_evidence(
            student_ids=tuple(student_ids),
            session_ids=tuple(session_ids),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            key = (
                int(row.get("session_id") or 0),
                str(row.get("question_id") or ""),
            )
            projected = projected_by_item.get(key)
            if projected is None or not projected.is_graph_eligible:
                continue
            enriched = dict(row)
            enriched["bank_question_id"] = projected.bank_question_id
            enriched["question_tags"] = {
                tag_type: list(values)
                for tag_type, values in projected.tags.items()
            }
            result.append(enriched)
        return sorted(
            result,
            key=lambda row: (
                int(row.get("session_id") or 0),
                int(row.get("student_id") or 0),
                str(row.get("question_id") or ""),
                int(row.get("result_id") or 0),
            ),
        )

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
