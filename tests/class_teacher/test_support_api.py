from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "合成支持接口密码-足够长-001"


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
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
        operation_id="initialize-support-api",
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app), {
        "x-class-teacher-session": str(initialized["session_token"]),
        "x-class-teacher-client": "class-teacher-browser-v1",
    }


def test_support_quick_evidence_and_attention_routes_are_wired(
    tmp_path: Path,
) -> None:
    client, headers = _client(tmp_path)
    subject_response = client.post(
        "/api/class-teacher/support/subjects",
        headers=headers,
        json={
            "operation_id": "api-create-subject",
            "source_student_id": "synthetic-api-001",
            "display_name": "合成接口学生",
            "class_label": "合成一班",
        },
    )
    assert subject_response.status_code == 200
    assert subject_response.headers["cache-control"] == "no-store, max-age=0"
    subject_id = subject_response.json()["subject_id"]

    quick_response = client.post(
        "/api/class-teacher/quick-inbox",
        headers=headers,
        json={
            "operation_id": "api-create-quick-text",
            "text": "合成接口文字速记。",
            "subject_id": subject_id,
        },
    )
    assert quick_response.status_code == 200
    assert quick_response.json()["voice_inbox_available"] is False

    evidence_response = client.post(
        "/api/class-teacher/evidence/batches",
        headers=headers,
        json={
            "operation_id": "api-confirm-evidence",
            "batch": {
                "source_kind": "confirmed_spreadsheet",
                "teacher_confirmed": True,
                "source_label": "合成接口证据",
                "assessments": [{
                    "title": "合成接口考试",
                    "subject_name": "数学",
                    "occurred_on": "2026-08-03",
                    "max_score": 100,
                    "rank_scope": "class",
                    "participant_count": 40,
                    "assessment_nature": "unit",
                    "results": [{
                        "subject_id": subject_id,
                        "result_state": "normal",
                        "score": 0,
                        "rank": 40,
                    }],
                }],
            },
        },
    )
    assert evidence_response.status_code == 200
    evidence = client.get(
        f"/api/class-teacher/evidence/subjects/{subject_id}",
        headers=headers,
    ).json()["items"][0]

    card_response = client.post(
        "/api/class-teacher/attention-cards",
        headers=headers,
        json={
            "operation_id": "api-create-attention",
            "evidence_version_id": evidence["evidence_version_id"],
            "observed_fact": "合成接口考试记录为 0 分",
            "comparability": "insufficient_information",
            "limitations": ["只有一条证据"],
            "verification_question": "是否需要核实考试情况？",
            "low_risk_next_step": "建议先了解近期情况",
            "evidence_sufficiency": "不足",
            "review_suggestion": "后续复查",
        },
    )
    assert card_response.status_code == 200
    assert card_response.json()["risk_score"] is None
