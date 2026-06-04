from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_api_profiles(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and isinstance(item.get("name"), str)]


def save_api_profiles(file_path: Path, profiles: list[dict[str, Any]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def upsert_api_profile(file_path: Path, profile: dict[str, Any]) -> None:
    profiles = load_api_profiles(file_path)
    replaced = False
    for idx, item in enumerate(profiles):
        if item.get("name") == profile.get("name"):
            profiles[idx] = profile
            replaced = True
            break
    if not replaced:
        profiles.append(profile)
    save_api_profiles(file_path, profiles)


def delete_api_profile(file_path: Path, profile_name: str) -> bool:
    profiles = load_api_profiles(file_path)
    next_profiles = [item for item in profiles if item.get("name") != profile_name]
    if len(next_profiles) == len(profiles):
        return False
    save_api_profiles(file_path, next_profiles)
    return True


def active_api_profile(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return profiles[-1] if profiles else {}


def normalize_question_allowlist(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_list(value: Any, default: list[str]) -> list[str]:
    normalized = normalize_question_allowlist(value)
    return normalized if normalized else list(default)


def get_objective_replacement_config_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "objective_replacement_enabled": bool(profile.get("objective_replacement_enabled", False)),
        "objective_replacement_mode": str(profile.get("objective_replacement_mode") or "off"),
        "objective_replacement_question_allowlist": normalize_question_allowlist(
            profile.get("objective_replacement_question_allowlist")
        ),
    }


def get_objective_api_config() -> dict[str, Any]:
    import os
    from path_manager import get_path_manager
    pm = get_path_manager()
    profiles = load_api_profiles(pm.api_profiles_path)
    profile = active_api_profile(profiles)
    
    return {
        "base_url": profile.get("objective_base_url") or os.getenv("LLM_OBJECTIVE_BASE_URL", ""),
        "api_key": profile.get("objective_api_key") or os.getenv("LLM_OBJECTIVE_API_KEY", ""),
        "model": profile.get("objective_model") or os.getenv("LLM_OBJECTIVE_MODEL", ""),
        "temperature": float(profile.get("objective_temperature", 0.0)),
        "max_tokens": int(profile.get("objective_max_tokens", 100)),
        "thinking_type": str(profile.get("objective_thinking_type", "disabled")),
        "timeout": int(profile.get("objective_timeout", 60)),
        "enabled": bool(profile.get("objective_enabled", False)),
    }
