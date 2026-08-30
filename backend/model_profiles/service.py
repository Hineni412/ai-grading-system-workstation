from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from api_profiles import ApiProfileStore, PROFILE_FIELD_REMOVE
from backend.llm.policy import REQUEST_TIMEOUT_MAX, REQUEST_TIMEOUT_MIN
from backend.llm.execution import (
    LLMExecutionGovernorRegistry,
    LLMExecutionSettingsError,
    execution_scope_key,
    execution_snapshot_from_profile,
    get_default_execution_governors,
    validate_execution_profile_updates,
)


_PROFILE_NAME_PATTERN = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,80}$")
_EDITABLE_FIELDS = frozenset(
    {
        "base_url",
        "api_key",
        "ocr_model",
        "grading_model",
        "config_base_url",
        "config_api_key",
        "config_model",
        "teaching_prep_model",
        "class_teacher_model",
        "request_speed_mode",
        "max_concurrent_requests",
        "requests_per_minute",
        "max_auto_retries",
        "request_timeout_seconds",
        "batch_enabled",
        "batch_model",
        "batch_base_url",
        "batch_api_key",
    }
)
_ENDPOINT_FIELDS = frozenset({"base_url", "config_base_url", "batch_base_url"})
_MODEL_FIELDS = frozenset(
    {
        "ocr_model",
        "grading_model",
        "config_model",
        "teaching_prep_model",
        "class_teacher_model",
        "batch_model",
    }
)
_SECRET_FIELDS = frozenset({"api_key", "config_api_key", "batch_api_key"})
_MAX_ENDPOINT_LENGTH = 2048
_MAX_MODEL_LENGTH = 200
_MAX_SECRET_LENGTH = 8192
_TASK_KEYS = (
    "content_generation",
    "grading",
    "teaching_prep",
    "class_teacher",
)


class ModelProfileInvalid(ValueError):
    pass


class ModelProfileNotFound(LookupError):
    pass


class ModelProfileService:
    """Public model-profile behavior over the machine-local profile store."""

    def __init__(
        self,
        store: ApiProfileStore,
        *,
        execution_governors: LLMExecutionGovernorRegistry | None = None,
    ) -> None:
        self.store = store
        self.execution_governors = (
            execution_governors or get_default_execution_governors()
        )

    def list_state(self) -> dict[str, Any]:
        profiles = self.store.load()
        public_profiles = [_public_profile(profile) for profile in profiles]
        active_name = (
            _clean_existing_text(profiles[-1].get("name"))
            if profiles
            else None
        )
        return {
            "profiles": public_profiles,
            "active_profile_name": active_name,
            "active_profile": public_profiles[-1] if public_profiles else None,
            "task_bindings": self._public_task_bindings(profiles),
        }

    def update_task_bindings(
        self,
        bindings: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if set(bindings) != set(_TASK_KEYS):
            raise ModelProfileInvalid("All model task bindings are required")
        profiles = self.store.load()
        profile_names = {
            _clean_existing_text(profile.get("name")) for profile in profiles
        }
        normalized: dict[str, dict[str, str]] = {}
        for task in _TASK_KEYS:
            value = bindings.get(task)
            if not isinstance(value, Mapping):
                raise ModelProfileInvalid("Model task binding is invalid")
            profile_name = _validated_profile_name(value.get("profile_name"))
            model = _clean_existing_text(value.get("model"))
            if profile_name not in profile_names or not model or len(model) > _MAX_MODEL_LENGTH:
                raise ModelProfileInvalid("Model task binding is invalid")
            normalized[task] = {"profile_name": profile_name, "model": model}
        self.store.replace_task_bindings(normalized)
        return self.list_state()

    def _public_task_bindings(
        self,
        profiles: list[dict[str, Any]],
    ) -> dict[str, dict[str, str | None]]:
        saved = self.store.load_task_bindings()
        profile_names = {
            _clean_existing_text(profile.get("name")) for profile in profiles
        }
        active = profiles[-1] if profiles else {}
        active_name = _clean_existing_text(active.get("name")) or None
        defaults = {
            "content_generation": _clean_existing_text(
                active.get("config_model") or active.get("grading_model")
            ),
            "grading": _clean_existing_text(
                active.get("grading_model") or active.get("ocr_model")
            ),
            "teaching_prep": _clean_existing_text(
                active.get("teaching_prep_model")
                or active.get("config_model")
                or active.get("grading_model")
            ),
            "class_teacher": _clean_existing_text(
                active.get("class_teacher_model")
                or active.get("config_model")
                or active.get("grading_model")
            ),
        }
        return {
            task: {
                "profile_name": (
                    saved.get(task, {}).get("profile_name")
                    if saved.get(task, {}).get("profile_name") in profile_names
                    else active_name
                ),
                "model": (
                    saved.get(task, {}).get("model")
                    if saved.get(task, {}).get("profile_name") in profile_names
                    else defaults[task]
                ),
            }
            for task in _TASK_KEYS
        }

    def upsert(
        self,
        profile_name: str,
        updates: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = _validated_profile_name(profile_name)
        normalized_updates = _validated_updates(updates)
        try:
            self.store.update_named(
                name,
                normalized_updates,
                preserve_nonempty_keys=_SECRET_FIELDS,
                required_nonempty_keys_on_create=("api_key",),
            )
        except ValueError as exc:
            raise ModelProfileInvalid(
                "A new model profile requires an API key"
            ) from exc
        return self.list_state()

    def activate(self, profile_name: str) -> dict[str, Any]:
        name = _validated_profile_name(profile_name)
        if self.store.activate(name) is None:
            raise ModelProfileNotFound("Model profile not found")
        return self.list_state()

    def delete(self, profile_name: str) -> dict[str, Any]:
        name = _validated_profile_name(profile_name)
        if not any(
            _clean_existing_text(profile.get("name")) == name
            for profile in self.store.load()
        ):
            raise ModelProfileNotFound("Model profile not found")
        if not self.store.delete(name):
            raise ModelProfileNotFound("Model profile not found")
        saved = self.store.load_task_bindings()
        retained = {
            task: binding
            for task, binding in saved.items()
            if binding.get("profile_name") != name
        }
        if retained != saved:
            self.store.replace_task_bindings(retained)
        return self.list_state()

    def execution_status(self, profile_name: str) -> dict[str, int | str]:
        name = _validated_profile_name(profile_name)
        profile = next(
            (
                candidate
                for candidate in self.store.load()
                if _clean_existing_text(candidate.get("name")) == name
            ),
            None,
        )
        if profile is None:
            raise ModelProfileNotFound("Model profile not found")
        snapshot = execution_snapshot_from_profile(profile)
        return self.execution_governors.status(
            execution_scope_key(profile),
            snapshot,
        )


def _validated_profile_name(value: object) -> str:
    name = str(value or "").strip()
    if not _PROFILE_NAME_PATTERN.fullmatch(name):
        raise ModelProfileInvalid("Model profile name is invalid")
    return name


def _validated_updates(values: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(values) - _EDITABLE_FIELDS
    if unknown:
        raise ModelProfileInvalid("Model profile fields are invalid")
    updates: dict[str, Any] = {}
    if "max_auto_retries" in values:
        updates["max_auto_retries"] = _validated_max_auto_retries(
            values["max_auto_retries"]
        )
    if "request_timeout_seconds" in values:
        updates["request_timeout_seconds"] = _validated_request_timeout(
            values["request_timeout_seconds"]
        )
    execution_updates = {
        key: value
        for key, value in values.items()
        if key
        in {
            "request_speed_mode",
            "max_concurrent_requests",
            "requests_per_minute",
        }
    }
    if execution_updates:
        try:
            normalized_execution = validate_execution_profile_updates(
                execution_updates
            )
        except LLMExecutionSettingsError as exc:
            raise ModelProfileInvalid(str(exc)) from exc
        for key in execution_updates:
            updates[key] = normalized_execution[key]
    for field_name, raw_value in values.items():
        if (
            field_name in execution_updates
            or field_name == "max_auto_retries"
            or field_name == "request_timeout_seconds"
        ):
            continue
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if field_name == "batch_enabled":
            updates[field_name] = bool(raw_value)
        elif field_name in _ENDPOINT_FIELDS:
            updates[field_name] = _validated_endpoint(value)
        elif field_name in _MODEL_FIELDS:
            if len(value) > _MAX_MODEL_LENGTH:
                raise ModelProfileInvalid("Model name is too long")
            updates[field_name] = value
        elif field_name in _SECRET_FIELDS:
            if len(value) > _MAX_SECRET_LENGTH:
                raise ModelProfileInvalid("API key is too long")
            updates[field_name] = value
    return updates


def _validated_max_auto_retries(value: object) -> int | object:
    if value is None:
        return PROFILE_FIELD_REMOVE
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelProfileInvalid(
            "Auto retry limit must be an integer between 0 and 5"
        )
    if not 0 <= value <= 5:
        raise ModelProfileInvalid(
            "Auto retry limit must be an integer between 0 and 5"
        )
    return value


def _public_max_auto_retries(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 5 else None


_REQUEST_TIMEOUT_MIN = int(REQUEST_TIMEOUT_MIN)
_REQUEST_TIMEOUT_MAX = int(REQUEST_TIMEOUT_MAX)
_REQUEST_TIMEOUT_RANGE = (
    f"Request timeout must be an integer between "
    f"{_REQUEST_TIMEOUT_MIN} and {_REQUEST_TIMEOUT_MAX}"
)


def _validated_request_timeout(value: object) -> int | object:
    if value is None:
        return PROFILE_FIELD_REMOVE
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelProfileInvalid(_REQUEST_TIMEOUT_RANGE)
    if not _REQUEST_TIMEOUT_MIN <= value <= _REQUEST_TIMEOUT_MAX:
        raise ModelProfileInvalid(_REQUEST_TIMEOUT_RANGE)
    return value


def _public_request_timeout(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return (
        value
        if _REQUEST_TIMEOUT_MIN <= value <= _REQUEST_TIMEOUT_MAX
        else None
    )


def _validated_endpoint(value: str) -> str:
    if not value:
        return ""
    if len(value) > _MAX_ENDPOINT_LENGTH:
        raise ModelProfileInvalid("Model endpoint is too long")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ModelProfileInvalid(
            "Model endpoint must be an HTTP(S) URL"
        ) from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or any(character.isspace() for character in parsed.netloc)
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ModelProfileInvalid("Model endpoint must be an HTTP(S) URL")
    return value


def _public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    execution = execution_snapshot_from_profile(profile)
    return {
        "name": _clean_existing_text(profile.get("name")),
        "base_url": _public_endpoint(profile.get("base_url")),
        "has_api_key": _has_nonempty_value(profile.get("api_key")),
        "ocr_model": _clean_existing_text(profile.get("ocr_model")),
        "grading_model": _clean_existing_text(profile.get("grading_model")),
        "config_base_url": _public_endpoint(profile.get("config_base_url")),
        "has_config_api_key": _has_nonempty_value(profile.get("config_api_key")),
        "config_model": _clean_existing_text(profile.get("config_model")),
        "teaching_prep_model": _clean_existing_text(
            profile.get("teaching_prep_model")
        ),
        "class_teacher_model": _clean_existing_text(
            profile.get("class_teacher_model")
        ),
        "request_speed_mode": execution.mode,
        "max_concurrent_requests": execution.max_in_flight,
        "requests_per_minute": execution.requests_per_minute,
        "max_auto_retries": _public_max_auto_retries(
            profile.get("max_auto_retries")
        ),
        "request_timeout_seconds": _public_request_timeout(
            profile.get("request_timeout_seconds")
        ),
        "batch_enabled": bool(profile.get("batch_enabled")),
        "batch_model": _clean_existing_text(profile.get("batch_model")),
        "batch_base_url": _public_endpoint(profile.get("batch_base_url")),
        "has_batch_api_key": _has_nonempty_value(profile.get("batch_api_key")),
    }


def _public_endpoint(value: object) -> str:
    endpoint = _clean_existing_text(value)
    if not endpoint:
        return ""
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        return ""
    public_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        public_host = f"{public_host}:{port}"
    return urlunsplit((parsed.scheme, public_host, parsed.path, "", ""))


def _clean_existing_text(value: object) -> str:
    return str(value or "").strip()


def _has_nonempty_value(value: object) -> bool:
    return bool(_clean_existing_text(value))
