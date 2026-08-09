from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.model_approval import FakeApprovedModelGateway
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _service(tmp_path: Path, result: dict[str, object]) -> VaultService:
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
        model_gateway=FakeApprovedModelGateway(
            result=json.dumps(result, ensure_ascii=False),
        ),
    )


def _ready(
    tmp_path: Path,
    result: dict[str, object],
) -> tuple[VaultService, str, str]:
    service = _service(tmp_path, result)
    service.ensure_plaintext_ready()
    token = ""
    subject = service.support.create_subject(
        token=token,
        operation_id="student-card-subject-create",
        source_student_id="synthetic-student-001",
        display_name="合成学生甲",
        class_label="合成一班",
    )
    return service, token, str(subject["subject_id"])


def _proposal() -> dict[str, object]:
    return {
        "kind": "proposal",
        "proposal": {
            "portrait": {
                "summary": "需要先核实作业安排",
                "strengths": ["愿意主动说明情况"],
                "needs": ["拆分近期任务"],
                "open_questions": ["本周可用时间是什么"],
            },
            "sop": {
                "title": "作业安排支持流程",
                "steps": ["教师核实情况", "共同拆分任务", "约定复查"],
                "review_date": "2026-08-10",
            },
        },
    }


def _model_result(
    service: VaultService,
    token: str,
    subject_id: str,
) -> dict[str, object]:
    preview = service.model_approval.prepare(
        token=token,
        purpose="student_support_note",
        source_text="合成学生甲说最近作业安排有困难",
        context={
            "subject_id": subject_id,
            "teacher_quote": "合成学生甲说最近作业安排有困难",
        },
    )
    return service.model_approval.confirm(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="student-card-model-confirm",
    )


def test_existing_subjects_render_as_one_empty_card_each(tmp_path: Path) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())

    result = service.student_cards.list_cards(token=token)

    assert len(result["items"]) == 1
    card = result["items"][0]
    assert card["subject"]["subject_id"] == subject_id
    assert card["subject"]["display_name"] == "合成学生甲"
    assert card["entries"] == []
    assert card["existing_records"] == []
    assert card["support_plans"] == []
    assert card["current_profile"] == {
        "entry_id": None,
        "revision": 0,
        "summary": "",
        "dimensions": [],
        "open_questions": [],
        "support_focus": [],
        "updated_at": None,
    }


def test_current_profile_is_merged_in_place_without_creating_versions(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())
    vmk = service.ensure_plaintext_ready()
    first_profile = {
        "summary": "当前愿意表达困难，也需要拆分长任务。",
        "dimensions": [{
            "key": "learning_ability",
            "label": "学习与能力",
            "items": ["长任务需要拆成短步骤"],
        }],
        "open_questions": ["书面步骤卡是否更有效"],
        "support_focus": [{
            "key": "focus_task_steps",
            "title": "长任务支持",
            "need": "降低开始长任务的困难",
            "effective_methods": [],
            "next_actions": ["尝试书面步骤卡"],
        }],
    }
    second_profile = {
        "summary": "愿意表达困难，书面步骤卡比连续口头提醒更有效。",
        "dimensions": [{
            "key": "effective_methods",
            "label": "已验证有效的方法",
            "items": ["书面步骤卡比连续口头提醒更有效"],
        }],
        "open_questions": [],
        "support_focus": [{
            "key": "focus_task_steps",
            "title": "长任务支持",
            "need": "逐步提升独立完成长任务的能力",
            "effective_methods": ["书面步骤卡"],
            "next_actions": ["逐渐减少步骤卡提示"],
        }],
    }

    with closing(service.database.connect()) as connection:
        with connection:
            first = service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=vmk,
                subject_id=subject_id,
                profile_update=first_profile,
                expected_revision=0,
                operation_id="profile-current-save-001",
                model_operation_id="profile-model-result-001",
                teacher_quote="合成第一轮输入",
                model_draft="合成第一轮草稿",
            )
        with connection:
            second = service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=vmk,
                subject_id=subject_id,
                profile_update=second_profile,
                expected_revision=int(first["revision"]),
                operation_id="profile-current-save-002",
                model_operation_id="profile-model-result-002",
                teacher_quote="合成第二轮输入",
                model_draft="合成第二轮草稿",
            )
        count = connection.execute(
            "SELECT COUNT(*) FROM student_card_entries WHERE subject_id=? AND state='active'",
            (subject_id,),
        ).fetchone()[0]

    card = service.student_cards.get_card(token=token, subject_id=subject_id)
    current = card["current_profile"]

    assert first["entry_id"] == second["entry_id"]
    assert int(second["revision"]) == int(first["revision"]) + 1
    assert count == 1
    assert len(card["entries"]) == 1
    assert current["summary"] == second_profile["summary"]
    assert {item["key"] for item in current["dimensions"]} == {
        "learning_ability",
        "effective_methods",
    }
    assert current["support_focus"][0]["effective_methods"] == ["书面步骤卡"]
    contexts = service.student_cards.model_contexts_for_mentions(
        token=token,
        class_label="合成一班",
        text="合成学生甲和同学发生分歧，需要先了解双方情况。",
    )
    assert len(contexts) == 1
    assert contexts[0]["display_name"] == "合成学生甲"
    assert contexts[0]["profile"]["summary"] == second_profile["summary"]


def test_teacher_confirmation_persists_card_and_only_anonymous_projections(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())
    model = _model_result(service, token, subject_id)
    structure = _proposal()["proposal"]

    saved = service.student_cards.confirm_structure(
        token=token,
        subject_id=subject_id,
        model_operation_id=str(model["operation_id"]),
        operation_id="student-card-save-001",
        portrait=dict(structure["portrait"]),
        sop=dict(structure["sop"]),
    )
    replay = service.student_cards.confirm_structure(
        token=token,
        subject_id=subject_id,
        model_operation_id=str(model["operation_id"]),
        operation_id="student-card-save-001",
        portrait=dict(structure["portrait"]),
        sop=dict(structure["sop"]),
    )
    cards = service.student_cards.list_cards(token=token)
    ordinary = service.work.query(as_of="2026-08-10")

    assert saved["entry_id"] == replay["entry_id"]
    assert saved["projection_state"] == "applied"
    entry = cards["items"][0]["entries"][0]
    assert entry["teacher_quote"] == "合成学生甲说最近作业安排有困难"
    assert entry["portrait"]["summary"] == "需要先核实作业安排"
    assert entry["sop"]["steps"] == ["教师核实情况", "共同拆分任务", "约定复查"]
    assert {node["title"] for node in ordinary["nodes"]} == {"学生支持待跟进"}
    assert all(node["classification"] == "restricted_projection" for node in ordinary["nodes"])
    ordinary_bytes = service.ordinary_database.database_path.read_bytes()
    assert "合成学生甲".encode() not in ordinary_bytes
    assert "最近作业安排有困难".encode() not in ordinary_bytes
    assert "需要先核实作业安排".encode() not in ordinary_bytes
    assert "合成学生甲".encode() in service.database.database_path.read_bytes()


def test_model_follow_up_cannot_be_written_as_final_structure(tmp_path: Path) -> None:
    follow_up = {"kind": "follow_up", "questions": ["困难主要发生在哪一天？"]}
    service, token, subject_id = _ready(tmp_path, follow_up)
    model = _model_result(service, token, subject_id)

    assert model["response_kind"] == "follow_up"
    assert model["follow_up_questions"] == ["困难主要发生在哪一天？"]
    with pytest.raises(VaultError) as error:
        service.student_cards.confirm_structure(
            token=token,
            subject_id=subject_id,
            model_operation_id=str(model["operation_id"]),
            operation_id="student-card-follow-up-save",
            portrait={
                "summary": "不能提前落库",
                "strengths": [],
                "needs": [],
                "open_questions": [],
            },
            sop={"title": "不能提前落库", "steps": ["等待回答"], "review_date": None},
        )
    assert error.value.code == "student_card_follow_up_required"


def test_sensitive_outbox_recovers_after_ordinary_projection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())
    model = _model_result(service, token, subject_id)
    structure = _proposal()["proposal"]
    original = service.work.apply_projection_envelope

    def fail_projection(**_kwargs):
        raise RuntimeError("synthetic ordinary store unavailable")

    monkeypatch.setattr(service.work, "apply_projection_envelope", fail_projection)
    saved = service.student_cards.confirm_structure(
        token=token,
        subject_id=subject_id,
        model_operation_id=str(model["operation_id"]),
        operation_id="student-card-save-pending",
        portrait=dict(structure["portrait"]),
        sop=dict(structure["sop"]),
    )
    assert saved["projection_state"] == "pending"
    assert service.work.query(as_of="2026-08-10")["nodes"] == []

    monkeypatch.setattr(service.work, "apply_projection_envelope", original)
    cards = service.student_cards.list_cards(token=token)

    assert cards["items"][0]["entries"][0]["projection_state"] == "applied"
    with closing(service.database.connect()) as connection:
        rows = connection.execute(
            "SELECT state, attempts FROM sensitive_work_projection_outbox"
        ).fetchall()
    assert all(str(row["state"]) == "applied" for row in rows)
    assert all(int(row["attempts"]) == 2 for row in rows)


def test_full_student_delete_removes_model_preview_and_result_payloads(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())
    _model_result(service, token, subject_id)
    with closing(service.database.connect()) as connection:
        artifact = connection.execute(
            """
            SELECT preview_payload_object_id, result_payload_object_id
            FROM student_model_artifacts WHERE subject_id = ?
            """,
            (subject_id,),
        ).fetchone()
    assert artifact is not None
    object_ids = {
        str(artifact["preview_payload_object_id"]),
        str(artifact["result_payload_object_id"]),
    }

    deleted = service.support.delete_subject(
        token=token,
        subject_id=subject_id,
        operation_id="student-card-delete-artifacts",
        confirmation_phrase="确认完整删除学生支持数据",
    )

    assert deleted["deleted"] is True
    with closing(service.database.connect()) as connection:
        remaining = connection.execute(
            """
            SELECT object_id FROM encrypted_objects
            WHERE object_id IN (?, ?)
            """,
            tuple(sorted(object_ids)),
        ).fetchall()
        links = connection.execute(
            "SELECT preview_id FROM student_model_artifacts WHERE subject_id = ?",
            (subject_id,),
        ).fetchall()
    assert remaining == []
    assert links == []


def test_legacy_anonymous_preview_and_student_card_routes_are_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, subject_id = _ready(tmp_path, _proposal())

    def reject_bulk_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("selected student read must not scan or drain the class")

    monkeypatch.setattr(service.student_cards, "drain_projection_outbox", reject_bulk_read)
    monkeypatch.setattr(service.student_cards.projections, "drain", reject_bulk_read)
    monkeypatch.setattr(service.student_cards.support, "list_subjects", reject_bulk_read)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    headers = {
        "x-class-teacher-client": "class-teacher-browser-v1",
        "x-class-teacher-session": token,
    }

    preview = client.post(
        "/api/class-teacher/model/previews",
        headers=headers,
        json={
            "purpose": "student_support_note",
            "source_text": "合成学生甲希望一起拆分任务",
            "subject_id": subject_id,
        },
    )
    cards = client.get("/api/class-teacher/student-cards", headers=headers)
    selected_card = client.get(
        f"/api/class-teacher/support/subjects/{subject_id}/student-card",
        headers=headers,
    )

    assert preview.status_code == 404
    assert cards.status_code == 404
    assert selected_card.status_code == 200
    assert selected_card.json()["subject"]["subject_id"] == subject_id
