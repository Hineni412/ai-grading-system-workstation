from __future__ import annotations

from typing import Any


def remember_saved_config_for_new_session(
    state: Any,
    *,
    selected_session_id: int | None,
    rubric_path: str,
    answer_key_path: str,
) -> bool:
    if selected_session_id is not None:
        clear_pending_config_for_new_session(state)
        return False
    state["latest_rubric_path"] = str(rubric_path)
    state["latest_answer_path"] = str(answer_key_path)
    return True


def clear_pending_config_for_new_session(state: Any) -> None:
    state["latest_rubric_path"] = ""
    state["latest_answer_path"] = ""
