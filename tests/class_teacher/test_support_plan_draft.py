from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.support_plan_draft_service import SupportPlanDraftService
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SyntheticDraftModel:
    """假 configured model：按 test_intake_shared_ai_tasks 的假网关模式。"""

    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def destination_snapshot(self) -> dict[str, object]:
        return {"destination_fingerprint": "a" * 64}

    def invoke_workspace_task(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        return json.dumps(self.result, ensure_ascii=False)

    def physical_request_count(self, _operation_id: str) -> int:
        return len(self.calls)


class _FakeStudentCards:
    def model_context(self, *, token: str, subject_id: str) -> dict[str, object]:
        return {
            "subject_ref": {"kind": "student", "id": subject_id, "revision": "1"},
            "display_name": "合成学生",
            "class_label": "一班",
            "profile": {
                "summary": "能按计划完成任务。",
                "dimensions": [],
                "support_focus": ["任务核对习惯"],
                "effective_methods": ["课前步骤卡"],
                "open_questions": [],
            },
            "academic_summary": None,
            "support_plans": [],
        }


class _FakeSupport:
    def get_subject(self, *, token: str, subject_id: str) -> dict[str, object]:
        return {
            "subject_id": subject_id,
            "revision": 1,
            "display_name": "合成学生",
            "class_label": "一班",
        }


def _draft_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "contract_version": "class_teacher_support_plan_draft.v1",
        "goal": "两周内形成稳定的任务核对习惯",
        "support_actions": ["课前提供步骤卡", "课后由教师复查一次"],
        "review_at": "2099-09-01",
    }
    result.update(overrides)
    return result


def _service(
    result: dict[str, object] | None,
) -> tuple[SupportPlanDraftService, SyntheticDraftModel | None]:
    model = None if result is None else SyntheticDraftModel(result)
    service = SupportPlanDraftService(
        model_gateway=model,
        student_cards=_FakeStudentCards(),
        support=_FakeSupport(),
    )
    return service, model


def test_draft_plan_returns_contract_fields(tmp_path: Path) -> None:
    service, model = _service(_draft_result())

    draft = service.draft_plan(
        token="",
        subject_id="subject-1",
        operation_id="synthetic-draft-operation-001",
    )

    assert draft == {
        "goal": "两周内形成稳定的任务核对习惯",
        "support_actions": ["课前提供步骤卡", "课后由教师复查一次"],
        "review_at": "2099-09-01",
    }
    assert model is not None and len(model.calls) == 1
    call = model.calls[0]
    assert call["operation_id"] == "synthetic-draft-operation-001"
    assert call["purpose"] == "class_teacher_support_plan_draft"
    assert call["expected_destination_fingerprint"] == "a" * 64
    messages = call["messages"]
    assert messages[0]["role"] == "system"
    assert "class_teacher_support_plan_draft.v1" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"].startswith("当前学生档案与支持情况：")
    assert "课前步骤卡" in messages[1]["content"]


def test_draft_plan_drops_extra_fields_and_truncates(tmp_path: Path) -> None:
    service, _model = _service(_draft_result(
        goal="长" * 1500,
        support_actions=["行" * 300, "  ", "正常行动"],
        diagnosis="模型自行作出的诊断",
        risk_score=99,
    ))

    draft = service.draft_plan(
        token="",
        subject_id="subject-1",
        operation_id="synthetic-draft-operation-002",
    )

    assert set(draft) == {"goal", "support_actions", "review_at"}
    assert len(str(draft["goal"])) == 1000
    assert draft["support_actions"] == ["行" * 200, "正常行动"]


@pytest.mark.parametrize("bad_review_at", [None, "", "下个月复查", "2099-13-40"])
def test_draft_plan_falls_back_review_at(bad_review_at: object) -> None:
    result = _draft_result()
    if bad_review_at is None:
        del result["review_at"]
    else:
        result["review_at"] = bad_review_at
    service, _model = _service(result)

    draft = service.draft_plan(
        token="",
        subject_id="subject-1",
        operation_id="synthetic-draft-operation-003",
    )

    expected = (datetime.now(UTC).date() + timedelta(days=14)).isoformat()
    assert draft["review_at"] == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"goal": "", "support_actions": ["行动"]},
        {"goal": "   ", "support_actions": ["行动"]},
        {"goal": "目标", "support_actions": []},
        {"goal": "目标", "support_actions": ["", "  "]},
        {"goal": "目标", "support_actions": None},
    ],
)
def test_draft_plan_rejects_empty_goal_or_actions(overrides: dict[str, object]) -> None:
    service, _model = _service(_draft_result(**overrides))

    with pytest.raises(VaultError) as caught:
        service.draft_plan(
            token="",
            subject_id="subject-1",
            operation_id="synthetic-draft-operation-004",
        )

    assert caught.value.code == "support_plan_draft_invalid_result"
    assert caught.value.status_code == 422


def test_draft_plan_unavailable_without_configured_model() -> None:
    service, _model = _service(None)

    with pytest.raises(VaultError) as caught:
        service.draft_plan(
            token="",
            subject_id="subject-1",
            operation_id="synthetic-draft-operation-005",
        )

    assert caught.value.code == "support_plan_draft_unavailable"
    assert caught.value.status_code == 422


def _vault(tmp_path: Path) -> VaultService:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.commit()
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
            db_path=grading,
        ),
    )
    return VaultService(context)


def _client(service: VaultService) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code}},
        )

    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app)


def test_ai_draft_route_returns_draft(tmp_path: Path) -> None:
    service = _vault(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="synthetic-draft-subject-create",
        source_student_id="SYN-DRAFT-001",
        display_name="合成起草学生",
        class_label="一班",
    )
    model = SyntheticDraftModel(_draft_result())
    service.support_plan_drafts.model_gateway = model
    client = _client(service)

    response = client.post(
        f"/api/class-teacher/support/subjects/{subject['subject_id']}/plans/ai-draft",
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
        json={"operation_id": "synthetic-draft-route-001"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "goal": "两周内形成稳定的任务核对习惯",
        "support_actions": ["课前提供步骤卡", "课后由教师复查一次"],
        "review_at": "2099-09-01",
    }
    assert len(model.calls) == 1
    user_content = str(model.calls[0]["messages"][1]["content"])
    assert "合成起草学生" in user_content
