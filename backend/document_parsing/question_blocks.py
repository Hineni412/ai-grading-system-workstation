from __future__ import annotations

import html
import re
from typing import Any
from xml.etree import ElementTree

from equivalence_engine import merge_equivalent_forms


_INLINE_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
_INLINE_MAIN_QUESTION_MARKER = re.compile(
    r"(?P<prefix>[。！？!?．.][ \t\r\n]*)"
    r"(?P<number>\d{1,2})[ \t]*[.．、](?![ \t]*\d)[ \t]*"
)


def parse_plain_question_blocks(doc_text: str) -> list[dict[str, Any]]:
    normalized_doc_text = "\n".join(_normalize_inline_main_question_lines(doc_text))
    inline_blocks = _parse_inline_answer_blocks(normalized_doc_text)
    if inline_blocks:
        return inline_blocks

    parsed_questions: list[Any] = []
    try:
        from question_bank.importers.batch_importer import parse_paper_text

        parsed = parse_paper_text(
            normalized_doc_text,
            source_file="grading_config.docx",
            page_range="document",
        )
        parsed_questions = list(parsed.questions)
    except Exception:
        parsed_questions = []

    if parsed_questions:
        truncated: list[Any] = []
        last_number = 0
        for item in parsed_questions:
            number_str = str(getattr(item, "question_number", "") or "").strip()
            if not number_str.isdigit():
                truncated.append(item)
                continue
            number = int(number_str)
            if truncated and number <= last_number:
                break
            truncated.append(item)
            last_number = number
        parsed_questions = truncated

    fallback_blocks = (
        [] if parsed_questions else _split_doc_text_into_question_blocks(normalized_doc_text)
    )
    if parsed_questions:
        question_numbers = [
            str(int(str(getattr(item, "question_number", "") or "").strip()))
            for item in parsed_questions
            if str(getattr(item, "question_number", "") or "").strip().isdigit()
        ]
    else:
        question_numbers = [
            str(block.get("question_id") or "").removeprefix("Q")
            for block in fallback_blocks
            if str(block.get("question_id") or "").removeprefix("Q").isdigit()
        ]
    answer_section = _local_answer_section_text(normalized_doc_text)
    answer_blocks = _local_answer_blocks(answer_section)
    choice_answers = _extract_choice_answer_sequence(
        answer_section,
        question_numbers=question_numbers,
    )
    section_hints = _question_type_hints_from_section_headings(normalized_doc_text)

    blocks: list[dict[str, Any]] = []
    if parsed_questions:
        for item in parsed_questions:
            number = str(getattr(item, "question_number", "") or "").strip()
            if not number.isdigit():
                continue
            qid = f"Q{int(number)}"
            question_text = str(getattr(item, "question_text", "") or "").strip()
            answer_from_map = str(answer_blocks.get(number, "") or "").strip()
            parsed_answer = str(getattr(item, "answer_text", "") or "").strip()
            raw_answer = str(answer_from_map or parsed_answer).strip()
            qtype = _infer_local_question_type(
                question_text,
                raw_answer,
                number,
                section_type=section_hints.get(str(int(number)), ""),
            )
            canonical = _extract_canonical_answer_for_local_question(
                number=number,
                qtype=qtype,
                question_text=question_text,
                answer_text=raw_answer or answer_blocks.get(number, ""),
                choice_answers=choice_answers,
            )
            local_answer_trusted = _is_local_answer_trusted(
                qtype=qtype,
                question_text=question_text,
                canonical_answer=canonical,
                answer_text=raw_answer,
                explicitly_mapped=bool(answer_from_map),
            )
            blocks.append(
                {
                    "question_id": qid,
                    "text": question_text,
                    "question_text": question_text,
                    "answer_text": raw_answer,
                    "question_type": qtype,
                    "canonical_answer": canonical,
                    "accepted_forms": _local_accepted_forms(canonical, qtype),
                    "local_answer_trusted": local_answer_trusted,
                    "needs_review": not local_answer_trusted,
                }
            )
    else:
        for block in fallback_blocks:
            qid = str(block.get("question_id") or "")
            number = qid.removeprefix("Q")
            question_text = str(block.get("text") or "")
            raw_answer = answer_blocks.get(number, "")
            qtype = _infer_local_question_type(
                question_text,
                raw_answer,
                number,
                section_type=(
                    section_hints.get(str(int(number)), "")
                    if number.isdigit()
                    else ""
                ),
            )
            canonical = _extract_canonical_answer_for_local_question(
                number=number,
                qtype=qtype,
                question_text=question_text,
                answer_text=raw_answer,
                choice_answers=choice_answers,
            )
            local_answer_trusted = _is_local_answer_trusted(
                qtype=qtype,
                question_text=question_text,
                canonical_answer=canonical,
                answer_text=raw_answer,
                explicitly_mapped=bool(answer_blocks.get(number)),
            )
            block.update(
                {
                    "question_text": question_text,
                    "answer_text": raw_answer,
                    "question_type": qtype,
                    "canonical_answer": canonical,
                    "accepted_forms": _local_accepted_forms(canonical, qtype),
                    "local_answer_trusted": local_answer_trusted,
                    "needs_review": not local_answer_trusted,
                }
            )
            blocks.append(block)
    return blocks


def _parse_inline_answer_blocks(doc_text: str) -> list[dict[str, Any]] | None:
    full_text = str(doc_text or "")
    text, answer_section = _split_local_question_answer_text(full_text)
    if ("【答案】" not in text) and ("【解析】" not in text):
        return None

    lines = text.splitlines()
    markers: list[tuple[int, int]] = []
    last_number = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(\d{1,2})\s*[.．]", line.strip())
        if not match:
            continue
        number = int(match.group(1))
        if not (1 <= number <= 99):
            continue
        if markers and number <= last_number:
            break
        markers.append((index, number))
        last_number = number
    if not markers:
        return None

    choice_answers = _extract_choice_answer_sequence(
        answer_section or _local_answer_section_text(text),
        question_numbers=[str(number) for _, number in markers],
    )
    section_hints = _question_type_hints_from_section_headings(text)
    blocks: list[dict[str, Any]] = []
    for position, (start, number) in enumerate(markers):
        end = markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        parsed = _parse_inline_segment(
            number,
            lines[start:end],
            choice_answers,
            section_type=section_hints.get(str(number), ""),
        )
        if parsed:
            blocks.append(parsed)
    return blocks or None


def _parse_inline_segment(
    number: int,
    segment_lines: list[str],
    choice_answers: dict[str, str],
    *,
    section_type: str = "",
) -> dict[str, Any] | None:
    bucket = "stem"
    stem_lines: list[str] = []
    answer_lines: list[str] = []
    analysis_lines: list[str] = []
    for raw in segment_lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if line.startswith("[公式:") or line.startswith("[公式："):
            continue
        if "【答案】" in line:
            bucket = "answer"
            after = line.split("【答案】", 1)[1].strip()
            if after:
                answer_lines.append(after)
            continue
        if ("【解析】" in line) or ("【点睛】" in line):
            bucket = "analysis"
            marker = "【解析】" if "【解析】" in line else "【点睛】"
            after = line.split(marker, 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if line.startswith("【") and "】" in line:
            bucket = "analysis"
            after = line.split("】", 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if bucket == "stem":
            stem_lines.append(line)
        elif bucket == "answer":
            answer_lines.append(line)
        else:
            analysis_lines.append(line)

    question_text = _strip_leading_question_number(
        number, "\n".join(stem_lines).strip()
    )
    answer_raw = "\n".join(answer_lines).strip()
    analysis_raw = "\n".join(analysis_lines).strip()
    if not question_text and not answer_raw and not analysis_raw:
        return None

    num_str = str(number)
    qtype = _infer_local_question_type(
        question_text, answer_raw, num_str, section_type=section_type
    )
    canonical = _extract_canonical_answer_for_local_question(
        number=num_str,
        qtype=qtype,
        question_text=question_text,
        answer_text=answer_raw,
        choice_answers=choice_answers,
    )
    local_answer_trusted = _is_local_answer_trusted(
        qtype=qtype,
        question_text=question_text,
        canonical_answer=canonical,
        answer_text=answer_raw,
        explicitly_mapped=bool(answer_raw or choice_answers.get(num_str)),
    )
    return {
        "question_id": f"Q{number}",
        "text": question_text,
        "question_text": question_text,
        "answer_text": answer_raw,
        "analysis": analysis_raw,
        "question_type": qtype,
        "canonical_answer": canonical,
        "accepted_forms": _local_accepted_forms(canonical, qtype),
        "local_answer_trusted": local_answer_trusted,
        "needs_review": not local_answer_trusted,
    }


def _split_local_question_answer_text(doc_text: str) -> tuple[str, str]:
    text = str(doc_text or "")
    try:
        from question_bank.importers.batch_importer import _split_answer_text

        question_text, answer_text = _split_answer_text(text)
        return str(question_text or ""), str(answer_text or "")
    except Exception:
        for index, line in enumerate(text.splitlines()):
            if _looks_like_answer_section_heading(line):
                lines = text.splitlines()
                return (
                    "\n".join(lines[:index]).strip(),
                    "\n".join(lines[index + 1 :]).strip(),
                )
    return text, ""


def _question_type_hints_from_section_headings(doc_text: str) -> dict[str, str]:
    question_text, _ = _split_local_question_answer_text(doc_text)
    current_type = ""
    hints: dict[str, str] = {}
    for line in question_text.splitlines():
        value = str(line or "").strip()
        if "选择题" in value:
            current_type = "choice"
            continue
        if "填空题" in value:
            current_type = "fill_blank"
            continue
        if "证明题" in value:
            current_type = "proof"
            continue
        if "解答题" in value:
            current_type = "comprehensive"
            continue
        number = _extract_question_marker_number(value)
        if number is not None and current_type:
            hints[str(number)] = current_type
    return hints


def _local_answer_section_text(doc_text: str) -> str:
    text = str(doc_text or "")
    try:
        from question_bank.importers.batch_importer import _split_answer_text

        _, answer_text = _split_answer_text(text)
        if str(answer_text or "").strip():
            return str(answer_text or "")
    except Exception:
        pass
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _looks_like_answer_section_heading(line):
            return "\n".join(lines[index + 1 :]).strip()
    return ""


def _local_answer_blocks(answer_section: str) -> dict[str, str]:
    section = str(answer_section or "")
    if not section.strip():
        return {}
    try:
        from question_bank.importers.batch_importer import _split_numbered_blocks

        result: dict[str, str] = {}
        for block in _split_numbered_blocks(section):
            number = str(getattr(block, "number", "") or "").strip()
            if number.isdigit() and 1 <= int(number) <= 99:
                result.setdefault(
                    str(int(number)), str(getattr(block, "text", "") or "").strip()
                )
        if result:
            return result
    except Exception:
        pass

    lines = section.splitlines()
    markers: list[tuple[int, str]] = []
    last_number = 0
    for index, line in enumerate(lines):
        if "声明" in line or "菁优网" in line:
            break
        number = _extract_question_marker_number(line)
        if number is None:
            continue
        if markers and number <= last_number:
            break
        markers.append((index, str(number)))
        last_number = number
    result: dict[str, str] = {}
    for position, (start, number) in enumerate(markers):
        end = (
            markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        )
        block = "\n".join(lines[start:end]).strip()
        if block:
            result[number] = block
    return result


def _choice_answer_from_text(value: str) -> str:
    text = str(value or "")
    match = re.search(
        r"(?:故\s*选|选择|选\s*[:：]|答\s*案\s*(?:是|为|选)?\s*[:：]?"
        r"|答\s*[:：]|【答案】)\s*([A-Da-d])",
        text,
    )
    if match:
        return match.group(1).upper()
    bare = re.fullmatch(
        r"\s*(?:\d{1,2}\s*[.．、:：)）]\s*)?([A-Da-d])\s*[。；;]?\s*",
        text,
    )
    return bare.group(1).upper() if bare else ""


def _extract_choice_answer_sequence(
    answer_section: str,
    *,
    question_numbers: list[str] | None = None,
) -> dict[str, str]:
    section = str(answer_section or "")
    explicitly_numbered: dict[str, str] = {}
    for number, answer_text in _local_answer_blocks(section).items():
        answer = _choice_answer_from_text(answer_text)
        if answer:
            explicitly_numbered[str(int(number))] = answer
    if explicitly_numbered:
        return explicitly_numbered

    found: list[str] = []
    pattern = (
        r"(?:"
        r"故\s*选"
        r"|选择"
        r"|选\s*[:：]"
        r"|答\s*案\s*(?:是|为|选)?\s*[:：]?"
        r"|答\s*[:：]"
        r"|【答案】"
        r")\s*([A-Da-d])"
    )
    for match in re.finditer(pattern, section):
        found.append(match.group(1).upper())
    if not found:
        for match in re.finditer(
            r"(?:^|\n)\s*(?:\(\s*)?\d{1,2}\s*[.．、)）]\s*([A-Da-d])(?:\s|$)",
            section,
        ):
            found.append(match.group(1).upper())
    if not found:
        for match in re.finditer(r"(?:^|\n)\s*([A-D])\s*(?:\n|$)", section):
            found.append(match.group(1).upper())
    normalized_numbers = [
        str(int(number))
        for number in question_numbers or []
        if str(number or "").isdigit()
    ]
    if normalized_numbers:
        return {
            number: answer
            for number, answer in zip(normalized_numbers, found)
        }
    return {str(index): value for index, value in enumerate(found[:20], start=1)}


def _explicit_option_labels(value: str) -> set[str]:
    """Return option letters only when they carry visible option punctuation."""
    return {
        match.group(1).upper()
        for match in re.finditer(
            r"(?:^|[\s;；])([A-Da-d])\s*(?:[.．、:：)）])",
            str(value or ""),
            flags=re.MULTILINE,
        )
    }


def _infer_local_question_type(
    question_text: str,
    answer_text: str,  # noqa: ARG001
    number: str,  # noqa: ARG001
    *,
    section_type: str = "",
) -> str:
    value = str(question_text or "")
    normalized_section_type = str(section_type or "").strip()
    if normalized_section_type in {
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    }:
        return normalized_section_type
    if len(_explicit_option_labels(value)) >= 3:
        return "choice"
    if re.search(r"_{2,}|　{1,}|（\s*）|\(\s*\)|\b填空\b", value):
        return "fill_blank"
    if any(token in value for token in ["作图", "作出", "画出", "保留作图痕迹"]):
        return "comprehensive"
    if any(token in value for token in ["证明", "理由", "说明", "求证", "全等", "证得"]):
        return "proof"
    if re.search(r"[（(]\s*[1-9]\s*[）)]", value) or any(
        token in value for token in ["计算", "求", "解答", "解："]
    ):
        return "calculation"
    return infer_question_type_from_text(question_text)


def _extract_canonical_answer_for_local_question(
    *,
    number: str,
    qtype: str,
    question_text: str,  # noqa: ARG001
    answer_text: str,
    choice_answers: dict[str, str],
) -> str:
    if qtype == "choice":
        answer = (
            choice_answers.get(str(int(number))) if str(number or "").isdigit() else ""
        )
        if answer:
            return answer
        match = re.search(
            r"(?:故\s*选|选择|选\s*[:：]|答\s*案\s*(?:是|为|选)?\s*[:：]?|答\s*[:：]|【答案】)\s*([A-Da-d])",
            str(answer_text or ""),
        )
        if match:
            return match.group(1).upper()
        bare = re.fullmatch(r"\s*([A-Da-d])\s*", str(answer_text or ""))
        return bare.group(1).upper() if bare else ""
    if qtype != "fill_blank":
        return ""

    text = str(answer_text or "")
    patterns = [
        r"【答案】\s*([^。\n；;]{1,120})",
        r"故答案为\s*[:：]?\s*([^。\n；;]{1,120})",
        r"答案(?:是|为)\s*[:：]?\s*([^。\n；;]{1,120})",
        r"答案\s*[:：]\s*([^。\n；;]{1,120})",
        r"答\s*[:：]\s*([^。\n；;]{1,120})",
        r"故答案为\s*[:：]?\s*([\s\S]{1,80}?)[。．\n]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = _clean_local_answer_text(match.group(1))
            if value:
                return value
    for value in re.findall(r"\u3000\s*([^\u3000\n]{1,40}?)\s*\u3000", text):
        cleaned = _clean_local_answer_text(value)
        if cleaned:
            return cleaned
    return ""


def _clean_local_answer_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[：:，,、;\s]+|[。．，,、;\s]+$", "", text)
    text = text.replace("°", "").strip() if re.fullmatch(r"[\d.]+°", text) else text
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text in {"解", "如下", "见解析"}:
        return ""
    return text[:120]


def _local_accepted_forms(canonical: str, qtype: str) -> list[str]:
    value = str(canonical or "").strip()
    if not value:
        return []
    if qtype == "choice":
        return [value.upper()]
    candidates = [value]
    for chunk in re.split(r"[；;、，,]|\s+或\s+|\bor\b", value):
        cleaned = _clean_local_answer_text(chunk)
        if cleaned:
            candidates.append(cleaned)
    return merge_equivalent_forms([], *candidates, max_forms=16)


def _has_multiple_required_answers(question_text: str) -> bool:
    text = str(question_text or "")
    subquestion_numbers = {
        match.group(1)
        for match in re.finditer(r"[（(]\s*(\d{1,2})\s*[)）]", text)
    }
    if len(subquestion_numbers) >= 2:
        return True
    blank_count = len(re.findall(r"_{2,}|＿{2,}|　{2,}", text))
    return blank_count >= 2


def _is_local_answer_trusted(
    *,
    qtype: str,
    question_text: str,
    canonical_answer: str,
    answer_text: str,
    explicitly_mapped: bool,
) -> bool:
    """Trust only a complete-looking objective answer tied to this question."""
    canonical = str(canonical_answer or "").strip()
    if not explicitly_mapped or not canonical or not str(answer_text or "").strip():
        return False
    if qtype == "choice":
        return re.fullmatch(r"[A-D]", canonical.upper()) is not None
    if qtype == "fill_blank":
        return not _has_multiple_required_answers(question_text)
    return False


def _strip_leading_question_number(number: int, text: str) -> str:
    value = str(text or "").lstrip()
    value = re.sub(rf"^{number}\s*[.．]\s*", "", value, count=1)
    return value.strip()


def _split_doc_text_into_question_blocks(doc_text: str) -> list[dict[str, str]]:
    lines = _normalize_inline_main_question_lines(doc_text)
    markers: list[tuple[int, str]] = []
    last_number = 0
    for index, line in enumerate(lines):
        if _looks_like_answer_section_heading(line) and markers:
            break
        number = _extract_question_marker_number(line)
        if number is None:
            continue
        if markers and number <= last_number:
            break
        markers.append((index, f"Q{number}"))
        last_number = number
    blocks: list[dict[str, str]] = []
    for position, (start, qid) in enumerate(markers):
        end = (
            markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        )
        text = "\n".join(lines[start:end]).strip()
        if text:
            blocks.append({"question_id": qid, "text": text})
    return blocks


def _normalize_inline_main_question_lines(doc_text: str) -> list[str]:
    lines: list[str] = []
    current_number: int | None = None
    in_answer_section = False
    raw_lines = [line.rstrip() for line in str(doc_text or "").splitlines()]
    for index, line in enumerate(raw_lines):
        if _looks_like_answer_section_heading(line) and current_number is not None:
            in_answer_section = True
        if in_answer_section:
            lines.append(line)
            continue
        leading_number = _extract_question_marker_number(line)
        if leading_number is not None:
            current_number = leading_number
        if current_number is None:
            lines.append(line)
            continue
        segments, current_number = _split_consecutive_inline_main_questions(
            line,
            current_number=current_number,
            following_number=_next_leading_main_question_number(
                raw_lines, after_index=index
            ),
        )
        lines.extend(segments)
    return lines


def _split_consecutive_inline_main_questions(
    text: str,
    *,
    current_number: int,
    following_number: int | None,
) -> tuple[list[str], int]:
    split_offsets: list[int] = []
    expected_number = current_number + 1
    for marker in _INLINE_MAIN_QUESTION_MARKER.finditer(text):
        marker_number = int(marker.group("number"))
        if marker_number != expected_number:
            continue
        split_offsets.append(marker.start("number"))
        expected_number += 1
    if not split_offsets or expected_number != following_number:
        return [text], current_number
    boundaries = [0, *split_offsets, len(text)]
    return (
        [
            text[start:end].strip()
            for start, end in zip(boundaries, boundaries[1:])
            if text[start:end].strip()
        ],
        expected_number - 1,
    )


def _extract_question_marker_number(line: str) -> int | None:
    value = str(line or "").strip()
    if not value:
        return None
    for pattern in [
        r"^(?:Q|q)\s*(\d{1,2})(?:\b|[\s:：.．、)])",
        r"^第\s*(\d{1,2})\s*[题題]",
        r"^(\d{1,2})\s*[.．、)]",
    ]:
        match = re.match(pattern, value)
        if match and 1 <= int(match.group(1)) <= 99:
            return int(match.group(1))
    return None


def _looks_like_answer_section_heading(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    if any(marker in value for marker in ["参考答案", "试题解析", "答案与解析"]):
        return True
    return value.lower().strip(":： ") in {
        "answer",
        "answers",
        "solution",
        "solutions",
        "answer key",
    }


def _next_leading_main_question_number(
    texts: list[str], *, after_index: int
) -> int | None:
    for text in texts[after_index + 1 :]:
        if _looks_like_answer_section_heading(text):
            return None
        number = _extract_question_marker_number(text)
        if number is not None:
            return number
    return None


def infer_question_type_from_text(text: str) -> str:
    value = str(text or "")
    if len(_explicit_option_labels(value)) >= 3:
        return "choice"
    if re.search(r"_{2,}|[ \t]{3,}|　{1,}|（\s*）|\(\s*\)", value):
        return "fill_blank"
    if any(token in value for token in ["作图", "作出", "画出", "保留作图痕迹"]):
        return "comprehensive"
    if any(token in value for token in ["证明", "理由", "说明", "求证", "全等", "证得"]):
        return "proof"
    if re.search(r"[（(]\s*[1-9]\s*[）)]", value):
        return "calculation"
    return "comprehensive"


def split_inline_main_question_paragraphs(
    rich_paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    current_number: int | None = None
    in_answer_section = False
    plain_paragraphs = [
        _strip_inline_html(
            _INLINE_IMAGE_MARKER.sub("", str(paragraph.get("text") or ""))
        ).strip()
        for paragraph in rich_paragraphs
    ]
    for index, paragraph in enumerate(rich_paragraphs):
        text = str(paragraph.get("text") or "").strip()
        plain_text = plain_paragraphs[index]
        if _looks_like_answer_section_heading(plain_text):
            in_answer_section = True
        if in_answer_section or not text:
            normalized.append(paragraph)
            continue
        leading_number = _extract_question_marker_number(plain_text)
        if leading_number is not None:
            current_number = leading_number
        if current_number is None:
            normalized.append(paragraph)
            continue
        segments, final_number = _split_consecutive_inline_main_questions(
            _INLINE_IMAGE_MARKER.sub("", text).strip(),
            current_number=current_number,
            following_number=_next_leading_main_question_number(
                plain_paragraphs, after_index=index
            ),
        )
        if len(segments) == 1:
            normalized.append(paragraph)
            continue
        segments = _attach_inline_images_to_source_segments(
            paragraph,
            segments=segments,
            current_number=current_number,
            final_number=final_number,
        )
        normalized.extend({**paragraph, "text": segment} for segment in segments)
        current_number = final_number
    return normalized


def _attach_inline_images_to_source_segments(
    paragraph: dict[str, Any],
    *,
    segments: list[str],
    current_number: int,
    final_number: int,
) -> list[str]:
    fallback_paths = image_paths_from_rich_text(str(paragraph.get("text") or ""))

    def attach_to_last(paths: list[str]) -> list[str]:
        assigned = list(segments)
        for path in paths:
            assigned[-1] = f"{assigned[-1].rstrip()}\n[[IMAGE:{path}]]"
        return assigned

    relationships = paragraph.get("image_relationships")
    raw_xml = str(paragraph.get("xml") or "")
    if not isinstance(relationships, dict) or not relationships or not raw_xml:
        return attach_to_last(fallback_paths)
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError:
        return attach_to_last(fallback_paths)

    text_parts: list[str] = []
    positioned_images: list[tuple[int, str]] = []
    text_length = 0
    for element in root.iter():
        local_name = str(element.tag).split("}")[-1]
        if local_name == "t":
            value = str(element.text or "")
            text_parts.append(value)
            text_length += len(value)
            continue
        if local_name != "blip":
            continue
        relationship_id = next(
            (
                str(value)
                for key, value in element.attrib.items()
                if str(key).split("}")[-1] == "embed"
            ),
            "",
        )
        image_path = str(relationships.get(relationship_id) or "").strip()
        if image_path:
            positioned_images.append((text_length, image_path))
    if not positioned_images:
        return attach_to_last(fallback_paths)

    source_text = "".join(text_parts)
    expected_number = current_number + 1
    split_offsets: list[int] = []
    for marker in _INLINE_MAIN_QUESTION_MARKER.finditer(source_text):
        marker_number = int(marker.group("number"))
        if marker_number != expected_number:
            continue
        split_offsets.append(marker.start("number"))
        expected_number += 1
        if marker_number == final_number:
            break
    if len(split_offsets) != len(segments) - 1:
        return attach_to_last(fallback_paths)

    assigned = list(segments)
    positioned_paths: set[str] = set()
    for image_offset, image_path in positioned_images:
        segment_index = sum(image_offset >= offset for offset in split_offsets)
        assigned[segment_index] = (
            f"{assigned[segment_index].rstrip()}\n[[IMAGE:{image_path}]]"
        )
        positioned_paths.add(image_path)
    for image_path in fallback_paths:
        if image_path not in positioned_paths:
            assigned[-1] = f"{assigned[-1].rstrip()}\n[[IMAGE:{image_path}]]"
    return assigned


def flatten_answer_blocks(answer_map: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(answer_map, dict):
        for blocks in answer_map.values():
            if isinstance(blocks, list):
                result.extend(block for block in blocks if isinstance(block, dict))
    return result


def _rich_block_text(block: Any) -> str:
    return str(block.get("text") or "") if isinstance(block, dict) else str(block or "")


def rich_blocks_plain_text(blocks: list[Any]) -> str:
    return _strip_inline_html(
        _INLINE_IMAGE_MARKER.sub("", "\n".join(_rich_block_text(b) for b in blocks))
    )


def rich_text_for_model(value: Any) -> str:
    """Preserve visible structure while removing private image paths and HTML."""
    text = _INLINE_IMAGE_MARKER.sub("[图片]", str(value or ""))
    return _strip_inline_html(text).strip()


def _strip_inline_html(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"<sup\b[^>]*>(.*?)</sup>",
        lambda match: f"^({match.group(1)})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<sub\b[^>]*>(.*?)</sub>",
        lambda match: f"_({match.group(1)})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<u\b[^>]*>(.*?)</u>",
        lambda match: f"____{match.group(1)}____",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<br\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:td|th)\s*>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    return html.unescape(text)


def image_paths_from_rich_text(value: str) -> list[str]:
    seen: list[str] = []
    for match in _INLINE_IMAGE_MARKER.finditer(str(value or "")):
        path = match.group("path").strip()
        if path and path not in seen:
            seen.append(path)
    return seen


def _parse_answer_section_blocks(answer_blocks: list[Any]) -> tuple[str, str]:
    # The rich-content mapper has already assigned these blocks to one question's
    # answer section. Treat unheaded paragraphs as solution content as well:
    # many Word answer sheets put only the first answer on a labelled line and
    # continue the proof/derivation in ordinary paragraphs.
    in_solution = True
    awaiting_answer = False
    solution_lines: list[str] = []
    final_answer = ""
    for block in answer_blocks:
        text = _rich_block_text(block)
        for raw in text.splitlines() if "\n" in text else [text]:
            line = str(raw or "").strip()
            if not line:
                continue
            if "【答案】" in line:
                in_solution = False
                after = line.split("【答案】", 1)[1].strip()
                if after:
                    final_answer = _clean_local_answer_text(_strip_inline_html(after))
                    awaiting_answer = False
                    in_solution = True
                else:
                    awaiting_answer = True
                continue
            if awaiting_answer:
                final_answer = _clean_local_answer_text(_strip_inline_html(line))
                awaiting_answer = False
                in_solution = True
                continue
            if "【点评】" in line or "【点睛】" in line:
                in_solution = False
                continue
            if any(marker in line for marker in ("【解答】", "【分析】", "【解析】")):
                in_solution = True
                marker = next(
                    marker
                    for marker in ("【解答】", "【分析】", "【解析】")
                    if marker in line
                )
                after = line.split(marker, 1)[1].strip()
                if after:
                    solution_lines.append(after)
                continue
            if line.startswith("【") and "】" in line:
                in_solution = True
                after = line.split("】", 1)[1].strip()
                if after:
                    solution_lines.append(after)
                continue
            if in_solution:
                solution_lines.append(line)
    solution_text = "\n".join(solution_lines).strip()
    plain = _strip_inline_html(_INLINE_IMAGE_MARKER.sub("", solution_text))
    if not final_answer:
        match = re.search(r"故\s*选\s*[:：]?\s*([A-Da-d]+)", plain)
        if match:
            final_answer = match.group(1).upper()
        else:
            match = re.search(r"故\s*答\s*案\s*为\s*[:：]?\s*([^。\n．]+)", plain)
            if match:
                final_answer = match.group(1).strip().rstrip("．.")
    return final_answer, solution_text


def _merge_complete_rich_text(parts: list[str], additional: str) -> list[str]:
    extra = str(additional or "").strip()
    if not extra:
        return parts
    current = "\n".join(str(part or "").strip() for part in parts if str(part or "").strip())
    if not current:
        return [extra]

    def signature(value: str) -> str:
        visible = _strip_inline_html(str(value or ""))
        return re.sub(r"\s+", "", visible).strip()

    current_signature = signature(current)
    extra_signature = signature(extra)
    if not extra_signature or extra_signature == current_signature:
        return parts
    # Prefer the fuller projection when one sufficiently descriptive fragment
    # contains the other; short answers such as "A" are compared only exactly.
    if len(current_signature) >= 12 and current_signature in extra_signature:
        return [extra]
    if len(extra_signature) >= 12 and extra_signature in current_signature:
        return parts
    return [*parts, extra]


def parse_rich_question_blocks(
    number: int,
    question_blocks: list[Any],
    answer_blocks: list[Any] | None,
    choice_answers: dict[str, str],
    *,
    section_type: str = "",
) -> dict[str, Any] | None:
    lines: list[str] = []
    for block in question_blocks:
        text = _rich_block_text(block)
        lines.extend(text.splitlines() if "\n" in text else [text])
    bucket = "stem"
    stem_lines: list[str] = []
    answer_lines: list[str] = []
    analysis_lines: list[str] = []
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if "【答案】" in line:
            bucket = "answer"
            after = line.split("【答案】", 1)[1].strip()
            if after:
                answer_lines.append(after)
            continue
        if ("【解析】" in line) or ("【点睛】" in line):
            bucket = "analysis"
            marker = "【解析】" if "【解析】" in line else "【点睛】"
            after = line.split(marker, 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if line.startswith("【") and "】" in line:
            bucket = "analysis"
            after = line.split("】", 1)[1].strip()
            if after:
                analysis_lines.append(after)
            continue
        if bucket == "stem":
            stem_lines.append(line)
        elif bucket == "answer":
            answer_lines.append(line)
        else:
            analysis_lines.append(line)

    if answer_blocks:
        final, analysis = _parse_answer_section_blocks(answer_blocks)
        if final:
            answer_lines = _merge_complete_rich_text(answer_lines, final)
        if analysis:
            analysis_lines = _merge_complete_rich_text(analysis_lines, analysis)

    question_html = _strip_leading_question_number(
        number, "\n".join(stem_lines).strip()
    )
    answer_html = "\n".join(answer_lines).strip()
    analysis_html = "\n".join(analysis_lines).strip()
    image_paths = image_paths_from_rich_text(question_html)
    question_text = _strip_inline_html(
        _INLINE_IMAGE_MARKER.sub("", question_html)
    ).strip()
    answer_text = _strip_inline_html(
        _INLINE_IMAGE_MARKER.sub("", answer_html)
    ).strip()
    if not question_text and not answer_text and not analysis_html and not image_paths:
        return None

    num_str = str(number)
    qtype = _infer_local_question_type(
        question_text,
        answer_text,
        num_str,
        section_type=section_type,
    )
    canonical = _extract_canonical_answer_for_local_question(
        number=num_str,
        qtype=qtype,
        question_text=question_text,
        answer_text=answer_text,
        choice_answers=choice_answers,
    )
    local_answer_trusted = _is_local_answer_trusted(
        qtype=qtype,
        question_text=question_text,
        canonical_answer=canonical,
        answer_text=answer_text,
        explicitly_mapped=bool(answer_text and (answer_lines or answer_blocks)),
    )
    return {
        "question_id": f"Q{number}",
        "text": question_text,
        "question_text": question_text,
        "question_html": question_html,
        "answer_text": answer_text,
        "answer_html": answer_html,
        "analysis": _strip_inline_html(analysis_html).strip(),
        "analysis_html": analysis_html,
        "image_paths": image_paths,
        "question_type": qtype,
        "canonical_answer": canonical,
        "accepted_forms": _local_accepted_forms(canonical, qtype),
        "local_answer_trusted": local_answer_trusted,
        "needs_review": not local_answer_trusted,
    }
