from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from objective_answer_loader import normalize_objective_question_id
from question_id_contract import QuestionIdCatalog, QuestionIdContractError


_ANSWER_FIELDS = (
    "standard_answer",
    "correct_answer",
    "answer",
    "answers",
    "reference_answer",
    "expected_answer",
    "solution",
    "canonical_answer",
    "accepted_forms",
)


def normalize_question_id(question_id: str) -> str:
    return normalize_objective_question_id(question_id)


def _answer_from_node(node: Mapping[str, Any]) -> str | None:
    for field in _ANSWER_FIELDS:
        value = node.get(field)
        if not value:
            continue
        if isinstance(value, list):
            for item in value:
                text = str(item).strip()
                if text:
                    return text
            continue
        return str(value)
    return None

def get_standard_answer_for_question(rubric: dict, question_id: str) -> tuple[str | None, str]:
    if not isinstance(rubric, dict):
        return None, "missing"

    questions = rubric.get("questions", [])
    if isinstance(questions, dict):
        questions = list(questions.values())
    if not isinstance(questions, list):
        return None, "missing"
    document = {
        "questions": [
            question
            for question in questions
            if isinstance(question, Mapping)
        ]
    }
    try:
        catalog = QuestionIdCatalog.from_document(document)
    except QuestionIdContractError:
        # Never choose an arbitrary answer when legacy aliases collide.
        return None, "missing"

    target_qid = catalog.resolve(normalize_question_id(question_id))
    if target_qid is None:
        return None, "missing"
    parent_id = catalog.parent_for(target_qid)
    if parent_id is None:
        return None, "missing"

    question_node: Mapping[str, Any] | None = None
    for question in document["questions"]:
        if catalog.resolve(question.get("question_id")) == parent_id:
            question_node = question
            break
    if question_node is None:
        return None, "missing"

    if target_qid == parent_id:
        answer = _answer_from_node(question_node)
        if answer is not None:
            return answer, "rubric_question"
        # A one-part question has the parent as its only formal detail. A
        # multi-part parent must not guess one child's answer.
        if catalog.parts_by_parent.get(parent_id) == (parent_id,):
            parts = question_node.get("parts")
            if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], Mapping):
                answer = _answer_from_node(parts[0])
                if answer is not None:
                    return answer, "rubric_part"
        return None, "missing"

    parts = question_node.get("parts")
    if not isinstance(parts, list):
        return None, "missing"
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        if catalog.resolve(
            part.get("part_id"),
            parent_id=parent_id,
        ) != target_qid:
            continue
        answer = _answer_from_node(part)
        if answer is not None:
            return answer, "rubric_part"
    return None, "missing"
