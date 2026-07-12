from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.ops.journal import (
    OpsJournalInvalid,
    OpsOperationBusy,
    OpsOperationJournal,
    OpsOperationManifest,
)


OPERATION_ID = "11111111-1111-4111-8111-111111111111"


def _manifest(tmp_path: Path, *, operation_id: str = OPERATION_ID) -> OpsOperationManifest:
    staging = tmp_path / "ops" / "operations" / operation_id / "staging"
    staging.mkdir(parents=True)
    (staging / "payload.json").write_text("{}", encoding="utf-8")
    preparation_backup = tmp_path / "backups" / f"backup_{operation_id}.zip"
    preparation_backup.parent.mkdir(exist_ok=True)
    preparation_backup.write_bytes(b"backup")
    return OpsOperationManifest(
        operation_id=operation_id,
        operation="restore",
        parameters={"backup_filename": "backup_source.zip"},
        resource_fingerprint="f" * 64,
        staging_root=str(staging),
        preparation_backup=str(preparation_backup),
        created_at="2026-07-12T12:00:00+00:00",
    )


def test_journal_allows_exactly_one_pending_operation(tmp_path: Path) -> None:
    journal = OpsOperationJournal(tmp_path / "ops")
    first = journal.prepare(_manifest(tmp_path))

    assert first.status == "restart_required"
    with pytest.raises(OpsOperationBusy):
        journal.prepare(
            _manifest(
                tmp_path,
                operation_id="22222222-2222-4222-8222-222222222222",
            )
        )


def test_journal_public_projection_is_path_free(tmp_path: Path) -> None:
    journal = OpsOperationJournal(tmp_path / "ops")
    manifest = _manifest(tmp_path)
    journal.prepare(manifest)

    public = journal.load_public(manifest.operation_id)

    assert public == {
        "operation_id": manifest.operation_id,
        "operation": "restore",
        "status": "restart_required",
        "result_code": "prepared_restart_required",
        "created_at": "2026-07-12T12:00:00+00:00",
        "updated_at": "2026-07-12T12:00:00+00:00",
        "recovery": {
            "code": "restart_required",
            "backup_filename": Path(manifest.preparation_backup).name,
        },
    }
    assert str(tmp_path) not in json.dumps(public)


def test_cancel_pending_removes_staging_but_retains_preparation_backup(tmp_path: Path) -> None:
    journal = OpsOperationJournal(tmp_path / "ops")
    manifest = _manifest(tmp_path)
    journal.prepare(manifest)

    public = journal.cancel_pending(manifest.operation_id)

    assert public["status"] == "cancelled"
    assert public["result_code"] == "cancelled_before_apply"
    assert not Path(manifest.staging_root).exists()
    assert Path(manifest.preparation_backup).is_file()
    assert not journal.pending_exists()


def test_cancel_rejects_operation_after_apply_claim(tmp_path: Path) -> None:
    journal = OpsOperationJournal(tmp_path / "ops")
    manifest = _manifest(tmp_path)
    journal.prepare(manifest)
    claimed = journal.claim_pending()
    assert claimed is not None

    with pytest.raises(OpsOperationBusy):
        journal.cancel_pending(manifest.operation_id)


def test_corrupted_pending_pointer_fails_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "ops"
    state_root.mkdir()
    (state_root / "pending.json").write_text("not-json", encoding="utf-8")
    journal = OpsOperationJournal(state_root)

    with pytest.raises(OpsJournalInvalid):
        journal.claim_pending()
