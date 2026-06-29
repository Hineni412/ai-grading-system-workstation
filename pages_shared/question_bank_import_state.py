from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any


SCAN_ROWS_KEY = "qb_scan_rows"
SCAN_EDITOR_KEY = "qb_scan_editor"
IMPORT_SUCCESS_MESSAGE_KEY = "qb_import_success_message"


def replace_import_scan_rows(
    state: MutableMapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
) -> None:
    """Replace the pending import selection without retaining stale widget state."""

    state[SCAN_ROWS_KEY] = [dict(row) for row in rows]
    state.pop(SCAN_EDITOR_KEY, None)
    state.pop(IMPORT_SUCCESS_MESSAGE_KEY, None)


def clear_import_dialog_state(state: MutableMapping[str, Any]) -> None:
    """Discard transient import data while preserving the user's tagging preferences."""

    state.pop(SCAN_ROWS_KEY, None)
    state.pop(SCAN_EDITOR_KEY, None)
    state.pop(IMPORT_SUCCESS_MESSAGE_KEY, None)
