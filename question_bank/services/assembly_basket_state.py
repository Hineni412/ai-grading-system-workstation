from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from question_bank.database.paths import project_data_root


@dataclass
class SectionSpec:
    """AI 智能分类组卷：单个知识点大类的标题与题目集合。

    title         中文简短标题，如 "折叠的性质"（不含编号，编号在渲染时加"一、二、"）。
    question_ids  属于该分类的题目 ID 列表，顺序即段内排序。
    """

    title: str
    question_ids: list[int] = field(default_factory=list)


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


BASKET_KEY = "qb_question_basket"
ORDER_KEY = "qb_assembly_order"
SECTIONS_KEY = "qb_assembly_sections"  # AI 智能分类分组（None=未启用，list[SectionSpec]=已分类）


def _draft_file_path() -> Path:
    return project_data_root() / "question_bank" / "assembly_draft.json"


def _sections_to_payload(sections: list[SectionSpec] | None) -> list[dict] | None:
    if not sections:
        return None
    return [
        {
            "title": str(sec.title or "").strip(),
            "question_ids": normalize_question_ids(sec.question_ids),
        }
        for sec in sections
    ]


def _sections_from_payload(payload: object) -> list[SectionSpec] | None:
    if not isinstance(payload, list) or not payload:
        return None
    sections: list[SectionSpec] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        ids = normalize_question_ids(item.get("question_ids"))
        if not title or not ids:
            continue
        sections.append(SectionSpec(title=title, question_ids=ids))
    return sections or None


def save_basket_draft(
    basket_ids: list[int],
    order_ids: list[int] | None = None,
    sections: list[SectionSpec] | None = None,
) -> None:
    try:
        path = _draft_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = {}
        payload["basket_ids"] = normalize_question_ids(basket_ids)
        payload["order_ids"] = normalize_question_ids(order_ids or [])
        sections_payload = _sections_to_payload(sections)
        if sections_payload:
            payload["sections"] = sections_payload
        else:
            payload.pop("sections", None)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        pass


def load_basket_draft() -> tuple[list[int], list[int], list[SectionSpec] | None]:
    try:
        path = _draft_file_path()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                basket_ids = normalize_question_ids(payload.get("basket_ids"))
                order_ids = normalize_question_ids(payload.get("order_ids"))
                sections = _sections_from_payload(payload.get("sections"))
                return basket_ids, order_ids, sections
    except Exception:
        pass
    return [], [], None
