from pathlib import Path


QUESTION_BANK_PAGE = Path("pages") / "题库管理.py"
ASSEMBLY_PAGE = Path("pages") / "组卷.py"
AI_TAGGING_SERVICE = Path("question_bank") / "services" / "ai_tagging_service.py"


def test_ai_tagging_schema_no_longer_requests_typicality() -> None:
    service = AI_TAGGING_SERVICE.read_text(encoding="utf-8")

    assert '"typicality": score_field' not in service
    assert '"typicality": 1' not in service
    assert "Scores difficulty and typicality" not in service


def test_question_bank_and_assembly_pages_no_longer_show_typicality() -> None:
    question_bank_page = QUESTION_BANK_PAGE.read_text(encoding="utf-8")
    assembly_page = ASSEMBLY_PAGE.read_text(encoding="utf-8")

    assert "典型度" not in question_bank_page
    assert "典型程度" not in question_bank_page
    assert "典型度" not in assembly_page
    assert "典型程度" not in assembly_page


def test_question_bank_and_assembly_pages_show_frequency_metrics() -> None:
    question_bank_page = QUESTION_BANK_PAGE.read_text(encoding="utf-8")
    assembly_page = ASSEMBLY_PAGE.read_text(encoding="utf-8")

    assert "QuestionFrequencyService" in question_bank_page
    assert "QuestionFrequencyService" in assembly_page
    assert "考频" in question_bank_page
    assert "考频" in assembly_page
