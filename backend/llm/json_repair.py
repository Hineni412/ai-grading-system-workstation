"""Deterministic, local-only repair for model JSON responses.

The repairer only fixes syntax that can be decided without inventing business
content. It never closes truncated objects and never asks a model for help.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LocalJsonRepairReport:
    repaired: bool
    operations: tuple[str, ...]
    response_chars: int
    response_sha256: str


@dataclass(frozen=True, slots=True)
class LocalJsonParseResult:
    payload: dict[str, Any]
    report: LocalJsonRepairReport


_MISSING_COLON = re.compile(
    r'(?P<prefix>(?:^|[{,])\s*)(?P<key>"(?:\\.|[^"\\])*")'
    r'(?P<gap>\s*)(?P<value>["{\[\-0-9tfn])'
)


def parse_json_object_locally(text: str) -> LocalJsonParseResult:
    """Parse one JSON object after conservative, deterministic syntax repair."""
    original = str(text or "")
    response_chars = len(original)
    response_sha256 = (
        hashlib.sha256(original.encode("utf-8")).hexdigest() if original else ""
    )
    operations: list[str] = []
    candidate = _strip_code_fence(original)
    extracted = _extract_complete_object(candidate)
    if extracted != candidate.strip():
        operations.append("extract_json_object")
    candidate = extracted

    parsed = _loads_object(candidate)
    if parsed is not None:
        return _result(parsed, operations, response_chars, response_sha256)

    candidate, changed = _repair_string_escapes(candidate)
    if "control" in changed:
        operations.append("escape_control_character")
    if "backslash" in changed:
        operations.append("escape_invalid_backslash")

    candidate, count = _MISSING_COLON.subn(
        lambda match: (
            f'{match.group("prefix")}{match.group("key")}:'
            f'{match.group("gap")}{match.group("value")}'
        ),
        candidate,
    )
    if count:
        operations.append("insert_missing_colon")

    candidate, removed = _remove_trailing_commas(candidate)
    if removed:
        operations.append("remove_trailing_comma")

    candidate, inserted_commas = _insert_parser_confirmed_commas(candidate)
    if inserted_commas:
        operations.append("insert_missing_comma")

    parsed = _loads_object(candidate)
    if parsed is None:
        raise _safe_parse_error(original, response_chars, response_sha256)
    return _result(parsed, operations, response_chars, response_sha256)


def _result(
    payload: dict[str, Any],
    operations: list[str],
    response_chars: int,
    response_sha256: str,
) -> LocalJsonParseResult:
    return LocalJsonParseResult(
        payload=payload,
        report=LocalJsonRepairReport(
            repaired=bool(operations),
            operations=tuple(dict.fromkeys(operations)),
            response_chars=response_chars,
            response_sha256=response_sha256,
        ),
    )


def _loads_object(candidate: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        raise ValueError("模型返回 JSON 顶层必须为对象")
    return parsed


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip().lstrip("\ufeff")
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_complete_object(text: str) -> str:
    cleaned = text.strip()
    start = cleaned.find("{")
    if start < 0:
        raise _safe_parse_error(cleaned, len(cleaned), _sha256(cleaned))
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or (stack[-1], char) not in {("{", "}"), ("[", "]")}:
                return cleaned[start:]
            stack.pop()
            if not stack:
                return cleaned[start : index + 1]
    raise _safe_parse_error(cleaned, len(cleaned), _sha256(cleaned))


def _repair_string_escapes(text: str) -> tuple[str, set[str]]:
    output: list[str] = []
    changed: set[str] = set()
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            output.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if next_char in '"\\/bfnrt':
                output.extend((char, next_char))
                index += 2
                continue
            if next_char == "u" and re.match(r"^[0-9a-fA-F]{4}$", text[index + 2 : index + 6]):
                output.append(text[index : index + 6])
                index += 6
                continue
            output.append("\\\\")
            changed.add("backslash")
            index += 1
            continue
        if ord(char) < 0x20:
            output.append(json.dumps(char)[1:-1])
            changed.add("control")
        else:
            output.append(char)
        index += 1
    return "".join(output), changed


def _remove_trailing_commas(text: str) -> tuple[str, int]:
    output: list[str] = []
    removed = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                removed += 1
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output), removed


def _insert_parser_confirmed_commas(text: str) -> tuple[str, int]:
    candidate = text
    inserted = 0
    for _attempt in range(32):
        try:
            json.loads(candidate)
            return candidate, inserted
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting ',' delimiter":
                return candidate, inserted
            position = exc.pos
            if position < 0 or position >= len(candidate):
                return candidate, inserted
            next_char = candidate[position]
            previous_index = position - 1
            while previous_index >= 0 and candidate[previous_index].isspace():
                previous_index -= 1
            previous_char = candidate[previous_index] if previous_index >= 0 else ""
            # Only structural token boundaries are safe. A letter at the error
            # position is usually an unescaped quote inside business text.
            if previous_char not in '}]"eElL0123456789' or next_char not in '{["tfn-0123456789':
                return candidate, inserted
            candidate = candidate[:position] + "," + candidate[position:]
            inserted += 1
    return candidate, inserted


def _safe_parse_error(text: str, response_chars: int, response_sha256: str) -> ValueError:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        location = f"解析位置 line {exc.lineno}, col {exc.colno}: {exc.msg}。"
    else:
        location = ""
    return ValueError(
        "模型返回非 JSON，本地修复后仍无法解析。"
        f"{location}响应字符数: {response_chars}；响应摘要: {response_sha256 or '无'}。"
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
