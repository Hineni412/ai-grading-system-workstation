from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.performance.metrics import instrument_sqlite_connection
from backend.repositories import (
    BorrowedReadOnlySessionProvider,
    InstrumentedSQLiteConnectionFactory,
    RepositorySessionProvider,
)
from backend.repositories.papers import PaperRepositoryGateway
from backend.repositories.reporting import ReportRepositoryGateway
from backend.repositories.results import ResultRepositoryGateway
from backend.repositories.review import ReviewRepositoryGateway
from backend.repositories.settings import SettingsRepositoryGateway
from backend.repositories.sessions import SessionRepositoryGateway
from backend.repositories.students import (
    StudentGradingActiveError,
    StudentRecord,
    StudentRepositoryGateway,
)
from backend.repositories.templates import TemplateRegionRepositoryGateway
from backend.schema_migrations import ensure_schema_current


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
    """Compose the grading database connection and its repository gateways."""

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
        self._result_repository = ResultRepositoryGateway(
            self._repository_sessions,
            db_path=self.db_path,
        )
        self._session_repository = SessionRepositoryGateway(
            self._repository_sessions,
            db_path=self.db_path,
            create_backup=lambda reason: self.create_backup(reason),
            invalidate_rubric_maps=self._result_repository._invalidate_rubric_maps,
        )
        self._paper_repository = PaperRepositoryGateway(
            self._repository_sessions,
            results=self._result_repository,
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
        ensure_schema_current(
            "grading",
            self.db_path,
            allow_existing_migrations=False,
        )
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

    def commit(self) -> None:
        """No persistent connection is owned; borrowed connections stay with their owner."""
        return None

    def rollback(self) -> None:
        """No persistent connection is owned; borrowed connections stay with their owner."""
        return None

    def close(self) -> None:
        """No persistent connection is owned; borrowed connections stay with their owner."""
        return None
