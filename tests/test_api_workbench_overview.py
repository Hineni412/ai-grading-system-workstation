from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.repositories.grading_database import open_grading_repositories


def test_review_summary_refreshes_on_score_context_and_rubric_changes(tmp_path, monkeypatch):
    from backend.workbench.service import WorkbenchService
    db = open_grading_repositories(tmp_path / "databases" / "TEST-workbench.db")
    db.initialize()
    rubric_path = tmp_path / "TEST-rubric.json"
    rubric_path.write_text("{}", encoding="utf-8")
    sid = db.sessions.create_grading_session("TEST-home", str(rubric_path), "")
    calls = []
    workspace = SimpleNamespace(context=None)
    monkeypatch.setattr("backend.workbench.service.current_manual_context",
                        lambda _sid, source, **kwargs: source.context)
    def questions(current_id, *args, **kwargs):
        calls.append(current_id)
        return [SimpleNamespace(needs_review_count=calls.count(sid))] if current_id == sid else []
    service = WorkbenchService(db, SimpleNamespace(list_questions=questions), SimpleNamespace(list=lambda **kwargs: ([], 0)))
    session = db.sessions.get_grading_session(sid)
    assert service.overview(sid, 5)["review"] == {"question_count": 1, "item_count": 1}
    service = WorkbenchService(db, service.review_service, service.job_manager)
    assert service.overview(sid, 5)["review"]["item_count"] == 1
    assert len(calls) == 1
    workspace.context = {"scan_batch_id": "TEST-new", "papers": []}
    assert service.overview(sid, 5, workspace=workspace)["review"]["item_count"] == 2
    rubric_path.write_text('{"questions": []}', encoding="utf-8")
    assert service.overview(sid, 5)["review"]["item_count"] == 3
    db.sessions.create_grading_session("TEST-new-score-generation", "", "")
    assert service.overview(sid, 5)["review"]["item_count"] == 4


def test_overview_filters_before_limit_and_keeps_current_exam(tmp_path, monkeypatch):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db, get_workbench_service, get_scan_grading_workspace, get_reports_dir,
    )
    from backend.workbench.service import WorkbenchService

    db = open_grading_repositories(tmp_path / "TEST-workbench.db")
    db.initialize()
    ids = [db.sessions.create_grading_session(
        f"TEST-home-{index}", "", "", curriculum_volume_id=volume,
    ) for index, volume in enumerate(["term-a", "term-b", "term-a", "term-b"])]
    service = WorkbenchService(
        db, SimpleNamespace(list_questions=lambda *args, **kwargs: []),
        SimpleNamespace(list=lambda **kwargs: ([], 0)),
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_workbench_service] = lambda: service
    app.dependency_overrides[get_scan_grading_workspace] = lambda: SimpleNamespace()
    app.dependency_overrides[get_reports_dir] = lambda: tmp_path / "TEST-reports"
    monkeypatch.setattr("backend.personal_reports.personal_report_summary",
                        lambda *args: dict(current=8, stale=2, missing=3))
    contexts = []
    def context(session_id, _workspace, *, read_only):
        assert read_only is True
        contexts.append(session_id)
        return {"scan_batch_id": f"TEST-batch-{session_id}", "papers": []}
    monkeypatch.setattr("backend.workbench.service.current_manual_context", context)
    with TestClient(app, follow_redirects=False) as client:
        unfiltered = client.get("/api/workbench/overview", params={"session_id": ids[0]})
        assert unfiltered.status_code == 200
        full = unfiltered.json()
        assert full["current_session"]["curriculum_volume_id"] == "term-a"
        assert full["personal_reports"] == dict(current=8, stale=2, missing=3)
        assert [row["session"]["id"] for row in full["recent_sessions"]] == ids[::-1]
        assert contexts == ids[::-1]
        assert all(row["review"] == {"question_count": 0, "item_count": 0} for row in full["recent_sessions"])
        filtered = client.get("/api/workbench/overview", params={
            "session_id": ids[3], "curriculum_volume_id": "term-a", "recent_limit": 2,
        })
        assert filtered.status_code == 200
        data = filtered.json()
        assert [row["session"]["id"] for row in data["recent_sessions"]] == [ids[3], ids[2]]
        assert data["current_session"]["curriculum_volume_id"] == "term-b"
        without_current = client.get("/api/workbench/overview", params={"curriculum_volume_id": "term-a"}).json()
        assert without_current["current_session"] is None
        assert without_current["personal_reports"] is None
        assert [row["session"]["id"] for row in without_current["recent_sessions"]] == [ids[2], ids[0]]
        current_unfiltered = client.get("/api/workbench/overview", params={"session_id": ids[3]}).json()
        for key in ["progress", "review", "anomalies"]:
            assert data[key] == current_unfiltered[key]
        def unavailable(*args):
            raise OSError("TEST-report-source-unavailable")
        monkeypatch.setattr("backend.personal_reports.personal_report_summary", unavailable)
        failed_reports = client.get("/api/workbench/overview", params={"session_id": ids[3]}).json()
        assert failed_reports["personal_reports"] is None
        assert failed_reports["progress"] == data["progress"]


def test_recent_review_counts_honor_teacher_confirmation_independent_of_selection(tmp_path):
    import sqlite3
    from tests.test_api_review_routes import _seed_review_db
    from backend.review.manual_review_service import ManualReviewService
    from backend.review.service import ReviewApplicationService, ReviewConfirmationInput
    from backend.workbench.service import WorkbenchService

    client, db, first_id, _result_id, _detail_id = _seed_review_db(tmp_path, result_count=3)
    original_session = db.sessions.get_grading_session(first_id)
    ids = [first_id] + [db.sessions.create_grading_session(
        f"TEST-review-home-{index}", original_session["rubric_path"], "answer.json",
    ) for index in (2, 3)]
    with sqlite3.connect(db.db_path) as conn:
        rows = conn.execute("SELECT id, paper_id FROM session_results ORDER BY id").fetchall()
        for session_id, (result_id, paper_id) in zip(ids, rows):
            conn.execute("UPDATE session_results SET session_id=? WHERE id=?", (session_id, result_id))
            conn.execute("UPDATE exam_papers SET session_id=? WHERE id=?", (session_id, paper_id))

    review_service = ReviewApplicationService(db, ManualReviewService(db))
    for session_id in ids[:2]:
        session = db.sessions.get_grading_session(session_id)
        item = review_service.list_items(session_id, session, requested_question_id="Q1")[0]
        assert item.needs_review is True
        review_service.confirm(session_id, session, "Q1", [ReviewConfirmationInput(
            result_id=item.result_id, detail_id=item.detail_id,
            score_awarded=item.score_awarded, expected_revision=item.revision,
        )])

    service = WorkbenchService(db, review_service, SimpleNamespace(list=lambda **kwargs: ([], 0)))
    expected = {ids[0]: 0, ids[1]: 0, ids[2]: 1}
    for selected in [None, *ids, ids[0]]:
        response = service.overview(selected, 5)
        assert {row["session"]["id"]: row["review"]["item_count"]
                for row in response["recent_sessions"]} == expected
        assert [row["progress"]["needs_human_review"] for row in response["recent_sessions"]] == [1, 1, 1]
        if selected is not None:
            assert response["review"]["item_count"] == expected[selected]
    client.close()
