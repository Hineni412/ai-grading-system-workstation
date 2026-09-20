from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


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
