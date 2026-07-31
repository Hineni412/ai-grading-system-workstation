from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成支持档案密码-足够长-001"


def _service(tmp_path: Path) -> VaultService:
    return VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )


def _unlocked(tmp_path: Path) -> tuple[VaultService, str]:
    service = _service(tmp_path)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="initialize-support-records",
    )
    return service, str(initialized["session_token"])


def _subject(service: VaultService, token: str) -> dict[str, object]:
    return service.support.create_subject(
        token=token,
        operation_id="create-synthetic-subject",
        source_student_id="synthetic-2026-001",
        display_name="合成学生甲",
        class_label="合成一班",
    )


def _record(
    service: VaultService,
    token: str,
    subject_id: str,
    operation_id: str,
    kind: str = "teacher_observation",
) -> dict[str, object]:
    expiring = kind in {"teacher_observation", "provisional_judgment"}
    return service.support.create_record(
        token=token,
        operation_id=operation_id,
        subject_id=subject_id,
        record_kind=kind,
        content="在合成课堂任务中主动核对步骤",
        scene="合成数学课堂",
        source="教师当堂记录",
        basis="合成课堂记录单",
        counterexample="另一节合成课中未出现",
        category="learning_habit",
        observed_at="2026-08-03T09:00:00+08:00",
        review_at="2026-08-10T09:00:00+08:00" if expiring else None,
        expires_at="2026-08-20T09:00:00+08:00" if expiring else None,
    )


def test_revisions_are_immutable_and_summary_traces_current_source(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = _subject(service, token)
    subject_id = str(subject["subject_id"])
    fact = _record(
        service,
        token,
        subject_id,
        "create-supporting-fact",
        "fact",
    )
    observation = _record(
        service,
        token,
        subject_id,
        "create-observation",
    )
    service.support.link_observation_evidence(
        token=token,
        operation_id="link-observation-counterexample",
        observation_record_id=str(observation["record_id"]),
        evidence_record_id=str(fact["record_id"]),
        relation_kind="counterexample",
    )
    revised = service.support.revise_record(
        token=token,
        record_id=str(observation["record_id"]),
        operation_id="revise-observation",
        expected_revision=1,
        content="修订后：只在一次合成课堂任务中核对步骤",
        scene="合成数学课堂",
        source="教师复核记录",
        basis="合成课堂记录单",
        counterexample="另一节合成课中未出现",
        category="learning_habit",
        observed_at="2026-08-03T09:00:00+08:00",
        review_at="2026-08-10T09:00:00+08:00",
        expires_at="2026-08-20T09:00:00+08:00",
        revision_reason="补充限定范围",
    )

    assert len(revised["revision_history"]) == 2
    assert (
        revised["revision_history"][0]["payload"]["content"]
        == "在合成课堂任务中主动核对步骤"
    )
    summary = service.support.get_summary(
        token=token,
        subject_id=subject_id,
        as_of="2026-08-12T00:00:00+08:00",
    )
    current = next(
        item for item in summary["items"]
        if item["record_id"] == observation["record_id"]
    )
    assert current["revision"] == 2
    assert current["source"] == "教师复核记录"


def test_expired_and_unconfirmed_ai_records_never_enter_current_summary(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject_id = str(_subject(service, token)["subject_id"])
    observation = _record(
        service,
        token,
        subject_id,
        "create-expiring-observation",
    )
    draft = _record(
        service,
        token,
        subject_id,
        "create-ai-draft",
        "ai_draft",
    )
    before = service.support.get_summary(
        token=token,
        subject_id=subject_id,
        as_of="2026-08-12T00:00:00+08:00",
    )
    assert draft["record_id"] not in {
        item["record_id"] for item in before["items"]
    }
    after = service.support.get_summary(
        token=token,
        subject_id=subject_id,
        as_of="2026-08-21T00:00:00+08:00",
    )
    assert observation["record_id"] not in {
        item["record_id"] for item in after["items"]
    }
    confirmed = service.support.confirm_ai_draft(
        token=token,
        draft_record_id=str(draft["record_id"]),
        operation_id="teacher-confirm-ai-draft",
        confirmed_kind="reported_statement",
    )
    assert confirmed["record_kind"] == "reported_statement"
    assert (
        service.support.get_record(
            token=token,
            record_id=str(draft["record_id"]),
        )["state"]
        == "archived"
    )


def test_interrupted_ai_confirmation_resumes_without_duplicate_record(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject_id = str(_subject(service, token)["subject_id"])
    draft = _record(
        service,
        token,
        subject_id,
        "create-interrupted-ai-draft",
        "ai_draft",
    )
    service.support._claim_ai_confirmation(
        draft_record_id=str(draft["record_id"]),
        operation_id="interrupted-ai-confirm",
        confirmed_kind="reported_statement",
    )
    target = service.support.create_record(
        token=token,
        operation_id=(
            "ai-confirm-target-"
            + hashlib.sha256(
                str(draft["record_id"]).encode("utf-8")
            ).hexdigest()[:32]
        ),
        subject_id=subject_id,
        record_kind="reported_statement",
        content=str(draft["content"]),
        scene=str(draft["scene"]),
        source="合成故障注入",
        basis=None,
        counterexample=None,
        category="general",
        observed_at=str(draft["observed_at"]),
        review_at=None,
        expires_at=None,
    )
    resumed = service.support.confirm_ai_draft(
        token=token,
        draft_record_id=str(draft["record_id"]),
        operation_id="resumed-ai-confirm",
        confirmed_kind="reported_statement",
    )

    assert resumed["record_id"] == target["record_id"]
    formal = [
        item
        for item in service.support.list_records(
            token=token,
            subject_id=subject_id,
        )["items"]
        if item["record_kind"] != "ai_draft"
    ]
    assert len(formal) == 1


def test_support_plan_result_and_full_subject_deletion_leave_no_b07_objects(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject_id = str(_subject(service, token)["subject_id"])
    _record(service, token, subject_id, "create-delete-observation")
    plan = service.support.create_support_plan(
        token=token,
        operation_id="create-support-plan",
        subject_id=subject_id,
        goal="在两周内形成稳定的任务核对习惯",
        support_actions=["课前提供步骤卡", "课后由教师复查一次"],
        review_at="2026-08-20T17:00:00+08:00",
        action_id=None,
    )
    completed = service.support.complete_support_plan(
        token=token,
        support_plan_id=str(plan["support_plan_id"]),
        operation_id="complete-support-plan",
        expected_revision=1,
        result="合成检查显示能独立核对步骤",
    )
    assert completed["state"] == "completed"
    assert completed["result"] == "合成检查显示能独立核对步骤"
    listed_plans = service.support.list_support_plans(
        token=token,
        subject_id=subject_id,
    )
    assert listed_plans["items"] == [completed]

    result = service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="delete-synthetic-subject",
        confirmation_phrase="确认完整删除学生支持数据",
    )
    assert result["deleted"] is True
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM support_records"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM encrypted_objects
            WHERE object_type IN (
                'student_subject', 'support_record_revision',
                'support_plan', 'support_summary_cache'
            )
            """
        ).fetchone()[0] == 0


def test_subject_delete_failure_keeps_live_vault_and_backups_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = _subject(service, token)
    subject_id = str(subject["subject_id"])
    _record(service, token, subject_id, "create-rollback-record")
    backup = service.create_backup(
        token=token,
        backup_password="合成回滚备份密码-足够长-001",
        operation_id="create-rollback-backup",
    )
    backup_path = service.database.backup_dir / str(backup["file_name"])
    interrupted = backup_path.with_suffix(
        ".ctbackup.interrupted-delete.pending-delete"
    )
    backup_path.replace(interrupted)
    preview = service.support.preview_subject_deletion(
        token=token,
        subject_id=subject_id,
    )
    assert preview["affected_backup_count"] == 1
    assert backup_path.is_file()
    assert not interrupted.exists()
    original_replace = service.database.replace_from_snapshot_atomically
    calls = 0

    def fail_first_replace(payload: bytes, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic replacement failure")
        original_replace(payload, **kwargs)

    monkeypatch.setattr(
        service.database,
        "replace_from_snapshot_atomically",
        fail_first_replace,
    )
    with pytest.raises(RuntimeError, match="synthetic replacement failure"):
        service.support.delete_subject(
            token=token,
            subject_id=subject_id,
            operation_id="delete-rollback-subject",
            confirmation_phrase="确认完整删除学生支持数据",
            backup_confirmation_phrase="确认销毁受影响的班主任专用备份",
        )

    assert service.support.get_subject(
        token=token,
        subject_id=subject_id,
    )["display_name"] == subject["display_name"]
    assert (
        service.database.backup_dir / str(backup["file_name"])
    ).is_file()
    assert not list(
        service.database.backup_dir.glob("*.pending-delete")
    )


def test_subject_delete_restores_all_backups_when_second_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = _subject(service, token)
    subject_id = str(subject["subject_id"])
    _record(service, token, subject_id, "create-multi-backup-record")
    backups = [
        service.create_backup(
            token=token,
            backup_password=f"合成多备份密码-足够长-00{index}",
            operation_id=f"create-multi-backup-{index}",
        )
        for index in (1, 2)
    ]
    backup_paths = sorted(
        service.database.backup_dir / str(item["file_name"])
        for item in backups
    )
    original_payloads = {
        path.name: path.read_bytes() for path in backup_paths
    }
    original_unlink = Path.unlink
    failed_once = False

    def fail_second_backup(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal failed_once
        if path == backup_paths[1] and not failed_once:
            failed_once = True
            raise OSError("synthetic second backup deletion failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_backup)
    with pytest.raises(
        OSError,
        match="synthetic second backup deletion failure",
    ):
        service.support.delete_subject(
            token=token,
            subject_id=subject_id,
            operation_id="delete-subject-with-two-backups",
            confirmation_phrase="确认完整删除学生支持数据",
            backup_confirmation_phrase="确认销毁受影响的班主任专用备份",
        )

    assert service.support.get_subject(
        token=token,
        subject_id=subject_id,
    )["display_name"] == subject["display_name"]
    assert {
        path.name: path.read_bytes() for path in backup_paths
    } == original_payloads
    assert not list(
        service.database.root.glob(
            ".subject-delete-*.rollback.cttxn"
        )
    )


def test_subject_delete_recovers_after_exit_before_final_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = _subject(service, token)
    subject_id = str(subject["subject_id"])
    _record(service, token, subject_id, "create-exit-recovery-record")
    backups = [
        service.create_backup(
            token=token,
            backup_password=f"合成退出恢复密码-足够长-00{index}",
            operation_id=f"create-exit-recovery-backup-{index}",
        )
        for index in (1, 2)
    ]
    backup_paths = [
        service.database.backup_dir / str(item["file_name"])
        for item in backups
    ]
    original_payloads = {
        path.name: path.read_bytes() for path in backup_paths
    }

    def interrupt_before_commit(_transaction: Path) -> None:
        raise SystemExit("synthetic process exit before delete commit")

    monkeypatch.setattr(
        service.database,
        "commit_subject_delete_transaction",
        interrupt_before_commit,
    )
    with pytest.raises(
        SystemExit,
        match="synthetic process exit before delete commit",
    ):
        service.support.delete_subject(
            token=token,
            subject_id=subject_id,
            operation_id="delete-subject-before-final-commit",
            confirmation_phrase="确认完整删除学生支持数据",
            backup_confirmation_phrase="确认销毁受影响的班主任专用备份",
        )

    assert all(not path.exists() for path in backup_paths)
    assert len(
        list(
            service.database.root.glob(
                ".subject-delete-*.rollback.cttxn"
            )
        )
    ) == 1

    restarted = _service(tmp_path)
    unlocked = restarted.unlock(password=PASSWORD)
    assert restarted.support.get_subject(
        token=str(unlocked["session_token"]),
        subject_id=subject_id,
    )["display_name"] == subject["display_name"]
    assert {
        path.name: path.read_bytes() for path in backup_paths
    } == original_payloads
    assert not list(
        restarted.database.root.glob(
            ".subject-delete-*.rollback.cttxn"
        )
    )


def test_subject_delete_neutralizes_shared_reference_and_preserves_other_result(
    tmp_path: Path,
) -> None:
    service, token = _unlocked(tmp_path)
    subject = _subject(service, token)
    subject_id = str(subject["subject_id"])
    vmk = bytes(service._require_session(token).vmk)
    shared_object_id = "collection-board-synthetic-shared"
    with closing(service.database.connect()) as connection:
        with connection:
            service.repository.put(
                connection,
                vmk=vmk,
                object_id=shared_object_id,
                object_type="collection_board",
                payload={
                    "title": f"{subject['display_name']}与同伴的合成收集板",
                    "items": [
                        {
                            "participant_ref": subject_id,
                            "status": "pending_notice",
                        },
                        {
                            "participant_ref": "synthetic-other-student",
                            "status": "received",
                        },
                    ],
                },
            )

    preview = service.support.preview_subject_deletion(
        token=token,
        subject_id=subject_id,
    )
    assert preview["shared_object_count"] == 1
    service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="delete-shared-subject",
        confirmation_phrase="确认完整删除学生支持数据",
    )

    with closing(service.database.connect()) as connection:
        payload, _revision = service.repository.get(
            connection,
            vmk=vmk,
            object_id=shared_object_id,
        )
    assert payload["title"] == "[与已删除参与者相关的共享正文已移除]"
    assert str(payload["items"][0]["participant_ref"]).startswith(
        "deleted-participant-"
    )
    assert payload["items"][1] == {
        "participant_ref": "synthetic-other-student",
        "status": "received",
    }


def test_two_page_revision_conflict_is_rejected(tmp_path: Path) -> None:
    service, token = _unlocked(tmp_path)
    subject_id = str(_subject(service, token)["subject_id"])
    record = _record(
        service,
        token,
        subject_id,
        "create-conflict-observation",
    )
    service.support.revise_record(
        token=token,
        record_id=str(record["record_id"]),
        operation_id="first-page-revision",
        expected_revision=1,
        content="第一页已经保存的合成修订",
        scene="合成课堂",
        source="教师",
        basis=None,
        counterexample=None,
        category="general",
        observed_at="2026-08-03T09:00:00+08:00",
        review_at="2026-08-10T09:00:00+08:00",
        expires_at="2026-08-20T09:00:00+08:00",
        revision_reason="第一页保存",
    )
    with pytest.raises(VaultError) as caught:
        service.support.revise_record(
            token=token,
            record_id=str(record["record_id"]),
            operation_id="second-page-stale-revision",
            expected_revision=1,
            content="第二页过期修订",
            scene="合成课堂",
            source="教师",
            basis=None,
            counterexample=None,
            category="general",
            observed_at="2026-08-03T09:00:00+08:00",
            review_at="2026-08-10T09:00:00+08:00",
            expires_at="2026-08-20T09:00:00+08:00",
            revision_reason="过期页面",
        )
    assert caught.value.code == "vault_revision_conflict"
