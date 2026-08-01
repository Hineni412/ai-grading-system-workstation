from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from backend.schema_migrations import ensure_schema_current
from backend.workspaces.contracts import WorkspaceContext

from .errors import VaultError


class _SilentMigrationLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class OrdinaryWorkDatabase:
    """Own the non-student class-teacher store.

    Construction and reads have no filesystem side effects.  The database is
    created only by an explicit ordinary-work mutation or PIN setup command.
    """

    def __init__(self, context: WorkspaceContext) -> None:
        self.root = Path(context.root)
        self.database_path = self.root / "class_teacher_work.db"
        self.backup_dir = self.root / "work-backups"
        project_root = Path(
            getattr(context.paths, "migration_project_root", context.paths.project_root)
        )
        self.migrations_dir = project_root / "migrations" / "class_teacher_work"

    @property
    def exists(self) -> bool:
        return self.database_path.is_file()

    def initialize_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            ensure_schema_current(
                "class_teacher_work",
                self.database_path,
                migrations_dir=self.migrations_dir,
                backup_dir=self.backup_dir,
                logger_override=_SilentMigrationLogger(),
            )
        except Exception as exc:
            raise VaultError(
                "class_teacher_work_initialization_failed",
                "普通工作区初始化失败，现有数据没有改变",
                status_code=500,
            ) from exc

    def connect(self, *, create: bool = False) -> sqlite3.Connection:
        if not self.exists:
            if not create:
                raise VaultError(
                    "class_teacher_work_not_initialized",
                    "普通工作区尚未创建",
                    status_code=404,
                )
            self.initialize_schema()
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


__all__ = ["OrdinaryWorkDatabase"]
