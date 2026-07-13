from __future__ import annotations

import importlib

import llm_client
from backend.llm import LLMRequestKind, NullUsageSink


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


def test_normalized_urls_share_an_opaque_config_identity() -> None:
    transport = _transport()

    normalized = transport.normalize_openai_base_url(" https://example.test/ ")
    first = transport.gateway_config_key("secret-one", "https://example.test")
    equivalent = transport.gateway_config_key(
        "secret-one", "https://example.test/v1/"
    )
    different = transport.gateway_config_key(
        "secret-two", "https://example.test/v1"
    )

    assert normalized == "https://example.test/v1"
    assert first == equivalent
    assert first != different
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")
    assert "secret-one" not in first
    assert "example.test" not in first


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


def test_adapter_forwards_request_kind_client_and_a_fresh_kwargs_copy() -> None:
    transport = _transport()
    _RecordingGateway.instances.clear()
    fake_client = object()
    original_kwargs = {
        "messages": [],
        "response_format": {"type": "json_object"},
    }

    adapter = transport.LLMProtocolAdapter(
        api_key="secret",
        base_url="https://example.test/v1",
        policy_profile={"llm_recognition_timeout_seconds": 45},
        client=fake_client,
        gateway_factory=_RecordingGateway,
        usage_sink_factory=NullUsageSink,
    )
    result = adapter.chat_completions(
        request_kind=LLMRequestKind.RECOGNITION,
        model="objective-model",
        kwargs=original_kwargs,
        request_id="logical-request",
        allow_retry=False,
    )

    assert result == "chat-response"
    assert len(_RecordingGateway.instances) == 1
    gateway = _RecordingGateway.instances[0]
    assert gateway.config["profile"] == {
        "llm_recognition_timeout_seconds": 45
    }
    assert isinstance(gateway.config["usage_sink"], NullUsageSink)
    assert gateway.calls[0]["request_kind"] is LLMRequestKind.RECOGNITION
    assert gateway.calls[0]["client"] is fake_client
    assert gateway.calls[0]["model"] == "objective-model"
    assert gateway.calls[0]["request_id"] == "logical-request"
    assert gateway.calls[0]["allow_retry"] is False
    assert gateway.calls[0]["kwargs"] == original_kwargs
    assert gateway.calls[0]["kwargs"] is not original_kwargs
    assert original_kwargs == {
        "messages": [],
        "response_format": {"type": "json_object"},
    }


def test_adapter_owns_one_created_client_and_one_gateway_for_both_protocols(
    monkeypatch,
) -> None:
    transport = _transport()
    _RecordingGateway.instances.clear()
    fake_client = object()
    client_calls: list[tuple[str, str]] = []

    def fake_create_client(api_key: str, base_url: str) -> object:
        client_calls.append((api_key, base_url))
        return fake_client

    monkeypatch.setattr(transport, "create_openai_client", fake_create_client)
    adapter = transport.LLMProtocolAdapter(
        api_key="secret",
        base_url="https://example.test",
        gateway_factory=_RecordingGateway,
        usage_sink_factory=NullUsageSink,
    )

    assert adapter.chat_completions(
        request_kind=LLMRequestKind.RECOGNITION,
        model="vision-model",
        kwargs={"messages": []},
    ) == "chat-response"
    assert adapter.responses(
        request_kind=LLMRequestKind.TAGGING,
        model="tag-model",
        kwargs={"input": "prompt", "text": {"format": {}}},
    ) == "responses-response"

    assert client_calls == [("secret", "https://example.test")]
    assert len(_RecordingGateway.instances) == 1
    assert [call["client"] for call in _RecordingGateway.instances[0].calls] == [
        fake_client,
        fake_client,
    ]
    assert _RecordingGateway.instances[0].calls[1]["request_kind"] is LLMRequestKind.TAGGING


def test_llm_client_compatibility_helpers_delegate_to_shared_transport(
    monkeypatch,
) -> None:
    client_sentinel = object()
    monkeypatch.setattr(
        llm_client,
        "_shared_create_openai_client",
        lambda api_key, base_url: (client_sentinel, api_key, base_url),
        raising=False,
    )
    monkeypatch.setattr(
        llm_client,
        "_shared_gateway_config_key",
        lambda api_key, base_url: f"opaque:{api_key}:{base_url}",
        raising=False,
    )
    monkeypatch.setattr(
        llm_client,
        "_shared_normalize_openai_base_url",
        lambda base_url: f"normalized:{base_url}",
        raising=False,
    )

    assert llm_client._create_openai_client("key", "url") == (
        client_sentinel,
        "key",
        "url",
    )
    assert llm_client._gateway_config_key("key", "url") == "opaque:key:url"
    assert llm_client.normalize_openai_base_url("url") == "normalized:url"


def test_transport_interfaces_are_reexported_from_backend_llm() -> None:
    backend_llm = importlib.import_module("backend.llm")
    transport = _transport()

    assert backend_llm.LLMProtocolAdapter is transport.LLMProtocolAdapter
    assert backend_llm.create_openai_client is transport.create_openai_client
    assert backend_llm.gateway_config_key is transport.gateway_config_key
    assert (
        backend_llm.normalize_openai_base_url
        is transport.normalize_openai_base_url
    )
