from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from api_profiles import ApiProfileStorageError, ApiProfileStore, active_api_profile
from backend.llm import (
    create_openai_client,
    gateway_config_key,
    normalize_openai_base_url,
    policy_overrides_from_profile,
)
from backend.workspaces.contracts import WorkspaceContext
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)

from .crypto import canonical_json
from .model_approval import ModelDestinationChanged, ModelDispatchDisabled


class _ProfileStore(Protocol):
    def load(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class _ResolvedModel:
    api_key: str
    base_url: str
    model: str
    policy_profile: Mapping[str, object]


class ActiveProfileApprovedModelGateway:
    """Resolve the current machine-local model only after teacher approval.

    Construction and preview are network-free.  ``invoke`` reuses the shared
    workspace gateway, which claims the operation durably and forces exactly
    zero automatic retries.  The only model message is the canonical form of
    the payload already displayed to the teacher.
    """

    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: _ProfileStore,
        gateway_factory: Callable[..., object] = WorkspaceModelGateway,
        client_factory: Callable[[str, str], object] = create_openai_client,
    ) -> None:
        self.context = context
        self.profile_store = profile_store
        self.gateway_factory = gateway_factory
        self.client_factory = client_factory

    @property
    def model_name(self) -> str:
        return str(self.destination_snapshot()["model"] or "未启用真实模型")

    def is_available(self) -> bool:
        return bool(self.destination_snapshot()["available"])

    def destination_snapshot(self) -> dict[str, object]:
        """Return only the public destination identity; never credentials."""

        try:
            resolved = self._resolve()
        except ModelDispatchDisabled:
            return _destination_snapshot(None)
        return _destination_snapshot(resolved)

    def invoke(
        self,
        *,
        payload: dict[str, object],
        operation_id: str,
        purpose: str = "student_support_note",
        data_classification: str = "restricted_anonymized",
        expected_destination_fingerprint: str | None = None,
    ) -> str:
        try:
            resolved = self._resolve()
        except ModelDispatchDisabled:
            current = _destination_snapshot(None)
            if (
                expected_destination_fingerprint is not None
                and current["destination_fingerprint"]
                != expected_destination_fingerprint
            ):
                raise ModelDestinationChanged(
                    "model destination changed after preview"
                )
            raise
        current = _destination_snapshot(resolved)
        if (
            expected_destination_fingerprint is not None
            and current["destination_fingerprint"] != expected_destination_fingerprint
        ):
            raise ModelDestinationChanged("model destination changed after preview")
        gateway = self.gateway_factory(
            context=self.context,
            profile=resolved.policy_profile,
            config_key=gateway_config_key(resolved.api_key, resolved.base_url),
        )
        client = self.client_factory(resolved.api_key, resolved.base_url)
        request = getattr(gateway, "chat_completions", None)
        if not callable(request):
            raise ModelDispatchDisabled("workspace model gateway is unavailable")
        response = request(
            request=WorkspaceModelRequest(
                purpose=purpose,
                data_classification=data_classification,
                operation_id=operation_id,
            ),
            client=client,
            model=resolved.model,
            kwargs={
                "messages": [
                    {
                        "role": "user",
                        "content": canonical_json(payload).decode("utf-8"),
                    }
                ],
                "response_format": {"type": "json_object"},
            },
            timeout_override_seconds=110,
        )
        return _response_text(response)

    def _resolve(self) -> _ResolvedModel:
        try:
            profile = active_api_profile(self.profile_store.load())
            api_key = _first_text(
                profile.get("config_api_key"),
                profile.get("api_key"),
                os.environ.get("LLM_CONFIG_API_KEY"),
                os.environ.get("LLM_API_KEY"),
            )
            base_url = normalize_openai_base_url(
                _first_text(
                    profile.get("config_base_url"),
                    profile.get("base_url"),
                    os.environ.get("LLM_CONFIG_BASE_URL"),
                    os.environ.get("LLM_BASE_URL"),
                    "https://api.openai.com/v1",
                )
            )
            model = _first_text(
                profile.get("class_teacher_model"),
                profile.get("config_model"),
                profile.get("grading_model"),
                os.environ.get("LLM_CONFIG_MODEL"),
                os.environ.get("LLM_GRADING_MODEL"),
            )
            if not api_key or not model:
                raise ModelDispatchDisabled("active model configuration is incomplete")
            return _ResolvedModel(
                api_key=api_key,
                base_url=base_url,
                model=model,
                policy_profile=policy_overrides_from_profile(profile),
            )
        except ModelDispatchDisabled:
            raise
        except (ApiProfileStorageError, OSError, TypeError, ValueError) as exc:
            raise ModelDispatchDisabled(
                "active model configuration is unavailable"
            ) from exc


def create_active_profile_model_gateway(
    context: WorkspaceContext,
) -> ActiveProfileApprovedModelGateway:
    return ActiveProfileApprovedModelGateway(
        context=context,
        profile_store=ApiProfileStore(
            context.paths.api_profiles_path,
            legacy_paths=tuple(
                item
                for item in getattr(context.paths, "legacy_api_profiles_paths", ())
            ),
        ),
    )


def _first_text(*values: object) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def _safe_endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _destination_snapshot(resolved: _ResolvedModel | None) -> dict[str, object]:
    if resolved is None:
        identity: dict[str, object] = {
            "available": False,
            "model_provider": None,
            "model_endpoint": None,
            "model": None,
        }
    else:
        endpoint = _safe_endpoint(resolved.base_url)
        identity = {
            "available": True,
            "model_provider": urlsplit(endpoint).hostname,
            "model_endpoint": endpoint,
            "model": resolved.model,
        }
    identity["destination_fingerprint"] = hashlib.sha256(
        canonical_json(identity)
    ).hexdigest()
    return identity


def _response_text(response: object) -> str:
    if isinstance(response, Mapping):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    raise RuntimeError("class-teacher model response text is unavailable")


__all__ = [
    "ActiveProfileApprovedModelGateway",
    "create_active_profile_model_gateway",
]
