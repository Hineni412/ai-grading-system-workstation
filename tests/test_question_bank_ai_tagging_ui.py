from pathlib import Path


PAGE = Path("pages") / "题库管理.py"


def test_question_bank_page_has_review_model_config() -> None:
    page = PAGE.read_text(encoding="utf-8")

    assert "tagging_review_enabled_input" in page
    assert "tagging_review_api_key_input" in page
    assert "tagging_review_base_url_input" in page
    assert "tagging_review_model_input" in page
    assert "QUESTION_BANK_TAGGING_REVIEW_MODEL" in page


def test_question_bank_page_only_auto_saves_complete_ai_results() -> None:
    page = PAGE.read_text(encoding="utf-8")

    assert "is_auto_saveable_result(result)" in page
    assert "quality_status" in page
    assert "quality_notes" in page
    assert "model_name=result.model_name" in page
    assert "待确认" in page


def test_question_bank_page_reuses_exact_duplicate_tags_before_ai_call() -> None:
    page = PAGE.read_text(encoding="utf-8")

    assert "find_exact_duplicate_tag_analysis(selected_id)" in page
    assert '"reused": reused_count' in page


def test_question_bank_page_reuses_one_skill_resolver_for_batch_save() -> None:
    page = PAGE.read_text(encoding="utf-8")

    assert "build_skill_context_ranker" in page
    assert "skill_resolver=skill_resolver" in page
