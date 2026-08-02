from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成投影保险箱密码-足够长-001"


def _service(tmp_path: Path) -> tuple[VaultService, str]:
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="projection-vault-init-001",
    )
    return service, str(initialized["session_token"])


def test_same_sensitive_aggregate_has_one_fixed_title_projection(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject = service.support.create_subject(
        token=token,
        operation_id="projection-subject-create-001",
        source_student_id="synthetic-projection-student",
        display_name="合成投影学生",
        class_label="合成一班",
    )
    record = service.support.create_record(
        token=token,
        operation_id="projection-record-create-001",
        subject_id=str(subject["subject_id"]),
        record_kind="teacher_observation",
        content="合成观察正文不应进入普通库。",
        scene="合成课堂",
        source="教师观察",
        basis=None,
        category="learning",
        observed_at="2026-08-01T08:00:00+00:00",
        review_at="2026-08-10T08:00:00+00:00",
        expires_at="2026-09-01T08:00:00+00:00",
        counterexample=None,
    )
    first = service.projections.upsert(
        token=token,
        source_kind="student_support",
        source_id=str(record["record_id"]),
        state="pending",
        due_date="2026-08-10",
    )
    second = service.projections.upsert(
        token=token,
        source_kind="student_support",
        source_id=str(record["record_id"]),
        state="in_progress",
        due_date="2026-08-10",
    )

    assert first["projection_id"] == second["projection_id"]
    snapshot = service.work.read(view="all")
    projected = [item for item in snapshot["nodes"] if item["classification"] == "restricted_projection"]
    assert len(projected) == 1
    assert projected[0]["title"] == "学生支持待跟进"
    assert projected[0]["details"] is None
    raw = service.ordinary_database.database_path.read_bytes()
    assert "合成投影学生".encode() not in raw
    assert "合成观察正文".encode() not in raw


def test_projection_replay_rejects_same_revision_with_different_fingerprint(
    tmp_path: Path,
) -> None:
    service, _token = _service(tmp_path)
    envelope = {
        "projection_id": "projection_conflict_001",
        "projection_type": "sensitive_affair",
        "state": "pending",
        "due_date": None,
        "source_revision": 1,
        "envelope_fingerprint": "a" * 64,
    }
    service.work.apply_projection_envelope(envelope)
    with pytest.raises(VaultError) as conflict:
        service.work.apply_projection_envelope({
            **envelope,
            "envelope_fingerprint": "b" * 64,
        })
    assert conflict.value.code == "class_teacher_projection_revision_conflict"


def test_restricted_projection_cannot_be_completed_in_ordinary_work(tmp_path: Path) -> None:
    service, _token = _service(tmp_path)
    service.work.apply_projection_envelope({
        "projection_id": "projection_read_only_001",
        "projection_type": "attention_followup",
        "state": "pending",
        "due_date": "2026-08-10",
        "source_revision": 1,
        "envelope_fingerprint": "c" * 64,
    })
    node = next(
        item for item in service.work.read(view="all")["nodes"]
        if item["classification"] == "restricted_projection"
    )
    with pytest.raises(VaultError) as blocked:
        service.work.command(
            node_id=str(node["node_id"]),
            command="update_status",
            expected_revision=int(node["revision"]),
            operation_id="projection-update-blocked-001",
            status="completed",
        )
    assert blocked.value.code == "open_required"


def test_tombstone_removes_ordinary_node_before_mapping_can_disappear(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    with closing(service.database.connect()) as connection:
        with connection:
            connection.execute("PRAGMA foreign_keys = OFF")
    group = service.projections.upsert(
        token=token,
        source_kind="student_support",
        source_id="missing_source_001",
        state="pending",
        due_date=None,
    )
    resolved = service.projections.resolve(
        token=token,
        projection_id=str(group["projection_id"]),
    )

    assert resolved == {"gone": True}
    assert all(
        item.get("source_projection_id") != group["projection_id"]
        for item in service.work.read(view="all")["nodes"]
    )
