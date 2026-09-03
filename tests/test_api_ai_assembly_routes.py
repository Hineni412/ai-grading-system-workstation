"""AI 组卷 LLM 链路与路由契约测试：全程 mock 模型，不碰真实配置与数据。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.api.routers.ai_assembly as ai_assembly_router
import backend.jobs.default_handlers as default_handlers
import question_bank.services.ai_assembly_service as ai_assembly_service
from backend.api.app import create_app
from backend.api.dependencies import (
    get_assembly_workspace_service,
    get_job_manager,
    get_question_bank_read_service,
)
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from llm_client import LLMOutputTruncatedError, LLMResponseFormatError
from question_bank.database.schema import initialize_database
from question_bank.services.ai_assembly_service import (
    AssemblyModelNotConfiguredError,
    AssemblySpecError,
    SpecGenerationRequest,
    generate_spec,
)
from question_bank.services.assembly_workspace_service import (
    AssemblyRecordCreate,
    AssemblyWorkspaceService,
)
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
from tests.question_bank_support import QuestionBankTestStore
from tests.test_ai_assembly_service import _add_question, _insert_paper

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
    app.dependency_overrides[get_question_bank_read_service] = lambda: store.reader
    app.dependency_overrides[get_job_manager] = lambda: manager
    client = TestClient(app)
    yield client, manager, store, workspace, holder
    manager.shutdown()


def test_preflight_unconfigured(
    ai_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, manager, *_ = ai_client
    monkeypatch.setattr(
        ai_assembly_router,
        "resolve_content_generation_settings",
        lambda: None,
    )
    monkeypatch.setattr(
        ai_assembly_router,
        "content_generation_public_info",
        lambda: (None, None),
    )
    response = client.get("/api/question-assembly/ai/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["service_name"] is None
    assert body["model_name"] is None
    assert body["call_count"] == 1
    assert body["estimated_total_tokens"] > 0


def test_preflight_configured(
    ai_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, *_ = ai_client
    monkeypatch.setattr(
        ai_assembly_router,
        "resolve_content_generation_settings",
        lambda: SimpleNamespace(config_model="model-x"),
    )
    monkeypatch.setattr(
        ai_assembly_router,
        "content_generation_public_info",
        lambda: ("服务甲 @ api.example.com", "model-x"),
    )
    response = client.get("/api/question-assembly/ai/preflight")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["service_name"] == "服务甲 @ api.example.com"
    assert body["model_name"] == "model-x"
    assert body["call_count"] == 1


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


def test_spec_job_invalid_json_fails_without_resend(ai_client) -> None:
    client, manager, _, _, holder = ai_client
    fake = _FakeLLMClient(error=LLMResponseFormatError("returned invalid json"))
    holder["client"] = fake

    submit = client.post(
        "/api/question-assembly/ai/spec-jobs",
        json={"request": _spec_request_payload()},
    )
    assert submit.status_code == 202
    job_id = submit.json()["id"]
    manager.wait(job_id, timeout=10)

    record = manager.get(job_id)
    assert record is not None
    assert record.status == "failed"
    # JSON 损坏直接报错，不重发模型请求。
    assert len(fake.prompts) == 1

    public = client.get(f"/api/jobs/{job_id}").json()
    assert public["status"] == "failed"
    assert _FREE_TEXT not in repr(public)


def test_spec_job_truncated_output_fails_without_resend(ai_client) -> None:
    client, manager, _, _, holder = ai_client
    fake = _FakeLLMClient(
        error=LLMOutputTruncatedError(
            finish_reason="length",
            response_chars=2400,
            response_sha256="abc",
            provider_reported=True,
        )
    )
    holder["client"] = fake

    submit = client.post(
        "/api/question-assembly/ai/spec-jobs",
        json={"request": _spec_request_payload()},
    )
    assert submit.status_code == 202
    job_id = submit.json()["id"]
    manager.wait(job_id, timeout=10)

    record = manager.get(job_id)
    assert record is not None
    assert record.status == "failed"
    assert "输出长度上限" in str(record.error)
    assert len(fake.prompts) == 1


def test_generate_spec_unconfigured_model_raises_chinese_error(
    ai_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, store, _, _ = ai_client
    monkeypatch.setattr(
        ai_assembly_service,
        "resolve_content_generation_settings",
        lambda: None,
    )
    with pytest.raises(AssemblyModelNotConfiguredError, match="未配置内容生成模型"):
        generate_spec(
            SpecGenerationRequest(scope_keys=(_KP_A1,)),
            store.reader,
        )


def test_generate_spec_rejects_unknown_template(ai_client) -> None:
    _, _, store, _, _ = ai_client
    fake = _FakeLLMClient(payload=_spec_llm_payload())
    with pytest.raises(AssemblySpecError, match="模板试卷不存在"):
        generate_spec(
            SpecGenerationRequest(template_paper_id=999),
            store.reader,
            llm_client=fake,
        )
    # 模板无效时不发起模型请求。
    assert fake.prompts == []


def test_select_returns_rows_and_gaps(ai_client) -> None:
    client, _, store, _, _ = ai_client
    for index in range(3):
        _add_question(
            store,
            number=str(index + 1),
            question_type="选择题",
            difficulty="5",
            knowledge_points=(_KP_A1,),
        )

    response = client.post(
        "/api/question-assembly/ai/select",
        json={
            "spec": {
                "title": "小测",
                "rows": [
                    {
                        "question_type": "选择题",
                        "count": 2,
                        "knowledge_points": [_KP_A1],
                        "difficulty": 5,
                        "score": 3,
                    }
                ],
                "scope_knowledge_points": [_KP_A1],
            },
            "dedupe_enabled": True,
            "exclude_ids": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    assert len(body["rows"][0]["question_ids"]) == 2
    assert body["gaps"] == []

    invalid = client.post(
        "/api/question-assembly/ai/select",
        json={
            "spec": {
                "title": "坏表",
                "rows": [{"question_type": "选择题", "count": 0}],
            }
        },
    )
    assert invalid.status_code == 422


def test_template_structure_200_and_404(ai_client) -> None:
    client, _, store, _, _ = ai_client
    _insert_paper(store, 1, "期末真卷")
    _add_question(
        store,
        number="2",
        question_type="解答题",
        difficulty="7",
        paper_id=1,
    )
    _add_question(
        store,
        number="1",
        question_type="选择题",
        difficulty="3",
        paper_id=1,
    )

    found = client.get("/api/question-assembly/ai/template-structure/1")
    assert found.status_code == 200
    body = found.json()
    assert body["paper_id"] == 1
    assert body["paper_title"] == "期末真卷"
    assert [entry["question_number"] for entry in body["entries"]] == ["1", "2"]
    assert body["entries"][0]["question_type"] == "选择题"
    assert body["entries"][0]["difficulty"] == 3

    missing = client.get("/api/question-assembly/ai/template-structure/999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ai_assembly_template_not_found"


def test_record_source_marker_roundtrip(ai_client, tmp_path: Path) -> None:
    client, _, _, workspace, _ = ai_client
    draft = workspace.load_draft()
    saved = workspace.save_draft(
        expected_revision=draft.revision,
        draft={
            "basket_ids": [11, 12],
            "order_ids": [11, 12],
            "sections": [],
            "title": "AI 组卷",
            "header_text": "",
            "include_answer": True,
            "layout_mode": "sequential",
            "preview_mode": "teacher",
        },
    )
    ai_record = workspace.create_record(
        AssemblyRecordCreate(
            title="AI 组卷",
            draft=saved,
            output_path=tmp_path / "ai卷.docx",
            export_format="docx",
            source="ai",
        )
    )
    manual_record = workspace.create_record(
        AssemblyRecordCreate(
            title="人工组卷",
            draft=saved,
            output_path=tmp_path / "人工卷.docx",
            export_format="docx",
        )
    )
    # 旧记录缺 source 字段时按 None 兼容读取。
    legacy_payload = ai_record.to_payload()
    legacy_payload.pop("source")
    legacy_path = workspace.records_root / f"{ai_record.id}.json"
    legacy_path.write_text(
        json.dumps(legacy_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.get("/api/question-assembly/records")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["items"]}
    # 兼容读取后的 AI 记录被改写为无 source 字段的旧格式，按 None 处理。
    assert by_id[ai_record.id]["source"] is None
    assert by_id[manual_record.id]["source"] is None

    fresh = workspace.create_record(
        AssemblyRecordCreate(
            title="AI 组卷 2",
            draft=saved,
            output_path=tmp_path / "ai卷2.docx",
            export_format="docx",
            source="ai",
        )
    )
    response = client.get("/api/question-assembly/records")
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert by_id[fresh.id]["source"] == "ai"
