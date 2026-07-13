from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Callable, Mapping

from .errors import classify_llm_error, is_retryable_error
from .pacing import LLMPacerRegistry
from .policy import LLMProtocol, LLMRequestKind, policy_from_profile
from .usage import LLMUsageEvent, NullUsageSink, usage_fields


logger = logging.getLogger(__name__)
_DEFAULT_PACERS = LLMPacerRegistry()


def _warn_safely(message: str) -> None:
    try:
        logger.warning(message)
    except Exception:
        return


class LLMGateway:
    def __init__(
        self,
        *,
        profile: Mapping[str, object] | None = None,
        config_key: str = "default",
        pacers: object | None = None,
        usage_sink: object | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = dict(profile or {})
        self.config_key = str(config_key)
        self.pacers = pacers if pacers is not None else _DEFAULT_PACERS
        self.usage_sink = usage_sink if usage_sink is not None else NullUsageSink()
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
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.CHAT_COMPLETIONS,
            request_kind=request_kind,
            client=client,
            model=model,
            kwargs=kwargs,
            request_id=request_id,
            allow_retry=allow_retry,
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
    ) -> object:
        return self._execute(
            protocol=LLMProtocol.RESPONSES,
            request_kind=request_kind,
            client=client,
            model=model,
            kwargs=kwargs,
            request_id=request_id,
            allow_retry=allow_retry,
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
    ) -> object:
        kind = LLMRequestKind(request_kind)
        logical_request_id = str(
            uuid.uuid4() if request_id is None else request_id
        )
        policy = policy_from_profile(kind, self.profile)
        retry_limit = policy.max_retries if allow_retry else 0

        for attempt in range(1, retry_limit + 2):
            self.pacers.acquire(
                self.config_key,
                kind,
                policy.requests_per_minute,
            )
            started = self.clock()
            try:
                payload = dict(kwargs)
                payload["model"] = model
                payload["timeout"] = policy.timeout_seconds
                response = self._invoke(protocol, client, payload)
            except Exception as exc:
                latency_ms = self._latency_ms(started)
                self._record_failure(
                    request_id=logical_request_id,
                    attempt=attempt,
                    request_kind=kind,
                    protocol=protocol,
                    model=model,
                    latency_ms=latency_ms,
                    error=exc,
                )
                if attempt > retry_limit or not is_retryable_error(exc):
                    raise
                deterministic_delay = policy.retry_delays[attempt - 1]
                self.sleeper(self._retry_delay(exc, deterministic_delay))
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
    ) -> None:
        try:
            normalized_usage = usage_fields(response)
            event = LLMUsageEvent(
                request_id=request_id,
                attempt=attempt,
                request_kind=request_kind.value,
                protocol=protocol.value,
                model=str(model),
                latency_ms=latency_ms,
                success=True,
                **normalized_usage,
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
            )
        self._safe_write(event)

    def _safe_write(self, event: LLMUsageEvent) -> None:
        try:
            self.usage_sink.write(event)
        except Exception:
            _warn_safely("Failed to record LLM usage metadata")
