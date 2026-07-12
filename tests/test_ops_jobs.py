from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.jobs.manager import JobCancellationRequested
from backend.ops.jobs import (
    register_ops_job_handlers,
    run_ops_backup_job,
    run_ops_transfer_export_job,
)
from backend.ops.plan_store import OpsPlanStore
from backend.ops.write_service import OpsWriteService


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        connection.commit()


def _paths(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"
    paths = SimpleNamespace(
        project_root=project_root,
        data_root=data_root,
        databases_dir=data_root / "databases",
        db_path=data_root / "databases" / "grading_system.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        exams_dir=data_root / "exams",
        config_dir=data_root / "config",
        templates_dir=data_root / "templates",
        annotated_dir=data_root / "annotated",
        reports_dir=data_root / "reports",
        qb_data_dir=data_root / "question_bank",
        outputs_dir=data_root / "outputs",
        snapshots_dir=data_root / "snapshots",
        backups_dir=data_root / "backups",
        logs_dir=project_root / "logs",
        ops_state_dir=tmp_path / "local" / "ops",
    )
    _create_database(paths.db_path, "grading")
    _create_database(paths.qb_db_path, "question-bank")
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "safe.json").write_text('{"ok": true}', encoding="utf-8")
    (paths.config_dir / "api_profiles.json").write_text('{"api_key": "secret"}', encoding="utf-8")
    (project_root / "config").mkdir(parents=True)
    return paths


class _Context:
    def __init__(self, payload: dict[str, object], *, cancel_on_check: int | None = None):
        self.job_id = 23
        self.payload = payload
        self.reports: list[tuple[float, str, str]] = []
        self._checks = 0
        self._cancel_on_check = cancel_on_check

    def report(self, progress: float, stage: str, detail: str = "") -> None:
        self.reports.append((progress, stage, detail))

    def raise_if_cancelled(self) -> None:
        self._checks += 1
        if self._cancel_on_check == self._checks:
            raise JobCancellationRequested("cancelled")


def _payload(service: OpsWriteService, operation: str, **parameters: object) -> dict[str, object]:
    request = SimpleNamespace(operation=operation, **parameters)
    preflight = service.preflight(request)
    plan = service.consume_plan(str(preflight["confirmation_token"]))
    payload = service.build_job_payload(plan)
    payload["operation_id"] = "11111111-1111-4111-8111-111111111111"
    return payload


def _service(paths: SimpleNamespace) -> OpsWriteService:
    return OpsWriteService(paths, plan_store=OpsPlanStore())


def test_ops_backup_publishes_valid_zip_with_consistent_databases(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    context = _Context(_payload(_service(paths), "backup", reason="manual"))

    result = run_ops_backup_job(context=context, paths=paths)

    published = paths.backups_dir / str(result["filename"])
    assert published.is_file()
    assert zipfile.is_zipfile(published)
    assert not list(paths.backups_dir.glob(".job-*"))
    with zipfile.ZipFile(published, "r") as archive:
        names = set(archive.namelist())
        assert "user_data/databases/grading_system.db" in names
        assert "user_data/databases/question_bank.db" in names
        assert "user_data/config/safe.json" in names
        assert "user_data/config/api_profiles.json" not in names
        extracted = tmp_path / "extracted.db"
        extracted.write_bytes(archive.read("user_data/databases/grading_system.db"))
    with sqlite3.connect(extracted) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "grading"
    assert result["operation"] == "backup"
    assert result["outcome"] == "published"
    assert result["file_path"] == str(published)


def test_ops_backup_cancel_before_publish_leaves_no_zip(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    context = _Context(
        _payload(_service(paths), "backup", reason="manual"),
        cancel_on_check=2,
    )

    with pytest.raises(JobCancellationRequested):
        run_ops_backup_job(context=context, paths=paths)

    assert not list(paths.backups_dir.glob("*.zip"))
    assert not list(paths.backups_dir.glob(".job-*"))


def test_ops_transfer_export_publishes_under_ops_output_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    context = _Context(_payload(_service(paths), "transfer_export", scope="lean"))

    result = run_ops_transfer_export_job(context=context, paths=paths)

    published = paths.outputs_dir / "ops" / str(result["filename"])
    assert published.is_file()
    assert zipfile.is_zipfile(published)
    assert result["file_path"] == str(published)
    assert result["operation"] == "transfer_export"


def test_ops_online_job_rejects_resource_changed_after_submission(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = _service(paths)
    payload = _payload(service, "transfer_export", scope="lean")
    (paths.config_dir / "safe.json").write_text('{"changed": true}', encoding="utf-8")

    with pytest.raises(ValueError, match="preflight resource changed"):
        run_ops_transfer_export_job(context=_Context(payload), paths=paths)


def test_register_ops_job_handlers_registers_online_types(tmp_path: Path) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    paths = _paths(tmp_path)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_ops_job_handlers(manager, paths=paths)
    try:
        job = manager.submit(
            "ops_transfer_export",
            _payload(_service(paths), "transfer_export", scope="lean"),
        )
        manager.wait(job.id, timeout=5)
        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "succeeded"
    finally:
        manager.shutdown()
