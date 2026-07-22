"""P3-02 public domain-model compatibility contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, asdict, fields
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_model_imports_are_aliases_of_the_domain_models() -> None:
    from ai_grader import (
        GradingResult as LegacyGradingResult,
        QuestionGradingDetail as LegacyQuestionGradingDetail,
        SecondaryError as LegacySecondaryError,
    )
    from backend.domain_models import (
        ExamPaperGroup,
        GradingResult,
        QuestionGradingDetail,
        SecondaryError,
    )
    from scanner import ExamPaperGroup as LegacyExamPaperGroup

    assert LegacyQuestionGradingDetail is QuestionGradingDetail
    assert LegacySecondaryError is SecondaryError
    assert LegacyGradingResult is GradingResult
    assert LegacyExamPaperGroup is ExamPaperGroup


def test_domain_model_fields_defaults_and_serialization_match_the_frozen_contract() -> None:
    from backend.domain_models import (
        ExamPaperGroup,
        GradingResult,
        QuestionGradingDetail,
        SecondaryError,
    )

    assert [field.name for field in fields(QuestionGradingDetail)] == [
        "question_id",
        "score_awarded",
        "deduction_reason",
        "knowledge_id",
        "error_category",
        "error_summary",
        "confidence_score",
        "knowledge_ids",
        "secondary_errors",
    ]
    assert [field.name for field in fields(GradingResult)] == [
        "student_name",
        "total_score",
        "student_score",
        "needs_human_review",
        "grading_details",
        "raw_json",
    ]
    assert [field.name for field in fields(ExamPaperGroup)] == [
        "front_image",
        "back_image",
        "student_name",
        "student_id",
        "detected_name",
        "source_label",
        "enhanced_front_image",
        "enhanced_back_image",
        "match_method",
        "match_score",
    ]
    first = QuestionGradingDetail("Q1", 3.5, None)
    second = QuestionGradingDetail("Q2", 2.0, "reason")
    assert first.knowledge_id == "UNKNOWN"
    assert first.knowledge_ids == [] and first.knowledge_ids is not second.knowledge_ids
    assert first.secondary_errors == [] and first.secondary_errors is not second.secondary_errors
    error = SecondaryError("calculation", "sign", "line 2")
    first.knowledge_ids.append("K1")
    first.secondary_errors.append(error)
    result = GradingResult("Alice", 100.0, 3.5, True, [first], {"safe": True})
    group = ExamPaperGroup(Path("front.jpg"), Path("back.jpg"), "Alice")

    assert asdict(result) == {
        "student_name": "Alice",
        "total_score": 100.0,
        "student_score": 3.5,
        "needs_human_review": True,
        "grading_details": [
            {
                "question_id": "Q1",
                "score_awarded": 3.5,
                "deduction_reason": None,
                "knowledge_id": "UNKNOWN",
                "error_category": None,
                "error_summary": None,
                "confidence_score": None,
                "knowledge_ids": ["K1"],
                "secondary_errors": [
                    {"category": "calculation", "summary": "sign", "evidence": "line 2"}
                ],
            }
        ],
        "raw_json": {"safe": True},
    }
    assert asdict(group) == {
        "front_image": Path("front.jpg"),
        "back_image": Path("back.jpg"),
        "student_name": "Alice",
        "student_id": None,
        "detected_name": None,
        "source_label": "",
        "enhanced_front_image": None,
        "enhanced_back_image": None,
        "match_method": "exact",
        "match_score": 1.0,
    }
    with pytest.raises(FrozenInstanceError):
        error.summary = "changed"  # type: ignore[misc]


def test_domain_models_have_no_service_dependencies_and_db_manager_has_no_reverse_imports() -> None:
    domain_tree = ast.parse(
        (PROJECT_ROOT / "backend" / "domain_models.py").read_text(encoding="utf-8")
    )
    domain_imports = {
        node.module
        for node in ast.walk(domain_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(domain_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert domain_imports <= {"__future__", "dataclasses", "pathlib", "typing"}

    db_tree = ast.parse((PROJECT_ROOT / "db_manager.py").read_text(encoding="utf-8"))
    db_imports = {
        node.module
        for node in ast.walk(db_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(db_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "ai_grader" not in db_imports
    assert "scanner" not in db_imports
