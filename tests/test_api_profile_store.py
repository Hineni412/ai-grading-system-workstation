from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import api_profiles
from path_manager import PathManager


ROOT = Path(__file__).resolve().parents[1]


POLICY_PROFILE = {
    "name": "default",
    "api_key": "grading-key",
    "config_api_key": "config-key",
    "base_url": "https://private.example/v1",
    "config_base_url": "https://private.example/v1",
    "grading_model": "grading-v1",
    "config_model": "config-v1",
    "llm_config_generation_timeout_seconds": 90,
    "llm_tagging_max_retries": 1,
    "objective_timeout": 60,
}


class _FakeStore:
    def __init__(self, profile: dict[str, object]) -> None:
        self.profile = profile

    def load(self) -> list[dict[str, object]]:
        return [self.profile]


class _SessionState(dict):
    def __getattr__(self, name: str):
        return self[name]

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state = _SessionState()
        self.sidebar = self

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def expander(self, *_args, **_kwargs):
        return self

    def markdown(self, *_args, **_kwargs) -> None:
        return None

    def caption(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def success(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None

    def text_input(self, _label: str, *, key: str, **_kwargs):
        return self.session_state[key]

    def selectbox(self, _label: str, *, key: str, **_kwargs):
        return self.session_state[key]

    def number_input(self, _label: str, *, key: str, **_kwargs):
        return self.session_state[key]

    def checkbox(self, _label: str, *, key: str, **_kwargs):
        return self.session_state[key]

    def button(self, *_args, **_kwargs) -> bool:
        return False


def _store(target: Path, *legacy_paths: Path):
    assert hasattr(api_profiles, "ApiProfileStore"), "ApiProfileStore must own profile persistence"
    return api_profiles.ApiProfileStore(target, legacy_paths=legacy_paths)


def test_api_profile_path_uses_machine_local_appdata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("AI_GRADING_API_PROFILES_PATH", raising=False)

    path_manager = PathManager()

    assert path_manager.api_profiles_path == (
        tmp_path / "local" / "AIGradingSystem" / "config" / "api_profiles.json"
    )


def test_api_profile_path_honors_explicit_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "private" / "profiles.json"
    monkeypatch.setenv("AI_GRADING_API_PROFILES_PATH", str(target))

    path_manager = PathManager()

    assert path_manager.api_profiles_path == target.resolve()


def test_legacy_api_profile_paths_include_current_user_data_location(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AI_GRADING_API_PROFILES_PATH", str(tmp_path / "external.json"))

    path_manager = PathManager()

    assert path_manager.config_dir / "api_profiles.json" in path_manager.legacy_api_profiles_paths


def test_store_migrates_a_legacy_profile_only_when_target_is_missing(tmp_path: Path) -> None:
    legacy = tmp_path / "project" / "user_data" / "config" / "api_profiles.json"
    target = tmp_path / "local" / "config" / "api_profiles.json"
    legacy.parent.mkdir(parents=True)
    original = [{"name": "default", "api_key": "grading-key", "tagging_model": "tag-v1"}]
    legacy.write_text(json.dumps(original), encoding="utf-8")

    store = _store(target, legacy)

    assert store.load() == original
    assert json.loads(target.read_text(encoding="utf-8")) == original

    legacy.write_text(
        json.dumps([{"name": "default", "api_key": "newer-legacy-value"}]),
        encoding="utf-8",
    )
    assert store.load() == original


def test_first_migration_fills_blank_target_fields_without_resurrecting_them_later(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "project" / "user_data" / "config" / "api_profiles.json"
    target = tmp_path / "local" / "config" / "api_profiles.json"
    legacy.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            [
                {
                    "name": "default",
                    "api_key": "legacy-key",
                    "tagging_api_key": "legacy-tag-key",
                    "grading_model": "legacy-model",
                }
            ]
        ),
        encoding="utf-8",
    )
    target.write_text(
        json.dumps(
            [
                {
                    "name": "default",
                    "api_key": "",
                    "grading_model": "newer-model",
                }
            ]
        ),
        encoding="utf-8",
    )
    store = _store(target, legacy)

    migrated = store.load()[-1]

    assert migrated["api_key"] == "legacy-key"
    assert migrated["tagging_api_key"] == "legacy-tag-key"
    assert migrated["grading_model"] == "newer-model"
    assert target.with_name(f"{target.name}.migration-v1").exists()

    store.clear_active_keys({"api_key", "tagging_api_key"})
    reloaded = store.load()[-1]
    assert reloaded["api_key"] == ""
    assert reloaded["tagging_api_key"] == ""


def test_update_active_preserves_unrelated_fields_and_nonempty_keys(tmp_path: Path) -> None:
    target = tmp_path / "api_profiles.json"
    target.write_text(
        json.dumps(
            [
                {
                    "name": "default",
                    "api_key": "saved-key",
                    "grading_model": "old-model",
                    "tagging_api_key": "tag-key",
                    "tagging_max_workers": 12,
                }
            ]
        ),
        encoding="utf-8",
    )
    store = _store(target)

    updated = store.update_active(
        {"api_key": "", "grading_model": "new-model"},
        preserve_nonempty_keys={"api_key"},
    )

    assert updated["api_key"] == "saved-key"
    assert updated["grading_model"] == "new-model"
    assert updated["tagging_api_key"] == "tag-key"
    assert updated["tagging_max_workers"] == 12


def test_clear_active_keys_is_explicit_and_preserves_other_settings(tmp_path: Path) -> None:
    target = tmp_path / "api_profiles.json"
    target.write_text(
        json.dumps(
            [
                {
                    "name": "default",
                    "api_key": "grading-key",
                    "config_api_key": "config-key",
                    "grading_model": "model-v1",
                }
            ]
        ),
        encoding="utf-8",
    )
    store = _store(target)

    cleared = store.clear_active_keys({"api_key", "config_api_key"})

    assert cleared["api_key"] == ""
    assert cleared["config_api_key"] == ""
    assert cleared["grading_model"] == "model-v1"

    backup = target.with_name(f"{target.name}.bak")
    assert "grading-key" not in backup.read_text(encoding="utf-8")
    assert "config-key" not in backup.read_text(encoding="utf-8")


def test_delete_profile_does_not_leave_its_key_in_backup(tmp_path: Path) -> None:
    target = tmp_path / "api_profiles.json"
    target.write_text(
        json.dumps(
            [
                {"name": "保留", "api_key": "keep-key"},
                {"name": "删除", "api_key": "deleted-key"},
            ]
        ),
        encoding="utf-8",
    )
    store = _store(target)

    assert store.delete("删除") is True

    backup = target.with_name(f"{target.name}.bak")
    assert "deleted-key" not in backup.read_text(encoding="utf-8")
    assert [item["name"] for item in json.loads(backup.read_text(encoding="utf-8"))] == ["保留"]


def test_failed_atomic_replace_keeps_the_previous_profile(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "api_profiles.json"
    original = [{"name": "default", "api_key": "safe-key", "grading_model": "old"}]
    target.write_text(json.dumps(original), encoding="utf-8")
    store = _store(target)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(api_profiles.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.update_active({"grading_model": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupt_primary_reads_the_last_known_good_backup(tmp_path: Path) -> None:
    target = tmp_path / "api_profiles.json"
    backup = target.with_name(f"{target.name}.bak")
    expected = [{"name": "default", "api_key": "backup-key"}]
    target.write_text("{broken", encoding="utf-8")
    backup.write_text(json.dumps(expected), encoding="utf-8")

    assert _store(target).load() == expected


def test_parallel_field_updates_do_not_overwrite_each_other(tmp_path: Path) -> None:
    target = tmp_path / "api_profiles.json"
    target.write_text(json.dumps([{"name": "default"}]), encoding="utf-8")
    store = _store(target)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.update_active, {"grading_model": "grading-v2"}),
            executor.submit(store.update_active, {"tagging_model": "tagging-v2"}),
        ]
        for future in futures:
            future.result()

    saved = store.load()[-1]
    assert saved["grading_model"] == "grading-v2"
    assert saved["tagging_model"] == "tagging-v2"


def test_tagging_service_reads_the_canonical_profile_store(monkeypatch) -> None:
    from question_bank.services import ai_tagging_service

    class FakeStore:
        def load(self):
            return [
                {
                    "name": "default",
                    "api_key": "grading-key",
                    "config_api_key": "config-key",
                    "base_url": "https://example.test/v1",
                    "config_base_url": "https://example.test/v1",
                    "grading_model": "grading-v1",
                    "config_model": "config-v1",
                }
            ]

    monkeypatch.setattr(
        ai_tagging_service,
        "get_api_profile_store",
        lambda: FakeStore(),
    )

    settings = ai_tagging_service._llm_settings_from_profile()

    assert settings is not None
    assert settings.api_key == "grading-key"
    assert settings.config_api_key == "config-key"
    assert settings.config_model == "config-v1"


def test_active_backend_settings_receive_sanitized_policy_overrides(monkeypatch) -> None:
    from backend.jobs import default_handlers

    monkeypatch.setattr(
        default_handlers,
        "get_api_profile_store",
        lambda: _FakeStore(POLICY_PROFILE),
    )

    settings = default_handlers._active_llm_settings()

    assert settings is not None
    policy_profile = dict(settings.policy_profile or {})
    execution_scope = policy_profile.pop("_llm_execution_scope_key")
    assert policy_profile == {
        "llm_config_generation_timeout_seconds": 90,
        "llm_tagging_max_retries": 1,
    }
    assert str(execution_scope).startswith("profile-")
    assert "default" not in str(execution_scope)


def test_tagging_profile_settings_receive_sanitized_policy_overrides(monkeypatch) -> None:
    from question_bank.services import ai_tagging_service

    monkeypatch.setattr(
        ai_tagging_service,
        "get_api_profile_store",
        lambda: _FakeStore(POLICY_PROFILE),
    )

    settings = ai_tagging_service._llm_settings_from_profile()

    assert settings is not None
    policy_profile = dict(settings.policy_profile or {})
    execution_scope = policy_profile.pop("_llm_execution_scope_key")
    assert policy_profile == {
        "llm_config_generation_timeout_seconds": 90,
        "llm_tagging_max_retries": 1,
    }
    assert str(execution_scope).startswith("profile-")
    assert "default" not in str(execution_scope)


def test_environment_and_dedicated_tagging_clients_keep_default_policies() -> None:
    from question_bank.services import ai_tagging_service

    environment_settings = ai_tagging_service._llm_settings_from_env(
        {
            "LLM_API_KEY": "environment-key",
            "LLM_CONFIG_API_KEY": "environment-config-key",
            "llm_tagging_max_retries": "5",
        }
    )
    service = ai_tagging_service.AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "dedicated-key",
            "QUESTION_BANK_TAGGING_MODEL": "tag-model",
            "QUESTION_BANK_TAGGING_REVIEW_API_KEY": "review-key",
            "QUESTION_BANK_TAGGING_REVIEW_MODEL": "review-model",
            "llm_tagging_max_retries": "5",
        }
    )

    assert environment_settings is not None
    assert environment_settings.policy_profile is None
    assert service.llm_client.settings.policy_profile is None
    assert service.review_llm_client is not None
    assert service.review_llm_client.settings.policy_profile is None


def test_saved_config_profile_is_forwarded_to_combined_analysis_adapter(monkeypatch) -> None:
    from question_bank.services import ai_tagging_service

    configured_client = object()
    fake_llm = SimpleNamespace(
        settings=SimpleNamespace(
            api_key="grading-key",
            base_url="https://grading.example/v1",
            config_api_key="config-key",
            config_base_url="https://config.example/v1",
            config_model="config-model",
            policy_profile={"llm_config_generation_timeout_seconds": 90},
        ),
        config_client=configured_client,
    )
    captured: dict[str, object] = {}

    class CapturingAdapter:
        def __init__(self, api_key, base_url, **kwargs):
            captured.update(api_key=api_key, base_url=base_url, **kwargs)

    monkeypatch.setattr(ai_tagging_service, "LLMProtocolAdapter", CapturingAdapter)
    service = ai_tagging_service.AITaggingService(env={}, llm_client=fake_llm)

    adapter = service._protocol_adapter()

    assert isinstance(adapter, CapturingAdapter)
    assert captured["api_key"] == "config-key"
    assert captured["base_url"] == "https://config.example/v1"
    assert captured["client"] is configured_client
    assert service.model == "config-model"


def test_updates_do_not_copy_api_keys_into_data_backups() -> None:
    update_source = (ROOT / "update_tools" / "apply_update.py").read_text(encoding="utf-8")

    assert 'create_backup("before_update", include_api_keys=False)' in update_source
