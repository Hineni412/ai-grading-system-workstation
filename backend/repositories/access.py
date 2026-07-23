"""Named repository access for active grading callers.

The compatibility source is deliberately kept behind this boundary.  Active
services consume the named repositories below; methods that have not yet been
assigned to one repository remain available only as logged compatibility
operations for the one-release DBManager transition window.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from backend.repositories.papers import PaperRepositoryGateway
from backend.repositories.results import ResultRepositoryGateway
from backend.repositories.review import ReviewRepositoryGateway
from backend.repositories.settings import SettingsRepositoryGateway
from backend.repositories.sessions import SessionRepositoryGateway
from backend.repositories.students import StudentRepositoryGateway
from backend.repositories.templates import TemplateRegionRepositoryGateway


logger = logging.getLogger(__name__)
_COMPATIBILITY_LOG_GUARD = threading.Lock()
_LOGGED_COMPATIBILITY_OPERATIONS: set[str] = set()


class GradingRepositoryAccess:
    """Expose grading persistence through named repository gateways."""

    def __init__(self, compatibility_source: Any) -> None:
        self._compatibility_source = compatibility_source
        self.db_path = Path(getattr(compatibility_source, "db_path", "."))
        self.students: StudentRepositoryGateway = (
            getattr(compatibility_source, "student_repository", compatibility_source)
        )
        self.sessions: SessionRepositoryGateway = (
            getattr(compatibility_source, "session_repository", compatibility_source)
        )
        self.papers: PaperRepositoryGateway = getattr(
            compatibility_source,
            "paper_repository",
            compatibility_source,
        )
        self.results: ResultRepositoryGateway = (
            getattr(compatibility_source, "result_repository", compatibility_source)
        )
        self.reviews: ReviewRepositoryGateway = (
            getattr(compatibility_source, "review_repository", compatibility_source)
        )
        self.templates: TemplateRegionRepositoryGateway = (
            getattr(compatibility_source, "template_repository", compatibility_source)
        )
        self.settings: SettingsRepositoryGateway = (
            getattr(compatibility_source, "settings_repository", compatibility_source)
        )
        repositories = [
            self.students,
            self.sessions,
            self.papers,
            self.results,
            self.reviews,
            self.templates,
            self.settings,
        ]
        self._named_repositories = tuple(
            repository
            for index, repository in enumerate(repositories)
            if all(repository is not prior for prior in repositories[:index])
        )

    @property
    def student_repository(self) -> StudentRepositoryGateway:
        return self.students

    @property
    def session_repository(self) -> SessionRepositoryGateway:
        return self.sessions

    @property
    def paper_repository(self) -> PaperRepositoryGateway:
        return self.papers

    @property
    def result_repository(self) -> ResultRepositoryGateway:
        return self.results

    @property
    def review_repository(self) -> ReviewRepositoryGateway:
        return self.reviews

    @property
    def template_repository(self) -> TemplateRegionRepositoryGateway:
        return self.templates

    @property
    def settings_repository(self) -> SettingsRepositoryGateway:
        return self.settings

    def __getattr__(self, name: str) -> Any:
        instance_overrides = getattr(self._compatibility_source, "__dict__", {})
        if name in instance_overrides:
            _log_compatibility_operation(name)
            return instance_overrides[name]

        matches = [
            getattr(repository, name)
            for repository in self._named_repositories
            if hasattr(repository, name)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AttributeError(
                f"repository operation {name!r} is ambiguous; use a named repository"
            )

        operation = getattr(self._compatibility_source, name)
        _log_compatibility_operation(name)
        return operation


def as_grading_repositories(source: Any) -> GradingRepositoryAccess:
    if isinstance(source, GradingRepositoryAccess):
        return source
    return GradingRepositoryAccess(source)


def _log_compatibility_operation(name: str) -> None:
    with _COMPATIBILITY_LOG_GUARD:
        if name in _LOGGED_COMPATIBILITY_OPERATIONS:
            return
        _LOGGED_COMPATIBILITY_OPERATIONS.add(name)
    logger.debug(
        "grading repository compatibility operation used",
        extra={"repository_compatibility_operation": name},
    )


__all__ = ["GradingRepositoryAccess", "as_grading_repositories"]
