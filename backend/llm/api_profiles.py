from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from backend.llm import policy_overrides_from_profile

_DEPRECATED_OBJECTIVE_PROFILE_KEYS = {
    "objective_timeout",
    "objective_max_tokens",
}
# Sentinel consumed by update_named(): pop the key instead of storing a value,
# so callers can reset an optional field back to "unset".
PROFILE_FIELD_REMOVE = object()
_PROFILE_PROCESS_LOCK = threading.RLock()
_LOCK_TIMEOUT_SECONDS = 10.0
MODEL_TASK_KEYS = (
    "content_generation",
    "grading",
)
_TASK_BINDING_REQUIRED_KEYS = frozenset({"profile_name", "model"})
_TASK_BINDING_IGNORED_KEYS = frozenset({"batch_model"})
logger = logging.getLogger(__name__)


class ApiProfileStorageError(RuntimeError):
    """Raised when neither the primary profile file nor its backup is usable."""


def _serialized_profile_copy(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(profile).items()
        if key not in _DEPRECATED_OBJECTIVE_PROFILE_KEYS
    }


def _validated_profiles(data: Any, *, source: Path) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise ApiProfileStorageError(f"API profile data in {source} must be a list")
    profiles: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ApiProfileStorageError(f"API profile {index} in {source} must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ApiProfileStorageError(f"API profile {index} in {source} has no valid name")
        profiles.append(dict(item))
    return profiles


def _read_profiles_file(file_path: Path) -> list[dict[str, Any]]:
    with file_path.open("r", encoding="utf-8") as file_handle:
        return _validated_profiles(json.load(file_handle), source=file_path)


def _backup_path(file_path: Path) -> Path:
    return file_path.with_name(f"{file_path.name}.bak")


def _migration_marker_path(file_path: Path) -> Path:
    return file_path.with_name(f"{file_path.name}.migration-v1")


@contextmanager
def _exclusive_profile_lock(file_path: Path) -> Iterator[None]:
    """Serialize read-modify-write operations across threads and app processes."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_name(f"{file_path.name}.lock")
    with _PROFILE_PROCESS_LOCK:
        with lock_path.open("a+b") as lock_handle:
            lock_handle.seek(0, os.SEEK_END)
            if lock_handle.tell() == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()

            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        lock_handle.seek(0)
                        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out waiting for API profile lock: {lock_path}")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_profiles(file_path: Path, profiles: list[dict[str, Any]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_profiles = [
        _serialized_profile_copy(profile)
        for profile in profiles
        if isinstance(profile, dict)
    ]
    _validated_profiles(serialized_profiles, source=file_path)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{file_path.name}.",
        suffix=".tmp",
        dir=file_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
            json.dump(serialized_profiles, file_handle, ensure_ascii=False, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())

        _read_profiles_file(temporary_path)

        # Publish the same validated state to the recovery slot first. Keeping
        # a previous-state backup would retain a key after the teacher cleared
        # it. If the following primary replace fails, the existing primary is
        # still authoritative and the operation reports failure.
        backup_path = _backup_path(file_path)
        backup_descriptor, backup_temporary_name = tempfile.mkstemp(
            prefix=f"{backup_path.name}.",
            suffix=".tmp",
            dir=file_path.parent,
        )
        backup_temporary_path = Path(backup_temporary_name)
        try:
            with os.fdopen(backup_descriptor, "w", encoding="utf-8") as backup_handle:
                json.dump(serialized_profiles, backup_handle, ensure_ascii=False, indent=2)
                backup_handle.flush()
                os.fsync(backup_handle.fileno())
            _read_profiles_file(backup_temporary_path)
            os.replace(backup_temporary_path, backup_path)
            os.replace(temporary_path, file_path)
        finally:
            if backup_temporary_path.exists():
                backup_temporary_path.unlink()
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _validated_task_bindings(data: Any) -> dict[str, dict[str, str]]:
    if not isinstance(data, dict) or set(data) - set(MODEL_TASK_KEYS):
        raise ValueError("Model task bindings are invalid")
    normalized: dict[str, dict[str, str]] = {}
    allowed_keys = _TASK_BINDING_REQUIRED_KEYS | _TASK_BINDING_IGNORED_KEYS
    for task, value in data.items():
        if (
            not isinstance(value, dict)
            or not _TASK_BINDING_REQUIRED_KEYS <= set(value)
            or set(value) - allowed_keys
        ):
            raise ValueError("Model task binding is invalid")
        profile_name = str(value.get("profile_name") or "").strip()
        model = str(value.get("model") or "").strip()
        if not profile_name or len(profile_name) > 80 or not model or len(model) > 200:
            raise ValueError("Model task binding is invalid")
        leftover_batch_model = str(value.get("batch_model") or "").strip()
        if len(leftover_batch_model) > 200:
            raise ValueError("Model task binding is invalid")
        normalized[str(task)] = {
            "profile_name": profile_name,
            "model": model,
        }
    return normalized


def _normalized_persisted_task_bindings(data: Any) -> dict[str, dict[str, str]]:
    """Load persisted bindings; drop task names retired from this build."""
    if not isinstance(data, dict):
        raise ValueError("Model task bindings are invalid")
    return _validated_task_bindings(
        {key: value for key, value in data.items() if key in MODEL_TASK_KEYS}
    )


class ApiProfileStore:
    """Single persistence boundary for machine-local API profiles."""

    def __init__(self, path: Path, *, legacy_paths: Iterable[Path] = ()) -> None:
        self.path = Path(path)
        self.legacy_paths = tuple(Path(item) for item in legacy_paths)

    @property
    def task_bindings_path(self) -> Path:
        return self.path.with_name("model_task_bindings.json")

    def load_task_bindings(self) -> dict[str, dict[str, str]]:
        path = self.task_bindings_path
        with _exclusive_profile_lock(path):
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return _normalized_persisted_task_bindings(data)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ApiProfileStorageError(
                    f"Unable to read model task bindings from {path}"
                ) from exc

    def replace_task_bindings(
        self,
        bindings: Mapping[str, Mapping[str, Any]],
    ) -> None:
        normalized = _validated_task_bindings(dict(bindings))
        path = self.task_bindings_path
        with _exclusive_profile_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(normalized, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                _validated_task_bindings(
                    json.loads(temporary_path.read_text(encoding="utf-8"))
                )
                os.replace(temporary_path, path)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink()

    def profile_for_task(self, task: str) -> dict[str, Any]:
        profiles = self.load()
        fallback = active_api_profile(profiles)
        binding = self.load_task_bindings().get(str(task), {})
        profile_name = str(binding.get("profile_name") or "").strip()
        selected = next(
            (
                profile
                for profile in profiles
                if str(profile.get("name") or "").strip() == profile_name
            ),
            fallback,
        )
        result = dict(selected)
        model = str(binding.get("model") or "").strip()
        if not model:
            return result
        if task == "content_generation":
            result.update({
                "config_base_url": result.get("base_url"),
                "config_api_key": result.get("api_key"),
                "config_model": model,
            })
        elif task == "grading":
            result.update({
                "ocr_model": model,
                "grading_model": model,
            })
        return result

    def _ensure_migrated_unlocked(self) -> None:
        if not self.legacy_paths or _migration_marker_path(self.path).exists():
            return
        legacy_profiles: list[dict[str, Any]] | None = None
        for legacy_path in self.legacy_paths:
            if legacy_path == self.path or not legacy_path.is_file():
                continue
            try:
                legacy_profiles = _read_profiles_file(legacy_path)
            except (OSError, ValueError, json.JSONDecodeError, ApiProfileStorageError) as exc:
                logger.warning("Skipping invalid legacy API profile file %s: %s", legacy_path, exc)
                continue
            break
        if legacy_profiles is None:
            return

        current_profiles: list[dict[str, Any]] = []
        if self.path.exists():
            try:
                current_profiles = _read_profiles_file(self.path)
            except (OSError, ValueError, json.JSONDecodeError, ApiProfileStorageError):
                backup = _backup_path(self.path)
                if backup.exists():
                    try:
                        current_profiles = _read_profiles_file(backup)
                    except (OSError, ValueError, json.JSONDecodeError, ApiProfileStorageError):
                        current_profiles = []

        changed = not self.path.exists()
        profile_indexes = {
            str(profile.get("name")): index
            for index, profile in enumerate(current_profiles)
        }
        for legacy_profile in legacy_profiles:
            profile_name = str(legacy_profile.get("name"))
            if profile_name not in profile_indexes:
                current_profiles.append(dict(legacy_profile))
                profile_indexes[profile_name] = len(current_profiles) - 1
                changed = True
                continue
            current_profile = current_profiles[profile_indexes[profile_name]]
            for key, legacy_value in legacy_profile.items():
                if key not in current_profile or (
                    _is_blank(current_profile.get(key)) and not _is_blank(legacy_value)
                ):
                    current_profile[key] = legacy_value
                    changed = True

        if changed:
            _atomic_write_profiles(self.path, current_profiles)
        marker = _migration_marker_path(self.path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("migration-v1-complete\n", encoding="utf-8")
        logger.info("Completed one-time API profile migration into %s", self.path)

    def _load_unlocked(self) -> list[dict[str, Any]]:
        self._ensure_migrated_unlocked()
        if not self.path.exists():
            backup = _backup_path(self.path)
            return _read_profiles_file(backup) if backup.exists() else []
        try:
            return _read_profiles_file(self.path)
        except (OSError, ValueError, json.JSONDecodeError, ApiProfileStorageError) as primary_error:
            backup = _backup_path(self.path)
            if backup.exists():
                try:
                    profiles = _read_profiles_file(backup)
                except (OSError, ValueError, json.JSONDecodeError, ApiProfileStorageError):
                    pass
                else:
                    logger.warning("Using last known good API profile backup for %s", self.path)
                    return profiles
            raise ApiProfileStorageError(
                f"Unable to read API profiles from {self.path} or its backup"
            ) from primary_error

    def load(self) -> list[dict[str, Any]]:
        with _exclusive_profile_lock(self.path):
            return self._load_unlocked()

    def replace_all(self, profiles: list[dict[str, Any]]) -> None:
        with _exclusive_profile_lock(self.path):
            _atomic_write_profiles(self.path, profiles)

    def update_active(
        self,
        updates: Mapping[str, Any],
        *,
        preserve_nonempty_keys: Iterable[str] = (),
    ) -> dict[str, Any]:
        preserved = set(preserve_nonempty_keys)
        with _exclusive_profile_lock(self.path):
            profiles = self._load_unlocked()
            active = dict(profiles[-1]) if profiles else {"name": "default"}
            for key, value in updates.items():
                normalized_key = str(key)
                if (
                    normalized_key in preserved
                    and _is_blank(value)
                    and not _is_blank(active.get(normalized_key))
                ):
                    continue
                active[normalized_key] = value
            if profiles:
                profiles[-1] = active
            else:
                profiles = [active]
            _atomic_write_profiles(self.path, profiles)
            return dict(active)

    def clear_active_keys(self, keys: Iterable[str]) -> dict[str, Any]:
        return self.update_active({str(key): "" for key in keys})

    def upsert(self, profile: Mapping[str, Any]) -> None:
        replacement = dict(profile)
        with _exclusive_profile_lock(self.path):
            profiles = self._load_unlocked()
            for index, item in enumerate(profiles):
                if item.get("name") == replacement.get("name"):
                    profiles[index] = replacement
                    break
            else:
                profiles.append(replacement)
            _atomic_write_profiles(self.path, profiles)

    def update_named(
        self,
        profile_name: str,
        updates: Mapping[str, Any],
        *,
        preserve_nonempty_keys: Iterable[str] = (),
        required_nonempty_keys_on_create: Iterable[str] = (),
    ) -> tuple[dict[str, Any], bool]:
        """Atomically merge allowed updates into one named profile."""
        normalized_name = str(profile_name)
        preserved = set(preserve_nonempty_keys)
        required_on_create = set(required_nonempty_keys_on_create)
        with _exclusive_profile_lock(self.path):
            profiles = self._load_unlocked()
            created = True
            profile: dict[str, Any] = {"name": normalized_name}
            profile_index: int | None = None
            for index, item in enumerate(profiles):
                if item.get("name") == normalized_name:
                    profile = dict(item)
                    profile_index = index
                    created = False
                    break
            for key, value in updates.items():
                normalized_key = str(key)
                if value is PROFILE_FIELD_REMOVE:
                    profile.pop(normalized_key, None)
                    continue
                if (
                    normalized_key in preserved
                    and _is_blank(value)
                    and not _is_blank(profile.get(normalized_key))
                ):
                    continue
                profile[normalized_key] = value
            profile["name"] = normalized_name
            if created and any(
                _is_blank(profile.get(key))
                for key in required_on_create
            ):
                raise ValueError("Required profile value is missing")
            if profile_index is None:
                profiles.append(profile)
            else:
                profiles[profile_index] = profile
            _atomic_write_profiles(self.path, profiles)
            return dict(profile), created

    def activate(self, profile_name: str) -> dict[str, Any] | None:
        """Atomically make a named profile active by moving it to the tail."""
        normalized_name = str(profile_name)
        with _exclusive_profile_lock(self.path):
            profiles = self._load_unlocked()
            profile_index = next(
                (
                    index
                    for index, item in enumerate(profiles)
                    if item.get("name") == normalized_name
                ),
                None,
            )
            if profile_index is None:
                return None
            profile = profiles.pop(profile_index)
            profiles.append(profile)
            _atomic_write_profiles(self.path, profiles)
            return dict(profile)

    def delete(self, profile_name: str) -> bool:
        with _exclusive_profile_lock(self.path):
            profiles = self._load_unlocked()
            retained = [item for item in profiles if item.get("name") != profile_name]
            if len(retained) == len(profiles):
                return False
            _atomic_write_profiles(self.path, retained)
            return True


def get_api_profile_store() -> ApiProfileStore:
    from path_manager import get_path_manager

    path_manager = get_path_manager()
    return ApiProfileStore(
        path_manager.api_profiles_path,
        legacy_paths=getattr(path_manager, "legacy_api_profiles_paths", ()),
    )


def load_api_profiles(file_path: Path) -> list[dict[str, Any]]:
    return ApiProfileStore(file_path).load()


def save_api_profiles(file_path: Path, profiles: list[dict[str, Any]]) -> None:
    ApiProfileStore(file_path).replace_all(profiles)


def upsert_api_profile(file_path: Path, profile: dict[str, Any]) -> None:
    ApiProfileStore(file_path).upsert(profile)


def delete_api_profile(file_path: Path, profile_name: str) -> bool:
    return ApiProfileStore(file_path).delete(profile_name)


def active_api_profile(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return profiles[-1] if profiles else {}


def resolve_profile_for_task(store: Any, task: str) -> dict[str, Any]:
    """Resolve task routing while keeping older injected stores compatible."""
    resolver = getattr(store, "profile_for_task", None)
    if callable(resolver):
        return dict(resolver(task))
    return dict(active_api_profile(store.load()))


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
    profile = active_api_profile(get_api_profile_store().load())

    return {
        "base_url": profile.get("objective_base_url") or os.getenv("LLM_OBJECTIVE_BASE_URL", ""),
        "api_key": profile.get("objective_api_key") or os.getenv("LLM_OBJECTIVE_API_KEY", ""),
        "model": profile.get("objective_model") or os.getenv("LLM_OBJECTIVE_MODEL", ""),
        "temperature": float(profile.get("objective_temperature", 0.0)),
        "thinking_type": str(profile.get("objective_thinking_type", "disabled")),
        "enabled": bool(profile.get("objective_enabled", False)),
        "policy_profile": policy_overrides_from_profile(profile),
    }
