"""AI 组卷 LLM 链路与路由契约测试：全程 mock 模型，不碰真实配置与数据。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.jobs.default_handlers as default_handlers
from backend.api.app import create_app
from backend.api.dependencies import (
    get_ai_assembly_session_service,
    get_assembly_workspace_service,
    get_job_manager,
    get_question_bank_read_service,
)
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from llm_client import LLMResponseFormatError
from question_bank.database.schema import initialize_database
from question_bank.services.assembly_workspace_service import (
    AiAssemblySessionService,
    AssemblyWorkspaceService,
)
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
from tests.question_bank_support import QuestionBankTestStore
from tests.test_ai_assembly_service import _add_question

_CATALOG = load_curriculum_catalog()
_SECTION = _CATALOG["volumes"][0]["chapters"][0]["sections"][0]
_KP_A1 = _SECTION["knowledge_points"][0]["display_name"]
_KP_A2 = _SECTION["knowledge_points"][1]["display_name"]

_FREE_TEXT = "出一份勾股定理小测，不要出偏题"
_NEW_INSTRUCTION = "解答题再加一道"


class _FakeLLMClient:
    """mock 的 content_generation 通道客户端：记录 prompt，按预设返回或抛错。"""

    def __init__(
        self,
        payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self.prompts: list[str] = []
        self.settings = SimpleNamespace(config_model="fake-assembly-model")

    def json_from_text(
        self,
        prompt: str,
        model=None,
        extra_kwargs=None,
        response_format=None,
        *,
        request_kind=None,
    ) -> dict:
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return dict(self._payload or {})


def _spec_llm_payload() -> dict:
    return {
        "title": "勾股定理小测",
        "rows": [
            {
                "question_type": "选择题",
                "count": 2,
                "knowledge_points": [_KP_A1],
                "difficulty": 5,
                "score": 3,
            }
        ],
    }


def _spec_request_payload(**overrides) -> dict:
    payload = {
        "template_paper_id": None,
        "scope_keys": [_KP_A1],
        "difficulty_ratio": {"easy": 1, "mid": 2},
        "type_counts": {"选择题": 2},
        "exam_types": [],
        "years": [],
        "free_text": _FREE_TEXT,
        "current_spec": None,
        "locked_question_ids": [101, 102],
        "new_instruction": _NEW_INSTRUCTION,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def ai_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """装配测试 app；返回 (client, manager, store, workspace, set_llm)。"""

    data_root = tmp_path / "data"
    store = QuestionBankTestStore(data_root / "databases" / "question_bank.db")
    initialize_database(store.db_path)
    workspace = AssemblyWorkspaceService(data_root)
    session_service = AiAssemblySessionService(data_root)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    holder: dict[str, _FakeLLMClient | None] = {"client": None}
    monkeypatch.setattr(
        default_handlers,
        "_content_generation_llm_client",
        lambda: holder["client"],
    )
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        data_root=data_root,
        question_bank_db_path=store.db_path,
    )
    app = create_app()
    app.dependency_overrides[get_assembly_workspace_service] = lambda: workspace
    app.dependency_overrides[get_ai_assembly_session_service] = lambda: session_service
    app.dependency_overrides[get_question_bank_read_service] = lambda: store.reader
    app.dependency_overrides[get_job_manager] = lambda: manager
    client = TestClient(app)
    yield client, manager, store, workspace, holder
    manager.shutdown()


def test_spec_job_success_prompt_constraints_and_public_whitelist(ai_client) -> None:
    client, manager, _, _, holder = ai_client
    fake = _FakeLLMClient(payload=_spec_llm_payload())
    holder["client"] = fake

    submit = client.post(
        "/api/question-assembly/ai/spec-jobs",
        json={"request": _spec_request_payload()},
    )
    assert submit.status_code == 202
    job_id = submit.json()["id"]
    manager.wait(job_id, timeout=10)

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    body = job.json()
    assert body["job_type"] == "ai_assembly_spec"
    assert body["status"] == "succeeded"

    # result 只暴露 spec payload 与模型名。
    assert set(body["result"]) == {"spec", "model_name"}
    assert body["result"]["model_name"] == "fake-assembly-model"
    spec = body["result"]["spec"]
    assert spec["title"] == "勾股定理小测"
    assert spec["rows"][0]["question_type"] == "选择题"
    assert spec["scope_knowledge_points"] == [_KP_A1]

    # prompt 必须带锁定约束与本次新指令。
    assert len(fake.prompts) == 1
    prompt = fake.prompts[0]
    assert "101" in prompt and "102" in prompt
    assert "原样保留" in prompt
    assert _NEW_INSTRUCTION in prompt
    assert _FREE_TEXT in prompt

    # payload 脱敏白名单：模板 id、题型数量、是否有口语、锁定题数。
    assert body["payload"] == {
        "type_counts": {"选择题": 2},
        "has_free_text": True,
        "locked_count": 2,
    }
    # 不暴露口语原文、新指令原文与题库概况。
    assert _FREE_TEXT not in repr(body)
    assert _NEW_INSTRUCTION not in repr(body)
    assert "bank_profile" not in repr(body)
    assert "difficulty_distribution" not in repr(body)

    # 通用 job 提交端点拒绝该类型。
    generic = client.post(
        "/api/jobs/ai_assembly_spec",
        json={"payload": {"request": _spec_request_payload()}},
    )
    assert generic.status_code == 422
    assert generic.json()["error"]["code"] == "dedicated_job_endpoint_required"


# ---------------------------------------------------------------------------
# 解答题子类（W4）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AI 组卷会话持久化（W6）
# ---------------------------------------------------------------------------

_SESSION_URL = "/api/question-assembly/ai/session"
