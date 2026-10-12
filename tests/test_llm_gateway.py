from __future__ import annotations
from backend.llm.execution import LLMExecutionGovernorRegistry
from types import SimpleNamespace
import pytest
from backend.llm.gateway import LLMGateway

from backend.llm.policy import LLMRequestKind
import importlib


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
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingPacer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, LLMRequestKind, int]] = []

    def acquire(
        self,
        config_key: str,
        kind: LLMRequestKind,
        requests_per_minute: int,
    ) -> None:
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


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
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
    sink: object | None = None,
    sleeper=lambda _seconds: None,
) -> LLMGateway:
    clock = StepClock()

    def advance_time(seconds: float) -> None:
        sleeper(seconds)
        clock.value += seconds

    return LLMGateway(
        profile=None,
        config_key="profile-a",
        pacers=RecordingPacer(),
        governors=LLMExecutionGovernorRegistry(clock=clock),
        usage_sink=sink or RecordingSink(),
        trace_sink=RecordingSink(),
        clock=clock,
        sleeper=advance_time,
    )


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


def llm_transport__transport():
    return importlib.import_module("backend.llm.transport")


def test_sdk_client_disables_sdk_retries_and_uses_a_bounded_default_timeout() -> None:
    transport = llm_transport__transport()
    factory_calls: list[dict[str, object]] = []
    fake_client = object()

    def fake_factory(**kwargs: object) -> object:
        factory_calls.append(kwargs)
        return fake_client

    created = transport.create_openai_client(
        " secret ",
        "https://example.test/",
        client_factory=fake_factory,
    )

    assert created is fake_client
    assert factory_calls == [
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "timeout": 120.0,
            "max_retries": 0,
        }
    ]
