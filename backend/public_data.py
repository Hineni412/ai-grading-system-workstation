from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
    }
)


def canonical_public_key(value: object) -> str:
    return "".join(
        character
        for character in str(value or "").casefold()
        if character.isalnum()
    )


def is_sensitive_public_key(value: object) -> bool:
    key = canonical_public_key(value)
    return (
        key in _SENSITIVE_KEYS
        or key.endswith("apikey")
        or key.endswith("secret")
        or key.endswith("token")
    )


def contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_public_key(key) or contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_key(item) for item in value)
    return False
