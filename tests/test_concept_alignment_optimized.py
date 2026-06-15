from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from question_bank.models.knowledge_alignment import AlignmentStatus
from question_bank.services.concept_alignment_service import ConceptAlignmentService


@pytest.fixture
def alignment_service(tmp_path: Path) -> ConceptAlignmentService:
    return ConceptAlignmentService(tmp_path / "question_bank_opt.db")


class MockCompactLLMClient:
    def __init__(self, response: list[list[Any]]):
        self.response = response
        self.prompts = []

    def json_from_text(self, prompt: str) -> list[list[Any]]:
        self.prompts.append(prompt)
        return self.response


def test_delta_check_avoids_ai_calls_for_existing_mappings(
    alignment_service: ConceptAlignmentService,
) -> None:
    concept = alignment_service.create_concept("math.quadratic", "二次函数")
    
    # Pre-populate database with an existing mapping
    alignment_service.confirm_mapping(
        source_namespace="grading_weak_point",
        source_value="二次函数的值域",
        concept_id=concept.id,
        sub_skill_tags=["值域"],
    )

    # Mock LLM Client that should NOT be called
    mock_client = MockCompactLLMClient([["二次函数的值域", "math.quadratic", ["新技能"], 0.9]])
    
    results = alignment_service.ai_batch_align(
        source_namespace="grading_weak_point",
        source_terms=["二次函数的值域"],
        llm_client=mock_client,
    )
    
    # Assert LLM was never called because mapping exists
    assert len(mock_client.prompts) == 0
    assert len(results) == 1
    assert results[0].source_value == "二次函数的值域"
    assert results[0].sub_skill_tags == ("值域",)


def test_ai_batch_align_compact_and_concurrency(
    alignment_service: ConceptAlignmentService,
) -> None:
    # Set up some standard concepts
    c1 = alignment_service.create_concept("math.triangle", "等腰三角形")
    c2 = alignment_service.create_concept("math.equation", "一元一次方程")

    # Mock response for a batch alignment of 3 terms
    # Since chunk_size is 15 in code, 3 terms will run in 1 chunk
    mock_response = [
        ["等腰三角形的角度计算", "math.triangle", ["角度计算"], 0.9],
        ["一元一次方程的解法", "math.equation", ["解法"], 0.85],
        ["未知的其它考法", None, [], 0.3],
    ]
    mock_client = MockCompactLLMClient(mock_response)

    results = alignment_service.ai_batch_align(
        source_namespace="grading_weak_point",
        source_terms=["等腰三角形的角度计算", "一元一次方程的解法", "未知的其它考法"],
        llm_client=mock_client,
    )

    assert len(results) == 3
    
    r1 = next(r for r in results if r.source_value == "等腰三角形的角度计算")
    assert r1.concept_id == c1.id
    assert r1.sub_skill_tags == ("角度计算",)
    assert r1.confidence == 0.9

    r2 = next(r for r in results if r.source_value == "一元一次方程的解法")
    assert r2.concept_id == c2.id
    assert r2.sub_skill_tags == ("解法",)
    assert r2.confidence == 0.85

    r3 = next(r for r in results if r.source_value == "未知的其它考法")
    assert r3.concept_id is None
    assert r3.sub_skill_tags == ()
    assert r3.confidence == 0.3

    # Ensure candidate pruning worked (since mock_client prompted with only the relevant concepts)
    assert len(mock_client.prompts) == 1
    prompt_text = mock_client.prompts[0]
    assert "math.triangle" in prompt_text
    assert "math.equation" in prompt_text


def test_ai_batch_align_progress_callback(
    alignment_service: ConceptAlignmentService,
) -> None:
    alignment_service.create_concept("math.quadratic", "二次函数")
    mock_response = [["二次函数的值域", "math.quadratic", ["值域"], 0.95]]
    mock_client = MockCompactLLMClient(mock_response)

    calls = []

    def callback(completed: int, total: int, terms: list[str], results: list[Any]) -> None:
        calls.append((completed, total, terms, results))

    results = alignment_service.ai_batch_align(
        source_namespace="grading_weak_point",
        source_terms=["二次函数的值域"],
        llm_client=mock_client,
        on_chunk_complete=callback,
    )

    assert len(results) == 1
    assert len(calls) == 1
    assert calls[0][0] == 1  # completed
    assert calls[0][1] == 1  # total
    assert calls[0][2] == ["二次函数的值域"]  # terms
    assert isinstance(calls[0][3], list)  # results list
    assert calls[0][3][0][0] == "二次函数的值域"


def test_ai_batch_align_progress_callback_error(
    alignment_service: ConceptAlignmentService,
) -> None:
    # Initialize DB schema by creating a concept
    alignment_service.create_concept("math.test", "测试概念")
    
    # Set up a Mock LLM client that returns invalid format (a string instead of list/dict)
    mock_client = MockCompactLLMClient("not a valid json list or dict")
    
    calls = []
    
    def callback(completed: int, total: int, terms: list[str], results: list[Any], error_info: dict[str, Any] = None) -> None:
        calls.append((completed, total, terms, results, error_info))
        
    results = alignment_service.ai_batch_align(
        source_namespace="grading_weak_point",
        source_terms=["出错原始词"],
        llm_client=mock_client,
        on_chunk_complete=callback,
    )
    
    assert len(results) == 0
    assert len(calls) == 1
    assert calls[0][3] == []  # results should be empty
    assert calls[0][4] is not None
    assert "返回值类型不匹配" in calls[0][4]["error"]
    assert calls[0][4]["raw_response"] == "not a valid json list or dict"
