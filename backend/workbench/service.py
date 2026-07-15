from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.jobs.manager import JobManager
from backend.public_data import sanitize_public_diagnostic_text
from backend.review.service import ReviewApplicationService
from db_manager import DBManager


class WorkbenchService:
    def __init__(
        self,
        db: DBManager,
        review_service: ReviewApplicationService,
        job_manager: JobManager,
    ) -> None:
        self.db = db
        self.review_service = review_service
        self.job_manager = job_manager

    def overview(
        self,
        session_id: int | None,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate existing read models without internal HTTP calls."""
        recent_sessions = []
        for session in self.db.list_grading_sessions()[: int(recent_limit)]:
            recent_sessions.append(
                {
                    "session": _session_summary(session),
                    "progress": self.db.get_session_progress(int(session["id"])),
                }
            )

        current_session = None
        progress = None
        review = None
        anomalies = None
        recent_jobs: list[dict[str, Any]] = []
        if session_id is not None:
            session = self.db.get_grading_session(int(session_id))
            if session is not None:
                current_session = _session_summary(session)
                progress = self.db.get_session_progress(int(session_id))
                questions = self.review_service.list_questions(
                    int(session_id),
                    session,
                )
                review_questions = [
                    question
                    for question in questions
                    if question.needs_review_count > 0
                ]
                review = {
                    "question_count": len(review_questions),
                    "item_count": sum(
                        question.needs_review_count for question in review_questions
                    ),
                }
                anomalies = {
                    "unmatched_papers": progress["unmatched_papers"],
                    "scan_issue_students": progress["scan_issue_students"],
                    "failed_papers": progress["failed_papers"],
                }
                jobs, _total = self.job_manager.list(
                    session_id=int(session_id),
                    limit=5,
                )
                recent_jobs = [_job_summary(job) for job in jobs]

        return {
            "current_session": current_session,
            "progress": progress,
            "review": review,
            "anomalies": anomalies,
            "recent_jobs": recent_jobs,
            "recent_sessions": recent_sessions,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def list_anomalies(self, session_id: int) -> list[dict[str, Any]]:
        """Return sanitized stable anomaly rows."""
        items: list[dict[str, Any]] = []
        for row in self.db.list_session_anomalies(int(session_id)):
            item = dict(row)
            item["detail"] = sanitize_public_diagnostic_text(item.get("detail"))
            items.append(item)
        return items


def _session_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["session_name"]),
        "status": str(row["status"]),
        "is_deleted": bool(int(row.get("is_deleted") or 0)),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _job_summary(job: Any) -> dict[str, Any]:
    detail = sanitize_public_diagnostic_text(job.detail) or ""
    return {
        "id": int(job.id),
        "job_type": str(job.job_type),
        "status": str(job.status),
        "progress": float(job.progress),
        "stage": str(job.stage),
        "detail": str(detail),
        "created_at": str(job.created_at),
        "started_at": job.started_at,
        "updated_at": str(job.updated_at),
        "finished_at": job.finished_at,
    }
