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


import json
from pathlib import Path
from question_bank.database.paths import project_data_root

BASKET_KEY = "qb_question_basket"
ORDER_KEY = "qb_assembly_order"


def _draft_file_path() -> Path:
    return project_data_root() / "question_bank" / "assembly_draft.json"


def save_basket_draft(basket_ids: list[int], order_ids: list[int] | None = None) -> None:
    try:
        path = _draft_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "basket_ids": normalize_question_ids(basket_ids),
            "order_ids": normalize_question_ids(order_ids or []),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_basket_draft() -> tuple[list[int], list[int]]:
    try:
        path = _draft_file_path()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                basket_ids = normalize_question_ids(payload.get("basket_ids"))
                order_ids = normalize_question_ids(payload.get("order_ids"))
                return basket_ids, order_ids
    except Exception:
        pass
    return [], []
