from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.model_approval import FakeApprovedModelGateway
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _service(
    tmp_path: Path,
    *,
    gateway=None,
) -> VaultService:
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )
    return VaultService(
        context,
        model_gateway=gateway,
    )


def _ready(tmp_path: Path, *, gateway=None) -> tuple[VaultService, str]:
    service = _service(tmp_path, gateway=gateway)
    service.ensure_plaintext_ready()
    return service, ""


def test_preview_is_exact_anonymous_and_plaintext_stays_out_of_ordinary_db(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway()
    service, token = _ready(tmp_path, gateway=gateway)
    source = "张三同学本次数学 88分，希望一起梳理下一步"

    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text=source,
    )

    assert preview["exact_payload"]["student_alias"] == "学生A"
    assert preview["exact_payload"]["task_text"] == (
        "学生A本次数学 [已移除具体数值]，希望一起梳理下一步"
    )
    assert preview["removed_categories"] == ["姓名或称呼", "具体分数或名次"]
    assert preview["max_physical_requests"] == 1
    assert "json" in json.dumps(preview["exact_payload"], ensure_ascii=False)
    assert source.encode("utf-8") not in service.ordinary_database.database_path.read_bytes()
    assert source.encode("utf-8") not in service.database.database_path.read_bytes()
    assert gateway.calls == []


def test_selected_student_given_name_is_removed_from_exact_preview(
    tmp_path: Path,
) -> None:
    service, token = _ready(
        tmp_path,
        gateway=FakeApprovedModelGateway(),
    )

    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="小明今天希望调整作业节奏",
        identity_terms=("王小明",),
    )

    assert preview["exact_payload"]["task_text"] == (
        "学生A今天希望调整作业节奏"
    )
    assert preview["removed_categories"] == ["姓名或称呼"]


@pytest.mark.parametrize(
    ("source_text", "expected_text"),
    (
        ("提醒全班的王小明明天交材料", "提醒全班的学生B明天交材料"),
        ("通知班级中的王小明8月30日交材料", "通知班级中的学生B8月30日交材料"),
        ("提醒全班同学中的王小明明天交材料", "提醒全班同学中的学生B明天交材料"),
        ("提醒全班内王小明明天交材料", "提醒全班内学生B明天交材料"),
    ),
)
def test_organization_phrase_is_preserved_while_following_name_is_removed(
    tmp_path: Path,
    source_text: str,
    expected_text: str,
) -> None:
    service, token = _ready(
        tmp_path,
        gateway=FakeApprovedModelGateway(),
    )

    named_preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text=source_text,
    )
    organization_preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="提醒全班同学明天交材料",
    )

    assert named_preview["exact_payload"]["task_text"] == expected_text
    assert named_preview["removed_categories"] == ["姓名或称呼"]
    assert organization_preview["exact_payload"]["task_text"] == (
        "提醒全班同学明天交材料"
    )
    assert organization_preview["removed_categories"] == []


def test_confirm_uses_fingerprint_and_never_dispatches_same_preview_twice(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway(result="合成结构化草稿与一个追问")
    service, token = _ready(tmp_path, gateway=gateway)
    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="张三同学希望调整作业节奏",
    )

    with pytest.raises(VaultError) as changed:
        service.model_approval.confirm(
            token=token,
            preview_id=str(preview["preview_id"]),
            fingerprint="0" * 64,
            operation_id="model-confirm-changed",
        )
    first = service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-confirm-001",
    )
    replay = service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-confirm-001",
    )

    assert changed.value.code == "class_teacher_model_preview_changed"
    assert first == replay
    assert first["state"] == "succeeded"
    assert first["physical_request_count"] == 1
    assert first["draft_text"] == "合成结构化草稿与一个追问"
    assert first["teacher_confirmation_required"] is True
    assert len(gateway.calls) == 1


def test_operation_id_cannot_be_reused_for_a_different_preview(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway()
    service, token = _ready(tmp_path, gateway=gateway)
    first = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="学生需要确认第一项任务",
    )
    second = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="学生需要确认第二项任务",
    )
    service.model_approval.confirm(
        token=token,
        preview_id=str(first["preview_id"]),
        fingerprint=str(first["fingerprint"]),
        operation_id="model-operation-shared",
    )

    with pytest.raises(VaultError) as conflict:
        service.model_approval.confirm(
            token=token,
            preview_id=str(second["preview_id"]),
            fingerprint=str(second["fingerprint"]),
            operation_id="model-operation-shared",
        )

    assert conflict.value.code == "class_teacher_model_operation_conflict"
    assert len(gateway.calls) == 1


def test_default_live_gateway_is_disabled_before_send_and_costs_zero(
    tmp_path: Path,
) -> None:
    service, token = _ready(tmp_path)
    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="学生近期希望重新安排复习步骤",
    )

    result = service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-disabled-confirm",
    )

    assert preview["model_enabled"] is False
    assert result["state"] == "failed_before_send"
    assert result["physical_request_count"] == 0
    assert result["error_category"] == "model_disabled"


def test_unknown_result_is_recorded_once_and_never_auto_retried(
    tmp_path: Path,
) -> None:
    gateway = FakeApprovedModelGateway(unknown=True)
    service, token = _ready(tmp_path, gateway=gateway)
    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="学生希望确认下一次沟通时间",
    )

    first = service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-unknown-confirm",
    )
    replay = service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="model-unknown-confirm",
    )

    assert first == replay
    assert first["state"] == "result_unknown"
    assert first["physical_request_count"] == 1
    assert len(gateway.calls) == 1


def test_restart_after_claim_fails_closed_without_dispatch(tmp_path: Path) -> None:
    gateway = FakeApprovedModelGateway()
    service, token = _ready(tmp_path, gateway=gateway)
    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="学生需要确认材料提交方式",
    )
    with closing(service.ordinary_database.connect()) as connection:
        with connection:
            connection.execute(
                """
                UPDATE model_approval_operations
                SET operation_id = ?, state = 'claimed'
                WHERE preview_id = ?
                """,
                ("model-interrupted-001", str(preview["preview_id"])),
            )

    result = service.model_approval.status(
        token=token,
        operation_id="model-interrupted-001",
    )

    assert result["state"] == "result_unknown"
    assert result["physical_request_count"] == 1
    assert result["error_category"] == "interrupted_after_claim"
    assert gateway.calls == []


def test_hard_blocked_identifier_never_creates_preview_metadata(
    tmp_path: Path,
) -> None:
    service, token = _ready(tmp_path, gateway=FakeApprovedModelGateway())

    with pytest.raises(VaultError) as blocked:
        service.model_approval.prepare(
            token=token,
            purpose="student_support_note",
            source_text="家长电话 13800138000",
        )

    assert blocked.value.code == "class_teacher_model_content_blocked"
    assert not service.ordinary_database.exists
