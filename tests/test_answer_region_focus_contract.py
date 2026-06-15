from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

from answer_region_focus_page import EditorEventResult, process_editor_state


ROOT = Path(__file__).resolve().parents[1]
FOCUS_PAGE = ROOT / "answer_region_focus_page.py"
WEB_APP = ROOT / "web_app.py"
IMAGE_SIZES = {"front": (1000, 1400), "back": (1000, 1400)}


def _region(
    region_uuid: str,
    *,
    question_id: str | None = None,
    page: str = "front",
    y: int = 20,
    multi_region_confirmed: bool = False,
) -> dict[str, Any]:
    return {
        "region_uuid": region_uuid,
        "page": page,
        "x": 20,
        "y": y,
        "w": 200,
        "h": 100,
        "mapped_question_id": question_id,
        "mapping_status": "manual" if question_id else "unbound",
        "is_confirmed": bool(question_id),
        "multi_region_confirmed": multi_region_confirmed,
    }


def _event(
    regions: list[dict[str, Any]],
    *,
    revision: int = 1,
    handled_revision: int | None = None,
    operation: str = "regions_changed",
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "revision": revision,
        "last_operation": operation,
        "active_page": "back",
        "regions": regions,
        "undo_stack": [[_region("older", question_id="Q9")]],
        "redo_stack": [[_region("newer", question_id="Q10")]],
        "drawer_open": False,
        "complete_state_marker": {"keep": True},
    }
    if handled_revision is not None:
        state["handled_revision"] = handled_revision
    return state


def _process(
    editor_state: dict[str, Any],
    *,
    previous_regions: list[dict[str, Any]] | None = None,
    candidates: list[str] | None = None,
) -> EditorEventResult:
    return process_editor_state(
        editor_state,
        previous_regions=previous_regions or [],
        question_candidates=candidates or ["Q1", "Q2"],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )


def test_editor_event_result_is_frozen_with_exact_public_fields() -> None:
    assert [field.name for field in fields(EditorEventResult)] == [
        "regions",
        "active_page",
        "revision",
        "drawer_open_requested",
        "handled",
        "editor_state",
    ]

    result = _process(_event([_region("new")]))
    with pytest.raises(FrozenInstanceError):
        result.handled = False  # type: ignore[misc]


def test_new_unbound_region_gets_first_candidate_without_drawer_and_preserves_history() -> None:
    editor_state = _event([_region("new")])

    result = _process(editor_state)

    assert result.handled is True
    assert result.regions[0]["mapped_question_id"] == "Q1"
    assert result.regions[0]["mapping_status"] == "auto"
    assert result.drawer_open_requested is False
    assert result.active_page == "back"
    assert result.editor_state["undo_stack"] == editor_state["undo_stack"]
    assert result.editor_state["redo_stack"] == editor_state["redo_stack"]
    assert result.editor_state["drawer_open"] is False
    assert result.editor_state["complete_state_marker"] == {"keep": True}
    assert result.editor_state["handled_revision"] == 1
    assert result.editor_state["regions"] == result.regions


def test_third_new_region_after_q1_q2_stays_unbound_and_requests_drawer() -> None:
    previous = [_region("one", question_id="Q1"), _region("two", question_id="Q2", y=150)]
    result = _process(
        _event([*previous, _region("three", y=280)]),
        previous_regions=previous,
    )

    assert result.regions[2]["mapped_question_id"] is None
    assert result.regions[2]["mapping_status"] == "unbound"
    assert result.drawer_open_requested is True


def test_same_revision_already_handled_is_not_processed_again() -> None:
    result = _process(_event([_region("new")], revision=4, handled_revision=4))

    assert result.handled is False
    assert result.revision == 4


def test_existing_unbound_uuid_is_not_newly_auto_bound() -> None:
    previous = [_region("existing-unbound")]
    result = _process(
        _event([_region("existing-unbound"), _region("new", y=160)]),
        previous_regions=previous,
    )

    assert result.regions[0]["mapped_question_id"] is None
    assert result.regions[0]["mapping_status"] == "unbound"
    assert result.regions[1]["mapped_question_id"] == "Q1"
    assert result.drawer_open_requested is True


def test_unconfirmed_multi_region_requests_drawer() -> None:
    previous = [_region("one", question_id="Q1")]
    result = _process(
        _event(
            [
                _region("one", question_id="Q1"),
                _region("two", question_id="Q1", y=160),
            ],
            operation="mapping_changed",
        ),
        previous_regions=previous,
    )

    assert result.drawer_open_requested is True


def test_focus_page_source_keeps_formal_writes_behind_commit_service() -> None:
    source = FOCUS_PAGE.read_text(encoding="utf-8")

    assert "AnswerRegionCommitService" in source
    assert ".commit(" in source
    assert ".retry_pending_snapshot(" in source
    for forbidden in (
        "db.save_answer_regions(",
        "db.replace_answer_regions_atomic(",
        "db.add_answer_region(",
        "db.delete_answer_region(",
        "db.update_answer_region_bbox(",
        "db.bulk_update_answer_region_mapping(",
        "db.mark_template_confirmed(",
    ):
        assert forbidden not in source


def test_focus_page_source_handles_all_draft_states_and_focus_contracts() -> None:
    source = FOCUS_PAGE.read_text(encoding="utf-8")

    for status in ("missing", "compatible", "incompatible", "corrupt"):
        assert f'"{status}"' in source
    assert "region_focus_session_id" in source
    assert "load_template_image_data_url" in source
    assert "load_question_binding_catalog" in source
    assert "focus-answer-region-" in source
    assert "handled_revision" in source
    assert "drawer_open_requested" in source
    assert "st.rerun()" in source


def test_web_app_routes_focus_before_tabs_and_gates_v3_as_legacy_fallback() -> None:
    module = ast.parse(WEB_APP.read_text(encoding="utf-8"))
    imports = [
        node
        for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module == "answer_region_focus_page"
    ]
    assert any(alias.name == "render_answer_region_focus_page" for node in imports for alias in node.names)

    main = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    main_source = ast.unparse(main)
    assert main_source.index("render_answer_region_focus_page") < main_source.index("st.tabs")

    config = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_config_and_session_tab"
    )
    config_source = ast.unparse(config)
    assert "进入专注题框标定" in config_source
    assert "旧版题框编辑器（紧急回退）" in config_source
    assert "AI_REGION_EDITOR_LEGACY" in config_source
    assert config_source.count("_render_region_editor_v3") == 1
