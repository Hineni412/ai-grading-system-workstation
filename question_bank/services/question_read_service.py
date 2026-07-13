from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, Iterator

from backend.file_access import (
    ControlledFileForbidden,
    ResolvedFile,
    resolve_controlled_file,
)
from backend.performance.metrics import instrument_sqlite_connection
from question_bank.models.question import ALLOWED_TAG_TYPES
from question_bank.services.asset_path_service import (
    AmbiguousQuestionBankAssetPathError,
    resolve_question_bank_asset_path,
)
from question_bank.services.question_service import build_question_filter_query
from question_bank.services.question_revision import question_revision, question_revisions


ANALYSIS_TAG_TYPES = (
    "knowledge_point",
    "method",
    "ability",
    "model",
    "error_type",
    "exam_scope",
)

_IMAGE_MARKER_PATTERN = re.compile(
    r"\[\[IMAGE:(?P<path>.*?)\]\]",
    re.DOTALL | re.IGNORECASE,
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
_QUESTION_SORT_CLAUSES = {
    "newest": "q.created_at DESC, q.id DESC",
    "difficulty": "CAST(q.difficulty AS REAL) DESC, q.created_at DESC, q.id DESC",
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
    "frequency_midterm",
    "frequency_final",
    "frequency_zhongkao",
    "frequency_contextual",
}
_PUBLIC_TAG_TYPES = tuple(sorted(ALLOWED_TAG_TYPES))
_RICH_CONTENT_VERSION = 3
_CURRENT_PREVIEW_ORDER_SQL = "updated_at DESC, id DESC"
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
    difficulty_min: int | None = None
    difficulty_max: int | None = None
    question_types: tuple[str, ...] = ()
    paper_ids: tuple[int, ...] = ()
    years: tuple[str, ...] = ()
    exam_types: tuple[str, ...] = ()
    grades: tuple[str, ...] = ()
    exam_scopes: tuple[str, ...] = ()
    tag_status: str = "all"
    sort: str = "newest"


@dataclass(frozen=True, slots=True)
class QuestionReadPage:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int


@contextmanager
def _read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    with captured_sqlite_read_connection(
        db_path,
        required_tables=_SNAPSHOT_REQUIRED_TABLES,
    ) as conn:
        yield conn


@contextmanager
def captured_sqlite_read_connection(
    db_path: Path,
    *,
    required_tables: frozenset[str],
) -> Iterator[sqlite3.Connection]:
    with _captured_snapshot_candidate(db_path) as candidate:
        conn = _open_snapshot_connection(candidate, required_tables=required_tables)
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
) -> sqlite3.Connection:
    conn: sqlite3.Connection | None = None
    try:
        uri = f"{candidate.resolve(strict=True).as_uri()}?mode=ro"
        conn = instrument_sqlite_connection(
            sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
            )
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN")
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


class QuestionBankReadService:
    def __init__(self, db_path: Path, *, data_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else None

    def list_papers(self) -> list[dict[str, Any]]:
        tag_placeholders = ", ".join("?" for _ in ANALYSIS_TAG_TYPES)
        with _read_connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    p.id,
                    p.title,
                    p.year,
                    p.province,
                    p.city,
                    p.district,
                    p.exam_type,
                    p.grade,
                    p.semester,
                    p.textbook_version,
                    p.import_status,
                    p.created_at,
                    p.updated_at,
                    COUNT(DISTINCT CASE
                        WHEN q.id IS NOT NULL AND COALESCE(q.is_deleted, 0) = 0
                        THEN q.id
                    END) AS question_count,
                    COUNT(DISTINCT CASE
                        WHEN q.id IS NOT NULL
                         AND COALESCE(q.is_deleted, 0) = 0
                         AND t.id IS NOT NULL
                        THEN q.id
                    END) AS tagged_question_count
                FROM papers p
                LEFT JOIN questions q ON q.paper_id = p.id
                LEFT JOIN question_tags t
                  ON t.question_id = q.id
                 AND t.tag_type IN ({tag_placeholders})
                 AND COALESCE(t.tag_value, '') <> ''
                WHERE COALESCE(p.import_status, '') <> 'deleted'
                GROUP BY p.id
                ORDER BY p.created_at DESC, p.id DESC
                """,
                ANALYSIS_TAG_TYPES,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_questions(self, filters: QuestionReadFilters) -> QuestionReadPage:
        difficulty_range = None
        if filters.difficulty_min is not None and filters.difficulty_max is not None:
            difficulty_range = (filters.difficulty_min, filters.difficulty_max)

        joins, where, params = build_question_filter_query(
            question_number=filters.question_number,
            keyword=filters.keyword,
            knowledge_point=filters.knowledge_point,
            difficulty_range=difficulty_range,
            question_types=list(filters.question_types),
            paper_ids=list(filters.paper_ids),
            years=list(filters.years),
            exam_types=list(filters.exam_types),
            grades=list(filters.grades),
            tag_filters={"exam_scope": list(filters.exam_scopes)} if filters.exam_scopes else None,
            is_deleted=False,
            tag_status=_TAG_STATUS_MAP[filters.tag_status],
        )
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
        list_sql = " ".join(
            [
                """
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
                *list_joins,
                where_sql,
                f"ORDER BY {_QUESTION_SORT_CLAUSES[filters.sort]}",
                "LIMIT ? OFFSET ?",
            ]
        )
        offset = (filters.page - 1) * filters.page_size

        with _read_connection(self.db_path) as conn:
            count_row = conn.execute(count_sql, params).fetchone()
            total = int(count_row[0] or 0)
            rows = conn.execute(
                list_sql,
                [*params, filters.page_size, offset],
            ).fetchall()
            tags_by_question = _load_page_tags(conn, [int(row["id"]) for row in rows])
            revisions = question_revisions(conn, [int(row["id"]) for row in rows])

        items = [
            _public_question_item(
                row,
                tags_by_question.get(int(row["id"]), []),
                revision=revisions[int(row["id"])],
            )
            for row in rows
        ]
        return QuestionReadPage(
            items=items,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            total_pages=max(math.ceil(total / filters.page_size), 1),
        )

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
            tags = _load_page_tags(conn, [int(question_id)]).get(
                int(question_id),
                [],
            )
            previews = _load_question_previews(conn, int(question_id))
            revision = question_revision(conn, int(question_id))

        rich_payload = self._load_rich_content(int(question_id))
        asset_paths, rich_blocks = _ordered_question_assets(row, rich_payload)

        item = _public_question_item(
            row,
            tags,
            asset_paths=asset_paths,
            revision=revision,
        )
        item["page_range"] = row["page_range"]
        item["assets"] = [
            {
                "index": index,
                "url": f"/api/question-bank/questions/{question_id}/assets/{index}",
            }
            for index in range(len(asset_paths))
        ]
        item["rich_content"] = _public_rich_content(
            int(question_id),
            rich_payload is not None,
            rich_blocks,
            asset_paths,
        )
        item["previews"] = previews
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
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if type(payload.get("version")) is not int:  # noqa: E721 - reject bool/float
            return None
        if payload["version"] != _RICH_CONTENT_VERSION:
            return None
        if type(payload.get("question_id")) is not int:  # noqa: E721 - reject bool/float
            return None
        if payload["question_id"] != int(question_id):
            return None
        if not _valid_rich_block_list(payload.get("question_blocks")):
            return None
        if not _valid_rich_block_list(payload.get("answer_blocks")):
            return None
        return payload


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


def _load_page_tags(
    conn: sqlite3.Connection,
    question_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not question_ids:
        return {}
    question_placeholders = ", ".join("?" for _ in question_ids)
    tag_type_placeholders = ", ".join("?" for _ in _PUBLIC_TAG_TYPES)
    rows = conn.execute(
        f"""
        SELECT question_id, tag_type, tag_value, confidence
        FROM question_tags
        WHERE question_id IN ({question_placeholders})
          AND tag_type IN ({tag_type_placeholders})
        ORDER BY id ASC
        """,
        [*question_ids, *_PUBLIC_TAG_TYPES],
    ).fetchall()
    tags_by_question: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        tag_value = _public_tag_value(row["tag_value"])
        if tag_value is None:
            continue
        tags_by_question.setdefault(int(row["question_id"]), []).append(
            {
                "tag_type": str(row["tag_type"]),
                "tag_value": tag_value,
                "confidence": (
                    float(row["confidence"])
                    if row["confidence"] is not None
                    else None
                ),
            }
        )
    return tags_by_question


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
        projected_blocks = []
        for block in blocks:
            indexes = [
                asset_indexes[path]
                for path in block["_asset_paths"]
                if path in asset_indexes
            ]
            projected_blocks.append(
                {
                    "text": block["text"],
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
