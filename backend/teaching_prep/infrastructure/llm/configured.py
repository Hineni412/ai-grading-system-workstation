from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from api_profiles import ApiProfileStorageError, ApiProfileStore, resolve_profile_for_task
from backend.llm import (
    LLMGateway,
    create_openai_client,
    gateway_config_key,
    normalize_openai_base_url,
    policy_overrides_from_profile,
)
from backend.llm.trace import safe_endpoint_host
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.contracts import WorkspaceContext
from backend.workspaces.model_policy import WorkspaceModelGateway
from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway

from .lesson_model import WorkspaceLessonModelAdapter
from .exercise_suggestions import WorkspaceExerciseSuggestionModelAdapter
from .semester_mapping import WorkspaceSemesterMappingModelAdapter
from .slide_animation import WorkspaceSlideAnimationModelAdapter


@dataclass(frozen=True, slots=True)
class _ResolvedModel:
    api_key: str
    base_url: str
    model: str
    policy_profile: Mapping[str, object]


class _ActiveProfileRuntime:
    """Resolve the active machine-local model profile at action time."""

    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: ApiProfileStore,
    ) -> None:
        self.context = context
        self.profile_store = profile_store

    def is_available(self) -> bool:
        try:
            self._resolve()
        except (
            ApiProfileStorageError,
            OSError,
            TeachingPrepValidationError,
            ValueError,
        ):
            return False
        return True

    def lesson_adapter(
        self,
        task_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> WorkspaceLessonModelAdapter:
        resolved = self._resolve()
        gateway, client = self._gateway_and_client(resolved, task_gateway)
        return WorkspaceLessonModelAdapter(
            gateway=gateway,
            client=client,
            model=resolved.model,
        )

    def semester_mapping_adapter(
        self,
        task_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> WorkspaceSemesterMappingModelAdapter:
        resolved = self._resolve()
        gateway, client = self._gateway_and_client(resolved, task_gateway)
        return WorkspaceSemesterMappingModelAdapter(
            gateway=gateway,
            client=client,
            model=resolved.model,
        )

    def exercise_suggestion_adapter(
        self,
        task_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> WorkspaceExerciseSuggestionModelAdapter:
        resolved = self._resolve()
        gateway, client = self._gateway_and_client(resolved, task_gateway)
        return WorkspaceExerciseSuggestionModelAdapter(
            gateway=gateway,
            client=client,
            model=resolved.model,
        )

    def slide_animation_adapter(
        self,
        task_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> WorkspaceSlideAnimationModelAdapter:
        resolved = self._resolve()
        gateway, client = self._gateway_and_client(resolved, task_gateway)
        return WorkspaceSlideAnimationModelAdapter(
            gateway=gateway,
            client=client,
            model=resolved.model,
        )

    def _resolve(self) -> _ResolvedModel:
        profile = resolve_profile_for_task(self.profile_store, "teaching_prep")
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
            profile.get("teaching_prep_model"),
            profile.get("config_model"),
            profile.get("grading_model"),
            os.environ.get("LLM_CONFIG_MODEL"),
            os.environ.get("LLM_GRADING_MODEL"),
        )
        if not api_key or not model:
            raise TeachingPrepValidationError(
                "active teaching-prep model configuration is incomplete"
            )
        return _ResolvedModel(
            api_key=api_key,
            base_url=base_url,
            model=model,
            policy_profile=policy_overrides_from_profile(profile),
        )

    def _gateway_and_client(
        self,
        resolved: _ResolvedModel,
        task_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> tuple[object, object]:
        config_key = gateway_config_key(
            resolved.api_key,
            resolved.base_url,
        )
        diagnostic_gateway = LLMGateway(
            profile=resolved.policy_profile,
            config_key=config_key,
            endpoint_host=safe_endpoint_host(resolved.base_url),
        )
        workspace_gateway = WorkspaceModelGateway(
                context=self.context,
                profile=resolved.policy_profile,
                config_key=config_key,
                gateway=diagnostic_gateway,
                metadata_only=False,
                claim_operations=True,
                allow_retry=False,
            )
        return (
            task_gateway.bind(workspace_gateway) if task_gateway else workspace_gateway,
            create_openai_client(
                resolved.api_key,
                resolved.base_url,
            ),
        )


class ActiveProfileLessonModelAdapter:
    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: ApiProfileStore,
    ) -> None:
        self._runtime = _ActiveProfileRuntime(
            context=context,
            profile_store=profile_store,
        )

    def is_available(self) -> bool:
        return self._runtime.is_available()

    def generate(
        self,
        *,
        operation_id: str,
        resource_pack: dict[str, Any],
        task_model_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> dict[str, Any]:
        return self._runtime.lesson_adapter(task_model_gateway).generate(
            operation_id=operation_id,
            resource_pack=resource_pack,
        )


class ActiveProfileSemesterMappingModelAdapter:
    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: ApiProfileStore,
    ) -> None:
        self._runtime = _ActiveProfileRuntime(
            context=context,
            profile_store=profile_store,
        )

    def is_available(self) -> bool:
        return self._runtime.is_available()

    def generate(
        self,
        *,
        operation_id: str,
        semester_snapshot: dict[str, Any],
        dispatch_callback: Callable[[], None] | None = None,
        task_model_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> dict[str, Any]:
        return self._runtime.semester_mapping_adapter(task_model_gateway).generate(
            operation_id=operation_id,
            semester_snapshot=semester_snapshot,
            dispatch_callback=dispatch_callback,
        )


class ActiveProfileExerciseSuggestionModelAdapter:
    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: ApiProfileStore,
    ) -> None:
        self._runtime = _ActiveProfileRuntime(
            context=context,
            profile_store=profile_store,
        )

    def is_available(self) -> bool:
        return self._runtime.is_available()

    def generate(
        self,
        *,
        operation_id: str,
        reference_snapshot: dict[str, Any],
        task_model_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> dict[str, Any]:
        return self._runtime.exercise_suggestion_adapter(task_model_gateway).generate(
            operation_id=operation_id,
            reference_snapshot=reference_snapshot,
        )


class ActiveProfileSlideAnimationModelAdapter:
    def __init__(
        self,
        *,
        context: WorkspaceContext,
        profile_store: ApiProfileStore,
    ) -> None:
        self._runtime = _ActiveProfileRuntime(
            context=context,
            profile_store=profile_store,
        )

    def is_available(self) -> bool:
        return self._runtime.is_available()

    def generate(
        self,
        *,
        operation_id: str,
        page_payload: dict[str, Any],
        task_model_gateway: WorkspaceAITaskModelGateway | None = None,
    ) -> dict[str, Any]:
        return self._runtime.slide_animation_adapter(task_model_gateway).generate(
            operation_id=operation_id,
            page_payload=page_payload,
        )


def _first_text(*values: object) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


__all__ = [
    "ActiveProfileExerciseSuggestionModelAdapter",
    "ActiveProfileLessonModelAdapter",
    "ActiveProfileSemesterMappingModelAdapter",
    "ActiveProfileSlideAnimationModelAdapter",
]
