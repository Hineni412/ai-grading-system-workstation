from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import backend.llm.api_profiles as api_profiles


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


def _store(target: Path, *legacy_paths: Path):
    assert hasattr(api_profiles, "ApiProfileStore"), (
        "ApiProfileStore must own profile persistence"
    )
    return api_profiles.ApiProfileStore(target, legacy_paths=legacy_paths)


def test_failed_atomic_replace_keeps_the_previous_profile(
    tmp_path: Path, monkeypatch
) -> None:
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
