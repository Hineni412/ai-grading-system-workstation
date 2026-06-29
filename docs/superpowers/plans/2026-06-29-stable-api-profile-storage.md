# Stable API Profile Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep grading and question-bank API configuration stable across code edits, branches, worktrees, updates, exports, and portable packages.

**Architecture:** `PathManager` resolves one machine-local API profile path outside the repository. `ApiProfileStore` owns legacy migration, validated reads, field-level active-profile updates, explicit key clearing, process locking, backups, and atomic replacement. UI and tagging services use this store instead of directly rewriting JSON or searching multiple project-relative paths.

**Tech Stack:** Python 3.12, pathlib, JSON, Streamlit, pytest, Windows local application data.

## Global Constraints

- Preserve the existing profile schema and field names; do not introduce duplicate grading/tagging concepts.
- Default path on Windows is `%LOCALAPPDATA%/AIGradingSystem/config/api_profiles.json`.
- `AI_GRADING_API_PROFILES_PATH` is the explicit test/operations override.
- Blank UI values do not erase an existing API key; key deletion is a separate explicit action.
- No new third-party dependency.
- The real API profile file must not remain tracked by Git or enter packages/data exports.
- Existing `user_data/config/api_profiles.json` is migrated only when the external target does not yet exist.

---

### Task 1: Stable path and profile store contract

**Files:**
- Create: `tests/test_api_profile_store.py`
- Modify: `path_manager.py`
- Modify: `api_profiles.py`

**Interfaces:**
- Produces: `PathManager.legacy_api_profiles_paths: tuple[Path, ...]`
- Produces: `ApiProfileStore(path: Path, legacy_paths: Iterable[Path] = ())`
- Produces: `get_api_profile_store() -> ApiProfileStore`
- Produces: `ApiProfileStore.load() -> list[dict[str, Any]]`
- Produces: `ApiProfileStore.update_active(updates, preserve_nonempty_keys=()) -> dict[str, Any]`
- Produces: `ApiProfileStore.clear_active_keys(keys) -> dict[str, Any]`

- [ ] **Step 1: Write failing stable-path tests**

```python
def test_api_profile_path_uses_machine_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("AI_GRADING_API_PROFILES_PATH", raising=False)
    assert PathManager().api_profiles_path == tmp_path / "local" / "AIGradingSystem" / "config" / "api_profiles.json"

def test_api_profile_path_honors_explicit_override(monkeypatch, tmp_path):
    target = tmp_path / "private" / "profiles.json"
    monkeypatch.setenv("AI_GRADING_API_PROFILES_PATH", str(target))
    assert PathManager().api_profiles_path == target.resolve()
```

- [ ] **Step 2: Run the path tests and verify they fail because the current path is project-relative**

Run: `python -m pytest tests/test_api_profile_store.py -q`

- [ ] **Step 3: Implement stable path resolution and legacy path enumeration**

```python
override = os.getenv("AI_GRADING_API_PROFILES_PATH")
if override:
    return Path(override).expanduser().resolve()
local_appdata = os.getenv("LOCALAPPDATA")
base = Path(local_appdata) if local_appdata else Path.home() / ".ai_grading_system"
return base / "AIGradingSystem" / "config" / "api_profiles.json"
```

- [ ] **Step 4: Write failing store behavior tests**

```python
def test_store_migrates_legacy_file_once(tmp_path): ...
def test_update_active_preserves_unrelated_fields_and_blank_keys(tmp_path): ...
def test_clear_active_keys_is_the_only_way_to_remove_keys(tmp_path): ...
def test_failed_atomic_replace_keeps_previous_file(tmp_path, monkeypatch): ...
def test_corrupt_primary_loads_last_known_good_backup(tmp_path): ...
```

- [ ] **Step 5: Run the store tests and verify the missing store/behavior failures**

Run: `python -m pytest tests/test_api_profile_store.py -q`

- [ ] **Step 6: Implement the minimal locked and atomic store**

Use an in-process re-entrant lock plus an OS file lock. Write JSON into a sibling temporary file, flush and `fsync`, validate it, copy the current valid file to `.bak`, then call `os.replace`. Always remove an abandoned temporary file in `finally`.

- [ ] **Step 7: Run store and existing API tests**

Run: `python -m pytest tests/test_api_profile_store.py tests/test_grading_limits.py -q`

### Task 2: Move all runtime consumers to the store

**Files:**
- Modify: `web_app.py`
- Modify: `pages/题库管理.py`
- Modify: `question_bank/services/ai_tagging_service.py`
- Test: `tests/test_api_profile_store.py`

**Interfaces:**
- Consumes: `get_api_profile_store()` and field-level store methods from Task 1.
- Produces: one runtime source for grading, rubric generation, objective recognition, tagging, and tagging review settings.

- [ ] **Step 1: Write source-contract tests that reject direct whole-file page saves and multi-path tagging lookup**

```python
def test_pages_use_field_level_profile_store(): ...
def test_tagging_service_uses_the_canonical_profile_store(): ...
```

- [ ] **Step 2: Run the new tests and confirm current direct saves/path search fail them**

Run: `python -m pytest tests/test_api_profile_store.py -q`

- [ ] **Step 3: Replace page save blocks with `update_active` calls**

Main page updates only grading/config/objective/concurrency keys. Question-bank page updates only `tagging_*` keys. Both pass the relevant API key names in `preserve_nonempty_keys`.

- [ ] **Step 4: Replace `_profile_paths()` with one `get_api_profile_store().load()` call**

Remove current-working-directory and legacy project-path probing from the tagging service.

- [ ] **Step 5: Add explicit two-step clear actions**

The main page clears `api_key`, `config_api_key`, and `objective_api_key`; the question-bank page clears `tagging_api_key` and `tagging_review_api_key`. Clear matching session/environment values only after confirmation.

- [ ] **Step 6: Run targeted page and tagging tests**

Run: `python -m pytest tests/test_api_profile_store.py tests/test_grading_limits.py tests/test_ai_tagging_service.py -q`

### Task 3: Keep secrets out of Git, packages, exports, and update backups

**Files:**
- Modify: `.gitignore`
- Remove from index: `user_data/config/api_profiles.json`
- Modify: `data_transfer_service.py`
- Modify: `package_v1.5.0.py`
- Modify: `update_tools/apply_update.py`
- Modify: `tests/test_data_transfer_service.py`
- Create or modify: package/update tests as available

**Interfaces:**
- Consumes: canonical external path from Task 1.
- Produces: exports and packages that never contain `api_profiles.json`.

- [ ] **Step 1: Change export/package tests first to require exclusion in lean and full modes**

```python
assert "user_data/config/api_profiles.json" not in arc_names
```

- [ ] **Step 2: Run tests and confirm they fail under current inclusion behavior**

Run: `python -m pytest tests/test_data_transfer_service.py -q`

- [ ] **Step 3: Exclude the legacy filename from every export/package scope and stop update backups requesting API keys**

Keep database and document export behavior unchanged.

- [ ] **Step 4: Ignore and untrack the legacy file without deleting the worktree copy before migration validation**

Run: `git rm --cached user_data/config/api_profiles.json`

- [ ] **Step 5: Run export/package/update tests**

Run: `python -m pytest tests/test_data_transfer_service.py tests/test_portable_path_resolution.py -q`

### Task 4: Documentation, migration, and verification

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `README_工作机使用说明.md`
- Modify: `README_私人便携版_v1.5.0.md`

**Interfaces:**
- Documents: external path, one-time migration, non-portable credentials, explicit clearing, and remaining Git-history risk.

- [ ] **Step 1: Update current architecture and operating instructions**

Record that databases/documents remain under `user_data`, while API profiles are machine-local and excluded from Git, packages, and data exports.

- [ ] **Step 2: Run local one-time migration without printing secret values**

Load through `get_api_profile_store()` and verify profile count plus required key presence only.

- [ ] **Step 3: Run syntax and targeted tests**

Run: `python -m compileall api_profiles.py path_manager.py web_app.py pages question_bank/services/ai_tagging_service.py`

- [ ] **Step 4: Run the complete suite with an isolated API profile override**

Run: set `AI_GRADING_API_PROFILES_PATH` to a temporary test path, then execute `python -m pytest -q`.

- [ ] **Step 5: Review the complete diff and secret tracking state**

Verify no secret values appear in diffs, `git ls-files user_data/config/api_profiles.json` is empty, and only planned files changed.
