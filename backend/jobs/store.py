from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.schema_migrations import ensure_schema_current


JOB_STATUSES = ("queued", "running", "paused", "succeeded", "failed", "cancelled")
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_SCAN_BATCH_ID = re.compile(r"^[0-9a-f]{32}$")
_QUESTION_BANK_SYNC_STATES = {
    "not_started",
    "running",
    "ready",
    "partial",
    "failed",
}


class ConfigRetryAlreadySubmittedError(RuntimeError):
    pass


class ConfigRequestTokenConflictError(RuntimeError):
    pass


class ConfigSessionBusyError(RuntimeError):
    pass


class QuestionBankSyncRequestTokenConflictError(RuntimeError):
    pass


class QuestionBankSyncSessionBusyError(RuntimeError):
    pass


class TaxonomySuggestionJobRequestConflictError(RuntimeError):
    pass


class TaxonomySuggestionJobBusyError(RuntimeError):
    pass


class GradingSessionBusyError(RuntimeError):
    pass


class GradingSubmissionChangedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    job_type: str
    payload: dict[str, Any]
    result: dict[str, Any]
    status: str
    progress: float
    stage: str
    detail: str
    error: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    updated_at: str
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class _InterruptedQuestionBankSync:
    session_id: int
    job_id: int
    source_sha256: str
    config_revision: str
    current_details_json: str


class JobStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_schema_current("grading", self.db_path)

    def create_job(self, job_type: str, payload: dict[str, Any] | None) -> JobRecord:
        clean_type = str(job_type or "").strip()
        if not clean_type:
            raise ValueError("job_type must be nonblank")
        payload_json = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (job_type, payload_json, status)
                VALUES (?, ?, 'queued')
                """,
                (clean_type, payload_json),
            )
            job_id = int(cursor.lastrowid)
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded

    def create_idempotent_taxonomy_suggestion_job(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        clean_payload = dict(payload)
        run_id = str(clean_payload.get("run_id") or "").strip().casefold()
        if _SOURCE_ID.fullmatch(run_id) is None:
            raise ValueError("run_id must contain 32 hexadecimal characters")
        operation = str(clean_payload.get("operation") or "").strip()
        if operation not in {"process", "retry"}:
            raise ValueError("taxonomy suggestion operation is invalid")
        token = _clean_request_token(
            clean_payload.get("client_request_token")
        )
        clean_payload["run_id"] = run_id
        clean_payload["operation"] = operation
        clean_payload["client_request_token"] = token
        payload_json = json.dumps(
            clean_payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM jobs
                    WHERE job_type = 'taxonomy_suggestion'
                    ORDER BY id DESC
                    """
                ).fetchall()
                for row in rows:
                    try:
                        existing_payload = json.loads(
                            str(row["payload_json"] or "{}")
                        )
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(existing_payload, dict):
                        continue
                    if (
                        existing_payload.get("client_request_token")
                        == token
                    ):
                        if (
                            existing_payload.get("run_id") != run_id
                            or existing_payload.get("operation") != operation
                        ):
                            raise TaxonomySuggestionJobRequestConflictError(
                                "taxonomy suggestion request token was reused"
                            )
                        conn.commit()
                        return _job_record(row), False
                    if (
                        existing_payload.get("run_id") == run_id
                        and str(row["status"])
                        in {"queued", "running", "paused"}
                    ):
                        if existing_payload.get("operation") == operation:
                            conn.commit()
                            return _job_record(row), False
                        raise TaxonomySuggestionJobBusyError(
                            "taxonomy suggestion run already has active work"
                        )
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (job_type, payload_json, status)
                    VALUES ('taxonomy_suggestion', ?, 'queued')
                    """,
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(
                f"created taxonomy suggestion job {job_id} could not be loaded"
            )
        return loaded, True

    def create_idempotent_scan_grading_start(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        clean_payload = dict(payload)
        session_id = _positive_int(clean_payload.get("session_id"))
        batch_id = str(clean_payload.get("scan_batch_id") or "").strip().lower()
        if not _SCAN_BATCH_ID.fullmatch(batch_id):
            raise ValueError("scan_batch_id must be 32 lowercase hex characters")
        config_revision = str(
            clean_payload.get("config_revision") or ""
        ).strip().casefold()
        if not _SHA256.fullmatch(config_revision):
            raise ValueError("config_revision must be sha256")
        expected_rubric_path = _nonblank_text(
            clean_payload.get("expected_rubric_path"),
            "expected_rubric_path",
        )
        expected_answer_key_path = _nonblank_text(
            clean_payload.get("expected_answer_key_path"),
            "expected_answer_key_path",
        )
        expected_template_id = _positive_int(
            clean_payload.get("expected_template_id")
        )
        expected_front_template_path = _nonblank_text(
            clean_payload.get("expected_front_template_path"),
            "expected_front_template_path",
        )
        expected_back_template_path = _nonblank_text(
            clean_payload.get("expected_back_template_path"),
            "expected_back_template_path",
        )
        payload_json = json.dumps(clean_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                binding = conn.execute(
                    """
                    SELECT s.rubric_path, s.answer_key_path,
                           t.id AS template_id,
                           t.front_template_path, t.back_template_path,
                           t.is_confirmed, t.regions_snapshot_pending
                    FROM grading_sessions AS s
                    LEFT JOIN session_templates AS t ON t.session_id = s.id
                    WHERE s.id = ? AND s.is_deleted = 0
                    """,
                    (session_id,),
                ).fetchone()
                if (
                    binding is None
                    or str(binding["rubric_path"] or "") != expected_rubric_path
                    or str(binding["answer_key_path"] or "")
                    != expected_answer_key_path
                    or _positive_int_or_zero(binding["template_id"])
                    != expected_template_id
                    or str(binding["front_template_path"] or "")
                    != expected_front_template_path
                    or str(binding["back_template_path"] or "")
                    != expected_back_template_path
                    or int(binding["is_confirmed"] or 0) != 1
                    or int(binding["regions_snapshot_pending"] or 0) != 0
                ):
                    raise GradingSubmissionChangedError(
                        "grading configuration or template changed before job creation"
                    )
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE job_type = 'grading_run' "
                    "AND status IN ('queued','running','paused','succeeded') "
                    "AND json_valid(payload_json) = 1 "
                    "AND json_extract(payload_json, '$.session_id') = ? "
                    "AND json_extract(payload_json, '$.scan_batch_id') = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (session_id, batch_id),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return _job_record(existing), False
                active = conn.execute(
                    "SELECT id FROM jobs WHERE job_type = 'grading_run' "
                    "AND status IN ('queued','running','paused') "
                    "AND json_valid(payload_json) = 1 "
                    "AND json_extract(payload_json, '$.session_id') = ? "
                    "LIMIT 1",
                    (session_id,),
                ).fetchone()
                if active is not None:
                    raise GradingSessionBusyError(
                        f"grading work is already active for session {session_id}"
                    )
                if self._find_active_config_session_row(
                    conn,
                    session_id=session_id,
                ) is not None:
                    raise GradingSessionBusyError(
                        f"configuration work is already active for session {session_id}"
                    )
                cursor = conn.execute(
                    "INSERT INTO jobs (job_type, payload_json, status) "
                    "VALUES ('grading_run', ?, 'queued')",
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded, True

    def create_claimed_config_job(self, payload: dict[str, Any]) -> JobRecord:
        clean_payload = dict(payload)
        session_id = _positive_int(clean_payload.get("session_id"))
        payload_json = json.dumps(clean_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._find_active_config_session_row(
                    conn,
                    session_id=session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {session_id}"
                    )
                cursor = conn.execute(
                    "INSERT INTO jobs (job_type, payload_json, status) "
                    "VALUES ('config_generation', ?, 'queued')",
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded

    def create_idempotent_config_job(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        clean_payload = dict(payload)
        token = _clean_request_token(clean_payload.get("client_request_token"))
        fingerprint = _clean_request_fingerprint(
            clean_payload.get("client_request_fingerprint")
        )
        session_id = _positive_int(clean_payload.get("session_id"))
        mode = str(clean_payload.get("mode") or "").strip()
        payload_json = json.dumps(clean_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_config_request_row(
                    conn,
                    session_id=session_id,
                    token=token,
                )
                if existing is not None:
                    record = _job_record(existing)
                    if record.payload.get("abandoned") is True:
                        raise ConfigRequestTokenConflictError(
                            "config request token was abandoned"
                        )
                    if (
                        record.payload.get("mode") != mode
                        or record.payload.get("client_request_fingerprint") != fingerprint
                    ):
                        raise ConfigRequestTokenConflictError(
                            "config request token was reused for another request"
                        )
                    conn.commit()
                    return record, False
                if self._find_active_config_session_row(
                    conn,
                    session_id=session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {session_id}"
                    )
                cursor = conn.execute(
                    "INSERT INTO jobs (job_type, payload_json, status) "
                    "VALUES ('config_generation', ?, 'queued')",
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded, True

    def create_idempotent_question_bank_sync_job(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        """Create one version-bound question-bank sync for a session.

        A request token may be replayed only for the exact same source/config
        version.  A different token cannot start while an earlier sync for the
        same session is active.
        """

        clean_payload = dict(payload)
        token = _clean_request_token(clean_payload.get("client_request_token"))
        fingerprint = _clean_request_fingerprint(
            clean_payload.get("client_request_fingerprint")
        )
        session_id = _positive_int(clean_payload.get("session_id"))
        mode = str(clean_payload.get("mode") or "").strip()
        if mode not in {"sync", "sync_retry", "tag_retry"}:
            raise ValueError("unsupported question-bank sync mode")
        source_sha256 = str(
            clean_payload.get("source_paper_sha256") or ""
        ).strip().casefold()
        config_revision = str(clean_payload.get("config_revision") or "").strip()
        if not _SHA256.fullmatch(source_sha256):
            raise ValueError("source_paper_sha256 must be sha256")
        if not _SHA256.fullmatch(config_revision):
            raise ValueError("config_revision must be sha256")

        payload_json = json.dumps(clean_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_question_bank_sync_request_row(
                    conn,
                    session_id=session_id,
                    token=token,
                )
                if existing is not None:
                    record = _job_record(existing)
                    if (
                        record.payload.get("mode") != mode
                        or record.payload.get("client_request_fingerprint")
                        != fingerprint
                    ):
                        raise QuestionBankSyncRequestTokenConflictError(
                            "question-bank sync token was reused for another request"
                        )
                    conn.commit()
                    return record, False
                existing_version = (
                    self._find_question_bank_sync_fingerprint_row(
                        conn,
                        session_id=session_id,
                        fingerprint=fingerprint,
                    )
                )
                if existing_version is not None:
                    conn.commit()
                    return _job_record(existing_version), False
                if self._find_active_question_bank_sync_session_row(
                    conn,
                    session_id=session_id,
                ) is not None:
                    raise QuestionBankSyncSessionBusyError(
                        f"question-bank sync is already active for session {session_id}"
                    )
                cursor = conn.execute(
                    "INSERT INTO jobs (job_type, payload_json, status) "
                    "VALUES ('question_bank_sync', ?, 'queued')",
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded, True

    @staticmethod
    def _find_question_bank_sync_request_row(
        conn: sqlite3.Connection,
        *,
        session_id: int,
        token: str,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE job_type = 'question_bank_sync' "
            "ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if (
                _positive_int_or_zero(payload.get("session_id")) == session_id
                and payload.get("client_request_token") == token
            ):
                return row
        return None

    @staticmethod
    def _find_question_bank_sync_fingerprint_row(
        conn: sqlite3.Connection,
        *,
        session_id: int,
        fingerprint: str,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE job_type = 'question_bank_sync' "
            "AND status != 'cancelled' ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if (
                _positive_int_or_zero(payload.get("session_id")) == session_id
                and payload.get("client_request_fingerprint") == fingerprint
            ):
                return row
        return None

    @staticmethod
    def _find_active_question_bank_sync_session_row(
        conn: sqlite3.Connection,
        *,
        session_id: int,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE job_type = 'question_bank_sync' "
            "AND status IN ('queued','running','paused') ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if _positive_int_or_zero(payload.get("session_id")) == session_id:
                return row
        return None

    def transition_question_bank_sync_state_if_owned(
        self,
        *,
        session_id: int,
        job_id: int,
        source_paper_sha256: str,
        config_revision: str,
        state: str,
        details: dict[str, object],
        error: str | None = None,
    ) -> bool:
        """Finish only the sync state still owned by this exact versioned job."""

        clean_session_id = _positive_int(session_id)
        clean_job_id = _positive_int(job_id)
        clean_source_sha256 = str(source_paper_sha256 or "").strip().casefold()
        clean_config_revision = str(config_revision or "").strip().casefold()
        clean_state = str(state or "").strip().casefold()
        if not _SHA256.fullmatch(clean_source_sha256):
            raise ValueError("source_paper_sha256 must be sha256")
        if not _SHA256.fullmatch(clean_config_revision):
            raise ValueError("config_revision must be sha256")
        if clean_state not in _QUESTION_BANK_SYNC_STATES - {"running"}:
            raise ValueError("unsupported terminal question-bank sync state")
        terminal_details = {
            **dict(details),
            "job_id": clean_job_id,
            "source_paper_sha256": clean_source_sha256,
            "config_revision": clean_config_revision,
        }
        details_json = json.dumps(
            terminal_details,
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    """
                    SELECT question_bank_sync_state, question_bank_sync_details_json
                    FROM grading_sessions
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (clean_session_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(
                        f"grading session not found: {clean_session_id}"
                    )
                try:
                    owner = json.loads(
                        str(current["question_bank_sync_details_json"] or "{}")
                    )
                except json.JSONDecodeError:
                    owner = {}
                if (
                    str(current["question_bank_sync_state"]) != "running"
                    or not isinstance(owner, dict)
                    or _positive_int_or_zero(owner.get("job_id")) != clean_job_id
                    or str(owner.get("source_paper_sha256") or "").casefold()
                    != clean_source_sha256
                    or str(owner.get("config_revision") or "").casefold()
                    != clean_config_revision
                ):
                    conn.rollback()
                    return False
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET question_bank_sync_state = ?,
                        question_bank_sync_details_json = ?,
                        question_bank_sync_error = ?,
                        question_bank_sync_updated_at = datetime('now','localtime'),
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (
                        clean_state,
                        details_json,
                        str(error).strip() if error else None,
                        clean_session_id,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except BaseException:
                conn.rollback()
                raise

    def claim_question_bank_sync_state_if_current(
        self,
        *,
        session_id: int,
        job_id: int,
        source_paper_sha256: str,
        config_revision: str,
        expected_rubric_path: str,
        expected_answer_key_path: str,
        details: dict[str, object],
    ) -> bool:
        """Claim running state only while the job and config binding are current."""

        clean_session_id = _positive_int(session_id)
        clean_job_id = _positive_int(job_id)
        clean_source_sha256 = str(source_paper_sha256 or "").strip().casefold()
        clean_config_revision = str(config_revision or "").strip().casefold()
        if not _SHA256.fullmatch(clean_source_sha256):
            raise ValueError("source_paper_sha256 must be sha256")
        if not _SHA256.fullmatch(clean_config_revision):
            raise ValueError("config_revision must be sha256")
        clean_rubric_path = _nonblank_text(
            expected_rubric_path,
            "expected_rubric_path",
        )
        clean_answer_path = _nonblank_text(
            expected_answer_key_path,
            "expected_answer_key_path",
        )
        running_details = {
            **dict(details),
            "job_id": clean_job_id,
            "source_paper_sha256": clean_source_sha256,
            "config_revision": clean_config_revision,
        }
        details_json = json.dumps(
            running_details,
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                job = conn.execute(
                    """
                    SELECT payload_json, status
                    FROM jobs
                    WHERE id = ? AND job_type = 'question_bank_sync'
                    """,
                    (clean_job_id,),
                ).fetchone()
                if job is None or str(job["status"]) != "running":
                    conn.rollback()
                    return False
                try:
                    job_payload = json.loads(str(job["payload_json"] or "{}"))
                except json.JSONDecodeError:
                    job_payload = {}
                if (
                    not isinstance(job_payload, dict)
                    or _positive_int_or_zero(job_payload.get("session_id"))
                    != clean_session_id
                    or str(
                        job_payload.get("source_paper_sha256") or ""
                    ).casefold()
                    != clean_source_sha256
                    or str(job_payload.get("config_revision") or "").casefold()
                    != clean_config_revision
                ):
                    conn.rollback()
                    return False
                session = conn.execute(
                    """
                    SELECT rubric_path, answer_key_path, source_paper_sha256,
                           question_bank_sync_state,
                           question_bank_sync_details_json
                    FROM grading_sessions
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (clean_session_id,),
                ).fetchone()
                if (
                    session is None
                    or str(session["rubric_path"] or "") != clean_rubric_path
                    or str(session["answer_key_path"] or "") != clean_answer_path
                    or str(
                        session["source_paper_sha256"] or ""
                    ).casefold()
                    != clean_source_sha256
                ):
                    conn.rollback()
                    return False
                if str(session["question_bank_sync_state"]) == "running":
                    try:
                        owner = json.loads(
                            str(
                                session["question_bank_sync_details_json"]
                                or "{}"
                            )
                        )
                    except json.JSONDecodeError:
                        owner = {}
                    if (
                        not isinstance(owner, dict)
                        or _positive_int_or_zero(owner.get("job_id"))
                        != clean_job_id
                        or str(
                            owner.get("source_paper_sha256") or ""
                        ).casefold()
                        != clean_source_sha256
                        or str(owner.get("config_revision") or "").casefold()
                        != clean_config_revision
                    ):
                        conn.rollback()
                        return False
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET question_bank_sync_state = 'running',
                        question_bank_sync_details_json = ?,
                        question_bank_sync_error = NULL,
                        question_bank_sync_updated_at = datetime('now','localtime'),
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (details_json, clean_session_id),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except BaseException:
                conn.rollback()
                raise

    def abandon_config_request(self, *, session_id: int, request_token: str) -> None:
        """Atomically tombstone an unseen request token.

        The cancelled row is deliberately inert and is never submitted to an executor.
        """
        clean_session_id = _positive_int(session_id)
        clean_token = _clean_request_token(request_token)
        payload = {
            "session_id": clean_session_id,
            "mode": "abandoned",
            "client_request_token": clean_token,
            "abandoned": True,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_config_request_row(
                    conn, session_id=clean_session_id, token=clean_token
                )
                if existing is not None:
                    record = _job_record(existing)
                    if record.payload.get("abandoned") is True:
                        conn.commit()
                        return
                    raise ConfigRequestTokenConflictError(
                        "config request token already has a job"
                    )
                conn.execute(
                    "INSERT INTO jobs "
                    "(job_type, payload_json, status, stage, detail, finished_at) "
                    "VALUES ('config_generation', ?, 'cancelled', 'cancelled', '', "
                    "datetime('now','localtime'))",
                    (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def find_config_job_by_request_token(
        self,
        *,
        session_id: int,
        request_token: str,
    ) -> JobRecord | None:
        clean_session_id = _positive_int(session_id)
        clean_token = _clean_request_token(request_token)
        with self._connect() as conn:
            row = self._find_config_request_row(
                conn,
                session_id=clean_session_id,
                token=clean_token,
            )
        return _job_record(row) if row is not None else None

    @staticmethod
    def _find_config_request_row(
        conn: sqlite3.Connection,
        *,
        session_id: int,
        token: str,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE job_type = 'config_generation' ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if (
                _positive_int_or_zero(payload.get("session_id")) == session_id
                and payload.get("client_request_token") == token
            ):
                return row
        return None

    @staticmethod
    def _find_active_config_session_row(
        conn: sqlite3.Connection,
        *,
        session_id: int,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE job_type = 'config_generation' "
            "AND status IN ('queued','running','paused') ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if _positive_int_or_zero(payload.get("session_id")) == session_id:
                return row
        return None

    @staticmethod
    def _has_active_grading_session(
        conn: sqlite3.Connection,
        *,
        session_id: int,
    ) -> bool:
        run = conn.execute(
            "SELECT 1 FROM grading_runs "
            "WHERE session_id = ? "
            "AND state IN ('running','pause_requested','paused') LIMIT 1",
            (int(session_id),),
        ).fetchone()
        if run is not None:
            return True
        rows = conn.execute(
            "SELECT payload_json FROM jobs WHERE job_type = 'grading_run' "
            "AND status IN ('queued','running','paused')"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if (
                isinstance(payload, dict)
                and _positive_int_or_zero(payload.get("session_id")) == int(session_id)
            ):
                return True
        return False

    def has_active_job_types(self, job_types: set[str]) -> bool:
        clean_types = sorted({str(item).strip() for item in job_types if str(item).strip()})
        if not clean_types:
            return False
        placeholders = ",".join("?" for _ in clean_types)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM jobs WHERE status IN ('queued','running','paused') "
                f"AND job_type IN ({placeholders}) LIMIT 1",
                clean_types,
            ).fetchone()
        return row is not None

    def create_config_retry_job(self, payload: dict[str, Any]) -> JobRecord:
        clean_payload = dict(payload)
        clean_payload.setdefault("client_request_token", secrets.token_hex(16))
        clean_payload.setdefault(
            "client_request_fingerprint",
            hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )
        job, _created = self.create_idempotent_config_retry_job(clean_payload)
        return job

    def create_idempotent_config_retry_job(
        self,
        payload: dict[str, Any],
    ) -> tuple[JobRecord, bool]:
        source_job_id = int(payload.get("source_job_id") or 0)
        if source_job_id <= 0:
            raise ValueError("source_job_id must be a positive integer")
        clean_payload = dict(payload)
        token = _clean_request_token(clean_payload.get("client_request_token"))
        fingerprint = _clean_request_fingerprint(
            clean_payload.get("client_request_fingerprint")
        )
        session_id = _positive_int(clean_payload.get("session_id"))
        payload_json = json.dumps(clean_payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_request = self._find_config_request_row(
                    conn,
                    session_id=session_id,
                    token=token,
                )
                if existing_request is not None:
                    record = _job_record(existing_request)
                    if record.payload.get("abandoned") is True:
                        raise ConfigRequestTokenConflictError(
                            "config request token was abandoned"
                        )
                    if (
                        record.payload.get("mode") != "retry"
                        or record.payload.get("client_request_fingerprint") != fingerprint
                    ):
                        raise ConfigRequestTokenConflictError(
                            "config request token was reused for another request"
                        )
                    conn.commit()
                    return record, False
                rows = conn.execute(
                    "SELECT status, payload_json FROM jobs WHERE job_type = 'config_generation'"
                ).fetchall()
                for row in rows:
                    try:
                        existing_payload = json.loads(str(row["payload_json"] or "{}"))
                    except json.JSONDecodeError:
                        continue
                    try:
                        existing_source_id = int(
                            existing_payload.get("source_job_id") or 0
                        ) if isinstance(existing_payload, dict) else 0
                    except (TypeError, ValueError):
                        existing_source_id = 0
                    if (
                        isinstance(existing_payload, dict)
                        and existing_source_id == source_job_id
                        and str(row["status"]) not in {"failed", "cancelled"}
                    ):
                        raise ConfigRetryAlreadySubmittedError(
                            f"retry already submitted for config generation job {source_job_id}"
                        )
                if self._find_active_config_session_row(
                    conn,
                    session_id=session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {session_id}"
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (job_type, payload_json, status)
                    VALUES ('config_generation', ?, 'queued')
                    """,
                    (payload_json,),
                )
                job_id = int(cursor.lastrowid)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        loaded = self.get_job(job_id)
        if loaded is None:
            raise RuntimeError(f"created job {job_id} could not be loaded")
        return loaded, True

    def get_job(self, job_id: int) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return _job_record(row) if row is not None else None

    def list_jobs(
        self,
        *,
        session_id: int | None = None,
        job_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        """Filter valid JSON payload session_id and return id-desc pagination."""
        clean_job_types = tuple(
            dict.fromkeys(
                str(item).strip() for item in job_types if str(item).strip()
            )
        )
        clean_statuses = tuple(
            dict.fromkeys(
                str(item).strip() for item in statuses if str(item).strip()
            )
        )
        invalid_statuses = tuple(
            status for status in clean_statuses if status not in JOB_STATUSES
        )
        if invalid_statuses:
            raise ValueError("unsupported job status filter")

        conditions: list[str] = []
        values: list[object] = []
        if session_id is not None:
            conditions.append(
                "CASE "
                "WHEN json_valid(payload_json) = 0 THEN 0 "
                "WHEN COALESCE(json_type(payload_json, '$.session_id'), '') "
                "!= 'integer' THEN 0 "
                "ELSE json_extract(payload_json, '$.session_id') > 0 "
                "AND json_extract(payload_json, '$.session_id') = ? "
                "END"
            )
            values.append(int(session_id))
        if clean_job_types:
            placeholders = ",".join("?" for _ in clean_job_types)
            conditions.append(f"job_type IN ({placeholders})")
            values.extend(clean_job_types)
        if clean_statuses:
            placeholders = ",".join("?" for _ in clean_statuses)
            conditions.append(f"status IN ({placeholders})")
            values.extend(clean_statuses)

        where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM jobs{where_sql}",
                    values,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"SELECT * FROM jobs{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*values, int(limit), int(offset)],
            ).fetchall()
        return [_job_record(row) for row in rows], total

    def find_latest_config_generation_job(
        self,
        *,
        session_id: int,
        source_id: str,
        source_revision: str,
        generation_mode: str,
    ) -> JobRecord | None:
        clean_session_id = int(session_id)
        clean_source_id = str(source_id or "").strip()
        clean_revision = str(source_revision or "").strip()
        clean_mode = str(generation_mode or "").strip()
        if (
            clean_session_id <= 0
            or not _SOURCE_ID.fullmatch(clean_source_id)
            or not _SHA256.fullmatch(clean_revision)
            or clean_mode not in {"batched", "per_question", "whole_document"}
        ):
            return None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE job_type = 'config_generation' "
                "ORDER BY id DESC"
            ).fetchall()
        for row in rows:
            record = _job_record(row)
            payload = record.payload
            try:
                payload_session_id = int(payload.get("session_id") or 0)
            except (TypeError, ValueError):
                continue
            if (
                payload_session_id == clean_session_id
                and payload.get("mode") == "generate"
                and payload.get("source_id") == clean_source_id
                and payload.get("source_revision") == clean_revision
                and payload.get("generation_mode") == clean_mode
            ):
                return record
        return None

    def mark_running(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    stage = CASE WHEN stage = 'queued' THEN 'running' ELSE stage END,
                    started_at = COALESCE(started_at, datetime('now','localtime')),
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def update_progress(
        self,
        job_id: int,
        *,
        progress: float,
        stage: str,
        detail: str = "",
    ) -> None:
        bounded_progress = min(1.0, max(0.0, float(progress)))
        clean_stage = str(stage or "").strip() or "running"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET progress = ?,
                    stage = ?,
                    detail = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND status NOT IN ('succeeded','failed','cancelled')
                """,
                (bounded_progress, clean_stage, str(detail or ""), int(job_id)),
            )

    def finish(
        self,
        job_id: int,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        clean_status = str(status or "").strip()
        if clean_status not in TERMINAL_STATUSES:
            raise ValueError(f"unsupported terminal job status: {status}")
        progress = 1.0 if clean_status == "succeeded" else None
        result_json = json.dumps(dict(result or {}), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    progress = COALESCE(?, progress),
                    result_json = ?,
                    error = ?,
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE id = ?
                  AND status NOT IN ('succeeded','failed','cancelled')
                """,
                (clean_status, progress, result_json, error, int(job_id)),
            )

    def request_cancel(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET cancel_requested = CASE
                        WHEN status IN ('queued','running','paused') THEN 1
                        ELSE cancel_requested
                    END,
                    status = CASE
                        WHEN status IN ('queued','paused') THEN 'cancelled'
                        ELSE status
                    END,
                    updated_at = CASE
                        WHEN status IN ('queued','running','paused')
                            THEN datetime('now','localtime')
                        ELSE updated_at
                    END,
                    finished_at = CASE
                        WHEN status IN ('queued','paused')
                            THEN COALESCE(finished_at, datetime('now','localtime'))
                        ELSE finished_at
                    END
                WHERE id = ?
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def confirm_cancelled(self, job_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE id = ?
                  AND status = 'running'
                  AND cancel_requested = 1
                """,
                (int(job_id),),
            )
            return cursor.rowcount == 1

    def is_cancel_requested(self, job_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return bool(row and int(row["cancel_requested"]))

    def interrupted_question_bank_sync_owners(self) -> list[tuple[int, int]]:
        """Return (session_id, job_id) pairs that require cross-database cleanup."""

        with self._connect() as conn:
            interrupted = self._interrupted_question_bank_syncs(conn)
        return [(item.session_id, item.job_id) for item in interrupted]

    def fail_interrupted_jobs(self) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            interrupted_syncs = self._interrupted_question_bank_syncs(conn)
            recovered = conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', progress = 1.0,
                    stage = 'config_generation', detail = 'complete', error = NULL,
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE job_type = 'config_generation'
                  AND status = 'running'
                  AND stage = 'config_mapping'
                """
            )
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = COALESCE(error, 'interrupted by process restart'),
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE status IN ('queued','running')
                """
            )
            for interrupted in interrupted_syncs:
                failed_details = json.dumps(
                    {
                        "job_id": interrupted.job_id,
                        "source_paper_sha256": interrupted.source_sha256,
                        "config_revision": interrupted.config_revision,
                        "stage": "interrupted",
                        "reason": "process_restart",
                        "retryable": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                conn.execute(
                    """
                    UPDATE grading_sessions
                    SET question_bank_sync_state = 'failed',
                        question_bank_sync_details_json = ?,
                        question_bank_sync_error =
                            'interrupted by process restart',
                        question_bank_sync_updated_at =
                            datetime('now','localtime'),
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND question_bank_sync_state = 'running'
                      AND question_bank_sync_details_json = ?
                    """,
                    (
                        failed_details,
                        interrupted.session_id,
                        interrupted.current_details_json,
                    ),
                )
            conn.commit()
            return int(recovered.rowcount) + int(cursor.rowcount)

    def _interrupted_question_bank_syncs(
        self,
        conn: sqlite3.Connection,
    ) -> list[_InterruptedQuestionBankSync]:
        interrupted: list[_InterruptedQuestionBankSync] = []
        sessions = conn.execute(
            """
            SELECT id, question_bank_sync_details_json
            FROM grading_sessions
            WHERE is_deleted = 0
              AND question_bank_sync_state = 'running'
            ORDER BY id
            """
        ).fetchall()
        for session in sessions:
            current_details_json = str(
                session["question_bank_sync_details_json"] or "{}"
            )
            try:
                owner = json.loads(current_details_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(owner, dict):
                continue
            job_id = _positive_int_or_zero(owner.get("job_id"))
            source_sha256 = str(
                owner.get("source_paper_sha256") or ""
            ).strip().casefold()
            config_revision = str(
                owner.get("config_revision") or ""
            ).strip().casefold()
            if (
                job_id <= 0
                or not _SHA256.fullmatch(source_sha256)
                or not _SHA256.fullmatch(config_revision)
            ):
                continue
            job = conn.execute(
                """
                SELECT job_type, payload_json
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None or str(job["job_type"]) != "question_bank_sync":
                continue
            try:
                payload = json.loads(str(job["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            session_id = int(session["id"])
            if (
                not isinstance(payload, dict)
                or _positive_int_or_zero(payload.get("session_id")) != session_id
                or str(
                    payload.get("source_paper_sha256") or ""
                ).strip().casefold()
                != source_sha256
                or str(
                    payload.get("config_revision") or ""
                ).strip().casefold()
                != config_revision
            ):
                continue
            interrupted.append(
                _InterruptedQuestionBankSync(
                    session_id=session_id,
                    job_id=job_id,
                    source_sha256=source_sha256,
                    config_revision=config_revision,
                    current_details_json=current_details_json,
                )
            )
        return interrupted

    def assert_config_session_idle(self, session_id: int) -> None:
        clean_session_id = _positive_int(session_id)
        with self._connect() as conn:
            if self._find_active_config_session_row(
                conn,
                session_id=clean_session_id,
            ) is not None:
                raise ConfigSessionBusyError(
                    f"configuration work is already active for session {clean_session_id}"
                )
            if self._has_active_grading_session(
                conn,
                session_id=clean_session_id,
            ):
                raise ConfigSessionBusyError(
                    f"grading work is already active for session {clean_session_id}"
                )

    @contextmanager
    def config_session_mutation_guard(self, session_id: int) -> Iterator[None]:
        clean_session_id = _positive_int(session_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._find_active_config_session_row(
                    conn,
                    session_id=clean_session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {clean_session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=clean_session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {clean_session_id}"
                    )
                yield
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def update_session_config_if_idle(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        expected_rubric_path: str | None = None,
        expected_answer_key_path: str | None = None,
        template_config_path: str | None = None,
    ) -> bool:
        clean_session_id = _positive_int(session_id)
        if (expected_rubric_path is None) != (expected_answer_key_path is None):
            raise ValueError("expected config paths must be provided together")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    """
                    SELECT rubric_path, answer_key_path
                    FROM grading_sessions
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (clean_session_id,),
                ).fetchone()
                if current is None:
                    conn.rollback()
                    return False
                config_changed = (
                    str(current["rubric_path"] or "") != str(rubric_path)
                    or str(current["answer_key_path"] or "")
                    != str(answer_key_path)
                )
                if self._find_active_config_session_row(
                    conn,
                    session_id=clean_session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {clean_session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=clean_session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {clean_session_id}"
                    )
                if expected_rubric_path is None:
                    cursor = conn.execute(
                        """
                        UPDATE grading_sessions
                        SET rubric_path = ?, answer_key_path = ?,
                            template_config_path = COALESCE(?, template_config_path),
                            question_bank_sync_state =
                                CASE WHEN ? THEN 'not_started'
                                     ELSE question_bank_sync_state END,
                            question_bank_sync_details_json =
                                CASE WHEN ? THEN '{}'
                                     ELSE question_bank_sync_details_json END,
                            question_bank_sync_error =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_error END,
                            question_bank_sync_updated_at =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_updated_at END,
                            updated_at = datetime('now','localtime')
                        WHERE id = ? AND is_deleted = 0
                        """,
                        (
                            str(rubric_path), str(answer_key_path),
                            template_config_path,
                            config_changed,
                            config_changed,
                            config_changed,
                            config_changed,
                            clean_session_id,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE grading_sessions
                        SET rubric_path = ?, answer_key_path = ?,
                            question_bank_sync_state =
                                CASE WHEN ? THEN 'not_started'
                                     ELSE question_bank_sync_state END,
                            question_bank_sync_details_json =
                                CASE WHEN ? THEN '{}'
                                     ELSE question_bank_sync_details_json END,
                            question_bank_sync_error =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_error END,
                            question_bank_sync_updated_at =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_updated_at END,
                            updated_at = datetime('now','localtime')
                        WHERE id = ? AND is_deleted = 0
                          AND rubric_path = ? AND answer_key_path = ?
                        """,
                        (
                            str(rubric_path),
                            str(answer_key_path),
                            config_changed,
                            config_changed,
                            config_changed,
                            config_changed,
                            clean_session_id,
                            str(expected_rubric_path), str(expected_answer_key_path),
                        ),
                    )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    UPDATE session_templates
                    SET is_confirmed = 0,
                        regions_snapshot_pending = 0,
                        regions_snapshot_token = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE session_id = ?
                    """,
                    (clean_session_id,),
                )
                conn.commit()
                return True
            except BaseException:
                conn.rollback()
                raise

    def update_session_config_with_source_if_idle(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        source_paper_path: str,
        source_paper_sha256: str,
        expected_rubric_path: str,
        expected_answer_key_path: str,
    ) -> bool:
        """Atomically bind legacy config/source only while grading is idle."""

        clean_session_id = _positive_int(session_id)
        clean_source_path = _nonblank_text(
            source_paper_path,
            "source_paper_path",
        )
        clean_source_sha256 = str(source_paper_sha256 or "").strip().lower()
        if not _SHA256.fullmatch(clean_source_sha256):
            raise ValueError("source_paper_sha256 must be sha256")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    """
                    SELECT rubric_path, answer_key_path, source_paper_sha256
                    FROM grading_sessions
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (clean_session_id,),
                ).fetchone()
                if current is None:
                    conn.rollback()
                    return False
                if self._find_active_config_session_row(
                    conn,
                    session_id=clean_session_id,
                ) is not None:
                    raise ConfigSessionBusyError(
                        f"configuration work is already active for session {clean_session_id}"
                    )
                if self._has_active_grading_session(
                    conn,
                    session_id=clean_session_id,
                ):
                    raise ConfigSessionBusyError(
                        f"grading work is already active for session {clean_session_id}"
                    )
                changed = (
                    str(current["rubric_path"] or "") != str(rubric_path)
                    or str(current["answer_key_path"] or "")
                    != str(answer_key_path)
                    or str(current["source_paper_sha256"] or "").casefold()
                    != clean_source_sha256
                )
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET rubric_path = ?, answer_key_path = ?,
                        source_paper_path = ?, source_paper_sha256 = ?,
                        question_bank_sync_state = CASE
                            WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                        question_bank_sync_details_json = CASE
                            WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                        question_bank_sync_error = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_error END,
                        question_bank_sync_updated_at = CASE
                            WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND rubric_path = ? AND answer_key_path = ?
                    """,
                    (
                        str(rubric_path),
                        str(answer_key_path),
                        clean_source_path,
                        clean_source_sha256,
                        changed,
                        changed,
                        changed,
                        changed,
                        clean_session_id,
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    UPDATE session_templates
                    SET is_confirmed = 0,
                        regions_snapshot_pending = 0,
                        regions_snapshot_token = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE session_id = ?
                    """,
                    (clean_session_id,),
                )
                conn.commit()
                return True
            except BaseException:
                conn.rollback()
                raise

    def interrupted_owned_config_input_ids(self) -> set[str]:
        input_ids: set[str] = set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM jobs WHERE job_type = 'config_generation' "
                "AND status IN ('queued','running')"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("mode") == "retry":
                continue
            input_id = str(payload.get("input_id") or "").strip()
            if _SOURCE_ID.fullmatch(input_id):
                input_ids.add(input_id)
        return input_ids

    def update_succeeded_config_result(
        self,
        job_id: int,
        result: dict[str, Any],
    ) -> bool:
        result_json = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET result_json = ?, updated_at = datetime('now','localtime') "
                "WHERE id = ? AND job_type = 'config_generation' AND status = 'succeeded'",
                (result_json, int(job_id)),
            )
        return cursor.rowcount == 1

    def referenced_config_source_ids(self, session_id: int) -> set[str]:
        clean_session_id = int(session_id)
        references: set[str] = set()
        consumed_source_job_ids = {
            source_job_id
            for artifact_session_id, source_job_id, _input_id
            in self.consumed_config_retry_artifacts(clean_session_id)
            if artifact_session_id == clean_session_id
        }
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, status, payload_json, result_json FROM jobs "
                "WHERE job_type = 'config_generation' "
                "AND status IN ('queued','running','paused','succeeded')"
            ).fetchall()
        for row in rows:
            if int(row["id"]) in consumed_source_job_ids:
                continue
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if str(row["status"]) == "succeeded":
                try:
                    result = json.loads(str(row["result_json"] or "{}"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(result, dict) or result.get("outcome") != "partial":
                    continue
            try:
                payload_session_id = int(payload.get("session_id") or 0)
            except (TypeError, ValueError):
                continue
            source_id = str(payload.get("source_id") or "").strip()
            if payload_session_id == clean_session_id and _SOURCE_ID.fullmatch(source_id):
                references.add(source_id)
        return references

    def consumed_config_retry_artifacts(
        self,
        session_id: int | None = None,
    ) -> tuple[tuple[int, int, str], ...]:
        """Return partial-job artifacts made obsolete by a completed retry chain."""
        clean_session_id = int(session_id) if session_id is not None else None
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE job_type = 'config_generation'"
            ).fetchall()
        records = {int(row["id"]): _job_record(row) for row in rows}
        consumed: dict[int, tuple[int, int, str]] = {}
        for record in records.values():
            if (
                record.status != "succeeded"
                or record.payload.get("mode") != "retry"
                or record.result.get("outcome") != "complete"
            ):
                continue
            try:
                retry_session_id = int(record.payload.get("session_id") or 0)
                source_job_id = int(record.payload.get("source_job_id") or 0)
            except (TypeError, ValueError):
                continue
            if retry_session_id <= 0 or (
                clean_session_id is not None and retry_session_id != clean_session_id
            ):
                continue
            visited: set[int] = set()
            while source_job_id > 0 and source_job_id not in visited:
                visited.add(source_job_id)
                source = records.get(source_job_id)
                if (
                    source is None
                    or source.status != "succeeded"
                    or source.result.get("outcome") != "partial"
                ):
                    break
                try:
                    source_session_id = int(source.payload.get("session_id") or 0)
                except (TypeError, ValueError):
                    break
                input_id = str(source.payload.get("input_id") or "").strip()
                if source_session_id != retry_session_id or not _SOURCE_ID.fullmatch(input_id):
                    break
                consumed[source_job_id] = (
                    retry_session_id,
                    source_job_id,
                    input_id,
                )
                if source.payload.get("mode") != "retry":
                    break
                try:
                    source_job_id = int(source.payload.get("source_job_id") or 0)
                except (TypeError, ValueError):
                    break
        return tuple(consumed[key] for key in sorted(consumed))

    def active_config_input_ids(self) -> set[str]:
        input_ids: set[str] = set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM jobs WHERE job_type = 'config_generation' "
                "AND status IN ('queued','running','paused')"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            input_id = str(payload.get("input_id") or "").strip()
            if _SOURCE_ID.fullmatch(input_id):
                input_ids.add(input_id)
        return input_ids

    def referenced_config_paths(self, paths: set[str]) -> set[str]:
        candidates = {str(path) for path in paths if str(path)}
        if not candidates:
            return set()
        referenced: set[str] = set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rubric_path, answer_key_path FROM grading_sessions"
            ).fetchall()
        for row in rows:
            for column in ("rubric_path", "answer_key_path"):
                value = str(row[column] or "")
                if value in candidates:
                    referenced.add(value)
        return referenced

    def referenced_source_paper_paths(self, paths: set[str]) -> set[str]:
        candidates = {str(path) for path in paths if str(path)}
        if not candidates:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_paper_path FROM grading_sessions"
            ).fetchall()
        return {
            str(row["source_paper_path"])
            for row in rows
            if str(row["source_paper_path"] or "") in candidates
        }

    def finish_config_generation_and_bind(
        self,
        job_id: int,
        *,
        session_id: int,
        expected_rubric_path: str,
        expected_answer_key_path: str,
        rubric_path: str,
        answer_key_path: str,
        source_paper_path: str | None = None,
        source_paper_sha256: str | None = None,
        result: dict[str, Any],
    ) -> bool:
        clean_source_path = (
            str(source_paper_path).strip() if source_paper_path is not None else None
        )
        clean_source_sha256 = (
            str(source_paper_sha256).strip().lower()
            if source_paper_sha256 is not None
            else None
        )
        if (clean_source_path is None) != (clean_source_sha256 is None):
            raise ValueError("source paper path and sha256 must be provided together")
        if clean_source_path is not None and (
            not clean_source_path or not _SHA256.fullmatch(clean_source_sha256 or "")
        ):
            raise ValueError("source paper binding is invalid")
        result_json = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                job = conn.execute(
                    "SELECT status, cancel_requested FROM jobs WHERE id = ?",
                    (int(job_id),),
                ).fetchone()
                if (
                    job is None
                    or str(job["status"]) != "running"
                    or bool(int(job["cancel_requested"] or 0))
                ):
                    conn.rollback()
                    return False
                current_session = conn.execute(
                    """
                    SELECT rubric_path, answer_key_path, source_paper_sha256
                    FROM grading_sessions
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (int(session_id),),
                ).fetchone()
                if current_session is None:
                    conn.rollback()
                    return False
                config_changed = (
                    str(current_session["rubric_path"] or "") != str(rubric_path)
                    or str(current_session["answer_key_path"] or "")
                    != str(answer_key_path)
                )
                source_changed = (
                    clean_source_sha256 is not None
                    and str(current_session["source_paper_sha256"] or "")
                    != clean_source_sha256
                )
                sync_invalidated = config_changed or source_changed
                if self._has_active_grading_session(
                    conn,
                    session_id=int(session_id),
                ):
                    conn.rollback()
                    return False
                if clean_source_path is None:
                    session_update = conn.execute(
                        """
                        UPDATE grading_sessions
                        SET rubric_path = ?, answer_key_path = ?,
                            question_bank_sync_state =
                                CASE WHEN ? THEN 'not_started'
                                     ELSE question_bank_sync_state END,
                            question_bank_sync_details_json =
                                CASE WHEN ? THEN '{}'
                                     ELSE question_bank_sync_details_json END,
                            question_bank_sync_error =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_error END,
                            question_bank_sync_updated_at =
                                CASE WHEN ? THEN NULL
                                     ELSE question_bank_sync_updated_at END,
                            updated_at = datetime('now','localtime')
                        WHERE id = ? AND is_deleted = 0
                          AND rubric_path = ? AND answer_key_path = ?
                        """,
                        (
                            str(rubric_path),
                            str(answer_key_path),
                            sync_invalidated,
                            sync_invalidated,
                            sync_invalidated,
                            sync_invalidated,
                            int(session_id),
                            str(expected_rubric_path),
                            str(expected_answer_key_path),
                        ),
                    )
                else:
                    session_update = conn.execute(
                        """
                        UPDATE grading_sessions
                        SET rubric_path = ?, answer_key_path = ?,
                            source_paper_path = ?, source_paper_sha256 = ?,
                            question_bank_sync_state = CASE
                                WHEN ?
                                THEN 'not_started' ELSE question_bank_sync_state END,
                            question_bank_sync_details_json = CASE
                                WHEN ?
                                THEN '{}' ELSE question_bank_sync_details_json END,
                            question_bank_sync_error = CASE
                                WHEN ?
                                THEN NULL ELSE question_bank_sync_error END,
                            question_bank_sync_updated_at = CASE
                                WHEN ?
                                THEN NULL ELSE question_bank_sync_updated_at END,
                            updated_at = datetime('now','localtime')
                        WHERE id = ? AND is_deleted = 0
                          AND rubric_path = ? AND answer_key_path = ?
                        """,
                        (
                            str(rubric_path),
                            str(answer_key_path),
                            clean_source_path,
                            clean_source_sha256,
                            sync_invalidated,
                            sync_invalidated,
                            sync_invalidated,
                            sync_invalidated,
                            int(session_id),
                            str(expected_rubric_path),
                            str(expected_answer_key_path),
                        ),
                    )
                if session_update.rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    UPDATE session_templates
                    SET is_confirmed = 0,
                        regions_snapshot_pending = 0,
                        regions_snapshot_token = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE session_id = ?
                    """,
                    (int(session_id),),
                )
                job_update = conn.execute(
                    """
                    UPDATE jobs
                    SET progress = 0.99,
                        stage = 'config_mapping', detail = 'mapping',
                        result_json = ?, error = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND status = 'running' AND cancel_requested = 0
                    """,
                    (result_json, int(job_id)),
                )
                if job_update.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def finalize_bound_config_generation(
        self,
        job_id: int,
        result: dict[str, Any],
    ) -> bool:
        result_json = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', progress = 1.0,
                    stage = 'config_generation', detail = 'complete',
                    result_json = ?, error = NULL,
                    updated_at = datetime('now','localtime'),
                    finished_at = COALESCE(finished_at, datetime('now','localtime'))
                WHERE id = ? AND status = 'running' AND stage = 'config_mapping'
                """,
                (result_json, int(job_id)),
            )
        return cursor.rowcount == 1


def _job_record(row: sqlite3.Row) -> JobRecord:
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        result = json.loads(str(row["result_json"] or "{}"))
    except (IndexError, json.JSONDecodeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    return JobRecord(
        id=int(row["id"]),
        job_type=str(row["job_type"]),
        payload=payload,
        result=result,
        status=str(row["status"]),
        progress=float(row["progress"] or 0.0),
        stage=str(row["stage"] or ""),
        detail=str(row["detail"] or ""),
        error=row["error"],
        cancel_requested=bool(int(row["cancel_requested"] or 0)),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        updated_at=str(row["updated_at"]),
        finished_at=row["finished_at"],
    )


def _clean_request_token(value: object) -> str:
    clean = str(value or "").strip()
    if not _REQUEST_TOKEN.fullmatch(clean):
        raise ValueError("client request token must be 32 lowercase hex characters")
    return clean


def _clean_request_fingerprint(value: object) -> str:
    clean = str(value or "").strip()
    if not _SHA256.fullmatch(clean):
        raise ValueError("client request fingerprint must be sha256")
    return clean


def _positive_int(value: object) -> int:
    clean = _positive_int_or_zero(value)
    if clean <= 0:
        raise ValueError("value must be a positive integer")
    return clean


def _positive_int_or_zero(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _nonblank_text(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} must be nonblank")
    return clean
