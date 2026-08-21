from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake.ports import FakeWorkspaceAITaskPort
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> tuple[VaultService, FakeWorkspaceAITaskPort]:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [(1, "A001", "合成学生甲", "一班"), (2, "B001", "合成学生乙", "二班")],
        )
        connection.commit()
    port = FakeWorkspaceAITaskPort()
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
            db_path=grading,
        ),
    )
    return VaultService(context, workspace_ai_task_port=port), port


def _profile(summary: str, item: str) -> dict[str, object]:
    return {
        "summary": summary,
        "dimensions": [{
            "key": "peer_relationships",
            "label": "同伴与人际关系",
            "items": [item],
        }],
        "open_questions": [],
        "support_focus": [],
    }


def _adopt_profile_update(
    service: VaultService,
    *,
    operation_id: str,
    subject: dict[str, object],
    profile_update: dict[str, object],
    profile_base_revision: int,
) -> dict[str, object]:
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        message=f"{operation_id} 合成教师原文",
        operation_id=f"{operation_id}-turn",
    )
    turn = conversation["turns"][-1]
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到当前档案。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": f"{operation_id}-item",
                "domain": "student_growth",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "append",
                "reason_summary": "补充学生当前档案",
                "subject_refs": [{
                    "kind": "student",
                    "id": str(subject["subject_id"]),
                    "revision": str(subject["revision"]),
                }],
                "time_facts": [],
                "safety_level": "normal",
                "missing_fields": [],
                "draft": {
                    "summary": f"{operation_id} 合成记录摘要",
                    "record_kind": "fact",
                    "source": "合成教师核对",
                    "profile_update": profile_update,
                    "profile_base_revision": profile_base_revision,
                },
            }],
        },
    )
    handoff = ready["handoffs"][0]
    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=str(subject["revision"]),
        operation_id=operation_id,
    )
    return {"handoff": handoff, "receipt": receipt}


def _current_profile(service: VaultService, subject_id: str) -> dict[str, object]:
    return dict(service.student_cards.get_card(token="", subject_id=subject_id)["current_profile"])


def test_adopt_student_record_stores_pre_merge_profile_snapshot(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="revert-snapshot-subject",
        source_student_id="SYN-REVERT-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    with closing(service.database.connect()) as connection:
        with connection:
            service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=service.student_cards._key_provider(""),
                subject_id=str(subject["subject_id"]),
                profile_update=_profile("合并前的档案", "原有表现"),
                expected_revision=0,
                operation_id="revert_snapshot_seed",
                model_operation_id="revert-snapshot-model",
                teacher_quote="合成档案原话",
                model_draft="合成档案草稿",
            )

    outcome = _adopt_profile_update(
        service,
        operation_id="revert-snapshot-adopt",
        subject=subject,
        profile_update=_profile("合并后的档案", "新增表现"),
        profile_base_revision=1,
    )

    receipt = outcome["receipt"]
    assert receipt["formal_object_type"] == "student_record"
    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT profile_snapshot_object_id, reverted_at FROM handoff_adoption_receipts WHERE handoff_id=?",
            (str(outcome["handoff"]["handoff_id"]),),
        ).fetchone()
        assert row is not None
        assert row["profile_snapshot_object_id"]
        assert row["reverted_at"] is None
        snapshot, _revision = service.repository.get(
            connection,
            vmk=service.ensure_plaintext_ready(),
            object_id=str(row["profile_snapshot_object_id"]),
        )
    assert snapshot["payload"]["profile"]["summary"] == "合并前的档案"
    assert snapshot["revision_before"] == 1
    assert _current_profile(service, str(subject["subject_id"]))["summary"] == "合并后的档案"


def test_revert_restores_pre_merge_payload_with_incremented_revision(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="revert-restore-subject",
        source_student_id="SYN-REVERT-002",
        display_name="合成学生甲",
        class_label="一班",
    )
    with closing(service.database.connect()) as connection:
        with connection:
            service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=service.student_cards._key_provider(""),
                subject_id=str(subject["subject_id"]),
                profile_update=_profile("合并前的档案", "原有表现"),
                expected_revision=0,
                operation_id="revert_restore_seed",
                model_operation_id="revert-restore-model",
                teacher_quote="合成档案原话",
                model_draft="合成档案草稿",
            )
    outcome = _adopt_profile_update(
        service,
        operation_id="revert-restore-adopt",
        subject=subject,
        profile_update=_profile("合并后的档案", "新增表现"),
        profile_base_revision=1,
    )
    handoff_id = str(outcome["handoff"]["handoff_id"])
    entry_id = str(_current_profile(service, str(subject["subject_id"]))["entry_id"])

    result = service.intake.revert_profile_adoption(token="", handoff_id=handoff_id)

    assert result["adoption_state"] == "reverted"
    assert result["profile_state"] == "restored"
    assert result["source_record_retained"] is True
    profile = _current_profile(service, str(subject["subject_id"]))
    assert profile["entry_id"] == entry_id
    assert profile["summary"] == "合并前的档案"
    assert profile["revision"] == 3
    handoff = service.intake.open_handoff(handoff_id)
    assert handoff["adoption_state"] == "reverted"
    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT reverted_at FROM handoff_adoption_receipts WHERE handoff_id=?",
            (handoff_id,),
        ).fetchone()
        assert row["reverted_at"]
    replay = service.intake.revert_profile_adoption(token="", handoff_id=handoff_id)
    assert replay["replayed"] is True
    assert _current_profile(service, str(subject["subject_id"]))["revision"] == 3


def test_revert_rejects_when_a_newer_merge_exists(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="revert-latest-subject",
        source_student_id="SYN-REVERT-003",
        display_name="合成学生甲",
        class_label="一班",
    )
    first = _adopt_profile_update(
        service,
        operation_id="revert-latest-first",
        subject=subject,
        profile_update=_profile("第一轮档案", "第一轮表现"),
        profile_base_revision=0,
    )
    _adopt_profile_update(
        service,
        operation_id="revert-latest-second",
        subject=subject,
        profile_update=_profile("第二轮档案", "第二轮表现"),
        profile_base_revision=1,
    )

    with pytest.raises(VaultError) as error:
        service.intake.revert_profile_adoption(
            token="",
            handoff_id=str(first["handoff"]["handoff_id"]),
        )

    assert error.value.status_code == 409
    assert error.value.code == "class_teacher_revert_superseded"
    assert _current_profile(service, str(subject["subject_id"]))["summary"] == "第二轮档案"


def test_revert_without_prior_profile_returns_to_not_created(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="revert-blank-subject",
        source_student_id="SYN-REVERT-004",
        display_name="合成学生甲",
        class_label="一班",
    )
    outcome = _adopt_profile_update(
        service,
        operation_id="revert-blank-adopt",
        subject=subject,
        profile_update=_profile("首次合并的档案", "首次表现"),
        profile_base_revision=0,
    )
    assert _current_profile(service, str(subject["subject_id"]))["summary"] == "首次合并的档案"

    result = service.intake.revert_profile_adoption(
        token="",
        handoff_id=str(outcome["handoff"]["handoff_id"]),
    )

    assert result["profile_state"] == "not_created"
    card = service.student_cards.get_card(token="", subject_id=str(subject["subject_id"]))
    assert card["entries"] == []
    assert card["current_profile"]["summary"] == ""
    assert card["current_profile"]["revision"] == 0


def test_revert_keeps_the_original_support_record(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="revert-record-subject",
        source_student_id="SYN-REVERT-005",
        display_name="合成学生甲",
        class_label="一班",
    )
    outcome = _adopt_profile_update(
        service,
        operation_id="revert-record-adopt",
        subject=subject,
        profile_update=_profile("合并后的档案", "新增表现"),
        profile_base_revision=0,
    )
    record_id = str(outcome["receipt"]["formal_object_id"])

    service.intake.revert_profile_adoption(
        token="",
        handoff_id=str(outcome["handoff"]["handoff_id"]),
    )

    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT record_id, state FROM support_records WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "active"
    summary = service.support.get_summary(token="", subject_id=str(subject["subject_id"]))
    assert any(str(item.get("record_id")) == record_id for item in summary["items"])


def test_adopt_marks_latest_round_changes_and_revert_clears_them(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="latest-round-subject",
        source_student_id="SYN-ROUND-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    with closing(service.database.connect()) as connection:
        with connection:
            service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=service.student_cards._key_provider(""),
                subject_id=str(subject["subject_id"]),
                profile_update=_profile("合并前的档案", "原有表现"),
                expected_revision=0,
                operation_id="latest_round_seed",
                model_operation_id="latest-round-model",
                teacher_quote="合成档案原话",
                model_draft="合成档案草稿",
            )
    outcome = _adopt_profile_update(
        service,
        operation_id="latest-round-adopt",
        subject=subject,
        profile_update=_profile("合并后的档案", "新增表现"),
        profile_base_revision=1,
    )

    profile = _current_profile(service, str(subject["subject_id"]))
    latest = profile["latest_round"]
    assert isinstance(latest, dict)
    assert latest["record_id"] == str(outcome["receipt"]["formal_object_id"])
    assert latest["adopted_at"]
    changed = latest["changed"]
    assert changed["summary_changed"] is True
    assert changed["dimensions"] == {"peer_relationships": ["新增表现"]}
    assert changed["open_questions"] == []
    assert changed["support_focus"] == []

    service.intake.revert_profile_adoption(
        token="",
        handoff_id=str(outcome["handoff"]["handoff_id"]),
    )
    restored = _current_profile(service, str(subject["subject_id"]))
    assert restored["summary"] == "合并前的档案"
    assert restored["latest_round"] is None


def test_new_adopt_replaces_latest_round_marker(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="latest-round-replace-subject",
        source_student_id="SYN-ROUND-002",
        display_name="合成学生甲",
        class_label="一班",
    )
    _adopt_profile_update(
        service,
        operation_id="latest-round-first",
        subject=subject,
        profile_update=_profile("第一轮档案", "第一轮表现"),
        profile_base_revision=0,
    )
    second = _adopt_profile_update(
        service,
        operation_id="latest-round-second",
        subject=subject,
        profile_update=_profile("第二轮档案", "第二轮表现"),
        profile_base_revision=1,
    )

    latest = _current_profile(service, str(subject["subject_id"]))["latest_round"]
    assert isinstance(latest, dict)
    assert latest["record_id"] == str(second["receipt"]["formal_object_id"])
    assert latest["changed"]["dimensions"] == {"peer_relationships": ["第二轮表现"]}


def test_handoff_summary_carries_subject_id_for_card_dedup(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="handoff-subject-id-subject",
        source_student_id="SYN-ROUND-003",
        display_name="合成学生甲",
        class_label="一班",
    )
    outcome = _adopt_profile_update(
        service,
        operation_id="handoff-subject-id-adopt",
        subject=subject,
        profile_update=_profile("合成档案", "合成表现"),
        profile_base_revision=0,
    )
    handoff_id = str(outcome["handoff"]["handoff_id"])
    conversation_id = str(service.intake.open_handoff(handoff_id)["conversation_id"])

    conversation = service.intake.get_conversation(conversation_id)

    assert conversation["handoffs"][0]["subject_id"] == str(subject["subject_id"])
