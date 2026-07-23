from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, Sequence


DEFAULT_CONFIG_GENERATION_TIMEOUT_SECONDS = 600.0


class ConfigGenerationGateway(Protocol):
    """The only model-request surface used by config orchestration."""

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
    return {"timeout": max(120.0, timeout)}


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
