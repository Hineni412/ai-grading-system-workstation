from __future__ import annotations

import importlib
import importlib.util


MODULE_NAME = "pages_shared.question_bank_import_state"


def _import_state_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "question bank import state helper must exist"
    return importlib.import_module(MODULE_NAME)


def test_replace_import_scan_rows_clears_stale_editor_and_message() -> None:
    state_module = _import_state_module()
    state = {
        state_module.SCAN_ROWS_KEY: [{"文件": "old.docx"}],
        state_module.SCAN_EDITOR_KEY: {"edited_rows": {0: {"导入": False}}},
        state_module.IMPORT_SUCCESS_MESSAGE_KEY: "old success",
        "qb_tag_after_import": True,
    }
    new_rows = [{"文件": "new.docx", "导入": True}]

    state_module.replace_import_scan_rows(state, new_rows)

    assert state[state_module.SCAN_ROWS_KEY] == new_rows
    assert state_module.SCAN_EDITOR_KEY not in state
    assert state_module.IMPORT_SUCCESS_MESSAGE_KEY not in state
    assert state["qb_tag_after_import"] is True


def test_clear_import_dialog_state_only_removes_transient_import_data() -> None:
    state_module = _import_state_module()
    state = {
        state_module.SCAN_ROWS_KEY: [{"文件": "pending.docx"}],
        state_module.SCAN_EDITOR_KEY: {"edited_rows": {}},
        state_module.IMPORT_SUCCESS_MESSAGE_KEY: "old success",
        "qb_tag_after_import": True,
        "qb_skip_tagged_after_import": True,
        "unrelated": "keep",
    }

    state_module.clear_import_dialog_state(state)

    assert state_module.SCAN_ROWS_KEY not in state
    assert state_module.SCAN_EDITOR_KEY not in state
    assert state_module.IMPORT_SUCCESS_MESSAGE_KEY not in state
    assert state["qb_tag_after_import"] is True
    assert state["qb_skip_tagged_after_import"] is True
    assert state["unrelated"] == "keep"
