from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.compat import open_grading_repositories
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.question_read_service import (
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

        question_bank_connection = stack.enter_context(
            captured_sqlite_read_connection(
                Path(paths.qb_db_path),
                required_tables=_QUESTION_BANK_REQUIRED_TABLES,
                check_same_thread=False,
            )
        )
        question_bank_candidate = _main_candidate_path(question_bank_connection)

        grading_db = open_grading_repositories(
            grading_candidate,
            external_connection=grading_connection,
        )
        diagnosis_service = DiagnosisProfileService(
            grading_candidate,
            question_bank_candidate,
            grading_db=grading_db,
            question_bank_connection=question_bank_connection,
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


__all__ = [
    "RequestReadContext",
    "RequestReadContextCleanupError",
    "request_read_context",
]
