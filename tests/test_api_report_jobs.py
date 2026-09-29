from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import pytest
from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories


def _seed_result(db, session_id: int) -> None:
    with sqlite3.connect(db.db_path) as conn:
        student = conn.execute(
            "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, ?)",
            (f"S-{session_id}", "匿名学生", "匿名班级"),
        )
        paper = conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (?, '', '', ?, 'matched', 'graded')
            """,
            (session_id, int(student.lastrowid)),
        )
        conn.execute(
            """
            INSERT INTO session_results (
                session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (?, ?, ?, 100, 88, 0, '{}')
            """,
            (session_id, int(student.lastrowid), int(paper.lastrowid)),
        )


@pytest.fixture
def client_with_db_and_manager(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_reports_dir,
    )
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore
    

    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def report_handler(context: JobContext) -> dict[str, object]:
        session_id = int(context.payload["session_id"])
        context.report(0.5, "report_export", f"session {session_id}")
        report_type = str(context.payload.get("report_type") or "score_excel")
        is_pdf = report_type == "annotated_original_pdf"
        suffix = ".pdf" if is_pdf else ".xlsx"
        report_path = (
            tmp_path / "reports" / (f"七年级期中{suffix}" if is_pdf else "report.xlsx")
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(b"%PDF" if suffix == ".pdf" else b"xlsx")
        result = {
            "session_id": session_id,
            "file_path": str(report_path),
            "filename": report_path.name,
            "internal_debug": "must-not-be-public",
        }
        if "report_type" in context.payload:
            result["report_type"] = report_type
        if context.payload.get("score_revision") is not None:
            result["score_revision"] = context.payload["score_revision"]
        return result

    manager.register("report_export", report_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: tmp_path / "reports"
    with TestClient(app) as client:
        try:
            yield client, db, manager
        finally:
            manager.shutdown()


def test_session_report_export_uses_new_job_after_score_revision_changes(
    client_with_db_and_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.sessions.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)
    revisions = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(
        "backend.api.routers.reports.score_revision",
        lambda _db, _session_id: next(revisions),
    )

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    manager.wait(first.json()["id"], timeout=5)
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["payload"]["score_revision"] == "b" * 64
