from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class OpsJournalError(RuntimeError):
    pass


class OpsJournalInvalid(OpsJournalError):
    pass


class OpsOperationBusy(OpsJournalError):
    pass


class OpsOperationNotFound(OpsJournalError):
    pass


@dataclass(frozen=True, slots=True)
class OpsOperationManifest:
    operation_id: str
    operation: str
    parameters: dict[str, object]
    resource_fingerprint: str
    staging_root: str
    preparation_backup: str
    created_at: str


@dataclass(frozen=True, slots=True)
class OpsOperationRecord:
    manifest: OpsOperationManifest
    status: str
    result_code: str
    updated_at: str


_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[str, threading.RLock] = {}


class OpsOperationJournal:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.operations_root = self.state_root / "operations"
        self.pending_path = self.state_root / "pending.json"
        key = str(self.state_root.resolve(strict=False)).casefold()
        with _LOCK_REGISTRY_GUARD:
            self._lock = _LOCK_REGISTRY.setdefault(key, threading.RLock())

    def prepare(self, manifest: OpsOperationManifest) -> OpsOperationRecord:
        self._validate_manifest_values(manifest)
        with self._lock:
            if self.pending_path.exists():
                raise OpsOperationBusy("another ops operation is pending")
            operation_root = self._operation_root(manifest.operation_id)
            operation_root.mkdir(parents=True, exist_ok=True)
            self._write_manifest(operation_root / "manifest.json", manifest)
            record = OpsOperationRecord(
                manifest=manifest,
                status="restart_required",
                result_code="prepared_restart_required",
                updated_at=manifest.created_at,
            )
            self._write_status(operation_root / "status.json", record)
            _atomic_json(
                self.pending_path,
                {"operation_id": manifest.operation_id},
            )
            return record

    def pending_exists(self) -> bool:
        return self.pending_path.exists()

    def pending_record(self) -> OpsOperationRecord | None:
        with self._lock:
            if not self.pending_path.exists():
                return None
            return self._load_record(self._read_pending_id())

    def claim_pending(self) -> OpsOperationManifest | None:
        with self._lock:
            if not self.pending_path.exists():
                return None
            operation_id = self._read_pending_id()
            record = self._load_record(operation_id)
            if record.status != "restart_required":
                raise OpsJournalInvalid("pending operation has an invalid status")
            applying = OpsOperationRecord(
                manifest=record.manifest,
                status="applying",
                result_code="applying",
                updated_at=_utc_now(),
            )
            self._write_status(
                self._operation_root(operation_id) / "status.json",
                applying,
            )
            return record.manifest

    def cancel_pending(self, operation_id: str) -> dict[str, object]:
        with self._lock:
            pending_id = self._read_pending_id()
            if pending_id != operation_id:
                raise OpsOperationNotFound("operation not found")
            record = self._load_record(operation_id)
            if record.status != "restart_required":
                raise OpsOperationBusy("operation has already started applying")
            staging = self._controlled_staging(record.manifest)
            if staging.exists():
                shutil.rmtree(staging)
            cancelled = OpsOperationRecord(
                manifest=record.manifest,
                status="cancelled",
                result_code="cancelled_before_apply",
                updated_at=_utc_now(),
            )
            self._write_status(
                self._operation_root(operation_id) / "status.json",
                cancelled,
            )
            self.pending_path.unlink()
            return self._public(cancelled)

    def load_public(self, operation_id: str) -> dict[str, object]:
        with self._lock:
            return self._public(self._load_record(operation_id))

    def start_apply(self, operation_id: str, apply_backup: Path) -> None:
        with self._lock:
            record = self._load_record(operation_id)
            if record.status != "applying":
                raise OpsJournalInvalid("operation is not applying")
            backup = Path(apply_backup)
            if not backup.name.lower().endswith(".zip"):
                raise OpsJournalInvalid("apply backup is invalid")
            _atomic_json(
                self._operation_root(operation_id) / "apply.json",
                {"backup_path": str(backup), "replacements": []},
            )

    def record_replacement(
        self,
        operation_id: str,
        *,
        target: Path,
        archive_name: str,
        existed: bool,
    ) -> None:
        with self._lock:
            path = self._operation_root(operation_id) / "apply.json"
            payload = _read_json(path)
            replacements = payload.get("replacements")
            if not isinstance(replacements, list):
                raise OpsJournalInvalid("apply state is invalid")
            replacements.append(
                {
                    "target": str(Path(target)),
                    "archive_name": str(archive_name),
                    "existed": bool(existed),
                }
            )
            _atomic_json(path, payload)

    def load_apply_state(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            payload = _read_json(
                self._operation_root(operation_id) / "apply.json"
            )
            if not isinstance(payload.get("backup_path"), str) or not isinstance(
                payload.get("replacements"), list
            ):
                raise OpsJournalInvalid("apply state is invalid")
            return payload

    def mark_applied(self, operation_id: str) -> None:
        self._mark_terminal(operation_id, "applied", "applied")

    def mark_rolled_back(self, operation_id: str, *, result_code: str) -> None:
        self._mark_terminal(operation_id, "rolled_back", result_code)

    def mark_failed(self, operation_id: str, *, result_code: str) -> None:
        self._mark_terminal(operation_id, "failed", result_code)

    def _mark_terminal(self, operation_id: str, status: str, result_code: str) -> None:
        with self._lock:
            record = self._load_record(operation_id)
            updated = OpsOperationRecord(
                manifest=record.manifest,
                status=status,
                result_code=result_code,
                updated_at=_utc_now(),
            )
            self._write_status(
                self._operation_root(operation_id) / "status.json",
                updated,
            )
            if self.pending_path.exists() and self._read_pending_id() == operation_id:
                self.pending_path.unlink()

    def _load_record(self, operation_id: str) -> OpsOperationRecord:
        operation_root = self._operation_root(operation_id)
        manifest_path = operation_root / "manifest.json"
        status_path = operation_root / "status.json"
        if not manifest_path.is_file() or not status_path.is_file():
            raise OpsOperationNotFound("operation not found")
        manifest_payload = _read_json(manifest_path)
        checksum = str(manifest_payload.pop("manifest_sha256", ""))
        if checksum != _payload_sha256(manifest_payload):
            raise OpsJournalInvalid("operation manifest checksum is invalid")
        try:
            manifest = OpsOperationManifest(**manifest_payload)
        except (TypeError, ValueError) as exc:
            raise OpsJournalInvalid("operation manifest is invalid") from exc
        self._validate_manifest_values(manifest)
        status_payload = _read_json(status_path)
        try:
            return OpsOperationRecord(
                manifest=manifest,
                status=str(status_payload["status"]),
                result_code=str(status_payload["result_code"]),
                updated_at=str(status_payload["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OpsJournalInvalid("operation status is invalid") from exc

    def _read_pending_id(self) -> str:
        if not self.pending_path.is_file():
            raise OpsOperationNotFound("operation not found")
        payload = _read_json(self.pending_path)
        operation_id = str(payload.get("operation_id") or "")
        self._operation_root(operation_id)
        return operation_id

    def _operation_root(self, operation_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(str(operation_id)))
        except (ValueError, AttributeError) as exc:
            raise OpsJournalInvalid("operation id is invalid") from exc
        if normalized != str(operation_id):
            raise OpsJournalInvalid("operation id is invalid")
        return self.operations_root / normalized

    def _controlled_staging(self, manifest: OpsOperationManifest) -> Path:
        staging = Path(manifest.staging_root).resolve(strict=False)
        operation_root = self._operation_root(manifest.operation_id).resolve(strict=False)
        try:
            staging.relative_to(operation_root)
        except ValueError as exc:
            raise OpsJournalInvalid("operation staging root is invalid") from exc
        return staging

    def _validate_manifest_values(self, manifest: OpsOperationManifest) -> None:
        self._operation_root(manifest.operation_id)
        if manifest.operation not in {
            "restore",
            "migration",
            "transfer_import",
        }:
            raise OpsJournalInvalid("operation type is invalid")
        if len(str(manifest.resource_fingerprint)) != 64:
            raise OpsJournalInvalid("operation fingerprint is invalid")
        self._controlled_staging(manifest)
        if not Path(manifest.preparation_backup).name.lower().endswith(".zip"):
            raise OpsJournalInvalid("preparation backup is invalid")

    @staticmethod
    def _write_manifest(path: Path, manifest: OpsOperationManifest) -> None:
        payload = asdict(manifest)
        payload["manifest_sha256"] = _payload_sha256(payload)
        _atomic_json(path, payload)

    @staticmethod
    def _write_status(path: Path, record: OpsOperationRecord) -> None:
        _atomic_json(
            path,
            {
                "status": record.status,
                "result_code": record.result_code,
                "updated_at": record.updated_at,
            },
        )

    @staticmethod
    def _public(record: OpsOperationRecord) -> dict[str, object]:
        recovery_code = (
            "restart_required"
            if record.status == "restart_required"
            else record.result_code
        )
        apply_path = (
            Path(record.manifest.staging_root).parent / "apply.json"
        )
        backup_filename = Path(record.manifest.preparation_backup).name
        if apply_path.is_file():
            try:
                backup_filename = Path(str(_read_json(apply_path)["backup_path"])).name
            except (KeyError, OpsJournalInvalid):
                pass
        return {
            "operation_id": record.manifest.operation_id,
            "operation": record.manifest.operation,
            "status": record.status,
            "result_code": record.result_code,
            "created_at": record.manifest.created_at,
            "updated_at": record.updated_at,
            "recovery": {
                "code": recovery_code,
                "backup_filename": backup_filename,
            },
        }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_value = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_value)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpsJournalInvalid("operation state is invalid") from exc
    if not isinstance(payload, dict):
        raise OpsJournalInvalid("operation state is invalid")
    return payload


def _payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = [
    "OpsJournalInvalid",
    "OpsOperationBusy",
    "OpsOperationJournal",
    "OpsOperationManifest",
    "OpsOperationNotFound",
]
