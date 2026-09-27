from __future__ import annotations

import importlib
from types import SimpleNamespace

from backend.llm import LLMRequestKind, NullCallTraceSink, NullUsageSink


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
