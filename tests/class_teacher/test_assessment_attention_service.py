from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.assessment_evidence_service import (
    ConfirmedSpreadsheetAdapter,
    ExistingMathAssessmentAdapter,
)
from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成学业证据密码-足够长-001"


def _unlocked(tmp_path: Path) -> tuple[VaultService, str, str]:
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
        operation_id="initialize-assessment-evidence",
    )
    token = str(initialized["session_token"])
    subject = service.support.create_subject(
        token=token,
        operation_id="create-evidence-subject",
        source_student_id="synthetic-evidence-001",
        display_name="合成证据学生",
        class_label="合成一班",
    )
    return service, token, str(subject["subject_id"])


def _batch(subject_id: str) -> dict[str, object]:
    return ConfirmedSpreadsheetAdapter().read(
        {
            "teacher_confirmed": True,
            "source_label": "内存中的合成预览，不含原始文件",
            "assessments": [
                {
                    "title": f"合成数学检查{index}",
                    "subject_name": "数学",
                    "occurred_on": f"2026-0{index + 5}-01",
                    "max_score": 100,
                    "rank_scope": "class",
                    "participant_count": 40,
                    "assessment_nature": "unit",
                    "results": [
                        {
                            "subject_id": subject_id,
                            "result_state": "normal",
                            "score": score,
                            "rank": rank,
                        }
                    ],
                }
                for index, (score, rank) in enumerate(
                    [(0, 40), (62, 22), (75, 15)]
                )
            ],
        }
    )


def test_adapters_are_read_only_and_duplicate_batch_creates_no_second_evidence(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    calls: list[dict[str, object]] = []
    adapter = ExistingMathAssessmentAdapter(
        lambda query: (
            calls.append(dict(query))
            or {"source_label": "只读合成数学摘要", "assessments": []}
        )
    )
    assert adapter.read({"selected_exam_id": "synthetic"})["read_only"] is True
    assert calls == [{"selected_exam_id": "synthetic"}]

    first = service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-evidence-batch",
        batch=_batch(subject_id),
    )
    duplicate = service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-evidence-duplicate-operation",
        batch=_batch(subject_id),
    )
    assert first["created_results"] == 3
    assert duplicate["duplicate"] is True
    assert duplicate["created_results"] == 0
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )
    assert len(evidence["items"]) == 3
    assert evidence["items"][0]["score"] == 0


def test_csv_preview_stays_in_memory_and_preserves_zero_and_absence_text() -> None:
    preview = ConfirmedSpreadsheetAdapter.preview(
        file_name="synthetic-results.csv",
        content=(
            "学号,姓名,成绩\n"
            "S001,合成学生甲,0\n"
            "S002,合成学生乙,缺考\n"
        ).encode("utf-8"),
    )
    assert preview["headers"] == ["学号", "姓名", "成绩"]
    assert preview["rows"][0]["成绩"] == "0"
    assert preview["rows"][1]["成绩"] == "缺考"
    assert preview["raw_file_retained"] is False
    assert preview["temporary_file_created"] is False


def test_result_states_are_distinct_and_three_comparable_points_allow_trend(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-trend-batch",
        batch=_batch(subject_id),
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"]
    comparison = service.evidence.compare(
        token=token,
        older_evidence_version_id=str(evidence[0]["evidence_version_id"]),
        newer_evidence_version_id=str(evidence[1]["evidence_version_id"]),
    )
    assert comparison["comparability"] == "directly_comparable"
    assert service.evidence.trend(
        token=token,
        subject_id=subject_id,
        subject_name="数学",
    )["trend_allowed"] is True

    bad_batch = _batch(subject_id)
    bad_batch["source_label"] = "合成缺考状态"
    bad_batch["assessments"] = [
        {
            **bad_batch["assessments"][0],
            "title": "合成缺考检查",
            "occurred_on": "2026-09-01",
            "results": [
                {
                    "subject_id": subject_id,
                    "result_state": "absent",
                    "score": None,
                }
            ],
        }
    ]
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-absent-batch",
        batch=bad_batch,
    )
    states = {
        item["result_state"]
        for item in service.evidence.list_subject_evidence(
            token=token,
            subject_id=subject_id,
        )["items"]
    }
    assert {"normal", "absent"} <= states


def test_non_comparable_scores_never_return_raw_score_delta(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    batch = _batch(subject_id)
    batch["assessments"][1]["max_score"] = 120
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-different-max-scores",
        batch=batch,
    )
    items = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"]
    result = service.evidence.compare(
        token=token,
        older_evidence_version_id=str(items[0]["evidence_version_id"]),
        newer_evidence_version_id=str(items[1]["evidence_version_id"]),
    )
    assert result["comparability"] == "reference_only"
    assert result["score_delta"] is None


def test_attention_decision_creates_one_action_and_evidence_change_invalidates_draft(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-attention-batch",
        batch=_batch(subject_id),
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"]
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="create-attention-card",
        evidence_version_id=str(evidence[1]["evidence_version_id"]),
        observed_fact="两次合成数学证据的分数出现变化",
        comparability="directly_comparable",
        limitations=[],
        verification_question="近期学习安排是否发生变化？",
        low_risk_next_step="建议教师先了解近期情况",
        evidence_sufficiency="两次证据，只能提示变化，不能称为趋势",
        review_suggestion="一周后复查",
    )
    plan = service.actions.create_plan(
        token=token,
        operation_id="create-attention-plan",
        title="合成关注跟进",
        description=None,
        final_deadline=None,
    )
    resolved = service.attention.resolve(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
        operation_id="resolve-attention-card",
        revision=int(card["revision"]),
        decision="follow_up",
        reason=None,
        plan_id=str(plan["plan_id"]),
        review_at="2026-08-20T17:00:00+08:00",
    )
    replay = service.attention.resolve(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
        operation_id="resolve-attention-card",
        revision=int(card["revision"]),
        decision="follow_up",
        reason=None,
        plan_id=str(plan["plan_id"]),
        review_at="2026-08-20T17:00:00+08:00",
    )
    assert resolved == replay
    assert len(service.actions.list_actions(token=token)["items"]) == 1
    assert card["risk_score"] is None
    assert card["physical_request_count"] == 0

    another = service.attention.create_from_evidence(
        token=token,
        operation_id="create-invalidated-card",
        evidence_version_id=str(evidence[2]["evidence_version_id"]),
        observed_fact="一条合成证据等待核实",
        comparability="insufficient_information",
        limitations=["只有单条证据"],
        verification_question="需要补充什么信息？",
        low_risk_next_step="先向教师核实来源",
        evidence_sufficiency="不足",
        review_suggestion="补充后再看",
    )
    service.evidence.supersede_evidence(
        token=token,
        operation_id="supersede-attention-evidence",
        evidence_version_id=str(evidence[2]["evidence_version_id"]),
        reason="教师修订了合成证据",
    )
    assert (
        service.attention.get(
            token=token,
            attention_card_id=str(another["attention_card_id"]),
        )["state"]
        == "invalidated"
    )


def test_interrupted_attention_resolution_resumes_without_duplicate_action(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-recovery-attention-batch",
        batch=_batch(subject_id),
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"][0]
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="create-recovery-attention",
        evidence_version_id=str(evidence["evidence_version_id"]),
        observed_fact="合成恢复关注事实",
        comparability="insufficient_information",
        limitations=["仅用于恢复测试"],
        verification_question="是否需要核实？",
        low_risk_next_step="先了解情况",
        evidence_sufficiency="不足",
        review_suggestion="一周后复查",
    )
    plan = service.actions.create_plan(
        token=token,
        operation_id="create-recovery-attention-plan",
        title="合成恢复关注计划",
        description=None,
        final_deadline=None,
    )
    service.attention._claim_resolution(
        attention_card_id=str(card["attention_card_id"]),
        operation_id="interrupted-attention-resolve",
        decision="follow_up",
    )
    action = service.actions.create_action(
        token=token,
        operation_id=(
            "attention-target-"
            + hashlib.sha256(
                str(card["attention_card_id"]).encode("utf-8")
            ).hexdigest()[:32]
        ),
        plan_id=str(plan["plan_id"]),
        title="合成恢复行动",
        details=None,
        due_at="2026-08-20T17:00:00+08:00",
        depends_on_action_ids=[],
    )
    resumed = service.attention.resolve(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
        operation_id="resumed-attention-resolve",
        revision=int(card["revision"]),
        decision="follow_up",
        reason=None,
        plan_id=str(plan["plan_id"]),
        review_at="2026-08-20T17:00:00+08:00",
    )

    assert resumed["action_id"] == action["action_id"]
    assert len(service.actions.list_actions(token=token)["items"]) == 1


def test_observe_requires_date_and_no_action_requires_reason(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-decision-rules-batch",
        batch=_batch(subject_id),
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"][0]
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="create-decision-rules-card",
        evidence_version_id=str(evidence["evidence_version_id"]),
        observed_fact="一条合成变化",
        comparability="insufficient_information",
        limitations=["单次成绩"],
        verification_question="是否需要了解？",
        low_risk_next_step="先核实",
        evidence_sufficiency="不足",
        review_suggestion="稍后复查",
    )
    with pytest.raises(VaultError):
        service.attention.resolve(
            token=token,
            attention_card_id=str(card["attention_card_id"]),
            operation_id="observe-without-date",
            revision=int(card["revision"]),
            decision="observe",
            reason=None,
            plan_id="unused",
            review_at=None,
        )


def test_full_subject_delete_removes_quick_evidence_attention_and_linked_action(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path)
    quick = service.quick_inbox.create_text(
        token=token,
        operation_id="create-delete-quick",
        text="删除检查使用的合成速记。",
        subject_id=subject_id,
    )
    service.evidence.confirm_batch(
        token=token,
        operation_id="confirm-delete-evidence",
        batch=_batch(subject_id),
    )
    evidence = service.evidence.list_subject_evidence(
        token=token,
        subject_id=subject_id,
    )["items"][0]
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="create-delete-attention",
        evidence_version_id=str(evidence["evidence_version_id"]),
        observed_fact="删除检查使用的合成事实",
        comparability="insufficient_information",
        limitations=["单条证据"],
        verification_question="是否需要核实？",
        low_risk_next_step="先了解情况",
        evidence_sufficiency="不足",
        review_suggestion="稍后复查",
    )
    plan = service.actions.create_plan(
        token=token,
        operation_id="create-delete-attention-plan",
        title="删除检查目标",
        description=None,
        final_deadline=None,
    )
    resolved = service.attention.resolve(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
        operation_id="resolve-delete-attention",
        revision=int(card["revision"]),
        decision="follow_up",
        reason=None,
        plan_id=str(plan["plan_id"]),
        review_at="2026-08-20T17:00:00+08:00",
    )
    service.create_backup(
        token=token,
        backup_password="合成删除备份密码-足够长-001",
        operation_id="create-pre-delete-backup",
    )
    preview = service.support.preview_subject_deletion(
        token=token,
        subject_id=subject_id,
    )
    assert preview["affected_backup_count"] == 1
    with pytest.raises(VaultError) as confirmation:
        service.support.delete_subject(
            token=token,
            subject_id=subject_id,
            operation_id="delete-without-backup-confirmation",
            confirmation_phrase="确认完整删除学生支持数据",
        )
    assert (
        confirmation.value.code
        == "support_backup_delete_confirmation_required"
    )

    deletion = service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="delete-complete-subject-data",
        confirmation_phrase="确认完整删除学生支持数据",
        backup_confirmation_phrase="确认销毁受影响的班主任专用备份",
    )
    assert deletion["linked_actions_deleted"] == 1
    assert deletion["affected_backups_destroyed"] == 1
    assert service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="delete-complete-subject-data",
        confirmation_phrase="确认完整删除学生支持数据",
        backup_confirmation_phrase="确认销毁受影响的班主任专用备份",
    ) == deletion
    assert list(service.database.backup_dir.glob("*.ctbackup")) == []
    with closing(service.database.connect()) as connection:
        for table in (
            "quick_inbox_items",
            "subject_results",
            "evidence_versions",
            "attention_cards",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM actions WHERE action_id = ?",
            (resolved["action_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assessment_imports"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM encrypted_objects
            WHERE object_type IN (
                'student_subject', 'quick_inbox_item', 'subject_result',
                'rank_context', 'assessment_evidence_version',
                'attention_card', 'action_item'
            )
            """
        ).fetchone()[0] == 0
    assert quick["state"] == "draft"
    with pytest.raises(VaultError):
        service.attention.resolve(
            token=token,
            attention_card_id=str(card["attention_card_id"]),
            operation_id="no-action-without-reason",
            revision=int(card["revision"]),
            decision="no_action",
            reason=None,
            plan_id=None,
            review_at=None,
        )
