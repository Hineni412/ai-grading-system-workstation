from __future__ import annotations

from pathlib import Path


WEB_APP = Path("web_app.py")
COMPONENT = Path("pages_shared/grading_paper_skill_workflow_component.py")


def test_source_is_archived_on_config_save_without_running_intake() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    start = source.index('if st.button("确认保存评分依据"')
    save_block = source[start : source.index("st.divider()", start)]

    assert "archive_uploaded_grading_paper" in save_block
    assert "copy_and_intake_uploaded_grading_paper" not in save_block
    assert "intake_grading_paper_to_question_bank" not in save_block
    assert "source_paper_path=source_archive.stored_path" in save_block
    assert "source_paper_sha256=source_archive.sha256" in save_block


def test_optional_card_is_rendered_in_config_grading_and_graph_flows() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    assert source.count("render_grading_paper_skill_workflow_card(") >= 3
    component = COMPONENT.read_text(encoding="utf-8")
    for text in (
        "保存原始试卷",
        "入库并打标签",
        "可稍后处理，不影响批改",
        "部分完成",
        "重新处理",
    ):
        assert text in component
    assert "st.stop" not in component
    assert component.index("ai_service_factory()") > component.index("if st.button(button_label")


def test_session_creation_and_update_bind_the_saved_source() -> None:
    source = WEB_APP.read_text(encoding="utf-8")

    assert "source_paper_path=st.session_state.get(\"latest_source_paper_path\", \"\")" in source
    assert "source_paper_sha256=st.session_state.get(\"latest_source_paper_sha256\", \"\")" in source
    assert "db.bind_grading_session_source(" in source
    assert "clear_pending_config_for_new_session(st.session_state, settings_store=db)" in source


def test_global_graph_uses_skill_profiles_not_legacy_weak_point_rows() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    section = source.split("def render_global_weak_points_tab", 1)[1].split(
        "def _render_active_session_multiselect",
        1,
    )[0]

    assert "DiagnosisProfileService" in section
    assert "build_skill_graph_rows" in section
    assert "get_active_global_weak_points" not in section
    assert "知识图谱完整度" in section


def test_graph_detail_routes_by_positive_skill_id() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    graph_section = source.split("def _render_knowledge_graph_from_rows", 1)[1].split(
        "def _group_rows_by_student",
        1,
    )[0]
    query_section = source.split("def _read_graph_detail_query", 1)[1].split(
        "def _read_knowledge_detail_query",
        1,
    )[0]

    assert "kg_skill_id" in graph_section
    assert "kg_knowledge_id" not in graph_section
    assert '"skill_id": skill_id' in query_section
    assert "_render_skill_wrong_detail" in source


def test_aggregate_graph_keeps_same_named_skill_ids_separate() -> None:
    from web_app import _aggregate_knowledge_rows

    rows = [
        {
            "student_id": 1,
            "student_name": "甲",
            "skill_id": 10,
            "knowledge_id": "skill:10",
            "knowledge_label": "同名技能",
            "weighted_score_rate": 50.0,
            "item_count": 1,
            "deduction_count": 1,
        },
        {
            "student_id": 2,
            "student_name": "乙",
            "skill_id": 10,
            "knowledge_id": "skill:10",
            "knowledge_label": "同名技能",
            "weighted_score_rate": 100.0,
            "item_count": 3,
            "deduction_count": 0,
        },
        {
            "student_id": 2,
            "student_name": "乙",
            "skill_id": 11,
            "knowledge_id": "skill:11",
            "knowledge_label": "同名技能",
            "weighted_score_rate": 80.0,
            "item_count": 1,
            "deduction_count": 1,
        },
    ]

    aggregated = _aggregate_knowledge_rows(rows)["筛选学生合计"]

    assert {item["skill_id"] for item in aggregated} == {10, 11}
    skill_10 = next(item for item in aggregated if item["skill_id"] == 10)
    assert skill_10["weighted_score_rate"] == 87.5
    assert skill_10["item_count"] == 4
