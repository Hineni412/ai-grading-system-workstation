from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image

from answer_region_commit_service import AnswerRegionCommitService
from answer_region_draft_service import AnswerRegionDraftService
from answer_region_editor_component import (
    build_answer_region_editor_data,
    load_template_image_data_url,
    render_answer_region_editor,
)
from answer_region_models import (
    load_question_binding_catalog,
    normalize_regions,
    validate_regions,
)
from path_manager import resolve_stored_file_path


_COMPLETED_OPERATIONS = frozenset(
    {"regions_changed", "mapping_changed", "multi_region_confirmed"}
)
_DRAWER_ISSUES = frozenset({"unbound_question", "unconfirmed_multi_region"})


@dataclass(frozen=True)
class EditorEventResult:
    regions: list[dict[str, Any]]
    active_page: str
    revision: int
    drawer_open_requested: bool
    handled: bool
    editor_state: dict[str, Any]


def read_component_result_value(
    component_result: object,
    name: str,
    default: Any = None,
) -> Any:
    """Read a Components v2 result attribute or a dict-backed test double."""
    if isinstance(component_result, dict):
        return component_result.get(name, default)
    return getattr(component_result, name, default)


def merge_component_editor_state(
    returned_state: dict[str, Any],
    echoed_state: dict[str, Any],
) -> dict[str, Any]:
    """Carry Python's handled revision into repeated Components v2 state events."""
    merged = deepcopy(returned_state)
    if "handled_revision" not in merged:
        handled_revision = _optional_nonnegative_int(echoed_state.get("handled_revision"))
        if handled_revision is not None:
            merged["handled_revision"] = handled_revision
    return merged


def process_editor_state(
    editor_state: dict[str, Any],
    *,
    previous_regions: list[dict[str, Any]],
    question_candidates: list[str] | tuple[str, ...],
    image_sizes: dict[str, tuple[int, int]],
    template_matches: bool,
) -> EditorEventResult:
    """Normalize and validate one completed editor operation without side effects."""
    state = deepcopy(editor_state) if isinstance(editor_state, dict) else {}
    revision = _nonnegative_int(state.get("revision"))
    active_page = "back" if state.get("active_page") == "back" else "front"
    previous = normalize_regions(previous_regions)
    handled_revision = _optional_nonnegative_int(state.get("handled_revision"))

    if (
        state.get("last_operation") not in _COMPLETED_OPERATIONS
        or (handled_revision is not None and revision <= handled_revision)
    ):
        state["regions"] = deepcopy(previous)
        state["active_page"] = active_page
        return EditorEventResult(
            regions=previous,
            active_page=active_page,
            revision=revision,
            drawer_open_requested=False,
            handled=False,
            editor_state=state,
        )

    incoming = state.get("regions")
    normalized = normalize_regions(incoming if isinstance(incoming, list) else [])
    normalized = _bind_only_new_unbound_regions(
        normalized,
        previous_regions=previous,
        question_candidates=question_candidates,
    )
    validation = validate_regions(
        normalized,
        image_sizes=image_sizes,
        template_matches=template_matches,
    )
    drawer_open_requested = any(issue.code in _DRAWER_ISSUES for issue in validation.issues)

    state["revision"] = revision
    state["handled_revision"] = revision
    state["active_page"] = active_page
    state["regions"] = deepcopy(normalized)
    state["undo_stack"] = deepcopy(state.get("undo_stack", []))
    state["redo_stack"] = deepcopy(state.get("redo_stack", []))
    state["drawer_open"] = bool(state.get("drawer_open", False))
    return EditorEventResult(
        regions=normalized,
        active_page=active_page,
        revision=revision,
        drawer_open_requested=drawer_open_requested,
        handled=True,
        editor_state=state,
    )


def render_answer_region_focus_page(
    db: Any,
    *,
    session_id: int,
    templates_dir: Path,
) -> None:
    _render_focus_css()
    st.title("专注题框标定")

    session_dir = Path(templates_dir) / f"session_{session_id}"
    draft_service = AnswerRegionDraftService(session_dir)
    commit_service = AnswerRegionCommitService(db, session_dir, draft_service)

    try:
        session = db.get_grading_session(session_id)
        template = db.get_session_template(session_id)
    except Exception:
        st.error("无法读取当前考试批改或样卷信息，请返回后重试。")
        _render_exit_button(session_id)
        return
    if not session:
        st.error("当前考试批改不存在，无法进入题框标定。")
        _render_exit_button(session_id)
        return
    if not template:
        st.error("当前考试尚未上传正反面样卷，无法进入题框标定。")
        _render_exit_button(session_id)
        return

    template_paths = _resolve_template_paths(template, session_dir, Path(templates_dir))
    missing_pages = [page for page, path in template_paths.items() if not path.is_file()]
    if missing_pages:
        st.error(f"样卷图片缺失：{', '.join(missing_pages)}。请返回并重新上传样卷。")
        _render_exit_button(session_id)
        return

    try:
        fingerprint = draft_service.compute_template_fingerprint(
            template_paths["front"], template_paths["back"]
        )
        image_data_urls, image_sizes = _load_images(template_paths)
    except Exception:
        st.error("样卷图片无法读取，题框编辑器未启动。请检查图片后重试。")
        _render_exit_button(session_id)
        return

    try:
        formal_regions = normalize_regions(db.list_answer_regions(session_id))
        draft_result = draft_service.load(expected_template_fingerprint=fingerprint)
    except Exception:
        st.error("无法读取题框草稿或正式题框，请返回后重试。")
        _render_exit_button(session_id)
        return

    if bool(template.get("regions_snapshot_pending")):
        st.warning("正式题框已提交，但本地快照仍待完成。")
        if st.button("重试生成待处理快照", type="primary", key=f"focus-retry-snapshot-{session_id}"):
            retry_result = commit_service.retry_pending_snapshot(session_id=session_id)
            if retry_result.snapshot_pending:
                st.error("快照仍未完成，请保留本页并稍后重试。")
            else:
                st.success("待处理快照已完成。")
                st.rerun()

    seed = _select_draft_seed(
        draft_result=draft_result,
        formal_regions=formal_regions,
        draft_service=draft_service,
        session_id=session_id,
        fingerprint=fingerprint,
    )
    if seed is None:
        _render_exit_button(session_id)
        return
    regions, draft_revision = seed

    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        search_roots=[session_dir, Path(templates_dir)],
    )
    catalog = load_question_binding_catalog(rubric_path)
    state_key = _editor_state_key(session_id, fingerprint)
    if state_key not in st.session_state:
        st.session_state[state_key] = {
            "revision": draft_revision,
            "handled_revision": draft_revision,
            "last_operation": None,
            "active_page": "front",
            "regions": deepcopy(regions),
            "undo_stack": [],
            "redo_stack": [],
            "drawer_open": False,
        }
    editor_state = deepcopy(st.session_state[state_key])
    regions = normalize_regions(editor_state.get("regions", regions))
    validation = validate_regions(regions, image_sizes=image_sizes, template_matches=True)
    issues = [asdict(issue) for issue in validation.issues]
    manual_options = [asdict(option) for option in catalog.manual_options]
    payload = build_answer_region_editor_data(
        session_id=session_id,
        front_image_data_url=image_data_urls["front"],
        back_image_data_url=image_data_urls["back"],
        image_sizes=image_sizes,
        editor_state=editor_state,
        automatic_candidates=list(catalog.automatic_candidates),
        manual_question_options=manual_options,
        validation_issues=issues,
        draft_revision=draft_revision,
        save_status="草稿已保存" if draft_result.status == "compatible" else "等待首次编辑",
        drawer_open_requested=any(issue["code"] in _DRAWER_ISSUES for issue in issues),
    )
    payload["images"] = {
        page: {"src": image_data_urls[page], "width": size[0], "height": size[1]}
        for page, size in image_sizes.items()
    }

    st.caption(f"当前考试：{session.get('session_name') or session_id}")
    component_value = render_answer_region_editor(
        key=f"focus-answer-region-{session_id}",
        data=payload,
    )

    handled_event = False
    returned_state = read_component_result_value(component_value, "editor_state")
    if isinstance(returned_state, dict):
        returned_state = merge_component_editor_state(returned_state, editor_state)
        event_result = process_editor_state(
            returned_state,
            previous_regions=regions,
            question_candidates=catalog.automatic_candidates,
            image_sizes=image_sizes,
            template_matches=True,
        )
        if event_result.handled:
            draft_service.save(
                session_id=session_id,
                template_fingerprint=fingerprint,
                revision=event_result.revision,
                regions=event_result.regions,
            )
            st.session_state[_draft_choice_key(session_id, fingerprint)] = "restore"
            st.session_state[state_key] = event_result.editor_state
            regions = event_result.regions
            handled_event = True

    if read_component_result_value(component_value, "exit_requested") is not None:
        _leave_focus_mode(session_id, fingerprint)
        return

    if read_component_result_value(component_value, "finish_requested") is not None:
        result = commit_service.commit(
            session_id=session_id,
            template_id=int(template["id"]),
            regions=regions,
            image_sizes=image_sizes,
            template_matches=True,
            expected_template_fingerprint=fingerprint,
        )
        if not result.committed:
            st.error("仍有题框未通过校验，请按右侧提示修正后再完成。")
        elif result.snapshot_pending:
            st.warning("题框已正式提交，但快照仍待完成。请使用上方重试操作。")
        else:
            st.session_state.pop(state_key, None)
            st.session_state.pop(_draft_choice_key(session_id, fingerprint), None)
            st.session_state.pop("region_focus_session_id", None)
            st.rerun()
        return

    if handled_event:
        st.rerun()


def _bind_only_new_unbound_regions(
    regions: list[dict[str, Any]],
    *,
    previous_regions: list[dict[str, Any]],
    question_candidates: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    result = [dict(region) for region in regions]
    previous_uuids = {str(region.get("region_uuid")) for region in previous_regions}
    used = {
        str(region["mapped_question_id"]).strip()
        for region in result
        if str(region.get("mapped_question_id") or "").strip()
    }
    candidates: list[str] = []
    for candidate in question_candidates:
        value = str(candidate or "").strip()
        if value and value != "__student_name__" and value not in candidates:
            candidates.append(value)

    for region in result:
        if str(region.get("region_uuid")) in previous_uuids:
            continue
        if region.get("mapped_question_id") is not None:
            continue
        candidate = next((value for value in candidates if value not in used), None)
        if candidate is None:
            region["mapping_status"] = "unbound"
            continue
        region["mapped_question_id"] = candidate
        region["mapping_status"] = "auto"
        used.add(candidate)
    return result


def _select_draft_seed(
    *,
    draft_result: Any,
    formal_regions: list[dict[str, Any]],
    draft_service: AnswerRegionDraftService,
    session_id: int,
    fingerprint: str,
) -> tuple[list[dict[str, Any]], int] | None:
    if draft_result.status == "missing":
        return formal_regions, 0
    if draft_result.status == "corrupt":
        quarantined = draft_result.quarantined_path
        label = quarantined.name if quarantined is not None else "隔离文件"
        st.warning(f"检测到损坏草稿，已隔离为 {label}；编辑器将从正式题框重新开始。")
        return formal_regions, 0
    if draft_result.status == "incompatible":
        st.error("现有草稿来自另一版样卷。为避免错位，当前不会启动编辑器。")
        with st.expander("查看不兼容草稿", expanded=False):
            st.json(draft_result.draft or {})
        if st.button("丢弃不兼容草稿", type="primary", key=f"focus-discard-incompatible-{session_id}"):
            draft_service.discard()
            st.session_state.pop(_editor_state_key(session_id, fingerprint), None)
            st.rerun()
        return None

    choice_key = _draft_choice_key(session_id, fingerprint)
    choice = st.session_state.get(choice_key)
    if choice not in {"restore", "formal"}:
        st.info("发现与当前样卷兼容的未完成草稿。请选择继续草稿，或重新载入正式题框。")
        restore_col, formal_col = st.columns(2)
        if restore_col.button("恢复草稿", type="primary", key=f"focus-restore-{session_id}"):
            st.session_state[choice_key] = "restore"
            st.rerun()
        if formal_col.button("重新载入正式题框", key=f"focus-reload-formal-{session_id}"):
            draft_service.discard()
            st.session_state[choice_key] = "formal"
            st.session_state.pop(_editor_state_key(session_id, fingerprint), None)
            st.rerun()
        return None
    if choice == "formal":
        return formal_regions, 0
    draft = draft_result.draft or {}
    return normalize_regions(draft.get("regions", [])), _nonnegative_int(draft.get("revision"))


def _resolve_template_paths(
    template: dict[str, Any],
    session_dir: Path,
    templates_dir: Path,
) -> dict[str, Path]:
    return {
        page: resolve_stored_file_path(
            template.get(f"{page}_template_path"),
            search_roots=[session_dir, templates_dir],
        )
        for page in ("front", "back")
    }


def _load_images(
    paths: dict[str, Path],
) -> tuple[dict[str, str], dict[str, tuple[int, int]]]:
    data_urls: dict[str, str] = {}
    sizes: dict[str, tuple[int, int]] = {}
    for page, path in paths.items():
        data_urls[page] = load_template_image_data_url(str(path), path.stat().st_mtime_ns)
        with Image.open(path) as image:
            sizes[page] = (int(image.width), int(image.height))
    return data_urls, sizes


def _render_focus_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {
            display: none !important;
        }
        [data-testid="stAppViewContainer"] > .main {
            margin-left: 0 !important;
        }
        .block-container {
            max-width: none !important;
            padding: 1rem 1.5rem 2rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_exit_button(session_id: int) -> None:
    if st.button("退出专注模式", key=f"focus-error-exit-{session_id}"):
        st.session_state.pop("region_focus_session_id", None)
        st.rerun()


def _leave_focus_mode(session_id: int, fingerprint: str) -> None:
    st.session_state.pop("region_focus_session_id", None)
    st.session_state.pop(_draft_choice_key(session_id, fingerprint), None)
    st.session_state.pop(_editor_state_key(session_id, fingerprint), None)
    st.rerun()


def _draft_choice_key(session_id: int, fingerprint: str) -> str:
    return f"_answer_region_focus_draft_choice_{session_id}_{fingerprint}"


def _editor_state_key(session_id: int, fingerprint: str) -> str:
    return f"_answer_region_focus_editor_state_{session_id}_{fingerprint}"


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, number)


def _nonnegative_int(value: Any) -> int:
    return _optional_nonnegative_int(value) or 0
