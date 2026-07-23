from pathlib import Path


AI_TAGGING_SERVICE = Path("question_bank") / "services" / "ai_tagging_service.py"


def test_ai_tagging_schema_no_longer_requests_typicality() -> None:
    service = AI_TAGGING_SERVICE.read_text(encoding="utf-8")

    assert '"typicality": score_field' not in service
    assert '"typicality": 1' not in service
    assert "Scores difficulty and typicality" not in service
