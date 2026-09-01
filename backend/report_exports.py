from __future__ import annotations

import hashlib
import json
import threading

from backend.file_access import ControlledFileError
from backend.files.service import JobFileService
from backend.jobs.manager import JobManager
from backend.jobs.store import JobRecord
from backend.repositories.access import GradingRepositoryAccess


_submit_lock = threading.RLock()
_REPORT_RENDITION_VERSIONS = {
    "score_excel": "score_excel_print_v5",
    "annotated_original_pdf": "annotated_original_pdf_score_boxes_v3",
    "personal_analysis_html": "personal_analysis_html_v1",
}

# 考试分析报告（AI 叙述）导出类型：提交时不带 excel_options。
# 班级分析已改为系统内嵌页面（backend/class_analysis.py），不再是导出类型。
ANALYSIS_REPORT_TYPES = frozenset({"personal_analysis_html"})


def report_rendition_version(report_type: str) -> str:
    return _REPORT_RENDITION_VERSIONS.get(str(report_type), "unknown")


def score_revision(db: GradingRepositoryAccess, session_id: int) -> str:
    rows: list[dict[str, object]] = []
    for result in db.get_session_results(int(session_id)):
        result_id = int(result["result_id"])
        rows.append(
            {
                "result": dict(result),
                "details": [
                    dict(detail)
                    for detail in db.get_result_details(result_id)
                ],
            }
        )
    payload = {
        "results": rows,
        "locks": [
            dict(lock)
            for lock in db.review_repository.list_teacher_score_locks(
                int(session_id)
            )
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def submit_report_export(
    *,
    manager: JobManager,
    file_service: JobFileService,
    session_id: int,
    report_type: str,
    revision: str,
    force_regenerate: bool,
    score_excel_options: dict[str, object] | None = None,
) -> JobRecord:
    normalized_options = (
        _normalize_score_excel_options(score_excel_options)
        if report_type == "score_excel"
        else None
    )
    options_fingerprint = _report_options_fingerprint(
        report_type,
        normalized_options,
    )
    payload = {
        "session_id": int(session_id),
        "report_type": str(report_type),
        "score_revision": str(revision),
        "report_options_fingerprint": options_fingerprint,
    }
    if normalized_options is not None:
        payload["score_excel_options"] = normalized_options
    with _submit_lock:
        if not force_regenerate:
            offset = 0
            while True:
                jobs, total = manager.list(
                    session_id=int(session_id),
                    job_types=("report_export",),
                    statuses=("queued", "running", "succeeded"),
                    limit=100,
                    offset=offset,
                )
                for job in jobs:
                    if (
                        job.payload.get("report_type") != report_type
                        or job.payload.get("score_revision") != revision
                        or job.payload.get("report_options_fingerprint")
                        != options_fingerprint
                    ):
                        continue
                    if job.status in {"queued", "running"}:
                        return job
                    try:
                        file_service.resolve(job)
                    except ControlledFileError:
                        continue
                    return job
                offset += len(jobs)
                if not jobs or offset >= total:
                    break
        return manager.submit("report_export", payload)


def _normalize_score_excel_options(
    options: dict[str, object] | None,
) -> dict[str, object]:
    source = dict(options or {})
    hide_bottom_enabled = bool(source.get("hide_bottom_enabled", True))
    raw_bottom_n = source.get("hide_bottom_n", 8)
    try:
        hide_bottom_n = max(0, min(100, int(raw_bottom_n)))
    except (TypeError, ValueError):
        hide_bottom_n = 8
    if not hide_bottom_enabled:
        hide_bottom_n = 0

    raw_student_ids = source.get("manual_hidden_student_ids")
    student_ids: list[int] = []
    if isinstance(raw_student_ids, list):
        student_ids = sorted(
            {
                int(student_id)
                for student_id in raw_student_ids
                if isinstance(student_id, int)
                and not isinstance(student_id, bool)
                and student_id > 0
            }
        )
    return {
        "hide_bottom_enabled": hide_bottom_enabled,
        "hide_bottom_n": hide_bottom_n,
        "manual_hidden_student_ids": student_ids,
    }


def _report_options_fingerprint(
    report_type: str,
    score_excel_options: dict[str, object] | None,
) -> str:
    payload = {
        "rendition_version": _REPORT_RENDITION_VERSIONS.get(
            report_type,
            "unknown",
        ),
        "score_excel_options": score_excel_options,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
