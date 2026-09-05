"""AI 组卷 LLM 链路与路由契约测试：全程 mock 模型，不碰真实配置与数据。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.api.routers.ai_assembly as ai_assembly_router
import backend.jobs.default_handlers as default_handlers
import question_bank.services.ai_assembly_service as ai_assembly_service
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
from llm_client import LLMOutputTruncatedError, LLMResponseFormatError
from question_bank.database.schema import initialize_database
from question_bank.services.ai_assembly_service import (
    AssemblyModelNotConfiguredError,
    AssemblySpecError,
    SpecGenerationRequest,
    generate_spec,
)
from question_bank.services.assembly_workspace_service import (
    AiAssemblySessionService,
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


@pytest.mark.parametrize("template_types", [("选择题", "填空题", "解答题"), ("选择题", "填空题", "选择题")])
def test_generated_spec_is_aligned_to_template_order(tmp_path: Path, template_types) -> None:
    store = QuestionBankTestStore(tmp_path / "data" / "question_bank.db")
    _insert_paper(store, 1, "合成模板")
    for index, question_type in enumerate(template_types):
        _add_question(store, number=str(index + 1), paper_id=1, question_type=question_type, knowledge_points=(_KP_A1,))
    fake = _FakeLLMClient(payload={"title": "模板卷", "rows": [
        {"question_type": question_type, "count": template_types.count(question_type), "knowledge_points": [_KP_A1], "difficulty": 5, "score": 3}
        for question_type in sorted(set(template_types))
    ]})
    result = generate_spec(SpecGenerationRequest(template_paper_id=1, scope_keys=(_KP_A1,)), store.reader, llm_client=fake)
    assert [row.question_type for row in result.spec.rows for _ in range(row.count)] == list(template_types)
    assert len(fake.prompts) == 1


def test_generated_spec_rejects_a_template_count_mismatch(tmp_path: Path) -> None:
    store = QuestionBankTestStore(tmp_path / "data" / "question_bank.db")
    _insert_paper(store, 1, "合成模板")
    _add_question(store, number="1", paper_id=1, knowledge_points=(_KP_A1,))
    fake = _FakeLLMClient(payload=_spec_llm_payload())
    with pytest.raises(AssemblySpecError, match="模板"):
        generate_spec(SpecGenerationRequest(template_paper_id=1, scope_keys=(_KP_A1,)), store.reader, llm_client=fake)
    assert len(fake.prompts) == 1


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



# ---------------------------------------------------------------------------
# 解答题子类（W4）
# ---------------------------------------------------------------------------


def test_spec_job_carries_essay_subtype_constraint(ai_client) -> None:
    client, manager, _, _, holder = ai_client
    payload = _spec_llm_payload()
    payload["rows"].append(
        {
            "question_type": "解答题",
            "count": 1,
            "knowledge_points": [_KP_A1],
            "difficulty": 6,
            "score": 8,
            "essay_subtype": "证明",
        }
    )
    fake = _FakeLLMClient(payload=payload)
    holder["client"] = fake

    submit = client.post(
        "/api/question-assembly/ai/spec-jobs",
        json={"request": _spec_request_payload(essay_subtype="证明")},
    )
    assert submit.status_code == 202
    job_id = submit.json()["id"]
    manager.wait(job_id, timeout=10)

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    rows = job.json()["result"]["spec"]["rows"]
    assert rows[1]["question_type"] == "解答题"
    assert rows[1]["essay_subtype"] == "证明"
    # 旧式无 essay_subtype 的行按 null 兼容输出。
    assert rows[0]["essay_subtype"] is None

    # 用户选定的子类作为硬约束进入 prompt。
    assert len(fake.prompts) == 1
    assert '"essay_subtype": "证明"' in fake.prompts[0]


def test_select_rejects_essay_subtype_on_non_essay(ai_client) -> None:
    client, *_ = ai_client
    response = client.post(
        "/api/question-assembly/ai/select",
        json={
            "spec": {
                "title": "坏表",
                "rows": [
                    {
                        "question_type": "选择题",
                        "count": 1,
                        "essay_subtype": "证明",
                    }
                ],
            }
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ai_assembly_spec_invalid"


def test_select_rejects_unknown_essay_subtype_value(ai_client) -> None:
    client, *_ = ai_client
    response = client.post(
        "/api/question-assembly/ai/select",
        json={
            "spec": {
                "title": "坏表",
                "rows": [
                    {
                        "question_type": "解答题",
                        "count": 1,
                        "essay_subtype": "推理",
                    }
                ],
            }
        },
    )
    assert response.status_code == 422


def test_select_essay_subtype_filters_by_tag(ai_client) -> None:
    client, _, store, _, _ = ai_client
    proof = _add_question(
        store,
        number="1",
        question_type="解答题",
        difficulty="6",
        knowledge_points=(_KP_A1,),
        special_types=("证明",),
    )
    _add_question(
        store,
        number="2",
        question_type="解答题",
        difficulty="6",
        knowledge_points=(_KP_A1,),
    )

    response = client.post(
        "/api/question-assembly/ai/select",
        json={
            "spec": {
                "title": "子类",
                "rows": [
                    {
                        "question_type": "解答题",
                        "count": 1,
                        "knowledge_points": [_KP_A1],
                        "essay_subtype": "证明",
                    }
                ],
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["question_ids"] == [proof]
    assert body["gaps"] == []


def test_template_structure_merges_and_normalizes_legacy_types(ai_client) -> None:
    client, _, store, _, _ = ai_client
    _insert_paper(store, 1, "旧真卷")
    _add_question(
        store, number="1", question_type="选择题", difficulty="3", paper_id=1
    )
    _add_question(
        store,
        number="2",
        question_type="解答题（证明）",
        difficulty="7",
        paper_id=1,
    )
    _add_question(
        store,
        number="3",
        question_type="解答题（计算）",
        difficulty="7",
        paper_id=1,
    )

    found = client.get("/api/question-assembly/ai/template-structure/1")
    assert found.status_code == 200
    entries = found.json()["entries"]
    assert [
        (entry["question_number"], entry["question_type"], entry["count"])
        for entry in entries
    ] == [
        ("1", "选择题", 1),
        ("2-3", "解答题", 2),
    ]


# ---------------------------------------------------------------------------
# AI 组卷会话持久化（W6）
# ---------------------------------------------------------------------------

_SESSION_URL = "/api/question-assembly/ai/session"


def _session_payload(**overrides) -> dict:
    payload = {
        "params": {
            "template_paper_id": 7,
            "scope_keys": ["chapter-1"],
            "difficulty_ratio": {"easy": None, "medium": 50, "hard": None},
            "type_counts": {"选择题": 2},
            "exam_types": ["期末"],
            "years": [2024],
            "free_text": "出一份小测",
            "essay_subtype": "证明",
        },
        "spec": {
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
            "scope_knowledge_points": [_KP_A1],
        },
        "spec_model_name": "fake-assembly-model",
        "selections": {"0": [101, 102], "1": [103]},
        "locked_question_ids": [101],
        "locked_row_by_id": {"101": 0},
        "gaps": [],
        "dedupe_enabled": False,
        "title": "勾股定理小测",
        "spec_job_id": 41,
    }
    payload.update(overrides)
    return payload


def test_session_empty_get_returns_default_session(ai_client) -> None:
    client, *_ = ai_client
    response = client.get(_SESSION_URL)
    assert response.status_code == 200
    body = response.json()
    assert re.fullmatch(r"[0-9a-f]{64}", body["revision"])
    assert "schema_version" not in body
    assert body["updated_at"] == ""
    assert body["spec"] is None
    assert body["spec_model_name"] == ""
    assert body["params"] == {
        "template_paper_id": None,
        "scope_keys": [],
        "difficulty_ratio": {"easy": None, "medium": None, "hard": None},
        "type_counts": {},
        "exam_types": [],
        "years": [],
        "free_text": "",
        "essay_subtype": None,
    }
    assert body["selections"] == {}
    assert body["locked_question_ids"] == []
    assert body["locked_row_by_id"] == {}
    assert body["gaps"] == []
    assert body["dedupe_enabled"] is True
    assert body["title"] == ""
    assert body["spec_job_id"] is None

    # 空会话版本号是内容哈希，重复读取稳定一致。
    again = client.get(_SESSION_URL)
    assert again.status_code == 200
    assert again.json()["revision"] == body["revision"]


def test_session_put_get_roundtrip(ai_client, tmp_path: Path) -> None:
    client, *_ = ai_client
    initial = client.get(_SESSION_URL).json()

    saved = client.put(
        _SESSION_URL,
        json={"expected_revision": initial["revision"], "session": _session_payload()},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] != initial["revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", body["revision"])
    assert body["updated_at"]
    assert "schema_version" not in body
    assert body["params"]["template_paper_id"] == 7
    assert body["params"]["essay_subtype"] == "证明"
    assert body["params"]["difficulty_ratio"] == {
        "easy": None,
        "medium": 50,
        "hard": None,
    }
    assert body["spec"]["title"] == "勾股定理小测"
    assert body["spec_model_name"] == "fake-assembly-model"
    assert body["selections"] == {"0": [101, 102], "1": [103]}
    assert body["locked_question_ids"] == [101]
    assert body["locked_row_by_id"] == {"101": 0}
    assert body["dedupe_enabled"] is False
    assert body["title"] == "勾股定理小测"
    assert body["spec_job_id"] == 41

    # 重新进入（GET）完整恢复，内容与版本号与保存响应一致。
    restored = client.get(_SESSION_URL)
    assert restored.status_code == 200
    assert restored.json() == body

    # 落盘位置在数据根目录下的 question_bank 工作区（测试夹具临时目录）。
    session_file = tmp_path / "data" / "question_bank" / "ai_assembly_session.json"
    assert session_file.is_file()


def test_session_put_conflict_returns_current_revision(ai_client) -> None:
    client, *_ = ai_client
    initial = client.get(_SESSION_URL).json()
    first = client.put(
        _SESSION_URL,
        json={"expected_revision": initial["revision"], "session": _session_payload()},
    )
    assert first.status_code == 200

    stale = client.put(
        _SESSION_URL,
        json={
            "expected_revision": initial["revision"],
            "session": _session_payload(title="另一份"),
        },
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "ai_assembly_session_conflict"
    assert error["details"]["current_revision"] == first.json()["revision"]

    # 携带最新版本号可继续写入。
    follow_up = client.put(
        _SESSION_URL,
        json={
            "expected_revision": first.json()["revision"],
            "session": _session_payload(title="另一份"),
        },
    )
    assert follow_up.status_code == 200
    assert follow_up.json()["title"] == "另一份"


def test_session_delete_resets_to_empty_session(ai_client) -> None:
    client, *_ = ai_client
    initial = client.get(_SESSION_URL).json()
    saved = client.put(
        _SESSION_URL,
        json={"expected_revision": initial["revision"], "session": _session_payload()},
    )
    assert saved.status_code == 200

    cleared = client.delete(_SESSION_URL)
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["spec"] is None
    assert body["params"]["scope_keys"] == []
    assert body["params"]["essay_subtype"] is None
    assert body["selections"] == {}
    assert body["spec_job_id"] is None
    # 空会话内容哈希与初始空会话一致。
    assert body["revision"] == initial["revision"]

    reread = client.get(_SESSION_URL)
    assert reread.status_code == 200
    assert reread.json()["spec"] is None
    assert reread.json()["revision"] == initial["revision"]


def test_session_put_rejects_invalid_payload(ai_client) -> None:
    client, *_ = ai_client
    initial = client.get(_SESSION_URL).json()

    # 未知字段（extra=forbid）。
    unknown = client.put(
        _SESSION_URL,
        json={
            "expected_revision": initial["revision"],
            "session": {**_session_payload(), "unknown_field": 1},
        },
    )
    assert unknown.status_code == 422

    # spec 行不合法（count 越界）。
    bad_spec = _session_payload(
        spec={"title": "坏表", "rows": [{"question_type": "选择题", "count": 0}]}
    )
    invalid_spec = client.put(
        _SESSION_URL,
        json={"expected_revision": initial["revision"], "session": bad_spec},
    )
    assert invalid_spec.status_code == 422

    # revision 形态不合法。
    malformed = client.put(
        _SESSION_URL,
        json={"expected_revision": "not-a-revision", "session": _session_payload()},
    )
    assert malformed.status_code == 422

    # 非法子类取值。
    bad_subtype = _session_payload(
        params={**_session_payload()["params"], "essay_subtype": "推理"}
    )
    invalid_subtype = client.put(
        _SESSION_URL,
        json={"expected_revision": initial["revision"], "session": bad_subtype},
    )
    assert invalid_subtype.status_code == 422

    # 全部校验失败均不落盘：会话仍是空会话。
    assert client.get(_SESSION_URL).json()["revision"] == initial["revision"]
