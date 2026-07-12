from __future__ import annotations

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
)
from backend.api.dependencies import get_ops_self_check_service
from backend.api.app import create_app


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
