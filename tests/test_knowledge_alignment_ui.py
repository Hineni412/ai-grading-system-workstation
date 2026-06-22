from pathlib import Path


PAGE_PATH = Path("pages/知识点整理（高级）.py")


def test_bookmarked_page_path_now_uses_unified_skill_service() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    assert "SkillCatalogService" in source
    assert "get_path_manager().qb_db_path" in source
    assert "knowledge_mapping.json" not in source
    assert "ConceptAlignmentService" not in source


def test_page_has_no_old_confirmation_workbench_or_graph_editor() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")

    for removed in ("AI一键对齐", "批量确认", "标准知识点管理", "create_relation"):
        assert removed not in source
