from __future__ import annotations

from datetime import UTC, datetime
import threading

from pydantic import ValidationError
import pytest
from fastapi.testclient import TestClient

from backend.api.schemas.ops import (
    OpsBackupItem,
    OpsBackupListResponse,
    OpsDatabaseCheck,
    OpsDirectoryCheck,
    OpsSelfCheckResponse,
    OpsToolCheck,
    OpsJobSubmitRequest,
    OpsPreflightResponse,
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
from backend.api.routers.ops import _ops_write_api_error
from backend.ops.journal import OpsOperationBusy


def _snapshot() -> OpsSelfCheckResponse:
    return OpsSelfCheckResponse(
        version="v-test",
        status="warning",
        api_configured=False,
        directories=[
            OpsDirectoryCheck(
                key="data", exists=True, writable=True, status="ok"
            )
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


def test_ops_schema_uses_only_explicit_path_free_fields() -> None:
    payload = _snapshot().model_dump()
    assert set(payload) == {
        "version",
        "status",
        "api_configured",
        "directories",
        "databases",
        "tools",
        "warnings",
    }
    assert set(payload["directories"][0]) == {"key", "exists", "writable", "status"}
    assert set(payload["databases"][0]) == {
        "key",
        "exists",
        "size_bytes",
        "integrity",
        "migration_version",
        "pending_migrations",
        "status",
    }
    assert set(payload["tools"][0]) == {"key", "available", "status"}


def test_ops_schema_rejects_extra_internal_fields() -> None:
    with pytest.raises(ValidationError):
        OpsDirectoryCheck(
            key="data",
            exists=True,
            writable=True,
            status="ok",
            path="C:/private/user_data",
        )


def test_ops_backup_schema_is_bounded_and_path_free() -> None:
    response = OpsBackupListResponse(
        items=[
            OpsBackupItem(
                kind="zip",
                filename="backup_20260712_120000_manual.zip",
                created_at="2026-07-12T12:00:00",
                reason="manual",
                size_bytes=2048,
            )
        ],
        returned=1,
        limit=50,
    )
    assert set(response.model_dump()["items"][0]) == {
        "kind",
        "filename",
        "created_at",
        "reason",
        "size_bytes",
    }
    with pytest.raises(ValidationError):
        OpsBackupListResponse(items=[], returned=0, limit=101)


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


def test_ops_routes_return_explicit_safe_projection(ops_client) -> None:
    client, service = ops_client
    response = client.get("/api/ops/self-check")
    assert response.status_code == 200
    assert response.json()["version"] == "v-test"
    assert service.snapshot_calls == 1
    assert "path" not in response.text.lower()


def test_ops_backups_enforces_and_forwards_bounded_limit(ops_client) -> None:
    client, service = ops_client
    assert client.get("/api/ops/backups?limit=0").status_code == 422
    assert client.get("/api/ops/backups?limit=101").status_code == 422
    response = client.get("/api/ops/backups?limit=7")
    assert response.status_code == 200
    assert response.json() == {"items": [], "returned": 0, "limit": 7}
    assert service.backup_limits == [7]


def test_ops_registers_no_write_operations(ops_client) -> None:
    client, _service = ops_client
    for path in ("/api/ops/self-check", "/api/ops/backups"):
        assert client.post(path).status_code == 405
        assert client.put(path).status_code == 405
        assert client.delete(path).status_code == 405


@pytest.mark.parametrize("operation", ["snapshot", "backups_extra_field"])
def test_ops_service_failures_return_stable_path_free_503(operation: str) -> None:
    class FailingService(_FakeOpsService):
        def build_snapshot(self):
            raise RuntimeError("C:/private api_key=sk-secret")

        def list_backups(self, limit: int):
            return {
                "items": [],
                "returned": 0,
                "limit": limit,
                "path": "C:/private api_key=sk-secret",
            }

    service = FailingService()
    app = create_app()
    app.dependency_overrides[get_ops_self_check_service] = lambda: service
    client = TestClient(app)
    path = "/api/ops/self-check" if operation == "snapshot" else "/api/ops/backups"

    response = client.get(path)

    assert response.status_code == 503
    assert response.json()["error"]["code"] in {
        "ops_self_check_unavailable",
        "ops_backup_list_unavailable",
    }
    assert "c:/private" not in response.text.lower()
    assert "sk-secret" not in response.text


def _job_record(job_type: str = "ops_backup") -> JobRecord:
    now = "2026-07-12T12:00:00"
    return JobRecord(
        id=23,
        job_type=job_type,
        payload={"operation_id": "11111111-1111-4111-8111-111111111111", "operation": "backup"},
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
            "requires_restart": request.operation in {"restore", "migration", "transfer_import"},
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
            "recovery": {"code": "restart_required", "backup_filename": "backup_safe.zip"},
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


def test_ops_write_schemas_reject_internal_fields() -> None:
    with pytest.raises(ValidationError):
        OpsJobSubmitRequest(
            confirmation_token="confirm-token",
            path="C:/private/user_data",
        )
    response = OpsPreflightResponse.model_validate(
        {
            "operation": "backup",
            "confirmation_token": "confirm-token",
            "expires_at": "2026-07-12T12:05:00Z",
            "requires_restart": False,
            "summary": {"file_count": 1, "warnings": []},
        }
    )
    assert "path" not in response.model_dump()


def test_ops_import_upload_streams_to_write_service(ops_write_client) -> None:
    client, service = ops_write_client

    response = client.post(
        "/api/ops/transfer-import/uploads?filename=portable.zip",
        content=b"zip-bytes",
        headers={"content-type": "application/zip"},
    )

    assert response.status_code == 201
    assert response.json()["upload_id"] == "a" * 32
    assert service.uploaded == b"zip-bytes"
    assert "path" not in response.text.casefold()


@pytest.mark.parametrize("field", ["path", "destination", "command", "sql", "migrations_dir"])
def test_ops_preflight_rejects_dangerous_extra_fields(
    ops_write_client,
    field: str,
) -> None:
    client, _service = ops_write_client

    response = client.post(
        "/api/ops/preflights",
        json={"operation": "backup", "reason": "manual", field: "x"},
    )

    assert response.status_code == 422


def test_ops_preflight_and_submit_use_dedicated_contract(ops_write_client) -> None:
    client, service = ops_write_client
    preflight = client.post(
        "/api/ops/preflights",
        json={"operation": "backup", "reason": "manual"},
    )

    assert preflight.status_code == 200
    assert preflight.json()["confirmation_token"] == "confirm-token"
    assert service.preflight_operation == "backup"

    submitted = client.post(
        "/api/ops/jobs",
        json={"confirmation_token": "confirm-token"},
    )

    assert submitted.status_code == 202
    assert submitted.json()["job_type"] == "ops_backup"
    assert submitted.json()["payload"] == {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "operation": "backup",
    }
    assert service.submitted_token == "confirm-token"


def test_ops_operation_status_and_cancel_are_path_free(ops_write_client) -> None:
    client, service = ops_write_client
    operation_id = "11111111-1111-4111-8111-111111111111"

    loaded = client.get(f"/api/ops/operations/{operation_id}")
    cancelled = client.post(f"/api/ops/operations/{operation_id}/cancel")

    assert loaded.status_code == 200
    assert loaded.json()["status"] == "restart_required"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert service.cancelled_operation == operation_id
    assert "c:/" not in (loaded.text + cancelled.text).casefold()


def test_ops_cancel_after_apply_started_maps_to_stable_conflict() -> None:
    error = _ops_write_api_error(OpsOperationBusy("already applying"))

    assert error.status_code == 409
    assert error.code == "ops_operation_busy"


def test_concurrent_ops_api_submissions_return_one_busy_conflict(tmp_path) -> None:
    from tests.test_ops_write_service import _paths, _request, _service

    paths = _paths(tmp_path)
    service = _service(tmp_path, paths)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    release = threading.Event()
    manager.register("ops_backup", lambda _context: release.wait(timeout=5) or {})
    tokens = [
        str(service.preflight(_request("backup", reason="manual"))["confirmation_token"])
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
        responses.append(client.post("/api/ops/jobs", json={"confirmation_token": token}))

    threads = [threading.Thread(target=submit, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    release.set()
    manager.shutdown()

    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "ops_operation_busy"
