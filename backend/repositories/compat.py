"""The single active composition boundary for the DBManager compatibility facade."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from backend.repositories.access import (
    GradingRepositoryAccess,
    as_grading_repositories,
)


def open_compatibility_db(
    db_path: Path,
    *,
    external_connection: sqlite3.Connection | None = None,
) -> Any:
    from db_manager import DBManager

    return DBManager(
        Path(db_path),
        external_connection=external_connection,
    )


def open_grading_repositories(
    db_path: Path,
    *,
    external_connection: sqlite3.Connection | None = None,
) -> GradingRepositoryAccess:
    return as_grading_repositories(
        open_compatibility_db(
            db_path,
            external_connection=external_connection,
        )
    )


__all__ = ["open_compatibility_db", "open_grading_repositories"]
