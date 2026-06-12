from __future__ import annotations

from typing import Any


def remember_saved_config_for_new_session(
    state: Any,
    *,
    selected_session_id: int | None,
    rubric_path: str,
    answer_key_path: str,
    settings_store: Any | None = None,
) -> bool:
    state["latest_rubric_path"] = str(rubric_path)
    state["latest_answer_path"] = str(answer_key_path)
    if settings_store is not None:
        settings_store.set_app_setting("latest_confirmed_rubric_path", str(rubric_path))
        settings_store.set_app_setting("latest_confirmed_answer_path", str(answer_key_path))
    return selected_session_id is None


def clear_pending_config_for_new_session(state: Any) -> None:
    state.pop("generated_config_payload", None)


def restore_persistent_config_state(state: Any, settings_store: Any) -> None:
    latest_rubric_path = settings_store.get_app_setting("latest_confirmed_rubric_path", "") or ""
    latest_answer_path = settings_store.get_app_setting("latest_confirmed_answer_path", "") or ""
    raw_session_id = settings_store.get_app_setting("last_selected_session_id", "") or ""
    if not latest_rubric_path or not latest_answer_path or not raw_session_id:
        active_sessions = settings_store.list_grading_sessions(include_deleted=False)
        latest_session = active_sessions[0] if active_sessions else None
        if latest_session:
            latest_rubric_path = latest_rubric_path or str(latest_session.get("rubric_path") or "")
            latest_answer_path = latest_answer_path or str(latest_session.get("answer_key_path") or "")
            raw_session_id = raw_session_id or str(latest_session.get("id") or "")
            settings_store.set_app_setting("latest_confirmed_rubric_path", latest_rubric_path)
            settings_store.set_app_setting("latest_confirmed_answer_path", latest_answer_path)
            settings_store.set_app_setting("last_selected_session_id", raw_session_id)

    if not state.get("latest_rubric_path"):
        state["latest_rubric_path"] = latest_rubric_path
    if not state.get("latest_answer_path"):
        state["latest_answer_path"] = latest_answer_path
    if "selected_session_id" in state:
        return
    try:
        session_id = int(raw_session_id)
    except (TypeError, ValueError):
        return
    session = settings_store.get_grading_session(session_id)
    if session and int(session.get("is_deleted") or 0) == 0:
        state["selected_session_id"] = session_id
