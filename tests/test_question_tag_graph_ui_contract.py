from pathlib import Path


SOURCE = Path("web_app.py").read_text(encoding="utf-8")


def test_active_graph_and_detail_do_not_depend_on_legacy_skill_catalog() -> None:
    assert "build_question_tag_graph_rows" in SOURCE
    assert ".build_tag_profiles(" in SOURCE
    assert ".tag_evidence(" in SOURCE
    assert "SkillCatalogService" not in SOURCE
    assert "build_skill_graph_rows" not in SOURCE
    assert ".skill_evidence(" not in SOURCE


def test_detail_contract_exposes_current_tags_errors_and_candidate_count() -> None:
    detail = SOURCE.split("def _render_question_tag_wrong_detail", 1)[1].split(
        "def _render_wrong_detail_items",
        1,
    )[0]
    assert "question_tags" in detail
    assert "secondary_errors" in detail
    assert "tag_value_counts" in detail
    assert "推荐候选题" in detail
