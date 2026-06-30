# Question Bank Tagging Configuration Read-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the question-bank tagging sidebar and batch-tag action always use the current machine-local API profile, with read-only controls and no page-local configuration writes.

**Architecture:** Keep API profile persistence in the existing `ApiProfileStore`. Add one small presentation-state helper that deterministically replaces stale Streamlit state from the active profile on every rerun; the page reloads the active profile for readiness and runtime limits, renders disabled controls, and synchronizes the existing tagging environment variables before any tagging action is handled.

**Tech Stack:** Python 3.12, Streamlit 1.58, pytest.

## Global Constraints

- `%LOCALAPPDATA%/AIGradingSystem/config/api_profiles.json` remains the only persistent source for question-bank tagging configuration.
- Reuse existing `tagging_*` fields; do not add or migrate configuration fields.
- Do not modify databases, AI tag schemas, model prompts, concurrency executors, or API protocols.
- API keys stay password-masked in the UI; all configuration controls are read-only.
- Do not update `ARCHITECTURE.md` because module boundaries and persisted structures do not change.

---

### Task 1: Deterministic tagging sidebar state

**Files:**
- Create: `pages_shared/question_bank_tagging_config_state.py`
- Create: `tests/test_question_bank_tagging_config_state.py`

**Interfaces:**
- Consumes: a mutable Streamlit-session-compatible mapping and the active API profile mapping.
- Produces: `replace_tagging_config_state(state, profile)`, `missing_tagging_config_fields(profile)`, and `tagging_runtime_limits(profile)`.

- [ ] **Step 1: Write failing tests for stale-state replacement and profile validation**

```python
from pages_shared.question_bank_tagging_config_state import (
    missing_tagging_config_fields,
    replace_tagging_config_state,
    tagging_runtime_limits,
)


def test_saved_profile_replaces_stale_empty_session_values() -> None:
    state = {
        "tagging_api_key_input": "",
        "tagging_base_url_input": "",
        "tagging_model_input": "",
    }
    profile = {
        "tagging_api_key": "saved-key",
        "tagging_base_url": "https://example.test/v1",
        "tagging_model": "tag-model",
        "tagging_max_workers": 20,
        "tagging_requests_per_minute": 1000,
        "tagging_thinking": True,
    }

    replace_tagging_config_state(state, profile)

    assert state["tagging_api_key_input"] == "saved-key"
    assert state["tagging_base_url_input"] == "https://example.test/v1"
    assert state["tagging_model_input"] == "tag-model"
    assert state["tagging_max_workers_input"] == 20
    assert state["tagging_requests_per_minute_input"] == 1000
    assert state["tagging_thinking_input"] is True


def test_missing_fields_and_runtime_limits_come_from_profile() -> None:
    assert missing_tagging_config_fields({"tagging_api_key": "key"}) == (
        "tagging_base_url",
        "tagging_model",
    )
    assert tagging_runtime_limits(
        {"tagging_max_workers": 0, "tagging_requests_per_minute": "25"}
    ) == (1, 25)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m pytest tests/test_question_bank_tagging_config_state.py -q`

Expected: collection fails with `ModuleNotFoundError` because the helper does not exist.

- [ ] **Step 3: Add the minimal state helper**

```python
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any


REQUIRED_TAGGING_FIELDS = (
    "tagging_api_key",
    "tagging_base_url",
    "tagging_model",
)


def replace_tagging_config_state(
    state: MutableMapping[str, Any],
    profile: Mapping[str, Any],
) -> None:
    base_url = str(profile.get("tagging_base_url") or "").strip()
    state.update(
        {
            "tagging_api_key_input": str(profile.get("tagging_api_key") or "").strip(),
            "tagging_base_url_input": base_url,
            "tagging_model_input": str(profile.get("tagging_model") or "").strip(),
            "tagging_max_workers_input": _positive_int(profile.get("tagging_max_workers"), 4),
            "tagging_requests_per_minute_input": _positive_int(
                profile.get("tagging_requests_per_minute"), 1000
            ),
            "tagging_thinking_input": bool(profile.get("tagging_thinking", False)),
            "tagging_review_enabled_input": bool(
                profile.get("tagging_review_enabled", False)
            ),
            "tagging_review_api_key_input": str(
                profile.get("tagging_review_api_key") or ""
            ).strip(),
            "tagging_review_base_url_input": str(
                profile.get("tagging_review_base_url") or base_url
            ).strip(),
            "tagging_review_model_input": str(
                profile.get("tagging_review_model") or ""
            ).strip(),
            "tagging_enabled_input": True,
            "tagging_enabled": True,
        }
    )


def missing_tagging_config_fields(profile: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        field for field in REQUIRED_TAGGING_FIELDS if not str(profile.get(field) or "").strip()
    )


def tagging_runtime_limits(profile: Mapping[str, Any]) -> tuple[int, int]:
    return (
        _positive_int(profile.get("tagging_max_workers"), 4),
        _positive_int(profile.get("tagging_requests_per_minute"), 1000),
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default
```

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `python -m pytest tests/test_question_bank_tagging_config_state.py -q`

Expected: `2 passed`.

### Task 2: Read-only profile-driven question-bank page

**Files:**
- Modify: `pages/题库管理.py`
- Modify: `tests/test_question_bank_ai_tagging_ui.py`

**Interfaces:**
- Consumes: Task 1 helper functions and `ApiProfileStore.load()`.
- Produces: `_load_local_tagging_profile() -> tuple[object, dict[str, Any], str | None]`; existing `_tagging_config_ready()`, `_tagging_runtime_limits()`, and `_warn_missing_tagging_config()` become disk-profile-driven.

- [ ] **Step 1: Add failing UI-contract tests**

```python
def test_tagging_config_is_disk_backed_and_read_only() -> None:
    page = PAGE.read_text(encoding="utf-8")
    config_block = page.split("def _render_local_tagging_api_config", 1)[1].split(
        "def _tagging_config_ready", 1
    )[0]

    assert "replace_tagging_config_state(st.session_state, saved_profile)" in config_block
    assert config_block.count("disabled=True") >= 9
    assert "保存打标签配置" not in config_block
    assert "清除打标签 API 密钥" not in config_block
    assert "如需修改，请编辑上方本机配置文件后刷新页面" in config_block


def test_tagging_config_loads_before_paper_batch_action() -> None:
    page = PAGE.read_text(encoding="utf-8")
    footer = page.rsplit("st.set_page_config", 1)[1]
    assert footer.index("_render_local_tagging_api_config()") < footer.index(
        "_render_paper_list(service)"
    )
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest tests/test_question_bank_ai_tagging_ui.py tests/test_question_bank_tagging_config_state.py -q`

Expected: UI-contract tests fail because the page still initializes fields conditionally and renders editable save/clear controls.

- [ ] **Step 3: Wire fresh profile loading, read-only widgets, and disk-based validation**

Import the Task 1 helpers with the runtime function aliased to avoid colliding with the existing page helper:

```python
from pages_shared.question_bank_tagging_config_state import (
    missing_tagging_config_fields,
    replace_tagging_config_state,
    tagging_runtime_limits as profile_tagging_runtime_limits,
)
```

Add the disk loader and field labels, then replace the complete configuration rendering and validation block with this implementation:

```python
TAGGING_CONFIG_FIELD_LABELS = {
    "tagging_api_key": "API Key",
    "tagging_base_url": "Base URL",
    "tagging_model": "模型名称",
}


def _load_local_tagging_profile() -> tuple[Any, dict[str, Any], str | None]:
    from api_profiles import get_api_profile_store

    profile_store = get_api_profile_store()
    try:
        profiles = profile_store.load()
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to load machine-local tagging API profile")
        return profile_store, {}, str(exc)
    return profile_store, profiles[-1] if profiles else {}, None


def _render_local_tagging_api_config() -> None:
    import os

    profile_store, saved_profile, load_error = _load_local_tagging_profile()
    replace_tagging_config_state(st.session_state, saved_profile)

    os.environ["QUESTION_BANK_TAGGING_API_KEY"] = st.session_state.tagging_api_key_input
    os.environ["QUESTION_BANK_TAGGING_BASE_URL"] = st.session_state.tagging_base_url_input
    os.environ["QUESTION_BANK_TAGGING_MODEL"] = st.session_state.tagging_model_input
    os.environ["QUESTION_BANK_TAGGING_MAX_WORKERS"] = str(
        st.session_state.tagging_max_workers_input
    )
    os.environ["QUESTION_BANK_TAGGING_REQUESTS_PER_MINUTE"] = str(
        st.session_state.tagging_requests_per_minute_input
    )
    os.environ["QUESTION_BANK_TAGGING_THINKING"] = (
        "1" if st.session_state.tagging_thinking_input else "0"
    )
    review_enabled = bool(
        st.session_state.tagging_review_enabled_input
        and _cell_text(st.session_state.tagging_review_model_input)
    )
    os.environ["QUESTION_BANK_TAGGING_REVIEW_MODEL"] = (
        st.session_state.tagging_review_model_input if review_enabled else ""
    )
    os.environ["QUESTION_BANK_TAGGING_REVIEW_API_KEY"] = (
        st.session_state.tagging_review_api_key_input if review_enabled else ""
    )
    os.environ["QUESTION_BANK_TAGGING_REVIEW_BASE_URL"] = (
        st.session_state.tagging_review_base_url_input if review_enabled else ""
    )

    missing_fields = missing_tagging_config_fields(saved_profile)
    with st.sidebar:
        st.markdown("#### 🏷️ 题库打标签大模型 API 配置")
        st.caption(f"本机独立配置：{profile_store.path}")
        if load_error:
            st.error(f"读取本机打标签配置失败：{load_error}")
        elif missing_fields:
            missing_labels = "、".join(
                TAGGING_CONFIG_FIELD_LABELS[item] for item in missing_fields
            )
            st.warning(f"本机打标签配置缺少：{missing_labels}")
        else:
            st.info("已从本机配置加载打标签 API；批量打标签会使用这里显示的参数。")
        st.caption("如需修改，请编辑上方本机配置文件后刷新页面。")

        st.text_input(
            "打标签 API Key",
            type="password",
            key="tagging_api_key_input",
            disabled=True,
        )
        st.text_input(
            "打标签 API Base URL",
            key="tagging_base_url_input",
            disabled=True,
        )
        st.text_input(
            "打标签模型名称",
            key="tagging_model_input",
            disabled=True,
        )
        st.number_input(
            "打标签最大并发数",
            min_value=1,
            max_value=64,
            step=1,
            key="tagging_max_workers_input",
            disabled=True,
        )
        st.number_input(
            "打标签 RPM 上限",
            min_value=1,
            step=10,
            key="tagging_requests_per_minute_input",
            disabled=True,
        )
        st.checkbox(
            "开启 Thinking 模式",
            key="tagging_thinking_input",
            disabled=True,
        )
        st.divider()
        st.checkbox(
            "启用低置信度复核模型",
            key="tagging_review_enabled_input",
            disabled=True,
        )
        st.text_input(
            "复核模型 API Key",
            type="password",
            key="tagging_review_api_key_input",
            disabled=True,
        )
        st.text_input(
            "复核模型 API Base URL",
            key="tagging_review_base_url_input",
            disabled=True,
        )
        st.text_input(
            "复核模型名称",
            key="tagging_review_model_input",
            disabled=True,
        )


def _tagging_config_ready() -> bool:
    _, profile, load_error = _load_local_tagging_profile()
    return not load_error and not missing_tagging_config_fields(profile)


def _tagging_runtime_limits() -> tuple[int, int]:
    _, profile, load_error = _load_local_tagging_profile()
    if load_error:
        return 4, 1000
    return profile_tagging_runtime_limits(profile)


def _warn_missing_tagging_config() -> None:
    _, profile, load_error = _load_local_tagging_profile()
    if load_error:
        st.error(f"读取本机打标签配置失败：{load_error}")
        return
    missing_fields = missing_tagging_config_fields(profile)
    if missing_fields:
        missing_labels = "、".join(
            TAGGING_CONFIG_FIELD_LABELS[item] for item in missing_fields
        )
        st.warning(f"本机打标签配置缺少：{missing_labels}")
        return
    st.warning("本机打标签配置尚未就绪，请刷新页面后重试。")
```

Remove the existing `_render_local_tagging_api_config()` call from `_render_questions_v2()`. In the page footer, call it once immediately before `_render_import_area(service, raw_papers_dir)`, which is also before `_render_paper_list(service)` and every batch-tag action.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/test_question_bank_ai_tagging_ui.py tests/test_question_bank_tagging_config_state.py tests/test_api_profile_store.py tests/test_question_bank_import_dialog_ui.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: all focused tests pass.

### Task 3: Verification and review

**Files:**
- No production file additions beyond Tasks 1–2.

**Interfaces:**
- Verifies: configuration correctness, syntax, regressions, rendered read-only state, and batch-action readiness.

- [ ] **Step 1: Run syntax and broader regression checks**

Run: `python -m compileall pages pages_shared question_bank tests`

Run: `python -m pytest tests/test_question_bank_ai_tagging_ui.py tests/test_question_bank_tagging_config_state.py tests/test_api_profile_store.py tests/test_question_bank_ai_tagging_quality.py tests/test_question_bank_import_dialog_ui.py tests/test_grading_paper_skill_workflow_ui.py -q`

Expected: compilation succeeds and all selected tests pass.

- [ ] **Step 2: Verify the Streamlit page in the in-app browser**

Start the existing app with its repository-supported command, open the question-bank page, and verify:

- the page title and question-bank content render without an error overlay;
- the sidebar reads the machine-local profile path;
- API Key is populated but masked;
- Base URL, model, workers, RPM, Thinking, and review fields display the file values and are disabled;
- no save or clear button appears;
- the batch-tag button no longer reports missing configuration when the local profile is complete;
- no relevant console error or warning is emitted.

- [ ] **Step 3: Review the final diff**

Run: `git diff --check`

Run: `git diff --stat`

Run: `git status --short`

Expected: only the helper, its tests, the question-bank page, and the UI-contract test are changed; user data and databases are untouched in the implementation worktree.
