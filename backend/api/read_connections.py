from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.performance.metrics import instrument_sqlite_connection
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.compat import open_grading_repositories
from integration.data_generation import commit_generation
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.question_read_service import (
    QuestionBankSnapshotBusy,
    QuestionBankSnapshotUnavailable,
    captured_sqlite_read_connection,
)


_GRADING_REQUIRED_TABLES = frozenset(
    {
        "exam_papers",
        "grading_sessions",
        "session_details",
        "session_results",
        "students",
    }
)
_QUESTION_BANK_REQUIRED_TABLES = frozenset(
    {
        "grading_question_links",
        "question_tags",
        "questions",
    }
)


class _ReadPaths(Protocol):
    db_path: Path
    qb_db_path: Path


class RequestReadContextCleanupError(RuntimeError):
    """Raised when owned request resources cannot be closed cleanly."""


@dataclass(frozen=True, slots=True)
class RequestReadContext:
    grading_candidate: Path
    question_bank_candidate: Path
    grading_connection: sqlite3.Connection
    question_bank_connection: sqlite3.Connection
    grading_db: GradingRepositoryAccess
    diagnosis_service: DiagnosisProfileService
    practice_service: PracticePlanService


@contextmanager
def request_read_context(paths: _ReadPaths) -> Iterator[RequestReadContext]:
    stack = ExitStack()
    primary_error: BaseException | None = None
    try:
        grading_connection = stack.enter_context(
            captured_sqlite_read_connection(
                Path(paths.db_path),
                required_tables=_GRADING_REQUIRED_TABLES,
                check_same_thread=False,
            )
        )
        grading_candidate = _main_candidate_path(grading_connection)

        # The ~180 MB question bank is opened directly read-only instead of
        # copying it per request; the small grading database keeps the
        # captured snapshot.
        question_bank_connection = stack.enter_context(
            _direct_question_bank_read(
                Path(paths.qb_db_path),
                required_tables=_QUESTION_BANK_REQUIRED_TABLES,
            )
        )
        question_bank_candidate = _main_candidate_path(question_bank_connection)

        grading_db = open_grading_repositories(
            Path(paths.db_path),
            external_connection=grading_connection,
        )
        diagnosis_service = DiagnosisProfileService(
            grading_candidate,
            question_bank_candidate,
            grading_db=grading_db,
            question_bank_connection=question_bank_connection,
            # SQL reads use the captured database; referenced images and config
            # receipts still belong to the original data directory.
            data_root=Path(paths.qb_db_path).parent.parent,
            cache_identity=(
                *_database_generation(Path(paths.db_path)),
                *_database_generation(Path(paths.qb_db_path)),
            ),
        )
        practice_service = PracticePlanService(
            question_bank_candidate,
            external_connection=question_bank_connection,
        )
        yield RequestReadContext(
            grading_candidate=grading_candidate,
            question_bank_candidate=question_bank_candidate,
            grading_connection=grading_connection,
            question_bank_connection=question_bank_connection,
            grading_db=grading_db,
            diagnosis_service=diagnosis_service,
            practice_service=practice_service,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            stack.close()
        except BaseException as cleanup_error:
            if primary_error is None:
                raise RequestReadContextCleanupError(
                    "Request read resources could not be closed"
                ) from cleanup_error


@contextmanager
def _direct_question_bank_read(
    db_path: Path,
    *,
    required_tables: frozenset[str],
) -> Iterator[sqlite3.Connection]:
    """Open the question bank directly in read-only mode.

    Mirrors ``question_read_service._open_direct_read_connection`` (mode=ro,
    query_only, one deferred transaction) plus instrumentation, a
    cross-thread connection, and required-table validation.
    """

    source = Path(db_path).resolve(strict=False)
    connection: sqlite3.Connection | None = None
    try:
        connection = instrument_sqlite_connection(
            sqlite3.connect(
                f"{source.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
                check_same_thread=False,
            )
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN DEFERRED")
        placeholders = ", ".join("?" for _ in required_tables)
        rows = connection.execute(
            f"""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name IN ({placeholders})
            """,
            tuple(sorted(required_tables)),
        ).fetchall()
        if not required_tables.issubset({str(row[0]) for row in rows}):
            raise sqlite3.DatabaseError("Question bank schema is unavailable")
        yield connection
    except (OSError, sqlite3.Error) as exc:
        text = str(exc).casefold()
        if "locked" in text or "busy" in text:
            raise QuestionBankSnapshotBusy(
                "Question bank is busy; retry shortly"
            ) from exc
        raise QuestionBankSnapshotUnavailable(
            "Question bank is unavailable"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _main_candidate_path(connection: sqlite3.Connection) -> Path:
    try:
        row = next(
            row
            for row in connection.execute("PRAGMA database_list").fetchall()
            if str(row[1]) == "main"
        )
        return Path(str(row[2]))
    except (OSError, sqlite3.DatabaseError, StopIteration) as exc:
        raise QuestionBankSnapshotUnavailable(
            "Question bank snapshot is unavailable"
        ) from exc


def _database_generation(path: Path) -> tuple[str, ...]:
    """Identity token for cache keys: resolved path, file identity, and the
    commit generation (``PRAGMA data_version`` monitor). Real commits bump
    it; read-only opens, checkpoints, and -wal mtime flaps do not."""

    source = path.resolve(strict=False)
    try:
        stat = source.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
    except OSError:
        identity = "missing"
    return (
        str(source),
        f"identity:{identity}",
        f"commits:{commit_generation(source)}",
    )


__all__ = [
    "RequestReadContext",
    "RequestReadContextCleanupError",
    "request_read_context",
]
