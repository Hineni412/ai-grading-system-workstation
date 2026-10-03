from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.repositories.grading_database import open_grading_repositories


def test_overview_filters_before_limit_and_keeps_current_exam(tmp_path, monkeypatch):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db, get_workbench_service, get_scan_grading_workspace, get_reports_dir,
    )
    from backend.api.routers import workbench
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
    monkeypatch.setattr(workbench, "current_manual_context", lambda *args: None)
    with TestClient(app, follow_redirects=False) as client:
        unfiltered = client.get("/api/workbench/overview", params={"session_id": ids[0]})
        assert unfiltered.status_code == 200
        full = unfiltered.json()
        assert full["current_session"]["curriculum_volume_id"] == "term-a"
        assert full["personal_reports"] == dict(current=8, stale=2, missing=3)
        assert [row["session"]["id"] for row in full["recent_sessions"]] == ids[::-1]
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
