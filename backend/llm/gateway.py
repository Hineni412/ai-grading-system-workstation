from __future__ import annotations

import logging
import math
import time
import uuid
from itertools import count
from typing import Callable, Mapping

from .errors import LLMErrorCategory, classify_llm_error, is_retryable_error
from .pacing import LLMPacerRegistry
from .policy import LLMPolicyError, LLMProtocol, LLMRequestKind, policy_from_profile
from .usage import (
    LLMUsageEvent,
    NullUsageSink,
    response_content_bytes,
    response_diagnostics,
    usage_fields,
)
from .trace import (
    actual_model_label,
    error_diagnostics,
    LLMCallTraceEvent,
    NullCallTraceSink,
    provider_request_id,
    request_diagnostics,
    safe_host_label,
    safe_trace_label,
    utc_timestamp,
)


logger = logging.getLogger(__name__)
_DEFAULT_PACERS = LLMPacerRegistry()
_COMPATIBILITY_FALLBACKS = frozenset(
    {"max_completion_tokens", "response_format"}
)


def _warn_safely(message: str) -> None:
    try:
        logger.warning(message)
    except Exception:
        return


def _resolved_timeout_seconds(
    policy_timeout_seconds: float,
    timeout_override_seconds: float | None,
) -> float:
    if timeout_override_seconds is None:
        return float(policy_timeout_seconds)
    if isinstance(timeout_override_seconds, bool) or not isinstance(
        timeout_override_seconds,
        (int, float),
    ):
        raise LLMPolicyError(
            "timeout_override_seconds must be a finite number"
        )
    parsed = float(timeout_override_seconds)
    if not math.isfinite(parsed) or not 1.0 <= parsed <= 600.0:
        raise LLMPolicyError(
            "timeout_override_seconds must be between 1.0 and 600.0"
        )
    return parsed


class LLMGateway:
    def __init__(
        self,
        *,
        profile: Mapping[str, object] | None = None,
        config_key: str = "default",
        pacers: object | None = None,
        usage_sink: object | None = None,
        trace_sink: object | None = None,
        endpoint_host: str = "",
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = dict(profile or {})
        self.config_key = str(config_key)
        self.pacers = pacers if pacers is not None else _DEFAULT_PACERS
        self.usage_sink = usage_sink if usage_sink is not None else NullUsageSink()
        self.trace_sink = (
            trace_sink if trace_sink is not None else NullCallTraceSink()
        )
        self.endpoint_host = safe_host_label(endpoint_host)
        self.clock = clock
        self.sleeper = sleeper

    def chat_completions(
        self,
        *,
        request_kind: LLMRequestKind,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        request_id: object | None = None,
        allow_retry: bool = True,
        compatibility_fallback: str = "",
        planned_parameter_fallback: bool = False,
        timeout_override_seconds: float | None = None,
        _next_attempt: Callable[[], int] | None = None,
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.CHAT_COMPLETIONS,
            request_kind=request_kind,
            client=client,
            model=model,
            kwargs=kwargs,
            request_id=request_id,
            allow_retry=allow_retry,
            compatibility_fallback=compatibility_fallback,
            planned_parameter_fallback=planned_parameter_fallback,
            timeout_override_seconds=timeout_override_seconds,
            next_attempt=_next_attempt,
        )

    def responses(
        self,
        *,
        request_kind: LLMRequestKind,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        request_id: object | None = None,
        allow_retry: bool = True,
        compatibility_fallback: str = "",
        planned_parameter_fallback: bool = False,
        timeout_override_seconds: float | None = None,
        _next_attempt: Callable[[], int] | None = None,
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.RESPONSES,
            request_kind=request_kind,
            client=client,
            model=model,
            kwargs=kwargs,
            request_id=request_id,
            allow_retry=allow_retry,
            compatibility_fallback=compatibility_fallback,
            planned_parameter_fallback=planned_parameter_fallback,
            timeout_override_seconds=timeout_override_seconds,
            next_attempt=_next_attempt,
        )

    def _execute(
        self,
        *,
        protocol: LLMProtocol,
        request_kind: LLMRequestKind,
        client: object,
        model: str,
        kwargs: Mapping[str, object],
        request_id: object | None,
        allow_retry: bool,
        compatibility_fallback: str,
        planned_parameter_fallback: bool,
        timeout_override_seconds: float | None,
        next_attempt: Callable[[], int] | None,
    ) -> object:
        kind = LLMRequestKind(request_kind)
        logical_request_id = str(
            uuid.uuid4() if request_id is None else request_id
        )
        policy = policy_from_profile(kind, self.profile)
        retry_limit = policy.max_retries if allow_retry else 0
        fallback_candidate = str(compatibility_fallback or "")
        fallback = (
            fallback_candidate
            if fallback_candidate in _COMPATIBILITY_FALLBACKS
            else ""
        )
        attempt_counter = next_attempt or count(1).__next__
        request_timeout_seconds = _resolved_timeout_seconds(
            policy.timeout_seconds,
            timeout_override_seconds,
        )
        try:
            request_shape = request_diagnostics(kwargs)
        except Exception:
            _warn_safely("Failed to normalize LLM request trace metadata")
            request_shape = {
                "request_bytes_estimate": 0,
                "text_chars": 0,
                "image_count": 0,
                "image_bytes_estimate": 0,
            }

        for retry_index in range(retry_limit + 1):
            attempt = attempt_counter()
            pacing_started = self.clock()
            self.pacers.acquire(
                self.config_key,
                kind,
                policy.requests_per_minute,
            )
            pacer_wait_ms = self._latency_ms(pacing_started)
            payload = dict(kwargs)
            payload["model"] = model
            payload["timeout"] = request_timeout_seconds
            self._record_trace_started(
                request_id=logical_request_id,
                attempt=attempt,
                request_kind=kind,
                protocol=protocol,
                model=model,
                timeout_seconds=request_timeout_seconds,
                retry_limit=retry_limit,
                retry_index=retry_index,
                pacer_wait_ms=pacer_wait_ms,
                request_shape=request_shape,
            )
            started = self.clock()
            try:
                response = self._invoke(protocol, client, payload)
            except Exception as exc:
                latency_ms = self._latency_ms(started)
                should_retry = (
                    retry_index < retry_limit and is_retryable_error(exc)
                )
                retry_delay = (
                    self._retry_delay(exc, policy.retry_delays[retry_index])
                    if should_retry
                    else 0.0
                )
                try:
                    will_use_parameter_fallback = bool(
                        planned_parameter_fallback
                        and classify_llm_error(exc)
                        is LLMErrorCategory.PARAMETER_INCOMPATIBLE
                    )
                except Exception:
                    will_use_parameter_fallback = False
                self._record_failure(
                    request_id=logical_request_id,
                    attempt=attempt,
                    request_kind=kind,
                    protocol=protocol,
                    model=model,
                    latency_ms=latency_ms,
                    error=exc,
                    compatibility_fallback=fallback,
                )
                self._record_trace_failure(
                    request_id=logical_request_id,
                    attempt=attempt,
                    request_kind=kind,
                    protocol=protocol,
                    model=model,
                    timeout_seconds=request_timeout_seconds,
                    retry_limit=retry_limit,
                    retry_index=retry_index,
                    pacer_wait_ms=pacer_wait_ms,
                    request_shape=request_shape,
                    latency_ms=latency_ms,
                    error=exc,
                    will_retry=(
                        should_retry or will_use_parameter_fallback
                    ),
                    retry_delay=retry_delay,
                )
                if not should_retry:
                    raise
                self.sleeper(retry_delay)
                continue

            latency_ms = self._latency_ms(started)
            self._record_success(
                request_id=logical_request_id,
                attempt=attempt,
                request_kind=kind,
                protocol=protocol,
                model=model,
                latency_ms=latency_ms,
                response=response,
                compatibility_fallback=fallback,
            )
            self._record_trace_success(
                request_id=logical_request_id,
                attempt=attempt,
                request_kind=kind,
                protocol=protocol,
                model=model,
                timeout_seconds=request_timeout_seconds,
                retry_limit=retry_limit,
                retry_index=retry_index,
                pacer_wait_ms=pacer_wait_ms,
                request_shape=request_shape,
                latency_ms=latency_ms,
                response=response,
            )
            return response

        raise RuntimeError("unreachable LLM gateway execution state")

    @staticmethod
    def _invoke(
        protocol: LLMProtocol,
        client: object,
        payload: Mapping[str, object],
    ) -> object:
        if protocol is LLMProtocol.CHAT_COMPLETIONS:
            return client.chat.completions.create(**payload)
        if protocol is LLMProtocol.RESPONSES:
            return client.responses.create(**payload)
        raise ValueError(f"unsupported LLM protocol: {protocol}")

    def _latency_ms(self, started: float) -> int:
        elapsed = max(0.0, self.clock() - started)
        return int(round(elapsed * 1000.0))

    @staticmethod
    def _retry_delay(error: BaseException, deterministic_delay: float) -> float:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        raw_value = None
        if headers is not None:
            try:
                raw_value = headers.get("retry-after")
            except (AttributeError, TypeError):
                raw_value = None
            if raw_value is None:
                try:
                    raw_value = next(
                        value
                        for key, value in headers.items()
                        if str(key).lower() == "retry-after"
                    )
                except (AttributeError, StopIteration, TypeError):
                    raw_value = None
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            return deterministic_delay
        if not math.isfinite(parsed) or parsed < 0:
            return deterministic_delay
        return min(parsed, deterministic_delay)

    def _record_failure(
        self,
        *,
        request_id: str,
        attempt: int,
        request_kind: LLMRequestKind,
        protocol: LLMProtocol,
        model: str,
        latency_ms: int,
        error: BaseException,
        compatibility_fallback: str,
    ) -> None:
        category = classify_llm_error(error)
        self._safe_write(
            LLMUsageEvent(
                request_id=request_id,
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=str(model),
                latency_ms=latency_ms,
                success=False,
                error_category=category.value,
                compatibility_fallback=compatibility_fallback,
            )
        )

    def _record_success(
        self,
        *,
        request_id: str,
        attempt: int,
        request_kind: LLMRequestKind,
        protocol: LLMProtocol,
        model: str,
        latency_ms: int,
        response: object,
        compatibility_fallback: str,
    ) -> None:
        try:
            normalized_usage = usage_fields(response)
            diagnostics = response_diagnostics(response)
            event = LLMUsageEvent(
                request_id=request_id,
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=str(model),
                latency_ms=latency_ms,
                success=True,
                compatibility_fallback=compatibility_fallback,
                **normalized_usage,
                **diagnostics,
            )
        except Exception:
            _warn_safely("Failed to normalize LLM usage metadata")
            event = LLMUsageEvent(
                request_id=request_id,
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=str(model),
                latency_ms=latency_ms,
                success=True,
                compatibility_fallback=compatibility_fallback,
            )
        self._safe_write(event)

    def _safe_write(self, event: LLMUsageEvent) -> None:
        try:
            self.usage_sink.write(event)
        except Exception:
            _warn_safely("Failed to record LLM usage metadata")

    def _record_trace_started(
        self,
        *,
        request_id: str,
        attempt: int,
        request_kind: LLMRequestKind,
        protocol: LLMProtocol,
        model: str,
        timeout_seconds: float,
        retry_limit: int,
        retry_index: int,
        pacer_wait_ms: int,
        request_shape: Mapping[str, int],
    ) -> None:
        try:
            event = LLMCallTraceEvent(
                event_type="request_started",
                timestamp_utc=utc_timestamp(),
                request_id=safe_trace_label(request_id),
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=safe_trace_label(model),
                endpoint_host=self.endpoint_host,
                timeout_seconds=timeout_seconds,
                retry_limit=retry_limit,
                retry_index=retry_index,
                pacer_wait_ms=pacer_wait_ms,
                **request_shape,
            )
        except Exception:
            _warn_safely("Failed to normalize LLM call trace metadata")
            return
        self._safe_trace(event)

    def _record_trace_success(
        self,
        *,
        request_id: str,
        attempt: int,
        request_kind: LLMRequestKind,
        protocol: LLMProtocol,
        model: str,
        timeout_seconds: float,
        retry_limit: int,
        retry_index: int,
        pacer_wait_ms: int,
        request_shape: Mapping[str, int],
        latency_ms: int,
        response: object,
    ) -> None:
        try:
            normalized_usage = usage_fields(response)
            diagnostics = response_diagnostics(response)
            actual_model = actual_model_label(response, model)
            event = LLMCallTraceEvent(
                event_type="request_succeeded",
                timestamp_utc=utc_timestamp(),
                request_id=safe_trace_label(request_id),
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=safe_trace_label(model),
                endpoint_host=self.endpoint_host,
                timeout_seconds=timeout_seconds,
                retry_limit=retry_limit,
                retry_index=retry_index,
                pacer_wait_ms=pacer_wait_ms,
                elapsed_ms=latency_ms,
                outcome="success",
                response_bytes_estimate=response_content_bytes(response),
                actual_model=actual_model,
                provider_request_id=provider_request_id(response),
                **request_shape,
                **normalized_usage,
                **diagnostics,
            )
        except Exception:
            _warn_safely("Failed to normalize LLM call trace metadata")
            try:
                event = LLMCallTraceEvent(
                    event_type="request_succeeded",
                    timestamp_utc=utc_timestamp(),
                    request_id=safe_trace_label(request_id),
                    attempt=attempt,
                    request_kind=request_kind.value,
                    protocol=protocol.value,
                    model=safe_trace_label(model),
                    endpoint_host=self.endpoint_host,
                    timeout_seconds=timeout_seconds,
                    retry_limit=retry_limit,
                    retry_index=retry_index,
                    pacer_wait_ms=pacer_wait_ms,
                    elapsed_ms=latency_ms,
                    outcome="success",
                    **request_shape,
                )
            except Exception:
                _warn_safely("Failed to normalize LLM call trace metadata")
                return
        self._safe_trace(event)

    def _record_trace_failure(
        self,
        *,
        request_id: str,
        attempt: int,
        request_kind: LLMRequestKind,
        protocol: LLMProtocol,
        model: str,
        timeout_seconds: float,
        retry_limit: int,
        retry_index: int,
        pacer_wait_ms: int,
        request_shape: Mapping[str, int],
        latency_ms: int,
        error: BaseException,
        will_retry: bool,
        retry_delay: float,
    ) -> None:
        try:
            error_category = classify_llm_error(error).value
        except Exception:
            _warn_safely("Failed to normalize LLM call trace metadata")
            error_category = "unknown"
        try:
            diagnostics = error_diagnostics(error)
        except Exception:
            _warn_safely("Failed to normalize LLM call trace metadata")
            diagnostics = {
                "http_status_code": 0,
                "exception_type": "other",
                "network_phase": "unknown",
                "provider_request_id": "",
            }
        try:
            event = LLMCallTraceEvent(
                event_type="request_failed",
                timestamp_utc=utc_timestamp(),
                request_id=safe_trace_label(request_id),
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=safe_trace_label(model),
                endpoint_host=self.endpoint_host,
                timeout_seconds=timeout_seconds,
                retry_limit=retry_limit,
                retry_index=retry_index,
                pacer_wait_ms=pacer_wait_ms,
                elapsed_ms=latency_ms,
                outcome="failure",
                error_category=error_category,
                will_retry=will_retry,
                retry_delay_ms=int(round(max(0.0, retry_delay) * 1000.0)),
                **request_shape,
                **diagnostics,
            )
        except Exception:
            _warn_safely("Failed to normalize LLM call trace metadata")
            return
        self._safe_trace(event)

    def _safe_trace(self, event: LLMCallTraceEvent) -> None:
        try:
            self.trace_sink.write(event)
        except Exception:
            _warn_safely("Failed to record LLM call trace metadata")
