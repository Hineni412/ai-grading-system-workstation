from __future__ import annotations

import httpx
import openai
import pytest

from backend.llm.errors import (
    LLMErrorCategory,
    classify_llm_error,
    is_retryable_error,
)
from backend.llm.policy import (
    LLMPolicyError,
    LLMProtocol,
    LLMRequestKind,
    policy_from_profile,
    policy_overrides_from_profile,
)


def _status_error(status_code: int, message: str = "request failed") -> openai.APIStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(message, response=response, body=None)


def test_request_and_protocol_values_are_stable():
    assert [kind.value for kind in LLMRequestKind] == [
        "grading",
        "grading_batch",
        "recognition",
        "config_generation",
        "tagging",
        "tagging_batch",
        "workspace",
        "assembly",
    ]
    assert [protocol.value for protocol in LLMProtocol] == [
        "chat_completions",
        "responses",
    ]


def test_default_timeout_budgets_are_explicit():
    assert policy_from_profile(LLMRequestKind.GRADING, None).timeout_seconds == 300.0
    assert policy_from_profile(LLMRequestKind.GRADING_BATCH, None).timeout_seconds == 3600.0
    assert policy_from_profile(LLMRequestKind.RECOGNITION, None).timeout_seconds == 60.0
    assert policy_from_profile(LLMRequestKind.CONFIG_GENERATION, None).timeout_seconds == 600.0
    assert policy_from_profile(LLMRequestKind.TAGGING, None).timeout_seconds == 480.0
    assert policy_from_profile(LLMRequestKind.WORKSPACE, None).max_retries == 0


def test_profile_overrides_are_scoped_to_request_kind():
    profile = {
        "llm_grading_timeout_seconds": 240,
        "llm_grading_max_retries": 1,
        "llm_grading_requests_per_minute": 60,
        "llm_tagging_timeout_seconds": 30,
    }

    policy = policy_from_profile("grading", profile)

    assert policy.timeout_seconds == 240.0
    assert policy.max_retries == 1
    assert policy.requests_per_minute == 60
    assert policy.retry_delays == (0.5,)


def test_config_generation_profile_can_keep_a_shorter_timeout():
    policy = policy_from_profile(
        LLMRequestKind.CONFIG_GENERATION,
        {"llm_config_generation_timeout_seconds": 90},
    )

    assert policy.timeout_seconds == 90.0


def test_five_retries_have_five_deterministic_delays():
    policy = policy_from_profile(
        LLMRequestKind.GRADING,
        {"llm_grading_max_retries": 5},
    )

    assert policy.retry_delays == (0.5, 1.5, 3.0, 5.0, 8.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_grading_timeout_seconds", None),
        ("llm_grading_timeout_seconds", 0),
        ("llm_grading_timeout_seconds", 1201),
        ("llm_grading_timeout_seconds", float("inf")),
        ("llm_grading_max_retries", 6),
        ("llm_grading_max_retries", True),
        ("llm_grading_requests_per_minute", "fast"),
    ],
)
def test_invalid_profile_override_fails_before_request(field, value):
    with pytest.raises(LLMPolicyError, match=field):
        policy_from_profile(LLMRequestKind.GRADING, {field: value})


def test_policy_override_copy_excludes_secrets_and_unrelated_fields():
    copied = policy_overrides_from_profile(
        {
            "api_key": "secret",
            "base_url": "https://private.example/v1",
            "llm_grading_timeout_seconds": 240,
            "grading_model": "model",
        }
    )

    scope_key = copied.pop("_llm_execution_scope_key")
    assert copied == {"llm_grading_timeout_seconds": 240}
    assert isinstance(scope_key, str)
    assert "secret" not in scope_key
    assert "private.example" not in scope_key


def test_policy_override_copy_allows_all_twelve_policy_fields():
    profile = {
        f"llm_{kind.value}_{suffix}": index
        for index, (kind, suffix) in enumerate(
            (
                (kind, suffix)
                for kind in LLMRequestKind
                for suffix in (
                    "timeout_seconds",
                    "max_retries",
                    "requests_per_minute",
                )
            ),
            start=1,
        )
    }

    copied = policy_overrides_from_profile(profile)
    copied.pop("_llm_execution_scope_key")
    assert copied == profile


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (
            openai.APITimeoutError(
                request=httpx.Request("POST", "https://example.invalid")
            ),
            LLMErrorCategory.TIMEOUT,
        ),
        (
            openai.APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            ),
            LLMErrorCategory.CONNECTION,
        ),
        (_status_error(429), LLMErrorCategory.RATE_LIMIT),
        (_status_error(500), LLMErrorCategory.SERVER_TRANSIENT),
        (_status_error(502), LLMErrorCategory.SERVER_TRANSIENT),
        (_status_error(503), LLMErrorCategory.SERVER_TRANSIENT),
        (_status_error(504), LLMErrorCategory.SERVER_TRANSIENT),
    ],
)
def test_transient_openai_errors_are_retryable(error, category):
    assert classify_llm_error(error) is category
    assert is_retryable_error(error) is True


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (_status_error(401), LLMErrorCategory.AUTHENTICATION),
        (_status_error(400), LLMErrorCategory.INVALID_REQUEST),
        (_status_error(404), LLMErrorCategory.INVALID_REQUEST),
        (_status_error(422), LLMErrorCategory.INVALID_REQUEST),
        (RuntimeError("unexpected response"), LLMErrorCategory.UNKNOWN),
    ],
)
def test_non_transient_errors_are_not_retryable(error, category):
    assert classify_llm_error(error) is category
    assert is_retryable_error(error) is False


@pytest.mark.parametrize(
    "message",
    [
        "unsupported parameter: max_tokens",
        "unknown parameter max_completion_tokens",
        "response_format json_object is unavailable",
        "extra_forbidden for max_tokens",
    ],
)
def test_parameter_incompatibility_is_separate_and_not_ordinary_retryable(message):
    error = _status_error(400, message)

    assert classify_llm_error(error) is LLMErrorCategory.PARAMETER_INCOMPATIBLE
    assert is_retryable_error(error) is False


def test_generic_422_without_parameter_marker_is_invalid_request():
    error = _status_error(422, "input was rejected")

    assert classify_llm_error(error) is LLMErrorCategory.INVALID_REQUEST
