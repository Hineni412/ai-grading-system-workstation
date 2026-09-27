from __future__ import annotations

import sqlite3
import logging
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI

from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.schema_migrations import ensure_schema_current
from backend.workspaces.contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelPolicyError,
    WorkspaceModelRequest,
)
from backend.workspaces.registry import (
    WorkspaceRegistrationError,
    WorkspaceRegistry,
    load_default_workspace_registry,
)
from path_manager import PathManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _paths(tmp_path: Path) -> PathManager:
    paths = PathManager()
    paths._project_root = tmp_path / "project"
    paths._data_root = tmp_path / "user_data"
    return paths


def _context(paths: PathManager, module_id: str) -> WorkspaceContext:
    return WorkspaceContext(
        module_id=module_id,
        root=paths.workspace_dir(module_id),
        paths=paths,
    )


def _feature(
    *,
    module_id: str = "other-workspace",
    api_prefix: str = "/api/other-workspace",
    job_prefix: str = "other_workspace",
    enabled: bool = True,
    **changes,
) -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id=module_id,
        api_prefix=api_prefix,
        job_prefix=job_prefix,
        enabled=enabled,
        **changes,
    )


def test_failed_module_migration_rolls_back_and_blocks_startup(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    migrations = paths.project_root / "migrations" / "other_workspace"
    migrations.mkdir(parents=True)
    (migrations / "000_broken.sql").write_text(
        (
            "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);"
            "INSERT INTO missing_table(id) VALUES (1);"
        ),
        encoding="utf-8",
    )
    service_calls: list[str] = []

    def migration_provider(context):
        return WorkspaceMigrationPlan(
            target="other_workspace",
            database_path=context.root / "other_workspace.db",
            migrations_dir=migrations,
            backup_dir=context.root / "backups",
            preflight=lambda: None,
        )

    registry = WorkspaceRegistry(
        [
            _feature(
                migration_provider=migration_provider,
                service_factory=lambda _context: service_calls.append("called"),
            )
        ],
        paths=paths,
    )

    with pytest.raises(
        WorkspaceRegistrationError,
        match="other-workspace migration failed",
    ):
        registry.run_migrations()

    database = paths.workspace_dir("other-workspace") / "other_workspace.db"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='should_rollback'"
            ).fetchone()
            is None
        )
    assert service_calls == []


class _FakeGateway:
    def __init__(self) -> None:
        self.calls = []
        self.diagnostic_sink = object()
        self.trace_sink = object()
        self.usage_sink = object()

    def chat_completions(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
            }
        }

    def responses(self, **kwargs):
        return self.chat_completions(**kwargs)


def test_workspace_model_operation_claim_survives_new_gateway_instance(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    request = WorkspaceModelRequest(
        purpose="lesson_analysis",
        data_classification="confidential",
        operation_id="op-across-restart",
    )
    first_gateway = _FakeGateway()
    first_policy = WorkspaceModelGateway(
        context=_context(paths, "other-workspace"),
        gateway=first_gateway,
    )
    first_policy.chat_completions(
        request=request,
        client=object(),
        model="safe-model",
        kwargs={},
    )

    second_gateway = _FakeGateway()
    restarted_policy = WorkspaceModelGateway(
        context=_context(paths, "other-workspace"),
        gateway=second_gateway,
    )
    with pytest.raises(WorkspaceModelPolicyError, match="already used"):
        restarted_policy.chat_completions(
            request=request,
            client=object(),
            model="safe-model",
            kwargs={},
        )

    assert len(first_gateway.calls) == 1
    assert second_gateway.calls == []
