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
        "继续处理缺失项",
    ):
        assert text in component
    assert "st.stop" not in component
    assert component.index("ai_service_factory()") > component.index("if st.button(button_label")
    assert "评分题技能" not in component
    assert "题库技能" not in component
    assert "导入题目" in component
    assert "完整标签" in component
    assert "已关联" in component
    assert "event.stage" in component


def test_workflow_uses_saved_question_bank_concurrency_settings() -> None:
    source = WEB_APP.read_text(encoding="utf-8")

    assert "qb_tagging_workers" not in source
    assert "qb_tagging_rpm" not in source
    assert "_sync_session_skill_links" not in source
    assert "SkillLinkService" not in source
    assert source.count('st.session_state.get("tagging_max_workers_input"') >= 3
    assert source.count('st.session_state.get("tagging_requests_per_minute_input"') >= 3


def test_session_creation_and_update_bind_the_saved_source() -> None:
    source = WEB_APP.read_text(encoding="utf-8")

    assert "source_paper_path=st.session_state.get(\"latest_source_paper_path\", \"\")" in source
    assert "source_paper_sha256=st.session_state.get(\"latest_source_paper_sha256\", \"\")" in source
    assert "publish_legacy_config_and_refresh_mapping(" in source
    assert "clear_pending_config_for_new_session(st.session_state, settings_store=db)" in source


def test_global_graph_uses_current_question_tag_profiles() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    section = source.split("def render_global_weak_points_tab", 1)[1].split(
        "def _render_active_session_multiselect",
        1,
    )[0]

    assert "DiagnosisProfileService" in section
    assert "build_tag_profiles" in section
    assert "build_question_tag_graph_rows" in section
    assert "build_skill_profiles" not in section
    assert "build_skill_graph_rows" not in section
    assert "知识图谱完整度" in section
    assert "题库对应完成度" in section


def test_graph_detail_routes_by_exact_knowledge_key() -> None:
    source = WEB_APP.read_text(encoding="utf-8")
    graph_section = source.split("def _render_knowledge_graph_from_rows", 1)[1].split(
        "def _group_rows_by_student",
        1,
    )[0]
    query_section = source.split("def _read_graph_detail_query", 1)[1].split(
        "def _read_knowledge_detail_query",
        1,
    )[0]

    assert "kg_knowledge_key" in graph_section
    assert "kg_skill_id" not in graph_section
    assert '"knowledge_key":' in query_section
    assert "_render_question_tag_wrong_detail" in source
    assert "skill_evidence" not in source
    assert "SkillCatalogService" not in source


def test_aggregate_graph_merges_only_the_same_exact_knowledge_key() -> None:
    from web_app import _aggregate_knowledge_rows

    rows = [
        {
            "student_id": 1,
            "student_name": "甲",
            "knowledge_key": "knowledge_point:三角形全等",
            "knowledge_label": "三角形全等",
            "weighted_score_rate": 50.0,
            "item_count": 1,
            "deduction_count": 1,
        },
        {
            "student_id": 2,
            "student_name": "乙",
            "knowledge_key": "knowledge_point:三角形全等",
            "knowledge_label": "三角形全等",
            "weighted_score_rate": 100.0,
            "item_count": 3,
            "deduction_count": 0,
        },
        {
            "student_id": 2,
            "student_name": "乙",
            "knowledge_key": "knowledge_point:轴对称",
            "knowledge_label": "轴对称",
            "weighted_score_rate": 80.0,
            "item_count": 1,
            "deduction_count": 1,
        },
    ]

    aggregated = _aggregate_knowledge_rows(rows)["筛选学生合计"]

    assert {item["knowledge_key"] for item in aggregated} == {
        "knowledge_point:三角形全等",
        "knowledge_point:轴对称",
    }
    exact = next(
        item
        for item in aggregated
        if item["knowledge_key"] == "knowledge_point:三角形全等"
    )
    assert exact["weighted_score_rate"] == 87.5
    assert exact["item_count"] == 4
