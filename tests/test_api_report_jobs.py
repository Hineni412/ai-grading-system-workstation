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


@pytest.fixture
def personal_api(tmp_path):
    from tests.test_analysis_report import _seed_analysis_session, _add_personal_report_scans
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager, get_reports_dir, get_job_file_service
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.files.service import JobFileService
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    papers = _add_personal_report_scans(db, sid, tmp_path)
    reports = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register("report_export", lambda _c: dict(generated=1, failed=0, skipped=0))
    def bundle_handler(context):
        target = reports / "test-personal-download.html"
        return dict(file_path=str(target), filename=target.name, generated=1, failed=0, skipped=0)
    manager.register("personal_report_bundle", bundle_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports
    app.dependency_overrides[get_job_file_service] = lambda: JobFileService(reports)
    with TestClient(app) as client:
        try:
            yield client, db, sid, papers, reports, manager
        finally:
            manager.shutdown()
            db.close()


def test_personal_api_states_html_shots_and_read_only_boundaries(personal_api, monkeypatch):
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator
    from tests.test_analysis_report import FakeLLMClient
    client, db, sid, papers, reports, _manager = personal_api
    student_id = papers[0]["student_id"]
    prefix = f"/api/sessions/{sid}/personal-reports"
    states = client.get(prefix).json()["students"]
    assert [s["status"] for s in states].count("missing") == 2
    absent = next(s["student_id"] for s in states if s["status"] == "unavailable")
    missing = client.get(f"{prefix}/{student_id}/html")
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "personal_report_missing"
    unavailable = client.get(f"{prefix}/{absent}/html?narrative=none")
    assert unavailable.status_code == 409 and unavailable.json()["error"]["code"] == "personal_report_unavailable"
    html = client.get(f"{prefix}/{student_id}/html?narrative=none&review_links=1")
    assert html.status_code == 200 and html.headers["cache-control"] == "no-store"
    assert 'class="review-link"' in html.text and 'data:image/jpeg' not in html.text
    shot = client.get(f"{prefix}/{student_id}/shots/Q2")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
    assert shot.content.startswith(b"\xff\xd8")
    assert client.get(f"{prefix}/{student_id}/shots/Q2").content == shot.content
    from PIL import Image
    from backend.reporting.analysis_report_exporter import lost_question_shot_specs
    from backend.personal_reports import personal_render_context
    context = personal_render_context(db, sid, reports)
    student = next(s for s in context['data'].students if s.student_id == student_id)
    spec = lost_question_shot_specs(db, context['data'], student, regions=context['regions'], data_root=reports.parent)[0]
    Image.new('RGB', (1000, 1400), 'red').save(spec['image_path'])
    assert client.get(f"{prefix}/{student_id}/shots/Q2").content != shot.content  # 文件更新使解码缓存失效。
    assert client.get(f"{prefix}/{student_id}/shots/not-a-key").status_code == 404
    assert not reports.exists()  # 查看没有新增持久文件
    generator = AnalysisReportGenerator(db, reports.parent / "test-generated", data_root=reports.parent,
        narrative_cache_dir=reports / ".analysis_narrative_cache", llm_client_factory=lambda: FakeLLMClient())
    generator.export_session(sid, "personal_analysis_html", html_only=True)
    assert client.get(f"{prefix}/{student_id}/html").status_code == 200
    monkeypatch.setattr("backend.files.session_originals.originals_state", lambda *_args: "cleared")
    assert client.get(f"{prefix}/{student_id}/shots/Q2").status_code == 410
    released = client.get(f"{prefix}/{student_id}/html")
    assert '原卷已释放，无法显示作答图；分数与批语不受影响' in released.text


def test_personal_summary_matches_current_stale_and_missing_list(personal_api):
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator
    from backend.personal_reports import personal_report_summary
    from tests.test_analysis_report import FakeLLMClient
    client, db, sid, papers, reports, _manager = personal_api
    assert personal_report_summary(db, sid, reports) == dict(current=0, stale=0, missing=2)
    assert not reports.exists()
    generator = AnalysisReportGenerator(db, reports.parent / "TEST-generated", data_root=reports.parent,
        narrative_cache_dir=reports / ".analysis_narrative_cache", llm_client_factory=lambda: FakeLLMClient())
    generator.export_session(sid, "personal_analysis_html", html_only=True)
    assert personal_report_summary(db, sid, reports) == dict(current=2, stale=0, missing=0)
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("UPDATE session_results SET total_score=total_score+1 WHERE session_id=? AND student_id=?",
                           (sid, papers[0]["student_id"]))
    files_before = {str(path): path.stat().st_mtime_ns for path in reports.rglob('*') if path.is_file()}
    summary = personal_report_summary(db, sid, reports)
    assert summary == dict(current=1, stale=1, missing=0)
    states = client.get(f"/api/sessions/{sid}/personal-reports").json()["students"]
    assert summary == {status: sum(item['status'] == status for item in states) for status in summary}
    assert {str(path): path.stat().st_mtime_ns for path in reports.rglob('*') if path.is_file()} == files_before


def test_personal_shot_rejects_data_root_escape(personal_api):
    client, db, sid, papers, reports, _manager = personal_api
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("UPDATE exam_papers SET front_image=? WHERE id=?", (str(reports.parent.parent / "outside-test.png"), papers[0]["id"]))
    assert client.get(f"/api/sessions/{sid}/personal-reports/{papers[0]['student_id']}/shots/Q2").status_code in {403, 404}


def test_personal_preflight_filters_students_and_generation_fingerprint(personal_api, monkeypatch):
    client, db, sid, papers, reports, manager = personal_api
    monkeypatch.setattr("backend.model_profiles.content_generation.resolve_content_generation_settings", lambda: object())
    student_ids = [p["student_id"] for p in papers]
    preflight = client.get(f"/api/sessions/{sid}/reports/analysis-preflight?report_type=personal_analysis_html&student_ids={student_ids[0]}")
    assert preflight.status_code == 200
    assert preflight.json()["call_count"] == 1
    jobs = []
    for selected, publish in (([student_ids[0]], False), ([student_ids[1]], False), ([student_ids[0]], True)):
        response = client.post(f"/api/sessions/{sid}/reports/export", json=dict(report_type="personal_analysis_html", student_ids=selected, publish=publish))
        assert response.status_code == 202
        job = manager.wait(response.json()["id"], timeout=5)
        jobs.append(response.json())
    assert len({j["id"] for j in jobs}) == 3
    assert jobs[0]["payload"]["publish"] is False
    assert jobs[0]["payload"]["student_ids"] == [student_ids[0]]
    assert 'file_path' not in client.get(f"/api/jobs/{jobs[0]['id']}").json()["result"]
    context = client.get(f"/api/sessions/{sid}/reports/context").json()
    assert all(j["payload"].get("publish", True) for j in context["jobs"])
    assert context['total'] == len(context['jobs']) == 1
    assert client.get(f"/api/sessions/{sid}/reports/analysis-preflight?report_type=personal_analysis_html&student_ids=bad").status_code == 422
    assert client.post(f"/api/sessions/{sid}/reports/export", json=dict(report_type="score_excel", publish=False)).status_code == 422


def test_personal_student_exams_include_later_same_volume_only(personal_api):
    client, db, sid, papers, _reports, _manager = personal_api
    student_id = papers[0]["student_id"]
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("UPDATE grading_sessions SET curriculum_volume_id='test-volume' WHERE id=?", (sid,))
        connection.execute("UPDATE session_results SET graded_at='2026-09-01' WHERE session_id=?", (sid,))
    later = db.sessions.create_grading_session("较晚考试", "rubric.json", "answer.json")
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("UPDATE grading_sessions SET curriculum_volume_id='test-volume' WHERE id=?", (later,))
        connection.execute("INSERT INTO session_attendance(session_id,student_id,attendance_status) VALUES (?,?,'absent')", (later, student_id))
    response = client.get(f"/api/students/{student_id}/personal-reports?curriculum_volume_id=test-volume")
    assert response.status_code == 200
    assert {s["session_id"] for s in response.json()["sessions"]} == {sid, later}
    assert client.get(f"/api/students/{student_id}/personal-reports?curriculum_volume_id=another-volume").json()["sessions"] == []


def test_personal_bundle_download_is_consumed_and_public_result_has_no_path(personal_api):
    client, _db, sid, papers, reports, manager = personal_api
    reports.mkdir(exist_ok=True)
    target = reports / "test-personal-download.html"
    target.write_text("<!doctype html><p>合成报告</p>", encoding="utf-8")
    response = client.post("/api/personal-reports/bundles", json=dict(session_ids=[sid], student_ids=[papers[0]["student_id"]], scope_label="指定1人"))
    assert response.status_code == 202
    manager.wait(response.json()["id"], timeout=5)
    public = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert 'file_path' not in public["result"]
    assert public["result"]["download_url"]
    assert client.get(public["result"]["download_url"]).status_code == 200
    assert not target.exists()
    assert client.post("/api/personal-reports/bundles", json=dict(session_ids=[], student_ids=[1], scope_label="指定1人")).status_code == 422
