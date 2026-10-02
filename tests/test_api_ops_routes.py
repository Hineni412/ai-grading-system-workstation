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


def test_optional_office_tools_do_not_mark_a_healthy_system_as_faulty(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.ops.service import OpsSelfCheckService
    paths = SimpleNamespace(version="test", data_root=tmp_path, databases_dir=tmp_path,
        backups_dir=tmp_path, logs_dir=tmp_path, reports_dir=tmp_path, outputs_dir=tmp_path,
        db_path=tmp_path / "grading.db", qb_db_path=tmp_path / "bank.db")
    service = OpsSelfCheckService(paths, tool_checker=lambda key: key in {"wps", "tectonic"})
    monkeypatch.setattr(service, "_database_check", lambda key, *_: {
        "key": key, "exists": True, "size_bytes": 1, "integrity": "ok",
        "migration_version": "current", "pending_migrations": 0, "status": "ok"})
    monkeypatch.setattr(service, "_api_configured", lambda: (True, None))
    snapshot = service.build_snapshot()
    assert snapshot["status"] == "ok"
    assert snapshot["warnings"] == []
    assert {tool["key"] for tool in snapshot["tools"] if tool["available"]} == {"wps", "tectonic"}


def test_storage_reuses_references_per_read_including_archived_owners(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.api.routers.ops import get_storage
    import backend.api.routers.sessions as sessions_router
    import session_cleanup
    shared = tmp_path / "shared.jpg"
    own = tmp_path / "own.jpg"
    shared.write_bytes(b"shared")
    own.write_bytes(b"own")
    records = [{"id": sid, "session_name": f"TEST-{sid}", "is_deleted": sid == 3,
                "status": "graded", "created_at": str(sid)} for sid in (1, 2, 3)]
    reads = []
    class Sessions:
        def list_grading_sessions(self, *, include_deleted=False):
            assert include_deleted
            return records
        def collect_session_storage_paths(self, sid):
            reads.append(sid)
            return [str(own if sid == 1 else shared)]
    observed = {}
    def snapshot(sid, *_args, shared_refs):
        observed[sid] = shared_refs
        return {"originals_state": "complete", "scan_bytes": 0, "page_bytes": 0,
                "annotation_bytes": 0, "release_bytes": 0, "clear_bytes": 0,
                "can_release_scans": False, "can_clear": False, "blocked_reason": None}
    monkeypatch.setattr(sessions_router, "_originals_snapshot", snapshot)
    result = get_storage(SimpleNamespace(sessions=Sessions()), tmp_path, None, None)
    assert reads == [1, 2, 3]
    assert [item["session_id"] for item in result["sessions"]] == [2, 1]
    assert observed == {1: {shared.resolve()}, 2: {own.resolve(), shared.resolve()}}
    assert shared.exists() and own.exists()


def test_system_database_check_reuses_readonly_gate_and_reports_corruption(tmp_path, monkeypatch):
    from backend.ops.service import OpsSelfCheckService
    from backend.schema_migrations import SchemaGateResult, SchemaVersionError
    import backend.ops.service as module
    candidate = tmp_path / "test.db"
    candidate.write_bytes(b"test-only")
    calls = []
    def inspect(target, source):
        calls.append((target, source))
        return SchemaGateResult(target, "version-test", (), ("pending-test",))
    monkeypatch.setattr(module, "inspect_schema_version", inspect)
    before = candidate.read_bytes()
    result = OpsSelfCheckService._database_check("grading", candidate, "grading")
    assert calls == [("grading", candidate)]
    assert result["status"] == "warning" and result["pending_migrations"] == 1
    assert candidate.read_bytes() == before
    def corrupt(*_):
        raise SchemaVersionError("test corruption")
    monkeypatch.setattr(module, "inspect_schema_version", corrupt)
    assert OpsSelfCheckService._database_check("grading", candidate, "grading")["integrity"] == "unavailable"
    assert OpsSelfCheckService._database_check("grading", tmp_path / "missing.db", "grading")["integrity"] == "missing"


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
