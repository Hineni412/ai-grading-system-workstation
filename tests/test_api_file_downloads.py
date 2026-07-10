from __future__ import annotations

import warnings
from pathlib import Path

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


@pytest.fixture
def file_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager, get_reports_dir
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    with TestClient(app) as client:
        try:
            yield client, store, reports_dir
        finally:
            manager.shutdown()


def _finish_job(
    store,
    job_type: str,
    *,
    status: str = "succeeded",
    result: dict | None = None,
    error: str | None = None,
):
    job = store.create_job(job_type, {"session_id": 1})
    assert store.mark_running(job.id) is True
    store.finish(job.id, status, error=error, result=result)
    loaded = store.get_job(job.id)
    assert loaded is not None
    return loaded


def test_report_job_download_streams_controlled_file(file_client) -> None:
    client, store, reports_dir = file_client
    report = reports_dir / "report.xlsx"
    report.write_bytes(b"xlsx")
    job = _finish_job(
        store,
        "report_export",
        result={
            "session_id": 1,
            "file_path": str(report),
            "filename": report.name,
        },
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.content == b"xlsx"
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "report.xlsx" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_job_public_result_recursively_removes_internal_paths(file_client) -> None:
    client, store, _reports_dir = file_client
    job = _finish_job(
        store,
        "grading_run",
        result={
            "session_id": 1,
            "summary": {"graded": 1},
            "artifact_path": "C:/private/result.json",
            "nested": {
                "keep": "value",
                "preview_path": "C:/private/preview.jpg",
                "paths": ["C:/private/one", "C:/private/two"],
            },
        },
    )

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["result"] == {
        "session_id": 1,
        "summary": {"graded": 1},
        "nested": {"keep": "value"},
    }
    assert "C:/private" not in str(response.json()["result"])


def test_failed_job_public_error_does_not_expose_internal_path(
    file_client,
    tmp_path: Path,
) -> None:
    client, store, _reports_dir = file_client
    private_path = tmp_path / "private" / "scan.json"
    job = _finish_job(
        store,
        "scan_analysis",
        status="failed",
        error=f"cannot open {private_path}",
    )

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["error"] == "Job failed; see local logs for details."
    assert str(private_path) not in str(response.json())
    assert store.get_job(job.id).error == f"cannot open {private_path}"


def test_job_download_requires_existing_job(file_client) -> None:
    client, _store, _reports_dir = file_client

    response = client.get("/api/jobs/404/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_job_download_requires_succeeded_status(file_client, status: str) -> None:
    client, store, reports_dir = file_client
    report = reports_dir / f"{status}.xlsx"
    report.write_bytes(b"xlsx")
    job = _finish_job(
        store,
        "report_export",
        status=status,
        result={"file_path": str(report)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_file_unavailable"
    assert response.headers["cache-control"] == "no-store"


def test_job_download_rejects_queued_status(file_client) -> None:
    client, store, _reports_dir = file_client
    job = store.create_job("report_export", {"session_id": 1})

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_file_unavailable"
    assert response.headers["cache-control"] == "no-store"


def test_job_download_rejects_unsupported_job_type(file_client) -> None:
    client, store, reports_dir = file_client
    report = reports_dir / "scan.xlsx"
    report.write_bytes(b"xlsx")
    job = _finish_job(
        store,
        "scan_analysis",
        result={"file_path": str(report)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_file_not_found"
    assert response.headers["cache-control"] == "no-store"


def test_job_download_marks_missing_result_path_expired(file_client) -> None:
    client, store, _reports_dir = file_client
    job = _finish_job(
        store,
        "report_export",
        result={"session_id": 1, "filename": "missing.xlsx"},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "job_file_expired"
    assert response.headers["cache-control"] == "no-store"


def test_job_download_marks_deleted_file_expired_without_path_leak(file_client) -> None:
    client, store, reports_dir = file_client
    missing = reports_dir / "deleted.xlsx"
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": str(missing)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "job_file_expired"
    assert str(missing) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_job_download_rejects_absolute_path_outside_reports(file_client, tmp_path: Path) -> None:
    client, store, _reports_dir = file_client
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"xlsx")
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": str(outside)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "job_file_forbidden"
    assert str(outside) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_job_download_rejects_relative_path_traversal(file_client, tmp_path: Path) -> None:
    client, store, _reports_dir = file_client
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"xlsx")
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": "reports/../outside.xlsx"},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "job_file_forbidden"
    assert str(outside) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix", [".exe", ".html", ".svg"])
def test_job_download_rejects_disallowed_extension(file_client, suffix: str) -> None:
    client, store, reports_dir = file_client
    unsafe = reports_dir / f"report{suffix}"
    unsafe.write_bytes(b"unsafe")
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": str(unsafe)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "job_file_type_not_supported"
    assert str(unsafe) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_job_download_rejects_resolved_escape_deterministically(
    file_client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, reports_dir = file_client
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"xlsx")
    link = reports_dir / "linked.xlsx"
    link.write_bytes(b"placeholder")
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path: Path, *args, **kwargs) -> Path:
        if path == link:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_escape)
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": str(link)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "job_file_forbidden"
