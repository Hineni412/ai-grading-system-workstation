from __future__ import annotations

from pathlib import Path


PAGE_PATH = Path("pages/知识图谱适配调试.py")


def test_alignment_page_uses_persistent_service_and_real_sources() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "ConceptAlignmentService" in source
    assert "get_active_global_weak_points" in source
    assert "question_tags" in source
    assert "knowledge_mapping.json" not in source
    assert "load_sample_mastery_rows" not in source


def test_alignment_page_exposes_concentrated_confirmation_workflow() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    for label in ("批量确认", "待确认", "未映射", "已拒绝"):
        assert label in source
    assert "ALIGNMENT_FOCUS_SESSION_KEY" in source


def test_alignment_page_exposes_concept_and_relation_editors() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "标准知识点管理" in source
    assert "知识点关系管理" in source
    assert "create_concept" in source
    assert "create_relation" in source
