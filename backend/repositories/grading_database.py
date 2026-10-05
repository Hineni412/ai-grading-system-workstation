"""Open the grading database as an explicit named-repository container."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.repositories.access import GradingRepositoryAccess


def open_grading_repositories(
    db_path: Path,
    *,
    external_connection: sqlite3.Connection | None = None,
) -> GradingRepositoryAccess:
    from backend.repositories.db_manager import DBManager

    return GradingRepositoryAccess(
        DBManager(
            Path(db_path),
            external_connection=external_connection,
        )
    )


__all__ = ["open_grading_repositories"]
