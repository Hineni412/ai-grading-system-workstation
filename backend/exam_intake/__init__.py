from __future__ import annotations

import json
from typing import Any, Mapping


INTAKE_REQUIRED_KEY = "intake_required"
INTAKE_COMPLETE_STATES = frozenset({"ready"})
INTAKE_BLOCKING_STATES = frozenset({"not_started", "running", "partial", "failed"})


class ExamIntakeIncomplete(RuntimeError):
    """Exam scoring, template mapping, or grading is blocked until intake is complete."""

    def __init__(
        self,
        *,
        category: str,
        message: str,
        failed_question_ids: tuple[str, ...] = (),
        retryable: bool = True,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = str(category or "exam_intake_incomplete")
        self.message = str(message)
        self.failed_question_ids = tuple(failed_question_ids)
        self.retryable = bool(retryable)
        self.details = dict(details or {})

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "error_code": "exam_intake_incomplete",
            "category": self.category,
            "message": self.message,
            "failed_question_ids": list(self.failed_question_ids),
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def session_intake_details(session: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (session or {}).get("question_bank_sync_details_json")
    if raw in (None, ""):
        raw = (session or {}).get("question_bank_sync_details")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def intake_is_required(session: Mapping[str, Any] | None) -> bool:
    return bool(session_intake_details(session).get(INTAKE_REQUIRED_KEY))


def intake_is_complete(session: Mapping[str, Any] | None) -> bool:
    state = str((session or {}).get("question_bank_sync_state") or "").strip().casefold()
    return state in INTAKE_COMPLETE_STATES


def exam_intake_blocks_progress(session: Mapping[str, Any] | None) -> bool:
    """New exams that requested intake cannot score, map, or grade until ready."""

    if not intake_is_required(session):
        return False
    return not intake_is_complete(session)


def classify_intake_result(result: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(result or {})
    outcome = str(payload.get("outcome") or "").strip().casefold()
    failed_ids = [
        str(item).strip()
        for item in list(payload.get("failed_question_ids") or [])
        if str(item).strip()
    ]
    unresolved_ids = [
        str(item).strip()
        for item in payload.get("unresolved_question_ids") or []
        if str(item).strip()
    ]
    failed_ids = list(dict.fromkeys([*failed_ids, *unresolved_ids]))
    imported = max(0, int(payload.get("imported_count") or 0))
    tagged = max(0, int(payload.get("tagged_count") or 0))
    criteria = max(0, int(payload.get("criteria_count") or 0))
    incomplete = max(0, int(payload.get("analysis_incomplete_count") or 0))
    retryable = bool(payload.get("retryable", True))
    if outcome == "complete" and not failed_ids and incomplete == 0:
        category = "complete"
        state = "ready"
        message = "试卷已完整分析并写入题库判定点。"
        retryable = False
    elif imported <= 0 and tagged <= 0 and criteria <= 0:
        category = str(payload.get("failure_code") or "import_failed")
        state = "failed"
        message = "试卷未能写入题库；当前不能赋分、标定题框或开始批改。"
    elif incomplete:
        category = "analysis_incomplete"
        state = "partial"
        message = "题目分析尚未全部完成，不能赋分。"
    elif unresolved_ids:
        category = "question_mapping"
        state = "partial"
        message = (
            f"题目 {'、'.join(unresolved_ids)} 未能对应到独立的题库记录；"
            "请核对入库题目边界。已有分析已保留，入库完整后才能赋分。"
        )
    elif failed_ids:
        category = "question_failed"
        state = "partial"
        message = "部分题目未能写入题库判定点；只可继续处理缺失项，不能赋分。"
    else:
        category = "partial"
        state = "partial"
        message = "题库入库未完整成功；当前不能赋分、标定题框或开始批改。"
    return {
        "complete": category == "complete",
        "state": state,
        "category": category,
        "message": message,
        "failed_question_ids": failed_ids,
        "retryable": retryable,
        "imported_count": imported,
        "tagged_count": tagged,
        "criteria_count": criteria,
        "analysis_incomplete_count": incomplete,
    }


def persist_intake_required(db: Any, session_id: int) -> None:
    session = db.sessions.get_grading_session(int(session_id))
    details = session_intake_details(session)
    details[INTAKE_REQUIRED_KEY] = True
    current = str((session or {}).get("question_bank_sync_state") or "not_started")
    if current not in {"ready", "partial", "failed", "running"}:
        current = "not_started"
    db.sessions.update_question_bank_sync_state(
        int(session_id),
        state=current,
        details=details,
        error=(session or {}).get("question_bank_sync_error"),
    )


def persist_intake_result(
    db: Any,
    session_id: int,
    classified: Mapping[str, Any],
) -> None:
    session = db.sessions.get_grading_session(int(session_id))
    details = session_intake_details(session)
    details[INTAKE_REQUIRED_KEY] = True
    details["intake_category"] = str(classified.get("category") or "")
    details["intake_failed_question_ids"] = list(
        classified.get("failed_question_ids") or []
    )
    details["intake_retryable"] = bool(classified.get("retryable"))
    details["intake_message"] = str(classified.get("message") or "")
    error = None if classified.get("complete") else str(classified.get("message") or "")
    db.sessions.update_question_bank_sync_state(
        int(session_id),
        state=str(classified.get("state") or "failed"),
        details=details,
        error=error,
    )
