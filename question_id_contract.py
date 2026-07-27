"""Canonical question-id contract.

纯函数模块：把阅卷/评分文档里各种历史小问写法统一到规范题号，供绑定、批改、
完整性审计和标签投影共用。不依赖 UI、数据库或模型客户端。

规范格式：
- 大题号：``Q<正整数>``（例：``Q13``）。
- 小问号：``Q<正整数>(P<正整数>)``（例：``Q13(P1)``）。
- 只有一个小问的大题，其唯一明细号就是父题号本身（``Q1``），不写成 ``Q1(P1)``。

兼容别名（解析时接受，规范化时改写）：
- ``Q12(P1)`` / ``Q12(1)`` / ``Q12（1）`` / ``Q12-1`` / ``Q12_1`` / ``Q12_P1``。
- 裸 ``P1`` / ``1`` 只有在给定 ``parent_id`` 时才可解析，不能作为全局题号。
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


class QuestionIdContractError(ValueError):
    """题号无法解析或多个别名归一到同一规范号时抛出。"""


_PARENT_RE = re.compile(r"^Q?\s*(\d+)\s*$", re.IGNORECASE)
# Q12(P1) / Q12(1) / Q12（1）
_PARENT_PART_PAREN_RE = re.compile(
    r"^Q?\s*(\d+)\s*[\(（]\s*P?\s*(\d+)\s*[\)）]\s*$", re.IGNORECASE
)
# Q12-1 / Q12_1 / Q12_P3 / Q12.1
_PARENT_PART_SEP_RE = re.compile(
    r"^Q?\s*(\d+)\s*[\-_.]\s*P?\s*(\d+)\s*$", re.IGNORECASE
)
# 裸 P1 / 1 / (1) / （1）
_BARE_PART_RE = re.compile(r"^[\(（]?\s*P?\s*(\d+)\s*[\)）]?\s*$", re.IGNORECASE)


def _canonical_parent(raw: object) -> str | None:
    text = str(raw or "").strip()
    match = _PARENT_RE.match(text)
    if not match:
        return None
    return f"Q{int(match.group(1))}"


def _canonical_detail(parent: str, part_num: int) -> str:
    return f"{parent}(P{part_num})"


def _is_parent_qualified(raw: object) -> bool:
    """判断一个小问写法是否自带父题前缀（如 Q12(1)、Q12_P3）。

    裸写法（P1、1、(1)）不带父题信息，不能作为全局别名，只能在父题作用域内解析。
    """
    text = str(raw or "").strip()
    return bool(
        _PARENT_PART_PAREN_RE.match(text) or _PARENT_PART_SEP_RE.match(text)
    )


def _parse_part_number(raw: object, parent: str) -> int | None:
    """从一个小问 id（可能自带父题前缀或为裸小问）解析出小问序号。"""
    text = str(raw or "").strip()
    if not text:
        return None
    parent_digits = parent[1:]
    for pattern in (_PARENT_PART_PAREN_RE, _PARENT_PART_SEP_RE):
        match = pattern.match(text)
        if match:
            # 若带父题前缀，前缀必须与当前父题一致，避免跨题误配。
            if match.group(1) != parent_digits:
                return None
            return int(match.group(2))
    match = _BARE_PART_RE.match(text)
    if match:
        return int(match.group(1))
    return None


@dataclass(frozen=True, slots=True)
class QuestionIdCatalog:
    parent_ids: tuple[str, ...]
    detail_ids: tuple[str, ...]
    parts_by_parent: dict[str, tuple[str, ...]]
    aliases: dict[str, str]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "QuestionIdCatalog":
        return _build_catalog(document)

    def resolve(self, raw: object, parent_id: str | None = None) -> str | None:
        return _resolve_with_catalog(self, raw, parent_id)

    def expand(self, raw: object) -> tuple[str, ...]:
        resolved = self.resolve(raw)
        if resolved is None:
            return ()
        return self.parts_by_parent.get(resolved, (resolved,))


def _iter_questions(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    questions = document.get("questions") if isinstance(document, Mapping) else None
    if not isinstance(questions, list):
        return []
    return [q for q in questions if isinstance(q, Mapping)]


def _question_detail_ids(question: Mapping[str, Any]) -> tuple[str, list[str], dict[str, str]]:
    """返回 (父题规范号, 明细规范号列表, 该题的别名->规范号映射)。

    遇到同一父题下两个小问归一到同一规范号时抛 QuestionIdContractError。
    """
    raw_qid = str(question.get("question_id") or "").strip()
    # 非 Q<n> 形式的条目（如答题区里的 __student_name__、字母题号）不阻断整卷解析，
    # 原样透传，由调用方按需过滤；只在真正的规范号重复时才报错。
    parent = _canonical_parent(raw_qid) or raw_qid
    if not parent:
        return "", [], {}
    parts = question.get("parts")
    aliases: dict[str, str] = {parent: parent}

    if not isinstance(parts, list) or len(parts) == 0:
        # 无小问：父题即唯一明细。
        return parent, [parent], aliases

    if len(parts) == 1:
        # 单小问：明细号即父题号，并把该小问的原写法登记为别名。
        only = parts[0]
        detail = parent
        if isinstance(only, Mapping):
            raw_pid = str(only.get("part_id") or "").strip()
            if raw_pid and _is_parent_qualified(raw_pid):
                aliases[raw_pid] = detail
        return parent, [detail], aliases

    detail_ids: list[str] = []
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for index, part in enumerate(parts):
        raw_pid = str(part.get("part_id") or "").strip() if isinstance(part, Mapping) else ""
        part_num = _parse_part_number(raw_pid, parent)
        if part_num is None:
            # 无法从写法推断序号时，回退到 1 基位置序号，保证稳定可解析。
            part_num = index + 1
        detail = _canonical_detail(parent, part_num)
        if detail in seen:
            collisions.append(detail)
            continue
        seen[detail] = raw_pid or detail
        detail_ids.append(detail)
        if raw_pid and _is_parent_qualified(raw_pid):
            aliases[raw_pid] = detail
        aliases[detail] = detail
    if collisions:
        unique = ", ".join(dict.fromkeys(collisions))
        raise QuestionIdContractError(
            f"大题 {parent} 存在重复规范小问号: {unique}"
        )
    return parent, detail_ids, aliases


def _build_catalog(document: Mapping[str, Any]) -> QuestionIdCatalog:
    parent_ids: list[str] = []
    detail_ids: list[str] = []
    parts_by_parent: dict[str, tuple[str, ...]] = {}
    aliases: dict[str, str] = {}

    for question in _iter_questions(document):
        parent, question_details, question_aliases = _question_detail_ids(question)
        if not parent:
            continue
        if parent in parts_by_parent:
            raise QuestionIdContractError(f"大题号重复: {parent}")
        parent_ids.append(parent)
        detail_ids.extend(question_details)
        parts_by_parent[parent] = tuple(question_details)
        aliases.update(question_aliases)

    return QuestionIdCatalog(
        parent_ids=tuple(parent_ids),
        detail_ids=tuple(detail_ids),
        parts_by_parent=parts_by_parent,
        aliases=aliases,
    )


def _resolve_with_catalog(
    catalog: QuestionIdCatalog, raw: object, parent_id: str | None
) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None

    detail_set = set(catalog.detail_ids)
    parent_set = set(catalog.parent_ids)

    # 1) 直接命中已知规范号或已登记别名。
    if text in detail_set or text in parent_set:
        return text
    if text in catalog.aliases:
        return catalog.aliases[text]

    # 2) 带父题前缀的小问写法（Q12(1)、Q12-2 等）。
    for pattern in (_PARENT_PART_PAREN_RE, _PARENT_PART_SEP_RE):
        match = pattern.match(text)
        if match:
            parent = f"Q{int(match.group(1))}"
            candidate = _canonical_detail(parent, int(match.group(2)))
            if candidate in detail_set:
                return candidate
            # 单小问父题：其明细号是裸父题号。
            if parent in detail_set and catalog.parts_by_parent.get(parent) == (parent,):
                return parent
            return None

    # 3) 纯大题号。
    parent_candidate = _canonical_parent(text)
    if parent_candidate is not None and text == parent_candidate:
        if parent_candidate in parent_set or parent_candidate in detail_set:
            return parent_candidate

    # 4) 裸小问，仅在给定父题时可解析。
    if parent_id is not None:
        parent = _canonical_parent(parent_id)
        if parent is not None:
            part_num = _parse_part_number(text, parent)
            if part_num is not None:
                candidate = _canonical_detail(parent, part_num)
                if candidate in detail_set:
                    return candidate
                if parent in detail_set and catalog.parts_by_parent.get(parent) == (parent,):
                    return parent
    return None


def canonicalize_question_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """返回把所有小问号改写为规范号的深拷贝，绝不修改入参。"""
    normalized = deepcopy(dict(document))
    questions = normalized.get("questions")
    if not isinstance(questions, list):
        return normalized

    for question in questions:
        if not isinstance(question, dict):
            continue
        _, detail_ids, _ = _question_detail_ids(question)
        parts = question.get("parts")
        if not isinstance(parts, list) or len(parts) == 0:
            continue
        for part, canonical in zip(parts, detail_ids):
            if isinstance(part, dict):
                part["part_id"] = canonical
    return normalized


def canonicalize_grading_config_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an in-memory copy with rubric/answer-key part IDs kept in sync.

    The rubric is the authority for the question hierarchy. Historical aliases
    such as ``Q10(1)`` remain accepted, while the returned copy always exposes
    multi-part items as ``Q10(P1)``. The input mapping is never modified.
    """
    normalized = deepcopy(dict(payload))
    rubric = normalized.get("rubric")
    answer_key = normalized.get("answer_key")
    if not isinstance(rubric, Mapping):
        return normalized

    try:
        catalog = QuestionIdCatalog.from_document(rubric)
        normalized_rubric = canonicalize_question_document(rubric)
    except QuestionIdContractError:
        return normalized
    normalized["rubric"] = normalized_rubric

    if not isinstance(answer_key, Mapping):
        return normalized
    normalized_answer_key = deepcopy(dict(answer_key))
    answer_questions = normalized_answer_key.get("questions")
    if not isinstance(answer_questions, list):
        normalized["answer_key"] = normalized_answer_key
        return normalized

    for question in answer_questions:
        if not isinstance(question, dict):
            continue
        raw_question_id = str(question.get("question_id") or "").strip()
        parent_id = _canonical_parent(raw_question_id) or raw_question_id
        canonical_parts = catalog.parts_by_parent.get(parent_id, ())
        parts = question.get("parts")
        if not isinstance(parts, list) or not canonical_parts:
            continue
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            raw_part_id = str(part.get("part_id") or "").strip()
            canonical = catalog.resolve(raw_part_id, parent_id=parent_id)
            if canonical is None and index < len(canonical_parts):
                canonical = canonical_parts[index]
            if canonical is not None:
                part["part_id"] = canonical

    normalized["answer_key"] = normalized_answer_key
    return normalized
