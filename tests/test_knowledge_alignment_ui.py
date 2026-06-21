from __future__ import annotations

from pathlib import Path


PAGE_PATH = Path("pages/知识点整理（高级）.py")


def test_alignment_page_uses_persistent_service_and_real_sources() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "ConceptAlignmentService" in source
    assert "get_active_global_weak_points" in source
    assert "question_tags" in source
    assert "knowledge_mapping.json" not in source
    assert "load_sample_mastery_rows" not in source


def test_alignment_page_exposes_concentrated_confirmation_workflow() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    for label in ("确认此匹配", "待确认", "未映射", "已拒绝"):
        assert label in source
    assert "ALIGNMENT_FOCUS_SESSION_KEY" in source
    assert "merge_focus_sources" in source
    assert "filter_focus_sources" in source
    assert 'str(row["source_value"]).strip().lower() in focus_set' not in source


def test_alignment_page_is_an_advanced_exception_editor() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert 'st.title("知识点整理（高级）")' in source
    assert "待处理术语概览" in source
    assert "选择需要处理的术语" in source
    assert "拒绝此术语" in source
    assert 'st.expander("AI 处理详情（维护）"' in source
    assert "置信度阈值" not in source
    assert "自动勾选" not in source


def test_alignment_page_exposes_concept_and_relation_editors() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "标准知识点管理" in source
    assert "知识点关系管理" in source
    assert "create_concept" in source
    assert "create_relation" in source
    assert "if relations:" in source
