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
    from backend.api.dependencies import get_grading_db, get_job_manager, get_reports_dir
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def report_handler(context: JobContext) -> dict[str, object]:
        session_id = int(context.payload["session_id"])
        context.report(0.5, "report_export", f"session {session_id}")
        report_type = str(context.payload.get("report_type") or "score_excel")
        is_pdf = report_type == "annotated_original_pdf"
        suffix = ".pdf" if is_pdf else ".xlsx"
        report_path = tmp_path / "reports" / (
            f"七年级期中{suffix}" if is_pdf else "report.xlsx"
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


def test_session_report_export_route_queues_report_job(
    client_with_db_and_manager,
    tmp_path: Path,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")

    response = client.post(f"/api/sessions/{session_id}/reports/export")

    assert response.status_code == 202
    created = response.json()
    assert created["job_type"] == "report_export"
    assert created["payload"] == {"session_id": session_id}

    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["status"] == "succeeded"
    assert loaded["result"] == {
        "session_id": session_id,
        "filename": "report.xlsx",
        "download_url": f"/api/jobs/{created['id']}/download",
    }
    assert "file_path" not in str(loaded["result"])
    assert str(tmp_path) not in str(loaded["result"])


def test_session_report_export_route_queues_annotated_original_pdf(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("七年级期中", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "annotated_original_pdf",
            "force_regenerate": True,
        },
    )

    assert response.status_code == 202
    created = response.json()
    assert created["payload"]["session_id"] == session_id
    assert created["payload"]["report_type"] == "annotated_original_pdf"
    assert created["payload"]["score_revision"]
    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["result"] == {
        "session_id": session_id,
        "report_type": "annotated_original_pdf",
        "score_revision": created["payload"]["score_revision"],
        "filename": "七年级期中.pdf",
        "download_url": f"/api/jobs/{created['id']}/download",
    }


def test_session_report_export_route_rejects_unknown_report_type(
    client_with_db_and_manager,
) -> None:
    client, db, _manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "html"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_session_report_export_rejects_empty_exam(
    client_with_db_and_manager,
) -> None:
    client, db, _manager = client_with_db_and_manager
    session_id = db.create_grading_session("Empty exam", "rubric.json", "answer.json")

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_results_missing"


def test_session_report_export_allows_manual_only_exam_with_locks(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Manual exam", "rubric.json", "answer.json")
    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name) VALUES ('001', '张三')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, student_id,
                    match_status, processing_status
                ) VALUES (?, '', '', ?, 'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        conn.commit()
    db.confirm_teacher_score_locks(
        session_id,
        "batch-1",
        [
            {
                "student_id": student_id,
                "question_id": "Q1",
                "score_awarded": 4.0,
                "max_score": 5.0,
                "deduction_reason": None,
                "source_target_type": "exam_paper",
                "source_target_id": paper_id,
                "expected_revision": 0,
            }
        ],
    )

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )

    assert response.status_code == 202
    manager.wait(response.json()["id"], timeout=5)
    context = client.get(f"/api/sessions/{session_id}/reports/context").json()
    assert context["has_results"] is True

    # Removing the only score source restores the rejection.
    db.review_repository.delete_session_teacher_score_locks(session_id)
    rejected = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "report_results_missing"


def test_session_report_export_reuses_current_available_file_by_default(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)
    request = {"report_type": "score_excel"}

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json=request,
    )
    manager.wait(first.json()["id"], timeout=5)
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json=request,
    )

    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]


def test_session_report_export_cache_includes_excel_name_list_options(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "score_excel",
            "excel_options": {
                "hide_bottom_enabled": True,
                "hide_bottom_n": 8,
                "manual_hidden_student_ids": [],
            },
        },
    )
    manager.wait(first.json()["id"], timeout=5)
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "score_excel",
            "excel_options": {
                "hide_bottom_enabled": True,
                "hide_bottom_n": 10,
                "manual_hidden_student_ids": [],
            },
        },
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]
    assert first.json()["payload"]["score_excel_options"]["hide_bottom_n"] == 8
    assert second.json()["payload"]["score_excel_options"]["hide_bottom_n"] == 10
    assert (
        first.json()["payload"]["report_options_fingerprint"]
        != second.json()["payload"]["report_options_fingerprint"]
    )


def test_session_report_export_normalizes_manual_hidden_students_for_cache(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "score_excel",
            "excel_options": {
                "hide_bottom_enabled": False,
                "hide_bottom_n": 99,
                "manual_hidden_student_ids": [9, 2, 9],
            },
        },
    )
    manager.wait(first.json()["id"], timeout=5)
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "score_excel",
            "excel_options": {
                "hide_bottom_enabled": False,
                "hide_bottom_n": 1,
                "manual_hidden_student_ids": [2, 9],
            },
        },
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert first.json()["payload"]["score_excel_options"] == {
        "hide_bottom_enabled": False,
        "hide_bottom_n": 0,
        "manual_hidden_student_ids": [2, 9],
    }


def test_session_report_export_rejects_excel_options_for_pdf(
    client_with_db_and_manager,
) -> None:
    client, db, _manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "annotated_original_pdf",
            "excel_options": {"hide_bottom_enabled": False},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_session_report_export_reuses_cache_older_than_first_history_page(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    manager.wait(first.json()["id"], timeout=5)
    newest = None
    for index in range(100):
        newest = manager.submit(
            "report_export",
            {
                "session_id": session_id,
                "report_type": "annotated_original_pdf",
                "score_revision": f"historical-{index}",
            },
        )
    assert newest is not None
    manager.wait(newest.id, timeout=5)

    repeated = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )

    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]


def test_session_report_export_force_regenerate_creates_new_job(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    manager.wait(first.json()["id"], timeout=5)
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel", "force_regenerate": True},
    )

    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]


def test_session_report_export_creates_one_job_for_simultaneous_requests(
    client_with_db_and_manager,
) -> None:
    client, db, _manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    def submit(_index: int) -> int:
        response = client.post(
            f"/api/sessions/{session_id}/reports/export",
            json={"report_type": "score_excel"},
        )
        assert response.status_code == 202
        return int(response.json()["id"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        job_ids = list(executor.map(submit, range(8)))

    assert len(set(job_ids)) == 1


def test_session_report_export_uses_new_job_after_score_revision_changes(
    client_with_db_and_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
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


def test_session_report_export_rebuilds_when_cached_file_is_missing(
    client_with_db_and_manager,
    tmp_path: Path,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)

    first = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    manager.wait(first.json()["id"], timeout=5)
    (tmp_path / "reports" / "report.xlsx").unlink()
    second = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )

    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]


def test_report_context_marks_current_and_expired_history_without_paths(
    client_with_db_and_manager,
    tmp_path: Path,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)
    current = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    manager.wait(current.json()["id"], timeout=5)
    current_job = manager.get(current.json()["id"])
    assert current_job is not None
    Path(current_job.result["file_path"]).unlink()

    response = client.get(f"/api/sessions/{session_id}/reports/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["score_revision"] == current.json()["payload"]["score_revision"]
    assert payload["has_results"] is True
    assert payload["jobs"][0]["id"] == current.json()["id"]
    assert payload["jobs"][0]["is_current_revision"] is True
    assert payload["jobs"][0]["file_status"] == "expired"
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 100
    assert payload["total_pages"] == 1
    assert "file_path" not in str(payload)
    assert str(tmp_path) not in str(payload)


def test_report_context_paginates_history(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    _seed_result(db, session_id)
    for _index in range(2):
        created = client.post(
            f"/api/sessions/{session_id}/reports/export",
            json={"report_type": "score_excel", "force_regenerate": True},
        )
        manager.wait(created.json()["id"], timeout=5)

    response = client.get(
        f"/api/sessions/{session_id}/reports/context",
        params={"page": 2, "page_size": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["jobs"]) == 1
    assert payload["total"] == 2
    assert payload["page"] == 2
    assert payload["page_size"] == 1
    assert payload["total_pages"] == 2


def test_score_revision_changes_when_report_visible_detail_changes(tmp_path) -> None:
    from backend.report_exports import score_revision

    class FakeReviews:
        def __init__(self):
            self.locks = []

        def list_teacher_score_locks(self, _session_id):
            return self.locks

    class FakeDB:
        db_path = tmp_path / "grading.db"
        deduction_reason = "计算错误"

        def __init__(self):
            self.review_repository = FakeReviews()

        def get_session_results(self, _session_id):
            return [{
                "result_id": 1,
                "student_id": 2,
                "student_code": "S002",
                "student_name": "匿名学生",
                "class_name": "一班",
                "front_image": "front.jpg",
                "back_image": "back.jpg",
                "total_score": 100,
                "student_score": 88,
                "needs_human_review": 0,
                "raw_json": {},
            }]

        def get_result_details(self, _result_id):
            return [{
                "detail_id": 3,
                "question_id": "Q1",
                "score_awarded": 8,
                "deduction_reason": self.deduction_reason,
                "knowledge_id": "K1",
                "knowledge_ids": ["K1"],
                "error_category": "calculation",
                "error_summary": "符号错误",
            }]

    db = FakeDB()
    before = score_revision(db, 7)
    db.deduction_reason = "概念错误"
    after = score_revision(db, 7)

    assert before != after

    # Teacher locks are report-visible too: editing one must invalidate cache.
    db.review_repository.locks = [{
        "id": 1,
        "session_id": 7,
        "scan_batch_id": "batch-1",
        "student_id": 2,
        "question_id": "Q1",
        "score_awarded": 9,
        "max_score": 10,
    }]
    assert score_revision(db, 7) != after


def test_session_report_export_route_requires_existing_session(client_with_db_and_manager) -> None:
    client, _db, _manager = client_with_db_and_manager

    response = client.post(
        "/api/sessions/404/reports/export",
        headers={"x-request-id": "rid-report-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-report-missing"
