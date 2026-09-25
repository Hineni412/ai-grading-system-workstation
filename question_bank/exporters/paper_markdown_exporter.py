from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from question_bank.services.assembly_basket_state import SectionSpec
from question_bank.services.question_read_service import QuestionBankReadService


_IMAGE_MARKER = re.compile(
    r"\[\[IMAGE:(?P<path>[^\]|]+?)(?:\|[^\]]*)?\]\]"
)
_SECTION_LABELS = (
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
)


def export_question_paper_markdown(
    db_path: str | Path,
    question_ids: Iterable[int],
    output_dir: str | Path,
    *,
    title: str,
    include_answer: bool = False,
    grouped_by_type: bool = False,
    header_text: str | None = None,
    sections: Iterable[SectionSpec] | None = None,
) -> Path:
    service = QuestionBankReadService(Path(db_path))
    ordered_ids = _dedupe_ids(question_ids)
    questions = service.get_questions_for_export(ordered_ids)
    questions = [question for question in questions if question is not None]
    if not questions:
        raise ValueError("试题篮为空，无法导出 Markdown")

    clean_title = str(title or "").strip() or _default_title()
    lines: list[str] = []
    if str(header_text or "").strip():
        lines.extend([f"**{str(header_text).strip()}**", ""])
    lines.extend(
        [
            f"# {clean_title}",
            "",
            "姓名：________________    班级：________________    日期：________________",
            "",
        ]
    )

    groups = _question_groups(
        questions,
        sections=list(sections or []),
        grouped_by_type=grouped_by_type,
    )
    numbered: list[tuple[int, dict]] = []
    next_number = 1
    for group_index, (group_title, group_questions) in enumerate(groups):
        if group_title:
            lines.extend([f"## {_section_label(group_index)}、{group_title}", ""])
        for question in group_questions:
            numbered.append((next_number, question))
            lines.extend(_question_lines(next_number, question))
            next_number += 1

    if include_answer:
        lines.extend(["---", "", "## 答案", ""])
        for number, question in numbered:
            answer = _clean_text(question.get("answer_text")) or "暂无答案"
            lines.extend([f"{number}. {answer}", ""])

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / (
        f"{_safe_filename(clean_title)}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md"
    )
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output


def _question_groups(
    questions: list[dict],
    *,
    sections: list[SectionSpec],
    grouped_by_type: bool,
) -> list[tuple[str, list[dict]]]:
    by_id = {int(question["id"]): question for question in questions}
    if sections:
        groups: list[tuple[str, list[dict]]] = []
        used: set[int] = set()
        for section in sections:
            items: list[dict] = []
            for question_id in _dedupe_ids(section.question_ids):
                question = by_id.get(question_id)
                if question is not None and question_id not in used:
                    used.add(question_id)
                    items.append(question)
            if items:
                groups.append((str(section.title or "").strip() or "未分节", items))
        remaining = [
            question
            for question in questions
            if int(question["id"]) not in used
        ]
        if remaining:
            groups.append(("未分节", remaining))
        return groups
    if grouped_by_type:
        ordered_groups = [("选择题", []), ("填空题", []), ("解答题", [])]
        group_map = {name: items for name, items in ordered_groups}
        for question in questions:
            group_map[_canonical_type(question.get("question_type"))].append(question)
        return [(name, items) for name, items in ordered_groups if items]
    return [("", questions)]


def _question_lines(number: int, question: dict) -> list[str]:
    raw_text = str(question.get("question_text") or "")
    marker_paths = [
        match.group("path").strip()
        for match in _IMAGE_MARKER.finditer(raw_text)
        if match.group("path").strip()
    ]
    image_paths = [
        str(value).strip()
        for value in (question.get("image_paths") or [])
        if str(value).strip()
    ]
    text = _clean_text(raw_text) or "题干缺失"
    lines = [f"{number}. {text}", ""]
    if _dedupe_text([*marker_paths, *image_paths]):
        lines.extend(
            [
                "> 本题图像素材未嵌入 Markdown；请使用 Word 版查看原图。",
                "",
            ]
        )
    return lines


def _clean_text(value: object) -> str:
    return _IMAGE_MARKER.sub("", str(value or "")).strip()


def _canonical_type(value: object) -> str:
    text = str(value or "").strip()
    if "选择" in text:
        return "选择题"
    if "填空" in text:
        return "填空题"
    return "解答题"


def _section_label(index: int) -> str:
    return _SECTION_LABELS[index] if index < len(_SECTION_LABELS) else str(index + 1)


def _dedupe_ids(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id > 0 and question_id not in result:
            result.append(question_id)
    return result


def _dedupe_text(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _safe_filename(value: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return clean[:80] or "组卷"


def _default_title() -> str:
    return f"{datetime.now().strftime('%Y-%m-%d')}习题"


__all__ = ["export_question_paper_markdown"]
