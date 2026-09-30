from __future__ import annotations

from datetime import UTC, datetime
import threading

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas.ops import (
    OpsDatabaseCheck,
    OpsDirectoryCheck,
    OpsSelfCheckResponse,
    OpsToolCheck,
)
from backend.api.dependencies import (
    get_job_manager,
    get_ops_self_check_service,
    get_ops_write_service,
)
from backend.api.app import create_app
from backend.jobs.store import JobRecord
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore


def _snapshot() -> OpsSelfCheckResponse:
    return OpsSelfCheckResponse(
        version="v-test",
        status="warning",
        api_configured=False,
        directories=[
            OpsDirectoryCheck(key="data", exists=True, writable=True, status="ok")
        ],
        databases=[
            OpsDatabaseCheck(
                key="grading",
                exists=True,
                size_bytes=1024,
                integrity="ok",
                migration_version="1",
                pending_migrations=0,
                status="ok",
            )
        ],
        tools=[OpsToolCheck(key="pdflatex", available=False, status="warning")],
        warnings=["tool_unavailable:pdflatex"],
    )


class _FakeOpsService:
    def __init__(self) -> None:
        self.snapshot_calls = 0
        self.backup_limits: list[int] = []

    def build_snapshot(self):
        self.snapshot_calls += 1
        return _snapshot().model_dump()

    def list_backups(self, limit: int):
        self.backup_limits.append(limit)
        return {"items": [], "returned": 0, "limit": limit}


@pytest.fixture
def ops_client() -> tuple[TestClient, _FakeOpsService]:
    service = _FakeOpsService()
    app = create_app()
    app.dependency_overrides[get_ops_self_check_service] = lambda: service
    return TestClient(app), service


def _job_record(job_type: str = "ops_backup") -> JobRecord:
    now = "2026-07-12T12:00:00"
    return JobRecord(
        id=23,
        job_type=job_type,
        payload={
            "operation_id": "11111111-1111-4111-8111-111111111111",
            "operation": "backup",
        },
        result={},
        status="queued",
        progress=0.0,
        stage="",
        detail="",
        error=None,
        cancel_requested=False,
        created_at=now,
        started_at=None,
        updated_at=now,
        finished_at=None,
    )


class _FakeOpsWriteService:
    def __init__(self) -> None:
        self.uploaded = b""
        self.preflight_operation = ""
        self.submitted_token = ""
        self.cancelled_operation = ""

    async def stage_import_upload(self, *, filename: str, chunks):
        self.uploaded = b"".join([chunk async for chunk in chunks])
        return {
            "upload_id": "a" * 32,
            "filename": filename,
            "size_bytes": len(self.uploaded),
            "sha256": "b" * 64,
        }

    def preflight(self, request):
        self.preflight_operation = request.operation
        return {
            "operation": request.operation,
            "confirmation_token": "confirm-token",
            "expires_at": datetime(2026, 7, 12, 12, 5, tzinfo=UTC),
            "requires_restart": request.operation
            in {"restore", "migration", "transfer_import"},
            "summary": {
                "file_count": 2,
                "total_size_bytes": 128,
                "database_count": 2,
                "warnings": [],
            },
        }

    def submit(self, confirmation_token: str, _manager):
        self.submitted_token = confirmation_token
        return _job_record()

    def operation_status(self, operation_id: str):
        return {
            "operation_id": operation_id,
            "operation": "restore",
            "status": "restart_required",
            "result_code": "prepared_restart_required",
            "created_at": "2026-07-12T12:00:00",
            "updated_at": "2026-07-12T12:01:00",
            "recovery": {
                "code": "restart_required",
                "backup_filename": "backup_safe.zip",
            },
        }

    def cancel_operation(self, operation_id: str):
        self.cancelled_operation = operation_id
        payload = self.operation_status(operation_id)
        payload["status"] = "cancelled"
        payload["result_code"] = "cancelled_before_apply"
        return payload


@pytest.fixture
def ops_write_client() -> tuple[TestClient, _FakeOpsWriteService]:
    service = _FakeOpsWriteService()
    app = create_app()
    app.dependency_overrides[get_ops_write_service] = lambda: service
    app.dependency_overrides[get_job_manager] = lambda: object()
    return TestClient(app), service


def test_concurrent_ops_api_submissions_return_one_busy_conflict(tmp_path) -> None:
    from tests.test_ops_write_service import _paths, _request, _service

    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    release = threading.Event()
    manager.register("ops_backup", lambda _context: release.wait(timeout=5) or {})
    tokens = [
        str(
            service.preflight(_request("backup", reason="manual"))["confirmation_token"]
        )
        for _ in range(2)
    ]
    app = create_app()
    app.dependency_overrides[get_ops_write_service] = lambda: service
    app.dependency_overrides[get_job_manager] = lambda: manager
    client = TestClient(app)
    barrier = threading.Barrier(2)
    responses = []

    def submit(token: str) -> None:
        barrier.wait()
        responses.append(
            client.post("/api/ops/jobs", json={"confirmation_token": token})
        )

    threads = [threading.Thread(target=submit, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    release.set()
    manager.shutdown()

    assert sorted(response.status_code for response in responses) == [
        202,
        409,
    ], [response.json() for response in responses]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "ops_operation_busy"
