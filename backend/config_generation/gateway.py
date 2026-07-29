from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, Sequence


DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS = 240.0


class ConfigGenerationGateway(Protocol):
    """The only model-request surface used by config orchestration."""

    @property
    def max_parallel_requests(self) -> int: ...

    def request_text(self, prompt: str) -> dict[str, Any]: ...

    def request_images(
        self,
        prompt: str,
        image_blobs: Sequence[bytes],
    ) -> dict[str, Any]: ...


def config_generation_extra_kwargs() -> dict[str, float]:
    raw_timeout = os.getenv("AI_GRADING_CONFIG_TIMEOUT_SECONDS")
    try:
        timeout = (
            float(raw_timeout)
            if raw_timeout
            else DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS
        )
    except (TypeError, ValueError):
        timeout = DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS
    resolved_timeout = min(600.0, max(240.0, timeout))
    # The shared gateway derives its final deadline from the request policy.
    # Pass the explicit override as well as the SDK option so config generation
    # cannot silently fall back to a shorter profile timeout.
    return {
        "timeout": resolved_timeout,
        "timeout_override_seconds": resolved_timeout,
    }


class LLMConfigGenerationGateway:
    """Adapt the shared LLM client without adding retries or fallback calls."""

    def __init__(
        self,
        client: Any,
        *,
        model_name: str | None,
        extra_kwargs: Mapping[str, Any],
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._extra_kwargs = dict(extra_kwargs)

    @property
    def max_parallel_requests(self) -> int:
        gateway = getattr(self._client, "config_gateway", None)
        snapshot = getattr(gateway, "execution_snapshot", None)
        value = getattr(snapshot, "max_in_flight", 1)
        try:
            return max(1, min(100, int(value)))
        except (TypeError, ValueError):
            return 1

    def request_text(self, prompt: str) -> dict[str, Any]:
        return self._client.json_from_text_once(
            prompt,
            model=self._model_name,
            extra_kwargs=dict(self._extra_kwargs),
        )

    def request_images(
        self,
        prompt: str,
        image_blobs: Sequence[bytes],
    ) -> dict[str, Any]:
        return self._client.json_from_images_once(
            prompt,
            list(image_blobs),
            model=self._model_name,
            extra_kwargs=dict(self._extra_kwargs),
            use_config_client=True,
        )
