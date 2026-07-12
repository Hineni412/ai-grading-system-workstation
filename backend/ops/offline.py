from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from path_manager import get_path_manager
from update_tools.migrate_db import run_migrations

from .archive import OpsArchivePolicy, extract_validated_zip
from .jobs import create_safety_backup
from .journal import OpsJournalInvalid, OpsOperationJournal, OpsOperationManifest
from .plan_store import OpsPlanStore
from .write_service import OpsWriteService


class _QuietLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def apply_pending_operation(*, paths: Any) -> int:
    journal = OpsOperationJournal(Path(paths.ops_state_dir))
    record = journal.pending_record()
    if record is None:
        return 0
    operation_id = record.manifest.operation_id
    if record.status == "applying":
        try:
            state = journal.load_apply_state(operation_id)
        except OpsJournalInvalid:
            journal.mark_rolled_back(operation_id, result_code="crash_before_changes")
            return 0
        try:
            _rollback(paths=paths, state=state)
        except Exception:
            journal.mark_failed(operation_id, result_code="rollback_failed")
            return 2
        journal.mark_rolled_back(operation_id, result_code="crash_recovered")
        return 0
    if record.status != "restart_required":
        journal.mark_failed(operation_id, result_code="invalid_pending_state")
        return 2
    manifest = journal.claim_pending()
    if manifest is None:
        return 0
    apply_started = False
    try:
        _revalidate_and_stage(paths=paths, manifest=manifest)
        apply_backup = create_safety_backup(
            paths=paths, reason="before_apply", operation_id=operation_id
        )
        journal.start_apply(operation_id, apply_backup)
        apply_started = True
        if manifest.operation == "migration":
            _apply_migrations(paths=paths, manifest=manifest, journal=journal)
        else:
            _apply_overlay(paths=paths, manifest=manifest, journal=journal)
    except Exception:
        if apply_started:
            try:
                _rollback(paths=paths, state=journal.load_apply_state(operation_id))
            except Exception:
                journal.mark_failed(operation_id, result_code="rollback_failed")
                return 2
        journal.mark_rolled_back(operation_id, result_code="apply_failed_rolled_back")
        return 0
    journal.mark_applied(operation_id)
    return 0


def _revalidate_and_stage(*, paths: Any, manifest: OpsOperationManifest) -> None:
    service = OpsWriteService(paths, plan_store=OpsPlanStore())
    staging = Path(manifest.staging_root)
    operation_root = Path(paths.ops_state_dir) / "operations" / manifest.operation_id
    _require_within(staging, operation_root)
    if staging.exists():
        _reject_link(staging)
    if manifest.operation == "migration":
        target = str(manifest.parameters["target"])
        if service.migration_files_fingerprint(target) != manifest.resource_fingerprint:
            raise ValueError("migration files changed after preparation")
        service.migration_previews(target)
        return
    if manifest.operation == "restore":
        source = Path(paths.backups_dir) / str(manifest.parameters["backup_filename"])
        allowed_roots = {"user_data", "config", "logs"}
    elif manifest.operation == "transfer_import":
        source = Path(paths.ops_state_dir) / "uploads" / f"{manifest.parameters['upload_id']}.zip"
        allowed_roots = {"user_data", "config"}
    else:
        raise ValueError("unsupported offline operation")
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError("prepared source is missing")
    if _file_sha256(source) != manifest.resource_fingerprint:
        raise ValueError("prepared source changed")
    if staging.exists():
        shutil.rmtree(staging)
    extract_validated_zip(
        source, staging, policy=OpsArchivePolicy(), allowed_roots=allowed_roots
    )


def _apply_overlay(*, paths: Any, manifest: OpsOperationManifest, journal: OpsOperationJournal) -> None:
    staging = Path(manifest.staging_root)
    for source in sorted(path for path in staging.rglob("*") if path.is_file()):
        _reject_link_chain(source, staging)
        archive_name = source.relative_to(staging).as_posix()
        target = _target_for_archive_name(paths, archive_name)
        journal.record_replacement(
            manifest.operation_id,
            target=target,
            archive_name=archive_name,
            existed=target.is_file(),
        )
        _replace_staged_file(source, target)


def _apply_migrations(*, paths: Any, manifest: OpsOperationManifest, journal: OpsOperationJournal) -> None:
    requested = str(manifest.parameters["target"])
    targets = ("grading", "question_bank") if requested == "all" else (requested,)
    for target_name in targets:
        db_path = Path(paths.db_path if target_name == "grading" else paths.qb_db_path)
        archive_name = (
            "user_data/databases/grading_system.db"
            if target_name == "grading"
            else "user_data/databases/question_bank.db"
        )
        journal.record_replacement(
            manifest.operation_id,
            target=db_path,
            archive_name=archive_name,
            existed=db_path.is_file(),
        )
        report = run_migrations(
            target_name,
            db_path=db_path,
            migrations_dir=Path(paths.project_root) / "migrations" / target_name,
            logger_override=_QuietLogger(),
        )
        if report.error:
            raise RuntimeError("migration apply failed")


def _replace_staged_file(source: Path, target: Path) -> None:
    _reject_link_chain(target, target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_chain(target.parent, target.parent)
    fd, temporary_value = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".ops-tmp"
    )
    os.close(fd)
    temporary = Path(temporary_value)
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _rollback(*, paths: Any, state: dict[str, Any]) -> None:
    backup = Path(str(state["backup_path"]))
    if not backup.is_file() or backup.is_symlink():
        raise FileNotFoundError("apply backup is missing")
    replacements = state.get("replacements")
    if not isinstance(replacements, list):
        raise ValueError("replacement journal is invalid")
    with zipfile.ZipFile(backup, "r") as archive:
        for item in reversed(replacements):
            if not isinstance(item, dict):
                raise ValueError("replacement journal is invalid")
            target = Path(str(item["target"]))
            expected = _target_for_archive_name(paths, str(item["archive_name"]))
            if target.resolve(strict=False) != expected.resolve(strict=False):
                raise ValueError("replacement target is outside controlled roots")
            if bool(item["existed"]):
                with archive.open(str(item["archive_name"]), "r") as source:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary_value = tempfile.mkstemp(
                        dir=target.parent, prefix=f".{target.name}.", suffix=".rollback-tmp"
                    )
                    temporary = Path(temporary_value)
                    try:
                        with os.fdopen(fd, "wb") as output:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
            elif target.exists():
                _reject_link(target)
                target.unlink()


def _target_for_archive_name(paths: Any, archive_name: str) -> Path:
    pure = Path(archive_name)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError("archive target is invalid")
    if pure.parts[0] == "user_data":
        root = Path(paths.data_root)
        target = root.joinpath(*pure.parts[1:])
    elif pure.parts[0] == "config":
        root = Path(paths.project_root) / "config"
        target = root.joinpath(*pure.parts[1:])
    elif pure.parts[0] == "logs":
        root = Path(paths.logs_dir)
        target = root.joinpath(*pure.parts[1:])
    else:
        raise ValueError("archive target is invalid")
    _require_within(target, root)
    _reject_link_chain(target, root)
    return target


def _require_within(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("path is outside controlled root") from exc


def _reject_link_chain(path: Path, root: Path) -> None:
    current = Path(path)
    boundary = Path(root).resolve(strict=False)
    while True:
        if current.exists() or current.is_symlink():
            _reject_link(current)
        if current.resolve(strict=False) == boundary or current.parent == current:
            break
        current = current.parent


def _reject_link(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("symbolic links are not allowed")
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return
    if attributes & 0x400:
        raise ValueError("reparse points are not allowed")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a prepared protected operation")
    parser.add_argument("--apply-pending", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        return apply_pending_operation(paths=get_path_manager())
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["apply_pending_operation", "main"]
