from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> tuple[VaultService, str]:
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
    )
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=paths,
        )
    )
    service.ensure_plaintext_ready()
    return service, ""


def _template(service: VaultService, token: str, *, version: int = 1) -> dict[str, object]:
    return service.sop.publish_template(
        token=token,
        operation_id=f"discard-draft-template-v{version}",
        template_key="discard-synthetic",
        version=version,
        title=f"合成弃用事务模板 v{version}",
        steps=[
            {
                "key": "intake",
                "title": "记录合成事实",
                "details": "安全必做，不完成就不能结案",
                "required": True,
                "waivable": False,
                "safety_required": True,
                "depends_on": [],
            },
            {
                "key": "followup",
                "title": "合成后续跟进",
                "details": None,
                "required": False,
                "waivable": True,
                "safety_required": False,
                "depends_on": ["intake"],
            },
        ],
    )


def _subject(service: VaultService, token: str, suffix: str) -> dict[str, object]:
    return service.support.create_subject(
        token=token,
        operation_id=f"discard-draft-subject-{suffix}",
        source_student_id=f"synthetic-student-{suffix}",
        display_name=f"合成学生{suffix}",
        class_label="合成班",
    )


def _profile_update(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "dimensions": [],
        "open_questions": ["合成待核对问题？"],
        "support_focus": [],
    }


def _draft_update(subject: dict[str, object], *, base_revision: int = 0) -> dict[str, object]:
    return {
        "subject_id": str(subject["subject_id"]),
        "display_name": str(subject.get("display_name") or "学生"),
        "record_kind": "reported_statement",
        "source": "合成教师补充",
        "basis": None,
        "counterexample": None,
        "record_summary": f"{subject.get('display_name')}参与了待核对的合成同伴矛盾。",
        "scene": "冲突与安全事件",
        "category": "同伴冲突跟进",
        "observed_at": "2026-08-10T10:00:00+08:00",
        "review_at": None,
        "expires_at": None,
        "profile_base_revision": base_revision,
        "profile_update": _profile_update("正在持续了解这名合成学生的同伴相处。"),
    }


def _affair_with_draft(
    service: VaultService,
    token: str,
    template_version_id: str,
    subject: dict[str, object],
    *,
    operation_id: str,
    base_revision: int = 0,
) -> dict[str, object]:
    update = _draft_update(subject, base_revision=base_revision)

    def hook(
        connection: Any,
        vmk: bytes,
        affair_id: str,
        _occurrence_id: str,
    ) -> None:
        service.sop._insert_profile_update_drafts_in_connection(
            connection,
            vmk=vmk,
            affair_id=affair_id,
            updates=[update],
            source_task_id="synthetic-task-1",
        )

    return service.sop.create_affair(
        token=token,
        operation_id=operation_id,
        template_version_id=template_version_id,
        title="合成事务",
        summary="不含真实学生或学校流程",
        participant_refs=[],
        subject_ids=[str(subject["subject_id"])],
        verified_current_subject_ids=[str(subject["subject_id"])],
        pending_verifications=["双方是否已经分开且无人受伤？"],
        transaction_hook=hook,
    )


def test_create_affair_exposes_to_verify_and_pending_profile_drafts(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")

    affair = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-1",
    )

    assert affair["to_verify"] == ["双方是否已经分开且无人受伤？"]
    drafts = affair["profile_update_drafts"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["state"] == "pending"
    assert draft["subject_id"] == str(subject["subject_id"])
    assert draft["display_name"] == "合成学生甲"
    assert "合成同伴矛盾" in draft["record_summary"]
    assert draft["source_task_id"] == "synthetic-task-1"
    workspace = service.affairs.read(token=token, affair_id=str(affair["affair_id"]))
    assert workspace["to_verify"] == affair["to_verify"]
    assert workspace["profile_update_drafts"][0]["draft_id"] == draft["draft_id"]


def test_discard_affair_supersedes_unfinished_steps_and_is_final(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    affair = service.sop.create_affair(
        token=token,
        operation_id="discard-draft-create-2",
        template_version_id=str(template["template_version_id"]),
        title="合成事务",
        summary="安全必做步骤未完成，结案应被拒绝",
        participant_refs=["synthetic-participant"],
    )
    with pytest.raises(VaultError) as close_blocked:
        service.sop.close_affair(
            token=token,
            affair_id=str(affair["affair_id"]),
            operation_id="discard-draft-close-blocked",
            revision=int(affair["revision"]),
            closure_summary="必做步骤未完成不能结案",
        )
    assert close_blocked.value.code == "sop_required_steps_incomplete"

    discarded = service.sop.discard_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="discard-draft-discard-2",
        revision=int(affair["revision"]),
        reason="合成场景：双方已自行和解，不再走流程",
    )
    assert discarded["state"] == "discarded"
    assert discarded["discard_reason"] == "合成场景：双方已自行和解，不再走流程"
    steps = [
        *discarded["current_steps"],
        *discarded["completed_steps"],
        *discarded["preview_steps"],
    ]
    assert steps and all(step["state"] == "superseded" for step in steps)

    replay = service.sop.discard_affair(
        token=token,
        affair_id=str(affair["affair_id"]),
        operation_id="discard-draft-discard-2",
        revision=int(affair["revision"]),
        reason="合成场景：双方已自行和解，不再走流程",
    )
    assert replay["state"] == "discarded"

    with pytest.raises(VaultError) as second:
        service.sop.discard_affair(
            token=token,
            affair_id=str(affair["affair_id"]),
            operation_id="discard-draft-discard-again",
            revision=int(discarded["revision"]),
            reason="已弃用事务不能再次弃用",
        )
    assert second.value.code == "sop_affair_closed"
    assert second.value.message == "事务已弃用，不可重开"
    with pytest.raises(VaultError) as reopen:
        service.sop.reopen_affair(
            token=token,
            affair_id=str(affair["affair_id"]),
            operation_id="discard-draft-reopen",
            revision=int(discarded["revision"]),
            reason="弃用是终态，不能重开",
        )
    assert reopen.value.code == "sop_affair_not_closed"
    step = steps[0]
    with pytest.raises(VaultError) as completed:
        service.sop.complete_step(
            token=token,
            affair_id=str(affair["affair_id"]),
            step_instance_id=str(step["step_instance_id"]),
            operation_id="discard-draft-complete",
            revision=int(step["revision"]),
            outcome="completed",
            result="弃用后不能再操作步骤",
        )
    assert completed.value.code == "sop_affair_closed"
    assert completed.value.message == "事务已弃用，不可重开"
    with closing(service.database.connect()) as connection:
        events = {
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM affair_events WHERE affair_id = ?",
                (str(affair["affair_id"]),),
            ).fetchall()
        }
    assert "affair.discarded" in events


def test_workspace_discard_command_cancels_projection(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    created = service.affairs.create(
        token=token,
        operation_id="discard-draft-create-3",
        template_version_id=str(template["template_version_id"]),
        title="合成事务",
        summary=None,
        participant_refs=["synthetic-participant"],
    )

    discarded = service.affairs.advance(
        token=token,
        affair_id=str(created["affair_id"]),
        command="discard",
        operation_id="discard-draft-advance-3",
        expected_revision=int(created["revision"]),
        reason="合成场景：不再需要处理",
    )
    assert discarded["state"] == "discarded"
    with closing(service.database.connect()) as connection:
        state = connection.execute(
            """
            SELECT state FROM sensitive_work_groups
            WHERE source_kind = 'sensitive_affair' AND source_id = ?
            """,
            (str(created["affair_id"]),),
        ).fetchone()[0]
    assert str(state) == "cancelled"

    replay = service.affairs.advance(
        token=token,
        affair_id=str(created["affair_id"]),
        command="discard",
        operation_id="discard-draft-advance-3",
        expected_revision=int(created["revision"]),
        reason="合成场景：不再需要处理",
    )
    assert replay["state"] == "discarded"


def test_profile_draft_confirm_writes_record_and_profile_exactly_once(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")
    affair = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-4",
    )
    draft = affair["profile_update_drafts"][0]

    confirmed = service.sop.confirm_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-confirm-4",
    )
    assert confirmed["state"] == "confirmed"
    assert confirmed["confirmed_at"]
    assert confirmed["confirmed_record_id"]
    card = service.student_cards.get_card(
        token=token,
        subject_id=str(subject["subject_id"]),
    )
    assert card["current_profile"]["revision"] == 1
    assert "同伴相处" in card["current_profile"]["summary"]
    assert card["existing_records"][0]["record_kind"] == "reported_statement"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 1

    replay = service.sop.confirm_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-confirm-4",
    )
    assert replay["state"] == "confirmed"
    assert replay["confirmed_record_id"] == confirmed["confirmed_record_id"]
    repeated = service.sop.confirm_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-confirm-4b",
    )
    assert repeated["state"] == "confirmed"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 1


def test_profile_draft_confirm_surfaces_optimistic_lock_conflict(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")
    first = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-5a",
    )
    service.sop.confirm_profile_update_draft(
        token=token,
        affair_id=str(first["affair_id"]),
        draft_id=str(first["profile_update_drafts"][0]["draft_id"]),
        operation_id="discard-draft-confirm-5a",
    )
    second = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-5b",
        base_revision=0,
    )
    stale_draft = second["profile_update_drafts"][0]

    with pytest.raises(VaultError) as conflict:
        service.sop.confirm_profile_update_draft(
            token=token,
            affair_id=str(second["affair_id"]),
            draft_id=str(stale_draft["draft_id"]),
            operation_id="discard-draft-confirm-5b",
        )
    assert conflict.value.code == "student_profile_conflict"
    assert conflict.value.status_code == 409
    unchanged = service.sop.get_affair(token=token, affair_id=str(second["affair_id"]))
    assert unchanged["profile_update_drafts"][0]["state"] == "pending"


def test_profile_draft_discard_is_final(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")
    affair = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-6",
    )
    draft = affair["profile_update_drafts"][0]

    discarded = service.sop.discard_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-discard-6",
    )
    assert discarded["state"] == "discarded"
    replay = service.sop.discard_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-discard-6",
    )
    assert replay["state"] == "discarded"
    with pytest.raises(VaultError) as confirm:
        service.sop.confirm_profile_update_draft(
            token=token,
            affair_id=str(affair["affair_id"]),
            draft_id=str(draft["draft_id"]),
            operation_id="discard-draft-confirm-6",
        )
    assert confirm.value.code == "sop_profile_draft_not_pending"
    card = service.student_cards.get_card(
        token=token,
        subject_id=str(subject["subject_id"]),
    )
    assert card["existing_records"] == []


def test_discard_and_profile_draft_routes(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")
    affair = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-7",
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    headers = {
        "x-class-teacher-session": token,
        "x-class-teacher-client": "class-teacher-browser-v1",
    }

    detail = client.get(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    draft = detail.json()["profile_update_drafts"][0]
    assert draft["state"] == "pending"

    confirmed = client.post(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}"
        f"/profile-drafts/{draft['draft_id']}/confirm",
        headers=headers,
        json={"operation_id": "discard-draft-api-confirm-7"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "confirmed"
    replay = client.post(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}"
        f"/profile-drafts/{draft['draft_id']}/confirm",
        headers=headers,
        json={"operation_id": "discard-draft-api-confirm-7"},
    )
    assert replay.status_code == 200
    assert replay.json()["confirmed_record_id"] == confirmed.json()["confirmed_record_id"]

    refreshed = client.get(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}",
        headers=headers,
    ).json()
    discarded = client.post(
        f"/api/class-teacher/sop/affairs/{affair['affair_id']}/discard",
        headers=headers,
        json={
            "operation_id": "discard-draft-api-discard-7",
            "revision": int(refreshed["revision"]),
            "reason": "合成场景：事务不再需要处理",
        },
    )
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "discarded"


def test_confirmed_profile_draft_is_not_replaced_by_later_suggestions(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    template = _template(service, token)
    subject = _subject(service, token, "甲")
    affair = _affair_with_draft(
        service,
        token,
        str(template["template_version_id"]),
        subject,
        operation_id="discard-draft-create-8",
    )
    draft = affair["profile_update_drafts"][0]
    confirmed = service.sop.confirm_profile_update_draft(
        token=token,
        affair_id=str(affair["affair_id"]),
        draft_id=str(draft["draft_id"]),
        operation_id="discard-draft-confirm-8",
    )
    assert confirmed["state"] == "confirmed"

    # 同一（事务, 学生）的后续 AI 建议不得替换已确认草稿。
    vmk = service.sop._key_provider(token)
    later = _draft_update(subject, base_revision=1)
    later["record_summary"] = "后续合成建议试图改写已确认草稿。"
    with closing(service.database.connect()) as connection:
        with connection:
            inserted = service.sop._insert_profile_update_drafts_in_connection(
                connection,
                vmk=vmk,
                affair_id=str(affair["affair_id"]),
                updates=[later],
                source_task_id="synthetic-task-2",
            )
    assert inserted == []

    refreshed = service.sop.get_affair(token=token, affair_id=str(affair["affair_id"]))
    drafts = refreshed["profile_update_drafts"]
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft["draft_id"]
    assert drafts[0]["state"] == "confirmed"
    assert drafts[0]["revision"] == confirmed["revision"]
    assert "试图改写" not in drafts[0]["record_summary"]
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 1
