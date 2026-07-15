from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.review.service import ReviewApplicationService
from db_manager import DBManager
from path_manager import resolve_stored_file_path


@dataclass(frozen=True, slots=True)
class QuestionAnalysisRow:
    class_name: str
    question_id: str
    max_score: float | None
    score_rate: float | None
    average_score: float | None
    deduction_count: int
    attempt_count: int
    metric_status: Literal["ready", "missing_max_score", "no_attempts"]


@dataclass(frozen=True, slots=True)
class StudentAnalysisRow:
    result_id: int
    detail_id: int
    student_id: int
    student_code: str | None
    student_name: str
    class_name: str
    question_id: str
    score_awarded: float
    max_score: float | None
    deduction_amount: float | None
    deduction_reason: str | None
    needs_review: bool


class SessionAnalysisService:
    def __init__(
        self,
        db: DBManager,
        review_service: ReviewApplicationService | None = None,
    ) -> None:
        self.db = db
        self.review_service = review_service or ReviewApplicationService(db)

    def list_questions(
        self,
        session_id: int,
        class_name: str | None = None,
    ) -> list[QuestionAnalysisRow]:
        """Return legacy-equivalent class rows or merged all-class rows."""
        analysis = self._build_legacy_question_analysis(session_id)
        if class_name is None:
            source_rows = merge_question_analysis_rows(analysis["rows"])
        else:
            source_rows = [
                row for row in analysis["rows"] if str(row.get("班级") or "") == class_name
            ]
        return [_public_question_row(row) for row in source_rows]

    def list_students(
        self,
        session_id: int,
        question_id: str,
        class_name: str | None = None,
    ) -> list[StudentAnalysisRow]:
        """Return direct details plus legacy inferred child full-score rows."""
        requested_question_id = str(question_id or "").strip()
        if not requested_question_id:
            return []

        session = self.db.get_grading_session(int(session_id))
        score_map, _type_map = load_session_score_type_maps(
            session,
            data_root=_data_root(self.db),
        )
        review_by_detail_id = {
            item.detail_id: item.needs_review
            for item in self.review_service.list_items(
                int(session_id),
                session or {},
            )
        }
        selected_max_score = score_map.get(requested_question_id)
        selected_parent_id = question_parent_id(requested_question_id)
        rows: list[StudentAnalysisRow] = []

        for result in self.db.get_session_results(int(session_id)):
            result_class_name = str(result.get("class_name") or "未分班")
            if class_name is not None and result_class_name != class_name:
                continue
            result_id = int(result.get("result_id") or 0)
            details = self.db.get_result_details(result_id)
            direct_details: list[dict[str, Any]] = []
            for detail in details:
                raw_question_id = str(detail.get("question_id") or "").strip()
                canonical_question_id = canonical_question_id_for_score(
                    raw_question_id,
                    score_map,
                )
                if canonical_question_id != requested_question_id:
                    continue
                direct_details.append(detail)
                max_score = score_map.get(raw_question_id)
                if max_score is None:
                    max_score = score_map.get(canonical_question_id)
                rows.append(
                    _student_row(
                        result=result,
                        detail=detail,
                        class_name=result_class_name,
                        question_id=canonical_question_id,
                        max_score=max_score,
                        needs_review=review_by_detail_id.get(
                            int(detail.get("detail_id") or 0),
                            False,
                        ),
                    )
                )

            if direct_details or selected_parent_id is None or selected_max_score is None:
                continue
            parent_detail = next(
                (
                    detail
                    for detail in details
                    if str(detail.get("question_id") or "").strip() == selected_parent_id
                ),
                None,
            )
            parent_max_score = score_map.get(selected_parent_id)
            if parent_detail is None or parent_max_score is None:
                continue
            parent_score = float(parent_detail.get("score_awarded") or 0)
            if parent_score < float(parent_max_score) - 1e-6:
                continue
            rows.append(
                _student_row(
                    result=result,
                    detail=parent_detail,
                    class_name=result_class_name,
                    question_id=requested_question_id,
                    max_score=float(selected_max_score),
                    score_awarded=float(selected_max_score),
                    needs_review=review_by_detail_id.get(
                        int(parent_detail.get("detail_id") or 0),
                        False,
                    ),
                )
            )

        return sorted(
            rows,
            key=lambda item: (
                item.class_name,
                item.student_code or "",
                item.student_name,
                question_sort_key(item.question_id),
                item.detail_id,
            ),
        )

    def _build_legacy_question_analysis(self, session_id: int) -> dict[str, Any]:
        session = self.db.get_grading_session(int(session_id))
        score_map, _type_map = load_session_score_type_maps(
            session,
            data_root=_data_root(self.db),
        )
        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        for result in self.db.get_session_results(int(session_id)):
            class_name = str(result.get("class_name") or "未分班")
            result_id = int(result.get("result_id") or 0)
            details = self.db.get_result_details(result_id)
            for detail in normalize_question_analysis_details(details, score_map):
                question_id = str(detail.get("question_id") or "").strip()
                if not question_id:
                    continue
                max_score = float(score_map.get(question_id) or 0)
                score_awarded = float(detail.get("score_awarded") or 0)
                if max_score <= 0:
                    max_score = max(score_awarded, 0.0)
                elif score_awarded > max_score:
                    score_awarded = max_score
                key = (class_name, question_id)
                bucket = buckets.setdefault(
                    key,
                    {
                        "班级": class_name,
                        "题号": question_id,
                        "满分": max_score,
                        "score_sum": 0.0,
                        "full_sum": 0.0,
                        "attempt_count": 0,
                        "wrong_items": [],
                        "correct_items": [],
                    },
                )
                bucket["score_sum"] += score_awarded
                bucket["full_sum"] += max_score
                bucket["attempt_count"] += 1
                bucket["满分"] = max(bucket["满分"], max_score)
                deduction = max_score - score_awarded
                reason = str(detail.get("deduction_reason") or "").strip()
                item = {
                    **result,
                    **detail,
                    "session_id": int(session_id),
                    "max_score": max_score,
                }
                if deduction > 0.01:
                    item["deduction_amount"] = round(deduction, 2)
                    item["deduction_reason"] = reason
                    bucket["wrong_items"].append(item)
                else:
                    bucket["correct_items"].append(item)

        rows: list[dict[str, Any]] = []
        for bucket in buckets.values():
            full_sum = float(bucket.get("full_sum") or 0)
            score_sum = float(bucket.get("score_sum") or 0)
            wrong_items = sorted(
                bucket["wrong_items"],
                key=lambda item: (
                    -float(item.get("deduction_amount") or 0),
                    str(item.get("student_name") or ""),
                ),
            )
            wrong_text = "；".join(
                f"{item.get('student_name')}(-{float(item.get('deduction_amount') or 0):g})"
                for item in wrong_items
            )
            correct_items = sorted(
                bucket.get("correct_items", []),
                key=lambda item: str(item.get("student_name") or ""),
            )
            attempt_count = int(bucket.get("attempt_count") or 0)
            rows.append(
                {
                    "班级": bucket["班级"],
                    "题号": bucket["题号"],
                    "满分": float(bucket["满分"]),
                    "班级得分率": round(score_sum / full_sum * 100, 2) if full_sum > 0 else 0.0,
                    "平均得分": round(score_sum / max(1, attempt_count), 2),
                    "失分人数": len(wrong_items),
                    "失分学生与扣分": wrong_text or "无",
                    "_wrong_items": wrong_items,
                    "_correct_items": correct_items,
                    "_score_sum": score_sum,
                    "_full_sum": full_sum,
                    "_attempt_count": attempt_count,
                }
            )
        rows.sort(
            key=lambda item: (
                str(item["班级"]),
                question_sort_key(str(item["题号"])),
            )
        )
        classes = sorted({str(row["班级"]) for row in rows})
        return {"rows": rows, "classes": classes}


def build_legacy_question_analysis(db: DBManager, session_id: int) -> dict[str, Any]:
    """Compatibility adapter for the Streamlit analysis view."""
    return SessionAnalysisService(db)._build_legacy_question_analysis(session_id)


def load_session_score_type_maps(
    session: dict[str, Any] | None,
    *,
    data_root: Path | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    if not session:
        return {}, {}
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        data_root=data_root,
    )
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8")) if rubric_path.exists() else {}
    except Exception:
        rubric = {}
    score_map: dict[str, float] = {}
    type_map: dict[str, str] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return score_map, type_map
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        question_type = str(question.get("question_type") or "").strip()
        if question_id:
            score_map[question_id] = _to_float(question.get("max_score"), 0.0)
            type_map[question_id] = question_type
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or "").strip()
            if part_id:
                score_map[part_id] = _to_float(part.get("part_score"), 0.0)
                type_map[part_id] = question_type
    return score_map, type_map


def merge_question_analysis_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = str(row.get("题号") or "").strip()
        if not question_id:
            continue
        bucket = merged.setdefault(
            question_id,
            {
                "班级": "全部班级",
                "题号": question_id,
                "满分": float(row.get("满分") or 0),
                "_score_sum": 0.0,
                "_full_sum": 0.0,
                "_attempt_count": 0,
                "_wrong_items": [],
                "_correct_items": [],
            },
        )
        bucket["满分"] = max(
            float(bucket.get("满分") or 0),
            float(row.get("满分") or 0),
        )
        bucket["_score_sum"] += float(row.get("_score_sum") or 0)
        bucket["_full_sum"] += float(row.get("_full_sum") or 0)
        bucket["_attempt_count"] += int(row.get("_attempt_count") or 0)
        bucket["_wrong_items"].extend(row.get("_wrong_items") or [])
        bucket["_correct_items"].extend(row.get("_correct_items") or [])

    merged_rows: list[dict[str, Any]] = []
    for bucket in merged.values():
        wrong_items = sorted(
            bucket["_wrong_items"],
            key=lambda item: (
                str(item.get("class_name") or ""),
                -float(item.get("deduction_amount") or 0),
                str(item.get("student_name") or ""),
            ),
        )
        wrong_text = "、".join(
            f"{item.get('student_name')}(-{float(item.get('deduction_amount') or 0):g})"
            for item in wrong_items
        )
        full_sum = float(bucket.get("_full_sum") or 0)
        score_sum = float(bucket.get("_score_sum") or 0)
        attempt_count = max(1, int(bucket.get("_attempt_count") or 0))
        correct_items = sorted(
            bucket.get("_correct_items", []),
            key=lambda item: (
                str(item.get("class_name") or ""),
                str(item.get("student_name") or ""),
            ),
        )
        merged_rows.append(
            {
                "班级": "全部班级",
                "题号": bucket["题号"],
                "满分": float(bucket["满分"]),
                "班级得分率": round(score_sum / full_sum * 100, 2) if full_sum > 0 else 0.0,
                "平均得分": round(score_sum / attempt_count, 2),
                "失分人数": len(wrong_items),
                "失分学生与扣分": wrong_text or "无",
                "_wrong_items": wrong_items,
                "_correct_items": correct_items,
                "_score_sum": score_sum,
                "_full_sum": full_sum,
                "_attempt_count": attempt_count,
            }
        )
    return sorted(
        merged_rows,
        key=lambda item: (
            float(item.get("班级得分率") or 0),
            question_sort_key(str(item.get("题号") or "")),
        ),
    )


def normalize_question_analysis_details(
    details: list[dict[str, Any]],
    score_map: dict[str, float],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for detail in details:
        raw_question_id = str(detail.get("question_id") or "").strip()
        if not raw_question_id:
            continue
        question_id = canonical_question_id_for_score(raw_question_id, score_map)
        if question_id not in merged:
            item = dict(detail)
            item["question_id"] = question_id
            item["score_awarded"] = 0.0
            item["_source_question_ids"] = []
            item["_deduction_reasons"] = []
            merged[question_id] = item
            order.append(question_id)
        item = merged[question_id]
        item["score_awarded"] = float(item.get("score_awarded") or 0) + float(
            detail.get("score_awarded") or 0
        )
        item["_source_question_ids"].append(raw_question_id)
        reason = str(detail.get("deduction_reason") or "").strip()
        if reason:
            item["_deduction_reasons"].append(reason)

    normalized: list[dict[str, Any]] = []
    for question_id in order:
        item = merged[question_id]
        full_score = float(score_map.get(question_id) or 0)
        if full_score > 0 and float(item.get("score_awarded") or 0) > full_score:
            item["score_awarded"] = full_score
        reasons: list[str] = []
        seen: set[str] = set()
        for reason in item.pop("_deduction_reasons", []):
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)
        if reasons:
            item["deduction_reason"] = "；".join(reasons)
        normalized.append(item)
    return normalized


def canonical_question_id_for_score(question_id: str, score_map: dict[str, float]) -> str:
    normalized = question_id.strip()
    if normalized in score_map:
        return normalized
    parent_id = question_parent_id(normalized)
    if parent_id and parent_id in score_map:
        return parent_id
    return normalized


def question_parent_id(question_id: str) -> str | None:
    match = re.match(r"^(Q\d+)(?:\(|（|-)", question_id.strip())
    return match.group(1) if match else None


def question_sort_key(question_id: str) -> tuple[int, str]:
    match = re.search(r"\d+", question_id)
    return (int(match.group()) if match else 9999, question_id)


def _student_row(
    *,
    result: dict[str, Any],
    detail: dict[str, Any],
    class_name: str,
    question_id: str,
    max_score: float | None,
    needs_review: bool,
    score_awarded: float | None = None,
) -> StudentAnalysisRow:
    awarded = (
        float(detail.get("score_awarded") or 0)
        if score_awarded is None
        else score_awarded
    )
    deduction: float | None
    if max_score is None:
        deduction = None
    else:
        raw_deduction = float(max_score) - awarded
        deduction = round(raw_deduction, 2) if raw_deduction > 0.01 else 0.0
    reason = str(detail.get("deduction_reason") or "").strip() or None
    return StudentAnalysisRow(
        result_id=int(result.get("result_id") or 0),
        detail_id=int(detail.get("detail_id") or 0),
        student_id=int(result.get("student_id") or 0),
        student_code=_optional_text(result.get("student_code")),
        student_name=str(result.get("student_name") or result.get("ocr_name") or ""),
        class_name=class_name,
        question_id=question_id,
        score_awarded=awarded,
        max_score=float(max_score) if max_score is not None else None,
        deduction_amount=deduction,
        deduction_reason=reason,
        needs_review=bool(needs_review),
    )


def _public_question_row(row: dict[str, Any]) -> QuestionAnalysisRow:
    attempt_count = int(row.get("_attempt_count") or 0)
    full_sum = float(row.get("_full_sum") or 0)
    if attempt_count <= 0:
        metric_status: Literal["ready", "missing_max_score", "no_attempts"] = "no_attempts"
    elif full_sum <= 0:
        metric_status = "missing_max_score"
    else:
        metric_status = "ready"
    return QuestionAnalysisRow(
        class_name=str(row.get("班级") or ""),
        question_id=str(row.get("题号") or ""),
        max_score=float(row.get("满分")) if full_sum > 0 else None,
        score_rate=float(row.get("班级得分率")) if metric_status == "ready" else None,
        average_score=float(row.get("平均得分")) if attempt_count > 0 else None,
        deduction_count=int(row.get("失分人数") or 0),
        attempt_count=attempt_count,
        metric_status=metric_status,
    )


def _data_root(db: DBManager) -> Path | None:
    return db.db_path.parent.parent if db.db_path.parent.name == "databases" else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
