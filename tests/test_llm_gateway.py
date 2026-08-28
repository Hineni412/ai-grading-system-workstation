from __future__ import annotations

import uuid
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.llm.gateway import LLMGateway
from backend.llm.execution import LLMExecutionGovernorRegistry
from backend.llm.policy import LLMRequestKind


class StatusError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str = "request failed",
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


class ReadTimeout(TimeoutError):
    pass


class FakeCreate:
    def __init__(self, outcomes: list[object], order: list[str] | None = None) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []
        self.order = order

    def create(self, **kwargs: object) -> object:
        if self.order is not None:
            self.order.append("call")
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingPacer:
    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[tuple[str, LLMRequestKind, int]] = []
        self.order = order

    def acquire(
        self,
        config_key: str,
        kind: LLMRequestKind,
        requests_per_minute: int,
    ) -> None:
        if self.order is not None:
            self.order.append("pace")
        self.calls.append((config_key, kind, requests_per_minute))


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def write(self, event: object) -> None:
        self.events.append(event)


class RecordingTraceSink:
    def __init__(self, order: list[str] | None = None) -> None:
        self.events = []
        self.order = order

    def write(self, event: object) -> None:
        self.events.append(event)
        if self.order is not None:
            self.order.append(f"trace:{event.event_type}")


class FailingTraceSink:
    def write(self, _event: object) -> None:
        raise RuntimeError("trace sink failed")


class StepClock:
    def __init__(self, step: float = 0.025) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def _response(**usage: int) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=usage.get("prompt_tokens", 7),
            completion_tokens=usage.get("completion_tokens", 3),
            total_tokens=usage.get("total_tokens", 10),
        )
    )


def _client_for(protocol: str, operation: FakeCreate) -> SimpleNamespace:
    if protocol == "chat":
        return SimpleNamespace(
            chat=SimpleNamespace(completions=operation),
        )
    return SimpleNamespace(responses=operation)


def _gateway(
    *,
    profile: dict[str, object] | None = None,
    sink: object | None = None,
    trace_sink: object | None = None,
    pacer: RecordingPacer | None = None,
    sleeper=lambda _seconds: None,
) -> LLMGateway:
    return LLMGateway(
        profile=profile,
        config_key="profile-a",
        pacers=pacer or RecordingPacer(),
        governors=LLMExecutionGovernorRegistry(),
        usage_sink=sink or RecordingSink(),
        trace_sink=trace_sink or RecordingTraceSink(),
        clock=StepClock(),
        sleeper=sleeper,
    )


def test_call_trace_brackets_sdk_success_with_safe_request_shape():
    order: list[str] = []
    response = _response(
        prompt_tokens=11,
        completion_tokens=5,
        total_tokens=16,
    )
    operation = FakeCreate([response], order)
    trace_sink = RecordingTraceSink(order)
    gateway = _gateway(
        profile={"llm_config_generation_timeout_seconds": 45},
        trace_sink=trace_sink,
        pacer=RecordingPacer(order),
    )

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.CONFIG_GENERATION,
        client=_client_for("chat", operation),
        model="config-model",
        kwargs={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,QUJD",
                            },
                        },
                    ],
                }
            ]
        },
        allow_retry=False,
    )

    assert actual is response
    assert order == [
        "pace",
        "trace:request_started",
        "call",
        "trace:request_succeeded",
    ]
    assert [event.event_type for event in trace_sink.events] == [
        "request_started",
        "request_succeeded",
    ]
    started, succeeded = trace_sink.events
    assert started.request_id == succeeded.request_id
    assert started.attempt == succeeded.attempt == 1
    assert started.request_kind == "config_generation"
    assert started.protocol == "chat_completions"
    assert started.model == "config-model"
    assert started.timeout_seconds == 45.0
    assert started.retry_limit == 0
    assert started.text_chars == 5
    assert started.image_count == 1
    assert started.image_bytes_estimate == 3
    assert started.request_bytes_estimate > 5
    assert succeeded.elapsed_ms == 25
    assert succeeded.outcome == "success"
    assert succeeded.prompt_tokens == 11
    assert succeeded.completion_tokens == 5
    assert succeeded.total_tokens == 16


def test_call_trace_links_sanitized_timeout_failure_to_the_retry():
    secret = "student answer C:\\private\\paper.png api_key=secret"
    operation = FakeCreate([ReadTimeout(secret), _response()])
    trace_sink = RecordingTraceSink()
    sleeper_calls: list[float] = []
    gateway = _gateway(
        profile={"llm_grading_max_retries": 1},
        trace_sink=trace_sink,
        sleeper=sleeper_calls.append,
    )

    gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="grading-model",
        kwargs={"messages": []},
        request_id="logical-request",
    )

    assert [event.event_type for event in trace_sink.events] == [
        "request_started",
        "request_failed",
        "request_started",
        "request_succeeded",
    ]
    first_started, failed, second_started, _succeeded = trace_sink.events
    assert first_started.request_id == failed.request_id == "logical-request"
    assert second_started.request_id == "logical-request"
    assert [event.attempt for event in trace_sink.events] == [1, 1, 2, 2]
    assert failed.outcome == "failure"
    assert failed.error_category == "timeout"
    assert failed.exception_type == "ReadTimeout"
    assert failed.network_phase == "read"
    assert failed.http_status_code == 0
    assert failed.will_retry is True
    assert failed.retry_delay_ms == 500
    assert sleeper_calls == [0.5]
    serialized = repr(trace_sink.events)
    assert secret not in serialized
    assert "student answer" not in serialized
    assert "api_key" not in serialized
    assert "C:\\private" not in serialized


def test_call_trace_records_safe_http_status_and_provider_request_id():
    error = StatusError(
        503,
        "secret student answer",
        headers={
            "X-Request-ID": "provider-request_123",
            "Authorization": "Bearer secret-token",
        },
    )
    operation = FakeCreate([error])
    trace_sink = RecordingTraceSink()
    gateway = _gateway(trace_sink=trace_sink)

    with pytest.raises(StatusError) as raised:
        gateway.responses(
            request_kind=LLMRequestKind.TAGGING,
            client=_client_for("responses", operation),
            model="tag-model",
            kwargs={"input": "private prompt"},
            allow_retry=False,
        )

    assert raised.value is error
    failed = trace_sink.events[-1]
    assert failed.event_type == "request_failed"
    assert failed.http_status_code == 503
    assert failed.error_category == "server_transient"
    assert failed.exception_type == "other"
    assert failed.network_phase == "http"
    assert failed.provider_request_id == "provider-request_123"
    assert failed.will_retry is False
    assert failed.retry_delay_ms == 0
    serialized = repr(failed)
    assert "secret student answer" not in serialized
    assert "Bearer secret-token" not in serialized
    assert "private prompt" not in serialized


def test_call_trace_records_success_provider_id_and_utf8_response_bytes():
    content = "答案好"
    response = SimpleNamespace(
        _request_id="provider-success_123",
        model="actual-model",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=5,
        ),
    )
    trace_sink = RecordingTraceSink()
    gateway = _gateway(trace_sink=trace_sink)

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", FakeCreate([response])),
        model="requested-model",
        kwargs={"messages": []},
        allow_retry=False,
    )

    assert actual is response
    succeeded = trace_sink.events[-1]
    assert succeeded.event_type == "request_succeeded"
    assert succeeded.provider_request_id == "provider-success_123"
    assert succeeded.actual_model == "actual-model"
    assert succeeded.response_chars == len(content)
    assert succeeded.response_bytes_estimate == len(content.encode("utf-8"))


def test_call_trace_reads_actual_model_and_provider_id_from_mapping_response():
    response = {
        "_request_id": "provider-mapping_123",
        "model": "actual-mapping-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "ok"},
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
        },
    }
    trace_sink = RecordingTraceSink()

    actual = _gateway(trace_sink=trace_sink).chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", FakeCreate([response])),
        model="requested-model",
        kwargs={"messages": []},
        allow_retry=False,
    )

    assert actual is response
    succeeded = trace_sink.events[-1]
    assert succeeded.actual_model == "actual-mapping-model"
    assert succeeded.provider_request_id == "provider-mapping_123"


def test_trace_shape_extraction_cannot_change_a_successful_call():
    response = _response()
    operation = FakeCreate([response])
    trace_sink = RecordingTraceSink()
    gateway = _gateway(trace_sink=trace_sink)

    actual = gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="tag-model",
        kwargs={"input": "private prompt", "temperature": float("nan")},
        allow_retry=False,
    )

    assert actual is response
    assert len(operation.calls) == 1
    assert [event.event_type for event in trace_sink.events] == [
        "request_started",
        "request_succeeded",
    ]
    assert trace_sink.events[0].request_bytes_estimate == 0


def test_trace_metadata_and_warning_failures_cannot_change_a_successful_call(
    monkeypatch: pytest.MonkeyPatch,
):
    response = _response()
    operation = FakeCreate([response])

    class HostileLogger:
        def warning(self, _message: str) -> None:
            raise RuntimeError("logger failed")

    monkeypatch.setattr(
        "backend.llm.gateway.utc_timestamp",
        lambda: (_ for _ in ()).throw(RuntimeError("timestamp failed")),
    )
    monkeypatch.setattr("backend.llm.gateway.logger", HostileLogger())

    actual = _gateway().chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="grading-model",
        kwargs={"messages": []},
        allow_retry=False,
    )

    assert actual is response
    assert len(operation.calls) == 1


def test_trace_sink_failure_preserves_success_and_original_sdk_error():
    successful_response = _response()
    successful_operation = FakeCreate([successful_response])

    actual = _gateway(trace_sink=FailingTraceSink()).chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", successful_operation),
        model="grading-model",
        kwargs={"messages": []},
        allow_retry=False,
    )

    assert actual is successful_response
    assert len(successful_operation.calls) == 1

    original_error = StatusError(400, "private answer")
    failing_operation = FakeCreate([original_error])
    with pytest.raises(StatusError) as raised:
        _gateway(trace_sink=FailingTraceSink()).chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", failing_operation),
            model="grading-model",
            kwargs={"messages": []},
            allow_retry=False,
        )

    assert raised.value is original_error
    assert len(failing_operation.calls) == 1


def test_trace_error_metadata_failure_still_records_the_failure_terminal():
    class HostileTimeout(TimeoutError):
        @property
        def status_code(self):
            raise RuntimeError("status lookup failed")

    original_error = HostileTimeout("private answer")
    trace_sink = RecordingTraceSink()

    with pytest.raises(HostileTimeout) as raised:
        _gateway(trace_sink=trace_sink).chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", FakeCreate([original_error])),
            model="grading-model",
            kwargs={"messages": []},
            allow_retry=False,
        )

    assert raised.value is original_error
    assert [event.event_type for event in trace_sink.events] == [
        "request_started",
        "request_failed",
    ]
    failed = trace_sink.events[-1]
    assert failed.error_category == "timeout"
    assert failed.http_status_code == 0
    assert failed.provider_request_id == ""


def test_call_trace_prefers_wrapped_network_timeout_type():
    class APITimeoutError(TimeoutError):
        pass

    wrapped = APITimeoutError("outer private message")
    wrapped.__cause__ = ReadTimeout("inner private message")
    trace_sink = RecordingTraceSink()

    with pytest.raises(APITimeoutError) as raised:
        _gateway(trace_sink=trace_sink).chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", FakeCreate([wrapped])),
            model="grading-model",
            kwargs={"messages": []},
            allow_retry=False,
        )

    assert raised.value is wrapped
    failed = trace_sink.events[-1]
    assert failed.error_category == "timeout"
    assert failed.exception_type == "ReadTimeout"
    assert failed.network_phase == "read"
    assert "private message" not in repr(failed)


def test_chat_and_responses_receive_explicit_timeout_and_preserve_kwargs():
    chat_response = _response()
    responses_response = _response()
    chat = FakeCreate([chat_response])
    responses = FakeCreate([responses_response])
    chat_kwargs = {"messages": [], "temperature": 0.2}
    responses_kwargs = {"input": "x", "metadata": {"source": "test"}}
    chat_original = dict(chat_kwargs)
    responses_original = dict(responses_kwargs)
    gateway = _gateway(profile={})

    assert gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", chat),
        model="g",
        kwargs=chat_kwargs,
    ) is chat_response
    assert gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", responses),
        model="t",
        kwargs=responses_kwargs,
    ) is responses_response

    assert chat.calls == [
        {"messages": [], "temperature": 0.2, "model": "g", "timeout": 300.0}
    ]
    assert responses.calls == [
        {
            "input": "x",
            "metadata": {"source": "test"},
            "model": "t",
            "timeout": 480.0,
        }
    ]
    assert chat_kwargs == chat_original
    assert responses_kwargs == responses_original


def test_rate_limit_retries_are_bounded_and_share_request_id():
    result = _response()
    operation = FakeCreate([StatusError(429), StatusError(429), result])
    sink = RecordingSink()
    sleeper_calls: list[float] = []
    gateway = _gateway(
        sink=sink,
        sleeper=sleeper_calls.append,
    )

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
        request_id="req-fixed",
    )

    assert actual is result
    assert len(operation.calls) == 3
    assert {event.request_id for event in sink.events} == {"req-fixed"}
    assert [event.attempt for event in sink.events] == [1, 2, 3]
    assert [event.success for event in sink.events] == [False, False, True]
    assert sleeper_calls == [0.5, 1.5]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionError("connection failed"),
        StatusError(500),
    ],
)
def test_retryable_failures_succeed_after_one_retry(error: BaseException):
    result = _response()
    operation = FakeCreate([error, result])
    sink = RecordingSink()
    sleeper_calls: list[float] = []
    gateway = _gateway(sink=sink, sleeper=sleeper_calls.append)

    actual = gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="t",
        kwargs={"input": "x"},
    )

    assert actual is result
    assert len(operation.calls) == 2
    assert sleeper_calls == [0.5]
    assert [event.success for event in sink.events] == [False, True]


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (StatusError(401, "secret-key"), "authentication"),
        (StatusError(400, "student answer was rejected"), "invalid_request"),
        (RuntimeError("C:\\private\\answer.png"), "unknown"),
    ],
)
def test_non_retryable_failures_are_recorded_without_sensitive_exception_text(
    error: BaseException,
    category: str,
):
    operation = FakeCreate([error])
    sink = RecordingSink()
    sleeper_calls: list[float] = []
    gateway = _gateway(sink=sink, sleeper=sleeper_calls.append)

    with pytest.raises(type(error)) as raised:
        gateway.chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", operation),
            model="g",
            kwargs={"messages": []},
        )

    assert raised.value is error
    assert len(operation.calls) == 1
    assert sleeper_calls == []
    assert len(sink.events) == 1
    event_record = asdict(sink.events[0])
    assert event_record["error_category"] == category
    serialized = repr(event_record)
    assert "secret-key" not in serialized
    assert "student answer" not in serialized
    assert "C:\\private" not in serialized


def test_retry_budget_exhaustion_reraises_last_error():
    errors = [StatusError(503), StatusError(503), StatusError(503)]
    operation = FakeCreate(list(errors))
    sink = RecordingSink()
    sleeper_calls: list[float] = []
    gateway = _gateway(sink=sink, sleeper=sleeper_calls.append)

    with pytest.raises(StatusError) as raised:
        gateway.chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", operation),
            model="g",
            kwargs={"messages": []},
        )

    assert raised.value is errors[-1]
    assert len(operation.calls) == 3
    assert [event.attempt for event in sink.events] == [1, 2, 3]
    assert sleeper_calls == [0.5, 1.5]


def test_allow_retry_false_makes_exactly_one_physical_request():
    error = StatusError(429)
    operation = FakeCreate([error])
    sleeper_calls: list[float] = []
    gateway = _gateway(sleeper=sleeper_calls.append)

    with pytest.raises(StatusError) as raised:
        gateway.responses(
            request_kind=LLMRequestKind.TAGGING,
            client=_client_for("responses", operation),
            model="t",
            kwargs={"input": "x"},
            allow_retry=False,
        )

    assert raised.value is error
    assert len(operation.calls) == 1
    assert sleeper_calls == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("0.2", 0.5),
        ("10", 10.0),
        ("-1", 0.5),
        ("nan", 0.5),
        ("invalid", 0.5),
    ],
)
def test_retry_after_is_finite_non_negative_and_never_shorter_than_provider(
    header: str,
    expected: float,
):
    operation = FakeCreate(
        [StatusError(429, headers={"retry-after": header}), _response()]
    )
    sleeper_calls: list[float] = []
    gateway = _gateway(sleeper=sleeper_calls.append)

    gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
    )

    assert sleeper_calls == [expected]


def test_every_physical_request_is_paced_before_invocation():
    order: list[str] = []
    pacer = RecordingPacer(order)
    operation = FakeCreate([StatusError(429), _response()], order)
    gateway = _gateway(pacer=pacer)

    gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
    )

    assert order == ["pace", "call", "pace", "call"]
    assert pacer.calls == [
        ("profile-a", LLMRequestKind.GRADING, 1000),
        ("profile-a", LLMRequestKind.GRADING, 1000),
    ]


def test_default_pacer_registry_is_shared_across_gateway_instances():
    first = LLMGateway(config_key="profile-a")
    second = LLMGateway(config_key="profile-a")

    assert first.pacers is second.pacers
    assert first.governors is second.governors


def test_generated_request_id_is_a_uuid_shared_by_all_attempts():
    sink = RecordingSink()
    operation = FakeCreate([StatusError(500), _response()])
    gateway = _gateway(sink=sink)

    gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="t",
        kwargs={"input": "x"},
    )

    request_ids = {event.request_id for event in sink.events}
    assert len(request_ids) == 1
    uuid.UUID(request_ids.pop())


def test_success_event_normalizes_usage_and_latency():
    sink = RecordingSink()
    operation = FakeCreate(
        [_response(prompt_tokens=11, completion_tokens=5, total_tokens=16)]
    )
    gateway = _gateway(sink=sink)

    gateway.chat_completions(
        request_kind=LLMRequestKind.CONFIG_GENERATION,
        client=_client_for("chat", operation),
        model="config-model",
        kwargs={"messages": []},
    )

    event = sink.events[0]
    assert event.request_kind == "config_generation"
    assert event.protocol == "chat_completions"
    assert event.model == "config-model"
    assert event.latency_ms == 25
    assert event.prompt_tokens == 11
    assert event.completion_tokens == 5
    assert event.total_tokens == 16


def test_success_event_records_safe_output_diagnostics():
    sink = RecordingSink()
    result = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content='{"rubric":['),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=4096,
            total_tokens=4107,
        ),
    )
    operation = FakeCreate([result])
    gateway = _gateway(sink=sink)

    gateway.chat_completions(
        request_kind=LLMRequestKind.CONFIG_GENERATION,
        client=_client_for("chat", operation),
        model="config-model",
        kwargs={"messages": []},
    )

    event = sink.events[0]
    assert event.finish_reason == "length"
    assert event.output_truncated is True
    assert event.response_chars == 11
    assert len(event.response_sha256) == 64
    assert "rubric" not in repr(event)


def test_usage_sink_failure_does_not_change_returned_response():
    class FailingSink:
        def write(self, event: object) -> None:
            raise OSError("secret-key C:\\private\\usage.jsonl")

    result = _response()
    operation = FakeCreate([result])
    gateway = _gateway(sink=FailingSink())

    actual = gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="t",
        kwargs={"input": "x"},
    )

    assert actual is result


def test_malformed_usage_records_zero_token_success_without_changing_response():
    sink = RecordingSink()
    result = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens="not-an-int",
            completion_tokens=3,
            total_tokens=10,
        )
    )
    operation = FakeCreate([result])
    gateway = _gateway(sink=sink)

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
    )

    assert actual is result
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.success is True
    assert event.attempt == 1
    assert event.prompt_tokens == 0
    assert event.completion_tokens == 0
    assert event.total_tokens == 0


def test_logger_failure_during_malformed_usage_does_not_change_response(
    monkeypatch,
):
    def fail_to_warn(*args: object, **kwargs: object) -> None:
        raise RuntimeError("hostile logging handler")

    monkeypatch.setattr("backend.llm.gateway.logger.warning", fail_to_warn)
    sink = RecordingSink()
    result = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens="not-an-int")
    )
    operation = FakeCreate([result])
    gateway = _gateway(sink=sink)

    actual = gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="t",
        kwargs={"input": "x"},
    )

    assert actual is result
    assert len(sink.events) == 1
    assert sink.events[0].success is True
    assert sink.events[0].total_tokens == 0


def test_logger_failure_during_sink_failure_does_not_change_success_response(
    monkeypatch,
):
    class FailingSink:
        def write(self, event: object) -> None:
            raise OSError("usage sink unavailable")

    def fail_to_warn(*args: object, **kwargs: object) -> None:
        raise RuntimeError("hostile logging handler")

    monkeypatch.setattr("backend.llm.gateway.logger.warning", fail_to_warn)
    result = _response()
    operation = FakeCreate([result])
    gateway = _gateway(sink=FailingSink())

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
    )

    assert actual is result


def test_logger_failure_during_failure_record_does_not_prevent_provider_retry(
    monkeypatch,
):
    class FailOnceSink:
        def __init__(self) -> None:
            self.calls = 0
            self.events = []

        def write(self, event: object) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("usage sink unavailable")
            self.events.append(event)

    def fail_to_warn(*args: object, **kwargs: object) -> None:
        raise RuntimeError("hostile logging handler")

    monkeypatch.setattr("backend.llm.gateway.logger.warning", fail_to_warn)
    sink = FailOnceSink()
    result = _response()
    operation = FakeCreate([StatusError(429), result])
    sleeper_calls: list[float] = []
    gateway = _gateway(
        sink=sink,
        sleeper=sleeper_calls.append,
    )

    actual = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=_client_for("chat", operation),
        model="g",
        kwargs={"messages": []},
    )

    assert actual is result
    assert len(operation.calls) == 2
    assert sleeper_calls == [0.5]
    assert len(sink.events) == 1
    assert sink.events[0].success is True
    assert sink.events[0].attempt == 2


def test_logger_failure_during_failure_record_preserves_original_provider_error(
    monkeypatch,
):
    class FailingSink:
        def write(self, event: object) -> None:
            raise OSError("usage sink unavailable")

    def fail_to_warn(*args: object, **kwargs: object) -> None:
        raise RuntimeError("hostile logging handler")

    monkeypatch.setattr("backend.llm.gateway.logger.warning", fail_to_warn)
    error = StatusError(401, "authentication rejected")
    operation = FakeCreate([error])
    gateway = _gateway(sink=FailingSink())

    with pytest.raises(StatusError) as raised:
        gateway.responses(
            request_kind=LLMRequestKind.TAGGING,
            client=_client_for("responses", operation),
            model="t",
            kwargs={"input": "x"},
        )

    assert raised.value is error


@pytest.mark.parametrize(
    ("request_id", "expected"),
    [("", ""), (0, "0")],
)
def test_explicit_falsy_request_id_is_preserved(
    request_id: object,
    expected: str,
):
    sink = RecordingSink()
    operation = FakeCreate([_response()])
    gateway = _gateway(sink=sink)

    gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=_client_for("responses", operation),
        model="t",
        kwargs={"input": "x"},
        request_id=request_id,
    )

    assert sink.events[0].request_id == expected


def test_compatibility_fallback_metadata_is_recorded_for_physical_attempt():
    sink = RecordingSink()
    operation = FakeCreate([_response()])
    gateway = _gateway(sink=sink)

    gateway.chat_completions(
        request_kind=LLMRequestKind.CONFIG_GENERATION,
        client=_client_for("chat", operation),
        model="config-model",
        kwargs={"messages": []},
        request_id="req-compat",
        allow_retry=False,
        compatibility_fallback="response_format",
    )

    assert sink.events[0].request_id == "req-compat"
    assert sink.events[0].compatibility_fallback == "response_format"


def test_unknown_compatibility_fallback_metadata_is_not_persisted():
    sink = RecordingSink()
    operation = FakeCreate([_response()])
    gateway = _gateway(sink=sink)

    gateway.chat_completions(
        request_kind=LLMRequestKind.CONFIG_GENERATION,
        client=_client_for("chat", operation),
        model="config-model",
        kwargs={"messages": []},
        compatibility_fallback="secret-key C:\\private\\answer.png",
    )

    assert sink.events[0].compatibility_fallback == ""
