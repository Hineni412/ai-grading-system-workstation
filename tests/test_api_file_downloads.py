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


@pytest.fixture
def training_file_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_job_manager,
        get_outputs_dir,
        get_reports_dir,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    outputs_dir = tmp_path / "outputs"
    reports_dir.mkdir()
    (outputs_dir / "training").mkdir(parents=True)
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    app.dependency_overrides[get_outputs_dir] = lambda: outputs_dir
    with TestClient(app) as client:
        try:
            yield client, store, outputs_dir / "training"
        finally:
            manager.shutdown()


@pytest.fixture
def ops_file_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_backups_dir,
        get_job_manager,
        get_outputs_dir,
        get_reports_dir,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    backups_dir = tmp_path / "backups"
    outputs_dir = tmp_path / "outputs"
    reports_dir.mkdir()
    backups_dir.mkdir()
    (outputs_dir / "ops").mkdir(parents=True)
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    app.dependency_overrides[get_backups_dir] = lambda: backups_dir
    app.dependency_overrides[get_outputs_dir] = lambda: outputs_dir
    with TestClient(app) as client:
        try:
            yield client, store, backups_dir, outputs_dir / "ops"
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


def test_training_job_file_service_uses_training_output_root(tmp_path: Path) -> None:
    from backend.files.service import JobFileService
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    training_dir = tmp_path / "outputs" / "training"
    training_dir.mkdir(parents=True)
    exported = training_dir / "job-1" / "practice.md"
    exported.parent.mkdir()
    exported.write_text("practice", encoding="utf-8")
    store = JobStore(tmp_path / "jobs.db")
    job = _finish_job(
        store,
        "training_export",
        result={"task_id": 7, "file_path": str(exported)},
    )

    resolved = JobFileService(
        reports_dir,
        training_outputs_dir=training_dir,
    ).resolve(job)

    assert resolved.path == exported.resolve()
    assert resolved.media_type == "text/markdown"


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        (
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (".md", "text/markdown; charset=utf-8"),
        (".zip", "application/zip"),
    ],
)
def test_training_job_download_streams_controlled_file(
    training_file_client,
    suffix: str,
    media_type: str,
) -> None:
    client, store, training_dir = training_file_client
    exported = training_dir / "job-1" / f"practice{suffix}"
    exported.parent.mkdir()
    exported.write_bytes(b"training")
    job = _finish_job(
        store,
        "training_export",
        result={
            "task_id": 7,
            "export_ids": [31],
            "file_path": str(exported),
            "filename": exported.name,
        },
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.content == b"training"
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"] == "no-store"
    assert exported.name in response.headers["content-disposition"]
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert not str(loaded.result.get("file_path") or "").strip()
    assert loaded.result.get("filename") == exported.name
    assert not exported.exists()
    public = client.get(f"/api/jobs/{job.id}")
    assert public.status_code == 200
    assert "download_url" not in public.json()["result"]
    second = client.get(f"/api/jobs/{job.id}/download")
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "job_file_expired"


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
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert not str(loaded.result.get("file_path") or "").strip()
    assert not report.exists()
    public = client.get(f"/api/jobs/{job.id}")
    assert "download_url" not in public.json()["result"]
    second = client.get(f"/api/jobs/{job.id}/download")
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "job_file_expired"


def test_annotated_original_pdf_download_preserves_chinese_filename(file_client) -> None:
    client, store, reports_dir = file_client
    report = reports_dir / "七年级期中_批注原卷.pdf"
    report.write_bytes(b"%PDF-1.7")
    job = _finish_job(
        store,
        "report_export",
        result={
            "session_id": 1,
            "report_type": "annotated_original_pdf",
            "file_path": str(report),
            "filename": report.name,
        },
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.7"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert "filename*=" in response.headers["content-disposition"]


@pytest.mark.parametrize(
    ("job_type", "root_index"),
    [("ops_backup", 2), ("ops_transfer_export", 3)],
)
def test_ops_job_download_uses_operation_specific_root(
    ops_file_client,
    job_type: str,
    root_index: int,
) -> None:
    client, store, backups_dir, ops_outputs_dir = ops_file_client
    roots = {2: backups_dir, 3: ops_outputs_dir}
    exported = roots[root_index] / f"{job_type}.zip"
    exported.write_bytes(b"zip")
    job = _finish_job(
        store,
        job_type,
        result={
            "operation_id": "11111111-1111-4111-8111-111111111111",
            "operation": "backup" if job_type == "ops_backup" else "transfer_export",
            "outcome": "published",
            "file_path": str(exported),
            "filename": exported.name,
        },
    )

    response = client.get(f"/api/jobs/{job.id}/download")
    public = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert response.content == b"zip"
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert exported.exists()
    assert public.json()["result"] == {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "operation": "backup" if job_type == "ops_backup" else "transfer_export",
        "outcome": "published",
        "filename": exported.name,
        "download_url": f"/api/jobs/{job.id}/download",
    }


def test_ops_job_download_rejects_other_ops_root(ops_file_client) -> None:
    client, store, _backups_dir, ops_outputs_dir = ops_file_client
    wrong_root = ops_outputs_dir / "backup.zip"
    wrong_root.write_bytes(b"zip")
    job = _finish_job(
        store,
        "ops_backup",
        result={"file_path": str(wrong_root), "filename": wrong_root.name},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "job_file_forbidden"


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


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [(".html", "text/html"), (".zip", "application/zip")],
)
def test_job_download_allows_analysis_report_files(
    file_client, suffix: str, media_type: str
) -> None:
    client, store, reports_dir = file_client
    report = reports_dir / f"report{suffix}"
    report.write_bytes(b"payload")
    job = _finish_job(
        store,
        "report_export",
        result={"file_path": str(report)},
    )

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.content == b"payload"


@pytest.mark.parametrize("suffix", [".exe", ".svg"])
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


# ---------------------------------------------------------------------------
# 个人学情报告留存：下载不删除、手动删除释放空间
# ---------------------------------------------------------------------------


def _finish_typed_report_job(store, report_type: str, file_path: Path):
    job = store.create_job(
        "report_export",
        {"session_id": 1, "report_type": report_type, "score_revision": "rev-1"},
    )
    assert store.mark_running(job.id) is True
    store.finish(
        job.id,
        "succeeded",
        result={
            "session_id": 1,
            "file_path": str(file_path),
            "filename": file_path.name,
        },
    )
    loaded = store.get_job(job.id)
    assert loaded is not None
    return loaded


def test_personal_analysis_report_download_is_retained(file_client) -> None:
    """个人学情报告 zip 下载后留存在本机，可重复下载。"""
    client, store, reports_dir = file_client
    report = reports_dir / "personal.zip"
    report.write_bytes(b"zip-content")
    job = _finish_typed_report_job(store, "personal_analysis_html", report)

    first = client.get(f"/api/jobs/{job.id}/download")
    assert first.status_code == 200
    assert report.exists()
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert str(loaded.result.get("file_path") or "").strip()

    second = client.get(f"/api/jobs/{job.id}/download")
    assert second.status_code == 200
    assert report.exists()


def test_score_excel_download_is_still_consumed(file_client) -> None:
    """成绩 Excel 等非留存类型维持下载后即删。"""
    client, store, reports_dir = file_client
    report = reports_dir / "scores.xlsx"
    report.write_bytes(b"xlsx-content")
    job = _finish_typed_report_job(store, "score_excel", report)

    response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    assert not report.exists()
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert not str(loaded.result.get("file_path") or "").strip()
    second = client.get(f"/api/jobs/{job.id}/download")
    assert second.status_code == 410


def test_delete_retained_file_removes_and_is_idempotent(tmp_path: Path) -> None:
    from backend.files.service import JobFileService
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / "personal.zip"
    report.write_bytes(b"zip-content")
    store = JobStore(tmp_path / "jobs.db")
    job = _finish_typed_report_job(store, "personal_analysis_html", report)
    service = JobFileService(reports_dir)

    freed = service.delete_retained_file(job, store)

    assert freed == len(b"zip-content")
    assert not report.exists()
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert not str(loaded.result.get("file_path") or "").strip()
    # 幂等：已删除再删返回 None。
    assert service.delete_retained_file(loaded, store) is None


def test_delete_retained_file_rejects_non_retained_types(tmp_path: Path) -> None:
    from backend.files.service import JobFileNotFound, JobFileService
    from backend.jobs.store import JobStore

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    report = reports_dir / "scores.xlsx"
    report.write_bytes(b"xlsx-content")
    store = JobStore(tmp_path / "jobs.db")
    job = _finish_typed_report_job(store, "score_excel", report)

    with pytest.raises(JobFileNotFound):
        JobFileService(reports_dir).delete_retained_file(job, store)
    assert report.exists()
