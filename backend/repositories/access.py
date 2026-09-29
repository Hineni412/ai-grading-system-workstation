"""Named repository access for active grading callers.

GradingRepositoryAccess is an explicit container: the eight named repository
gateways plus the database lifecycle members callers need.  It performs no
attribute forwarding; every member below is a real, declared dependency.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.repositories.papers import PaperRepositoryGateway
from backend.repositories.reporting import ReportRepositoryGateway
from backend.repositories.results import ResultRepositoryGateway
from backend.repositories.review import ReviewRepositoryGateway
from backend.repositories.settings import SettingsRepositoryGateway
from backend.repositories.sessions import SessionRepositoryGateway
from backend.repositories.students import StudentRepositoryGateway
from backend.repositories.templates import TemplateRegionRepositoryGateway

if TYPE_CHECKING:
    from db_manager import DBManager


class GradingRepositoryAccess:
    """Expose grading persistence through named repository gateways."""

    def __init__(self, database: "DBManager") -> None:
        self._database = database
        self.db_path = Path(database.db_path)
        self.students: StudentRepositoryGateway = database.student_repository
        self.sessions: SessionRepositoryGateway = database.session_repository
        self.papers: PaperRepositoryGateway = database.paper_repository
        self.results: ResultRepositoryGateway = database.result_repository
        self.reviews: ReviewRepositoryGateway = database.review_repository
        self.templates: TemplateRegionRepositoryGateway = database.template_repository
        self.settings: SettingsRepositoryGateway = database.settings_repository
        self.reports: ReportRepositoryGateway = database.report_repository

    @property
    def backup_dir(self) -> Path:
        return self._database.backup_dir

    def initialize(self) -> None:
        self._database.initialize()

    def create_backup(self, reason: str, *, once_per_day: bool = False) -> Path | None:
        return self._database.create_backup(reason, once_per_day=once_per_day)

    def commit(self) -> None:
        self._database.commit()

    def rollback(self) -> None:
        self._database.rollback()

    def close(self) -> None:
        self._database.close()

    def _connect(self) -> sqlite3.Connection:
        return self._database._connect()


def as_grading_repositories(source: Any) -> GradingRepositoryAccess:
    if isinstance(source, GradingRepositoryAccess):
        return source
    return GradingRepositoryAccess(source)


__all__ = ["GradingRepositoryAccess", "as_grading_repositories"]
