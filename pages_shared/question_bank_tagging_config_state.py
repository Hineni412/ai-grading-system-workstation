from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any


REQUIRED_TAGGING_FIELDS = (
    "tagging_api_key",
    "tagging_base_url",
    "tagging_model",
)


def replace_tagging_config_state(
    state: MutableMapping[str, Any],
    profile: Mapping[str, Any],
) -> None:
    """Replace stale page state with the current machine-local profile."""
    base_url = str(profile.get("tagging_base_url") or "").strip()
    state.update(
        {
            "tagging_api_key_input": str(
                profile.get("tagging_api_key") or ""
            ).strip(),
            "tagging_base_url_input": base_url,
            "tagging_model_input": str(
                profile.get("tagging_model") or ""
            ).strip(),
            "tagging_max_workers_input": _positive_int(
                profile.get("tagging_max_workers"), 4
            ),
            "tagging_requests_per_minute_input": _positive_int(
                profile.get("tagging_requests_per_minute"), 1000
            ),
            "tagging_thinking_input": bool(
                profile.get("tagging_thinking", False)
            ),
            "tagging_review_enabled_input": bool(
                profile.get("tagging_review_enabled", False)
            ),
            "tagging_review_api_key_input": str(
                profile.get("tagging_review_api_key") or ""
            ).strip(),
            "tagging_review_base_url_input": str(
                profile.get("tagging_review_base_url") or base_url
            ).strip(),
            "tagging_review_model_input": str(
                profile.get("tagging_review_model") or ""
            ).strip(),
            "tagging_enabled_input": True,
            "tagging_enabled": True,
        }
    )


def missing_tagging_config_fields(
    profile: Mapping[str, Any],
) -> tuple[str, ...]:
    return tuple(
        field
        for field in REQUIRED_TAGGING_FIELDS
        if not str(profile.get(field) or "").strip()
    )


def tagging_runtime_limits(profile: Mapping[str, Any]) -> tuple[int, int]:
    return (
        _positive_int(profile.get("tagging_max_workers"), 4),
        _positive_int(profile.get("tagging_requests_per_minute"), 1000),
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default
