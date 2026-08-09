from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from api_profiles import ApiProfileStorageError, ApiProfileStore, resolve_profile_for_task
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
    """Resolve the current machine-local model at invocation time.

    Construction is network-free. Every teacher action gets at most one
    physical model request and writes complete text diagnostics only to the
    bounded local journal. Restricted student content reaches this seam only
    after the separate destination-preview confirmation flow.
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
        self._request_counts: dict[str, int] = {}
        self._request_counts_lock = threading.Lock()

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

    def audio_input_capabilities(self) -> dict[str, object]:
        """Report whether a model is configured; the provider owns modality support."""

        try:
            resolved = self._resolve()
        except ModelDispatchDisabled:
            empty = _destination_snapshot(None)
            return {
                "available": False,
                "status": "profile_missing",
                "provider": "configured_model",
                "model": None,
                "destination_fingerprint": empty["destination_fingerprint"],
            }
        return self._audio_capability_for(resolved)

    @staticmethod
    def _audio_capability_for(resolved: _ResolvedModel) -> dict[str, object]:
        snapshot = _destination_snapshot(resolved)
        return {
            "available": True,
            "status": "ready",
            "provider": "configured_model",
            "model": resolved.model,
            "destination_fingerprint": snapshot["destination_fingerprint"],
        }

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
            metadata_only=False,
            claim_operations=False,
            allow_retry=False,
        )
        _tag_class_teacher_diagnostics(gateway, task_kind=purpose)
        client = self.client_factory(resolved.api_key, resolved.base_url)
        request = getattr(gateway, "chat_completions", None)
        if not callable(request):
            raise ModelDispatchDisabled("workspace model gateway is unavailable")
        try:
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
        finally:
            count = max(1, int(getattr(gateway, "physical_request_count", 0) or 0))
            with self._request_counts_lock:
                self._request_counts[operation_id] = count
        return _response_text(response)

    def physical_request_count(self, operation_id: str) -> int:
        with self._request_counts_lock:
            return int(self._request_counts.get(str(operation_id), 0))

    def invoke_workspace_task(
        self,
        *,
        task_gateway,
        messages: tuple[dict[str, str], ...],
        operation_id: str,
        purpose: str,
        expected_destination_fingerprint: str,
    ) -> str:
        """Call through the local-diagnostic, zero-retry shared Task gateway."""

        resolved = self._resolve()
        current = _destination_snapshot(resolved)
        if current["destination_fingerprint"] != expected_destination_fingerprint:
            raise ModelDestinationChanged("model destination changed after prepare")
        gateway = self.gateway_factory(
            context=self.context,
            profile=resolved.policy_profile,
            config_key=gateway_config_key(resolved.api_key, resolved.base_url),
            metadata_only=False,
            claim_operations=False,
            allow_retry=False,
        )
        client = self.client_factory(resolved.api_key, resolved.base_url)
        try:
            response = task_gateway.chat_completions(
                gateway=gateway,
                request=WorkspaceModelRequest(
                    purpose=purpose,
                    data_classification="restricted",
                    operation_id=operation_id,
                ),
                client=client,
                model=resolved.model,
                kwargs={
                    "messages": list(messages),
                    "response_format": {"type": "json_object"},
                },
                timeout_override_seconds=110,
            )
        finally:
            count = max(1, int(getattr(gateway, "physical_request_count", 0) or 0))
            with self._request_counts_lock:
                self._request_counts[operation_id] = count
        return _response_text(response)

    def invoke_workspace_audio(
        self,
        *,
        messages: tuple[dict[str, object], ...],
        operation_id: str,
        expected_destination_fingerprint: str,
    ) -> str:
        """Send one in-memory WAV to the configured model, without retry."""

        resolved = self._resolve()
        capability = self._audio_capability_for(resolved)
        if not capability["available"]:
            raise ModelDispatchDisabled(
                "active model does not support class-teacher audio input"
            )
        if capability["destination_fingerprint"] != expected_destination_fingerprint:
            raise ModelDestinationChanged(
                "model destination changed before audio dispatch"
            )
        gateway = self.gateway_factory(
            context=self.context,
            profile=resolved.policy_profile,
            config_key=gateway_config_key(resolved.api_key, resolved.base_url),
            metadata_only=True,
            claim_operations=True,
            allow_retry=False,
        )
        client = self.client_factory(resolved.api_key, resolved.base_url)
        try:
            response = gateway.chat_completions(
                request=WorkspaceModelRequest(
                    purpose="class_teacher_audio_intake",
                    data_classification="restricted_audio",
                    operation_id=operation_id,
                ),
                client=client,
                model=resolved.model,
                kwargs={
                    "messages": list(messages),
                    "response_format": {"type": "json_object"},
                },
                timeout_override_seconds=110,
            )
        finally:
            count = max(
                0,
                int(getattr(gateway, "physical_request_count", 0) or 0),
            )
            with self._request_counts_lock:
                self._request_counts[operation_id] = count
        return _response_text(response)

    def _resolve(self) -> _ResolvedModel:
        try:
            profile = resolve_profile_for_task(self.profile_store, "class_teacher")
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


def _tag_class_teacher_diagnostics(
    gateway: object,
    *,
    task_kind: str,
) -> None:
    if not isinstance(gateway, WorkspaceModelGateway):
        return
    diagnostic_sink = getattr(gateway.gateway, "diagnostic_sink", None)
    for_workspace = getattr(diagnostic_sink, "for_workspace", None)
    if not callable(for_workspace):
        return
    gateway.gateway.diagnostic_sink = for_workspace(
        workspace_module="class_teacher",
        workspace_task_kind=task_kind,
    )


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
