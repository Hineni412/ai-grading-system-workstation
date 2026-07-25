from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from api_profiles import ApiProfileStore


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
    }
)
_ENDPOINT_FIELDS = frozenset({"base_url", "config_base_url"})
_MODEL_FIELDS = frozenset({"ocr_model", "grading_model", "config_model"})
_SECRET_FIELDS = frozenset({"api_key", "config_api_key"})
_MAX_ENDPOINT_LENGTH = 2048
_MAX_MODEL_LENGTH = 200
_MAX_SECRET_LENGTH = 8192


class ModelProfileInvalid(ValueError):
    pass


class ModelProfileNotFound(LookupError):
    pass


class ModelProfileService:
    """Public model-profile behavior over the machine-local profile store."""

    def __init__(self, store: ApiProfileStore) -> None:
        self.store = store

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


def _validated_profile_name(value: object) -> str:
    name = str(value or "").strip()
    if not _PROFILE_NAME_PATTERN.fullmatch(name):
        raise ModelProfileInvalid("Model profile name is invalid")
    return name


def _validated_updates(values: Mapping[str, Any]) -> dict[str, str]:
    unknown = set(values) - _EDITABLE_FIELDS
    if unknown:
        raise ModelProfileInvalid("Model profile fields are invalid")
    updates: dict[str, str] = {}
    for field_name, raw_value in values.items():
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if field_name in _ENDPOINT_FIELDS:
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
    return {
        "name": _clean_existing_text(profile.get("name")),
        "base_url": _public_endpoint(profile.get("base_url")),
        "has_api_key": _has_nonempty_value(profile.get("api_key")),
        "ocr_model": _clean_existing_text(profile.get("ocr_model")),
        "grading_model": _clean_existing_text(profile.get("grading_model")),
        "config_base_url": _public_endpoint(profile.get("config_base_url")),
        "has_config_api_key": _has_nonempty_value(profile.get("config_api_key")),
        "config_model": _clean_existing_text(profile.get("config_model")),
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
