from __future__ import annotations

import importlib
from types import SimpleNamespace

import httpx
import openai
import pytest

from backend.llm import LLMRequestKind, NullCallTraceSink, NullUsageSink
from backend.llm.errors import LLMErrorCategory, classify_transport_error


@pytest.mark.parametrize("error", [RuntimeError("synthetic callback detail"), KeyboardInterrupt()])
def test_usage_callback_preserves_response_and_control_signals(error, caplog) -> None:
    from backend.llm.llm_client import LLMClient

    client = LLMClient.__new__(LLMClient)
    gateway = _RecordingGateway()

    def broken_callback(*_args):
        raise error

    def request():
        return client._create_chat_completion(
            client=object(), model="synthetic-model", messages=[], expect_json=False,
            gateway=gateway, usage_callback=broken_callback,
        )

    if isinstance(error, Exception):
        assert request() == "chat-response"
        assert "usage callback failed (RuntimeError)" in caplog.text
        assert "synthetic callback detail" not in caplog.text
    else:
        with pytest.raises(KeyboardInterrupt):
            request()
    assert len(gateway.calls) == 1


def _transport():
    return importlib.import_module("backend.llm.transport")


class _RecordingGateway:
    instances: list["_RecordingGateway"] = []

    def __init__(self, **kwargs: object) -> None:
        self.config = kwargs
        self.calls: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def chat_completions(self, **kwargs: object) -> object:
        self.calls.append({"protocol": "chat_completions", **kwargs})
        return "chat-response"

    def responses(self, **kwargs: object) -> object:
        self.calls.append({"protocol": "responses", **kwargs})
        return "responses-response"


def test_sdk_client_disables_sdk_retries_and_uses_a_bounded_default_timeout() -> None:
    transport = _transport()
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


_OPENAI_REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.AuthenticationError(
                "Error code: 401 - invalid api key",
                response=httpx.Response(401, request=_OPENAI_REQUEST),
                body=None,
            ),
            LLMErrorCategory.AUTHENTICATION,
        ),
        (
            openai.RateLimitError(
                "Error code: 429 - too many requests",
                response=httpx.Response(429, request=_OPENAI_REQUEST),
                body=None,
            ),
            LLMErrorCategory.RATE_LIMIT,
        ),
        (
            openai.BadRequestError(
                "Error code: 400 - unsupported parameter: response_format",
                response=httpx.Response(400, request=_OPENAI_REQUEST),
                body=None,
            ),
            LLMErrorCategory.PARAMETER_INCOMPATIBLE,
        ),
        (
            openai.InternalServerError(
                "Error code: 503 - service unavailable",
                response=httpx.Response(503, request=_OPENAI_REQUEST),
                body=None,
            ),
            LLMErrorCategory.SERVER_TRANSIENT,
        ),
        (openai.APITimeoutError(request=_OPENAI_REQUEST), LLMErrorCategory.TIMEOUT),
        (openai.APIConnectionError(request=_OPENAI_REQUEST), LLMErrorCategory.CONNECTION),
    ],
    ids=["401", "429", "400-param", "503", "timeout", "connection"],
)
def test_classify_transport_error_marks_model_transport_failures(
    error: BaseException,
    expected: LLMErrorCategory,
) -> None:
    assert classify_transport_error(error) is expected


@pytest.mark.parametrize(
    "error",
    [ValueError("validation failed"), RuntimeError("synthetic local failure")],
    ids=["value-error", "runtime-error"],
)
def test_classify_transport_error_passes_on_local_errors(error: BaseException) -> None:
    assert classify_transport_error(error) is None
