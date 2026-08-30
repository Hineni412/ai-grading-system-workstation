from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm_client
from backend.llm import (
    LLMRequestKind,
    NullCallTraceSink,
    NullUsageSink,
    policy_from_profile,
)
from backend.llm.errors import LLMErrorCategory, classify_llm_error
from backend.llm.execution import LLMExecutionGovernorRegistry
from backend.llm.gateway import LLMGateway
from backend.llm.policy import LLMPolicyError
from backend.llm.transport import normalize_openai_base_url


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str = "request failed") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers={})


class _ReadTimeout(TimeoutError):
    pass


class _FakeCreate:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _NullPacer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, LLMRequestKind, int]] = []

    def acquire(self, config_key: str, kind: LLMRequestKind, rpm: int) -> None:
        self.calls.append((config_key, kind, rpm))


def _gateway(**overrides: object) -> LLMGateway:
    kwargs: dict[str, object] = {
        "profile": {},
        "config_key": "batch-test",
        "pacers": _NullPacer(),
        "governors": LLMExecutionGovernorRegistry(),
        "usage_sink": NullUsageSink(),
        "trace_sink": NullCallTraceSink(),
        "diagnostic_sink": None,
        "sleeper": lambda _seconds: None,
    }
    kwargs.update(overrides)
    if kwargs["diagnostic_sink"] is None:
        kwargs.pop("diagnostic_sink")
    return LLMGateway(**kwargs)  # type: ignore[arg-type]


def _json_completion(payload: str = '{"ok": true}') -> dict[str, object]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": payload},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def test_batch_policy_defaults_to_long_timeout_and_patient_retries() -> None:
    policy = policy_from_profile(LLMRequestKind.GRADING_BATCH, {})

    assert policy.timeout_seconds == 3600.0
    assert policy.max_retries == 10
    assert policy.retry_delays == (
        2.0, 5.0, 10.0, 20.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0,
    )
    assert policy.retry_on_timeout is False


def test_batch_retry_cap_allows_twenty_attempts() -> None:
    policy = policy_from_profile(
        LLMRequestKind.GRADING_BATCH,
        {"llm_grading_batch_max_retries": 20},
    )
    assert policy.max_retries == 20
    assert len(policy.retry_delays) == 20

    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.GRADING_BATCH,
            {"llm_grading_batch_max_retries": 21},
        )

    # Online kinds keep the tighter cap.
    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.GRADING,
            {"llm_grading_max_retries": 6},
        )


def test_tagging_batch_policy_matches_grading_batch() -> None:
    policy = policy_from_profile(LLMRequestKind.TAGGING_BATCH, {})

    assert policy.timeout_seconds == 3600.0
    assert policy.max_retries == 10
    assert policy.retry_delays == (
        2.0, 5.0, 10.0, 20.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0,
    )
    assert policy.retry_on_timeout is False

    capped = policy_from_profile(
        LLMRequestKind.TAGGING_BATCH,
        {"llm_tagging_batch_timeout_seconds": 7200},
    )
    assert capped.timeout_seconds == 7200.0


def test_batch_policy_accepts_overrides_beyond_the_online_cap() -> None:
    policy = policy_from_profile(
        LLMRequestKind.GRADING_BATCH,
        {"llm_grading_batch_timeout_seconds": 7200},
    )

    assert policy.timeout_seconds == 7200.0

    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.GRADING_BATCH,
            {"llm_grading_batch_timeout_seconds": 7201},
        )


def test_online_policy_timeout_cap_is_below_batch_cap() -> None:
    policy = policy_from_profile(
        LLMRequestKind.GRADING,
        {"llm_grading_timeout_seconds": 1200},
    )
    assert policy.timeout_seconds == 1200.0

    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.GRADING,
            {"llm_grading_timeout_seconds": 1201},
        )

    default = policy_from_profile(LLMRequestKind.GRADING, {})
    assert default.timeout_seconds == 300.0
    assert default.retry_on_timeout is True


# ---------------------------------------------------------------------------
# gateway retry behaviour
# ---------------------------------------------------------------------------


def test_batch_kind_retries_server_overloaded_with_backoff() -> None:
    sleeps: list[float] = []
    operation = _FakeCreate([_StatusError(429), _StatusError(503), "done"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=operation))
    gateway = _gateway(sleeper=sleeps.append)

    result = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING_BATCH,
        client=client,
        model="ep-bi-test",
        kwargs={"messages": []},
        allow_retry=True,
    )

    assert result == "done"
    assert len(operation.calls) == 3
    assert sleeps == [2.0, 5.0]
    # The batch policy timeout reaches the SDK per request.
    assert operation.calls[0]["timeout"] == 3600.0


def test_batch_kind_never_retries_timeouts_to_avoid_double_billing() -> None:
    operation = _FakeCreate([_ReadTimeout("slow"), "unreachable"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=operation))
    gateway = _gateway()

    with pytest.raises(_ReadTimeout):
        gateway.chat_completions(
            request_kind=LLMRequestKind.GRADING_BATCH,
            client=client,
            model="ep-bi-test",
            kwargs={"messages": []},
            allow_retry=True,
        )

    assert len(operation.calls) == 1


def test_online_kind_still_retries_timeouts() -> None:
    operation = _FakeCreate([_ReadTimeout("slow"), "recovered"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=operation))
    gateway = _gateway()

    result = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=client,
        model="online-model",
        kwargs={"messages": []},
        allow_retry=True,
    )

    assert result == "recovered"
    assert len(operation.calls) == 2


def test_classify_server_overloaded_variants_as_retryable() -> None:
    assert (
        classify_llm_error(_StatusError(429)) is LLMErrorCategory.RATE_LIMIT
    )
    assert (
        classify_llm_error(_StatusError(503))
        is LLMErrorCategory.SERVER_TRANSIENT
    )


# ---------------------------------------------------------------------------
# LLMClient batch routing
# ---------------------------------------------------------------------------


class _RecordingGateway:
    instances: list["_RecordingGateway"] = []

    def __init__(self, **kwargs: object) -> None:
        self.config = kwargs
        self.calls: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def chat_completions(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return _json_completion()


def _settings(**overrides: object) -> llm_client.LLMSettings:
    values: dict[str, object] = {
        "api_key": "online-key",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "ocr_model": "online-ocr",
        "grading_model": "online-grader",
        "config_model": "online-config",
    }
    values.update(overrides)
    return llm_client.LLMSettings(**values)  # type: ignore[arg-type]


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    settings: llm_client.LLMSettings,
) -> llm_client.LLMClient:
    _RecordingGateway.instances.clear()
    created_clients: list[tuple[str, str]] = []

    def fake_create(api_key: str, base_url: str) -> object:
        created_clients.append((api_key, base_url))
        return SimpleNamespace(name=f"client-{len(created_clients)}")

    monkeypatch.setattr(llm_client, "_shared_create_openai_client", fake_create)
    client = llm_client.LLMClient(
        settings,
        gateway_factory=_RecordingGateway,
        usage_sink_factory=NullUsageSink,
        trace_sink_factory=NullCallTraceSink,
    )
    client._created_clients = created_clients  # type: ignore[attr-defined]
    return client


def test_grading_requests_route_to_batch_channel_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(
            batch_enabled=True,
            batch_model="ep-bi-20260816162754-sj5bk",
        ),
    )

    result = client.json_from_images_with_options(
        "grade this",
        [b"fake-image-bytes"],
        extra_kwargs={"timeout_override_seconds": 600},
    )

    assert result == {"ok": True}
    # main gateway + batch gateway (config credentials reuse the main one)
    assert len(_RecordingGateway.instances) == 2
    batch_gateway = _RecordingGateway.instances[1]
    assert len(batch_gateway.calls) == 1
    call = batch_gateway.calls[0]
    assert call["request_kind"] is LLMRequestKind.GRADING_BATCH
    assert call["model"] == "ep-bi-20260816162754-sj5bk"
    assert call["allow_retry"] is True
    assert call["timeout_override_seconds"] is None
    assert call["client"] is client.batch_client
    assert client._created_clients[-1] == (  # type: ignore[attr-defined]
        "online-key",
        "https://ark.cn-beijing.volces.com/api/v3/batch",
    )
    # The online gateway saw nothing.
    assert _RecordingGateway.instances[0].calls == []


def test_batch_explicit_base_url_and_key_are_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(
            batch_enabled=True,
            batch_model="ep-bi-x",
            batch_base_url="https://custom.example.test/api/v3/batch/",
            batch_api_key="batch-key",
        ),
    )

    client.json_from_images("grade", [b"img"])

    assert client._created_clients[-1] == (  # type: ignore[attr-defined]
        "batch-key",
        "https://custom.example.test/api/v3/batch",
    )


def test_disabled_batch_keeps_everything_on_the_online_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(monkeypatch, _settings())

    client.json_from_images_with_options(
        "grade this",
        [b"img"],
        extra_kwargs={"timeout_override_seconds": 600},
    )

    assert len(_RecordingGateway.instances) == 1
    call = _RecordingGateway.instances[0].calls[0]
    assert call["request_kind"] is LLMRequestKind.GRADING
    assert call["model"] == "online-grader"
    assert call["allow_retry"] is False
    assert call["timeout_override_seconds"] == 600


def test_batch_enabled_without_model_falls_back_to_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(batch_enabled=True, batch_model=None),
    )

    client.json_from_images("grade", [b"img"])

    assert client.batch_enabled is False
    assert len(_RecordingGateway.instances) == 1
    assert (
        _RecordingGateway.instances[0].calls[0]["request_kind"]
        is LLMRequestKind.GRADING
    )


def test_non_grading_kinds_never_route_to_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(batch_enabled=True, batch_model="ep-bi-x"),
    )

    client.json_from_text("generate config")
    client.text_from_images("ocr this", [b"img"])

    online = _RecordingGateway.instances[0]
    assert [call["request_kind"] for call in online.calls] == [
        LLMRequestKind.CONFIG_GENERATION,
        LLMRequestKind.RECOGNITION,
    ]
    assert _RecordingGateway.instances[1].calls == []


def test_tagging_text_requests_route_to_batch_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(batch_enabled=True, batch_model="ep-bi-x"),
    )

    client.json_from_text(
        "tag this question",
        request_kind=LLMRequestKind.TAGGING,
        extra_kwargs={"timeout_override_seconds": 240},
    )

    batch_gateway = _RecordingGateway.instances[1]
    assert len(batch_gateway.calls) == 1
    call = batch_gateway.calls[0]
    assert call["request_kind"] is LLMRequestKind.TAGGING_BATCH
    assert call["model"] == "ep-bi-x"
    assert call["allow_retry"] is True
    assert call["timeout_override_seconds"] is None
    assert _RecordingGateway.instances[0].calls == []


def test_tagging_batch_opt_out_stays_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(batch_enabled=True, batch_model="ep-bi-x"),
    )

    client.json_from_text_once(
        "taxonomy merge suggestion",
        request_kind=LLMRequestKind.TAGGING,
        extra_kwargs={"disable_batch_routing": True},
    )

    online = _RecordingGateway.instances[0]
    assert len(online.calls) == 1
    call = online.calls[0]
    assert call["request_kind"] is LLMRequestKind.TAGGING
    assert call["model"] == "online-config"
    assert call["allow_retry"] is False
    assert _RecordingGateway.instances[1].calls == []


def test_json_from_images_once_uses_batch_channel_with_strict_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(
        monkeypatch,
        _settings(batch_enabled=True, batch_model="ep-bi-x"),
    )

    client.json_from_images_once(
        "grade",
        [b"img"],
        extra_kwargs={"timeout": None, "timeout_override_seconds": 600},
    )

    call = _RecordingGateway.instances[1].calls[0]
    assert call["request_kind"] is LLMRequestKind.GRADING_BATCH
    assert call["model"] == "ep-bi-x"
    assert call["allow_retry"] is True
    assert call["timeout_override_seconds"] is None
    assert "timeout" not in call["kwargs"]


# ---------------------------------------------------------------------------
# settings plumbing
# ---------------------------------------------------------------------------


def test_active_settings_pick_up_batch_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.jobs import default_handlers

    monkeypatch.setattr(default_handlers, "get_api_profile_store", lambda: object())
    monkeypatch.setattr(
        default_handlers,
        "resolve_profile_for_task",
        lambda _store, _task: {},
    )
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("LLM_GRADING_MODEL", "doubao-online")
    monkeypatch.setenv("LLM_BATCH_ENABLED", "true")
    monkeypatch.setenv("LLM_BATCH_MODEL", "ep-bi-20260816162754-sj5bk")
    monkeypatch.delenv("LLM_BATCH_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BATCH_API_KEY", raising=False)

    settings = default_handlers._active_llm_settings()

    assert settings is not None
    assert settings.batch_enabled is True
    assert settings.batch_model == "ep-bi-20260816162754-sj5bk"
    assert settings.batch_base_url is None
    assert settings.batch_api_key is None


def test_active_settings_default_batch_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.jobs import default_handlers

    monkeypatch.setattr(default_handlers, "get_api_profile_store", lambda: object())
    monkeypatch.setattr(
        default_handlers,
        "resolve_profile_for_task",
        lambda _store, _task: {},
    )
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    for name in (
        "LLM_BATCH_ENABLED",
        "LLM_BATCH_MODEL",
        "LLM_BATCH_BASE_URL",
        "LLM_BATCH_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = default_handlers._active_llm_settings()

    assert settings is not None
    assert settings.batch_enabled is False


def test_derive_batch_base_url_appends_batch_segment() -> None:
    assert (
        llm_client._derive_batch_base_url("https://ark.cn-beijing.volces.com/api/v3")
        == "https://ark.cn-beijing.volces.com/api/v3/batch"
    )
    assert (
        normalize_openai_base_url("https://ark.cn-beijing.volces.com/api/v3/batch")
        == "https://ark.cn-beijing.volces.com/api/v3/batch"
    )


def test_tagging_service_settings_pick_up_batch_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import ai_tagging_service

    monkeypatch.setattr(
        ai_tagging_service,
        "_active_saved_profile",
        lambda: {
            "api_key": "profile-key",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "batch_enabled": True,
            "batch_model": "ep-bi-x",
        },
    )

    settings = ai_tagging_service._llm_settings_from_profile()

    assert settings is not None
    assert settings.batch_enabled is True
    assert settings.batch_model == "ep-bi-x"
    assert settings.batch_base_url is None
    assert settings.batch_api_key is None
