from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v2 as components_v2


_ASSET_DIR = Path(__file__).resolve().parent / "components" / "answer_region_editor"
_EDITOR_HTML = (_ASSET_DIR / "editor.html").read_text(encoding="utf-8")
_EDITOR_CSS = (_ASSET_DIR / "editor.css").read_text(encoding="utf-8")
_EDITOR_JS = (_ASSET_DIR / "editor.js").read_text(encoding="utf-8")

_answer_region_editor = components_v2.component(
    "answer_region_editor",
    html=_EDITOR_HTML,
    css=_EDITOR_CSS,
    js=_EDITOR_JS,
)

_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@st.cache_data(show_spinner=False)
def load_template_image_data_url(path_value: str, mtime_ns: int) -> str:
    """Load a supported template image as a data URL.

    ``mtime_ns`` is intentionally part of the cached function signature so a
    changed image path is re-read without manually clearing Streamlit's cache.
    """
    path = Path(path_value)
    suffix = path.suffix.lower()
    try:
        mime = _IMAGE_MIME_BY_SUFFIX[suffix]
    except KeyError:
        display_suffix = suffix or "<none>"
        raise ValueError(
            f"Unsupported template image format: {display_suffix}. "
            "Expected .png, .jpg, or .jpeg."
        ) from None

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_answer_region_editor_data(
    *,
    session_id: int,
    front_image_data_url: str | None,
    back_image_data_url: str | None,
    image_sizes: dict[str, Any],
    editor_state: dict[str, Any],
    automatic_candidates: list[str] | tuple[str, ...],
    manual_question_options: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation_issues: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    draft_revision: int,
    save_status: str,
    drawer_open_requested: bool,
) -> dict[str, Any]:
    """Build an isolated, JSON-native payload for the editor component."""
    payload = {
        "session_id": session_id,
        "front_image_data_url": front_image_data_url,
        "back_image_data_url": back_image_data_url,
        "image_sizes": image_sizes,
        "editor_state": editor_state,
        "automatic_candidates": automatic_candidates,
        "manual_question_options": manual_question_options,
        "validation_issues": validation_issues,
        "draft_revision": draft_revision,
        "save_status": save_status,
        "drawer_open_requested": drawer_open_requested,
    }
    return json.loads(json.dumps(payload))


def render_answer_region_editor(*, key: str, data: dict[str, Any]) -> Any:
    """Mount the answer region editor and return its Streamlit component result."""
    return _answer_region_editor(
        key=key,
        data=data,
        default={"editor_state": None},
        height=860,
        on_editor_state_change=lambda: None,
        on_finish_requested_change=lambda: None,
        on_exit_requested_change=lambda: None,
    )
