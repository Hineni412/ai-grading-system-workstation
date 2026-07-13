from __future__ import annotations

from enum import Enum

import openai


class LLMErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    SERVER_TRANSIENT = "server_transient"
    PARAMETER_INCOMPATIBLE = "parameter_incompatible"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"


_PARAMETER_MARKERS = (
    "max_tokens",
    "max_completion_tokens",
    "response_format",
    "json_object",
    "unsupported parameter",
    "unknown parameter",
    "unrecognized request argument",
    "extra_forbidden",
)
_RETRYABLE_CATEGORIES = frozenset(
    {
        LLMErrorCategory.TIMEOUT,
        LLMErrorCategory.CONNECTION,
        LLMErrorCategory.RATE_LIMIT,
        LLMErrorCategory.SERVER_TRANSIENT,
    }
)


def _status_code(error: BaseException) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(error, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def classify_llm_error(error: BaseException) -> LLMErrorCategory:
    if isinstance(error, (openai.APITimeoutError, TimeoutError)):
        return LLMErrorCategory.TIMEOUT
    if isinstance(error, (openai.APIConnectionError, ConnectionError)):
        return LLMErrorCategory.CONNECTION
    if isinstance(error, openai.RateLimitError):
        return LLMErrorCategory.RATE_LIMIT
    if isinstance(error, openai.AuthenticationError):
        return LLMErrorCategory.AUTHENTICATION

    status_code = _status_code(error)
    if status_code == 429:
        return LLMErrorCategory.RATE_LIMIT
    if status_code in {500, 502, 503, 504}:
        return LLMErrorCategory.SERVER_TRANSIENT
    if status_code in {401, 403}:
        return LLMErrorCategory.AUTHENTICATION

    message = str(error).lower()
    if any(marker in message for marker in _PARAMETER_MARKERS):
        return LLMErrorCategory.PARAMETER_INCOMPATIBLE
    if status_code is not None and 400 <= status_code < 500:
        return LLMErrorCategory.INVALID_REQUEST
    return LLMErrorCategory.UNKNOWN


def is_retryable_error(error: BaseException) -> bool:
    return classify_llm_error(error) in _RETRYABLE_CATEGORIES
