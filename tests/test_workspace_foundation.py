from __future__ import annotations

import sqlite3
import logging
import shutil
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.class_teacher.feature import create_workspace_feature
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
from update_tools.backup_core import _safe_restore_destination


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
    module_id: str = "teaching-prep",
    api_prefix: str = "/api/teaching-prep",
    job_prefix: str = "teaching_prep",
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


def test_activated_class_teacher_shell_has_no_filesystem_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_GRADING_TEACHING_PREP_ENABLED", "0")
    paths = _paths(tmp_path)

    registry = load_default_workspace_registry(paths)
    api = FastAPI()
    registry.include_routers(api)
    registry.run_migrations()
    services = registry.create_services()

    assert [
        feature.module_id for feature in registry.enabled_features
    ] == ["class-teacher"]
    assert set(services) == {"class-teacher"}
    assert not (paths.data_root / "workspaces").exists()


def test_existing_class_teacher_vault_is_migrated_before_service_creation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths._project_root = PROJECT_ROOT
    legacy_migrations = tmp_path / "legacy-student-affairs-migrations"
    legacy_migrations.mkdir()
    for migration in sorted(
        (PROJECT_ROOT / "migrations" / "student_affairs").glob("*.sql")
    ):
        if int(migration.name.split("_", 1)[0]) <= 16:
            shutil.copy2(migration, legacy_migrations / migration.name)
    root = paths.workspace_dir("class-teacher")
    ensure_schema_current(
        "student_affairs",
        root / "student_affairs.db",
        migrations_dir=legacy_migrations,
        backup_dir=root / "backups",
        logger_override=logging.getLogger("test.class-teacher.migration"),
    )

    registry = WorkspaceRegistry(
        [create_workspace_feature()],
        paths=paths,
    )
    registry.run_migrations()
    services = registry.create_services()

    with sqlite3.connect(root / "student_affairs.db") as connection:
        assert connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name = 'initialization_recovery_receipts'
            """
        ).fetchone() == ("initialization_recovery_receipts",)
        confirmation_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(confirmation_claims)"
            )
        }
    assert {"state", "target_kind", "target_id", "updated_at"} <= (
        confirmation_columns
    )
    assert set(services) == {"class-teacher"}


def test_existing_main_class_teacher_vault_accepts_new_tail_migration(
    tmp_path: Path,
) -> None:
    """A released 018 history must remain a prefix of the next manifest."""

    migration_root = PROJECT_ROOT / "migrations" / "student_affairs"
    released_manifest = tmp_path / "released-student-affairs-migrations"
    released_manifest.mkdir()
    for migration in sorted(migration_root.glob("*.sql")):
        if int(migration.stem.split("_", 1)[0]) > 18:
            continue
        shutil.copy2(migration, released_manifest / migration.name)

    database = tmp_path / "class-teacher" / "student_affairs.db"
    ensure_schema_current(
        "student_affairs",
        database,
        migrations_dir=released_manifest,
        backup_dir=tmp_path / "released-backups",
        logger_override=logging.getLogger("test.class-teacher.released-migration"),
    )

    result = ensure_schema_current(
        "student_affairs",
        database,
        migrations_dir=migration_root,
        backup_dir=tmp_path / "upgrade-backups",
        logger_override=logging.getLogger("test.class-teacher.upgrade-migration"),
    )

    assert result.applied[-1] == "025_handoff_adoption_receipts"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'student_card_entries'"
        ).fetchone() == ("student_card_entries",)


@pytest.mark.parametrize(
    ("second", "message"),
    [
        (_feature(), "duplicate workspace module ID"),
        (
            _feature(
                module_id="class-teacher",
                api_prefix="/api/teaching-prep",
                job_prefix="class_teacher",
            ),
            "duplicate workspace API prefix",
        ),
        (
            _feature(
                module_id="class-teacher",
                api_prefix="/api/class-teacher",
                job_prefix="teaching_prep",
            ),
            "duplicate workspace Job prefix",
        ),
    ],
)
def test_registry_rejects_duplicate_feature_identity(
    tmp_path: Path,
    second: WorkspaceFeature,
    message: str,
) -> None:
    with pytest.raises(WorkspaceRegistrationError, match=message):
        WorkspaceRegistry([_feature(), second], paths=_paths(tmp_path))


def test_registry_uses_stable_module_id_order(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(
        [
            _feature(
                module_id="teaching-prep",
                api_prefix="/api/teaching-prep",
                job_prefix="teaching_prep",
            ),
            _feature(
                module_id="class-teacher",
                api_prefix="/api/class-teacher",
                job_prefix="class_teacher",
            ),
        ],
        paths=_paths(tmp_path),
    )

    assert [feature.module_id for feature in registry.features] == [
        "class-teacher",
        "teaching-prep",
    ]


def test_enabled_router_is_registered_below_reserved_prefix(
    tmp_path: Path,
) -> None:
    router = APIRouter()

    @router.get("/status")
    def status() -> dict[str, bool]:
        return {"ok": True}

    registry = WorkspaceRegistry(
        [_feature(router_factory=lambda: router)],
        paths=_paths(tmp_path),
    )
    api = FastAPI()
    registry.include_routers(api)

    assert TestClient(api).get("/api/teaching-prep/status").json() == {
        "ok": True
    }


def test_disabled_feature_calls_no_factories_or_registrars(
    tmp_path: Path,
) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("disabled feature callback must not run")

    feature = _feature(
        enabled=False,
        router_factory=fail,
        service_factory=fail,
        migration_provider=fail,
        register_jobs=fail,
        register_ai_tasks=fail,
    )
    paths = _paths(tmp_path)
    registry = WorkspaceRegistry([feature], paths=paths)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        registry.include_routers(FastAPI())
        registry.run_migrations()
        services = registry.create_services()
        registry.register_jobs(manager, services)
        registry.register_ai_tasks(object(), services)
    finally:
        manager.shutdown()

    assert services == {}
    assert not (paths.data_root / "workspaces").exists()


def test_workspace_job_registrar_prefixes_types_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    def register_jobs(registrar, _service) -> None:
        registrar.register("export", lambda _context: {"ok": True})

    registry = WorkspaceRegistry(
        [_feature(register_jobs=register_jobs)],
        paths=_paths(tmp_path),
    )
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        registry.register_jobs(manager, {})
        assert "teaching_prep.export" in manager._handlers
        with pytest.raises(
            WorkspaceRegistrationError,
            match="teaching-prep Job registration failed",
        ):
            registry.register_jobs(manager, {})
    finally:
        manager.shutdown()


def test_job_manager_never_silently_overwrites_a_handler(
    tmp_path: Path,
) -> None:
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        manager.register("same", lambda _context: {"first": True})
        with pytest.raises(ValueError, match="duplicate job type"):
            manager.register("same", lambda _context: {"second": True})
    finally:
        manager.shutdown()


def test_module_migrations_are_idempotent_and_gate_before_path_creation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    migrations = paths.project_root / "migrations" / "teaching_prep"
    migrations.mkdir(parents=True)
    (migrations / "000_baseline.sql").write_text(
        "CREATE TABLE lessons (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    preflight_calls: list[bool] = []

    def migration_provider(context):
        def preflight() -> None:
            preflight_calls.append(context.root.exists())

        return WorkspaceMigrationPlan(
            target="teaching_prep",
            database_path=context.root / "teaching_prep.db",
            migrations_dir=migrations,
            backup_dir=context.root / "backups",
            preflight=preflight,
        )

    registry = WorkspaceRegistry(
        [_feature(migration_provider=migration_provider)],
        paths=paths,
    )
    registry.run_migrations()
    registry.run_migrations()

    database = paths.workspace_dir("teaching-prep") / "teaching_prep.db"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='lessons'"
        ).fetchone() == ("lessons",)
    assert preflight_calls == [False, True]


def test_failed_module_migration_rolls_back_and_blocks_startup(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    migrations = paths.project_root / "migrations" / "teaching_prep"
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
            target="teaching_prep",
            database_path=context.root / "teaching_prep.db",
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
        match="teaching-prep migration failed",
    ):
        registry.run_migrations()

    database = paths.workspace_dir("teaching-prep") / "teaching_prep.db"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='should_rollback'"
        ).fetchone() is None
    assert service_calls == []


def test_module_migration_requires_an_explicit_preflight_gate(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    migrations = paths.project_root / "migrations" / "teaching_prep"
    migrations.mkdir(parents=True)
    (migrations / "000_baseline.sql").write_text(
        "CREATE TABLE lessons (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    def migration_provider(context):
        return WorkspaceMigrationPlan(
            target="teaching_prep",
            database_path=context.root / "teaching_prep.db",
            migrations_dir=migrations,
            backup_dir=context.root / "backups",
            preflight=None,  # type: ignore[arg-type]
        )

    registry = WorkspaceRegistry(
        [_feature(migration_provider=migration_provider)],
        paths=paths,
    )

    with pytest.raises(
        WorkspaceRegistrationError,
        match="migration preflight is required",
    ):
        registry.run_migrations()
    assert not (paths.data_root / "workspaces").exists()


@pytest.mark.parametrize(
    "workspace_id",
    ["", "../escape", "Teaching-Prep", "class_teacher", "a/b"],
)
def test_workspace_path_rejects_invalid_ids(
    tmp_path: Path,
    workspace_id: str,
) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(ValueError, match="workspace ID"):
        paths.workspace_dir(workspace_id)
    assert not (paths.data_root / "workspaces").exists()


def test_workspace_path_is_lazy_and_stays_under_data_root(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    candidate = paths.workspace_dir("class-teacher")
    assert candidate == paths.data_root / "workspaces" / "class-teacher"
    assert not candidate.exists()

    created = paths.workspace_dir("class-teacher", create=True)
    assert created.is_dir()
    created.resolve().relative_to(paths.data_root.resolve())


def test_legacy_restore_skips_workspace_data(tmp_path: Path) -> None:
    assert _safe_restore_destination(
        "user_data/workspaces/teaching-prep/private.db",
        project_root=tmp_path / "project",
        data_root=tmp_path / "user_data",
        logs_root=tmp_path / "logs",
    ) is None


class _AuditSink:
    def __init__(self) -> None:
        self.events = []

    def record(self, event) -> None:
        self.events.append(event)


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


class _ThreeAttemptGateway(_FakeGateway):
    def chat_completions(self, **kwargs):
        self.calls.append(kwargs)
        next_attempt = kwargs["_next_attempt"]
        assert callable(next_attempt)
        next_attempt()
        next_attempt()
        next_attempt()
        return {"usage": {"total_tokens": 0}}


def test_workspace_model_policy_requires_metadata_and_allows_one_request(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    gateway = _FakeGateway()
    audit = _AuditSink()
    policy = WorkspaceModelGateway(
        context=_context(paths, "teaching-prep"),
        gateway=gateway,
        audit_sink=audit,
    )
    request = WorkspaceModelRequest(
        purpose="lesson_analysis",
        data_classification="confidential",
        operation_id="op-123",
    )

    result = policy.chat_completions(
        request=request,
        client=object(),
        model="safe-model",
        kwargs={"messages": [{"role": "user", "content": "not logged"}]},
        timeout_override_seconds=110,
    )

    assert result["usage"]["total_tokens"] == 16
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["allow_retry"] is False
    assert gateway.calls[0]["request_id"] == "op-123"
    assert gateway.calls[0]["operation_id"] == "op-123"
    assert gateway.calls[0]["timeout_override_seconds"] == 110
    assert gateway.calls[0]["request_kind"].value == "workspace"
    assert audit.events[0].total_tokens == 16
    assert not hasattr(audit.events[0], "request_body")
    with pytest.raises(
        WorkspaceModelPolicyError,
        match="already used",
    ):
        policy.chat_completions(
            request=request,
            client=object(),
            model="safe-model",
            kwargs={},
        )
    assert len(gateway.calls) == 1


def test_workspace_model_policy_counts_each_physical_retry_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    gateway = _ThreeAttemptGateway()
    policy = WorkspaceModelGateway(
        context=_context(paths, "class-teacher"),
        gateway=gateway,
        metadata_only=False,
        claim_operations=False,
        allow_retry=True,
    )

    policy.chat_completions(
        request=WorkspaceModelRequest(
            purpose="ordinary_work_plan",
            data_classification="ordinary",
            operation_id="three-attempts-001",
        ),
        client=object(),
        model="safe-model",
        kwargs={},
    )

    assert policy.physical_request_count == 3


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
        context=_context(paths, "teaching-prep"),
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
        context=_context(paths, "teaching-prep"),
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


@pytest.mark.parametrize(
    "model_request",
    [
        WorkspaceModelRequest("", "internal", "op-1"),
        WorkspaceModelRequest("lesson_analysis", "", "op-1"),
        WorkspaceModelRequest("lesson_analysis", "internal", "../op"),
    ],
)
def test_workspace_model_policy_fails_closed_on_missing_metadata(
    tmp_path: Path,
    model_request: WorkspaceModelRequest,
) -> None:
    paths = _paths(tmp_path)
    gateway = _FakeGateway()
    policy = WorkspaceModelGateway(
        context=_context(paths, "class-teacher"),
        gateway=gateway,
    )

    with pytest.raises(WorkspaceModelPolicyError):
        policy.chat_completions(
            request=model_request,
            client=object(),
            model="safe-model",
            kwargs={},
        )
    assert gateway.calls == []
