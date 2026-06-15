from __future__ import annotations

import ast
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import answer_region_focus_page as focus_module
from answer_region_draft_service import AnswerRegionDraftService
from answer_region_focus_page import (
    EditorEventResult,
    merge_component_editor_state,
    process_editor_state,
    read_component_result_value,
)


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


def test_component_result_reader_supports_attributes_and_dict_test_doubles() -> None:
    attribute_result = SimpleNamespace(editor_state={"revision": 2})
    dict_result = {"editor_state": {"revision": 3}}

    assert read_component_result_value(attribute_result, "editor_state") == {"revision": 2}
    assert read_component_result_value(dict_result, "editor_state") == {"revision": 3}
    assert read_component_result_value(attribute_result, "missing", "fallback") == "fallback"
    assert read_component_result_value(None, "editor_state") is None


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


def test_repeated_component_state_keeps_first_sequential_binding() -> None:
    previous = [_region("one", question_id="Q1")]
    returned_state = _event([*previous, _region("new", y=160)], revision=1)
    initial_echo = {"handled_revision": 0}

    first = _process(
        merge_component_editor_state(returned_state, initial_echo),
        previous_regions=previous,
        candidates=["Q1", "Q2"],
    )
    second = _process(
        merge_component_editor_state(returned_state, first.editor_state),
        previous_regions=first.regions,
        candidates=["Q1", "Q2"],
    )

    assert first.regions[1]["mapped_question_id"] == "Q2"
    assert first.regions[1]["mapping_status"] == "auto"
    assert second.handled is False
    assert second.regions[1]["mapped_question_id"] == "Q2"


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


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state: dict[str, Any] = {"region_focus_session_id": 17}
        self.rerun_count = 0
        self.errors: list[str] = []
        self.component_mount_count = 0

    def __getattr__(self, name: str) -> Any:
        if name == "button":
            return lambda *args, **kwargs: False
        if name == "expander":
            return lambda *args, **kwargs: nullcontext()
        if name == "rerun":
            return self._rerun
        if name == "error":
            return lambda message, *args, **kwargs: self.errors.append(str(message))
        return lambda *args, **kwargs: None

    def _rerun(self) -> None:
        self.rerun_count += 1


def _render_focus_with_component_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    component_result: object,
    *,
    draft_status: str = "missing",
) -> tuple[_FakeStreamlit, list[dict[str, Any]], list[list[dict[str, Any]]], Path]:
    fake_st = _FakeStreamlit()
    session_dir = tmp_path / "templates" / "session_17"
    session_dir.mkdir(parents=True)
    front_path = session_dir / "front.png"
    back_path = session_dir / "back.png"
    front_path.write_bytes(b"front")
    back_path.write_bytes(b"back")
    saved_drafts: list[dict[str, Any]] = []
    committed_regions: list[list[dict[str, Any]]] = []

    class FakeDB:
        def get_grading_session(self, session_id: int) -> dict[str, Any]:
            return {"id": session_id, "session_name": "focus", "rubric_path": None}

        def get_session_template(self, session_id: int) -> dict[str, Any]:
            return {
                "id": 9,
                "session_id": session_id,
                "front_template_path": str(front_path),
                "back_template_path": str(back_path),
                "regions_snapshot_pending": 0,
            }

        def list_answer_regions(self, session_id: int) -> list[dict[str, Any]]:
            return []

    class FakeDraftService:
        def __init__(self, path: Path) -> None:
            self.draft_path = path / "region_draft.json"

        def compute_template_fingerprint(self, front: Path, back: Path) -> str:
            return "current-fingerprint"

        def load(self, *, expected_template_fingerprint: str) -> SimpleNamespace:
            return SimpleNamespace(status="missing", draft=None, quarantined_path=None)

        def save(self, **kwargs: Any) -> None:
            saved_drafts.append(kwargs)

        def discard(self) -> None:
            raise AssertionError("incompatible or uploaded draft must not be discarded automatically")

    class FakeCommitService:
        def __init__(self, db: Any, path: Path, draft_service: Any) -> None:
            pass

        def commit(self, **kwargs: Any) -> SimpleNamespace:
            committed_regions.append(kwargs["regions"])
            return SimpleNamespace(committed=True, snapshot_pending=False)

    monkeypatch.setattr(focus_module, "st", fake_st)
    if draft_status == "incompatible":
        draft_service = AnswerRegionDraftService(session_dir)
        old_fingerprint = draft_service.compute_template_fingerprint(front_path, back_path)
        draft_service.save(
            session_id=17,
            template_fingerprint=old_fingerprint,
            revision=1,
            regions=[_region("old", question_id="Q1")],
        )
        back_path.write_bytes(b"changed-back")
    else:
        monkeypatch.setattr(focus_module, "AnswerRegionDraftService", FakeDraftService)
    monkeypatch.setattr(focus_module, "AnswerRegionCommitService", FakeCommitService)
    monkeypatch.setattr(
        focus_module,
        "load_question_binding_catalog",
        lambda _path: SimpleNamespace(automatic_candidates=("Q1",), manual_options=()),
    )
    monkeypatch.setattr(
        focus_module,
        "_load_images",
        lambda _paths: (
            {"front": "front-data", "back": "back-data"},
            IMAGE_SIZES,
        ),
    )

    def fake_render_answer_region_editor(**kwargs: Any) -> object:
        fake_st.component_mount_count += 1
        return component_result

    monkeypatch.setattr(focus_module, "render_answer_region_editor", fake_render_answer_region_editor)

    focus_module.render_answer_region_focus_page(
        FakeDB(),
        session_id=17,
        templates_dir=tmp_path / "templates",
    )
    return fake_st, saved_drafts, committed_regions, session_dir


def test_attribute_component_finish_autosaves_then_commits_fresh_regions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    returned_state = _event([_region("fresh")], revision=2)
    component_result = SimpleNamespace(
        editor_state=returned_state,
        finish_requested={"revision": 2},
    )

    _fake_st, saved_drafts, committed_regions, _session_dir = _render_focus_with_component_result(
        monkeypatch,
        tmp_path,
        component_result,
    )

    assert saved_drafts[0]["revision"] == 2
    assert saved_drafts[0]["regions"][0]["region_uuid"] == "fresh"
    assert committed_regions == [saved_drafts[0]["regions"]]


def test_attribute_component_exit_autosaves_latest_draft_before_leaving_focus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    component_result = SimpleNamespace(
        editor_state=_event([_region("latest")], revision=3),
        exit_requested={"revision": 3},
    )

    fake_st, saved_drafts, committed_regions, _session_dir = _render_focus_with_component_result(
        monkeypatch,
        tmp_path,
        component_result,
    )

    assert saved_drafts[0]["regions"][0]["region_uuid"] == "latest"
    assert committed_regions == []
    assert "region_focus_session_id" not in fake_st.session_state
    assert fake_st.rerun_count == 1


def test_changed_template_fingerprint_blocks_focus_entry_and_preserves_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mounted = object()

    _fake_st, saved_drafts, committed_regions, session_dir = _render_focus_with_component_result(
        monkeypatch,
        tmp_path,
        mounted,
        draft_status="incompatible",
    )

    assert saved_drafts == []
    assert committed_regions == []
    assert _fake_st.component_mount_count == 0
    assert _fake_st.errors
    draft_service = AnswerRegionDraftService(session_dir)
    current_fingerprint = draft_service.compute_template_fingerprint(
        session_dir / "front.png",
        session_dir / "back.png",
    )
    assert draft_service.load(expected_template_fingerprint=current_fingerprint).status == "incompatible"


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


def test_template_upload_preserves_primary_editor_draft_and_legacy_cache_state() -> None:
    module = ast.parse(WEB_APP.read_text(encoding="utf-8"))
    config = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_config_and_session_tab"
    )
    upload_block = next(
        node
        for node in ast.walk(config)
        if isinstance(node, ast.If)
        and "detect_regions_btn_" in ast.unparse(node.test)
    )
    upload_source = ast.unparse(upload_block)
    formatted_values = [
        ast.unparse(node)
        for node in ast.walk(upload_block)
        if isinstance(node, ast.JoinedStr)
    ]

    assert "region_draft.json" not in upload_source
    assert ".discard()" not in upload_source
    assert not any(value.startswith("f'regions_") for value in formatted_values)
    assert not any(value.startswith("f'sel_region_idx_") for value in formatted_values)
    assert not any(value.startswith("f'region_canvas_version_") for value in formatted_values)


def test_legacy_editor_gate_accepts_only_trimmed_one() -> None:
    module = ast.parse(WEB_APP.read_text(encoding="utf-8"))
    config = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_config_and_session_tab"
    )
    legacy_gate = next(
        node
        for node in ast.walk(config)
        if isinstance(node, ast.If) and "AI_REGION_EDITOR_LEGACY" in ast.unparse(node.test)
    )

    assert ast.unparse(legacy_gate.test) == "os.getenv('AI_REGION_EDITOR_LEGACY', '').strip() == '1'"
