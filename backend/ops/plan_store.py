from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .models import OpsInternalPlan

CONFIRMATION_TTL_SECONDS = 300


class OpsConfirmationInvalid(ValueError):
    pass


class OpsConfirmationExpired(ValueError):
    pass


class OpsConfirmationUsed(ValueError):
    pass


class _Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class _SystemClock:
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _StoredPlan:
    plan: OpsInternalPlan
    expires_monotonic: float


class OpsPlanStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = CONFIRMATION_TTL_SECONDS,
        clock: _Clock | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if int(ttl_seconds) != CONFIRMATION_TTL_SECONDS:
            raise ValueError("Ops confirmation TTL must be 300 seconds")
        self._clock = clock or _SystemClock()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._plans: dict[str, _StoredPlan] = {}
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def issue(self, plan: OpsInternalPlan) -> tuple[str, datetime]:
        token = str(self._token_factory() or "")
        if not token:
            raise ValueError("confirmation token factory returned a blank token")
        digest = _token_digest(token)
        now_monotonic = self._clock.monotonic()
        expires_at = self._clock.utcnow() + timedelta(
            seconds=CONFIRMATION_TTL_SECONDS
        )
        with self._lock:
            if digest in self._plans or digest in self._used:
                raise ValueError("confirmation token factory returned a duplicate token")
            self._plans[digest] = _StoredPlan(
                plan=plan,
                expires_monotonic=now_monotonic + CONFIRMATION_TTL_SECONDS,
            )
        return token, expires_at

    def consume(self, token: str) -> OpsInternalPlan:
        clean_token = str(token or "").strip()
        if not clean_token:
            raise OpsConfirmationInvalid("confirmation token is invalid")
        digest = _token_digest(clean_token)
        with self._lock:
            if digest in self._used:
                raise OpsConfirmationUsed("confirmation token has already been used")
            stored = self._plans.pop(digest, None)
            if stored is None:
                raise OpsConfirmationInvalid("confirmation token is invalid")
            self._used.add(digest)
            if self._clock.monotonic() >= stored.expires_monotonic:
                raise OpsConfirmationExpired("confirmation token has expired")
            return stored.plan


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "CONFIRMATION_TTL_SECONDS",
    "OpsConfirmationExpired",
    "OpsConfirmationInvalid",
    "OpsConfirmationUsed",
    "OpsPlanStore",
]
