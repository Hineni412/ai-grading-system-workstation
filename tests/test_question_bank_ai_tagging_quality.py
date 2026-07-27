from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import backend.llm as backend_llm
import question_bank.services.ai_tagging_service as ai_tagging_module

from question_bank.models.question import QuestionCreate, TagCreate
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    _adaptive_batches,
    _batch_prompt_input,
    _batch_tag_analysis_response_format,
    _prompt_input,
    _tag_analysis_response_format,
)
from question_bank.services.question_service import QuestionService, has_complete_analysis_tags


def _analysis(**overrides) -> TagAnalysis:
    payload = {
        "knowledge_points": ["整式运算"],
        "method_tags": ["整体思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "difficulty": 4,
        "error_prone_points": ["运算化简错误"],
        "prerequisite_points": ["整数指数幂"],
        "textbook_chapter": "七年级下册 第一章 整式的乘除",
        "teaching_stage": "",
        "suitable_student_level": "基础巩固",
        "canonical_knowledge_id": "kp_alg_polynomial",
        "reason": "考查幂运算和整式化简。",
        "confidence": 0.86,
        "measured_skills": [],
        "supporting_skills": [],
    }
    payload.update(overrides)
    return TagAnalysis.from_dict(payload)


def _install_isolated_real_adapter(monkeypatch, *, init_calls, adapter_calls) -> None:
    real_adapter_class = backend_llm.LLMProtocolAdapter

    class UnexpectedLLMClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("raw Responses injection was shadowed by LLMClient")

    class IsolatedProtocolAdapter:
        def __init__(
            self,
            api_key: str,
            base_url: str,
            policy_profile=None,
            client=None,
        ) -> None:
            init_calls.append(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "policy_profile": dict(policy_profile or {}),
                    "client": client,
                }
            )
            self._adapter = real_adapter_class(
                api_key,
                base_url,
                policy_profile=policy_profile,
                client=client,
                usage_sink_factory=backend_llm.NullUsageSink,
                trace_sink_factory=backend_llm.NullCallTraceSink,
            )

        def responses(self, **kwargs):
            adapter_calls.append(kwargs)
            return self._adapter.responses(**kwargs)

    monkeypatch.setattr(
        ai_tagging_module,
        "LLMProtocolAdapter",
        IsolatedProtocolAdapter,
        raising=False,
    )
    monkeypatch.setattr("llm_client.LLMClient", UnexpectedLLMClient)
    monkeypatch.setattr(ai_tagging_module, "LLMClient", UnexpectedLLMClient)


def _tagging_env() -> dict[str, str]:
    return {
        "QUESTION_BANK_TAGGING_API_KEY": "fake-tagging-key",
        "QUESTION_BANK_TAGGING_BASE_URL": "https://provider.invalid/v1",
        "QUESTION_BANK_TAGGING_MODEL": "fake-tagging-model",
    }


def test_dedicated_llm_client_uses_only_dedicated_or_default_base_url(
    monkeypatch,
) -> None:
    captured_settings = []

    class CapturingLLMClient:
        def __init__(self, settings) -> None:
            self.settings = settings
            captured_settings.append(settings)

    monkeypatch.setattr(ai_tagging_module, "_dotenv_values", lambda: {})
    monkeypatch.setattr(ai_tagging_module, "LLMClient", CapturingLLMClient)

    AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "dedicated-key",
            "QUESTION_BANK_TAGGING_MODEL": "tag-model",
            "LLM_BASE_URL": "https://unrelated.invalid/v1",
        }
    )
    AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "dedicated-key",
            "QUESTION_BANK_TAGGING_BASE_URL": "https://dedicated.invalid/v1",
            "QUESTION_BANK_TAGGING_MODEL": "tag-model",
            "LLM_BASE_URL": "https://unrelated.invalid/v1",
        }
    )

    assert [settings.base_url for settings in captured_settings] == [
        "https://api.openai.com/v1",
        "https://dedicated.invalid/v1",
    ]
    assert [settings.config_base_url for settings in captured_settings] == [
        "https://api.openai.com/v1",
        "https://dedicated.invalid/v1",
    ]


def test_single_responses_uses_tagging_gateway_with_raw_client(monkeypatch) -> None:
    provider_calls = []
    adapter_calls = []
    init_calls = []
    expected_analysis = _analysis()

    def fake_provider_create(**kwargs):
        provider_calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(expected_analysis.to_dict()))

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_provider_create)
    )
    _install_isolated_real_adapter(
        monkeypatch,
        init_calls=init_calls,
        adapter_calls=adapter_calls,
    )
    monkeypatch.setattr(ai_tagging_module, "_dotenv_values", lambda: {})
    context = TaggingContext(
        question_text="计算 a^2 · a^3。",
        answer_text="a^5",
        question_number="1",
        question_type="选择题",
    )

    service = AITaggingService(env=_tagging_env(), client=fake_client)
    result = service.analyze_question(context)

    assert result.ok
    assert result.quality_status == "complete"
    assert result.analysis == expected_analysis
    assert result.model_name == "fake-tagging-model"
    assert init_calls == [
        {
            "api_key": "fake-tagging-key",
            "base_url": "https://provider.invalid/v1",
            "policy_profile": {},
            "client": fake_client,
        }
    ]
    assert len(adapter_calls) == 1
    assert adapter_calls[0]["request_kind"] is backend_llm.LLMRequestKind.TAGGING
    assert adapter_calls[0]["model"] == "fake-tagging-model"
    assert adapter_calls[0]["kwargs"] == {
        "text": {"format": _tag_analysis_response_format()},
        "input": _prompt_input(context),
    }
    assert provider_calls == [
        {
            "text": {"format": _tag_analysis_response_format()},
            "input": _prompt_input(context),
            "model": "fake-tagging-model",
            "timeout": 120.0,
        }
    ]
    assert math.isfinite(provider_calls[0]["timeout"])


def test_batch_responses_preserves_structured_payload_mapping_and_lazy_adapter(
    monkeypatch,
) -> None:
    provider_calls = []
    adapter_calls = []
    init_calls = []
    first_analysis = _analysis()
    second_analysis = _analysis(
        knowledge_points=["概率初步"],
        canonical_knowledge_id="kp_probability",
        reason="考查古典概型。",
    )

    def fake_provider_create(**kwargs):
        provider_calls.append(kwargs)
        if kwargs["text"]["format"]["name"] == "question_bank_batch_tag_analysis":
            payload = {
                "results": [
                    {"question_id": 1, **first_analysis.to_dict()},
                    {"question_id": 2, **second_analysis.to_dict()},
                ]
            }
        else:
            payload = first_analysis.to_dict()
        return SimpleNamespace(output_text=json.dumps(payload))

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_provider_create)
    )
    _install_isolated_real_adapter(
        monkeypatch,
        init_calls=init_calls,
        adapter_calls=adapter_calls,
    )
    monkeypatch.setattr(ai_tagging_module, "_dotenv_values", lambda: {})
    contexts = {
        1: TaggingContext(
            question_text="计算 a^2 · a^3。",
            answer_text="a^5",
            question_number="1",
            question_type="选择题",
        ),
        2: TaggingContext(
            question_text="随机抽取一个球，求概率。",
            answer_text="1/2",
            question_number="2",
            question_type="选择题",
        ),
    }
    batch_items = list(contexts.items())

    service = AITaggingService(env=_tagging_env(), client=fake_client)
    single = service.analyze_question(contexts[1])
    first_adapter = service._protocol_adapter()
    results = service.analyze_questions(
        contexts,
        max_workers=1,
        quality_retry_limit=0,
        enable_review=False,
    )

    assert service._protocol_adapter() is first_adapter
    assert len(init_calls) == 1
    assert len(adapter_calls) == 2
    assert all(
        call["request_kind"] is backend_llm.LLMRequestKind.TAGGING
        for call in adapter_calls
    )
    assert adapter_calls[1]["kwargs"] == {
        "text": {"format": _batch_tag_analysis_response_format()},
        "input": _batch_prompt_input(batch_items),
    }
    assert provider_calls == [
        {
            "text": {"format": _tag_analysis_response_format()},
            "input": _prompt_input(contexts[1]),
            "model": "fake-tagging-model",
            "timeout": 120.0,
        },
        {
            "text": {"format": _batch_tag_analysis_response_format()},
            "input": _batch_prompt_input(batch_items),
            "model": "fake-tagging-model",
            "timeout": 120.0,
        }
    ]
    assert all(math.isfinite(call["timeout"]) for call in provider_calls)
    assert single.analysis == first_analysis
    assert results[1].analysis == first_analysis
    assert results[2].analysis == second_analysis
    assert results[1].quality_status == "complete"
    assert results[2].quality_status == "complete"


def test_explicit_protocol_adapter_is_reused_for_single_and_batch_calls(
    monkeypatch,
) -> None:
    calls = []
    analysis = _analysis()

    class InjectedAdapter:
        def responses(self, **kwargs):
            calls.append(kwargs)
            format_name = kwargs["kwargs"]["text"]["format"]["name"]
            if format_name == "question_bank_batch_tag_analysis":
                payload = {"results": [{"question_id": 1, **analysis.to_dict()}]}
            else:
                payload = analysis.to_dict()
            return SimpleNamespace(output_text=json.dumps(payload))

    monkeypatch.setattr(ai_tagging_module, "_dotenv_values", lambda: {})
    adapter = InjectedAdapter()
    service = AITaggingService(
        env=_tagging_env(),
        protocol_adapter=adapter,
    )
    context = TaggingContext(
        question_text="计算 a^2 · a^3。",
        answer_text="a^5",
        question_number="1",
        question_type="选择题",
    )

    single = service.analyze_question(context)
    batch = service.analyze_questions(
        {1: context},
        max_workers=1,
        quality_retry_limit=0,
        enable_review=False,
    )

    assert service._protocol_adapter() is adapter
    assert single.analysis == analysis
    assert batch[1].analysis == analysis
    assert len(calls) == 2


def test_tag_analysis_normalizes_confidence_and_string_list_fields() -> None:
    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": "科学记数法",
            "method_tags": "数形结合",
            "ability_tags": "运算能力",
            "math_model_tags": "",
            "difficulty": 4,
            "error_prone_points": "运算化简错误",
            "prerequisite_points": "有理数运算",
            "textbook_chapter": ["七年级上册 第二章 有理数及其运算"],
            "teaching_stage": "期末复习",
            "suitable_student_level": "基础巩固",
            "reason": "可直接判断。",
            "confidence": 1.8,
            "measured_skills": "科学记数法表示",
            "supporting_skills": "实数分类",
        }
    )

    assert analysis.knowledge_points == ["科学记数法"]
    assert analysis.method_tags == ["数形结合"]
    assert analysis.math_model_tags == []
    assert analysis.error_prone_points == ["运算化简错误"]
    assert analysis.textbook_chapter == "七年级上册 第二章 有理数及其运算"
    assert analysis.confidence == 1.0
    assert analysis.measured_skills == ["科学记数法表示"]
    assert analysis.supporting_skills == ["实数分类"]


def test_save_tag_analysis_persists_model_name_and_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(question_number="1", question_text="计算 a^2 · a^3。", answer_text="a^5")
    )

    assert service.save_tag_analysis(
        question_id,
        _analysis(confidence=0.91),
        model_name="doubao-tag",
        confidence=0.77,
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tag_type, confidence, model_name FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchall()

    assert rows
    assert {row["model_name"] for row in rows} == {"doubao-tag"}
    assert {round(float(row["confidence"]), 2) for row in rows} == {0.77}


def test_only_scope_and_student_level_is_not_complete_analysis_tags(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    question_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_text="如图，添加条件使两直线平行。",
            tags=[
                TagCreate("exam_scope", "七年级下册 第二章 相交线与平行线", source="ai"),
                TagCreate("student_level", "基础巩固", source="ai"),
            ],
        )
    )

    saved = service.get_question(question_id)

    assert saved is not None
    assert not has_complete_analysis_tags(saved)


def test_exact_duplicate_complete_tags_can_be_reused_with_confidence_cap(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    source_id = service.add_question(
        QuestionCreate(question_number="1", question_text="计算 a^2 · a^3。", answer_text="a^5")
    )
    target_id = service.add_question(
        QuestionCreate(question_number="2", question_text=" 计算 a^2 · a^3。 ", answer_text=" a^5 ")
    )
    assert service.save_tag_analysis(source_id, _analysis(confidence=0.97), model_name="doubao-main", confidence=0.97)

    duplicate = service.find_exact_duplicate_tag_analysis(target_id)

    assert duplicate is not None
    analysis, model_name = duplicate
    assert analysis.knowledge_points == ["整式运算"]
    assert analysis.confidence == 0.9
    assert model_name == "doubao-main"


class _FakeTaggingService(AITaggingService):
    def __init__(
        self,
        primary: dict[int, list[TagAnalysis]],
        review: dict[int, TagAnalysis] | None = None,
    ) -> None:
        super().__init__(env={}, llm_client=None)
        self.primary = {qid: list(items) for qid, items in primary.items()}
        self.review = dict(review or {})
        self.primary_calls: list[int] = []
        self.review_calls: list[int] = []
        self.model = "doubao-main"
        self.review_model = "deepseek-review" if review else ""

    @property
    def mock_mode(self) -> bool:
        return False

    def analyze_question(self, context: TaggingContext) -> AITaggingResult:
        qid = int(context.question_number or 0)
        self.primary_calls.append(qid)
        items = self.primary[qid]
        analysis = items.pop(0) if len(items) > 1 else items[0]
        return AITaggingResult(ok=True, mock_mode=False, analysis=analysis, model_name=self.model)

    def analyze_review_question(self, context: TaggingContext) -> AITaggingResult:
        qid = int(context.question_number or 0)
        self.review_calls.append(qid)
        analysis = self.review[qid]
        return AITaggingResult(ok=True, mock_mode=False, analysis=analysis, model_name=self.review_model)


def test_batch_incomplete_single_question_retries_without_affecting_neighbors() -> None:
    service = _FakeTaggingService(
        primary={
            1: [_analysis()],
            2: [
                _analysis(knowledge_points=[], ability_tags=[], confidence=0.91),
                _analysis(knowledge_points=["概率初步"], ability_tags=["数据观念"], confidence=0.88),
            ],
        }
    )
    contexts = {
        1: TaggingContext(question_text="计算 a^2 · a^3。", question_number="1", question_type="选择题"),
        2: TaggingContext(question_text="随机掷骰子，求概率。", question_number="2", question_type="选择题"),
    }

    events = []
    results = service.analyze_questions(
        contexts,
        max_workers=1,
        request_callback=events.append,
    )

    assert results[1].quality_status == "complete"
    assert results[2].quality_status == "complete"
    assert service.primary_calls == [1, 2, 2]
    started = [event for event in events if event.phase == "started"]
    assert [(event.request_kind, event.question_ids) for event in started] == [
        ("batch", (1, 2)),
        ("single_fallback", (1,)),
        ("single_fallback", (2,)),
        ("quality_retry", (2,)),
    ]
    assert [event.request_number for event in started] == [1, 2, 3, 4]


def test_failed_batch_does_not_fan_out_when_fallback_is_disabled() -> None:
    service = _FakeTaggingService(primary={1: [_analysis()], 2: [_analysis()]})
    contexts = {
        1: TaggingContext(question_text="计算 a² · a³。", question_number="1", question_type="选择题"),
        2: TaggingContext(question_text="计算 b² · b³。", question_number="2", question_type="选择题"),
    }
    events = []

    results = service.analyze_questions(
        contexts,
        max_workers=1,
        request_callback=events.append,
        allow_batch_fallback=False,
        quality_retry_limit=0,
        enable_review=False,
    )

    assert not results[1].ok
    assert not results[2].ok
    assert service.primary_calls == []
    started = [event for event in events if event.phase == "started"]
    assert [(event.request_kind, event.question_ids) for event in started] == [
        ("batch", (1, 2))
    ]
    # 批次真正失败时应发出一个 failed 阶段事件（分类 + 脱敏）
    failed = [event for event in events if event.phase == "failed"]
    assert len(failed) == 1
    assert failed[0].request_kind == "batch"


def test_low_confidence_uses_review_model_when_agreement_is_found() -> None:
    primary = _analysis(knowledge_points=["概率初步"], confidence=0.55)
    review = _analysis(knowledge_points=["概率初步"], confidence=0.9)
    service = _FakeTaggingService(primary={1: [primary]}, review={1: review})
    contexts = {
        1: TaggingContext(question_text="随机抽取一个球，求概率。", question_number="1", question_type="选择题")
    }

    events = []
    results = service.analyze_questions(
        contexts,
        max_workers=1,
        request_callback=events.append,
    )

    assert results[1].quality_status == "complete"
    assert results[1].model_name == "doubao-main+deepseek-review"
    assert results[1].analysis is not None
    assert results[1].analysis.confidence >= 0.72
    assert service.review_calls == [1]
    started = [event for event in events if event.phase == "started"]
    assert [event.request_kind for event in started] == [
        "batch",
        "single_fallback",
        "review",
    ]


def test_low_confidence_without_review_stays_pending() -> None:
    service = _FakeTaggingService(primary={1: [_analysis(confidence=0.55)]})
    contexts = {
        1: TaggingContext(question_text="随机抽取一个球，求概率。", question_number="1", question_type="选择题")
    }

    results = service.analyze_questions(contexts, max_workers=1)

    assert results[1].quality_status == "low_confidence"
    assert service.review_calls == []


def test_adaptive_batches_keep_simple_questions_together_and_complex_questions_single() -> None:
    items = [
        (1, TaggingContext(question_text="计算 a^2 · a^3。", question_number="1", question_type="选择题")),
        (2, TaggingContext(question_text="随机事件概率。", question_number="2", question_type="选择题")),
        (3, TaggingContext(question_text="如图证明三角形全等。", question_number="3", question_type="解答题（证明）", has_images=True)),
        (4, TaggingContext(question_text="先化简，再求值。", question_number="4", question_type="解答题（计算）")),
        (5, TaggingContext(question_text="综合与实践：" + "阅读材料。" * 80, question_number="5", question_type="填空题")),
    ]

    batches = _adaptive_batches(items)

    assert [qid for qid, _ in batches[0]] == [1, 2]
    assert [qid for qid, _ in batches[1]] == [3]
    assert [qid for qid, _ in batches[2]] == [4]
    assert [qid for qid, _ in batches[3]] == [5]
