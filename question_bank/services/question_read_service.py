from __future__ import annotations

import json
import hashlib
import math
import os
import pickle
import re
import sqlite3
import stat
import tempfile
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from backend.file_access import (
    ControlledFileForbidden,
    ResolvedFile,
    resolve_controlled_file,
)
from backend.performance.metrics import instrument_sqlite_connection
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
    ResolvedKnowledge,
)
from question_bank.models.question import (
    ALLOWED_TAG_TYPES,
    has_complete_analysis_tags,
)
from question_bank.models.tag_schema import ERROR_PRONE_CATEGORIES, TagAnalysis
from question_bank.services.asset_path_service import (
    AmbiguousQuestionBankAssetPathError,
    resolve_question_bank_asset_path,
)
from question_bank.services.preview_html import block_preview_html
from question_bank.services.question_revision import (
    question_revision,
    question_revisions,
)
from question_bank.services.rich_content_service import (
    clean_question_blocks,
    strip_question_source_score,
    strip_question_source_score_blocks,
)
from question_bank.services.similar_question_ranker import (
    SimilarQuestionIndex,
)
from question_bank.services.similar_question_ranker import (
    build_index as build_similar_question_index,
)
from question_bank.services.similar_question_ranker import (
    dice as similarity_dice,
)
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_chapter_exam_scope_values,
    curriculum_knowledge_ancestors,
    curriculum_knowledge_node,
    curriculum_volume,
    load_curriculum_catalog,
    teaching_progress_allowed_exam_scope_values,
    teaching_progress_allowed_prefixes,
    teaching_progress_allowed_stable_keys,
)
from question_bank.taxonomy.governance import get_taxonomy_governance

ANALYSIS_TAG_TYPES = (
    "knowledge_point",
    "method",
    "thought",
    "ability",
    "model",
    "error_type",
    "exam_scope",
)

_CRITERIA_NEEDS_REVIEW_SQL = """
EXISTS (
    SELECT 1
    FROM training_criterion_heads head
    JOIN training_criterion_versions version
      ON version.version_id = head.current_version_id
    WHERE head.question_id = {qid}
      AND NOT EXISTS (
          SELECT 1 FROM training_criterion_versions approved
          WHERE approved.version_id = head.approved_version_id
            AND approved.status = 'approved'
            AND approved.source_content_hash = head.current_source_hash
      )
      AND (
            version.status IN ('rejected', 'stale')
            OR (
                version.status = 'proposed'
                AND COALESCE(version.quality_status, '') <> 'passed'
            )
      )
)
"""

_IMAGE_MARKER_PATTERN = re.compile(
    r"\[\[IMAGE:(?P<path>[^\]|]*?)(?:\|[^\]]*)?\]\]",
    re.DOTALL | re.IGNORECASE,
)
_RICH_INLINE_TOKEN_PATTERN = re.compile(
    r"(<br\s*/?>|</?(?:sup|sub|u)>)",
    re.IGNORECASE,
)
_RICH_TABLE_ROW_PATTERN = re.compile(
    r"<tr>(.*?)</tr>",
    re.IGNORECASE | re.DOTALL,
)
_RICH_TABLE_CELL_PATTERN = re.compile(
    r"<td>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_FILE_URI_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])file:",
    re.IGNORECASE,
)
_WINDOWS_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]"
)
_UNC_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_:/\\])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+"
)
_POSIX_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![\w/])/(?!/)(?:[^\s/\\]+/)+[^\s/\\]+"
)
_TAG_STATUS_MAP = {
    "all": None,
    "tagged": "已打标签",
    "untagged": "未打标签",
}

# 归属就绪：判定点关联已派生出知识点+章/小节标签，或写入侧已标记
# derived_pending（打标完成、等判定点关联）。与 models/question.py
# 的 analysis_ownership_satisfied 同口径：只有前置知识行（全部
# supporting_prerequisite 关联）也算归属就绪。
_OWNERSHIP_READY_SQL = """
    (
        EXISTS (SELECT 1 FROM question_tags t WHERE t.question_id = q.id
                AND t.tag_type = 'knowledge_point' AND COALESCE(t.tag_value, '') <> '')
        AND EXISTS (SELECT 1 FROM question_tags t WHERE t.question_id = q.id
                AND t.tag_type IN ('exam_scope', 'curriculum_section')
                AND COALESCE(t.tag_value, '') <> '')
    )
    OR EXISTS (SELECT 1 FROM question_tags t WHERE t.question_id = q.id
               AND t.tag_type = 'prerequisite' AND COALESCE(t.tag_value, '') <> '')
    OR EXISTS (SELECT 1 FROM question_tags t WHERE t.question_id = q.id
               AND t.tag_type = 'tag_status' AND t.tag_value = 'derived_pending')
"""


def build_question_filter_query(
    *,
    question_number: str | None = None,
    keyword: str | None = None,
    knowledge_point: str | None = None,
    difficulty: str | None = None,
    difficulty_range: tuple[float, float] | None = None,
    question_types: list[str] | None = None,
    paper_ids: list[int] | None = None,
    years: list[str] | None = None,
    exam_types: list[str] | None = None,
    grades: list[str] | None = None,
    curriculum_volume_ids: list[str] | None = None,
    tag_filters: dict[str, list[str]] | None = None,
    is_deleted: bool = False,
    tag_status: str | None = None,
) -> tuple[list[str], list[str], list[Any]]:
    """Build the shared read query without depending on the retired service."""

    cleaned_paper_ids = [int(value) for value in paper_ids or [] if int(value) > 0]
    # Reused questions have no questions row in this paper; their
    # paper-local membership lives in paper_question_occurrences.  The
    # join literalizes already-sanitized integer ids so join params do
    # not interleave with WHERE params.  When a paper filter is active,
    # ``papers p`` resolves to the viewing paper so paper attributes and
    # the question number display the importing paper's own values.
    joins: list[str] = []
    if cleaned_paper_ids:
        inline_ids = ", ".join(str(value) for value in cleaned_paper_ids)
        joins.append(
            "LEFT JOIN paper_question_occurrences occ "
            f"ON occ.question_id = q.id AND occ.paper_id IN ({inline_ids})"
        )
        joins.append(
            "LEFT JOIN papers p ON p.id = COALESCE(occ.paper_id, q.paper_id)"
        )
    else:
        joins.append("LEFT JOIN papers p ON p.id = q.paper_id")
    where = ["q.is_deleted = ?", "COALESCE(p.import_status, '') <> 'deleted'"]
    params: list[Any] = [1 if is_deleted else 0]
    if clean := _filter_text(knowledge_point):
        joins.append(
            "JOIN question_tags kt ON kt.question_id = q.id "
            "AND kt.tag_type = 'knowledge_point' AND kt.tag_value LIKE ?"
        )
        params.append(f"%{clean}%")
    if clean := _filter_text(question_number):
        number_column = (
            "COALESCE(occ.question_number, q.question_number)"
            if cleaned_paper_ids
            else "q.question_number"
        )
        where.append(f"{number_column} = ?")
        params.append(clean)
    if clean := _filter_text(keyword):
        where.append("(q.question_text LIKE ? OR COALESCE(q.answer_text, '') LIKE ?)")
        params.extend([f"%{clean}%", f"%{clean}%"])
    if clean := _filter_text(difficulty):
        # 精确难度是档位语义：数值输入匹配最近整数档（7 命中 6.5–7.4）；
        # 非数值输入沿用原文精确匹配。
        try:
            level = float(clean)
        except (TypeError, ValueError):
            where.append("q.difficulty = ?")
            params.append(clean)
        else:
            where.append(
                "CAST(q.difficulty AS REAL) >= ? "
                "AND CAST(q.difficulty AS REAL) < ?"
            )
            params.extend([level - 0.5, level + 0.5])
    if difficulty_range is not None:
        first, second = (
            float(difficulty_range[0]),
            float(difficulty_range[1]),
        )
        # 闭区间：两端都含（[4,6] 含 4.0 与 6.0，不含 3.9 与 6.1）。
        where.append("CAST(q.difficulty AS REAL) BETWEEN ? AND ?")
        params.extend([min(first, second), max(first, second)])
    for column, values in (
        ("q.question_type", question_types),
        ("CAST(p.year AS TEXT)", years),
        ("p.exam_type", exam_types),
        ("p.grade", grades),
    ):
        cleaned = [item for value in values or [] if (item := _filter_text(value))]
        if cleaned:
            placeholders = ", ".join("?" for _ in cleaned)
            where.append(f"{column} IN ({placeholders})")
            params.extend(cleaned)
    requested_volume_ids = list(
        dict.fromkeys(
            item
            for value in curriculum_volume_ids or []
            if (item := _filter_text(value))
        )
    )
    if requested_volume_ids:
        volumes = [
            volume
            for volume_id in requested_volume_ids
            if (volume := curriculum_volume(volume_id=volume_id)) is not None
        ]
        if len(volumes) != len(requested_volume_ids):
            where.append("1 = 0")
        else:
            clauses = []
            for volume in volumes:
                clauses.append(
                    "(p.grade = ? AND p.semester = ? AND p.textbook_version = ?)"
                )
                params.extend(
                    [
                        str(volume["grade"]),
                        str(volume["semester"]),
                        str(volume["textbook_version"]),
                    ]
                )
            where.append(f"({' OR '.join(clauses)})")
    if cleaned_paper_ids:
        placeholders = ", ".join("?" for _ in cleaned_paper_ids)
        where.append(
            f"(q.paper_id IN ({placeholders}) OR occ.question_id IS NOT NULL)"
        )
        params.extend(cleaned_paper_ids)
    for tag_type, tag_values in (tag_filters or {}).items():
        cleaned = [item for value in tag_values if (item := _filter_text(value))]
        if not cleaned:
            continue
        placeholders = ", ".join("?" for _ in cleaned)
        if tag_type == "thought":
            where.append(
                "EXISTS (SELECT 1 FROM question_tags tf "
                "WHERE tf.question_id = q.id "
                "AND tf.tag_type IN ('thought', 'method') "
                f"AND tf.tag_value IN ({placeholders}))"
            )
            params.extend(cleaned)
        else:
            where.append(
                "EXISTS (SELECT 1 FROM question_tags tf "
                "WHERE tf.question_id = q.id AND tf.tag_type = ? "
                f"AND tf.tag_value IN ({placeholders}))"
            )
            params.extend([tag_type, *cleaned])
    if (clean := _filter_text(tag_status)) and clean != "全部":
        complete = f"""
            EXISTS (SELECT 1 FROM question_tags t WHERE t.question_id = q.id
                    AND t.tag_type = 'ability' AND COALESCE(t.tag_value, '') <> '')
            AND ({_OWNERSHIP_READY_SQL})
            AND CAST(q.difficulty AS REAL) BETWEEN 1 AND 10
        """
        if clean == "已打标签":
            where.append(complete)
        elif clean == "未打标签":
            where.append(f"NOT ({complete})")
    return joins, where, params


def _filter_text(value: object) -> str:
    return str(value or "").strip()


def _analysis_from_question(
    question: Mapping[str, Any],
) -> tuple[TagAnalysis, str | None]:
    grouped: dict[str, list[str]] = {}
    model_name: str | None = None
    confidences: list[float] = []
    for tag in question.get("tags", []):
        if not isinstance(tag, dict):
            continue
        tag_type = str(tag.get("tag_type") or "").strip()
        tag_value = str(tag.get("tag_value") or "").strip()
        if tag_type and tag_value:
            grouped.setdefault(tag_type, [])
            if tag_value not in grouped[tag_type]:
                grouped[tag_type].append(tag_value)
        if model_name is None:
            model_name = str(tag.get("model_name") or "").strip() or None
        try:
            confidences.append(float(tag.get("confidence")))
        except (TypeError, ValueError):
            pass
    confidence = min(max(confidences), 0.9) if confidences else 0.9
    return (
        TagAnalysis.from_dict(
            {
                "knowledge_points": grouped.get("knowledge_point", []),
                "method_tags": grouped.get("method", []),
                "thought_tags": grouped.get("thought", []),
                "ability_tags": grouped.get("ability", []),
                "math_model_tags": grouped.get("model", []),
                "special_type_tags": grouped.get("special_type", []),
                "difficulty": question.get("difficulty") or 1,
                "error_prone_points": grouped.get("error_type", []),
                "prerequisite_points": grouped.get("prerequisite", []),
                "textbook_chapters": grouped.get("exam_scope", []),
                "curriculum_sections": grouped.get("curriculum_section", []),
                "teaching_stage": _first_grouped_value(grouped, "teaching_stage"),
                "suitable_student_level": _first_grouped_value(
                    grouped,
                    "student_level",
                ),
                "reason": question.get("reason") or "",
                "confidence": confidence,
                "canonical_knowledge_id": _first_grouped_value(
                    grouped,
                    "canonical_knowledge_id",
                ),
                "sub_skills": grouped.get("sub_skill", []),
                "measured_skills": grouped.get("measured_skill_name", []),
                "supporting_skills": grouped.get("supporting_skill_name", []),
            }
        ),
        model_name,
    )


def _first_grouped_value(grouped: dict[str, list[str]], key: str) -> str:
    values = grouped.get(key, [])
    return str(values[0] if values else "").strip()
_QUESTION_SORT_CLAUSES = {
    "newest": "q.created_at DESC, q.id DESC",
    "paper_order": """
        CASE
            WHEN TRIM(COALESCE(q.question_number, '')) <> ''
             AND TRIM(COALESCE(q.question_number, '')) NOT GLOB '*[^0-9]*'
            THEN 0
            ELSE 1
        END ASC,
        CASE
            WHEN TRIM(COALESCE(q.question_number, '')) <> ''
             AND TRIM(COALESCE(q.question_number, '')) NOT GLOB '*[^0-9]*'
            THEN CAST(TRIM(q.question_number) AS INTEGER)
            ELSE NULL
        END ASC,
        q.id ASC
    """,
    "difficulty_desc": """
        CASE WHEN CAST(q.difficulty AS REAL) BETWEEN 1 AND 10 THEN 0 ELSE 1 END,
        CAST(q.difficulty AS REAL) DESC,
        q.id DESC
    """,
    "difficulty_asc": """
        CASE WHEN CAST(q.difficulty AS REAL) BETWEEN 1 AND 10 THEN 0 ELSE 1 END,
        CAST(q.difficulty AS REAL) ASC,
        q.id ASC
    """,
    "frequency_desc": """
        CASE WHEN qfc.question_id IS NULL THEN 1 ELSE 0 END,
        MAX(qfc.score_midterm, qfc.score_final, qfc.score_zhongkao) DESC,
        q.id DESC
    """,
    "frequency_asc": """
        CASE WHEN qfc.question_id IS NULL THEN 1 ELSE 0 END,
        MAX(qfc.score_midterm, qfc.score_final, qfc.score_zhongkao) ASC,
        q.id ASC
    """,
    # Legacy API aliases stay readable for older local clients. The current UI
    # exposes only the unified difficulty and frequency controls above.
    "difficulty": "CAST(q.difficulty AS REAL) DESC, q.id DESC",
    "frequency_midterm": "COALESCE(qfc.score_midterm, 0.0) DESC, q.created_at DESC, q.id DESC",
    "frequency_final": "COALESCE(qfc.score_final, 0.0) DESC, q.created_at DESC, q.id DESC",
    "frequency_zhongkao": "COALESCE(qfc.score_zhongkao, 0.0) DESC, q.created_at DESC, q.id DESC",
    "frequency_contextual": """
        CASE
            WHEN COALESCE(p.exam_type, '') LIKE '%期中%' THEN COALESCE(qfc.score_midterm, 0.0)
            WHEN COALESCE(p.exam_type, '') LIKE '%期末%' THEN COALESCE(qfc.score_final, 0.0)
            ELSE COALESCE(qfc.score_zhongkao, 0.0)
        END DESC,
        q.created_at DESC,
        q.id DESC
    """,
}
_FREQUENCY_SORTS = {
    "frequency_desc",
    "frequency_asc",
    "frequency_midterm",
    "frequency_final",
    "frequency_zhongkao",
    "frequency_contextual",
}
_RETIRED_PUBLIC_TAG_TYPES = {
    "canonical_knowledge_id",
    "measured_skill_name",
    "sub_skill",
    "supporting_skill_name",
    "teaching_stage",
}
_PUBLIC_TAG_TYPES = tuple(
    sorted(ALLOWED_TAG_TYPES - _RETIRED_PUBLIC_TAG_TYPES)
)
_RICH_CONTENT_VERSION = 3
_CURRENT_PREVIEW_ORDER_SQL = "updated_at DESC, id DESC"
_READ_RESULT_CACHE_LIMIT = 48
_BROWSE_CACHE_LIMITS = {"skill_snapshot": 2, "skill_inventory": 2, "skill_page": 4,
                        "skill_index": 4, "skill_questions": 8, "facets": 4, "papers": 2}
_READ_RESULT_CACHE_LOCK = threading.Lock()
_SKILL_SNAPSHOT_LOCK = threading.Lock()
_SKILL_LOCAL_CACHE_MAX_BYTES = 16 * 1024 * 1024
_READ_RESULT_CACHE: OrderedDict[tuple[object, ...], object] = OrderedDict()
_CACHE_MISS = object()
# An index is shared across different target questions; no database-side index
# or result writes. Keep at most two prepared source generations in memory.
_SIMILAR_INDEX_CACHE_LOCK = threading.Lock()
_SIMILAR_INDEX_CACHE: OrderedDict[tuple[object, ...], SimilarQuestionIndex] = OrderedDict()
# Parsed rich-content sidecars keyed by (root, question_id, mtime_ns, size);
# a stat per hit keeps entries automatically correct when a sidecar is
# rewritten, and avoids re-reading plus re-parsing JSON on every request.
_RICH_CONTENT_CACHE_LIMIT = 512
_RICH_CONTENT_CACHE_LOCK = threading.Lock()
_RICH_CONTENT_CACHE: OrderedDict[
    tuple[str, int, int, int],
    dict[str, Any],
] = OrderedDict()
_ACTIVE_QUESTION_PREDICATE_SQL = """
    q.id = ?
    AND COALESCE(q.is_deleted, 0) = 0
    AND COALESCE(p.import_status, '') <> 'deleted'
"""
_QUESTION_ASSET_SUBDIR = "question_bank/extracted_images"
_QUESTION_PREVIEW_SUBDIR = "question_bank/previews"
_QUESTION_IMAGE_SUFFIXES = (".bmp", ".jpeg", ".jpg", ".png", ".webp")
_SNAPSHOT_CHUNK_SIZE = 1024 * 1024
_SNAPSHOT_MAX_ATTEMPTS = 4
_SNAPSHOT_DEADLINE_SECONDS = 5.0
_SNAPSHOT_REQUIRED_TABLES = frozenset(
    {
        "papers",
        "questions",
        "question_tags",
        "question_frequency_cache",
        "question_previews",
    }
)
_CURRENT_KNOWLEDGE_NOT_LOADED = object()


@dataclass(slots=True)
class _ActiveQuestionReadScope:
    source: Path
    connection: sqlite3.Connection
    current_knowledge: object = _CURRENT_KNOWLEDGE_NOT_LOADED
    identities: dict[str, dict[int, str]] = field(default_factory=dict)
    duplicate_groups: dict[int, list[int]] = field(default_factory=dict)
    skill_snapshot: dict[str, Any] | None = None
    skill_inventory: dict[str, Any] | None = None
    generation: tuple[object, ...] | None = None


_ACTIVE_READ_SCOPE: ContextVar[_ActiveQuestionReadScope | None] = ContextVar(
    "question_bank_active_read_scope",
    default=None,
)


class QuestionMediaNotFound(LookupError):
    """Raised when a question does not own an addressable media resource."""


class QuestionBankSnapshotError(RuntimeError):
    """Base class for path-free read-snapshot failures."""


class QuestionBankSnapshotBusy(QuestionBankSnapshotError):
    """Raised when bounded capture cannot observe a stable source generation."""


class QuestionBankSnapshotUnavailable(QuestionBankSnapshotError):
    """Raised when a stable source cannot produce a usable read snapshot."""


class _SnapshotChanged(RuntimeError):
    """Internal retry signal for a source generation change."""


class _SnapshotSourceUnavailable(RuntimeError):
    """Internal signal for a stable source that cannot be captured."""


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    mode: int
    device: int | None
    inode: int | None
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class QuestionReadFilters:
    page: int = 1
    page_size: int = 20
    question_number: str | None = None
    keyword: str | None = None
    knowledge_point: str | None = None
    knowledge_points: tuple[str, ...] = ()
    abilities: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    thoughts: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    special_types: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()
    error_pattern_categories: tuple[str, ...] = ()
    student_levels: tuple[str, ...] = ()
    teaching_stages: tuple[str, ...] = ()
    sub_skills: tuple[str, ...] = ()
    difficulty_min: float | None = None
    difficulty_max: float | None = None
    question_types: tuple[str, ...] = ()
    paper_ids: tuple[int, ...] = ()
    years: tuple[str, ...] = ()
    exam_types: tuple[str, ...] = ()
    grades: tuple[str, ...] = ()
    curriculum_volume_ids: tuple[str, ...] = ()
    exam_scopes: tuple[str, ...] = ()
    curriculum_sections: tuple[str, ...] = ()
    tag_status: str = "all"
    analysis_status: str = "all"
    sort: str = "newest"
    criteria_needs_review: bool = False
    teaching_progress_chapter: str = ""
    collapse_duplicates: bool = False
    scope_mode: str = "any"
    skill_keys: tuple[str, ...] = ()
    skill_unlinked: bool = False
    include_skills: bool = False


@dataclass(frozen=True, slots=True)
class QuestionReadPage:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int


@contextmanager
def _read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    requested = Path(db_path).resolve(strict=False)
    active = _ACTIVE_READ_SCOPE.get()
    if active is not None and active.source == requested:
        yield active.connection
        return
    generation = _source_generation_token(requested)
    connection = _open_direct_read_connection(requested)
    token = _ACTIVE_READ_SCOPE.set(
        _ActiveQuestionReadScope(
            source=requested,
            connection=connection,
            generation=generation,
        )
    )
    try:
        yield connection
    except sqlite3.Error as exc:
        raise _direct_read_error(exc) from exc
    finally:
        _ACTIVE_READ_SCOPE.reset(token)
        connection.close()


# WAL readers never block the grading/import writers; the busy timeout only
# covers -shm handover windows.  Reads run inside one deferred transaction so
# every statement of a request observes the same committed generation.
_DIRECT_READ_BUSY_TIMEOUT_SECONDS = 5.0


def _open_direct_read_connection(source: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            f"{source.as_uri()}?mode=ro",
            uri=True,
            timeout=_DIRECT_READ_BUSY_TIMEOUT_SECONDS,
        )
    except sqlite3.Error as exc:
        raise _direct_read_error(exc) from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN DEFERRED")
    except sqlite3.Error as exc:
        connection.close()
        raise _direct_read_error(exc) from exc
    return connection


def _direct_read_error(exc: sqlite3.Error) -> QuestionBankSnapshotError:
    text = str(exc).casefold()
    if "locked" in text or "busy" in text:
        return QuestionBankSnapshotBusy(
            "Question bank is busy; retry shortly"
        )
    return QuestionBankSnapshotUnavailable(
        "Question bank is unavailable"
    )


@contextmanager
def captured_sqlite_read_connection(
    db_path: Path,
    *,
    required_tables: frozenset[str],
    check_same_thread: bool = True,
) -> Iterator[sqlite3.Connection]:
    with _captured_snapshot_candidate(db_path) as candidate:
        conn = _open_snapshot_connection(
            candidate,
            required_tables=required_tables,
            check_same_thread=check_same_thread,
        )
        try:
            yield conn
        except sqlite3.DatabaseError as exc:
            raise QuestionBankSnapshotUnavailable(
                "Question bank snapshot is unavailable"
            ) from exc
        finally:
            conn.close()


@contextmanager
def captured_sqlite_snapshot_path(
    db_path: Path,
    *,
    required_tables: frozenset[str],
) -> Iterator[Path]:
    with _captured_snapshot_candidate(db_path) as candidate:
        validation = _open_snapshot_connection(
            candidate,
            required_tables=required_tables,
        )
        validation.close()
        yield candidate


def _snapshot_compare_hook(_source: Path, _attempt: int) -> None:
    """Deterministic capture barrier; production intentionally does nothing."""


@contextmanager
def _captured_snapshot_candidate(db_path: Path) -> Iterator[Path]:
    source = Path(db_path).resolve(strict=False)
    deadline = time.monotonic() + _SNAPSHOT_DEADLINE_SECONDS

    for attempt in range(1, _SNAPSHOT_MAX_ATTEMPTS + 1):
        if attempt > 1 and time.monotonic() >= deadline:
            break
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="question-bank-snapshot-"
            )
        except OSError as exc:
            raise QuestionBankSnapshotUnavailable(
                "Question bank snapshot is unavailable"
            ) from exc

        try:
            candidate = _capture_snapshot_attempt(
                source,
                Path(temporary.name),
                attempt=attempt,
                deadline=deadline,
            )
        except _SnapshotChanged:
            temporary.cleanup()
            continue
        except _SnapshotSourceUnavailable as exc:
            temporary.cleanup()
            raise QuestionBankSnapshotUnavailable(
                "Question bank snapshot is unavailable"
            ) from exc

        try:
            yield candidate
        finally:
            temporary.cleanup()
        return

    raise QuestionBankSnapshotBusy(
        "Question bank snapshot is temporarily busy"
    )


def _capture_snapshot_attempt(
    source: Path,
    attempt_root: Path,
    *,
    attempt: int,
    deadline: float,
) -> Path:
    candidate = attempt_root / source.name
    source_wal = Path(f"{source}-wal")
    candidate_wal = Path(f"{candidate}-wal")
    rollback_journal = Path(f"{source}-journal")

    _reject_visible_rollback_journal(rollback_journal)
    main_first = _copy_required_source(source, candidate, deadline=deadline)
    _reject_visible_rollback_journal(rollback_journal)
    wal_first = _copy_optional_source(
        source_wal,
        candidate_wal,
        deadline=deadline,
    )
    _reject_visible_rollback_journal(rollback_journal)

    _snapshot_compare_hook(source, attempt)

    wal_second = _compare_optional_source(
        source_wal,
        candidate_wal,
        expected=wal_first,
        deadline=deadline,
    )
    _reject_visible_rollback_journal(rollback_journal)
    main_second = _compare_required_source(
        source,
        candidate,
        deadline=deadline,
    )
    _reject_visible_rollback_journal(rollback_journal)

    if main_first != main_second or wal_first != wal_second:
        raise _SnapshotChanged("Question bank source generation changed")
    return candidate


def _copy_required_source(
    source: Path,
    candidate: Path,
    *,
    deadline: float,
) -> _FileIdentity:
    try:
        with source.open("rb") as source_handle, candidate.open("xb") as target:
            before = _regular_file_identity(source_handle)
            _stream_copy(source_handle, target, deadline=deadline)
            after = _regular_file_identity(source_handle)
    except FileNotFoundError as exc:
        raise _SnapshotSourceUnavailable("Question bank source is unavailable") from exc
    except OSError as exc:
        raise _SnapshotSourceUnavailable("Question bank source is unavailable") from exc
    if before != after:
        raise _SnapshotChanged("Question bank source changed during copy")
    return before


def _copy_optional_source(
    source: Path,
    candidate: Path,
    *,
    deadline: float,
) -> _FileIdentity | None:
    try:
        source_handle = source.open("rb")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _SnapshotSourceUnavailable("Question bank WAL is unavailable") from exc

    try:
        with source_handle, candidate.open("xb") as target:
            before = _regular_file_identity(source_handle)
            _stream_copy(source_handle, target, deadline=deadline)
            after = _regular_file_identity(source_handle)
    except OSError as exc:
        raise _SnapshotSourceUnavailable("Question bank WAL is unavailable") from exc
    if before != after:
        raise _SnapshotChanged("Question bank WAL changed during copy")
    return before


def _compare_required_source(
    source: Path,
    candidate: Path,
    *,
    deadline: float,
) -> _FileIdentity:
    try:
        return _compare_source_to_candidate(source, candidate, deadline=deadline)
    except FileNotFoundError as exc:
        raise _SnapshotChanged("Question bank source disappeared") from exc
    except OSError as exc:
        raise _SnapshotChanged("Question bank source comparison failed") from exc


def _compare_optional_source(
    source: Path,
    candidate: Path,
    *,
    expected: _FileIdentity | None,
    deadline: float,
) -> _FileIdentity | None:
    if expected is not None:
        try:
            return _compare_source_to_candidate(source, candidate, deadline=deadline)
        except FileNotFoundError as exc:
            raise _SnapshotChanged("Question bank WAL disappeared") from exc
        except OSError as exc:
            raise _SnapshotChanged("Question bank WAL comparison failed") from exc

    try:
        source_handle = source.open("rb")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _SnapshotSourceUnavailable("Question bank WAL is unavailable") from exc
    with source_handle:
        before = _regular_file_identity(source_handle)
        after = _regular_file_identity(source_handle)
    if before != after:
        raise _SnapshotChanged("Question bank WAL changed while appearing")
    raise _SnapshotChanged("Question bank WAL appeared during capture")


def _compare_source_to_candidate(
    source: Path,
    candidate: Path,
    *,
    deadline: float,
) -> _FileIdentity:
    with source.open("rb") as source_handle, candidate.open("rb") as target:
        before = _regular_file_identity(source_handle)
        target_info = os.fstat(target.fileno())
        if not stat.S_ISREG(target_info.st_mode):
            raise _SnapshotSourceUnavailable(
                "Question bank snapshot candidate is unavailable"
            )
        while True:
            _check_snapshot_deadline(deadline)
            source_chunk = source_handle.read(_SNAPSHOT_CHUNK_SIZE)
            target_chunk = target.read(_SNAPSHOT_CHUNK_SIZE)
            if source_chunk != target_chunk:
                raise _SnapshotChanged("Question bank source bytes changed")
            if not source_chunk:
                break
        after = _regular_file_identity(source_handle)
    if before != after:
        raise _SnapshotChanged("Question bank source changed during comparison")
    return before


def _stream_copy(
    source: BinaryIO,
    target: BinaryIO,
    *,
    deadline: float,
) -> None:
    while True:
        _check_snapshot_deadline(deadline)
        chunk = source.read(_SNAPSHOT_CHUNK_SIZE)
        if not chunk:
            return
        target.write(chunk)


def _regular_file_identity(handle: BinaryIO) -> _FileIdentity:
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise _SnapshotSourceUnavailable("Question bank source is not a regular file")
    device = int(info.st_dev) if getattr(info, "st_dev", 0) else None
    inode = int(info.st_ino) if getattr(info, "st_ino", 0) else None
    return _FileIdentity(
        mode=int(info.st_mode),
        device=device,
        inode=inode,
        size=int(info.st_size),
        mtime_ns=int(info.st_mtime_ns),
        ctime_ns=int(info.st_ctime_ns),
    )


def _reject_visible_rollback_journal(journal: Path) -> None:
    try:
        handle = journal.open("rb")
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _SnapshotChanged("Question bank rollback journal is unstable") from exc
    with handle:
        before = _regular_file_identity(handle)
        after = _regular_file_identity(handle)
    if before != after:
        raise _SnapshotChanged("Question bank rollback journal changed")
    raise _SnapshotChanged("Question bank rollback journal is visible")


def _check_snapshot_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _SnapshotChanged("Question bank snapshot deadline elapsed")


def _open_snapshot_connection(
    candidate: Path,
    *,
    required_tables: frozenset[str] = _SNAPSHOT_REQUIRED_TABLES,
    check_same_thread: bool = True,
    validate_snapshot: bool = True,
) -> sqlite3.Connection:
    conn: sqlite3.Connection | None = None
    try:
        uri = f"{candidate.resolve(strict=True).as_uri()}?mode=ro"
        conn = instrument_sqlite_connection(
            sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                check_same_thread=check_same_thread,
            )
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN")
        if validate_snapshot:
            quick_check = conn.execute("PRAGMA quick_check").fetchall()
            if len(quick_check) != 1 or str(quick_check[0][0]).casefold() != "ok":
                raise sqlite3.DatabaseError("Snapshot quick_check failed")
            placeholders = ", ".join("?" for _ in required_tables)
            rows = conn.execute(
                f"""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name IN ({placeholders})
                """,
                tuple(sorted(required_tables)),
            ).fetchall()
            available_tables = {str(row[0]) for row in rows}
            if not required_tables.issubset(available_tables):
                raise sqlite3.DatabaseError("Snapshot schema is unavailable")
        return conn
    except (OSError, sqlite3.DatabaseError) as exc:
        if conn is not None:
            conn.close()
        raise QuestionBankSnapshotUnavailable(
            "Question bank snapshot is unavailable"
        ) from exc


def _source_generation_token(db_path: Path) -> tuple[object, ...] | None:
    source = Path(db_path).resolve(strict=False)
    journal = Path(f"{source}-journal")
    if journal.exists():
        return None
    token: list[object] = [str(source)]
    for candidate in (source, Path(f"{source}-wal")):
        try:
            info = candidate.stat()
        except FileNotFoundError:
            if candidate == source:
                return None
            token.append(None)
            continue
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        identity = (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns),
        )
        if candidate != source and int(info.st_size) == 0:
            # An empty WAL carries no frames; read-only connections delete it
            # on close, so "absent" and "zero length" must share one token or
            # every read would mint a fresh generation for identical data.
            identity = None
        token.append(identity)
    return tuple(token)


def _taxonomy_generation_token() -> tuple[object, ...]:
    state_path = get_taxonomy_governance().state_path.resolve(strict=False)
    token: list[object] = [str(state_path)]
    for candidate in (
        state_path,
        state_path.with_name(f"{state_path.name}.bak"),
    ):
        try:
            info = candidate.stat()
        except (FileNotFoundError, OSError):
            token.append(None)
            continue
        token.append(
            (
                int(info.st_size),
                int(info.st_mtime_ns),
                int(info.st_ctime_ns),
            )
        )
    return tuple(token)


def _read_result_cache_get(key: tuple[object, ...], *, shared: bool = False) -> object:
    with _READ_RESULT_CACHE_LOCK:
        value = _READ_RESULT_CACHE.get(key, _CACHE_MISS)
        if value is _CACHE_MISS:
            return _CACHE_MISS
        _READ_RESULT_CACHE.move_to_end(key)
    return value if shared else deepcopy(value)


def _read_cache_group(key: tuple[object, ...]) -> object:
    if key[0] == "questions" and key[-1].include_skills:
        return "skill_questions"
    return key[0]


def _read_result_cache_put(key: tuple[object, ...], value: object) -> None:
    with _READ_RESULT_CACHE_LOCK:
        # Skill snapshots are internal, read-only projections. Public results
        # still receive independent copies; copying the whole bank per filter
        # request would defeat the shared projection.
        _READ_RESULT_CACHE[key] = value if key[0] in {"skill_snapshot", "skill_inventory", "skill_page"} else deepcopy(value)
        _READ_RESULT_CACHE.move_to_end(key)
        if key[0] == "collapsed_ids":
            # Group keys contain a candidate set, not just a 20-row page.
            # Keep fewer of these so changing filters cannot retain dozens
            # of full-bank identity maps.
            group_keys = [candidate for candidate in _READ_RESULT_CACHE if candidate[0] == "collapsed_ids"]
            for stale in group_keys[:-8]:
                del _READ_RESULT_CACHE[stale]
        group = _read_cache_group(key)
        if group in _BROWSE_CACHE_LIMITS:
            group_keys = [candidate for candidate in _READ_RESULT_CACHE if _read_cache_group(candidate) == group]
            for stale in group_keys[:-_BROWSE_CACHE_LIMITS[group]]:
                del _READ_RESULT_CACHE[stale]
        while len(_READ_RESULT_CACHE) > _READ_RESULT_CACHE_LIMIT:
            # Browsing many pages/filters must not evict the expensive bank
            # projection and force thousands of source checks on refresh.
            stale = next(candidate for candidate in _READ_RESULT_CACHE if _read_cache_group(candidate) not in _BROWSE_CACHE_LIMITS)
            del _READ_RESULT_CACHE[stale]


def _rich_content_cache_get(
    key: tuple[str, int, int, int],
) -> dict[str, Any] | None:
    with _RICH_CONTENT_CACHE_LOCK:
        payload = _RICH_CONTENT_CACHE.get(key)
        if payload is None:
            return None
        _RICH_CONTENT_CACHE.move_to_end(key)
        return deepcopy(payload)


def _rich_content_cache_put(
    key: tuple[str, int, int, int],
    payload: dict[str, Any],
) -> None:
    with _RICH_CONTENT_CACHE_LOCK:
        _RICH_CONTENT_CACHE[key] = deepcopy(payload)
        _RICH_CONTENT_CACHE.move_to_end(key)
        while len(_RICH_CONTENT_CACHE) > _RICH_CONTENT_CACHE_LIMIT:
            _RICH_CONTENT_CACHE.popitem(last=False)


@lru_cache(maxsize=1)
def _skill_cache_calculation_revision() -> str:
    # On restart, changed source or catalog files invalidate the derived data.
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(b"question-bank-skill-read-v1")
    for source in sorted([*root.rglob("*.py"), *(root / "taxonomy" / "catalogs").glob("*.json")]):
        digest.update(str(source.relative_to(root)).encode("utf-8"))
        digest.update(source.read_bytes())
    digest.update((root.parent / "integration" / "data_generation.py").read_bytes())
    return digest.hexdigest()


def _skill_asset_manifest(root: Path) -> dict[str, tuple | None] | None:
    """Scan metadata, never bodies; reject links and unstable/unreadable trees."""
    from question_bank.services.file_cache import _file_read_identity
    assets = root / "question_bank"
    # Only these trees participate in rich input and fallback image searches.
    # Other bank directories (imports/exports) are not source dependencies.
    manifest = {}
    pending = [assets / name for name in ("rich_content", "extracted_images", "previews")]
    try:
        root_identity = _file_read_identity(assets)
        if root_identity == ("reparse",):
            return None
        # A new unrelated export changes directory timestamps, not inputs.
        # Keep its identity to detect root replacement; scan source trees fully.
        manifest[str(assets)] = root_identity[:3] if root_identity is not None else None
        while pending:
            path = pending.pop()
            info = _file_read_identity(path)
            if info == ("reparse",):
                return None
            manifest[str(path)] = info
            if info is not None and stat.S_ISDIR(info[0]):
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
    except OSError:
        return None
    return manifest


class QuestionBankReadService:
    def __init__(self, db_path: Path, *, data_root: Path | None = None,
                 persist_skill_snapshots: bool = True) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else None
        self.persist_skill_snapshots = persist_skill_snapshots
        self._cache_data_root = (
            str(self.data_root.resolve(strict=False))
            if self.data_root is not None
            else None
        )

    @property
    def current_knowledge(self) -> CurrentKnowledgeResolver | None:
        active = _ACTIVE_READ_SCOPE.get()
        if (
            active is None
            or active.source != self.db_path.resolve(strict=False)
        ):
            return None
        if active.current_knowledge is _CURRENT_KNOWLEDGE_NOT_LOADED:
            try:
                active.current_knowledge = CurrentKnowledgeResolver.from_connection(
                    active.connection
                )
            except CurrentKnowledgeUnavailable:
                active.current_knowledge = None
        if isinstance(active.current_knowledge, CurrentKnowledgeResolver):
            return active.current_knowledge
        return None

    def _skill_snapshot(self, *, volume_id: str | None = None,
                        question_ids: tuple[int, ...] | None = None) -> dict[str, Any]:
        # A page can request papers, counts and questions concurrently. Only
        # one caller prepares a missing projection; waiters reuse its result.
        with _SKILL_SNAPSHOT_LOCK:
            return self._load_skill_snapshot(volume_id=volume_id, question_ids=question_ids)

    def _load_skill_snapshot(self, *, volume_id: str | None = None,
                             question_ids: tuple[int, ...] | None = None) -> dict[str, Any]:
        from question_bank.services.question_skill_index import build_skill_snapshot, load_skill_inventory

        active = _ACTIVE_READ_SCOPE.get()
        if active is not None and active.skill_snapshot is not None:
            return active.skill_snapshot
        generation = active.generation if active is not None else _source_generation_token(self.db_path)
        key = ("skill_snapshot", generation, self._cache_data_root)
        cached = _read_result_cache_get(key, shared=True) if generation is not None and generation == _source_generation_token(self.db_path) else _CACHE_MISS
        if cached is not _CACHE_MISS:
            if active is not None:
                active.skill_snapshot = cached
            return cached
        with _read_connection(self.db_path) as conn:
            scope = _ACTIVE_READ_SCOPE.get()
            inventory = scope.skill_inventory if scope is not None else None
            inventory_key = ("skill_inventory", generation, self._cache_data_root)
            if inventory is None:
                inventory = _read_result_cache_get(inventory_key, shared=True) if generation is not None else _CACHE_MISS
                if inventory is _CACHE_MISS:
                    inventory = load_skill_inventory(conn)
                    if generation is not None and generation == _source_generation_token(self.db_path):
                        _read_result_cache_put(inventory_key, inventory)
                if scope is not None:
                    scope.skill_inventory = inventory
            selected = (set(inventory["volumes"].get(volume_id, ())) if volume_id is not None
                        else set(question_ids) if question_ids is not None else set(inventory["questions"]))
            selected.intersection_update(inventory["questions"])
            full = selected == set(inventory["questions"])
            ids = None if full else tuple(sorted(selected))
            if not full:
                key = ("skill_snapshot" if volume_id is not None else "skill_page",
                       generation, self._cache_data_root, ids)
                cached = _read_result_cache_get(key, shared=True) if generation is not None else _CACHE_MISS
                if cached is not _CACHE_MISS:
                    return cached
            # The full projection is already shared in memory. Persist only
            # that existing projection, with its exact source dependencies.
            manifest = (_skill_asset_manifest(Path(self._cache_data_root))
                        if full and self.persist_skill_snapshots and self._cache_data_root and generation is not None else None)
            snapshot = self._read_local_skill_snapshot(generation, inventory, manifest, connection=conn) if manifest is not None else None
            if snapshot is None:
                from question_bank.services.file_cache import capture_file_reads
                with capture_file_reads() if manifest is not None else nullcontext(None) as trace:
                    snapshot = build_skill_snapshot(conn, self.db_path, self.data_root,
                                                    inventory=inventory, question_ids=ids)
                if manifest is not None and trace.usable:
                    self._save_local_skill_snapshot(generation, manifest, trace.paths, snapshot, connection=conn)
            if scope is not None and full:
                scope.skill_snapshot = snapshot
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, snapshot)
        return snapshot

    def _local_skill_path(self) -> Path:
        return Path(self._cache_data_root) / "cache" / "question_bank_skills.cache"

    def _skill_dependencies_current(self, dependencies: dict) -> bool:
        from question_bank.services.file_cache import _file_read_identity
        assets = Path(self._cache_data_root) / "question_bank"
        source_trees = tuple(assets / name for name in ("rich_content", "extracted_images", "previews"))
        for name, expected in dependencies.items():
            path = Path(name)
            # Existing material outside the bank, or any reparse point, uses
            # the full reader. Missing remapping candidates remain tracked.
            if expected == ("reparse",) or (expected is not None and not any(path.is_relative_to(tree) for tree in source_trees)):
                return False
            if _file_read_identity(path) != expected:
                return False
        return True

    def _read_local_skill_snapshot(self, generation, inventory, manifest, *, connection):
        try:
            path = self._local_skill_path()
            with path.open("rb") as saved:
                encoded = saved.read(_SKILL_LOCAL_CACHE_MAX_BYTES + 1)
            if len(encoded) > _SKILL_LOCAL_CACHE_MAX_BYTES:
                return None
            entry = pickle.loads(encoded)
            if (entry["calculation"] != _skill_cache_calculation_revision()
                    or entry["manifest"] != manifest or not self._skill_dependencies_current(entry["dependencies"])
                    or hashlib.sha256(entry["payload"]).hexdigest() != entry["digest"]):
                return None
            if entry["generation"] != generation:
                from integration.data_generation import database_content_revision
                if entry["database_revision"] != database_content_revision(self.db_path, connection):
                    return None
            snapshot = pickle.loads(entry["payload"])
            if (not isinstance(snapshot, dict)
                    or any(not isinstance(snapshot.get(key), dict) for key in (
                        "nodes", "questions", "members", "volumes", "by_skill", "by_question",
                        "point_counts", "topics", "topic_keys", "sections"))
                    or any(not isinstance(snapshot.get(key), set) for key in ("no_usable", "unlinked"))):
                return None
            if any(snapshot[key] != inventory[key] for key in ("release", "nodes", "questions", "members", "volumes")):
                return None
            if generation != _source_generation_token(self.db_path) or manifest != _skill_asset_manifest(Path(self._cache_data_root)):
                return None
            return snapshot
        except (OSError, sqlite3.Error, pickle.PickleError, EOFError, KeyError, TypeError, ValueError, AttributeError, ImportError):
            return None

    def _save_local_skill_snapshot(self, generation, manifest, dependencies, snapshot, *, connection) -> None:
        temporary = None
        try:
            if (generation != _source_generation_token(self.db_path)
                    or manifest != _skill_asset_manifest(Path(self._cache_data_root))
                    or not self._skill_dependencies_current(dependencies)):
                return
            payload = pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
            from integration.data_generation import database_content_revision
            encoded = pickle.dumps({"generation": generation, "calculation": _skill_cache_calculation_revision(),
                "database_revision": database_content_revision(self.db_path, connection),
                "manifest": manifest, "dependencies": dependencies, "payload": payload,
                "digest": hashlib.sha256(payload).hexdigest()}, protocol=pickle.HIGHEST_PROTOCOL)
            if len(encoded) > _SKILL_LOCAL_CACHE_MAX_BYTES:
                return
            if generation != _source_generation_token(self.db_path):
                return
            path = self._local_skill_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix="skill-read-", suffix=".tmp", delete=False) as saved:
                temporary = Path(saved.name)
                saved.write(encoded)
            from question_bank.atomic_files import replace_with_retry
            replace_with_retry(temporary, path)
        except (OSError, sqlite3.Error, pickle.PickleError, TypeError, ValueError, AttributeError, ImportError):
            pass  # A derived cache cannot fail question browsing or a save.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def skill_index(self, curriculum_volume_id: str) -> dict[str, Any]:
        from question_bank.services.question_skill_index import skill_index

        generation = None if _ACTIVE_READ_SCOPE.get() is not None else _source_generation_token(self.db_path)
        key = ("skill_index", generation, self._cache_data_root, curriculum_volume_id)
        cached = _read_result_cache_get(key) if generation is not None else _CACHE_MISS
        if cached is not _CACHE_MISS:
            return cached
        with _read_connection(self.db_path) as conn:
            snapshot = self._skill_snapshot(volume_id=curriculum_volume_id)
            review_ids = _load_criteria_needs_review_ids(conn, list(snapshot["questions"]))
        result = skill_index(snapshot, curriculum_volume_id, review_ids)
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, result)
        return result

    def standard_summary(self) -> dict[str, Any]:
        """Maintenance metadata, using the same current evidence and links as training."""
        from question_bank.solution_evidence.knowledge_links import load_point_links
        from question_bank.solution_evidence.part_assessments import load_profiles

        with _read_connection(self.db_path) as conn:
            versions = [dict(row) for row in conn.execute(
                "SELECT release_id, taxonomy_revision, status, activated_at, created_at "
                "FROM knowledge_graph_releases ORDER BY created_at DESC, release_id DESC"
            )]
            active = next((row for row in versions if row["status"] == "active"), None)
            ids = [int(row[0]) for row in conn.execute("SELECT id FROM questions WHERE is_deleted=0")]
            profiles = load_profiles(self.db_path, ids, connection=conn, data_root=self.data_root)
            usable = {qid: profile for qid, profile in profiles.items() if profile.get("available")}
            links = load_point_links(self.db_path,
                [profile["evidence_version_id"] for profile in usable.values()],
                active["release_id"] if active else None, connection=conn)
            skill_ids, coarse_ids, missing_ids, older_ids = set(), set(), set(), set()
            for qid, profile in usable.items():
                point_links = links.get(profile["evidence_version_id"], {})
                for part in profile["evidence"].get("parts", []):
                    for point in part.get("evidence_points", []):
                        rows = [row for row in point_links.get(point["evidence_point_id"], ()) if row.role == "direct"]
                        resolved = [row for row in rows if row.resolution_status == "resolved" and row.stable_key]
                        if not resolved:
                            missing_ids.add(qid)
                        elif any(row.stable_key.startswith("sk_") for row in resolved):
                            skill_ids.add(qid)
                        else:
                            coarse_ids.add(qid)
                        if active and any(row.graph_release_id != active["release_id"] for row in rows):
                            older_ids.add(qid)
            return {
                "active_release_id": active["release_id"] if active else None,
                "taxonomy_revision": active["taxonomy_revision"] if active else None,
                "versions": versions, "question_count": len(ids),
                "usable_question_count": len(usable), "skill_question_count": len(skill_ids),
                "section_only_question_count": len(coarse_ids),
                "missing_link_question_count": len(missing_ids),
                "older_link_question_count": len(older_ids),
                "model_calls": 0,
            }

    def list_papers(self, *, deleted: bool = False) -> list[dict[str, Any]]:
        generation = (
            None
            if _ACTIVE_READ_SCOPE.get() is not None
            else _source_generation_token(self.db_path)
        )
        key = ("papers", generation, bool(deleted))
        if generation is not None:
            cached = _read_result_cache_get(key)
            if cached is not _CACHE_MISS:
                return cached  # type: ignore[return-value]
        with _read_connection(self.db_path):
            items = self._list_papers(deleted=deleted)
            snapshot = self._skill_snapshot() if not deleted else None
        for item in items:
            item["skill_unlinked_question_count"] = (
                len(snapshot["members"].get(item["id"], set()) & snapshot["unlinked"]) if snapshot else 0
            )
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, items)
        return items

    def _list_papers(self, *, deleted: bool = False) -> list[dict[str, Any]]:
        paper_tag_types = ANALYSIS_TAG_TYPES + (
            "curriculum_section",
            "tag_status",
        )
        tag_placeholders = ", ".join("?" for _ in paper_tag_types)
        visible_question_sql = (
            """
            q.id IS NOT NULL
            AND q.paper_delete_operation_id = p.delete_operation_id
            """
            if deleted
            else """
            q.id IS NOT NULL
            AND COALESCE(q.is_deleted, 0) = 0
            """
        )
        paper_state_sql = (
            "COALESCE(p.import_status, '') = 'deleted'"
            if deleted
            else "COALESCE(p.import_status, '') <> 'deleted'"
        )
        order_sql = (
            "p.updated_at DESC, p.id DESC"
            if deleted
            else "p.created_at DESC, p.id DESC"
        )
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                WITH tag_summary AS (
                    SELECT
                        question_id,
                        1 AS has_analysis_tag,
                        MAX(CASE
                            WHEN tag_type = 'ability' THEN 1 ELSE 0
                        END) AS has_ability,
                        MAX(CASE
                            WHEN tag_type = 'knowledge_point' THEN 1 ELSE 0
                        END) AS has_knowledge,
                        MAX(CASE
                            WHEN tag_type IN (
                                'exam_scope',
                                'curriculum_section'
                            )
                            THEN 1 ELSE 0
                        END) AS has_scope,
                        MAX(CASE
                            WHEN tag_type = 'tag_status'
                             AND tag_value = 'derived_pending'
                            THEN 1 ELSE 0
                        END) AS derived_pending
                    FROM question_tags
                    WHERE tag_type IN ({tag_placeholders})
                      AND COALESCE(tag_value, '') <> ''
                    GROUP BY question_id
                )
                SELECT
                    p.id,
                    p.title,
                    p.source_file AS _source_file,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.folder_name,
                    p.textbook_version,
                    p.import_status,
                    p.created_at,
                    p.updated_at,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                        THEN 1
                    END) AS question_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND ts.has_analysis_tag = 1
                        THEN 1
                    END) AS tagged_any_question_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND (
                            (
                                ts.has_ability = 1
                                AND ts.has_knowledge = 1
                                AND ts.has_scope = 1
                            )
                            OR ts.derived_pending = 1
                         )
                         AND CAST(q.difficulty AS REAL) BETWEEN 1 AND 10
                        THEN 1
                    END) AS tagged_question_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND EXISTS (
                            SELECT 1
                            FROM question_solution_evidence_versions evidence
                            WHERE evidence.question_id = q.id
                              AND evidence.status IN ('proposed', 'approved')
                         )
                        THEN 1
                    END) AS evidence_question_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND EXISTS (
                            SELECT 1
                            FROM training_criterion_heads head
                            JOIN training_criterion_versions version
                              ON version.version_id = head.current_version_id
                            WHERE head.question_id = q.id
                              AND version.status IN ('proposed', 'approved')
                         )
                        THEN 1
                    END) AS criteria_question_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND {_CRITERIA_NEEDS_REVIEW_SQL.format(qid="q.id")}
                        THEN 1
                    END) AS criteria_needs_review_count,
                    COUNT(CASE
                        WHEN {visible_question_sql}
                         AND (
                            (
                                ts.has_ability = 1
                                AND ts.has_knowledge = 1
                                AND ts.has_scope = 1
                            )
                            OR ts.derived_pending = 1
                         )
                         AND CAST(q.difficulty AS REAL) BETWEEN 1 AND 10
                         AND EXISTS (
                            SELECT 1
                            FROM question_solution_evidence_versions evidence
                            WHERE evidence.question_id = q.id
                              AND evidence.status IN ('proposed', 'approved')
                         )
                         AND EXISTS (
                            SELECT 1
                            FROM training_criterion_heads head
                            JOIN training_criterion_versions version
                              ON version.version_id = head.current_version_id
                            WHERE head.question_id = q.id
                              AND version.status IN ('proposed', 'approved')
                         )
                        THEN 1
                    END) AS complete_analysis_count
                FROM papers p
                LEFT JOIN (
                    SELECT id, is_deleted, difficulty, paper_delete_operation_id,
                           paper_id AS _member_paper_id
                    FROM questions
                    UNION ALL
                    SELECT questions.id, questions.is_deleted, questions.difficulty,
                           questions.paper_delete_operation_id, occ.paper_id
                    FROM paper_question_occurrences occ
                    JOIN questions ON questions.id = occ.question_id
                ) q ON q._member_paper_id = p.id
                LEFT JOIN tag_summary ts ON ts.question_id = q.id
                WHERE {paper_state_sql}
                GROUP BY p.id
                ORDER BY {order_sql}
                """,
                paper_tag_types,
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["source_type"] = _safe_source_type(item.pop("_source_file", None))
            volume = curriculum_volume(
                grade=item.get("grade"),
                semester=item.get("semester"),
                textbook_version=item.get("textbook_version"),
            )
            item["curriculum_volume_id"] = (
                None if volume is None else str(volume["id"])
            )
            items.append(item)
        return items

    def list_questions(self, filters: QuestionReadFilters) -> QuestionReadPage:
        generation = (
            None
            if _ACTIVE_READ_SCOPE.get() is not None
            else _source_generation_token(self.db_path)
        )
        key = ("questions", generation, self._cache_data_root, filters)
        if generation is not None:
            cached = _read_result_cache_get(key)
            if cached is not _CACHE_MISS:
                return cached  # type: ignore[return-value]
        with _read_connection(self.db_path):
            result = self._list_questions(filters)
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, result)
        return result

    def session_analysis_status(
        self,
        grading_session_id: int,
    ) -> dict[str, Any]:
        """Return the current saved projections for a grading session's linked questions."""

        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT
                    q.id,
                    q.question_number,
                    CAST(q.difficulty AS REAL) BETWEEN 1 AND 10 AS difficulty_ready,
                    (
                        EXISTS (SELECT 1 FROM question_tags qt
                                WHERE qt.question_id = q.id
                                  AND qt.tag_type = 'ability'
                                  AND COALESCE(qt.tag_value, '') <> '')
                        AND ({_OWNERSHIP_READY_SQL})
                    ) AS tags_ready,
                    EXISTS (
                        SELECT 1
                        FROM question_solution_evidence_versions evidence
                        WHERE evidence.question_id = q.id
                          AND evidence.status IN ('proposed', 'approved')
                    ) AS evidence_ready,
                    EXISTS (
                        SELECT 1
                        FROM training_criterion_heads head
                        JOIN training_criterion_versions version
                          ON version.version_id = head.current_version_id
                        WHERE head.question_id = q.id
                          AND version.status IN ('proposed', 'approved')
                    ) AS criteria_ready
                FROM grading_question_links link
                JOIN questions q ON q.id = link.bank_question_id
                WHERE link.grading_session_id = ?
                  AND link.status = 'confirmed'
                  AND COALESCE(q.is_deleted, 0) = 0
                ORDER BY q.id
                """,
                [str(int(grading_session_id))],
            ).fetchall()
        items = [dict(row) for row in rows]
        snapshot = self._skill_snapshot(question_ids=tuple(int(item["id"]) for item in items)) if items else None
        incomplete = [
            item
            for item in items
            if not (
                bool(item["difficulty_ready"])
                and bool(item["tags_ready"])
                and bool(item["evidence_ready"])
                and bool(item["criteria_ready"])
            )
        ]
        return {
            "question_count": len(items),
            "unlinked_skill_count": len(snapshot["unlinked"]) if snapshot else 0,
            "tagged_count": sum(
                bool(item["difficulty_ready"]) and bool(item["tags_ready"])
                for item in items
            ),
            "evidence_count": sum(bool(item["evidence_ready"]) for item in items),
            "criteria_count": sum(bool(item["criteria_ready"]) for item in items),
            "complete_count": len(items) - len(incomplete),
            "incomplete_question_ids": [int(item["id"]) for item in incomplete],
            "incomplete_source_refs": [
                str(item["question_number"] or f"Q{item['id']}")
                for item in incomplete
            ],
        }

    def _read_filter_parts(self, filters: QuestionReadFilters, *, taxonomy_expansions: dict[str, tuple[str, ...]] | None = None):
        joins, where, params = _question_filter_parts(filters, current_knowledge=self.current_knowledge, taxonomy_expansions=taxonomy_expansions)
        if filters.skill_keys or filters.skill_unlinked:
            snapshot = self._skill_snapshot()
            ids = set(snapshot["questions"])
            if filters.skill_keys:
                ids &= set().union(*(snapshot["by_skill"].get(key, set()) for key in filters.skill_keys))
            if filters.skill_unlinked:
                ids &= snapshot["unlinked"]
            where.append("q.id IN (" + ",".join("?" for _ in ids) + ")" if ids else "1=0")
            params.extend(sorted(ids))
        if filters.collapse_duplicates:
            from question_bank.services.duplicate_analysis_copy_service import (
                canonical_question_ranks,
                exact_identity_map,
            )
            with _read_connection(self.db_path) as conn:
                ids = [int(row[0]) for row in conn.execute(" ".join([
                    "SELECT DISTINCT q.id FROM questions q", *joins,
                    "WHERE " + " AND ".join(where), "ORDER BY q.id"]), params).fetchall()]
                scope = _ACTIVE_READ_SCOPE.get()
                known = scope.identities.setdefault(self._cache_data_root or str(self.db_path.parent.parent), {}) if scope is not None else {}
                missing = [qid for qid in ids if qid not in known]
                known.update(exact_identity_map(conn, data_root=self.data_root or self.db_path.parent.parent,
                                               question_ids=missing, persist=False))
                identities = {qid: known[qid] for qid in ids if qid in known}
                # Page/sort changes share the same candidate set. Identity
                # revisions still validate file edits; the DB token also
                # invalidates representative choices after manual relabelling.
                generation = scope.generation if scope is not None else _source_generation_token(self.db_path)
                collapse_key = ("collapsed_ids", generation, self._cache_data_root, tuple(ids), tuple(identities.items()))
                cached = _read_result_cache_get(collapse_key) if generation is not None else _CACHE_MISS
                if cached is not _CACHE_MISS:
                    hidden, groups = cached
                    if scope is not None:
                        scope.duplicate_groups = groups
                    if hidden:
                        where.append("q.id NOT IN (" + ",".join("?" for _ in hidden) + ")")
                        params.extend(hidden)
                    return joins, where, params
                ranks = canonical_question_ranks(conn, ids)
            representatives: dict[str, int] = {}
            groups: dict[int, list[int]] = {}
            hidden = []
            for qid in sorted(ids, key=lambda qid: ranks[qid]):
                key = identities.get(qid)
                if key and key in representatives:
                    hidden.append(qid)
                    groups.setdefault(representatives[key], []).append(qid)
                elif key:
                    representatives[key] = qid
            if scope is not None:
                scope.duplicate_groups = groups
            if generation is not None and generation == _source_generation_token(self.db_path):
                _read_result_cache_put(collapse_key, (hidden, groups))
            if hidden:
                where.append("q.id NOT IN (" + ",".join("?" for _ in hidden) + ")")
                params.extend(hidden)
        return joins, where, params

    def _list_questions(self, filters: QuestionReadFilters) -> QuestionReadPage:
        joins, where, params = self._read_filter_parts(filters)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        count_sql = " ".join(
            [
                "SELECT COUNT(DISTINCT q.id) FROM questions q",
                *joins,
                where_sql,
            ]
        )

        list_joins = list(joins)
        if filters.sort in _FREQUENCY_SORTS:
            list_joins.append(
                "LEFT JOIN question_frequency_cache qfc ON qfc.question_id = q.id"
            )
        # A reused question displays the importing paper's own number.
        number_select = (
            "COALESCE(occ.question_number, q.question_number) AS question_number"
            if filters.paper_ids
            else "q.question_number"
        )
        order_clause = _QUESTION_SORT_CLAUSES[filters.sort]
        if filters.paper_ids and filters.sort == "paper_order":
            order_clause = order_clause.replace(
                "q.question_number",
                "COALESCE(occ.question_number, q.question_number)",
            )
        list_sql = " ".join(
            [
                f"""
                SELECT DISTINCT
                    q.id,
                    q.paper_id,
                    q.question_number,
                    q.question_type,
                    q.question_text,
                    q.answer_text,
                    q.image_paths AS _image_paths,
                    q.difficulty,
                    q.typicality,
                    q.reason,
                    q.needs_review,
                    q.has_images,
                    q.needs_image_review,
                    q.created_at,
                    q.updated_at,
                    p.title AS paper_title,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.textbook_version
                FROM questions q
                """,
                "LEFT JOIN papers p ON p.id = q.paper_id",
                "WHERE q.id IN ({page_ids})",
            ]
        )
        offset = (filters.page - 1) * filters.page_size

        with _read_connection(self.db_path) as conn:
            count_row = conn.execute(count_sql, params).fetchone()
            total = int(count_row[0] or 0)
            # Sort/filter narrow identity rows first; only load the page's
            # long stems/answers afterwards. Preserve occurrence numbering
            # and DISTINCT semantics, including a question in multiple papers.
            page_rows = conn.execute(
                " ".join([f"SELECT DISTINCT q.id, {number_select} FROM questions q",
                          *list_joins, where_sql, f"ORDER BY {order_clause}", "LIMIT ? OFFSET ?"]),
                [*params, filters.page_size, offset],
            ).fetchall()
            page_ids = list(dict.fromkeys(int(row["id"]) for row in page_rows))
            full_rows = conn.execute(
                list_sql.format(page_ids=",".join("?" for _ in page_ids)), page_ids,
            ).fetchall() if page_ids else []
            by_id = {int(row["id"]): dict(row) for row in full_rows}
            rows = [dict(by_id[int(row["id"])], question_number=row["question_number"]) for row in page_rows]
            tags_by_question = _load_page_tags(
                conn,
                [int(row["id"]) for row in rows],
                current_knowledge=self.current_knowledge,
            )
            revisions = question_revisions(conn, [int(row["id"]) for row in rows])
            review_ids = _load_criteria_needs_review_ids(
                conn,
                [int(row["id"]) for row in rows],
            )
            scope = _ACTIVE_READ_SCOPE.get()
            groups = scope.duplicate_groups if scope and filters.collapse_duplicates else {}
            member_ids = list({qid for parent in page_ids for qid in groups.get(parent, [])})
            members = {int(row['id']): dict(row) for row in conn.execute(
                "SELECT q.id,q.paper_id,q.question_number,p.title AS paper_title FROM questions q "
                "LEFT JOIN papers p ON p.id=q.paper_id WHERE q.id IN (" + ','.join('?' for _ in member_ids) + ')', member_ids)} if member_ids else {}


        items = [
            self._public_question_with_rich_content(
                row,
                tags_by_question.get(int(row["id"]), []),
                revision=revisions[int(row["id"])],
            )
            for row in rows
        ]
        for item in items:
            item["criteria_needs_review"] = int(item["id"]) in review_ids
        if filters.include_skills:
            from question_bank.services.question_skill_index import short_node_name
            snapshot = self._skill_snapshot(question_ids=tuple(int(item["id"]) for item in items))
            for item in items:
                skills = snapshot["by_question"].get(int(item["id"]), {})
                item["evidence_point_count"] = snapshot["point_counts"].get(int(item["id"]), 0)
                item["duplicate_members"] = [members[qid] for qid in groups.get(int(item["id"]), []) if qid in members]
                item["skills"] = [{"stable_key": key, "display_name": short_node_name(
                    snapshot["nodes"].get(key, {}).get("display_name", key))} for key in skills]
                item["skill_hits"] = deepcopy(list({hit["point_id"]: hit for key in filters.skill_keys
                    for hit in skills.get(key, [])}.values()))
        return QuestionReadPage(
            items=items,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            total_pages=max(math.ceil(total / filters.page_size), 1),
        )

    def list_question_refs(self, filters: QuestionReadFilters) -> QuestionReadPage:
        """Slim question identity rows for UI refresh cascades.

        Returns only id/paper_id/question_number so poll-driven refreshes do
        not drag rich content, tags or asset files across the wire.
        """
        # Governed tag expansion needs the same active read context as the
        # full question query, even when the caller only requests identities.
        generation = _source_generation_token(self.db_path) if not filters.collapse_duplicates and _ACTIVE_READ_SCOPE.get() is None else None
        key = ("question_refs", generation, self._cache_data_root, _taxonomy_generation_token(), filters)
        if generation is not None:
            cached = _read_result_cache_get(key)
            if cached is not _CACHE_MISS:
                return cached  # type: ignore[return-value]
        with _read_connection(self.db_path):
            result = self._list_question_refs(filters)
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, result)
        return result

    def _list_question_refs(self, filters: QuestionReadFilters) -> QuestionReadPage:
        joins, where, params = self._read_filter_parts(filters)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        count_sql = " ".join(
            [
                "SELECT COUNT(DISTINCT q.id) FROM questions q",
                *joins,
                where_sql,
            ]
        )
        number_select = (
            "COALESCE(occ.question_number, q.question_number) AS question_number"
            if filters.paper_ids
            else "q.question_number"
        )
        paper_select = (
            "COALESCE(occ.paper_id, q.paper_id) AS paper_id"
            if filters.paper_ids
            else "q.paper_id"
        )
        order_clause = _QUESTION_SORT_CLAUSES["paper_order"]
        if filters.paper_ids:
            order_clause = order_clause.replace(
                "q.question_number",
                "COALESCE(occ.question_number, q.question_number)",
            )
        list_sql = " ".join(
            [
                f"""
                SELECT DISTINCT
                    q.id,
                    {paper_select},
                    {number_select}
                FROM questions q
                """,
                *joins,
                where_sql,
                f"ORDER BY {order_clause}",
                "LIMIT ? OFFSET ?",
            ]
        )
        offset = (filters.page - 1) * filters.page_size
        with _read_connection(self.db_path) as conn:
            total = int(conn.execute(count_sql, params).fetchone()[0] or 0)
            rows = conn.execute(
                list_sql,
                [*params, filters.page_size, offset],
            ).fetchall()
        items = [
            {
                "id": int(row["id"]),
                "paper_id": int(row["paper_id"]),
                "question_number": str(row["question_number"] or ""),
            }
            for row in rows
        ]
        return QuestionReadPage(
            items=items,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            total_pages=max(math.ceil(total / filters.page_size), 1),
        )

    def list_facets(
        self,
        filters: QuestionReadFilters,
    ) -> dict[str, list[dict[str, Any]]]:
        generation = (
            None
            if _ACTIVE_READ_SCOPE.get() is not None
            else _source_generation_token(self.db_path)
        )
        key = ("facets", generation, _taxonomy_generation_token(), filters)
        if generation is not None:
            cached = _read_result_cache_get(key)
            if cached is not _CACHE_MISS:
                return cached  # type: ignore[return-value]
        with _read_connection(self.db_path):
            result = self._list_facets(filters)
        if generation is not None and generation == _source_generation_token(self.db_path):
            _read_result_cache_put(key, result)
        return result

    def tag_value_counts(
        self,
        tag_type: str,
        tag_values: Iterable[str],
    ) -> dict[str, int]:
        normalized_type = str(tag_type or "").strip()
        normalized_values = list(
            dict.fromkeys(
                str(value or "").strip()
                for value in tag_values
                if str(value or "").strip()
            )
        )
        if not normalized_values:
            return {}
        placeholders = ", ".join("?" for _ in normalized_values)
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.tag_value, COUNT(DISTINCT t.question_id) AS question_count
                FROM question_tags t
                JOIN questions q ON q.id = t.question_id
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE t.tag_type = ?
                  AND t.tag_value IN ({placeholders})
                  AND COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                GROUP BY t.tag_value
                """,
                [normalized_type, *normalized_values],
            ).fetchall()
        counts = {
            str(row["tag_value"]): int(row["question_count"] or 0)
            for row in rows
        }
        return {value: counts.get(value, 0) for value in normalized_values}

    def _list_facets(
        self,
        filters: QuestionReadFilters,
    ) -> dict[str, list[dict[str, Any]]]:
        taxonomy_expansions = _taxonomy_filter_expansions(
            filters,
            current_knowledge=self.current_knowledge,
        )
        source_cache: dict[QuestionReadFilters, tuple[str, list[Any]]] = {}

        def facet_source(
            **excluded_dimension: object,
        ) -> tuple[str, list[Any]]:
            facet_filters = replace(filters, **excluded_dimension)
            if facet_filters in source_cache:
                return source_cache[facet_filters]
            joins, where, params = self._read_filter_parts(facet_filters, taxonomy_expansions=taxonomy_expansions)
            where_sql = "WHERE " + " AND ".join(where) if where else ""
            result = (
                " ".join(
                    [
                        """
                        SELECT DISTINCT
                            q.id,
                            q.question_type,
                            p.year,
                            p.exam_type,
                            p.grade
                        FROM questions q
                        """,
                        *joins,
                        where_sql,
                    ]
                ),
                params,
            )
            source_cache[facet_filters] = result
            return result

        sources = {
            "exam_scopes": facet_source(exam_scopes=()),
            "curriculum_sections": facet_source(
                curriculum_sections=(),
            ),
            "knowledge_points": facet_source(
                knowledge_point=None,
                knowledge_points=(),
            ),
            "curriculum_chapters": facet_source(exam_scopes=()),
            "abilities": facet_source(abilities=()),
            "methods": facet_source(methods=()),
            "thoughts": facet_source(thoughts=()),
            "models": facet_source(models=()),
            "special_types": facet_source(special_types=()),
            "error_types": facet_source(error_types=()),
            "error_pattern_categories": facet_source(error_pattern_categories=()),
            "student_levels": facet_source(student_levels=()),
            "teaching_stages": facet_source(teaching_stages=()),
            "sub_skills": facet_source(sub_skills=()),
            "question_types": facet_source(question_types=()),
            "years": facet_source(years=()),
            "exam_types": facet_source(exam_types=()),
            "grades": facet_source(grades=()),
        }

        tag_specs = {
            "exam_scopes": ("exam_scope", None),
            "curriculum_sections": ("curriculum_section", None),
            "knowledge_points": ("knowledge_point", "knowledge"),
            "abilities": ("ability", "ability"),
            "methods": ("method", "method"),
            "thoughts": ("thought", "thought"),
            "models": ("model", "model"),
            "special_types": ("special_type", "special_type"),
            "error_types": ("error_type", None),
            "student_levels": ("student_level", None),
            "teaching_stages": ("teaching_stage", None),
            "sub_skills": ("sub_skill", None),
        }
        column_specs = {
            "question_types": "question_type", "years": "year",
            "exam_types": "exam_type", "grades": "grade",
        }
        # Each facet excludes only its own selection. Most facets share the
        # same source, so read its tags/columns once within this snapshot.
        grouped: dict[tuple[str, tuple[Any, ...]], list[str]] = {}
        for name, (sql, params) in sources.items():
            grouped.setdefault((sql, tuple(params)), []).append(name)
        current_knowledge = self.current_knowledge
        taxonomy_snapshot, taxonomy_identity_lookup = (
            get_taxonomy_governance().snapshot_and_identity_lookup(
                taxonomy_revision=(
                    current_knowledge.taxonomy_revision
                    if current_knowledge is not None else None
                ),
            )
        )
        result: dict[str, list[dict[str, Any]]] = {}
        with _read_connection(self.db_path) as conn:
            for (sql, parameters), names in grouped.items():
                params = list(parameters)
                tag_types = {
                    tag_specs[name][0] for name in names if name in tag_specs
                }
                if "thoughts" in names:
                    tag_types.add("method")
                if "curriculum_chapters" in names:
                    tag_types.add("exam_scope")
                tags: dict[str, list[Any]] = {}
                if tag_types:
                    placeholders = ", ".join("?" for _ in tag_types)
                    rows = conn.execute(
                        f"""
                        WITH filtered_questions AS ({sql})
                        SELECT f.id AS question_id,
                               t.tag_type, t.tag_value AS value
                        FROM filtered_questions f
                        JOIN question_tags t ON t.question_id = f.id
                        WHERE t.tag_type IN ({placeholders})
                          AND COALESCE(t.tag_value, '') <> ''
                        """,
                        [*params, *sorted(tag_types)],
                    ).fetchall()
                    for row in rows:
                        tags.setdefault(str(row["tag_type"]), []).append(row)
                columns = (
                    conn.execute(sql, params).fetchall()
                    if any(name in column_specs for name in names) else []
                )
                for name in names:
                    if name in tag_specs:
                        tag_type, dimension = tag_specs[name]
                        tag_rows = tags.get(tag_type, [])
                        if dimension == "thought":
                            tag_rows = [*tag_rows, *tags.get("method", [])]
                        result[name] = _tag_facet(
                            tag_rows,
                            taxonomy_dimension=dimension,
                            taxonomy_snapshot=taxonomy_snapshot,
                            taxonomy_identity_lookup=taxonomy_identity_lookup,
                            current_knowledge=self.current_knowledge,
                            allowed_values=(
                                frozenset(ERROR_PRONE_CATEGORIES)
                                if name == "error_types" else None
                            ),
                        )
                    elif name == "curriculum_chapters":
                        result[name] = _curriculum_chapter_facet(
                            tags.get("exam_scope", []),
                        )
                    elif name == "error_pattern_categories":
                        result[name] = [
                            {
                                "value": str(row["value"]),
                                "count": int(row["count"]),
                            }
                            for row in conn.execute(
                                f"""
                                WITH filtered_questions AS ({sql})
                                SELECT ep.category AS value,
                                       COUNT(DISTINCT f.id) AS count
                                FROM filtered_questions f
                                JOIN question_error_patterns ep
                                  ON ep.question_id = f.id
                                WHERE ep.status = 'confirmed'
                                  AND COALESCE(TRIM(ep.category), '') <> ''
                                GROUP BY ep.category
                                ORDER BY count DESC, value
                                """,
                                params,
                            ).fetchall()
                        ]
                    else:
                        result[name] = _column_facet(
                            columns, column=column_specs[name],
                        )
        return result

    def find_similar_questions(
        self,
        question_id: int,
        *,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        # 题库文件未变时整份结果可直接复用；与 list_questions 同一代际令牌。
        generation: tuple[object, ...] | None = None
        taxonomy_token: tuple[object, ...] = ()
        if _ACTIVE_READ_SCOPE.get() is None:
            try:
                generation = _source_generation_token(self.db_path)
                taxonomy_token = _taxonomy_generation_token()
            except (AttributeError, OSError):
                # 治理替身在测试中没有状态文件；无法计算代际时不缓存。
                generation = None
        key = (
            "similar_questions",
            generation,
            self._cache_data_root,
            taxonomy_token,
            int(question_id),
            int(limit),
        )
        if generation is not None:
            cached = _read_result_cache_get(key)
            if cached is not _CACHE_MISS:
                return cached  # type: ignore[return-value]
        result = self._find_similar_questions(question_id, limit=limit)
        if (
            generation is not None
            and generation == _source_generation_token(self.db_path)
            and taxonomy_token == _taxonomy_generation_token()
        ):
            _read_result_cache_put(key, result)
        return result

    def _similar_question_index(self, conn, rows, cache_key) -> SimilarQuestionIndex:
        with _SIMILAR_INDEX_CACHE_LOCK:
            if cache_key is not None and cache_key in _SIMILAR_INDEX_CACHE:
                _SIMILAR_INDEX_CACHE.move_to_end(cache_key)
                return _SIMILAR_INDEX_CACHE[cache_key]
            resolver = self.current_knowledge
            public_tags = _load_page_tags(
                conn, [int(row["id"]) for row in rows], current_knowledge=resolver,
            )
            index = build_similar_question_index(
                rows, public_tags, resolver, db_path=self.db_path,
                connection=conn, data_root=self.data_root,
            )
            if (cache_key is not None
                    and cache_key[0] == _source_generation_token(self.db_path)
                    and cache_key[2] == _taxonomy_generation_token()):
                _SIMILAR_INDEX_CACHE[cache_key] = index
                _SIMILAR_INDEX_CACHE.move_to_end(cache_key)
                while len(_SIMILAR_INDEX_CACHE) > 2:
                    _SIMILAR_INDEX_CACHE.popitem(last=False)
            return index

    def _find_similar_questions(
        self,
        question_id: int,
        *,
        limit: int,
    ) -> list[dict[str, Any]] | None:
        cache_key = None
        if _ACTIVE_READ_SCOPE.get() is None:
            try:
                generation = _source_generation_token(self.db_path)
                if generation is not None:
                    cache_key = (generation, self._cache_data_root, _taxonomy_generation_token())
            except (AttributeError, OSError):
                pass
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    q.id,
                    q.paper_id,
                    q.question_number,
                    q.question_type,
                    q.question_text,
                    q.answer_text,
                    q.image_paths AS _image_paths,
                    q.difficulty,
                    q.typicality,
                    q.reason,
                    q.needs_review,
                    q.has_images,
                    q.needs_image_review,
                    q.created_at,
                    q.updated_at,
                    p.title AS paper_title,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.textbook_version
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.id
                """
            ).fetchall()
            rows_by_id = {int(row["id"]): row for row in rows}
            target = rows_by_id.get(int(question_id))
            if target is None:
                return None
            if (cache_key is not None
                    and (cache_key[0] != _source_generation_token(self.db_path)
                         or cache_key[2] != _taxonomy_generation_token())):
                cache_key = None
            index = self._similar_question_index(conn, rows, cache_key)
            selected = index.rank(int(question_id), max(1, min(int(limit), 20)))
            selected_ids = [candidate_id for candidate_id, _ in selected]
            display_tags = _load_page_tags(
                conn,
                [int(question_id), *selected_ids],
                current_knowledge=self.current_knowledge,
            )
            target_tags = display_tags.get(int(question_id), [])
            revisions = question_revisions(conn, selected_ids)
            review_ids = _load_criteria_needs_review_ids(conn, selected_ids)

        items: list[dict[str, Any]] = []
        target_input = index.questions[index.positions[int(question_id)]]
        for candidate_id, score in selected:
            candidate = rows_by_id[candidate_id]
            candidate_input = index.questions[index.positions[candidate_id]]
            wording_score = similarity_dice(target_input.text_grams, candidate_input.text_grams)
            candidate_tags = display_tags.get(candidate_id, [])
            reasons = _similarity_reasons(
                target,
                target_tags,
                candidate,
                candidate_tags,
                wording_score=wording_score,
            )
            item = self._public_question_with_rich_content(
                candidate,
                candidate_tags,
                revision=revisions[candidate_id],
            )
            item["criteria_needs_review"] = candidate_id in review_ids
            item["similarity_score"] = score
            item["similarity_reasons"] = reasons
            items.append(item)
        return items

    def get_question(self, question_id: int) -> dict[str, Any] | None:
        with _read_connection(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT
                    q.id,
                    q.paper_id,
                    q.question_number,
                    q.question_type,
                    q.question_text,
                    q.answer_text,
                    q.page_range,
                    q.image_paths AS _image_paths,
                    q.difficulty,
                    q.typicality,
                    q.reason,
                    q.needs_review,
                    q.has_images,
                    q.needs_image_review,
                    q.created_at,
                    q.updated_at,
                    p.title AS paper_title,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.textbook_version
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE {_ACTIVE_QUESTION_PREDICATE_SQL}
                """,
                (int(question_id),),
            ).fetchone()
            if row is None:
                return None
            tags = _load_page_tags(
                conn,
                [int(question_id)],
                current_knowledge=self.current_knowledge,
            ).get(
                int(question_id),
                [],
            )
            previews = _load_question_previews(conn, int(question_id))
            revision = question_revision(conn, int(question_id))
            review_ids = _load_criteria_needs_review_ids(conn, [int(question_id)])
            from question_bank.services.error_pattern_service import (
                list_patterns,
                pattern_skill_index,
                preferred_active_patterns,
            )

            pattern_rows = preferred_active_patterns(list_patterns(
                conn, [int(question_id)], statuses=("confirmed", "candidate")
            )[int(question_id)])
            pattern_skills = pattern_skill_index(
                conn, self.db_path, int(question_id), pattern_rows,
                knowledge=self.current_knowledge,
            )
            selectable_skills = _question_skill_options(
                tags, self.current_knowledge,
            )

        item = self._public_question_with_rich_content(
            row,
            tags,
            revision=revision,
        )
        item["criteria_needs_review"] = int(question_id) in review_ids
        item["selectable_skills"] = selectable_skills
        item["error_patterns"] = [
            {
                "id": int(pattern["id"]),
                "category": pattern["category"],
                "pattern": pattern["pattern"],
                "explanation": pattern["explanation"],
                "trigger_kind": pattern["trigger_kind"],
                "trigger_value": pattern["trigger_value"],
                "status": pattern["status"],
                "source": pattern["source"],
                "has_evidence": bool(pattern["occurrences"]),
                "skill_key": pattern_skills.get(int(pattern["id"]), {}).get("skill_key"),
                "skill_label": pattern_skills.get(int(pattern["id"]), {}).get("skill_label"),
                "skill_keys": pattern_skills.get(int(pattern["id"]), {}).get("skill_keys") or [],
                "skill_labels": pattern_skills.get(int(pattern["id"]), {}).get("skill_labels") or [],
                "skill_source": pattern_skills.get(int(pattern["id"]), {}).get("skill_source"),
            }
            for pattern in pattern_rows if pattern.get("id") is not None
        ]
        from backend.error_patterns import (
            extract_canonical_option,
            normalize_option_answer,
            parse_option_letters,
        )

        correct = normalize_option_answer(row["answer_text"]) or extract_canonical_option(
            row["answer_text"]
        )
        item["wrong_option_letters"] = (
            [letter for letter in parse_option_letters(row["question_text"]) if letter != correct]
            if str(row["question_type"] or "").strip() in {
                "choice", "single_choice", "选择题", "单选题",
            } else []
        )
        item["page_range"] = row["page_range"]
        item["assets"] = [
            {
                "index": index,
                "url": f"/api/question-bank/questions/{question_id}/assets/{index}",
            }
            for index in range(len(item["asset_urls"]))
        ]
        item["previews"] = previews
        return item

    def question_skill_options(self, question_id: int) -> list[dict[str, str]]:
        """本题可关联的技能 [{key, label}]；与题目详情 ``selectable_skills`` 同源。"""
        with _read_connection(self.db_path) as conn:
            knowledge = self.current_knowledge
            if knowledge is None:
                return []
            tags = _load_page_tags(
                conn, [int(question_id)], current_knowledge=knowledge,
            ).get(int(question_id), [])
            return _question_skill_options(tags, knowledge)

    def find_exact_duplicate_tag_analysis(
        self,
        question_id: int,
    ) -> tuple[TagAnalysis, str | None] | None:
        with _read_connection(self.db_path) as conn:
            target = conn.execute(
                """
                SELECT id, question_number, question_text, answer_text, difficulty, reason, image_paths, has_images
                FROM questions
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
                """,
                (int(question_id),),
            ).fetchone()
            if target is None:
                return None
            from question_bank.services.duplicate_analysis_copy_service import (
                exact_question_key,
            )
            target_key = exact_question_key(dict(target), data_root=self.data_root or self.db_path.parent.parent)
            if not target_key:
                return None
            rows = conn.execute(
                """
                SELECT q.id, q.question_number, q.question_text, q.answer_text, q.difficulty, q.reason, q.image_paths, q.has_images
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id <> ? AND q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.updated_at DESC, q.id DESC
                """,
                (int(question_id),),
            ).fetchall()
            for row in rows:
                if exact_question_key(dict(row), data_root=self.data_root or self.db_path.parent.parent) != target_key:
                    continue
                tags = [
                    dict(tag)
                    for tag in conn.execute(
                        """
                        SELECT tag_type, tag_value, confidence, source, model_name
                        FROM question_tags
                        WHERE question_id = ?
                        ORDER BY id
                        """,
                        (int(row["id"]),),
                    ).fetchall()
                ]
                candidate = {**dict(row), "tags": tags}
                if has_complete_analysis_tags(candidate):
                    return _analysis_from_question(candidate)
        return None

    def get_questions(self, question_ids: Iterable[int]) -> list[dict[str, Any]]:
        return self._get_questions_by_id(question_ids, include_storage_fields=False)

    def task_papers(
        self, *, question_ids: Iterable[int] = (), paper_ids: Iterable[int] = (),
    ) -> list[dict[str, Any]]:
        """Read only identities and titles, without loading question content."""
        questions = sorted(set(question_ids))
        papers = sorted(set(paper_ids))
        if not questions and not papers:
            return []
        found: dict[int, dict[str, Any]] = {}
        with _read_connection(self.db_path) as conn:
            for ids, by_question in ((papers, False), (questions, True)):
                for offset in range(0, len(ids), 400):
                    batch = ids[offset:offset + 400]
                    placeholders = ','.join('?' for _ in batch)
                    condition = f"p.id IN ({placeholders})"
                    if by_question:
                        condition = (
                            f"p.id IN (SELECT paper_id FROM questions WHERE id IN ({placeholders}) AND COALESCE(is_deleted, 0) = 0 "
                            "UNION SELECT occ.paper_id FROM paper_question_occurrences occ "
                            "JOIN questions q ON q.id = occ.question_id "
                            f"WHERE occ.question_id IN ({placeholders}) AND COALESCE(q.is_deleted, 0) = 0)"
                        )
                    rows = conn.execute(
                        f"SELECT p.id, p.title FROM papers p WHERE {condition} "
                        "AND COALESCE(p.import_status, '') <> 'deleted' ORDER BY p.id",
                        [*batch, *batch] if by_question else batch,
                    ).fetchall()
                    for row in rows:
                        found[int(row['id'])] = {'id': int(row['id']), 'title': row['title']}
        return [found[key] for key in sorted(found)]

    def import_task_filename(self, request_id: str) -> str | None:
        """Read import display metadata without creating directories or hashing input."""
        if self.data_root is None or re.fullmatch(r'[0-9a-f]{32}', request_id) is None:
            return None
        try:
            data_root = self.data_root.resolve()
            requests_root = (data_root / 'question_bank' / 'import_staging' / 'requests').resolve()
            requests_root.relative_to(data_root)
            request_path = (requests_root / f'{request_id}.json').resolve()
            request_path.relative_to(requests_root)
            payload = json.loads(request_path.read_text(encoding='utf-8'))
            if payload.get('request_id') != request_id:
                return None
            filename = payload.get('filename')
            if not isinstance(filename, str) or not filename.strip():
                return None
            return PureWindowsPath(PurePosixPath(filename).name).name
        except (OSError, ValueError, AttributeError):
            return None

    def get_questions_for_export(
        self,
        question_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        """Return the private storage fields needed by local document exporters."""

        return self._get_questions_by_id(question_ids, include_storage_fields=True)

    def get_question_for_preview(self, question_id: int) -> dict[str, Any] | None:
        """Return one question with its private source reference for preview rendering."""

        questions = self._get_questions_by_id(
            [int(question_id)],
            include_storage_fields=True,
        )
        return questions[0] if questions else None

    def _get_questions_by_id(
        self,
        question_ids: Iterable[int],
        *,
        include_storage_fields: bool,
    ) -> list[dict[str, Any]]:
        # An empty/invalid id list must not require a database connection;
        # the dedupe keeps parity with _load_questions_by_id's own guard.
        ordered_ids: list[int] = []
        for value in question_ids:
            question_id = int(value)
            if question_id > 0 and question_id not in ordered_ids:
                ordered_ids.append(question_id)
        if not ordered_ids:
            return []
        with _read_connection(self.db_path):
            return self._load_questions_by_id(ordered_ids, include_storage_fields=include_storage_fields)

    def _load_questions_by_id(
        self, question_ids: Iterable[int], *, include_storage_fields: bool,
    ) -> list[dict[str, Any]]:
        ordered_ids: list[int] = []
        for value in question_ids:
            question_id = int(value)
            if question_id > 0 and question_id not in ordered_ids:
                ordered_ids.append(question_id)
        if not ordered_ids:
            return []
        if len(ordered_ids) > 500:
            raise ValueError("At most 500 question IDs are supported")

        placeholders = ", ".join("?" for _ in ordered_ids)
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    q.id,
                    q.paper_id,
                    q.question_number,
                    q.question_type,
                    q.question_text,
                    q.answer_text,
                    q.source_file AS _source_file,
                    q.page_range,
                    q.image_paths AS _image_paths,
                    q.difficulty,
                    q.typicality,
                    q.reason,
                    q.needs_review,
                    q.has_images,
                    q.needs_image_review,
                    q.created_at,
                    q.updated_at,
                    p.title AS paper_title,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.textbook_version
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id IN ({placeholders})
                  AND COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """,
                ordered_ids,
            ).fetchall()
            rows_by_id = {int(row["id"]): row for row in rows}
            tags_by_question = _load_page_tags(
                conn,
                list(rows_by_id),
                current_knowledge=self.current_knowledge,
            )
            revisions = question_revisions(conn, list(rows_by_id))
            review_ids = _load_criteria_needs_review_ids(conn, list(rows_by_id))

        items: list[dict[str, Any]] = []
        for question_id in ordered_ids:
            row = rows_by_id.get(question_id)
            if row is None:
                continue
            item = self._public_question_with_rich_content(
                rows_by_id[question_id],
                tags_by_question.get(question_id, []),
                revision=revisions[question_id],
            )
            item["criteria_needs_review"] = question_id in review_ids
            if include_storage_fields:
                raw_question_text = str(row["question_text"] or "")
                raw_answer_text = (
                    str(row["answer_text"] or "")
                    if row["answer_text"] is not None
                    else None
                )
                item.update(
                    {
                        "question_text": raw_question_text,
                        "answer_text": raw_answer_text,
                        "source_file": row["_source_file"],
                        "page_range": row["page_range"],
                        "image_paths": _question_asset_paths(
                            row["_image_paths"],
                            raw_question_text,
                            raw_answer_text or "",
                        ),
                    }
                )
            items.append(item)
        return items

    def _public_question_with_rich_content(
        self,
        row: sqlite3.Row,
        tags: list[dict[str, Any]],
        *,
        revision: str,
    ) -> dict[str, Any]:
        question_id = int(row["id"])
        rich_payload = self._load_rich_content(question_id)
        asset_paths, rich_blocks = _ordered_question_assets(row, rich_payload)
        item = _public_question_item(
            row,
            tags,
            asset_paths=asset_paths,
            revision=revision,
        )
        item["question_text"] = strip_question_source_score(
            item["question_text"], question_number=str(row["question_number"] or ""),
        )
        item["rich_content"] = _public_rich_content(
            question_id,
            rich_payload is not None,
            rich_blocks,
            asset_paths,
            question_number=str(row["question_number"] or ""),
        )
        item["duplicate_of_question_id"] = None
        item["duplicate_labels_reused"] = False
        with _read_connection(self.db_path) as conn:
            source = conn.execute("""SELECT q.* FROM question_duplicate_links link
                JOIN questions q ON q.id=link.duplicate_of_question_id
                LEFT JOIN papers p ON p.id=q.paper_id
                WHERE link.question_id=? AND q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'""",
                (question_id,)).fetchone()
            if source is not None:
                from question_bank.services.duplicate_analysis_copy_service import (
                    exact_question_key,
                )
                target_key = exact_question_key(dict(row), data_root=self.data_root or self.db_path.parent.parent, rich_content=rich_payload)
                if target_key and target_key == exact_question_key(dict(source), data_root=self.data_root or self.db_path.parent.parent):
                    item["duplicate_of_question_id"] = int(source["id"])
                    source_tags = {(str(tag["tag_type"]), str(tag["tag_value"])) for tag in conn.execute(
                        "SELECT tag_type,tag_value FROM question_tags WHERE question_id=?", (int(source["id"]),)).fetchall()}
                    target_tags = {(str(tag["tag_type"]), str(tag["tag_value"])) for tag in tags}
                    # 难度可能是小数（如 8.8）；按数值比较，"8" 与 "8.0" 视为相同。
                    def _difficulty_number(value: Any) -> float | None:
                        try:
                            number = float(str(value or "").strip())
                        except (TypeError, ValueError):
                            return None
                        return number
                    item["duplicate_labels_reused"] = (
                        bool(source_tags)
                        and source_tags <= target_tags
                        and _difficulty_number(row["difficulty"])
                        == _difficulty_number(source["difficulty"])
                    )
        return item

    def resolve_asset(self, question_id: int, asset_index: int) -> ResolvedFile:
        with _read_connection(self.db_path) as conn:
            row = _load_question_media_row(conn, int(question_id))
        if row is None:
            raise QuestionMediaNotFound("Question media resource not found")

        rich_payload = self._load_rich_content(int(question_id))
        asset_paths, _ = _ordered_question_assets(row, rich_payload)
        index = int(asset_index)
        if index < 0 or index >= len(asset_paths):
            raise QuestionMediaNotFound("Question media resource not found")
        return self._resolve_media_path(
            asset_paths[index],
            search_subdir=_QUESTION_ASSET_SUBDIR,
        )

    def resolve_preview(self, question_id: int, preview_type: str) -> ResolvedFile:
        normalized_type = str(preview_type)
        if normalized_type not in {"question", "answer"}:
            raise QuestionMediaNotFound("Question media resource not found")
        with _read_connection(self.db_path) as conn:
            question = _load_question_media_row(conn, int(question_id))
            if question is None:
                raise QuestionMediaNotFound("Question media resource not found")
            rows = _select_current_preview_rows(
                conn,
                int(question_id),
                preview_type=normalized_type,
            )
        if not rows or not str(rows[0]["_image_path"] or "").strip():
            raise QuestionMediaNotFound("Question media resource not found")
        return self._resolve_media_path(
            rows[0]["_image_path"],
            search_subdir=_QUESTION_PREVIEW_SUBDIR,
        )

    def _resolve_media_path(
        self,
        path_value: Any,
        *,
        search_subdir: str,
    ) -> ResolvedFile:
        if self.data_root is None:
            raise ControlledFileForbidden(
                "Question media storage boundary is unavailable"
        )
        try:
            data_root = self.data_root.resolve(strict=False)
            expected_root = data_root / search_subdir
            route_root = expected_root.resolve(strict=False)
            route_root.relative_to(data_root)
            if route_root != expected_root:
                raise ValueError("Question media route root is redirected")
            candidate = resolve_question_bank_asset_path(
                path_value,
                data_root=data_root,
                search_subdirs=(search_subdir,),
            )
        except (AmbiguousQuestionBankAssetPathError, OSError, ValueError) as exc:
            raise ControlledFileForbidden(
                "Question media storage boundary could not be resolved"
            ) from exc
        return resolve_controlled_file(
            candidate,
            root=route_root,
            data_root=data_root,
            allowed_suffixes=_QUESTION_IMAGE_SUFFIXES,
        )

    def _load_rich_content(self, question_id: int) -> dict[str, Any] | None:
        if self.data_root is None:
            return None
        try:
            data_root = self.data_root.resolve(strict=True)
            expected_root = data_root / "question_bank" / "rich_content"
            rich_root = expected_root.resolve(strict=True)
            rich_root.relative_to(data_root)
            if rich_root != expected_root:
                return None
            sidecar = (rich_root / f"question_{int(question_id)}.json").resolve(
                strict=True
            )
            sidecar.relative_to(rich_root)
            if not sidecar.is_file():
                return None
            info = sidecar.stat()
            cache_key = (
                str(rich_root),
                int(question_id),
                int(info.st_mtime_ns),
                int(info.st_size),
            )
            cached = _rich_content_cache_get(cache_key)
            if cached is not None:
                return cached
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if type(payload.get("version")) is not int:
            return None
        if payload["version"] != _RICH_CONTENT_VERSION:
            return None
        if type(payload.get("question_id")) is not int:
            return None
        if payload["question_id"] != int(question_id):
            return None
        if not _valid_rich_block_list(payload.get("question_blocks")):
            return None
        if not _valid_rich_block_list(payload.get("answer_blocks")):
            return None
        payload["question_blocks"] = clean_question_blocks(
            payload["question_blocks"]
        )
        _rich_content_cache_put(cache_key, payload)
        return payload


def _scope_mode_selection(
    filters: QuestionReadFilters,
    expanded_knowledge: tuple[str, ...],
    expanded_scopes: tuple[str, ...],
    current_knowledge,
) -> dict[str, Any] | None:
    """Resolve the selected scope (chapter/section/knowledge filters) into
    section knowledge keys + volume order for strict/primary matching."""
    if not (
        filters.exam_scopes
        or filters.curriculum_sections
        or expanded_knowledge
    ):
        return None
    catalog = load_curriculum_catalog()
    chapter_by_scope: dict[str, dict[str, Any]] = {}
    sections_of_chapter: dict[str, tuple[str, ...]] = {}
    section_key_of_id: dict[str, str] = {}
    section_id_of_key: dict[str, str] = {}
    volume_order: dict[str, int] = {}
    for volume in catalog["volumes"]:
        order = int(volume["order"])
        for chapter in volume["chapters"]:
            chapter_key = str(chapter["knowledge_id"])
            volume_order[chapter_key] = order
            sections_of_chapter[chapter_key] = tuple(
                str(section["knowledge_id"]) for section in chapter["sections"]
            )
            for value in chapter.get("exam_scope_values") or ():
                chapter_by_scope.setdefault(str(value), chapter)
            for section in chapter["sections"]:
                section_key = str(section["knowledge_id"])
                volume_order[section_key] = order
                section_key_of_id[str(section["id"])] = section_key
                section_id_of_key[section_key] = str(section["id"])
                for point in section["knowledge_points"]:
                    volume_order[str(point["id"])] = order

    parent_of = {
        str(relation.source_key): str(relation.target_key)
        for relation in current_knowledge.relations
        if relation.relation_type == "parent"
    }

    def anchor_sections(key: str) -> tuple[str, ...]:
        node = curriculum_knowledge_node(key)
        if node is None:
            parent = parent_of.get(key)
            if parent:
                node = curriculum_knowledge_node(parent)
                key = parent
            if node is None:
                return ()
        level = int(node.get("level") or 0)
        if level == 1:
            return sections_of_chapter.get(key, ())
        if level == 2:
            return (key,)
        ancestors = curriculum_knowledge_ancestors(key)
        return ancestors[:1] if ancestors else ()

    section_keys: set[str] = set()
    volume_orders: set[int] = set()

    def absorb_key(key: str) -> None:
        for section_key in anchor_sections(key):
            section_keys.add(section_key)
            if section_key in volume_order:
                volume_orders.add(volume_order[section_key])

    for value in expanded_scopes:
        chapter = chapter_by_scope.get(str(value))
        if chapter is None:
            continue
        chapter_key = str(chapter["knowledge_id"])
        section_keys.update(sections_of_chapter.get(chapter_key, ()))
        if chapter_key in volume_order:
            volume_orders.add(volume_order[chapter_key])

    for value in filters.curriculum_sections:
        absorb_key(section_key_of_id.get(str(value), str(value)))

    for value in expanded_knowledge:
        for identity in current_knowledge.resolve(str(value)):
            absorb_key(str(identity.stable_key))

    return {
        "section_keys": tuple(sorted(section_keys)),
        # Tag fallback compares against stored tag values, which are the raw
        # selected ids (catalog section ids or free values like 小节甲).
        "section_tag_ids": tuple(
            sorted(
                {section_id_of_key[key] for key in section_keys if key in section_id_of_key}
                | {str(value) for value in filters.curriculum_sections}
            )
        ),
        "volume_order_max": max(volume_orders) if volume_orders else 0,
        "scope_values": expanded_scopes if filters.exam_scopes else (),
        "selected_sections": bool(filters.curriculum_sections),
        "knowledge_values": expanded_knowledge,
    }


def _scope_mode_sql(mode: str, selection: dict[str, Any]) -> tuple[str, list[Any]]:
    """WHERE clause for strict/primary scope matching.

    ``question_scope_summary`` (rebuilt when evidence links are written) is the
    authoritative scope source.  Questions without a summary row fall back to
    the legacy tag checks so unlinked questions stay visible under their
    model-derived ownership tags.
    """
    section_keys = selection["section_keys"]
    section_tag_ids = selection["section_tag_ids"]
    placeholders = ",".join("?" for _ in section_keys) or "''"
    # Tag fallback keeps the legacy per-dimension AND semantics: each selected
    # dimension contributes one required EXISTS clause.
    tag_clauses: list[str] = []
    tag_params: list[Any] = []
    if selection["scope_values"]:
        tag_clauses.append(
            "(tag_type = 'exam_scope' AND tag_value IN ("
            + ",".join("?" for _ in selection["scope_values"])
            + "))"
        )
        tag_params.extend(selection["scope_values"])
    if selection["selected_sections"]:
        tag_clauses.append(
            "(tag_type = 'curriculum_section' AND tag_value IN ("
            + ",".join("?" for _ in section_tag_ids)
            + "))"
        )
        tag_params.extend(section_tag_ids)
    if selection["knowledge_values"]:
        tag_clauses.append(
            "(tag_type = 'knowledge_point' AND tag_value IN ("
            + ",".join("?" for _ in selection["knowledge_values"])
            + "))"
        )
        tag_params.extend(selection["knowledge_values"])
    legacy_match = (
        " AND ".join(
            "EXISTS (SELECT 1 FROM question_tags lt "
            "WHERE lt.question_id = q.id AND "
            + clause
            + ")"
            for clause in tag_clauses
        )
        if tag_clauses
        else "0"
    )
    no_summary = (
        "NOT EXISTS (SELECT 1 FROM question_scope_summary s0 "
        "WHERE s0.question_id = q.id)"
    )
    if mode == "primary":
        clause = (
            "EXISTS ("
            "SELECT 1 FROM question_scope_summary s "
            "WHERE s.question_id = q.id "
            f"AND s.primary_section_id IN ({placeholders})"
            ")"
        )
        return (
            f"({clause} OR ({no_summary} AND {legacy_match}))",
            [*section_keys, *tag_params],
        )
    # strict: every direct-link section must sit inside the selection and
    # supporting prerequisites must come from earlier volumes.
    clause = (
        "EXISTS ("
        "SELECT 1 FROM question_scope_summary s "
        "WHERE s.question_id = q.id "
        "AND json_array_length(s.direct_section_ids_json) > 0 "
        "AND s.supporting_max_volume_order < ? "
        "AND NOT EXISTS ("
        "SELECT 1 FROM json_each(s.direct_section_ids_json) je "
        f"WHERE je.value NOT IN ({placeholders})"
        ")"
        ")"
    )
    strict_fallback = f"({legacy_match}"
    strict_params: list[Any] = [
        selection["volume_order_max"],
        *section_keys,
        *tag_params,
    ]
    if section_tag_ids:
        strict_fallback += (
            " AND NOT EXISTS (SELECT 1 FROM question_tags ft "
            "WHERE ft.question_id = q.id AND ft.tag_type = 'curriculum_section' "
            "AND ft.tag_value NOT IN ("
            + ",".join("?" for _ in section_tag_ids)
            + "))"
        )
        strict_params.extend(section_tag_ids)
    if selection["scope_values"]:
        strict_fallback += (
            " AND NOT EXISTS (SELECT 1 FROM question_tags ft "
            "WHERE ft.question_id = q.id AND ft.tag_type = 'exam_scope' "
            "AND ft.tag_value NOT IN ("
            + ",".join("?" for _ in selection["scope_values"])
            + "))"
        )
        strict_params.extend(selection["scope_values"])
    strict_fallback += ")"
    return (
        f"({clause} OR ({no_summary} AND {strict_fallback}))",
        strict_params,
    )


def _question_filter_parts(
    filters: QuestionReadFilters,
    *,
    taxonomy_expansions: dict[str, tuple[str, ...]] | None = None,
    current_knowledge: CurrentKnowledgeResolver | None = None,
) -> tuple[list[str], list[str], list[Any]]:
    difficulty_range = None
    if filters.difficulty_min is not None and filters.difficulty_max is not None:
        difficulty_range = (filters.difficulty_min, filters.difficulty_max)

    def expand(
        dimension: str,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not values:
            return ()
        if taxonomy_expansions is not None:
            cached = taxonomy_expansions.get(dimension)
            if cached is not None:
                return cached
        if dimension == "knowledge":
            if current_knowledge is None:
                return ()
            return tuple(
                dict.fromkeys(
                    stored
                    for value in values
                    for stored in current_knowledge.stored_values_for_term(
                        value
                    )
                    if current_knowledge.resolve(stored)
                )
            )
        return get_taxonomy_governance().expand_filter_values(
            dimension,
            values,
        )

    requested_knowledge = tuple(
        value
        for value in (
            *filters.knowledge_points,
            filters.knowledge_point,
        )
        if value
    )
    expanded_knowledge = expand("knowledge", requested_knowledge)
    expanded_scopes = expand("curriculum", filters.exam_scopes)
    scope_mode = str(filters.scope_mode or "any").strip().casefold()
    scope_clause: tuple[str, list[Any]] | None = None
    if (
        scope_mode in {"strict", "primary"}
        and current_knowledge is not None
        and (expanded_scopes or filters.curriculum_sections or expanded_knowledge)
    ):
        selection = _scope_mode_selection(
            filters,
            expanded_knowledge,
            expanded_scopes,
            current_knowledge,
        )
        if selection is not None:
            scope_clause = _scope_mode_sql(scope_mode, selection)
    joins, where, params = build_question_filter_query(
        question_number=filters.question_number,
        keyword=filters.keyword,
        knowledge_point=None,
        difficulty_range=difficulty_range,
        question_types=list(filters.question_types),
        paper_ids=list(filters.paper_ids),
        years=list(filters.years),
        exam_types=list(filters.exam_types),
        grades=list(filters.grades),
        curriculum_volume_ids=list(filters.curriculum_volume_ids),
        tag_filters={
            tag_type: list(values)
            for tag_type, values in (
                *(
                    ()
                    if scope_clause is not None
                    else (
                        (
                            "exam_scope",
                            expanded_scopes,
                        ),
                        ("curriculum_section", filters.curriculum_sections),
                        (
                            "knowledge_point",
                            expanded_knowledge,
                        ),
                    )
                ),
                (
                    "ability",
                    expand("ability", filters.abilities),
                ),
                (
                    "method",
                    expand("method", filters.methods),
                ),
                (
                    "thought",
                    expand("thought", filters.thoughts),
                ),
                (
                    "model",
                    expand("model", filters.models),
                ),
                (
                    "special_type",
                    expand("special_type", filters.special_types),
                ),
                # 错因不在治理维度内，直接精确匹配，不做别名扩展。
                ("error_type", filters.error_types),
                ("student_level", filters.student_levels),
                ("teaching_stage", filters.teaching_stages),
                ("sub_skill", filters.sub_skills),
            )
            if values
        },
        is_deleted=False,
        tag_status=_TAG_STATUS_MAP[filters.tag_status],
    )
    if requested_knowledge and not expanded_knowledge:
        where.append("1 = 0")
    if filters.analysis_status in {"complete", "incomplete"}:
        complete_sql = f"""
            (
                CAST(q.difficulty AS REAL) BETWEEN 1 AND 10
                AND EXISTS (SELECT 1 FROM question_tags qt
                            WHERE qt.question_id = q.id
                              AND qt.tag_type = 'ability'
                              AND COALESCE(qt.tag_value, '') <> '')
                AND ({_OWNERSHIP_READY_SQL})
                AND EXISTS (
                    SELECT 1
                    FROM question_solution_evidence_versions evidence
                    WHERE evidence.question_id = q.id
                      AND evidence.status IN ('proposed', 'approved')
                )
                AND EXISTS (
                    SELECT 1
                    FROM training_criterion_heads head
                    JOIN training_criterion_versions version
                      ON version.version_id = head.current_version_id
                    WHERE head.question_id = q.id
                      AND version.status IN ('proposed', 'approved')
                )
            )
        """
        where.append(
            complete_sql
            if filters.analysis_status == "complete"
            else f"NOT {complete_sql}"
        )
    if filters.criteria_needs_review:
        where.append(_CRITERIA_NEEDS_REVIEW_SQL.format(qid="q.id"))
    error_pattern_categories = list(
        dict.fromkeys(
            item
            for value in filters.error_pattern_categories
            if (item := _filter_text(value))
        )
    )
    if error_pattern_categories:
        placeholders = ", ".join("?" for _ in error_pattern_categories)
        where.append(
            "EXISTS (SELECT 1 FROM question_error_patterns ep "
            "WHERE ep.question_id = q.id "
            "AND ep.status = 'confirmed' "
            f"AND ep.category IN ({placeholders}))"
        )
        params.extend(error_pattern_categories)
    if filters.teaching_progress_chapter.strip():
        allowed_prefixes = teaching_progress_allowed_prefixes(
            filters.teaching_progress_chapter
        )
        allowed_scope_values = teaching_progress_allowed_exam_scope_values(
            filters.teaching_progress_chapter
        )
        if allowed_prefixes is None or allowed_scope_values is None:
            # 无法解析进度上限时失败关闭，不静默放行。
            where.append("1 = 0")
        else:
            allowed_keys = teaching_progress_allowed_stable_keys(
                filters.teaching_progress_chapter
            )
            exact_keys, key_prefixes = (
                allowed_keys if allowed_keys is not None else ((), ())
            )
            allowed_clauses = [
                "tp.tag_value LIKE ?" for _ in allowed_prefixes
            ]
            allowed_clauses.extend(
                "tp.tag_value LIKE ?" for _ in key_prefixes
            )
            if exact_keys:
                allowed_clauses.append(
                    "tp.tag_value IN ("
                    + ", ".join("?" for _ in exact_keys)
                    + ")"
                )
            allowed_sql = " OR ".join(allowed_clauses)
            scope_placeholders = ", ".join("?" for _ in allowed_scope_values)
            # knowledge_point 与 prerequisite 可为层级路径或稳定键，
            # 须落在已学范围内；exam_scope 为空格分隔值，须整体落在
            # 已学 exam_scope 值集合内。
            where.append(
                "NOT EXISTS ("
                "SELECT 1 FROM question_tags tp "
                "WHERE tp.question_id = q.id "
                "AND COALESCE(tp.tag_value, '') <> '' "
                "AND ("
                "(tp.tag_type IN ('knowledge_point', 'prerequisite') "
                f"AND NOT ({allowed_sql})) "
                "OR (tp.tag_type = 'exam_scope' "
                f"AND tp.tag_value NOT IN ({scope_placeholders}))"
                ")"
                ")"
            )
            params.extend(f"{prefix}%" for prefix in allowed_prefixes)
            params.extend(f"{prefix}%" for prefix in key_prefixes)
            params.extend(exact_keys)
            params.extend(allowed_scope_values)
    if scope_clause is not None:
        where.append(scope_clause[0])
        params.extend(scope_clause[1])
    return joins, where, params


def _taxonomy_filter_expansions(
    filters: QuestionReadFilters,
    *,
    current_knowledge: CurrentKnowledgeResolver | None = None,
) -> dict[str, tuple[str, ...]]:
    governance = get_taxonomy_governance()
    result: dict[str, tuple[str, ...]] = {}
    for dimension, values in (
        ("curriculum", filters.exam_scopes),
        (
            "knowledge",
            tuple(
                value
                for value in (
                    *filters.knowledge_points,
                    filters.knowledge_point,
                )
                if value
            ),
        ),
        ("ability", filters.abilities),
        ("method", filters.methods),
        ("thought", filters.thoughts),
        ("model", filters.models),
        ("special_type", filters.special_types),
    ):
        if not values:
            continue
        if dimension == "knowledge":
            result[dimension] = (
                ()
                if current_knowledge is None
                else tuple(
                    dict.fromkeys(
                        stored
                        for value in values
                        for stored in current_knowledge.stored_values_for_term(
                            value
                        )
                        if current_knowledge.resolve(stored)
                    )
                )
            )
        else:
            result[dimension] = governance.expand_filter_values(
                dimension, values
            )
    return result


def _tag_facet(
    rows: list[sqlite3.Row],
    *,
    taxonomy_dimension: str | None = None,
    taxonomy_snapshot: dict[str, Any] | None = None,
    taxonomy_identity_lookup: dict[str, dict[str, str]] | None = None,
    current_knowledge: CurrentKnowledgeResolver | None = None,
    allowed_values: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if taxonomy_dimension is not None:
        items = _controlled_taxonomy_facet(
            rows,
            dimension=taxonomy_dimension,
            taxonomy_snapshot=taxonomy_snapshot,
            taxonomy_identity_lookup=taxonomy_identity_lookup,
        )
        if taxonomy_dimension == "knowledge":
            return _current_knowledge_facet(items, current_knowledge)
        return items
    questions_by_value: dict[str, set[int]] = {}
    for row in rows:
        questions_by_value.setdefault(row["value"], set()).add(row["question_id"])
    items = _public_facet_items(
        {"value": value, "count": len(ids)}
        for value, ids in questions_by_value.items()
    )
    if allowed_values is not None:
        items = [item for item in items if item["value"] in allowed_values]
    return items


def _controlled_taxonomy_facet(
    rows: list[sqlite3.Row],
    *,
    dimension: str,
    taxonomy_snapshot: dict[str, Any] | None = None,
    taxonomy_identity_lookup: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    snapshot = (
        taxonomy_snapshot
        if taxonomy_snapshot is not None
        else get_taxonomy_governance().snapshot()
    )
    terms = snapshot["terms_by_dimension"].get(dimension, [])
    alias_index = dict(
        (taxonomy_identity_lookup or {}).get(dimension, {})
    )
    if not alias_index:
        for term in terms:
            canonical_name = str(term.get("name") or "").strip()
            if not canonical_name:
                continue
            for value in (
                term.get("id"),
                canonical_name,
                *term.get("aliases", []),
            ):
                key = _taxonomy_value_key(value)
                if key:
                    alias_index[key] = canonical_name
    thought_index = dict(
        (taxonomy_identity_lookup or {}).get("thought", {})
    )
    questions_by_value: dict[str, set[int]] = {}
    public_values: dict[tuple[str, str], str | None] = {}
    for row in rows:
        raw_value = str(row["value"] or "").strip()
        if not raw_value:
            continue
        identity = (str(row["tag_type"]), raw_value)
        if identity not in public_values:
            key = _taxonomy_value_key(raw_value)
            public_value = alias_index.get(key)
            if dimension == "method" and key in thought_index:
                public_value = None
            elif public_value is None and not (
                dimension == "thought" and identity[0] == "method"
            ):
                public_value = raw_value
            public_values[identity] = public_value
        public_value = public_values[identity]
        if public_value is None:
            continue
        questions_by_value.setdefault(public_value, set()).add(
            int(row["question_id"])
        )
    return [
        {"value": value, "count": len(question_ids)}
        for value, question_ids in sorted(
            questions_by_value.items(),
            key=lambda item: (-len(item[1]), item[0].casefold()),
        )
    ]


def _taxonomy_value_key(value: object) -> str:
    normalized = unicodedata.normalize(
        "NFKC", str(value or "")
    ).casefold()
    return re.sub(r"[\s\W_]+", "", normalized)


def _curriculum_chapter_facet(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    chapters_by_value: dict[str, list[str]] = {}
    for chapter, values in curriculum_chapter_exam_scope_values().items():
        for value in values:
            chapters_by_value.setdefault(value, []).append(chapter)
    questions_by_chapter: dict[str, set[int]] = {}
    for row in rows:
        for chapter in chapters_by_value.get(row["value"], []):
            questions_by_chapter.setdefault(chapter, set()).add(row["question_id"])
    return _public_facet_items(
        {"value": value, "count": len(ids)}
        for value, ids in questions_by_chapter.items()
    )


def _column_facet(
    rows: list[sqlite3.Row], *, column: str,
) -> list[dict[str, Any]]:
    questions_by_value: dict[Any, set[int]] = {}
    for row in rows:
        if row[column] is not None and row[column] != "":
            questions_by_value.setdefault(row[column], set()).add(row["id"])
    return _public_facet_items(
        {"value": value, "count": len(ids)}
        for value, ids in questions_by_value.items()
    )


def _public_facet_items(rows: Iterable[sqlite3.Row | Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        value = _public_tag_value(row["value"])
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + int(row["count"] or 0)
    return [
        {"value": value, "count": count}
        for value, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        if count > 0
    ]


def _current_knowledge_facet(
    items: list[dict[str, Any]],
    resolver: CurrentKnowledgeResolver | None,
) -> list[dict[str, Any]]:
    if resolver is None:
        return []
    counts: dict[str, int] = {}
    for item in items:
        term = resolver.canonical_term(item.get("value"))
        if term is None or not resolver.resolve(term[0]):
            continue
        counts[term[1]] = counts.get(term[1], 0) + int(
            item.get("count") or 0
        )
    return [
        {"value": value, "count": count}
        for value, count in sorted(
            counts.items(), key=lambda pair: (-pair[1], pair[0].casefold())
        )
    ]


def _combined_similarity_score(tag_score: float, wording_score: float) -> float:
    normalized_tag = max(0.0, min(float(tag_score), 1.0))
    normalized_wording = max(0.0, min(float(wording_score), 1.0))
    if normalized_tag > 0:
        return round((normalized_tag * 0.8) + (normalized_wording * 0.2), 4)
    return round(normalized_wording * 0.5, 4)


def _similarity_reasons(
    target: sqlite3.Row,
    target_tags: list[dict[str, Any]],
    candidate: sqlite3.Row,
    candidate_tags: list[dict[str, Any]],
    *,
    wording_score: float,
) -> list[dict[str, Any]]:
    """按维度分组的推荐理由；kind 是稳定标识，前端据其决定标签和样式。

    标签类维度返回原始 tag_value（知识点/技能是全路径，叶子名由前端截取），
    信号类维度的 values 直接是展示短语。
    """

    reasons: list[dict[str, Any]] = []
    for kind, tag_types in (
        ("knowledge_point", ("knowledge_point",)),
        ("skill", ("skill",)),
        ("method", ("method",)),
        ("model", ("model",)),
    ):
        shared = _shared_tag_values(target_tags, candidate_tags, tag_types)
        if shared:
            reasons.append({"kind": kind, "values": shared[:2]})
    target_difficulty = _numeric_difficulty(target["difficulty"])
    candidate_difficulty = _numeric_difficulty(candidate["difficulty"])
    if (
        target_difficulty is not None
        and candidate_difficulty is not None
        and abs(target_difficulty - candidate_difficulty) <= 1
    ):
        reasons.append({"kind": "difficulty", "values": ["难度接近"]})
    if wording_score >= 0.55:
        reasons.append({"kind": "wording", "values": ["题干表述相近"]})
    if (
        not reasons
        and str(target["question_type"] or "").strip()
        and target["question_type"] == candidate["question_type"]
    ):
        reasons.append({"kind": "question_type", "values": ["题型相同"]})
    if not reasons:
        reasons.append({"kind": "text_fragment", "values": ["题干存在相似片段"]})
    return reasons[:4]


def _shared_tag_values(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    tag_types: tuple[str, ...],
) -> list[str]:
    allowed_types = set(tag_types)
    left_values = {
        str(tag.get("tag_value") or "").strip()
        for tag in left
        if tag.get("tag_type") in allowed_types
        and str(tag.get("tag_value") or "").strip()
    }
    right_values = {
        str(tag.get("tag_value") or "").strip()
        for tag in right
        if tag.get("tag_type") in allowed_types
        and str(tag.get("tag_value") or "").strip()
    }
    return sorted(left_values & right_values)


def _numeric_difficulty(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_source_type(value: Any) -> str:
    suffix = Path(str(value or "")).suffix.casefold()
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    return "other"


def _load_question_media_row(
    conn: sqlite3.Connection,
    question_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT
            q.id,
            q.question_text,
            q.answer_text,
            q.image_paths AS _image_paths
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        WHERE {_ACTIVE_QUESTION_PREDICATE_SQL}
        """,
        (int(question_id),),
    ).fetchone()


def _ordered_question_assets(
    row: sqlite3.Row,
    rich_payload: dict[str, Any] | None,
) -> tuple[list[str], list[tuple[str, list[dict[str, Any]]]]]:
    asset_paths = _question_asset_paths(
        row["_image_paths"],
        str(row["question_text"] or ""),
        str(row["answer_text"] or ""),
    )
    rich_blocks = _rich_blocks(rich_payload)
    for _, blocks in rich_blocks:
        for block in blocks:
            for asset_path in block["_asset_paths"]:
                if asset_path not in asset_paths:
                    asset_paths.append(asset_path)
    return asset_paths, rich_blocks


def _current_knowledge_key_tags(
    conn: sqlite3.Connection,
    question_ids: list[int],
    resolver: CurrentKnowledgeResolver | None,
) -> dict[int, list[dict[str, Any]]]:
    """按原始存储值投影规范键，与考频统计共用同一套身份规则。

    粗粒度标签（如只标到章的题）与细粒度标签（标到小节的题）解析
    到不同层级的节点，直接比较会漏掉明显的同族重合；这里沿 parent
    关系把每个规范键的祖先一并计入，让层级包含关系也能得分。
    """
    if not question_ids or resolver is None:
        return {}
    parent_of = {
        relation.source_key: relation.target_key
        for relation in resolver.relations
        if relation.relation_type == "parent"
    }

    def _lineage(stable_key: str) -> set[str]:
        lineage = {stable_key}
        current = stable_key
        while current in parent_of:
            current = parent_of[current]
            if current in lineage:
                break
            lineage.add(current)
        return lineage

    placeholders = ", ".join("?" for _ in question_ids)
    rows = conn.execute(
        f"""
        SELECT question_id, tag_value
        FROM question_tags
        WHERE question_id IN ({placeholders})
          AND tag_type IN ('knowledge_point', 'canonical_knowledge_id')
        """,
        question_ids,
    ).fetchall()
    keys_by_question: dict[int, set[str]] = {}
    resolve_cache: dict[str, tuple[ResolvedKnowledge, ...]] = {}
    lineage_cache: dict[str, set[str]] = {}
    for row in rows:
        raw_value = str(row["tag_value"] or "")
        resolved_items = resolve_cache.get(raw_value)
        if resolved_items is None:
            resolved_items = resolver.resolve(raw_value)
            resolve_cache[raw_value] = resolved_items
        for resolved in resolved_items:
            lineage = lineage_cache.get(resolved.stable_key)
            if lineage is None:
                lineage = _lineage(resolved.stable_key)
                lineage_cache[resolved.stable_key] = lineage
            keys_by_question.setdefault(int(row["question_id"]), set()).update(
                lineage
            )
    return {
        question_id: [
            {"tag_type": "current_knowledge_key", "tag_value": key}
            for key in sorted(keys)
        ]
        for question_id, keys in keys_by_question.items()
    }


_TAG_LOOKUP_CACHE_LOCK = threading.Lock()
# (taxonomy_generation_token, lookups)；状态文件代际变化即自动失效。
_TAG_LOOKUP_CACHE: tuple[
    tuple[object, ...],
    tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]],
] | None = None


def _identity_and_teacher_lookup() -> (
    tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]
):
    """治理查找表重建一次约数百毫秒，按 taxonomy 状态代际缓存复用。

    返回的查找表在进程内共享，调用方只读不写。
    """

    global _TAG_LOOKUP_CACHE
    try:
        token: tuple[object, ...] | None = _taxonomy_generation_token()
    except (AttributeError, OSError):
        token = None
    if token is not None:
        with _TAG_LOOKUP_CACHE_LOCK:
            if _TAG_LOOKUP_CACHE is not None and _TAG_LOOKUP_CACHE[0] == token:
                return _TAG_LOOKUP_CACHE[1]
    lookups = get_taxonomy_governance().identity_and_teacher_lookup()
    if token is not None:
        with _TAG_LOOKUP_CACHE_LOCK:
            _TAG_LOOKUP_CACHE = (token, lookups)
    return lookups


def _load_page_tags(
    conn: sqlite3.Connection,
    question_ids: list[int],
    *,
    current_knowledge: CurrentKnowledgeResolver | None,
    tag_types: Iterable[str] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    if not question_ids:
        return {}
    selected_types = tuple(_PUBLIC_TAG_TYPES if tag_types is None else tag_types)
    if not selected_types:
        return {}
    question_placeholders = ", ".join("?" for _ in question_ids)
    tag_type_placeholders = ", ".join("?" for _ in selected_types)
    rows = conn.execute(
        f"""
        SELECT question_id, tag_type, tag_value, confidence
        FROM question_tags
        WHERE question_id IN ({question_placeholders})
          AND tag_type IN ({tag_type_placeholders})
        ORDER BY id ASC
        """,
        [*question_ids, *selected_types],
    ).fetchall()
    identity_lookup, teacher_lookup = _identity_and_teacher_lookup()
    tags_by_question: dict[int, list[dict[str, Any]]] = {}
    # 同一标签值在不同题上反复出现（全库扫描时可达上万行），规范值解析
    # 是纯函数，按 (tag_type, tag_value) 记忆化避免每行重跑正则与查询。
    resolved_tag_cache: dict[tuple[str, str], tuple[str, str] | None] = {}
    for row in rows:
        raw_type = str(row["tag_type"])
        raw_value = str(row["tag_value"] or "")
        cache_key = (raw_type, raw_value)
        if cache_key in resolved_tag_cache:
            resolved = resolved_tag_cache[cache_key]
        else:
            resolved = _resolve_public_tag(
                raw_type,
                raw_value,
                current_knowledge=current_knowledge,
                identity_lookup=identity_lookup,
                teacher_lookup=teacher_lookup,
            )
            resolved_tag_cache[cache_key] = resolved
        if resolved is None:
            continue
        tag_type, tag_value = resolved
        bucket = tags_by_question.setdefault(int(row["question_id"]), [])
        if any(
            item["tag_type"] == tag_type
            and item["tag_value"] == tag_value
            for item in bucket
        ):
            continue
        bucket.append(
            {
                "tag_type": tag_type,
                "tag_value": tag_value,
                "confidence": (
                    float(row["confidence"])
                    if row["confidence"] is not None
                    else None
                ),
            }
        )
    return tags_by_question


def _resolve_public_tag(
    tag_type: str,
    raw_value: str,
    *,
    current_knowledge: CurrentKnowledgeResolver | None,
    identity_lookup: dict[str, dict[str, str]],
    teacher_lookup: dict[str, dict[str, str]],
) -> tuple[str, str] | None:
    """把一行存储标签解析成公开 (tag_type, tag_value)；不可公开返回 None。"""

    tag_value = _public_tag_value(raw_value)
    if tag_value is None:
        return None
    if tag_type in {"knowledge_point", "prerequisite"}:
        if current_knowledge is None:
            return None
        term = current_knowledge.canonical_term(tag_value)
        if term is not None and current_knowledge.resolve(term[0]):
            tag_value = term[1]
            if tag_type == "knowledge_point" and term[0].startswith("sk_"):
                # 技能节点在存储层与知识点同列，对外读取单列成 skill；
                # prerequisite 保持原类型（前置技能仍是前置知识）。
                tag_type = "skill"
        else:
            teacher_name = teacher_lookup["knowledge"].get(
                _taxonomy_value_key(tag_value)
            )
            if teacher_name is None:
                return None
            tag_value = teacher_name
    dimension = {
        "knowledge_point": "knowledge",
        "method": "method",
        "thought": "thought",
        "ability": "ability",
        "model": "model",
        "special_type": "special_type",
        "exam_scope": "curriculum",
    }.get(tag_type)
    key = _taxonomy_value_key(tag_value)
    if tag_type == "method":
        thought_value = identity_lookup["thought"].get(key)
        if thought_value:
            tag_type = "thought"
            tag_value = thought_value
            dimension = "thought"
    if dimension:
        tag_value = identity_lookup[dimension].get(key, tag_value)
    return tag_type, tag_value


def _question_skill_options(
    tags: Iterable[dict[str, Any]],
    knowledge: CurrentKnowledgeResolver | None,
) -> list[dict[str, str]]:
    """题目当前技能标签 → 可关联技能 [{key, label}]，key 为规范术语 id（sk_*）。"""
    if knowledge is None:
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in tags:
        if tag.get("tag_type") != "skill":
            continue
        term = knowledge.canonical_term(tag.get("tag_value"))
        if term is None or not str(term[0]).startswith("sk_") or term[0] in seen:
            continue
        seen.add(term[0])
        options.append({"key": term[0], "label": term[1]})
    return options


def _load_criteria_needs_review_ids(
    conn: sqlite3.Connection,
    question_ids: Iterable[int],
) -> set[int]:
    clean_ids = [int(question_id) for question_id in question_ids if int(question_id) > 0]
    if not clean_ids:
        return set()
    placeholders = ", ".join("?" for _ in clean_ids)
    rows = conn.execute(
        f"""
        SELECT q.id AS question_id FROM questions q
        WHERE q.id IN ({placeholders})
          AND {_CRITERIA_NEEDS_REVIEW_SQL.format(qid="q.id")}
        """,
        clean_ids,
    ).fetchall()
    return {int(row["question_id"]) for row in rows}


def _public_question_item(
    row: sqlite3.Row,
    tags: list[dict[str, Any]],
    *,
    asset_paths: list[str] | None = None,
    revision: str,
) -> dict[str, Any]:
    question_text = str(row["question_text"] or "")
    answer_text = str(row["answer_text"] or "") if row["answer_text"] is not None else None
    if asset_paths is None:
        asset_paths = _question_asset_paths(
            row["_image_paths"],
            question_text,
            answer_text or "",
        )
    question_id = int(row["id"])
    return {
        "id": question_id,
        "revision": revision,
        "paper_id": int(row["paper_id"]) if row["paper_id"] is not None else None,
        "question_number": str(row["question_number"]),
        "question_type": row["question_type"],
        "question_text": _strip_image_markers(question_text),
        "answer_text": _strip_image_markers(answer_text) if answer_text is not None else None,
        "difficulty": row["difficulty"],
        "typicality": row["typicality"],
        "reason": (
            _strip_image_markers(str(row["reason"]))
            if row["reason"] is not None
            else None
        ),
        "needs_review": bool(row["needs_review"]),
        "criteria_needs_review": False,
        "has_images": bool(row["has_images"]) or bool(asset_paths),
        "needs_image_review": bool(row["needs_image_review"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "paper_title": row["paper_title"],
        "year": row["year"],
        "province": row["province"],
        "city": row["city"],
        "district": row["district"],
        "exam_type": row["exam_type"],
        "grade": row["grade"],
        "semester": row["semester"],
        "textbook_version": row["textbook_version"],
        "tags": tags,
        "asset_urls": [
            f"/api/question-bank/questions/{question_id}/assets/{index}"
            for index in range(len(asset_paths))
        ],
    }


def _question_asset_paths(image_paths_json: Any, *text_values: str) -> list[str]:
    try:
        parsed = json.loads(str(image_paths_json or "[]"))
    except (json.JSONDecodeError, TypeError):
        parsed = []
    candidates = [str(value).strip() for value in parsed] if isinstance(parsed, list) else []
    for text_value in text_values:
        candidates.extend(
            match.group("path").strip()
            for match in _IMAGE_MARKER_PATTERN.finditer(text_value)
        )
    return list(dict.fromkeys(value for value in candidates if value))


def _strip_image_markers(value: str) -> str:
    return _IMAGE_MARKER_PATTERN.sub("", value)


def _valid_rich_block_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for block in value:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            return False
        relationships = block.get("image_relationships", {})
        if not isinstance(relationships, dict):
            return False
        if any(
            not isinstance(key, str) or not isinstance(path, str)
            for key, path in relationships.items()
        ):
            return False
    return True


def _rich_blocks(
    payload: dict[str, Any] | None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    if payload is None:
        return [("question_blocks", []), ("answer_blocks", [])]
    result: list[tuple[str, list[dict[str, Any]]]] = []
    for key in ("question_blocks", "answer_blocks"):
        public_blocks: list[dict[str, Any]] = []
        for block in payload[key]:
            text = block["text"]
            marker_paths = _question_asset_paths(None, text)
            relationship_paths = [
                path.strip()
                for path in block.get("image_relationships", {}).values()
                if path.strip()
            ]
            public_blocks.append(
                {
                    "text": _strip_image_markers(text),
                    "_xml": str(block.get("xml") or ""),
                    "_rels": {
                        str(rel_id): path.strip()
                        for rel_id, path in block.get("image_relationships", {}).items()
                        if path.strip()
                    },
                    "_asset_paths": list(
                        dict.fromkeys([*marker_paths, *relationship_paths])
                    ),
                }
            )
        result.append((key, public_blocks))
    return result


def _public_rich_content(
    question_id: int,
    available: bool,
    rich_blocks: list[tuple[str, list[dict[str, Any]]]],
    asset_paths: list[str],
    *,
    question_number: str | None = None,
) -> dict[str, Any]:
    asset_indexes = {path: index for index, path in enumerate(asset_paths)}
    public: dict[str, Any] = {
        "available": available,
        "question_block_count": 0,
        "answer_block_count": 0,
        "question_blocks": [],
        "answer_blocks": [],
    }
    for key, blocks in rich_blocks:
        if key == "question_blocks":
            blocks = strip_question_source_score_blocks(
                [{**block, "xml": block.get("_xml") or ""} for block in blocks],
                question_number=question_number,
            )
        projected_blocks = []
        for block in blocks:
            indexes = [
                asset_indexes[path]
                for path in block["_asset_paths"]
                if path in asset_indexes
            ]
            structured = _structured_rich_text(block["text"])
            rel_urls = {
                rel_id: f"/api/question-bank/questions/{question_id}/assets/{asset_indexes[path]}"
                for rel_id, path in block["_rels"].items()
                if path in asset_indexes
            }
            preview_html = block_preview_html(
                str(block.get("xml") or block.get("_xml") or ""),
                rel_urls,
                expected_text=block["text"],
            ) or ""
            projected_blocks.append(
                {
                    "kind": structured["kind"],
                    "text": block["text"],
                    "segments": structured["segments"],
                    "rows": structured["rows"],
                    "html": preview_html,
                    "asset_indexes": indexes,
                    "asset_urls": [
                        f"/api/question-bank/questions/{question_id}/assets/{index}"
                        for index in indexes
                    ],
                }
            )
        public[key] = projected_blocks
        public[key.replace("blocks", "block_count")] = len(projected_blocks)
    return public


def _structured_rich_text(text: str) -> dict[str, Any]:
    normalized = str(text or "")
    if normalized.strip().casefold().startswith("<table"):
        rows: list[dict[str, Any]] = []
        for row_match in _RICH_TABLE_ROW_PATTERN.finditer(normalized):
            cells = [
                {"segments": _rich_inline_segments(cell_match.group(1))}
                for cell_match in _RICH_TABLE_CELL_PATTERN.finditer(
                    row_match.group(1)
                )
            ]
            if cells:
                rows.append({"cells": cells})
        if rows:
            return {
                "kind": "table",
                "segments": [],
                "rows": rows,
            }
    return {
        "kind": "paragraph",
        "segments": _rich_inline_segments(normalized),
        "rows": [],
    }


def _rich_inline_segments(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    superscript_depth = 0
    subscript_depth = 0
    underline_depth = 0

    def append_segment(value: str, *, line_break: bool = False) -> None:
        if not value and not line_break:
            return
        segment = {
            "text": value,
            "superscript": superscript_depth > 0,
            "subscript": subscript_depth > 0,
            "underline": underline_depth > 0,
            "line_break": line_break,
        }
        if (
            segments
            and not line_break
            and not segments[-1]["line_break"]
            and all(
                segments[-1][key] == segment[key]
                for key in ("superscript", "subscript", "underline")
            )
        ):
            segments[-1]["text"] += value
        else:
            segments.append(segment)

    cursor = 0
    for match in _RICH_INLINE_TOKEN_PATTERN.finditer(str(text or "")):
        append_segment(text[cursor : match.start()])
        token = match.group(0).casefold()
        if token.startswith("<br"):
            append_segment("", line_break=True)
        elif token == "<sup>":
            superscript_depth += 1
        elif token == "</sup>":
            superscript_depth = max(0, superscript_depth - 1)
        elif token == "<sub>":
            subscript_depth += 1
        elif token == "</sub>":
            subscript_depth = max(0, subscript_depth - 1)
        elif token == "<u>":
            underline_depth += 1
        elif token == "</u>":
            underline_depth = max(0, underline_depth - 1)
        cursor = match.end()
    append_segment(text[cursor:])
    return segments


def _select_current_preview_rows(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    preview_type: str | None = None,
) -> list[sqlite3.Row]:
    preview_filter = ""
    params: list[Any] = [int(question_id)]
    if preview_type is not None:
        preview_filter = "AND preview_type = ?"
        params.append(str(preview_type))
    return conn.execute(
        f"""
        WITH ranked_previews AS (
            SELECT
                id,
                preview_type,
                page_number,
                image_path,
                bbox_json,
                status,
                updated_at,
                ROW_NUMBER() OVER (
                    PARTITION BY preview_type
                    ORDER BY {_CURRENT_PREVIEW_ORDER_SQL}
                ) AS current_rank
            FROM question_previews
            WHERE question_id = ?
              AND preview_type IN ('question', 'answer')
        )
        SELECT
            preview_type,
            page_number,
            image_path AS _image_path,
            bbox_json,
            status,
            updated_at
        FROM ranked_previews
        WHERE current_rank = 1
          {preview_filter}
        ORDER BY
            CASE preview_type WHEN 'question' THEN 0 ELSE 1 END
        """,
        params,
    ).fetchall()


def _load_question_previews(
    conn: sqlite3.Connection,
    question_id: int,
) -> list[dict[str, Any]]:
    rows = _select_current_preview_rows(conn, int(question_id))
    previews: list[dict[str, Any]] = []
    for row in rows:
        preview_type = str(row["preview_type"])
        previews.append(
            {
                "preview_type": preview_type,
                "status": str(row["status"] or ""),
                "page_number": (
                    int(row["page_number"])
                    if row["page_number"] is not None
                    else None
                ),
                "bbox": _public_preview_box(row["bbox_json"]),
                "updated_at": str(row["updated_at"]),
                "url": (
                    f"/api/question-bank/questions/{question_id}/previews/{preview_type}"
                    if str(row["_image_path"] or "").strip()
                    else None
                ),
            }
        )
    return previews


def _public_preview_box(value: Any) -> dict[str, float | None] | None:
    try:
        parsed = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    box: dict[str, float | None] = {}
    for key in ("x0", "y0", "x1", "y1"):
        raw_value = parsed.get(key)
        if raw_value is None:
            box[key] = None
            continue
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            number = math.nan
        box[key] = number if math.isfinite(number) else None
    return box


def _public_tag_value(value: Any) -> str | None:
    raw_value = str(value or "").strip()
    clean_value = _strip_image_markers(raw_value).strip()
    if (
        not clean_value
        or "[[image:" in clean_value.casefold()
        or _contains_filesystem_token(clean_value)
    ):
        return None
    return clean_value


def _contains_filesystem_token(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        text
        and (
            _is_absolute_or_file_uri(text)
            or _FILE_URI_TOKEN_PATTERN.search(text)
            or _WINDOWS_PATH_TOKEN_PATTERN.search(text)
            or _UNC_PATH_TOKEN_PATTERN.search(text)
            or _POSIX_PATH_TOKEN_PATTERN.search(text)
        )
    )


def _is_absolute_or_file_uri(value: str) -> bool:
    clean_value = value.strip()
    return (
        clean_value.casefold().startswith("file:")
        or PureWindowsPath(clean_value).is_absolute()
        or PurePosixPath(clean_value).is_absolute()
    )
