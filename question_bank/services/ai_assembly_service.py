"""AI 组卷的编排服务。

题库概况构建、真卷模板结构提取、细目表（AssemblySpec）校验与确定性选题器
为纯确定性逻辑；``generate_spec`` 是唯一调用模型的入口，且只让 AI 做需求
解析与细目表编排，题目 100% 由选题器从本地题库确定性选出。发给模型的概况
只含统计与名录，不含题目正文；解析失败或截断直接报错，不重发请求。
"""

from __future__ import annotations

import math
import sqlite3
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.model_profiles.content_generation import resolve_content_generation_settings
from backend.llm.policy import LLMRequestKind
from llm_client import LLMClient
from question_bank.parsers.type_detector import (
    ESSAY_SUBTYPES,
    split_legacy_question_type,
)
from question_bank.services.ai_assembly_prompts import (
    SPEC_MAX_TOKENS,
    build_spec_prompt,
)

from question_bank.services import standard_difficulty
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
from question_bank.services.question_frequency_service import (
    QuestionFrequencyService,
    normalize_exam_type,
)
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

# 难度档取值范围（questions.difficulty 为 1.0–10.0 小数，即最难小问的公式分）。
MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 10
# 选题硬过滤允许难度偏离目标档的档数。
DIFFICULTY_TOLERANCE = 1
# 单行候选分页大小；题库为单机单用户规模，翻页取全量候选即可。
_CANDIDATE_PAGE_SIZE = 500
# 打分权重：知识点匹配 > 难度贴近 > 频度。
_WEIGHT_KNOWLEDGE = 3.0
_WEIGHT_DIFFICULTY = 2.0
_WEIGHT_FREQUENCY = 1.0
_DIFFICULTY_SCORE_SPAN = float(MAX_DIFFICULTY - MIN_DIFFICULTY)

# 兼容输入：迁移 034 执行前真实库题型仍是旧六值，解答题的三种旧写法
# 在选题与统计时归并到"解答题"，子类经 special_type 标签或旧题型串识别。
_LEGACY_ESSAY_TYPES = tuple(f"解答题（{subtype}）" for subtype in ESSAY_SUBTYPES)

_ACTIVE_QUESTION_WHERE = (
    "COALESCE(q.is_deleted, 0) = 0 "
    "AND COALESCE(p.import_status, '') <> 'deleted'"
)


class AssemblySpecError(ValueError):
    """细目表结构非法。"""


@dataclass(frozen=True, slots=True)
class SectionProfile:
    """单个 curriculum 小节下的题库统计与知识点名录（无题目正文）。"""

    section_id: str
    section_display_name: str
    chapter_id: str
    chapter_display_name: str
    volume_id: str
    volume_label: str
    question_count: int
    difficulty_distribution: dict[str, int]
    question_type_distribution: dict[str, int]
    # 解答题的子类分布（画图/计算/证明/未标注），无解答题的小节为空 dict。
    essay_subtype_distribution: dict[str, int]
    knowledge_points: tuple[str, ...]
    knowledge_inventory: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BankProfile:
    """题库概况：只含有题章节的统计与知识点名列表。"""

    total_questions: int
    sections: tuple[SectionProfile, ...]
    uncatalogued_knowledge_points: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    question_number: str
    question_type: str
    difficulty: float | None
    score: float | None = None
    # 同题型同难度的连续题目合并为一行，count 记录合并的题数。
    count: int = 1

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TemplateStructure:
    paper_id: int
    paper_title: str
    entries: tuple[TemplateEntry, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SpecRow:
    question_type: str
    count: int
    knowledge_points: tuple[str, ...] = ()
    difficulty: int | None = None
    score: float | None = None
    # 解答题子类（画图/计算/证明），仅 question_type == "解答题" 时有意义。
    essay_subtype: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "SpecRow":
        difficulty = payload.get("difficulty")
        score = payload.get("score")
        # 兼容输入：旧 payload 没有 essay_subtype 字段，按 None 读取。
        essay_subtype = str(payload.get("essay_subtype") or "").strip() or None
        return SpecRow(
            question_type=str(payload.get("question_type") or "").strip(),
            count=int(payload.get("count") or 0),
            knowledge_points=tuple(
                str(value).strip()
                for value in payload.get("knowledge_points") or ()
                if str(value or "").strip()
            ),
            difficulty=int(difficulty) if difficulty is not None else None,
            score=float(score) if score is not None else None,
            essay_subtype=essay_subtype,
        )


@dataclass(frozen=True, slots=True)
class AssemblySpec:
    title: str
    rows: tuple[SpecRow, ...]
    # 全卷范围为硬边界；行内知识点是范围内的优先选题目标。
    scope_knowledge_points: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "rows": [row.to_payload() for row in self.rows],
            "scope_knowledge_points": list(self.scope_knowledge_points),
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "AssemblySpec":
        return AssemblySpec(
            title=str(payload.get("title") or "").strip(),
            rows=tuple(
                SpecRow.from_payload(row)
                for row in payload.get("rows") or ()
                if isinstance(row, Mapping)
            ),
            scope_knowledge_points=tuple(
                str(value).strip()
                for value in payload.get("scope_knowledge_points") or ()
                if str(value or "").strip()
            ),
        )


@dataclass(frozen=True, slots=True)
class RelaxationSuggestion:
    """一条缺口放宽建议，sacrifice 为中文"牺牲说明"。"""

    step: str
    title: str
    sacrifice: str
    knowledge_points: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SelectionGap:
    row_index: int
    missing: int
    candidates: int
    excluded_by_dedupe: int
    suggestions: tuple[RelaxationSuggestion, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RowSelection:
    row_index: int
    question_ids: tuple[int, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SelectResult:
    rows: tuple[RowSelection, ...]
    gaps: tuple[SelectionGap, ...]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = Path(db_path).resolve(strict=False).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _parse_difficulty(raw: object) -> float | None:
    try:
        value = float(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    if not (MIN_DIFFICULTY <= value <= MAX_DIFFICULTY):
        return None
    return value


def _difficulty_band(raw: object) -> str:
    value = _parse_difficulty(raw)
    if value is None:
        return "未知"
    level = standard_difficulty.difficulty_level(value)
    return str(level) if level is not None else "未知"


# ---------------------------------------------------------------------------
# 题库概况
# ---------------------------------------------------------------------------

_PROFILE_CACHE_LOCK = threading.Lock()
# key: 解析后的题库路径；value: (questions 表最大 updated_at, BankProfile)
_PROFILE_CACHE: dict[str, tuple[str, BankProfile]] = {}


def _questions_generation(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT MAX(updated_at) FROM questions").fetchone()
    return str(row[0] or "")


def clear_bank_profile_cache() -> None:
    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE.clear()


def build_bank_profile(
    read_service: QuestionBankReadService,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> BankProfile:
    """按 curriculum 章节聚合题量、难度分布、题型分布与知识点名列表。

    题型分布按归一后四类统计（旧六值归并到"解答题"），解答题另附子类
    分布（画图/计算/证明/未标注）供模型决策。只输出统计与名录，绝不包含
    题目正文。进程内缓存以 questions 表最大 updated_at 作为失效依据。
    """

    db_path = Path(read_service.db_path).resolve(strict=False)
    catalog = catalog if catalog is not None else load_curriculum_catalog()
    with _connect_readonly(db_path) as connection:
        generation = _questions_generation(connection)
        cache_key = str(db_path)
        with _PROFILE_CACHE_LOCK:
            cached = _PROFILE_CACHE.get(cache_key)
        if cached is not None and cached[0] == generation:
            return cached[1]
        profile = _build_bank_profile(connection, catalog)
    with _PROFILE_CACHE_LOCK:
        _PROFILE_CACHE[cache_key] = (generation, profile)
    return profile


def _catalog_section_index(
    catalog: Mapping[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """知识点 display_name -> (volume, chapter, section)。"""

    index: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for volume in catalog.get("volumes", ()):
        for chapter in volume.get("chapters", ()):
            for section in chapter.get("sections", ()):
                for point in section.get("knowledge_points", ()):
                    index[str(point["display_name"])] = (volume, chapter, section)
    return index


def _build_bank_profile(
    connection: sqlite3.Connection,
    catalog: Mapping[str, Any],
) -> BankProfile:
    section_index = _catalog_section_index(catalog)
    rows = connection.execute(
        f"""
        SELECT q.id, q.paper_id, q.difficulty, q.question_type, p.exam_type, t.tag_value
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        JOIN question_tags t
          ON t.question_id = q.id AND t.tag_type = 'knowledge_point'
        WHERE {_ACTIVE_QUESTION_WHERE}
          AND COALESCE(t.tag_value, '') <> ''
        """
    ).fetchall()
    # 解答题子类标签（迁移 034 后写入）；旧库无标签时回退旧题型串识别。
    special_rows = connection.execute(
        f"""
        SELECT q.id, t.tag_value
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        JOIN question_tags t
          ON t.question_id = q.id AND t.tag_type = 'special_type'
        WHERE {_ACTIVE_QUESTION_WHERE}
          AND COALESCE(t.tag_value, '') <> ''
        """
    ).fetchall()
    special_by_question: dict[int, set[str]] = {}
    for row in special_rows:
        tag_value = str(row["tag_value"]).strip()
        if tag_value in ESSAY_SUBTYPES:
            special_by_question.setdefault(int(row["id"]), set()).add(tag_value)

    duplicate_ids = {
        int(row[0])
        for row in connection.execute("SELECT question_id FROM question_duplicate_links")
    }
    inventory: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row["tag_value"])
        if name not in section_index:
            continue
        point = inventory.setdefault(name, {"ids": set(), "papers": set(), "formal_papers": set(), "cells": {}})
        if row["paper_id"]:
            point["papers"].add(int(row["paper_id"]))
            if normalize_exam_type(row["exam_type"]):
                point["formal_papers"].add(int(row["paper_id"]))
        question_id = int(row["id"])
        if question_id in duplicate_ids or question_id in point["ids"]:
            continue
        point["ids"].add(question_id)
        base_type, legacy_subtype = split_legacy_question_type(row["question_type"])
        subtypes = tuple(sorted(special_by_question.get(question_id, set()) or ({legacy_subtype} if legacy_subtype else set())))
        key = (base_type or "未标注", _parse_difficulty(row["difficulty"]), subtypes)
        point["cells"][key] = point["cells"].get(key, 0) + 1

    # section_id -> 聚合状态；一题多知识点时在每个所属小节各计一次。
    per_section: dict[str, dict[str, Any]] = {}
    uncatalogued: dict[str, None] = {}
    question_ids: set[int] = set()
    for row in rows:
        question_id = int(row["id"])
        if question_id in duplicate_ids:
            continue
        question_ids.add(question_id)
        knowledge_name = str(row["tag_value"])
        location = section_index.get(knowledge_name)
        if location is None:
            uncatalogued.setdefault(knowledge_name)
            continue
        volume, chapter, section = location
        bucket = per_section.setdefault(
            str(section["id"]),
            {
                "volume": volume,
                "chapter": chapter,
                "section": section,
                "question_ids": set(),
                "difficulty": {},
                "question_type": {},
                "essay_subtype": {},
                "knowledge_points": [],
            },
        )
        if knowledge_name not in bucket["knowledge_points"]:
            bucket["knowledge_points"].append(knowledge_name)
        if question_id in bucket["question_ids"]:
            continue
        bucket["question_ids"].add(question_id)
        band = _difficulty_band(row["difficulty"])
        bucket["difficulty"][band] = bucket["difficulty"].get(band, 0) + 1
        # 兼容输入：旧六值题型归并到四类，子类从旧题型串带回。
        base_type, legacy_subtype = split_legacy_question_type(
            row["question_type"]
        )
        question_type = base_type or "未标注"
        bucket["question_type"][question_type] = (
            bucket["question_type"].get(question_type, 0) + 1
        )
        if question_type == "解答题":
            tagged = special_by_question.get(question_id, set())
            subtype = next(
                (value for value in ESSAY_SUBTYPES if value in tagged),
                None,
            )
            subtype = subtype or legacy_subtype or "未标注"
            bucket["essay_subtype"][subtype] = (
                bucket["essay_subtype"].get(subtype, 0) + 1
            )

    total = connection.execute(
        f"SELECT COUNT(*) FROM questions q "
        f"LEFT JOIN papers p ON p.id = q.paper_id WHERE {_ACTIVE_QUESTION_WHERE}"
    ).fetchone()
    sections = tuple(
        SectionProfile(
            section_id=section_id,
            section_display_name=str(state["section"]["display_name"]),
            chapter_id=str(state["chapter"]["id"]),
            chapter_display_name=str(state["chapter"]["display_name"]),
            volume_id=str(state["volume"]["id"]),
            volume_label=str(state["volume"]["label"]),
            question_count=len(state["question_ids"]),
            difficulty_distribution=dict(sorted(state["difficulty"].items())),
            question_type_distribution=dict(
                sorted(state["question_type"].items())
            ),
            essay_subtype_distribution={
                key: state["essay_subtype"][key]
                for key in (*ESSAY_SUBTYPES, "未标注")
                if state["essay_subtype"].get(key)
            },
            knowledge_points=tuple(state["knowledge_points"]),
            knowledge_inventory=tuple(
                {
                    "knowledge_point": name,
                    "question_count": len(inventory[name]["ids"]),
                    "paper_count": len(inventory[name]["papers"]),
                    "formal_paper_count": len(inventory[name]["formal_papers"]),
                    "combinations": [
                        {"question_type": key[0], "difficulty": key[1], "essay_subtypes": list(key[2]), "count": count}
                        for key, count in sorted(inventory[name]["cells"].items(), key=lambda pair: repr(pair[0]))
                    ],
                }
                for name in state["knowledge_points"]
            ),
        )
        for section_id, state in sorted(per_section.items())
    )
    return BankProfile(
        total_questions=int(total[0] or 0),
        sections=sections,
        uncatalogued_knowledge_points=tuple(sorted(uncatalogued)),
    )


# ---------------------------------------------------------------------------
# 模板结构提取
# ---------------------------------------------------------------------------


def extract_template_structure(
    read_service: QuestionBankReadService,
    paper_id: int,
) -> TemplateStructure:
    """按 question_number 顺序提取真卷模板的 [{题型, 难度档}]，分值留空。

    题型归一为四类（兼容输入：旧六值"解答题（子类）"并入"解答题"）；
    同题型同难度的连续行合并为一个模板行，count 记录题数，保持原有排序。
    """

    entries: list[TemplateEntry] = []
    paper_title = ""
    page = 1
    fetched = 0
    while True:
        result = read_service.list_questions(
            QuestionReadFilters(
                page=page,
                page_size=_CANDIDATE_PAGE_SIZE,
                paper_ids=(int(paper_id),),
                sort="paper_order",
            )
        )
        fetched += len(result.items)
        for item in result.items:
            paper_title = paper_title or str(item.get("paper_title") or "")
            raw_difficulty = _parse_difficulty(item.get("difficulty"))
            # 模板难度原样透传一位小数（如 8.8）；合行只按整数档判同，
            # 合并后的行难度记为该档位。
            difficulty = raw_difficulty
            level = standard_difficulty.difficulty_level(raw_difficulty)
            base_type, _legacy_subtype = split_legacy_question_type(
                item.get("question_type")
            )
            question_type = base_type or str(item.get("question_type") or "")
            number = str(item.get("question_number") or "")
            previous = entries[-1] if entries else None
            if (
                previous is not None
                and previous.question_type == question_type
                and standard_difficulty.difficulty_level(previous.difficulty)
                == level
            ):
                first_number = previous.question_number.split("-", 1)[0]
                entries[-1] = replace(
                    previous,
                    question_number=(
                        f"{first_number}-{number}"
                        if number and number != first_number
                        else previous.question_number
                    ),
                    difficulty=(
                        float(level) if level is not None else difficulty
                    ),
                    count=previous.count + 1,
                )
            else:
                entries.append(
                    TemplateEntry(
                        question_number=number,
                        question_type=question_type,
                        difficulty=difficulty,
                    )
                )
        if fetched >= result.total or not result.items:
            break
        page += 1
    return TemplateStructure(
        paper_id=int(paper_id),
        paper_title=paper_title,
        entries=tuple(entries),
    )


# ---------------------------------------------------------------------------
# 细目表校验
# ---------------------------------------------------------------------------


def validate_spec(spec: AssemblySpec) -> None:
    """校验细目表结构，非法行抛出 AssemblySpecError（中文说明）。"""

    if not spec.rows:
        raise AssemblySpecError("细目表为空：至少需要一行题型配置。")
    for index, row in enumerate(spec.rows):
        label = f"第 {index + 1} 行"
        if not row.question_type:
            raise AssemblySpecError(f"{label}：题型不能为空。")
        if not isinstance(row.count, int) or row.count < 1:
            raise AssemblySpecError(f"{label}：数量必须是大于等于 1 的整数。")
        if row.difficulty is not None and not (
            MIN_DIFFICULTY <= row.difficulty <= MAX_DIFFICULTY
        ):
            raise AssemblySpecError(
                f"{label}：难度档必须在 {MIN_DIFFICULTY}-{MAX_DIFFICULTY} 之间。"
            )
        if row.score is not None and row.score <= 0:
            raise AssemblySpecError(f"{label}：分值必须大于 0。")
        if any(not str(value).strip() for value in row.knowledge_points):
            raise AssemblySpecError(f"{label}：知识点名称不能为空。")
        if row.essay_subtype is not None:
            if row.essay_subtype not in ESSAY_SUBTYPES:
                raise AssemblySpecError(
                    f"{label}：解答题子类只支持 "
                    f"{'/'.join(ESSAY_SUBTYPES)}。"
                )
            if row.question_type != "解答题":
                raise AssemblySpecError(
                    f"{label}：只有解答题可以指定子类。"
                )


def _align_template_rows(spec: AssemblySpec, template: TemplateStructure) -> AssemblySpec:
    """沿模板题型区段排布，保留模型在同题型内部的配置顺序。"""
    expected: list[str] = [entry.question_type for entry in template.entries for _ in range(entry.count)]
    actual = [row.question_type for row in spec.rows for _ in range(row.count)]
    if sorted(expected) != sorted(actual):
        raise AssemblySpecError("细目表题型或题量与模板不一致，原结果已保留。请调整要求后重试。")
    if expected == actual:
        return spec
    queues: dict[str, deque[SpecRow]] = {}
    for row in spec.rows:
        queues.setdefault(row.question_type, deque()).append(row)
    blocks: list[tuple[str, int]] = []
    for question_type in expected:
        if blocks and blocks[-1][0] == question_type:
            blocks[-1] = (question_type, blocks[-1][1] + 1)
        else:
            blocks.append((question_type, 1))
    rows: list[SpecRow] = []
    for question_type, remaining in blocks:
        while remaining:
            row = queues[question_type].popleft()
            count = min(row.count, remaining)
            rows.append(replace(row, count=count))
            if row.count > count:
                queues[question_type].appendleft(replace(row, count=row.count - count))
            remaining -= count
    return replace(spec, rows=tuple(rows))


# ---------------------------------------------------------------------------
# 确定性选题器
# ---------------------------------------------------------------------------


def _knowledge_tags_for(
    connection: sqlite3.Connection,
    question_ids: list[int],
) -> dict[int, set[str]]:
    tags: dict[int, set[str]] = {}
    for start in range(0, len(question_ids), 500):
        chunk = question_ids[start : start + 500]
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT question_id, tag_value
            FROM question_tags
            WHERE tag_type = 'knowledge_point'
              AND COALESCE(tag_value, '') <> ''
              AND question_id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            tags.setdefault(int(row["question_id"]), set()).add(
                str(row["tag_value"])
            )
    return tags


def _recent_record_question_ids(
    workspace: AssemblyWorkspaceService,
    recent_records: int,
) -> set[int]:
    excluded: set[int] = set()
    for record in workspace.list_records(limit=max(1, recent_records)):
        excluded.update(record.question_ids)
    return excluded


def _row_scope_knowledge_points(
    row: SpecRow,
    scope_knowledge_points: tuple[str, ...],
) -> tuple[str, ...]:
    """教师考察范围是硬边界；有全卷范围时，行内知识点仅用于优先排序。"""

    return scope_knowledge_points or row.knowledge_points


def _normalized_row_type(row: SpecRow) -> tuple[str, str | None]:
    """行题型归一为四类，返回（题型, 解答题子类）。

    兼容输入：旧草稿行的"解答题（子类）"拆成大类 + 子类，与显式
    essay_subtype 走同一条子类过滤路径。
    """

    base_type, legacy_subtype = split_legacy_question_type(row.question_type)
    question_type = base_type or str(row.question_type or "").strip()
    subtype = row.essay_subtype or legacy_subtype
    if question_type != "解答题":
        subtype = None
    return question_type, subtype


def _paged_candidates(
    read_service: QuestionBankReadService,
    *,
    question_types: tuple[str, ...],
    special_types: tuple[str, ...] = (),
    difficulty_min: float | None,
    difficulty_max: float | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        result = read_service.list_questions(
            QuestionReadFilters(
                page=page,
                page_size=_CANDIDATE_PAGE_SIZE,
                question_types=question_types,
                special_types=special_types,
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                collapse_duplicates=True,
            )
        )
        items.extend(result.items)
        if len(items) >= result.total or not result.items:
            break
        page += 1
    return items


def _list_row_candidates(
    read_service: QuestionBankReadService,
    row: SpecRow,
) -> list[dict[str, Any]]:
    difficulty_min = None
    difficulty_max = None
    if row.difficulty is not None:
        difficulty_min = max(MIN_DIFFICULTY, row.difficulty - DIFFICULTY_TOLERANCE)
        difficulty_max = min(MAX_DIFFICULTY, row.difficulty + DIFFICULTY_TOLERANCE)
    question_type, subtype = _normalized_row_type(row)
    question_types = (question_type,)
    if question_type == "解答题":
        # 兼容输入：旧库三种"解答题（子类）"写法一并纳入候选。
        question_types = ("解答题", *_LEGACY_ESSAY_TYPES)
    items = _paged_candidates(
        read_service,
        question_types=question_types,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
    )
    if subtype is None:
        return items
    candidates = []
    for item in items:
        _, legacy_subtype = split_legacy_question_type(item.get("question_type"))
        known = {
            str(tag.get("tag_value"))
            for tag in item.get("tags", [])
            if tag.get("tag_type") == "special_type" and tag.get("tag_value") in ESSAY_SUBTYPES
        }
        if not known and legacy_subtype:
            known.add(legacy_subtype)
        # 未标注子类的综合解答题始终进入候选；已明确标注其他子类的题不冒充匹配。
        if not known or subtype in known:
            candidates.append({**item, "_assembly_subtype_match": subtype in known})
    return candidates


def _score_candidate(
    question_knowledge: set[str],
    row_knowledge: tuple[str, ...],
    raw_difficulty: object,
    target_difficulty: int | None,
    weighted_frequency: float,
) -> float:
    if row_knowledge:
        matched = len(question_knowledge.intersection(row_knowledge))
        knowledge_score = matched / len(row_knowledge)
    else:
        knowledge_score = 1.0
    if target_difficulty is None:
        difficulty_score = 1.0
    else:
        value = _parse_difficulty(raw_difficulty)
        difficulty_score = (
            1.0 - abs(value - target_difficulty) / _DIFFICULTY_SCORE_SPAN
            if value is not None
            else 0.5
        )
    return (
        _WEIGHT_KNOWLEDGE * knowledge_score
        + _WEIGHT_DIFFICULTY * difficulty_score
        + _WEIGHT_FREQUENCY * weighted_frequency
    )


def select_questions(
    spec: AssemblySpec,
    read_service: QuestionBankReadService,
    *,
    frequency_service: QuestionFrequencyService | None = None,
    workspace: AssemblyWorkspaceService | None = None,
    dedupe_enabled: bool = True,
    dedupe_recent_records: int = 3,
    exclude_ids: Iterable[int] = (),
) -> SelectResult:
    """范围/题型/难度过滤 -> 优先知识点与子类 -> 多样性优先 -> 整卷补位。

    去重排除集取最近 ``dedupe_recent_records`` 条组卷记录的 question_ids，
    可通过 ``dedupe_enabled=False`` 关闭。选不满的行产出缺口与放宽建议。
    """

    validate_spec(spec)
    dedupe_excluded: set[int] = set()
    if dedupe_enabled and workspace is not None:
        dedupe_excluded = _recent_record_question_ids(
            workspace,
            dedupe_recent_records,
        )
    chosen: set[int] = {int(value) for value in exclude_ids}
    reserved_ids = set(chosen)

    selections: list[RowSelection] = []
    gaps: list[SelectionGap] = []
    with _connect_readonly(Path(read_service.db_path)) as connection:
        # 第一遍只收集各行候选：频度指标整卷只批量计算一次，
        # 逐行调用会重复加载全库活跃题，造成选题整体耗时成倍放大。
        row_contexts: list[dict[str, Any]] = []
        all_candidate_ids: list[int] = []
        for row in spec.rows:
            scope = _row_scope_knowledge_points(
                row,
                spec.scope_knowledge_points,
            )
            items = _list_row_candidates(read_service, row)
            knowledge_tags = _knowledge_tags_for(
                connection,
                [int(item["id"]) for item in items],
            )
            # 全卷考察范围是硬边界；兼容未设置全卷范围的草稿使用行知识点。
            if scope:
                items = [
                    item
                    for item in items
                    if knowledge_tags.get(int(item["id"]), set()).intersection(
                        scope
                    )
                ]
            candidates = len(items)
            excluded_by_dedupe = 0
            available: list[dict[str, Any]] = []
            for item in items:
                question_id = int(item["id"])
                if question_id in dedupe_excluded:
                    excluded_by_dedupe += 1
                    continue
                if question_id in chosen:
                    continue
                available.append(item)
            row_contexts.append(
                {
                    "scope": scope,
                    "knowledge_tags": knowledge_tags,
                    "candidates": candidates,
                    "excluded_by_dedupe": excluded_by_dedupe,
                    "available": available,
                }
            )
            all_candidate_ids.extend(int(item["id"]) for item in available)

        frequencies: dict[int, float] = {}
        if frequency_service is not None and all_candidate_ids:
            metrics = frequency_service.metrics_for_questions(all_candidate_ids)
            frequencies = {
                question_id: (
                    metric.weighted_frequency if metric.available else 0.0
                )
                for question_id, metric in metrics.items()
            }

        ranked_by_row: list[list[int]] = []
        picked_by_row: list[list[int]] = []
        for row_index, row in enumerate(spec.rows):
            context = row_contexts[row_index]
            scope = context["scope"]
            knowledge_tags = context["knowledge_tags"]
            # 前序行已选题目在本行不可用（chosen 随选题推进增长）。
            scored = sorted(
                context["available"],
                key=lambda item: (
                    not bool(knowledge_tags.get(int(item["id"]), set()).intersection(row.knowledge_points)) if row.knowledge_points else False,
                    not item.get("_assembly_subtype_match", True),
                    -_score_candidate(
                        knowledge_tags.get(int(item["id"]), set()),
                        row.knowledge_points,
                        item.get("difficulty"),
                        row.difficulty,
                        frequencies.get(int(item["id"]), 0.0),
                    ),
                    int(item["id"]),
                ),
            )
            ranked_by_row.append([int(item["id"]) for item in scored])

            # 知识点多样性：同知识点题数上限按行均摊。
            per_knowledge_cap = 0
            if row.knowledge_points:
                per_knowledge_cap = max(
                    1,
                    math.ceil(row.count / len(row.knowledge_points)),
                )
            knowledge_used: dict[str, int] = {}
            picked: list[int] = []
            for item in scored:
                if len(picked) >= row.count:
                    break
                question_id = int(item["id"])
                if question_id in chosen:
                    continue
                matched = sorted(
                    knowledge_tags.get(question_id, set()).intersection(row.knowledge_points or scope)
                )
                if per_knowledge_cap and matched:
                    if all(
                        knowledge_used.get(name, 0) >= per_knowledge_cap
                        for name in matched
                    ):
                        continue
                    for name in matched:
                        knowledge_used[name] = knowledge_used.get(name, 0) + 1
                picked.append(question_id)
            # 多样性只影响优先顺序；不能让已符合教师范围、题型和难度的题被闲置。
            for question_id in ranked_by_row[-1]:
                if len(picked) >= row.count:
                    break
                if question_id not in chosen and question_id not in picked:
                    picked.append(question_id)
            chosen.update(picked)
            picked_by_row.append(picked)

        # 只在缺口时重排已选题：沿候选链挪出位置，避免宽泛行占走稀缺行的唯一题。
        owners = {qid: index for index, picked in enumerate(picked_by_row) for qid in picked}
        for root, row in enumerate(spec.rows):
            while len(picked_by_row[root]) < row.count:
                queue = deque([root])
                parents: dict[int, tuple[int, int]] = {}
                visited = {root}
                free: tuple[int, int] | None = None
                while queue and free is None:
                    current = queue.popleft()
                    for qid in ranked_by_row[current]:
                        owner = owners.get(qid)
                        if owner is None:
                            free = (current, qid)
                            break
                        if owner not in visited:
                            visited.add(owner)
                            parents[owner] = (current, qid)
                            queue.append(owner)
                if free is None:
                    break
                current, qid = free
                picked_by_row[current].append(qid)
                owners[qid] = current
                while current != root:
                    previous, moved = parents[current]
                    picked_by_row[current].remove(moved)
                    picked_by_row[previous].append(moved)
                    owners[moved] = previous
                    current = previous

        for row_index, row in enumerate(spec.rows):
            context = row_contexts[row_index]
            picked = picked_by_row[row_index]
            selections.append(
                RowSelection(row_index=row_index, question_ids=tuple(picked))
            )

            missing = row.count - len(picked)
            if missing > 0:
                gaps.append(
                    SelectionGap(
                        row_index=row_index,
                        missing=missing,
                        candidates=context["candidates"],
                        excluded_by_dedupe=context["excluded_by_dedupe"],
                        suggestions=suggest_relaxations(
                            row,
                            scope_knowledge_points=spec.scope_knowledge_points,
                            dedupe_enabled=dedupe_enabled,
                            excluded_by_dedupe=context["excluded_by_dedupe"],
                            read_service=read_service,
                            exclude_ids=reserved_ids | set(owners),
                            recent_ids=dedupe_excluded,
                        ),
                    )
                )
    return SelectResult(rows=tuple(selections), gaps=tuple(gaps))


# ---------------------------------------------------------------------------
# 缺口放宽建议
# ---------------------------------------------------------------------------


def neighbor_knowledge_points(
    read_service: QuestionBankReadService,
    knowledge_points: Iterable[str],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """同章近邻知识点：优先 knowledge_relations（parent/related，已确认），
    无关系时退化为同 curriculum_section 的其他知识点。"""

    seeds = tuple(
        dict.fromkeys(
            str(value).strip() for value in knowledge_points if str(value).strip()
        )
    )
    if not seeds:
        return ()
    neighbors: list[str] = []
    try:
        with _connect_readonly(Path(read_service.db_path)) as connection:
            placeholders = ", ".join("?" for _ in seeds)
            identity_rows = connection.execute(
                f"""
                SELECT stable_key, display_name
                FROM knowledge_tag_identities
                WHERE status = 'active' AND display_name IN ({placeholders})
                """,
                list(seeds),
            ).fetchall()
            key_by_name = {
                str(row["display_name"]): str(row["stable_key"])
                for row in identity_rows
            }
            seed_keys = set(key_by_name.values())
            if seed_keys:
                key_placeholders = ", ".join("?" for _ in seed_keys)
                relation_rows = connection.execute(
                    f"""
                    SELECT source_key, target_key
                    FROM knowledge_relations
                    WHERE relation_type IN ('parent', 'related')
                      AND status = 'confirmed'
                      AND (
                          source_key IN ({key_placeholders})
                          OR target_key IN ({key_placeholders})
                      )
                    """,
                    [*seed_keys, *seed_keys],
                ).fetchall()
                neighbor_keys = {
                    key
                    for row in relation_rows
                    for key in (str(row["source_key"]), str(row["target_key"]))
                    if key not in seed_keys
                }
                if neighbor_keys:
                    name_placeholders = ", ".join("?" for _ in neighbor_keys)
                    name_rows = connection.execute(
                        f"""
                        SELECT display_name
                        FROM knowledge_tag_identities
                        WHERE status = 'active'
                          AND stable_key IN ({name_placeholders})
                        """,
                        sorted(neighbor_keys),
                    ).fetchall()
                    neighbors.extend(
                        str(row["display_name"]) for row in name_rows
                    )
    except sqlite3.Error:
        neighbors = []
    if neighbors:
        return tuple(
            dict.fromkeys(name for name in neighbors if name not in seeds)
        )

    # 退化路径：同 curriculum_section 的其他知识点。
    catalog = catalog if catalog is not None else load_curriculum_catalog()
    fallback: list[str] = []
    for volume in catalog.get("volumes", ()):
        for chapter in volume.get("chapters", ()):
            for section in chapter.get("sections", ()):
                names = [
                    str(point["display_name"])
                    for point in section.get("knowledge_points", ())
                ]
                if any(seed in names for seed in seeds):
                    fallback.extend(
                        name for name in names if name not in seeds
                    )
    return tuple(dict.fromkeys(fallback))


def suggest_relaxations(
    row: SpecRow,
    *,
    scope_knowledge_points: Iterable[str] = (),
    dedupe_enabled: bool = True,
    excluded_by_dedupe: int = 0,
    read_service: QuestionBankReadService | None = None,
    catalog: Mapping[str, Any] | None = None,
    exclude_ids: Iterable[int] = (),
    recent_ids: Iterable[int] = (),
) -> tuple[RelaxationSuggestion, ...]:
    """只建议能增加未占用候选的调整；扩大范围由教师明确追加章节。"""
    if read_service is None or (row.difficulty is None and not (dedupe_enabled and excluded_by_dedupe)):
        return ()
    scope = _row_scope_knowledge_points(row, tuple(scope_knowledge_points))
    blocked = set(exclude_ids)
    recent = set(recent_ids) if dedupe_enabled else set()

    def eligible_ids(candidate_row: SpecRow) -> set[int]:
        items = _list_row_candidates(read_service, candidate_row)
        ids = [int(item["id"]) for item in items]
        if not scope:
            return set(ids) - blocked
        with _connect_readonly(Path(read_service.db_path)) as connection:
            tags = _knowledge_tags_for(connection, ids)
        return {qid for qid in ids if tags.get(qid, set()).intersection(scope)} - blocked

    original = eligible_ids(row)
    suggestions: list[RelaxationSuggestion] = []
    if dedupe_enabled and original.intersection(recent):
        suggestions.append(
            RelaxationSuggestion(
                step="disable_dedupe",
                title="关闭去重",
                sacrifice="最近组卷已用过的题目可能再次出现。",
            )
        )
    if row.difficulty is not None and (eligible_ids(replace(row, difficulty=None)) - original - recent):
        suggestions.append(
            RelaxationSuggestion(
                step="relax_difficulty",
                title="放宽难度限制",
                sacrifice=(
                    f"题目难度可能偏离目标 {row.difficulty} 档 ±1 以上。"
                ),
            )
        )
    return tuple(suggestions)


# ---------------------------------------------------------------------------
# 细目表生成（LLM 编排，唯一调用模型的入口）
# ---------------------------------------------------------------------------


class AssemblyModelNotConfiguredError(RuntimeError):
    """内容生成模型未配置，无法生成细目表。"""


@dataclass(frozen=True, slots=True)
class SpecGenerationRequest:
    """一次细目表生成请求：结构化参数 + 口语 + 重生成上下文。"""

    scope_keys: tuple[str, ...] = ()
    difficulty_ratio: Mapping[str, int] = field(default_factory=dict)
    type_counts: Mapping[str, int] = field(default_factory=dict)
    exam_types: tuple[str, ...] = ()
    years: tuple[int, ...] = ()
    free_text: str = ""
    # 自由组卷时用户选定的解答题子类（画图/计算/证明），作为模型硬约束。
    essay_subtype: str | None = None
    template_paper_id: int | None = None
    current_spec: AssemblySpec | None = None
    locked_question_ids: tuple[int, ...] = ()
    new_instruction: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "scope_keys": list(self.scope_keys),
            "difficulty_ratio": dict(self.difficulty_ratio),
            "type_counts": dict(self.type_counts),
            "exam_types": list(self.exam_types),
            "years": list(self.years),
            "free_text": self.free_text,
            "essay_subtype": self.essay_subtype,
            "template_paper_id": self.template_paper_id,
            "current_spec": (
                self.current_spec.to_payload() if self.current_spec else None
            ),
            "locked_question_ids": list(self.locked_question_ids),
            "new_instruction": self.new_instruction,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "SpecGenerationRequest":
        current_spec = payload.get("current_spec")
        template_paper_id = payload.get("template_paper_id")
        return SpecGenerationRequest(
            scope_keys=_clean_str_tuple(payload.get("scope_keys")),
            difficulty_ratio=_clean_int_mapping(payload.get("difficulty_ratio")),
            type_counts=_clean_int_mapping(payload.get("type_counts")),
            exam_types=_clean_str_tuple(payload.get("exam_types")),
            years=tuple(
                int(value)
                for value in payload.get("years") or ()
                if str(value or "").strip().isdigit()
            ),
            free_text=str(payload.get("free_text") or "").strip(),
            # 兼容输入：旧 payload 没有 essay_subtype 字段，按 None 读取。
            essay_subtype=(
                str(payload.get("essay_subtype") or "").strip() or None
            ),
            template_paper_id=(
                int(template_paper_id) if template_paper_id is not None else None
            ),
            current_spec=(
                AssemblySpec.from_payload(current_spec)
                if isinstance(current_spec, Mapping)
                else None
            ),
            locked_question_ids=tuple(
                int(value)
                for value in payload.get("locked_question_ids") or ()
                if isinstance(value, int) and not isinstance(value, bool)
            ),
            new_instruction=str(payload.get("new_instruction") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class GeneratedSpec:
    """细目表生成结果：校验通过的 spec + 可对外展示的模型名。"""

    spec: AssemblySpec
    model_name: str

    def to_payload(self) -> dict[str, Any]:
        return {"spec": self.spec.to_payload(), "model_name": self.model_name}


def _clean_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        text for item in value if (text := str(item or "").strip())
    )


def _clean_int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        key = str(raw_key or "").strip()
        if not key or isinstance(raw_count, bool):
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            cleaned[key] = count
    return cleaned


def resolve_scope_knowledge_points(
    scope_keys: Iterable[str],
    *,
    catalog: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """把范围选择（册/章/节/知识点 id 或知识点名）展开为知识点名列表。

    知识点名统一取 catalog 节点的 display_name，与 question_tags.tag_value
    的命名惯例一致。能匹配 catalog id 的 key 展开为该节点下全部知识点；
    不匹配的 key 按知识点名原样保留。结果去重并保持输入顺序。
    """

    keys = tuple(
        dict.fromkeys(
            str(value).strip() for value in scope_keys if str(value).strip()
        )
    )
    if not keys:
        return ()
    catalog = catalog if catalog is not None else load_curriculum_catalog()
    node_knowledge: dict[str, list[str]] = {}
    for volume in catalog.get("volumes", ()):
        volume_points: list[str] = []
        for chapter in volume.get("chapters", ()):
            chapter_points: list[str] = []
            for section in chapter.get("sections", ()):
                section_points = []
                for point in section.get("knowledge_points", ()):
                    name = str(point["display_name"])
                    section_points.append(name)
                    node_knowledge[str(point["id"])] = [name]
                node_knowledge[str(section["id"])] = section_points
                chapter_points.extend(section_points)
            node_knowledge[str(chapter["id"])] = chapter_points
            volume_points.extend(chapter_points)
        node_knowledge[str(volume["id"])] = volume_points
    resolved: list[str] = []
    for key in keys:
        expanded = node_knowledge.get(key)
        if expanded is not None:
            resolved.extend(expanded)
        else:
            resolved.append(key)
    return tuple(dict.fromkeys(resolved))


def generate_spec(
    request: SpecGenerationRequest,
    read_service: QuestionBankReadService,
    *,
    llm_client: Any | None = None,
    catalog: Mapping[str, Any] | None = None,
    report: Callable[[float, str, str], None] | None = None,
    raise_if_cancelled: Callable[[], None] | None = None,
) -> GeneratedSpec:
    """LLM 编排细目表：拼 prompt -> 单次 json_from_text -> 结构校验。

    只发 1 次模型请求；JSON 修复失败或输出截断时异常直接上抛，不重发。
    考察范围是用户设定的硬边界，由本函数确定性地写入 spec，不采用模型输出。
    """

    def _report(progress: float, stage: str, detail: str = "") -> None:
        if report is not None:
            report(progress, stage, detail)

    def _raise_if_cancelled() -> None:
        if raise_if_cancelled is not None:
            raise_if_cancelled()

    _report(0.05, "解析需求", "正在整理题库概况与组卷需求")
    profile = build_bank_profile(read_service, catalog=catalog)
    template = None
    if request.template_paper_id is not None:
        template = extract_template_structure(
            read_service,
            int(request.template_paper_id),
        )
        if not template.entries:
            raise AssemblySpecError(
                "模板试卷不存在或没有可用题目，请重新选择模板。"
            )
    scope_knowledge_points = resolve_scope_knowledge_points(
        request.scope_keys,
        catalog=catalog,
    )
    if scope_knowledge_points:
        scope_set = set(scope_knowledge_points)
        profile = replace(
            profile,
            sections=tuple(
                replace(
                    section,
                    knowledge_points=tuple(point for point in section.knowledge_points if point in scope_set),
                    knowledge_inventory=tuple(item for item in section.knowledge_inventory if item["knowledge_point"] in scope_set),
                )
                for section in profile.sections
                if scope_set.intersection(section.knowledge_points)
            ),
            uncatalogued_knowledge_points=tuple(point for point in profile.uncatalogued_knowledge_points if point in scope_set),
        )
    prompt = build_spec_prompt(
        {
            "bank_profile": profile.to_payload(),
            "template_structure": (
                template.to_payload() if template is not None else None
            ),
            "requirements": {
                "scope_knowledge_points": list(scope_knowledge_points),
                "difficulty_ratio": dict(request.difficulty_ratio),
                "type_counts": dict(request.type_counts),
                "exam_types": list(request.exam_types),
                "years": list(request.years),
                "free_text": request.free_text,
                "essay_subtype": request.essay_subtype,
            },
            "current_spec": (
                request.current_spec.to_payload()
                if request.current_spec is not None
                else None
            ),
            "locked": {
                "question_ids": list(request.locked_question_ids),
                "rule": "覆盖这些题目的细目表行必须原样保留",
            },
            "new_instruction": request.new_instruction,
        }
    )

    client = llm_client
    model_name = ""
    if client is None:
        settings = resolve_content_generation_settings()
        if settings is None:
            raise AssemblyModelNotConfiguredError(
                "未配置内容生成模型：请先在「大模型 API」设置中为"
                "「内容生成」任务绑定模型，再使用 AI 组卷。"
            )
        client = LLMClient(settings)
        model_name = str(settings.config_model or "")
    else:
        model_name = str(
            getattr(getattr(client, "settings", None), "config_model", "") or ""
        )

    _raise_if_cancelled()
    _report(0.4, "生成细目表", "已发起 1 次模型请求，正在等待返回")
    payload = client.json_from_text(
        prompt,
        extra_kwargs={"max_tokens": SPEC_MAX_TOKENS},
        request_kind=LLMRequestKind.ASSEMBLY,
    )
    _raise_if_cancelled()

    _report(0.85, "校验", "正在校验细目表结构")
    if not isinstance(payload, Mapping):
        raise AssemblySpecError("模型返回的细目表不是 JSON 对象。")
    spec = AssemblySpec.from_payload(payload)
    spec = replace(spec, scope_knowledge_points=scope_knowledge_points)
    validate_spec(spec)
    if template is not None:
        spec = _align_template_rows(spec, template)
    _report(0.95, "校验", "细目表校验通过")
    return GeneratedSpec(spec=spec, model_name=model_name)


__all__ = [
    "AssemblyModelNotConfiguredError",
    "AssemblySpec",
    "AssemblySpecError",
    "BankProfile",
    "DIFFICULTY_TOLERANCE",
    "GeneratedSpec",
    "MAX_DIFFICULTY",
    "MIN_DIFFICULTY",
    "RelaxationSuggestion",
    "RowSelection",
    "SectionProfile",
    "SelectResult",
    "SelectionGap",
    "SpecGenerationRequest",
    "SpecRow",
    "TemplateEntry",
    "TemplateStructure",
    "build_bank_profile",
    "clear_bank_profile_cache",
    "extract_template_structure",
    "generate_spec",
    "neighbor_knowledge_points",
    "resolve_scope_knowledge_points",
    "select_questions",
    "suggest_relaxations",
    "validate_spec",
]
