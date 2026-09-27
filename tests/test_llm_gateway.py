from __future__ import annotations

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
    clock = StepClock()

    def advance_time(seconds: float) -> None:
        sleeper(seconds)
        clock.value += seconds

    return LLMGateway(
        profile=profile,
        config_key="profile-a",
        pacers=pacer or RecordingPacer(),
        governors=LLMExecutionGovernorRegistry(clock=clock),
        usage_sink=sink or RecordingSink(),
        trace_sink=trace_sink or RecordingTraceSink(),
        clock=clock,
        sleeper=advance_time,
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

    assert (
        gateway.chat_completions(
            request_kind=LLMRequestKind.GRADING,
            client=_client_for("chat", chat),
            model="g",
            kwargs=chat_kwargs,
        )
        is chat_response
    )
    assert (
        gateway.responses(
            request_kind=LLMRequestKind.TAGGING,
            client=_client_for("responses", responses),
            model="t",
            kwargs=responses_kwargs,
        )
        is responses_response
    )

    assert chat.calls == [
        {"messages": [], "temperature": 0.2, "model": "g", "timeout": 600.0}
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
