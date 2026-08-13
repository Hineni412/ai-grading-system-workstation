import json
from types import SimpleNamespace

import pytest

import backend.llm as backend_llm
import question_bank.services.ai_tagging_service as ai_tagging_module
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingService,
    TaggingRequestEvent,
    _TaggingRequestController,
    classify_tagging_error,
    sanitize_tagging_error,
)


def test_classify_tagging_error_categories() -> None:
    assert classify_tagging_error(RuntimeError("429 Too Many Requests")) == "rate_limit"
    assert classify_tagging_error(TimeoutError("request timed out")) == "timeout"
    assert classify_tagging_error(ConnectionError("connection refused")) == "network"
    assert classify_tagging_error(ValueError("failed to parse JSON")) == "parse"
    assert classify_tagging_error(ValueError("response did not include results")) == "validation"
    assert classify_tagging_error(RuntimeError("something odd")) == "unknown"


def test_sanitize_tagging_error_masks_secrets_and_truncates() -> None:
    message = "auth failed api_key=sk-secret-123 token: bearer-xyz rest of message"
    cleaned = sanitize_tagging_error(message)
    assert "sk-secret-123" not in cleaned
    assert "bearer-xyz" not in cleaned
    assert "api_key=***" in cleaned

    long = "x" * 900
    assert len(sanitize_tagging_error(long)) <= 501


def test_controller_run_emits_started_then_succeeded() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)

    result = controller.run("batch", (1, 2), lambda: "ok", model_name="m")

    assert result == "ok"
    assert [e.phase for e in events] == ["started", "succeeded"]
    assert events[-1].model_name == "m"


def test_controller_run_emits_failed_and_reraises_with_sanitized_error() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)

    def boom() -> None:
        raise RuntimeError("429 rate limit api_key=sk-topsecret")

    with pytest.raises(RuntimeError):
        controller.run("batch", (7,), boom, model_name="m")

    assert [e.phase for e in events] == ["started", "failed"]
    failed = events[-1]
    assert failed.error_category == "rate_limit"
    assert "sk-topsecret" not in failed.error_message
    assert failed.question_ids == (7,)


def test_controller_finish_helper_emits_failed_with_category() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)
    started = controller.begin("batch", (3, 4))
    controller.finish(started, "failed", ConnectionError("network unreachable"), model_name="m")

    assert events[-1].phase == "failed"
    assert events[-1].error_category == "network"
    assert events[-1].request_number == started.request_number


def test_batch_gateway_retry_does_not_multiply_logical_request_events(
    monkeypatch,
) -> None:
    provider_calls = 0
    events: list[TaggingRequestEvent] = []

    class Retryable503Error(RuntimeError):
        status_code = 503
        response = type(
            "Response",
            (),
            {"status_code": 503, "headers": {"retry-after": "0"}},
        )()

    def fake_provider_create(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            raise Retryable503Error("temporary provider outage")
        payload = {
            "results": [
                {
                    "question_id": 1,
                    "knowledge_points": ["整式运算"],
                    "method_tags": ["整体思想"],
                    "ability_tags": ["运算能力"],
                    "math_model_tags": [],
                    "difficulty": 3,
                    "error_prone_points": ["符号错误"],
                    "prerequisite_points": ["有理数运算"],
                    "textbook_chapter": "七年级下册 第一章 整式的乘除",
                    "teaching_stage": "期末复习",
                    "suitable_student_level": "基础巩固",
                    "canonical_knowledge_id": "",
                    "sub_skills": [],
                    "measured_skills": ["整式乘法运算"],
                    "supporting_skills": ["整数指数幂运算"],
                    "reason": "考查幂运算和整式化简。",
                    "confidence": 0.86,
                }
            ]
        }
        return SimpleNamespace(output_text=json.dumps(payload))

    fake_client = SimpleNamespace(
        responses=SimpleNamespace(create=fake_provider_create)
    )
    real_adapter_class = backend_llm.LLMProtocolAdapter

    class IsolatedProtocolAdapter:
        def __init__(
            self,
            api_key,
            base_url,
            policy_profile=None,
            client=None,
        ) -> None:
            self._adapter = real_adapter_class(
                api_key,
                base_url,
                policy_profile=policy_profile,
                client=client,
                gateway_factory=lambda **kwargs: backend_llm.LLMGateway(
                    **kwargs,
                    sleeper=lambda _seconds: None,
                ),
                usage_sink_factory=backend_llm.NullUsageSink,
                trace_sink_factory=backend_llm.NullCallTraceSink,
            )

        def responses(self, **kwargs):
            return self._adapter.responses(**kwargs)

    monkeypatch.setattr(
        ai_tagging_module,
        "LLMProtocolAdapter",
        IsolatedProtocolAdapter,
        raising=False,
    )

    class UnexpectedLLMClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("raw Responses injection was shadowed by LLMClient")

    monkeypatch.setattr("llm_client.LLMClient", UnexpectedLLMClient)
    monkeypatch.setattr(ai_tagging_module, "LLMClient", UnexpectedLLMClient)
    monkeypatch.setattr(ai_tagging_module, "_dotenv_values", lambda: {})
    service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "fake-tagging-key",
            "QUESTION_BANK_TAGGING_BASE_URL": "https://provider.invalid/v1",
            "QUESTION_BANK_TAGGING_MODEL": "fake-tagging-model",
        },
        client=fake_client,
    )
    context = TaggingContext(
        question_text="计算 a^2 · a^3。",
        answer_text="a^5",
        question_number="1",
        question_type="选择题",
    )

    results = service.analyze_questions(
        {1: context},
        max_workers=1,
        requests_per_minute=10000,
        request_callback=events.append,
        allow_batch_fallback=False,
        quality_retry_limit=0,
        enable_review=False,
    )

    assert provider_calls == 2
    assert results[1].quality_status == "complete"
    assert [(event.request_number, event.phase) for event in events] == [
        (1, "started"),
        (1, "succeeded"),
    ]
