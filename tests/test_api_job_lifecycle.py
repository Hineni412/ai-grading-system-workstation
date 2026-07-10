from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _path_manager(tmp_path):
    data_root = tmp_path / "user_data"
    return SimpleNamespace(
        data_root=data_root,
        db_path=data_root / "databases" / "grading_system.db",
        reports_dir=data_root / "reports",
        exams_dir=data_root / "exams",
        templates_dir=data_root / "templates",
    )


def test_app_lifespan_owns_manager_and_recovers_interrupted_jobs(
    tmp_path,
    monkeypatch,
) -> None:
    from backend.api import dependencies
    from backend.api.app import create_app
    from backend.jobs.store import JobStore

    path_manager = _path_manager(tmp_path)
    store = JobStore(path_manager.db_path)
    queued = store.create_job("report_export", {})
    running = store.create_job("scan_analysis", {})
    succeeded = store.create_job("grading_run", {})
    assert store.mark_running(running.id) is True
    store.finish(succeeded.id, "succeeded")
    monkeypatch.setattr(dependencies, "get_path_manager", lambda: path_manager)

    api = create_app()
    with TestClient(api) as client:
        manager = api.state.job_manager
        assert manager.is_shutdown is False
        assert store.get_job(queued.id).status == "failed"
        assert store.get_job(running.id).status == "failed"
        assert store.get_job(succeeded.id).status == "succeeded"
        response = client.get(f"/api/jobs/{queued.id}")
        assert response.status_code == 200
        assert response.json()["status"] == "failed"

    assert manager.is_shutdown is True
    assert not hasattr(api.state, "job_manager")


def test_app_lifespan_does_not_own_overridden_job_manager(
    tmp_path,
    monkeypatch,
) -> None:
    from backend.api import dependencies
    from backend.api.app import create_app
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    external_manager = JobManager(
        JobStore(tmp_path / "external" / "jobs.db"),
        max_workers=1,
    )
    api = create_app()
    api.dependency_overrides[dependencies.get_job_manager] = lambda: external_manager

    def fail_if_created():
        raise AssertionError("default JobManager must not be created")

    monkeypatch.setattr(dependencies, "create_job_manager", fail_if_created, raising=False)
    try:
        with TestClient(api):
            assert not hasattr(api.state, "job_manager")
            assert external_manager.is_shutdown is False

        assert external_manager.is_shutdown is False
    finally:
        external_manager.shutdown()
