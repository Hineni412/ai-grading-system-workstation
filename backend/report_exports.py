from __future__ import annotations

import threading

from backend.file_access import ControlledFileError
from backend.files.service import JobFileService
from backend.jobs.manager import JobManager
from backend.jobs.store import JobRecord

_submit_lock = threading.RLock()
# 考试分析报告（AI 叙述）导出类型：提交时不带 excel_options。
# 班级分析已改为系统内嵌页面（backend/class_analysis.py），不再是导出类型。
ANALYSIS_REPORT_TYPES = frozenset({"personal_analysis_html"})


def submit_report_export(
    *,
    manager: JobManager,
    file_service: JobFileService,
    session_id: int,
    report_type: str,
    force_regenerate: bool,
    score_excel_options: dict[str, object] | None = None,
    student_ids: list[int] | None = None,
    publish: bool = True,
) -> JobRecord:
    normalized_options = (
        _normalize_score_excel_options(score_excel_options)
        if report_type == "score_excel"
        else None
    )
    payload = {'session_id': int(session_id), 'report_type': str(report_type)}
    if normalized_options is not None:
        payload['score_excel_options'] = normalized_options
    if report_type == 'personal_analysis_html':
        payload.update(student_ids=sorted(set(student_ids)) if student_ids is not None else None,
                       publish=publish)
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
                    if job.payload.get('report_type', 'score_excel') != report_type:
                        continue
                    if report_type == 'score_excel' and _normalize_score_excel_options(
                            job.payload.get('score_excel_options')) != normalized_options:
                        continue
                    if report_type == 'personal_analysis_html' and (
                            job.payload.get('student_ids') != payload['student_ids']
                            or job.payload.get('publish', True) != publish):
                        continue
                    if job.status in {"queued", "running"}:
                        return job
                    if not publish and job.result.get("failed", 0) == 0:
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
