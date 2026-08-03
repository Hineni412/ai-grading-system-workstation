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
from backend.class_teacher.protection import FakeCurrentUserProtection
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成学生卡保险箱密码-足够长-001"


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
        protection_provider=FakeCurrentUserProtection(b"C" * 32),
        model_gateway=FakeApprovedModelGateway(
            result=json.dumps(result, ensure_ascii=False),
        ),
    )


def _unlocked(
    tmp_path: Path,
    result: dict[str, object],
) -> tuple[VaultService, str, str]:
    service = _service(tmp_path, result)
    initialized = service.initialize(
        password=PASSWORD,
        operation_id="student-card-vault-init",
    )
    token = str(initialized["session_token"])
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
    service, token, subject_id = _unlocked(tmp_path, _proposal())

    result = service.student_cards.list_cards(token=token)

    assert len(result["items"]) == 1
    card = result["items"][0]
    assert card["subject"]["subject_id"] == subject_id
    assert card["subject"]["display_name"] == "合成学生甲"
    assert card["entries"] == []
    assert card["existing_records"] == []
    assert card["support_plans"] == []


def test_teacher_confirmation_persists_card_and_only_anonymous_projections(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path, _proposal())
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
    assert "合成学生甲".encode() not in service.database.database_path.read_bytes()


def test_model_follow_up_cannot_be_written_as_final_structure(tmp_path: Path) -> None:
    follow_up = {"kind": "follow_up", "questions": ["困难主要发生在哪一天？"]}
    service, token, subject_id = _unlocked(tmp_path, follow_up)
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
    service, token, subject_id = _unlocked(tmp_path, _proposal())
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


def test_full_student_delete_removes_model_preview_and_result_ciphertexts(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path, _proposal())
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


def test_student_card_api_wires_exact_preview_to_confirmed_sensitive_write(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _unlocked(tmp_path, _proposal())
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)
    headers = {
        "x-class-teacher-client": "class-teacher-browser-v1",
        "x-class-teacher-session": token,
    }

    cards = client.get("/api/class-teacher/student-cards", headers=headers)
    preview = client.post(
        "/api/class-teacher/model/previews",
        headers=headers,
        json={
            "purpose": "student_support_note",
            "source_text": "合成学生甲希望一起拆分任务",
            "subject_id": subject_id,
        },
    )
    preview_body = preview.json()
    model = client.post(
        f"/api/class-teacher/model/previews/{preview_body['preview_id']}/confirm",
        headers=headers,
        json={
            "fingerprint": preview_body["fingerprint"],
            "operation_id": "student-card-api-model",
        },
    )
    proposal = _proposal()["proposal"]
    saved = client.post(
        f"/api/class-teacher/student-cards/{subject_id}/entries/confirm",
        headers=headers,
        json={
            "model_operation_id": "student-card-api-model",
            "operation_id": "student-card-api-save",
            "portrait": proposal["portrait"],
            "sop": proposal["sop"],
        },
    )

    assert cards.status_code == 200
    assert len(cards.json()["items"]) == 1
    assert preview.status_code == 200
    assert preview_body["exact_payload"]["student_alias"] == "学生A"
    assert preview_body["exact_payload"]["task_text"] == "学生A希望一起拆分任务"
    assert "合成学生甲" not in preview_body["exact_payload"]["task_text"]
    assert model.status_code == 200
    assert model.json()["response_kind"] == "proposal"
    assert saved.status_code == 200
    replay = client.post(
        f"/api/class-teacher/student-cards/{subject_id}/entries/confirm",
        headers=headers,
        json={
            "model_operation_id": "student-card-api-model",
            "operation_id": "student-card-api-save",
            "portrait": proposal["portrait"],
            "sop": proposal["sop"],
        },
    )
    assert replay.status_code == 200
    assert replay.json()["saved"] is True
    assert replay.json()["entry_id"] == saved.json()["entry_id"]
    assert saved.json()["projection_state"] == "applied"
    assert client.get(
        "/api/class-teacher/student-cards",
        headers=headers,
    ).json()["items"][0]["entries"][0]["teacher_quote"] == (
        "合成学生甲希望一起拆分任务"
    )
