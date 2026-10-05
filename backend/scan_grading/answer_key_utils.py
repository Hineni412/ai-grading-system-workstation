from __future__ import annotations

from typing import Any

_LEGACY_FALLBACK_KEYS = ("standard_answer", "correct_answer", "answer", "answers", "reference_answer")


def answer_forms_for_question(answer_key: dict, question_id: str) -> list[str]:
    if not isinstance(answer_key, dict):
        return []
    target = str(question_id or "").strip()
    if not target:
        return []
    for question in answer_key.get("questions", []):
        if not isinstance(question, dict):
            continue
        question_node = _find_matching_node(question, target)
        if question_node is None:
            continue
        return _forms_from_node(question_node) or _forms_from_node(question)
    return []


def answer_forms_map(answer_key: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(answer_key, dict):
        return result
    for question in answer_key.get("questions", []):
        if not isinstance(question, dict):
            continue
        question_id = _node_id(question)
        if question_id:
            forms = _forms_from_node(question)
            if forms:
                result[question_id] = forms
        for part in _parts(question):
            part_id = _node_id(part)
            if not part_id:
                continue
            forms = _forms_from_node(part) or _forms_from_node(question)
            if forms:
                result[part_id] = forms
    return result


def _find_matching_node(question: dict[str, Any], question_id: str) -> dict[str, Any] | None:
    if _node_id(question) == question_id:
        return question
    for part in _parts(question):
        if _node_id(part) == question_id:
            return part
    return None


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("part_id") or node.get("question_id") or "").strip()


def _parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    parts = question.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def _forms_from_node(node: dict[str, Any]) -> list[str]:
    primary_values: list[Any] = []
    accepted = node.get("accepted_forms")
    if isinstance(accepted, list):
        primary_values.extend(accepted)
    elif accepted is not None:
        primary_values.append(accepted)
    canonical = node.get("canonical_answer")
    if canonical is not None:
        primary_values.append(canonical)
    primary_forms = _stable_unique_non_empty(primary_values)
    if primary_forms:
        return primary_forms

    for key in _LEGACY_FALLBACK_KEYS:
        value = node.get(key)
        values = value if isinstance(value, list) else [value]
        forms = _stable_unique_non_empty(values)
        if forms:
            return forms
    return []


def _stable_unique_non_empty(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
