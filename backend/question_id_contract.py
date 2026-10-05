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
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


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


def canonical_parent_id(raw: object) -> str | None:
    """Return the canonical parent identity ``Qn`` for a supported spelling."""

    text = str(raw or "").strip()
    return _canonical_parent_text(text)


@lru_cache(maxsize=4096)
def _canonical_parent_text(text: str) -> str | None:
    match = _PARENT_RE.match(text)
    if not match:
        return None
    return f"Q{int(match.group(1))}"


def canonical_part_id(parent: object, part_num: int) -> str:
    """Build one canonical multi-part identity ``Qn(Pm)``."""

    canonical_parent = canonical_parent_id(parent)
    if canonical_parent is None:
        raise QuestionIdContractError(f"无法解析父题号: {parent!r}")
    if (
        not isinstance(part_num, int)
        or isinstance(part_num, bool)
        or part_num <= 0
    ):
        raise QuestionIdContractError(f"小问序号必须为正整数: {part_num!r}")
    return f"{canonical_parent}(P{part_num})"


def question_id_coordinates(
    raw: object,
    *,
    parent_id: object | None = None,
) -> tuple[int, int | None] | None:
    """Parse a supported canonical/legacy spelling into numeric coordinates.

    Bare ``P1``/``1`` spellings are accepted only when the caller supplies the
    containing parent.  This keeps compatibility at an explicit input seam
    without turning a context-free number into a guessed sub-question.
    """

    text = str(raw or "").strip()
    return _question_coordinates_text(text, None if parent_id is None else str(parent_id or "").strip())


@lru_cache(maxsize=4096)
def _question_coordinates_text(text: str, parent_id: str | None) -> tuple[int, int | None] | None:
    if not text:
        return None
    for pattern in (_PARENT_PART_PAREN_RE, _PARENT_PART_SEP_RE):
        match = pattern.fullmatch(text)
        if match:
            part_number = int(match.group(2))
            if part_number <= 0:
                return None
            return int(match.group(1)), part_number
    if parent_id is not None and not text.casefold().startswith("q"):
        parent = canonical_parent_id(parent_id)
        bare_match = _BARE_PART_RE.fullmatch(text)
        if parent is not None and bare_match is not None:
            part_number = int(bare_match.group(1))
            if part_number <= 0:
                return None
            return int(parent[1:]), part_number
    parent = canonical_parent_id(text)
    if parent is None:
        return None
    return int(parent[1:]), None


@lru_cache(maxsize=256)
def _known_question_coordinates(known_ids: tuple[str, ...]) -> dict[tuple[int, int | None], tuple[str, ...]]:
    buckets: dict[tuple[int, int | None], list[str]] = {}
    for known in known_ids:
        coordinates = question_id_coordinates(known)
        if not known or coordinates is None:
            continue
        bucket = buckets.setdefault(coordinates, [])
        if known not in bucket:
            bucket.append(known)
    return {coordinate: tuple(names) for coordinate, names in buckets.items()}


def resolve_known_question_id(
    raw: object,
    known_ids: Iterable[object],
) -> str | None:
    """Resolve one input/read-side spelling against known formal identities.

    This helper is for seams that have a score/result key set but not the full
    rubric document.  It never invents a target: duplicate known aliases for
    one coordinate, unknown part numbers, and context-free bare parts all
    return ``None``.
    """

    text = str(raw or "").strip()
    coordinates = question_id_coordinates(text)
    if not text or coordinates is None:
        return None

    known_by_coordinates = _known_question_coordinates(tuple(str(value or "").strip() for value in known_ids))

    exact_coordinate_matches = known_by_coordinates.get(coordinates, [])
    if len(exact_coordinate_matches) == 1:
        return exact_coordinate_matches[0]
    if len(exact_coordinate_matches) > 1:
        return None

    parent_number, part_number = coordinates
    if part_number != 1:
        return None

    # Qn(1) may fold to Qn only when the known key set has no formal children
    # for that parent.  If children exist, an unknown child must not be guessed
    # as the parent aggregate.
    has_known_children = any(
        known_parent == parent_number and known_part is not None
        for known_parent, known_part in known_by_coordinates
    )
    if has_known_children:
        return None
    parent_matches = known_by_coordinates.get((parent_number, None), [])
    return parent_matches[0] if len(parent_matches) == 1 else None


def _canonical_detail(parent: str, part_num: int) -> str:
    # Internal fast path; ``parent`` was already checked while building a
    # catalog.
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
            part_number = int(match.group(2))
            return part_number if part_number > 0 else None
    match = _BARE_PART_RE.match(text)
    if match:
        part_number = int(match.group(1))
        return part_number if part_number > 0 else None
    return None


def _register_alias(
    aliases: dict[str, str],
    raw_alias: object,
    canonical: str,
) -> None:
    alias = str(raw_alias or "").strip()
    if not alias:
        return
    previous = aliases.get(alias)
    if previous is not None and previous != canonical:
        raise QuestionIdContractError(
            f"题号别名 {alias!r} 同时指向 {previous} 与 {canonical}"
        )
    aliases[alias] = canonical


@dataclass(frozen=True, slots=True)
class QuestionIdCatalog:
    parent_ids: tuple[str, ...]
    detail_ids: tuple[str, ...]
    parts_by_parent: dict[str, tuple[str, ...]]
    aliases: dict[str, str]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> QuestionIdCatalog:
        return _build_catalog(document)

    def resolve(self, raw: object, parent_id: str | None = None) -> str | None:
        return _resolve_with_catalog(self, raw, parent_id)

    def resolve_detail(
        self,
        raw: object,
        parent_id: str | None = None,
    ) -> str | None:
        resolved = self.resolve(raw, parent_id=parent_id)
        return resolved if resolved in self.detail_ids else None

    def parent_for(self, raw: object) -> str | None:
        resolved = self.resolve(raw)
        if resolved in self.parent_ids:
            return resolved
        for parent_id, detail_ids in self.parts_by_parent.items():
            if resolved in detail_ids:
                return parent_id
        coordinates = question_id_coordinates(raw)
        if coordinates is None:
            return None
        candidate = f"Q{coordinates[0]}"
        return candidate if candidate in self.parent_ids else None

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
    parent = canonical_parent_id(raw_qid)
    if parent is None:
        raise QuestionIdContractError(f"无法解析父题号: {raw_qid!r}")
    parts = question.get("parts")
    aliases: dict[str, str] = {}
    _register_alias(aliases, parent, parent)
    _register_alias(aliases, raw_qid, parent)

    if not isinstance(parts, list) or len(parts) == 0:
        # 无小问：父题即唯一明细。
        return parent, [parent], aliases

    if len(parts) == 1:
        # 单小问：正式明细身份折叠为父题号。只有第 1 小问的历史写法
        # 可以兼容到父题；Qn(P2) 不能被猜成 Qn。
        only = parts[0]
        if not isinstance(only, Mapping):
            raise QuestionIdContractError(f"大题 {parent} 的小问不是对象")
        raw_pid = str(only.get("part_id") or "").strip()
        coordinates = question_id_coordinates(raw_pid, parent_id=parent)
        if coordinates is None:
            raise QuestionIdContractError(
                f"大题 {parent} 的小问号无法解析: {raw_pid!r}"
            )
        parent_number = int(parent[1:])
        if coordinates not in {(parent_number, None), (parent_number, 1)}:
            raise QuestionIdContractError(
                f"大题 {parent} 的唯一小问不能使用 {raw_pid!r}"
            )
        if _is_parent_qualified(raw_pid) or canonical_parent_id(raw_pid) == parent:
            _register_alias(aliases, raw_pid, parent)
        return parent, [parent], aliases

    detail_ids: list[str] = []
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            raise QuestionIdContractError(f"大题 {parent} 的小问不是对象")
        raw_pid = str(part.get("part_id") or "").strip()
        part_num = _parse_part_number(raw_pid, parent)
        if part_num is None:
            raise QuestionIdContractError(
                f"大题 {parent} 的小问号无法解析: {raw_pid!r}"
            )
        detail = _canonical_detail(parent, part_num)
        if detail in seen:
            collisions.append(detail)
            continue
        seen[detail] = raw_pid or detail
        detail_ids.append(detail)
        if _is_parent_qualified(raw_pid):
            _register_alias(aliases, raw_pid, detail)
        _register_alias(aliases, detail, detail)
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
        for raw_alias, canonical in question_aliases.items():
            _register_alias(aliases, raw_alias, canonical)

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
            # 单小问父题只兼容历史第 1 小问，不能把 P2/P3 猜成父题。
            if (
                int(match.group(2)) == 1
                and parent in detail_set
                and catalog.parts_by_parent.get(parent) == (parent,)
            ):
                return parent
            return None

    # 3) 裸小问，仅在给定父题时可解析。
    if parent_id is not None:
        parent = canonical_parent_id(parent_id)
        if parent is not None:
            part_num = _parse_part_number(text, parent)
            if part_num is not None:
                candidate = _canonical_detail(parent, part_num)
                if candidate in detail_set:
                    return candidate
                if (
                    part_num == 1
                    and parent in detail_set
                    and catalog.parts_by_parent.get(parent) == (parent,)
                ):
                    return parent

    # 4) 纯大题号（包括历史裸数字）。
    parent_candidate = canonical_parent_id(text)
    if parent_candidate is not None:
        if parent_candidate in parent_set or parent_candidate in detail_set:
            return parent_candidate
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
        parent, detail_ids, _ = _question_detail_ids(question)
        question["question_id"] = parent
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

    catalog = QuestionIdCatalog.from_document(rubric)
    normalized_rubric = canonicalize_question_document(rubric)
    normalized["rubric"] = normalized_rubric

    if not isinstance(answer_key, Mapping):
        return normalized
    normalized_answer_key = deepcopy(dict(answer_key))
    answer_questions = normalized_answer_key.get("questions")
    if not isinstance(answer_questions, list):
        normalized["answer_key"] = normalized_answer_key
        return normalized

    seen_answer_parents: set[str] = set()
    for question in answer_questions:
        if not isinstance(question, dict):
            continue
        raw_question_id = str(question.get("question_id") or "").strip()
        parent_id = canonical_parent_id(raw_question_id)
        if parent_id is None or parent_id not in catalog.parent_ids:
            raise QuestionIdContractError(
                f"标准答案父题号无法对应评分依据: {raw_question_id!r}"
            )
        if parent_id in seen_answer_parents:
            raise QuestionIdContractError(f"标准答案大题号重复: {parent_id}")
        seen_answer_parents.add(parent_id)
        question["question_id"] = parent_id
        canonical_parts = catalog.parts_by_parent.get(parent_id, ())
        parts = question.get("parts")
        if not isinstance(parts, list) or not canonical_parts:
            continue
        seen_parts: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                raise QuestionIdContractError(
                    f"标准答案大题 {parent_id} 的小问不是对象"
                )
            raw_part_id = str(part.get("part_id") or "").strip()
            canonical = catalog.resolve(raw_part_id, parent_id=parent_id)
            if canonical is None or canonical not in canonical_parts:
                raise QuestionIdContractError(
                    f"标准答案大题 {parent_id} 的小问号无法解析: "
                    f"{raw_part_id!r}"
                )
            if canonical in seen_parts:
                raise QuestionIdContractError(
                    f"标准答案大题 {parent_id} 存在重复规范小问号: "
                    f"{canonical}"
                )
            seen_parts.add(canonical)
            part["part_id"] = canonical

    normalized["answer_key"] = normalized_answer_key
    return normalized


__all__ = [
    "QuestionIdCatalog",
    "QuestionIdContractError",
    "canonical_parent_id",
    "canonical_part_id",
    "canonicalize_grading_config_payload",
    "canonicalize_question_document",
    "question_id_coordinates",
    "resolve_known_question_id",
]
