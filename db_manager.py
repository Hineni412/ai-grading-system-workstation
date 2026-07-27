from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.domain_models import GradingResult, QuestionGradingDetail
from backend.performance.metrics import instrument_sqlite_connection
from backend.repositories import (
    BorrowedReadOnlySessionProvider,
    InstrumentedSQLiteConnectionFactory,
    RepositorySessionProvider,
)
from backend.repositories.papers import PaperRepository, PaperRepositoryGateway
from backend.repositories.reporting import ReportRepositoryGateway
from backend.repositories.results import ResultRepository, ResultRepositoryGateway
from backend.repositories.review import (
    ReviewAdjustmentOwnershipError,
    ReviewRepository,
    ReviewRepositoryGateway,
)
from backend.repositories.settings import SettingsRepositoryGateway
from backend.repositories.sessions import (
    SessionDeletionActiveWork,
    SessionDeletionRevisionConflict,
    SessionRepository,
    SessionRepositoryGateway,
    session_deletion_revision,
)
from backend.repositories.students import (
    StudentBackupFailedError,
    StudentCodeConflictError,
    StudentGradingActiveError,
    StudentRecord,
    StudentRepositoryGateway,
    StudentRosterRevisionConflict,
    student_roster_revision,
)
from backend.schema_migrations import ensure_schema_current
from backend.status_contracts import validate_status
from backend.repositories.templates import (
    RegionRepository,
    TemplateRegionRepositoryGateway,
    TemplateRepository,
)
from grading_completeness import resolve_grading_completeness
from path_manager import resolve_stored_file_path
try:
    from question_bank.taxonomy.registry import canonicalize_knowledge as _registry_canonicalize
except Exception:
    _registry_canonicalize = None  # type: ignore[assignment]


QUESTION_BANK_SYNC_STATES = {"not_started", "running", "ready", "partial", "failed"}


class _BorrowedSQLiteConnection:
    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        object.__setattr__(self, "_connection", connection)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._connection, name, value)

    def __enter__(self) -> _BorrowedSQLiteConnection:
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class DBManager:
    def __init__(
        self,
        db_path: Path,
        *,
        external_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.db_path = db_path
        self._external_connection = external_connection
        self._repository_sessions: RepositorySessionProvider
        if external_connection is None:
            self._repository_sessions = InstrumentedSQLiteConnectionFactory(db_path)
        else:
            self._repository_sessions = BorrowedReadOnlySessionProvider(
                external_connection
            )
        self._student_repository = StudentRepositoryGateway(
            self._repository_sessions,
            create_backup=lambda reason: self.create_backup(reason),
        )
        self._session_repository = SessionRepositoryGateway(
            self._repository_sessions
        )
        self._paper_repository = PaperRepositoryGateway(
            self._repository_sessions
        )
        self._result_repository = ResultRepositoryGateway(
            self._repository_sessions
        )
        self._review_repository = ReviewRepositoryGateway(
            self._repository_sessions
        )
        self._template_repository = TemplateRegionRepositoryGateway(
            self._repository_sessions
        )
        self._settings_repository = SettingsRepositoryGateway(
            self._repository_sessions
        )
        self._report_repository = ReportRepositoryGateway(
            self._repository_sessions
        )
        try:
            from path_manager import get_path_manager
            pm = get_path_manager()
            if self.db_path.resolve() == pm.db_path.resolve():
                self.backup_dir = pm.backups_dir
            else:
                self.backup_dir = self.db_path.parent / "backups"
        except Exception:
            self.backup_dir = self.db_path.parent / "backups"

    @property
    def student_repository(self) -> StudentRepositoryGateway:
        return self._student_repository

    @property
    def session_repository(self) -> SessionRepositoryGateway:
        return self._session_repository

    @property
    def paper_repository(self) -> PaperRepositoryGateway:
        return self._paper_repository

    @property
    def result_repository(self) -> ResultRepositoryGateway:
        return self._result_repository

    @property
    def review_repository(self) -> ReviewRepositoryGateway:
        return self._review_repository

    @property
    def template_repository(self) -> TemplateRegionRepositoryGateway:
        return self._template_repository

    @property
    def settings_repository(self) -> SettingsRepositoryGateway:
        return self._settings_repository

    @property
    def report_repository(self) -> ReportRepositoryGateway:
        return self._report_repository

    def _connect(self) -> sqlite3.Connection | _BorrowedSQLiteConnection:
        if self._external_connection is not None:
            return _BorrowedSQLiteConnection(self._external_connection)
        conn = instrument_sqlite_connection(sqlite3.connect(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def create_backup(self, reason: str, *, once_per_day: bool = False) -> Path | None:
        if not self.db_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in reason).strip("_") or "manual"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if once_per_day:
            date_prefix = timestamp[:8]
            existing = sorted(self.backup_dir.glob(f"grading_before_{safe_reason}_{date_prefix}_*.db"))
            if existing:
                return existing[-1]
        backup_path = self.backup_dir / f"grading_before_{safe_reason}_{timestamp}.db"
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        return backup_path

    def initialize(self) -> None:
        ensure_schema_current("grading", self.db_path)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET updated_at = COALESCE(updated_at, datetime('now','localtime'))
                """
            )
            conn.execute(
                """
                UPDATE session_templates
                SET regions_snapshot_token = lower(hex(randomblob(16)))
                WHERE regions_snapshot_pending = 1
                  AND COALESCE(regions_snapshot_token, '') = ''
                """
            )
            conn.execute(
                """
                UPDATE session_templates
                SET regions_snapshot_token = NULL
                WHERE regions_snapshot_pending = 0
                """
            )

            seen_region_uuids: set[str] = set()
            region_identity_rows = conn.execute(
                "SELECT id, region_uuid FROM answer_regions ORDER BY id ASC"
            ).fetchall()
            for row in region_identity_rows:
                region_uuid = row["region_uuid"]
                if region_uuid is not None and str(region_uuid).strip() and str(region_uuid) not in seen_region_uuids:
                    seen_region_uuids.add(str(region_uuid))
                    continue
                replacement_uuid = str(uuid4())
                while replacement_uuid in seen_region_uuids:
                    replacement_uuid = str(uuid4())
                conn.execute(
                    "UPDATE answer_regions SET region_uuid = ? WHERE id = ?",
                    (replacement_uuid, int(row["id"])),
                )
                seen_region_uuids.add(replacement_uuid)

            conn.execute(
                """
                UPDATE answer_regions
                SET mapping_status = CASE
                    WHEN COALESCE(TRIM(mapped_question_id), '') <> '' THEN 'manual'
                    ELSE 'unbound'
                END
                WHERE mapping_status IS NULL
                   OR TRIM(mapping_status) = ''
                   OR (
                       mapping_status = 'unbound'
                       AND COALESCE(TRIM(mapped_question_id), '') <> ''
                   )
                """
            )

            conn.commit()

    # ---------- Student ----------
    def upsert_students(self, students: list[StudentRecord]) -> dict[str, int]:
        return self.student_repository.upsert_students(students)

    def student_roster_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        return self.student_repository.student_roster_snapshot()

    def student_workspace_snapshot(
        self,
        *,
        search: str,
        class_name: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        return self.student_repository.student_workspace_snapshot(
            search=search,
            class_name=class_name,
            page=page,
            page_size=page_size,
        )

    def upsert_students_if_revision(
        self,
        students: list[StudentRecord],
        *,
        expected_revision: str,
    ) -> dict[str, int | str]:
        return self.student_repository.upsert_students_if_revision(
            students,
            expected_revision=expected_revision,
        )

    def list_students(self) -> list[dict[str, Any]]:
        return self.student_repository.list_students()

    def update_student(self, student_id: int, student_code: str, name: str, class_name: str | None) -> None:
        self.student_repository.update_student(
            student_id,
            student_code,
            name,
            class_name,
        )

    def update_student_if_revision(
        self,
        student_id: int,
        student_code: str,
        name: str,
        class_name: str | None,
        *,
        expected_revision: str,
    ) -> dict[str, Any]:
        return self.student_repository.update_student_if_revision(
            student_id,
            student_code,
            name,
            class_name,
            expected_revision=expected_revision,
        )

    def student_deletion_impact(self, student_id: int) -> dict[str, Any]:
        return self.student_repository.student_deletion_impact(student_id)

    def delete_student_hard(
        self,
        student_id: int,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return self.student_repository.delete_student_hard(
            student_id,
            expected_revision=expected_revision,
        )

    def find_student_by_name(self, name: str) -> dict[str, Any] | None:
        return self.student_repository.find_student_by_name(name)

    # ---------- Session ----------
    def set_app_setting(self, key: str, value: str) -> None:
        self.settings_repository.set_app_setting(key, value)

    def get_app_setting(self, key: str, default: str | None = None) -> str | None:
        return self.settings_repository.get_app_setting(key, default)

    def create_grading_session(
        self,
        session_name: str,
        rubric_path: str,
        answer_key_path: str,
        *,
        source_paper_path: str = "",
        source_paper_sha256: str = "",
    ) -> int:
        return self.session_repository.create_grading_session(
            session_name,
            rubric_path,
            answer_key_path,
            source_paper_path=source_paper_path,
            source_paper_sha256=source_paper_sha256,
        )

    def bind_grading_session_source(
        self,
        session_id: int,
        *,
        source_paper_path: str,
        source_paper_sha256: str,
    ) -> None:
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        with self._connect() as conn:
            current = conn.execute(
                "SELECT source_paper_sha256 FROM grading_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            if current is None:
                raise KeyError(f"grading session not found: {session_id}")
            changed = str(current["source_paper_sha256"] or "") != source_sha256
            conn.execute(
                """
                UPDATE grading_sessions
                SET source_paper_path = ?, source_paper_sha256 = ?,
                    question_bank_sync_state = CASE WHEN ? THEN 'not_started' ELSE question_bank_sync_state END,
                    question_bank_sync_details_json = CASE WHEN ? THEN '{}' ELSE question_bank_sync_details_json END,
                    question_bank_sync_error = CASE WHEN ? THEN NULL ELSE question_bank_sync_error END,
                    question_bank_sync_updated_at = CASE WHEN ? THEN NULL ELSE question_bank_sync_updated_at END,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (
                    source_path,
                    source_sha256,
                    changed,
                    changed,
                    changed,
                    changed,
                    int(session_id),
                ),
            )
            conn.commit()

    def update_question_bank_sync_state(
        self,
        session_id: int,
        *,
        state: str,
        details: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        normalized = str(state or "").strip().casefold()
        if normalized not in QUESTION_BANK_SYNC_STATES:
            raise ValueError(f"unsupported question-bank sync state: {state}")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE grading_sessions
                SET question_bank_sync_state = ?, question_bank_sync_details_json = ?,
                    question_bank_sync_error = ?,
                    question_bank_sync_updated_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (
                    normalized,
                    json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True),
                    str(error).strip() if error else None,
                    int(session_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"grading session not found: {session_id}")
            conn.commit()

    def rename_grading_session(self, session_id: int, new_name: str) -> None:
        self.session_repository.rename_grading_session(session_id, new_name)

    def update_grading_session_config(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        template_config_path: str | None = None,
    ) -> None:
        if hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache.pop(session_id, None)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE grading_sessions
                SET rubric_path = ?,
                    answer_key_path = ?,
                    template_config_path = COALESCE(?, template_config_path),
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (rubric_path, answer_key_path, template_config_path, session_id),
            )
            conn.commit()

    def publish_grading_session_config(
        self,
        session_id: int,
        *,
        rubric_path: str,
        answer_key_path: str,
        expected_rubric_path: str,
        expected_answer_key_path: str,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    UPDATE grading_sessions
                    SET rubric_path = ?, answer_key_path = ?,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND is_deleted = 0
                      AND rubric_path = ? AND answer_key_path = ?
                    """,
                    (
                        str(rubric_path),
                        str(answer_key_path),
                        int(session_id),
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache.pop(int(session_id), None)
        return True

    def publish_grading_session_config_with_source(
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
        source_path, source_sha256 = _validated_source_binding(
            source_paper_path,
            source_paper_sha256,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT source_paper_sha256 FROM grading_sessions "
                    "WHERE id = ? AND is_deleted = 0",
                    (int(session_id),),
                ).fetchone()
                if current is None:
                    conn.rollback()
                    return False
                changed = str(current["source_paper_sha256"] or "") != source_sha256
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
                        source_path,
                        source_sha256,
                        changed,
                        changed,
                        changed,
                        changed,
                        int(session_id),
                        str(expected_rubric_path),
                        str(expected_answer_key_path),
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache.pop(int(session_id), None)
        return True

    def soft_delete_grading_session(self, session_id: int) -> None:
        self.session_repository.soft_delete_grading_session(session_id)

    def restore_grading_session(self, session_id: int) -> None:
        self.session_repository.restore_grading_session(session_id)

    def list_grading_sessions(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        return self.session_repository.list_grading_sessions(include_deleted)

    def get_grading_session(self, session_id: int) -> dict[str, Any] | None:
        return self.session_repository.get_grading_session(session_id)

    def collect_session_storage_paths(self, session_id: int) -> list[str]:
        paths: list[str] = []

        def add_values(row: Any, fields: list[str]) -> None:
            if row is None:
                return
            for field in fields:
                value = row[field]
                if value:
                    paths.append(str(value))

        with self._connect() as conn:
            add_values(
                conn.execute(
                    """
                    SELECT rubric_path, answer_key_path, template_config_path
                    FROM grading_sessions
                    WHERE id = ?
                    """,
                    (session_id,),
                ).fetchone(),
                ["rubric_path", "answer_key_path", "template_config_path"],
            )
        add_values(
            self.template_repository.get_session_storage_path_row(session_id),
            [
                "front_template_path",
                "back_template_path",
                "ai_analysis_path",
                "template_config_path",
                "regions_path",
            ],
        )
        for row in self.paper_repository.get_session_storage_path_rows(
            session_id
        ):
            add_values(row, ["front_image", "back_image"])
        for row in self.review_repository.get_session_annotation_path_rows(
            session_id
        ):
            add_values(row, ["annotated_front_path", "annotated_back_path"])
        with self._connect() as conn:
            report_rows = conn.execute(
                """
                SELECT json_extract(result_json, '$.file_path') AS file_path
                FROM jobs
                WHERE job_type = 'report_export'
                  AND json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                  AND json_valid(result_json) = 1
                  AND json_type(result_json, '$.file_path') = 'text'
                """,
                (str(int(session_id)),),
            ).fetchall()
            config_job_rows = conn.execute(
                """
                SELECT id, json_extract(payload_json, '$.input_id') AS input_id
                FROM jobs
                WHERE job_type = 'config_generation'
                  AND json_valid(payload_json) = 1
                  AND json_type(payload_json, '$.session_id') IN ('integer', 'text')
                  AND CAST(json_extract(payload_json, '$.session_id') AS TEXT) = ?
                """,
                (str(int(session_id)),),
            ).fetchall()
        for row in report_rows:
            add_values(row, ["file_path"])
        upload_config_dir = self.db_path.parent.parent / "config" / "uploaded"
        for row in config_job_rows:
            job_id = int(row["id"])
            paths.append(
                str(upload_config_dir / f"config_generation_draft_job_{job_id}.json")
            )
            input_id = str(row["input_id"] or "").strip().casefold()
            if re.fullmatch(r"[0-9a-f]{32}", input_id):
                paths.append(
                    str(upload_config_dir / f"config_generation_input_{input_id}.json")
                )

        return paths

    def hard_delete_grading_session(
        self,
        session_id: int,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, int]:
        counts = {
            "teacher_score_locks": 0,
            "grading_run_items": 0,
            "grading_runs": 0,
            "jobs": 0,
            "session_details": 0,
            "annotated_results": 0,
            "session_attendance": 0,
            "session_results": 0,
            "exam_papers": 0,
            "answer_regions": 0,
            "session_templates": 0,
            "grading_sessions": 0,
        }

        with self._repository_sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                results = ResultRepository(repository_session)
                reviews = ReviewRepository(repository_session)
                papers = PaperRepository(repository_session)
                sessions = SessionRepository(repository_session)
                regions = RegionRepository(repository_session)
                templates = TemplateRepository(repository_session)
                current = sessions.get_grading_session(int(session_id))
                if current is None:
                    raise ValueError(f"Session {session_id} does not exist.")
                if int(current.get("is_deleted") or 0) != 1:
                    raise ValueError(
                        "Only archived sessions can be permanently deleted."
                    )
                if (
                    expected_revision is not None
                    and session_deletion_revision(current) != str(expected_revision)
                ):
                    raise SessionDeletionRevisionConflict(
                        "Session changed before permanent deletion"
                    )
                active = sessions.session_active_work_counts(int(session_id))
                if active["active_jobs"] or active["active_grading_runs"]:
                    raise SessionDeletionActiveWork(**active)
                counts["grading_run_items"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM grading_run_items
                            WHERE run_id IN (
                                SELECT id FROM grading_runs WHERE session_id = ?
                            )
                            """,
                            (int(session_id),),
                        ).rowcount
                    ),
                )
                counts["grading_runs"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            "DELETE FROM grading_runs WHERE session_id = ?",
                            (int(session_id),),
                        ).rowcount
                    ),
                )
                counts["jobs"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM jobs
                            WHERE json_valid(payload_json) = 1
                              AND json_type(payload_json, '$.session_id')
                                  IN ('integer', 'text')
                              AND CAST(
                                  json_extract(payload_json, '$.session_id') AS TEXT
                              ) = ?
                            """,
                            (str(int(session_id)),),
                        ).rowcount
                    ),
                )
                counts["teacher_score_locks"] = (
                    reviews.delete_session_teacher_score_locks(session_id)
                )
                result_ids = results.get_session_result_ids(session_id)
                counts["session_details"] = results.delete_result_details(
                    result_ids
                )
                counts["annotated_results"] = (
                    reviews.delete_session_annotations(
                        session_id,
                        result_ids,
                    )
                )
                counts["session_attendance"] = (
                    sessions.delete_session_attendance(session_id)
                )
                counts["session_results"] = results.delete_session_results(
                    session_id
                )
                counts["exam_papers"] = papers.delete_session_papers(
                    session_id
                )
                counts["answer_regions"] = regions.delete_session_regions(
                    session_id
                )
                counts["session_templates"] = (
                    templates.delete_session_templates(session_id)
                )
                counts["grading_sessions"] = max(
                    0,
                    int(
                        repository_session.connection.execute(
                            """
                            DELETE FROM grading_sessions
                            WHERE id = ? AND is_deleted = 1
                            """,
                            (session_id,),
                        ).rowcount
                    ),
                )

        return counts

    def update_session_status(self, session_id: int, status: str) -> None:
        status = validate_status("grading_sessions.status", status)
        with self._repository_sessions.session() as repository_session:
            with repository_session.transaction():
                repository_session.connection.execute(
                    """
                    UPDATE grading_sessions
                    SET status = ?, updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (status, session_id),
                )
                if status != "running":
                    PaperRepository(
                        repository_session
                    ).mark_grading_papers_failed(
                        session_id,
                        "批改中途被终止或强制重置",
                    )

    def try_start_session_run(self, session_id: int) -> bool:
        with self._repository_sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                cursor = repository_session.connection.execute(
                    """
                    UPDATE grading_sessions
                    SET status = 'running',
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND COALESCE(status, '') <> 'running'
                    """,
                    (session_id,),
                )
                success = int(cursor.rowcount or 0) == 1
                if success:
                    PaperRepository(
                        repository_session
                    ).mark_grading_papers_failed(
                        session_id,
                        "批改中途被异常中断，请重试",
                    )
                return success

    def finish_session_run(self, session_id: int, status: str = "completed") -> None:
        self.update_session_status(session_id, status)

    def clear_session_run_data(self, session_id: int) -> None:
        self.create_backup("clear_session")
        with self._repository_sessions.session() as repository_session:
            with repository_session.transaction(immediate=True):
                # Teacher-confirmed sidecar scores intentionally survive an
                # ordinary AI run reset and are removed only by hard delete.
                results = ResultRepository(repository_session)
                result_ids = results.get_session_result_ids(session_id)
                results.delete_result_details(result_ids)
                ReviewRepository(
                    repository_session
                ).delete_session_annotations(session_id, result_ids)
                SessionRepository(
                    repository_session
                ).delete_session_attendance(session_id)
                results.delete_session_results(session_id)
                PaperRepository(repository_session).delete_session_papers(
                    session_id
                )

    def replace_session_attendance(self, session_id: int, rows: list[dict[str, Any]]) -> None:
        self.session_repository.replace_session_attendance(session_id, rows)

    def get_session_attendance(self, session_id: int) -> list[dict[str, Any]]:
        return self.session_repository.get_session_attendance(session_id)

    # ---------- Template & regions ----------
    def upsert_session_template(self, session_id: int, front_template_path: str, back_template_path: str) -> int:
        return self.template_repository.upsert_session_template(
            session_id,
            front_template_path,
            back_template_path,
        )

    def activate_session_template(
        self,
        session_id: int,
        *,
        front_template_path: str,
        back_template_path: str,
        ai_analysis_path: str,
        template_config_path: str,
        regions_path: str,
    ) -> int:
        """Atomically activate one generated template package for a session."""
        return self.template_repository.activate_session_template(
            session_id,
            front_template_path=front_template_path,
            back_template_path=back_template_path,
            ai_analysis_path=ai_analysis_path,
            template_config_path=template_config_path,
            regions_path=regions_path,
        )

    def get_session_template(self, session_id: int) -> dict[str, Any] | None:
        return self.template_repository.get_session_template(session_id)

    def update_session_template_analysis(
        self,
        session_id: int,
        *,
        ai_analysis_path: str | None,
        template_config_path: str | None,
        regions_path: str | None,
    ) -> None:
        self.template_repository.update_session_template_analysis(
            session_id,
            ai_analysis_path=ai_analysis_path,
            template_config_path=template_config_path,
            regions_path=regions_path,
        )

    def swap_template_page_assignment(
        self,
        session_id: int,
        template_id: int,
        *,
        expected_front_path: str,
        expected_back_path: str,
    ) -> bool:
        return self.template_repository.swap_template_page_assignment(
            session_id,
            template_id,
            expected_front_path=expected_front_path,
            expected_back_path=expected_back_path,
        )

    def save_answer_regions(self, session_id: int, template_id: int, regions: list[dict[str, Any]]) -> None:
        self.template_repository.save_answer_regions(
            session_id,
            template_id,
            regions,
        )

    def list_answer_regions(self, session_id: int) -> list[dict[str, Any]]:
        return self.template_repository.list_answer_regions(session_id)

    def bulk_update_answer_region_mapping(self, session_id: int, rows: list[dict[str, Any]]) -> None:
        self.template_repository.bulk_update_answer_region_mapping(
            session_id,
            rows,
        )

    def add_answer_region(self, session_id: int, template_id: int, region: dict[str, Any]) -> int:
        """Insert a single region and return its new id."""
        return self.template_repository.add_answer_region(
            session_id,
            template_id,
            region,
        )

    def replace_answer_regions_atomic(
        self,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        *,
        confirmed: bool,
    ) -> str:
        return self.template_repository.replace_answer_regions_atomic(
            session_id,
            template_id,
            regions,
            confirmed=confirmed,
        )

    def mark_region_snapshot_complete(
        self,
        session_id: int,
        *,
        expected_token: str,
    ) -> bool:
        return self.template_repository.mark_region_snapshot_complete(
            session_id,
            expected_token=expected_token,
        )

    def delete_answer_region(self, region_id: int) -> None:
        self.template_repository.delete_answer_region(region_id)

    def update_answer_region_bbox(
        self,
        region_id: int,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> None:
        self.template_repository.update_answer_region_bbox(
            region_id,
            x,
            y,
            w,
            h,
        )

    def mark_template_confirmed(self, session_id: int, confirmed: bool = True) -> None:
        self.template_repository.mark_template_confirmed(
            session_id,
            confirmed,
        )

    def is_template_ready(self, session_id: int) -> bool:
        return self.template_repository.is_template_ready(session_id)

    # ---------- Paper/result persistence ----------
    def create_exam_paper(
        self,
        session_id: int,
        front_image: str,
        back_image: str,
        ocr_name: str,
        student_id: int | None,
        match_status: str,
        processing_status: str,
        error_message: str | None = None,
    ) -> int:
        return self.paper_repository.create_exam_paper(
            session_id,
            front_image,
            back_image,
            ocr_name,
            student_id,
            match_status,
            processing_status,
            error_message,
        )

    def update_exam_paper_status(self, paper_id: int, processing_status: str, error_message: str | None = None) -> None:
        self.paper_repository.update_exam_paper_status(
            paper_id,
            processing_status,
            error_message,
        )

    def update_exam_paper_status_if_current_assignment(
        self,
        paper_id: int,
        student_id: int,
        processing_status: str,
        error_message: str | None = None,
    ) -> bool:
        return self.paper_repository.update_exam_paper_status_if_current_assignment(
            paper_id,
            student_id,
            processing_status,
            error_message,
        )

    def save_session_result(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
    ) -> int:
        return self.result_repository.save_session_result(
            session_id,
            student_id,
            paper_id,
            grading_result,
        )

    def publish_session_result_if_current_assignment(
        self,
        session_id: int,
        student_id: int,
        paper_id: int,
        grading_result: GradingResult,
    ) -> int | None:
        return self.result_repository.publish_session_result_if_current_assignment(
            session_id,
            student_id,
            paper_id,
            grading_result,
        )

    def get_session_progress(self, session_id: int) -> dict[str, int | float]:
        source = self.paper_repository.get_session_progress_source(session_id)
        total = source["total"]
        matched = source["matched"]
        unmatched = source["unmatched"]
        graded = source["graded"]
        failed = source["failed"]
        in_progress = source["in_progress"]
        absent = source["absent"]
        scan_issue = source["scan_issue"]

        done = graded + failed
        progress_percent = round((done / matched) * 100, 2) if matched else 0.0
        return {
            "total_papers": int(total),
            "matched_papers": int(matched),
            "unmatched_papers": int(unmatched),
            "graded_papers": int(graded),
            "failed_papers": int(failed),
            "grading_papers": int(in_progress),
            "needs_human_review": int(source["review_count"]),
            "absent_students": int(absent),
            "scan_issue_students": int(scan_issue),
            "progress_percent": progress_percent,
        }

    def list_session_anomalies(self, session_id: int) -> list[dict[str, Any]]:
        """Return unmatched papers, scan issues, and failed papers without paths."""
        items = self.paper_repository.get_session_anomaly_rows(session_id)
        for row in self.list_failed_papers(int(session_id)):
            paper_id = int(row["paper_id"])
            items.append(
                {
                    "anomaly_id": f"grading_failed:{paper_id:020d}",
                    "anomaly_type": "grading_failed",
                    "display_name": f"批改失败试卷 #{paper_id}",
                    "student_code": row.get("student_code"),
                    "class_name": row.get("class_name"),
                    "status": str(row.get("processing_status") or "failed"),
                    "detail": row.get("error_message"),
                    "created_at": row.get("created_at"),
                }
            )
        return sorted(items, key=lambda item: (item["anomaly_type"], item["anomaly_id"]))

    def list_failed_papers(self, session_id: int) -> list[dict[str, Any]]:
        """返回本场次中批改失败（processing_status='failed'、'grading'（非运行状态下）或含有局部失败降级）的所有试卷，含学生姓名与错误信息。"""
        items = self.paper_repository.get_failed_paper_rows(session_id)
        for item in items:
            item["error_message"] = sanitize_incomplete_failure_summary(item.get("error_message"))
        existing_paper_ids = {int(item["paper_id"]) for item in items if item.get("paper_id") is not None}
        for row in self.list_incomplete_results(session_id):
            paper_id = int(row["paper_id"])
            if paper_id in existing_paper_ids:
                continue
            items.append(
                {
                    "paper_id": paper_id,
                    "ocr_name": row.get("ocr_name"),
                    "student_name": row.get("student_name"),
                    "student_code": row.get("student_code"),
                    "class_name": row.get("class_name"),
                    "processing_status": row.get("processing_status"),
                    "error_message": "批改结果不完整，需补跑受影响大题",
                    "created_at": None,
                }
            )
            existing_paper_ids.add(paper_id)
        return sorted(items, key=lambda item: int(item["paper_id"]))

    def list_failed_papers_detailed(self, session_id: int) -> list[dict[str, Any]]:
        """返回本场次中批改失败（processing_status='failed'、'grading'（非运行状态下）或含有局部失败降级）的所有试卷的详细信息，用于增量重试。"""
        items = self.paper_repository.get_failed_paper_detail_rows(session_id)
        existing_paper_ids = {int(item["paper_id"]) for item in items if item.get("paper_id") is not None}
        for row in self.list_incomplete_results(session_id):
            paper_id = int(row["paper_id"])
            if paper_id in existing_paper_ids:
                continue
            items.append(
                {
                    "paper_id": paper_id,
                    "front_image": row.get("front_image"),
                    "back_image": row.get("back_image"),
                    "ocr_name": row.get("ocr_name"),
                    "student_id": row.get("student_id"),
                    "match_status": row.get("match_status"),
                }
            )
            existing_paper_ids.add(paper_id)
        return items

    def list_incomplete_results(self, session_id: int) -> list[dict[str, Any]]:
        rubric = self._load_session_rubric(session_id)
        result_rows, details_by_result = (
            self.result_repository.get_session_completeness_source(session_id)
        )
        if not result_rows:
            return []

        items: list[dict[str, Any]] = []
        for row in result_rows:
            parsed_raw_json = _safe_json_loads(row["raw_json"])
            completeness = resolve_grading_completeness(
                parsed_raw_json,
                rubric=rubric,
                details=details_by_result.get(int(row["result_id"]), []),
            )
            if not isinstance(completeness, dict):
                continue
            if completeness.get("status") not in {"incomplete", "invalid"}:
                continue
            retry_attempts = _grading_retry_attempts(parsed_raw_json)
            items.append(
                {
                    "result_id": int(row["result_id"]),
                    "student_id": int(row["student_id"]),
                    "paper_id": int(row["paper_id"]),
                    "student_code": row["student_code"],
                    "student_name": row["student_name"],
                    "class_name": row["class_name"],
                    "ocr_name": row["ocr_name"],
                    "front_image": row["front_image"],
                    "back_image": row["back_image"],
                    "match_status": row["match_status"],
                    "processing_status": row["processing_status"],
                    "status": completeness["status"],
                    "missing_question_ids": list(completeness.get("missing_question_ids", [])),
                    "affected_major_question_ids": list(completeness.get("affected_major_question_ids", [])),
                    "last_failure_reason": _last_incomplete_failure_reason(
                        parsed_raw_json,
                        fallback_error=row["error_message"],
                        completeness_status=str(completeness.get("status") or ""),
                    ),
                    "retry_attempt_count": len(retry_attempts),
                }
            )
        return items

    def get_session_results(self, session_id: int) -> list[dict[str, Any]]:
        return self.result_repository.get_session_results(session_id)

    def get_session_review_rows(self, session_id: int) -> list[dict[str, Any]]:
        return self.review_repository.get_session_review_rows(session_id)

    def list_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str | None = None,
        *,
        student_id: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.review_repository.list_teacher_score_locks(
            session_id,
            scan_batch_id=scan_batch_id,
            student_id=student_id,
        )

    def get_teacher_score_lock(
        self,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
    ) -> dict[str, Any] | None:
        return self.review_repository.get_teacher_score_lock(
            session_id,
            scan_batch_id,
            student_id,
            question_id,
        )

    def confirm_teacher_score_lock(
        self,
        *,
        session_id: int,
        scan_batch_id: str,
        student_id: int,
        question_id: str,
        score_awarded: float,
        max_score: float,
        deduction_reason: str | None,
        source_target_type: str,
        source_target_id: int,
        expected_revision: int,
        sync_existing_detail: bool = True,
    ) -> dict[str, Any]:
        return self.review_repository.confirm_teacher_score_lock(
            session_id=session_id,
            scan_batch_id=scan_batch_id,
            student_id=student_id,
            question_id=question_id,
            score_awarded=score_awarded,
            max_score=max_score,
            deduction_reason=deduction_reason,
            source_target_type=source_target_type,
            source_target_id=source_target_id,
            expected_revision=expected_revision,
            sync_existing_detail=sync_existing_detail,
        )

    def confirm_teacher_scores(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.review_repository.confirm_teacher_scores(
            session_id,
            scan_batch_id,
            confirmations,
        )

    def confirm_teacher_score_locks(
        self,
        session_id: int,
        scan_batch_id: str,
        confirmations: list[dict[str, Any]],
        *,
        sync_existing_details: bool = True,
    ) -> dict[str, Any]:
        return self.review_repository.confirm_teacher_score_locks(
            session_id,
            scan_batch_id,
            confirmations,
            sync_existing_details=sync_existing_details,
        )

    def get_review_media_context(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> dict[str, Any] | None:
        return self.review_repository.get_review_media_context(
            session_id,
            result_id,
            detail_id,
        )

    def get_result_context(self, result_id: int) -> dict[str, Any] | None:
        return self.result_repository.get_result_context(result_id)

    def get_result_details(self, result_id: int) -> list[dict[str, Any]]:
        return self.result_repository.get_result_details(result_id)

    def replace_result_details_atomic(
        self,
        result_id: int,
        remove_question_ids: list[str],
        replacement_details: list[QuestionGradingDetail],
        *,
        student_score: float,
        needs_human_review: bool,
        raw_json: dict,
        rubric: dict | None = None,
    ) -> None:
        """Replace one or more question details without replacing the parent result row."""
        self.result_repository.replace_result_details_atomic(
            result_id,
            remove_question_ids,
            replacement_details,
            student_score=student_score,
            needs_human_review=needs_human_review,
            raw_json=raw_json,
            rubric=rubric,
        )

    def record_result_retry_failure(self, result_id: int, attempt: dict[str, Any]) -> None:
        """Append an uncapped structured retry attempt while preserving completeness state."""
        self.result_repository.record_result_retry_failure(
            result_id,
            attempt,
        )

    def update_result_detail(
        self,
        detail_id: int,
        score_awarded: float,
        deduction_reason: str | None,
        error_category: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.review_repository.update_result_detail(
            detail_id,
            score_awarded,
            deduction_reason,
            error_category,
            error_summary,
        )

    def recalculate_result_score(self, result_id: int) -> None:
        self.review_repository.recalculate_result_score(result_id)

    def update_session_detail_scores(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        return self.review_repository.update_session_detail_scores(
            session_id,
            adjustments,
        )

    def apply_session_review_adjustments(
        self,
        session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        return self.review_repository.apply_session_review_adjustments(
            session_id,
            adjustments,
        )

    def upsert_annotated_result(
        self,
        session_id: int,
        result_id: int,
        annotated_front_path: str,
        annotated_back_path: str,
    ) -> dict[str, Any] | None:
        return self.review_repository.upsert_annotated_result(
            session_id,
            result_id,
            annotated_front_path,
            annotated_back_path,
        )

    def is_annotated_result_path_referenced(self, path_value: str) -> bool:
        return self.review_repository.is_annotated_result_path_referenced(
            path_value
        )

    def get_annotated_result(self, result_id: int) -> dict[str, Any] | None:
        return self.review_repository.get_annotated_result(result_id)

    def get_session_weak_points(self, session_id: int, student_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.result_repository.get_session_weak_point_rows(
            session_id,
            student_id,
        )
        return self._build_weak_point_rows(rows)

    def get_active_global_weak_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.result_repository.get_active_weak_point_rows(
            student_id,
            session_ids,
        )
        return self._build_weak_point_rows(rows)

    def get_active_assessment_evidence(
        self,
        *,
        student_ids: list[str] | tuple[str, ...] = (),
        session_ids: list[int] | tuple[int, ...] = (),
    ) -> list[dict[str, Any]]:
        normalized_students = [int(value) for value in student_ids]
        normalized_sessions = [int(value) for value in session_ids]
        rows = self.result_repository.get_active_assessment_rows(
            student_ids=normalized_students,
            session_ids=normalized_sessions,
        )
        enriched = self._enrich_detail_rows(rows)
        for row in enriched:
            row["full_score"] = _safe_float(row.get("max_score"), 0.0)
        return enriched

    def get_active_global_error_points(
        self,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.result_repository.get_active_error_point_rows(
            student_id,
            session_ids,
        )
        return self._build_error_point_rows(rows)

    def get_active_student_score_rates(self) -> list[dict[str, Any]]:
        return self.result_repository.get_active_student_score_rates()

    def get_active_wrong_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        return [
            item for item in self.get_active_items_for_knowledge(student_id, knowledge_id)
            if item.get("is_deducted")
        ]

    def get_active_items_for_knowledge(self, student_id: int, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self.result_repository.get_active_detail_rows(
            student_id=student_id
        )

        result: list[dict[str, Any]] = []
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            row_knowledge_ids = _knowledge_ids_from_row(row)
            if not _knowledge_id_matches(knowledge_id, row_knowledge_ids):
                continue
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "")
            max_score = full_score if full_score is not None else awarded
            row["max_score"] = max_score
            row["score_rate"] = round(awarded / max_score * 100, 2) if max_score else 0.0
            row["is_deducted"] = _is_deducted(awarded, full_score, reason)
            row["deduction_amount"] = max(0.0, round((max_score or 0.0) - awarded, 2))
            row["knowledge_label"] = rubric_cache[session_id]["label"].get(
                knowledge_id,
                _fallback_knowledge_label(knowledge_id, qid),
            )
            result.append(row)
        return sorted(
            result,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                0 if item.get("is_deducted") else 1,
                str(item.get("graded_at") or ""),
            ),
        )

    def get_representative_wrong_items_for_knowledge(
        self,
        knowledge_id: str,
        session_ids: list[int] | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        rows = self._query_active_detail_rows(session_ids=session_ids)
        result = self._enrich_detail_rows(rows, knowledge_id=knowledge_id)
        wrong_items = [item for item in result if item.get("is_deducted")]
        random.shuffle(wrong_items)
        return sorted(
            wrong_items,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                str(item.get("graded_at") or ""),
            ),
        )[: max(1, int(limit))]

    def get_representative_wrong_items_for_error(
        self,
        error_category: str,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        rows = self._query_active_detail_rows(student_id=student_id, session_ids=session_ids)
        result: list[dict[str, Any]] = []
        for item in self._enrich_detail_rows(rows):
            if not item.get("is_deducted"):
                continue
            category = _normalize_error_category(item.get("error_category"), str(item.get("deduction_reason") or ""))
            if category != error_category:
                continue
            item["error_category"] = category
            if not item.get("error_summary"):
                item["error_summary"] = _short_reason(str(item.get("deduction_reason") or ""))
            result.append(item)
        random.shuffle(result)
        return sorted(
            result,
            key=lambda item: (
                float(item.get("score_rate") or 0),
                str(item.get("graded_at") or ""),
            ),
        )[: max(1, int(limit))]

    def _query_active_detail_rows(
        self,
        *,
        student_id: int | None = None,
        session_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        return self.result_repository.get_active_detail_rows(
            student_id=student_id,
            session_ids=session_ids,
        )

    def _enrich_detail_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        knowledge_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            row_knowledge_ids = _knowledge_ids_from_row(row)
            if knowledge_id is not None and not _knowledge_id_matches(knowledge_id, row_knowledge_ids):
                continue
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "")
            max_score = full_score if full_score is not None else awarded
            row = dict(row)
            row["max_score"] = max_score
            row["score_rate"] = round(awarded / max_score * 100, 2) if max_score else 0.0
            row["is_deducted"] = _is_deducted(awarded, full_score, reason)
            row["deduction_amount"] = max(0.0, round((max_score or 0.0) - awarded, 2))
            if knowledge_id:
                row["knowledge_label"] = rubric_cache[session_id]["label"].get(
                    knowledge_id,
                    _fallback_knowledge_label(knowledge_id, qid),
                )
            result.append(row)
        return result

    def _build_weak_point_rows(self, detail_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, str], dict[str, Any]] = {}
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}

        for row in detail_rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            maps = rubric_cache[session_id]
            qid = str(row.get("question_id") or "")
            knowledge_ids = _knowledge_ids_from_row(row)
            full_score = maps["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            full_score_value = _safe_float(full_score, 0.0)
            if full_score_value > 0:
                score_for_rate = min(max(awarded, 0.0), full_score_value)
            else:
                full_score_value = max(awarded, 0.0)
                score_for_rate = max(awarded, 0.0)
            reason = str(row.get("deduction_reason") or "").strip()
            deducted = _is_deducted(awarded, full_score, reason)

            for knowledge_id in knowledge_ids:
                # 当 knowledge_id 是 UNKNOWN 时，尝试获取该题目特定的中文标签
                if knowledge_id == "UNKNOWN":
                    label = maps["label"].get(f"{qid}_UNKNOWN")
                    # 如果找到了中文标签，则直接用该中文标签作为 knowledge_id，实现按实际知识点名称分组
                    if label and _has_chinese(label):
                        effective_kid = label
                    else:
                        effective_kid = qid
                else:
                    effective_kid = knowledge_id
                    label = maps["label"].get(effective_kid)

                if not label:
                    # 从 registry 查找标准中文名称（处理 C2_01、K1、JSSX_xxx 等各种代码格式）
                    label = _knowledge_label_from_registry(effective_kid)
                if not label:
                    label = _fallback_knowledge_label(effective_kid, qid)
                key = (row.get("student_id"), _knowledge_group_key(effective_kid, label))
                item = grouped.setdefault(
                    key,
                    {
                        "student_id": row.get("student_id"),
                        "student_code": row.get("student_code"),
                        "student_name": row.get("student_name"),
                        "class_name": row.get("class_name"),
                        "knowledge_id": effective_kid,
                        "knowledge_ids": [],
                        "knowledge_label": label,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "item_count": 0,
                        "deduction_count": 0,
                        "exam_ids": set(),
                        "reasons": set(),
                    },
                )
                if effective_kid not in item["knowledge_ids"]:
                    item["knowledge_ids"].append(effective_kid)
                item["score_sum"] += score_for_rate
                item["full_score_sum"] += full_score_value
                item["item_count"] += 1
                item["exam_ids"].add(session_id)
                if deducted:
                    item["deduction_count"] += 1
                    if _is_real_deduction_reason(reason):
                        item["reasons"].add(reason)

        result: list[dict[str, Any]] = []
        for item in grouped.values():
            item_count = max(1, int(item["item_count"]))
            full_score_sum = float(item.get("full_score_sum") or 0.0)
            score_sum = float(item.get("score_sum") or 0.0)
            weighted_score_rate = round(score_sum / full_score_sum * 100, 2) if full_score_sum > 0 else 100.0
            result.append(
                {
                    "student_id": item["student_id"],
                    "student_code": item["student_code"],
                    "student_name": item["student_name"],
                    "class_name": item["class_name"],
                    "knowledge_id": item["knowledge_id"],
                    "knowledge_ids": item["knowledge_ids"],
                    "knowledge_label": item["knowledge_label"],
                    "avg_score": round(score_sum / item_count, 2),
                    "score_sum": round(score_sum, 2),
                    "full_score_sum": round(full_score_sum, 2),
                    "weighted_score_rate": weighted_score_rate,
                    "deduction_count": int(item["deduction_count"]),
                    "item_count": item_count,
                    "exam_count": len(item["exam_ids"]),
                    "sample_reasons": "；".join(sorted(item["reasons"])),
                }
            )

        return sorted(
            result,
            key=lambda item: (
                _safe_float(item.get("weighted_score_rate"), 100.0),
                -int(item.get("deduction_count") or 0),
                str(item.get("knowledge_id") or ""),
            ),
        )

    def _build_error_point_rows(self, detail_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, str], dict[str, Any]] = {}
        rubric_cache: dict[int, dict[str, dict[str, Any]]] = {}

        for row in detail_rows:
            session_id = int(row.get("session_id") or 0)
            if session_id not in rubric_cache:
                rubric_cache[session_id] = self._load_rubric_maps_for_session(session_id)
            qid = str(row.get("question_id") or "")
            full_score = rubric_cache[session_id]["score"].get(qid)
            awarded = _safe_float(row.get("score_awarded"), 0.0)
            reason = str(row.get("deduction_reason") or "").strip()
            if not _is_deducted(awarded, full_score, reason):
                continue

            full_score_value = _safe_float(full_score, 0.0)
            if full_score_value <= 0:
                full_score_value = max(awarded, 0.0)
            category = _normalize_error_category(row.get("error_category"), reason)
            summary = str(row.get("error_summary") or "").strip() or _short_reason(reason)
            key = (row.get("student_id"), category)
            item = grouped.setdefault(
                key,
                {
                    "student_id": row.get("student_id"),
                    "student_code": row.get("student_code"),
                    "student_name": row.get("student_name"),
                    "class_name": row.get("class_name"),
                    "error_category": category,
                    "score_sum": 0.0,
                    "full_score_sum": 0.0,
                    "item_count": 0,
                    "deduction_count": 0,
                    "exam_ids": set(),
                    "summaries": set(),
                },
            )
            item["score_sum"] += min(max(awarded, 0.0), full_score_value)
            item["full_score_sum"] += full_score_value
            item["item_count"] += 1
            item["deduction_count"] += 1
            item["exam_ids"].add(session_id)
            if summary:
                item["summaries"].add(summary)

        result: list[dict[str, Any]] = []
        for item in grouped.values():
            full_score_sum = float(item.get("full_score_sum") or 0.0)
            score_sum = float(item.get("score_sum") or 0.0)
            score_rate = round(score_sum / full_score_sum * 100, 2) if full_score_sum > 0 else 0.0
            result.append(
                {
                    "student_id": item["student_id"],
                    "student_code": item["student_code"],
                    "student_name": item["student_name"],
                    "class_name": item["class_name"],
                    "error_category": item["error_category"],
                    "score_sum": round(score_sum, 2),
                    "full_score_sum": round(full_score_sum, 2),
                    "weighted_score_rate": score_rate,
                    "deduction_count": int(item["deduction_count"]),
                    "item_count": int(item["item_count"]),
                    "exam_count": len(item["exam_ids"]),
                    "sample_reasons": "；".join(sorted(item["summaries"])),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                -int(item.get("deduction_count") or 0),
                _safe_float(item.get("weighted_score_rate"), 100.0),
                str(item.get("error_category") or ""),
            ),
        )

    def _load_rubric_maps_for_session(self, session_id: int) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_rubric_map_cache"):
            self._rubric_map_cache = {}
        if session_id in self._rubric_map_cache:
            return self._rubric_map_cache[session_id]

        session = self.get_grading_session(session_id)
        score_map: dict[str, float] = {}
        label_map: dict[str, str] = {}
        knowledge_map: dict[str, list[str]] = {}
        result = {"score": score_map, "label": label_map, "knowledge": knowledge_map}

        if not session:
            self._rubric_map_cache[session_id] = result
            return result

        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            self._rubric_map_cache[session_id] = result
            return result
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            self._rubric_map_cache[session_id] = result
            return result

        questions = rubric.get("questions") if isinstance(rubric, dict) else []
        if not isinstance(questions, list):
            return {"score": score_map, "label": label_map, "knowledge": knowledge_map}
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "").strip()
            knowledge_ids = _knowledge_ids_from_question(question)
            if qid:
                score_map[qid] = _safe_float(question.get("max_score"), 0.0)
                knowledge_map[qid] = knowledge_ids
            for kid in knowledge_ids:
                lbl = _knowledge_label_from_question(kid, question)
                label_map.setdefault(kid, lbl)
                if kid == "UNKNOWN" and qid:
                    label_map[f"{qid}_UNKNOWN"] = lbl
            parts = question.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pid = str(part.get("part_id") or "").strip()
                    if pid:
                        score_map[pid] = _safe_float(part.get("part_score"), 0.0)
                        knowledge_map[pid] = _normalize_knowledge_ids(
                            part.get("knowledge_points") or part.get("knowledge_ids"),
                            part.get("knowledge_id"),
                        ) or knowledge_ids
                        for kid in knowledge_map[pid]:
                            lbl = _knowledge_label_from_question(kid, question)
                            label_map.setdefault(kid, lbl)
                            if kid == "UNKNOWN" and pid:
                                label_map[f"{pid}_UNKNOWN"] = lbl
        return {"score": score_map, "label": label_map, "knowledge": knowledge_map}

    def _load_session_rubric(self, session_id: int) -> dict[str, Any]:
        session = self.get_grading_session(session_id)
        if not session:
            return {}
        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            return {}
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return rubric if isinstance(rubric, dict) else {}

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        data_root = self.db_path.parent.parent if self.db_path.parent.name == "databases" else None
        return resolve_stored_file_path(path_value, data_root=data_root)



def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _grading_retry_attempts(raw_json: Any) -> list[dict[str, Any]]:
    parsed = _safe_json_loads(raw_json)
    attempts = parsed.get("grading_retry_attempts") if isinstance(parsed, dict) else None
    if not isinstance(attempts, list):
        return []
    return [dict(item) for item in attempts if isinstance(item, dict)]


def sanitize_incomplete_failure_summary(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"data:image/[^\s\"']+", "[图片数据已省略]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bAuthorization\b[\"']?\s*[:=]\s*[\"']?(?:(?:Bearer|Basic)\s+)?[^,\s;\"'}]+",
        "Authorization=[已隐藏认证信息]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bBearer\s+[^,\s;\"'}]+",
        "Bearer [已隐藏密钥]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])(?:[A-Za-z0-9_-]*api[_ -]?key)[\"']?\s*[:=]\s*[\"']?[^,\s;\"'}]+",
        "[API密钥已隐藏]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[已隐藏密钥]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{128,}(?![A-Za-z0-9+/=_-])", "[长数据已省略]", text)
    text = re.sub(r"\s+", " ", text).strip()
    limit = max(32, int(max_chars))
    if len(text) > limit:
        suffix = "…（内容已截断）"
        text = text[: limit - len(suffix)].rstrip() + suffix
    return text


def _last_incomplete_failure_reason(raw_json: Any, *, fallback_error: Any, completeness_status: str) -> str:
    raw_reason = ""
    attempts = _grading_retry_attempts(raw_json)
    if attempts:
        latest = attempts[-1]
        for key in ("error", "message", "reason"):
            value = str(latest.get(key) or "").strip()
            if value:
                raw_reason = value
                break

    parsed = _safe_json_loads(raw_json)
    if not raw_reason and isinstance(parsed, dict):
        legacy = parsed.get("hybrid_batch_fallback")
        if isinstance(legacy, dict):
            items = legacy.get("items") if isinstance(legacy.get("items"), list) else []
            reasons = _unique_text_list(
                [
                    item.get("reason")
                    for item in items
                    if isinstance(item, dict)
                ]
            )
            if reasons:
                raw_reason = "；".join(reasons)

    if not raw_reason:
        raw_reason = str(fallback_error or "").strip()
    safe_reason = sanitize_incomplete_failure_summary(raw_reason)
    if safe_reason:
        return safe_reason
    if completeness_status == "invalid":
        return "批改结果存在异常题目或分值，建议补跑受影响大题"
    return "批改结果缺少部分小题，建议补跑受影响大题"


def _unique_text_list(values: Any) -> list[str]:
    if isinstance(values, (str, bytes)):
        values = [values]
    elif not isinstance(values, list):
        try:
            values = list(values)
        except TypeError:
            values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _knowledge_ids_from_row(row: dict[str, Any]) -> list[str]:
    return _normalize_knowledge_ids(row.get("knowledge_ids"), row.get("knowledge_id")) or ["UNKNOWN"]


def _knowledge_ids_from_question(question: dict[str, Any]) -> list[str]:
    return _normalize_knowledge_ids(
        question.get("knowledge_points") or question.get("knowledge_ids"),
        question.get("knowledge_id"),
    ) or ["UNKNOWN"]


def _knowledge_id_filter_values(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").replace("|", ",").split(",") if part.strip()}


def _knowledge_id_matches(filter_value: str, candidate_ids: list[str]) -> bool:
    filters = _knowledge_id_filter_values(filter_value)
    return bool(filters.intersection({str(item).strip() for item in candidate_ids}))


def _knowledge_group_key(knowledge_id: str, knowledge_label: str) -> str:
    label = str(knowledge_label or "").strip()
    kid = str(knowledge_id or "").strip()
    if kid and label.startswith(kid):
        label = label[len(kid):].strip()
        for separator in ("·", "：", ":", "-", "|", " "):
            label = label.removeprefix(separator).strip()
    return label or kid or "UNKNOWN"


def _normalize_knowledge_ids(raw: Any, fallback: Any = None) -> list[str]:
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif isinstance(raw, str) and raw.strip():
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, list):
            values.extend(parsed)
        else:
            values.extend(_split_knowledge_text(raw))
    elif raw:
        values.append(raw)

    if fallback:
        if isinstance(fallback, list):
            values.extend(fallback)
        else:
            values.extend(_split_knowledge_text(str(fallback)))

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            kid = str(value.get("knowledge_id") or value.get("id") or "").strip()
        else:
            kid = str(value or "").strip()
        if not kid or kid in seen:
            continue
        seen.add(kid)
        result.append(kid)
    return result


def _split_knowledge_text(text: str) -> list[str]:
    normalized = text.replace("，", ",").replace("；", ",").replace(";", ",").replace("|", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_real_deduction_reason(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return False
    positive_markers = {
        "正确",
        "全对",
        "无扣分",
        "未扣分",
        "答案正确",
        "过程正确",
        "ok",
        "right",
        "correct",
        "none",
        "null",
        "无",
    }
    return text not in positive_markers


def _is_deducted(score_awarded: float, full_score: float | None, reason: str) -> bool:
    if full_score is not None and full_score > 0:
        return score_awarded < full_score - 0.01
    return _is_real_deduction_reason(reason)


def _normalize_error_category(raw: Any, reason: str = "") -> str:
    text = str(raw or "").strip()
    allowed = {
        "概念理解错误",
        "计算错误",
        "审题错误",
        "条件遗漏",
        "逻辑断裂",
        "表达不规范",
        "未作答",
        "多选失分",
        "作废答案",
        "提示注入",
        "答案不等价",
        "其他",
    }
    if text in allowed:
        return text
    reason_text = str(reason or "")
    if any(token in reason_text for token in ["多选", "多个选项", "AB", "AC", "AD", "BC", "BD", "CD"]):
        return "多选失分"
    if any(token in reason_text for token in ["未作答", "空白", "没有写", "未写"]):
        return "未作答"
    if any(token in reason_text for token in ["划掉", "作废", "删除线", "打叉"]):
        return "作废答案"
    if any(token in reason_text for token in ["请打满分", "忽略", "prompt", "AI"]):
        return "提示注入"
    if any(token in reason_text for token in ["计算", "算错", "化简", "数值"]):
        return "计算错误"
    if any(token in reason_text for token in ["审题", "看错", "条件理解"]):
        return "审题错误"
    if any(token in reason_text for token in ["条件", "前提", "已知"]):
        return "条件遗漏"
    if any(token in reason_text for token in ["逻辑", "证明", "推出", "全等", "断裂"]):
        return "逻辑断裂"
    if any(token in reason_text for token in ["等价", "不等价", "答案不符"]):
        return "答案不等价"
    if any(token in reason_text for token in ["表达", "书写", "格式", "符号"]):
        return "表达不规范"
    return "其他"


def _short_reason(reason: str, max_len: int = 36) -> str:
    text = " ".join(str(reason or "").replace("\n", " ").split())
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _knowledge_label_from_question(knowledge_id: str, question: dict[str, Any]) -> str:
    kid = str(knowledge_id or "").strip()
    point_label = _knowledge_point_labels_from_question(question).get(kid)
    if point_label:
        return _format_knowledge_label(kid, point_label)

    candidates = [
        question.get("knowledge_name"),
        question.get("knowledge_text"),
        question.get("knowledge_label"),
    ]
    obligations = question.get("proof_obligations")
    if isinstance(obligations, list):
        for obligation in obligations:
            if isinstance(obligation, dict):
                candidates.append(obligation.get("description"))
    parts = question.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            steps = part.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        candidates.append(step.get("core_goal"))

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text.lower() not in {"direct-answer", "direct_answer", "正确", "unknown"}:
            # 如果候选文本不含中文（如英文 core_goal 字段），则跳过，避免显示英文
            if not _has_chinese(text):
                continue
            return _format_knowledge_label(kid, text[:28])
    return _fallback_knowledge_label(knowledge_id, str(question.get("question_id") or ""))


def _knowledge_point_labels_from_question(question: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    raw_points = question.get("knowledge_points")
    if isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, dict):
                kid = str(point.get("knowledge_id") or point.get("id") or "").strip()
                label = str(
                    point.get("knowledge_name")
                    or point.get("name")
                    or point.get("knowledge_label")
                    or point.get("label")
                    or point.get("knowledge_text")
                    or ""
                ).strip()
                if kid and label:
                    labels.setdefault(kid, label)
                elif label:
                    labels.setdefault(label, label)
            else:
                for label in _split_knowledge_text(str(point or "")):
                    labels.setdefault(label, label)
    elif isinstance(raw_points, str):
        for label in _split_knowledge_text(raw_points):
            labels.setdefault(label, label)

    primary_kid = str(question.get("knowledge_id") or "").strip()
    primary_label = str(
        question.get("knowledge_name")
        or question.get("knowledge_label")
        or question.get("knowledge_text")
        or ""
    ).strip()
    if primary_kid and primary_label:
        labels.setdefault(primary_kid, primary_label)
    return labels


def _format_knowledge_label(knowledge_id: str, label: str) -> str:
    kid = str(knowledge_id or "").strip()
    text = str(label or "").strip()
    if not kid or kid == "UNKNOWN":
        return text
    if not text or text == kid:
        return kid
    return f"{kid} · {text}"


def _validated_source_binding(source_paper_path: object, source_paper_sha256: object) -> tuple[str, str]:
    source_path = str(source_paper_path or "").strip()
    source_sha256 = str(source_paper_sha256 or "").strip().lower()
    if not source_path and not source_sha256:
        return "", ""
    if not source_path or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("source paper path and full SHA-256 are required")
    return source_path, source_sha256


def _fallback_knowledge_label(knowledge_id: str, question_id: str) -> str:
    return knowledge_id


def _has_chinese(text: str) -> bool:
    """判断字符串是否含有中文字符（汉字）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _knowledge_label_from_registry(knowledge_id: str) -> str:
    """从 taxonomy registry 把知识点代码映射为标准中文名称。
    
    - 如果 knowledge_id 本身是中文（如 "等腰三角形性质"），直接返回
    - 如果是代码格式（C2_01、K1 等），在 registry 中查 alias
    - 查不到则返回空字符串（让调用方继续回退）
    """
    kid = str(knowledge_id or "").strip()
    if not kid or kid == "UNKNOWN":
        return ""
    # knowledge_id 本身是中文：直接作为标签
    if _has_chinese(kid):
        return kid
    # 通过 registry 查找
    if _registry_canonicalize is not None:
        try:
            canonical = _registry_canonicalize(kid)
            if canonical is not None:
                return canonical.canonical_name
        except Exception:
            pass
    return ""


def _normalize_name(value: str) -> str:
    return value.strip().replace(" ", "").replace("\u3000", "").replace("\u00b7", "").lower()
