from __future__ import annotations

import uuid
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.llm.gateway import LLMGateway
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
    pacer: RecordingPacer | None = None,
    sleeper=lambda _seconds: None,
) -> LLMGateway:
    return LLMGateway(
        profile=profile,
        config_key="profile-a",
        pacers=pacer or RecordingPacer(),
        usage_sink=sink or RecordingSink(),
        clock=StepClock(),
        sleeper=sleeper,
    )


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
            "timeout": 120.0,
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
        ("0.2", 0.2),
        ("10", 0.5),
        ("-1", 0.5),
        ("nan", 0.5),
        ("invalid", 0.5),
    ],
)
def test_retry_after_is_finite_non_negative_and_capped_to_policy_delay(
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
