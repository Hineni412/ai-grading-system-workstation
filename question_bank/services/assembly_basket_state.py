from __future__ import annotations

from typing import Iterable


def normalize_question_ids(values: object) -> list[int]:
    output: list[int] = []
    iterable: Iterable[object]
    if isinstance(values, (list, tuple, set)):
        iterable = values
    else:
        iterable = []

    for value in iterable:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id not in output:
            output.append(question_id)
    return output


def parse_question_ids_csv(value: object) -> list[int]:
    if isinstance(value, list):
        value = value[0] if value else ""
    return normalize_question_ids(str(value or "").split(","))


def merge_question_ids(current: object, additions: object) -> list[int]:
    output = normalize_question_ids(current)
    for question_id in normalize_question_ids(additions):
        if question_id not in output:
            output.append(question_id)
    return output


def order_for_basket(basket_ids: object, order_values: object) -> list[int]:
    basket = normalize_question_ids(basket_ids)
    ordered = [
        question_id
        for question_id in normalize_question_ids(order_values)
        if question_id in basket
    ]
    for question_id in basket:
        if question_id not in ordered:
            ordered.append(question_id)
    return ordered


def question_ids_to_csv(values: object) -> str:
    return ",".join(str(question_id) for question_id in normalize_question_ids(values))
