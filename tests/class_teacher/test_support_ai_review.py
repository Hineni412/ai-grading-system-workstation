from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.model_approval import FakeApprovedModelGateway
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _fixture(tmp_path: Path) -> tuple[VaultService, FakeApprovedModelGateway, str, dict[str, object]]:
    gateway = FakeApprovedModelGateway(result='{"kind":"proposal","proposal":{"summary":"合成建议"}}')
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
        ),
        model_gateway=gateway,
    )
    service.ensure_plaintext_ready()
    token = ""
    subject = service.support.create_subject(
        token=token,
        operation_id="review-subject-0001",
        source_student_id="review-student-001",
        display_name="合成复核学生",
        class_label="合成一班",
    )
    record = service.support.create_record(
        token=token,
        operation_id="review-record-0001",
        subject_id=str(subject["subject_id"]),
        record_kind="fact",
        content="按时完成了本周三次合成任务。",
        scene="合成课堂",
        source="教师核对",
        basis="三次任务记录",
        counterexample=None,
        category="learning",
        observed_at="2026-08-01T08:00:00+00:00",
        review_at=None,
        expires_at=None,
    )
    return service, gateway, token, record


def test_each_preview_calls_model_once_and_final_card_has_one_projection(tmp_path: Path) -> None:
    service, gateway, token, record = _fixture(tmp_path)
    preview = service.support_ai_reviews.prepare(
        token=token,
        record_id=str(record["record_id"]),
        expected_revision=1,
        teacher_supplement="请保持中性表述",
    )
    review = service.support_ai_reviews.confirm(
        token=token,
        review_id=str(preview["review_id"]),
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="review-model-confirm-0001",
    )
    replay = service.support_ai_reviews.confirm(
        token=token,
        review_id=str(preview["review_id"]),
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="review-model-confirm-0001",
    )
    assert len(gateway.calls) == 1
    assert replay["state"] == review["state"] == "proposal_ready"

    saved = service.support_ai_reviews.apply(
        token=token,
        review_id=str(preview["review_id"]),
        model_operation_id="review-model-confirm-0001",
        expected_revision=1,
        teacher_result={"summary": "教师核对后的合成摘要", "strengths": [], "needs": []},
        operation_id="review-apply-0001",
    )
    assert saved["saved"] is True
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_kind = 'student_support' AND source_id = ?",
            (saved["entry_id"],),
        ).fetchone()[0] == 1


def test_record_revision_change_invalidates_review_before_apply(tmp_path: Path) -> None:
    service, _gateway, token, record = _fixture(tmp_path)
    preview = service.support_ai_reviews.prepare(
        token=token,
        record_id=str(record["record_id"]),
        expected_revision=1,
    )
    service.support_ai_reviews.confirm(
        token=token,
        review_id=str(preview["review_id"]),
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="review-model-confirm-0002",
    )
    service.support.revise_record(
        token=token,
        record_id=str(record["record_id"]),
        operation_id="review-record-revise-0001",
        expected_revision=1,
        content="修订后的合成事实。",
        scene="合成课堂",
        source="教师核对",
        basis="新增核对",
        counterexample=None,
        category="learning",
        observed_at="2026-08-01T08:00:00+00:00",
        review_at=None,
        expires_at=None,
        revision_reason="纠正合成记录",
    )
    with pytest.raises(VaultError) as error:
        service.support_ai_reviews.apply(
            token=token,
            review_id=str(preview["review_id"]),
            model_operation_id="review-model-confirm-0002",
            expected_revision=1,
            teacher_result={"summary": "不应写入"},
            operation_id="review-apply-0002",
        )
    assert error.value.code == "support_ai_review_stale"


def test_student_card_and_projection_outbox_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _gateway, token, record = _fixture(tmp_path)
    preview = service.support_ai_reviews.prepare(
        token=token,
        record_id=str(record["record_id"]),
        expected_revision=1,
    )
    service.support_ai_reviews.confirm(
        token=token,
        review_id=str(preview["review_id"]),
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="review-model-atomic-0001",
    )

    def fail_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic outbox failure")

    monkeypatch.setattr(service.projections, "enqueue", fail_projection)
    with pytest.raises(RuntimeError, match="outbox failure"):
        service.support_ai_reviews.apply(
            token=token,
            review_id=str(preview["review_id"]),
            model_operation_id="review-model-atomic-0001",
            expected_revision=1,
            teacher_result={"summary": "必须和投影待办一起保存。"},
            operation_id="review-apply-atomic-0001",
        )

    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 0
        state = connection.execute(
            "SELECT state FROM support_ai_review_sessions WHERE review_id = ?",
            (str(preview["review_id"]),),
        ).fetchone()[0]
    assert state == "proposal_ready"


def test_subject_delete_is_preview_bound_and_tombstones_projection_before_mapping(tmp_path: Path) -> None:
    service, _gateway, token, record = _fixture(tmp_path)
    preview = service.support_ai_reviews.prepare(
        token=token, record_id=str(record["record_id"]), expected_revision=1
    )
    service.support_ai_reviews.confirm(
        token=token,
        review_id=str(preview["review_id"]),
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="review-model-delete-0001",
    )
    saved = service.support_ai_reviews.apply(
        token=token,
        review_id=str(preview["review_id"]),
        model_operation_id="review-model-delete-0001",
        expected_revision=1,
        teacher_result={"summary": "准备删除的合成卡片"},
        operation_id="review-apply-delete-0001",
    )
    subject_id = str(record["subject_id"])
    deletion = service.support.preview_subject_deletion(token=token, subject_id=subject_id)
    assert deletion["projection_count"] == 1
    with pytest.raises(VaultError) as stale:
        service.support.delete_subject(
            token=token,
            subject_id=subject_id,
            operation_id="review-delete-stale-0001",
            confirmation_phrase="确认完整删除学生支持数据",
            preview_version="0" * 64,
        )
    assert stale.value.code == "support_delete_preview_changed"

    service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="review-delete-valid-0001",
        confirmation_phrase="确认完整删除学生支持数据",
        preview_version=str(deletion["preview_version"]),
    )
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_id = ?",
            (saved["entry_id"],),
        ).fetchone()[0] == 0
    assert service.work.query()["nodes"] == []
