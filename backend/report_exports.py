from __future__ import annotations

import hashlib
import json
import threading

from backend.file_access import ControlledFileError
from backend.files.service import JobFileService
from backend.jobs.manager import JobManager
from backend.jobs.store import JobRecord
from db_manager import DBManager


_submit_lock = threading.RLock()


def score_revision(db: DBManager, session_id: int) -> str:
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
    serialized = json.dumps(
        rows,
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
) -> JobRecord:
    payload = {
        "session_id": int(session_id),
        "report_type": str(report_type),
        "score_revision": str(revision),
    }
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
