from __future__ import annotations

from typing import Any


SOURCE_STATE_KEYS = {
    "latest_source_paper_path": "latest_confirmed_source_paper_path",
    "latest_source_paper_sha256": "latest_confirmed_source_paper_sha256",
    "latest_source_paper_name": "latest_confirmed_source_paper_name",
}


def remember_saved_config_for_new_session(
    state: Any,
    *,
    selected_session_id: int | None,
    rubric_path: str,
    answer_key_path: str,
    source_paper_path: str = "",
    source_paper_sha256: str = "",
    source_paper_name: str = "",
    settings_store: Any | None = None,
) -> bool:
    state["latest_rubric_path"] = str(rubric_path)
    state["latest_answer_path"] = str(answer_key_path)
    source_values = {
        "latest_source_paper_path": str(source_paper_path or ""),
        "latest_source_paper_sha256": str(source_paper_sha256 or ""),
        "latest_source_paper_name": str(source_paper_name or ""),
    }
    for state_key, value in source_values.items():
        if value:
            state[state_key] = value
    if settings_store is not None:
        settings_store.set_app_setting("latest_confirmed_rubric_path", str(rubric_path))
        settings_store.set_app_setting("latest_confirmed_answer_path", str(answer_key_path))
        for state_key, setting_key in SOURCE_STATE_KEYS.items():
            settings_store.set_app_setting(setting_key, source_values[state_key])
    return selected_session_id is None


def clear_pending_config_for_new_session(state: Any, settings_store: Any | None = None) -> None:
    state.pop("generated_config_payload", None)
    for state_key, setting_key in SOURCE_STATE_KEYS.items():
        state.pop(state_key, None)
        if settings_store is not None:
            settings_store.set_app_setting(setting_key, "")


def restore_persistent_config_state(state: Any, settings_store: Any) -> None:
    latest_rubric_path = settings_store.get_app_setting("latest_confirmed_rubric_path", "") or ""
    latest_answer_path = settings_store.get_app_setting("latest_confirmed_answer_path", "") or ""
    source_values = {
        state_key: settings_store.get_app_setting(setting_key, "") or ""
        for state_key, setting_key in SOURCE_STATE_KEYS.items()
    }
    raw_session_id = settings_store.get_app_setting("last_selected_session_id", "") or ""
    if not latest_rubric_path or not latest_answer_path or not raw_session_id:
        active_sessions = settings_store.list_grading_sessions(include_deleted=False)
        latest_session = active_sessions[0] if active_sessions else None
        if latest_session:
            latest_rubric_path = latest_rubric_path or str(latest_session.get("rubric_path") or "")
            latest_answer_path = latest_answer_path or str(latest_session.get("answer_key_path") or "")
            raw_session_id = raw_session_id or str(latest_session.get("id") or "")
            source_values["latest_source_paper_path"] = (
                source_values["latest_source_paper_path"]
                or str(latest_session.get("source_paper_path") or "")
            )
            source_values["latest_source_paper_sha256"] = (
                source_values["latest_source_paper_sha256"]
                or str(latest_session.get("source_paper_sha256") or "")
            )
            settings_store.set_app_setting("latest_confirmed_rubric_path", latest_rubric_path)
            settings_store.set_app_setting("latest_confirmed_answer_path", latest_answer_path)
            settings_store.set_app_setting("last_selected_session_id", raw_session_id)

    if not state.get("latest_rubric_path"):
        state["latest_rubric_path"] = latest_rubric_path
    if not state.get("latest_answer_path"):
        state["latest_answer_path"] = latest_answer_path
    for state_key, value in source_values.items():
        if value and not state.get(state_key):
            state[state_key] = value
    if "selected_session_id" in state:
        return
    try:
        session_id = int(raw_session_id)
    except (TypeError, ValueError):
        return
    session = settings_store.get_grading_session(session_id)
    if session and int(session.get("is_deleted") or 0) == 0:
        state["selected_session_id"] = session_id
