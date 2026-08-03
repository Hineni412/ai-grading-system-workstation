from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import VaultError


@dataclass(frozen=True, slots=True)
class ExistingStudent:
    source_key: str
    student_code: str
    display_name: str
    class_label: str


class ExistingStudentRosterSource(Protocol):
    def snapshot(self) -> tuple[list[ExistingStudent], str]: ...


class SqliteExistingStudentRosterSource:
    """Read-only adapter over the grading system's existing student library."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def snapshot(self) -> tuple[list[ExistingStudent], str]:
        if not self.database_path.is_file():
            return [], self._revision([])
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                connection.row_factory = sqlite3.Row
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='students'"
                ).fetchone()
                if exists is None:
                    return [], self._revision([])
                rows = connection.execute(
                    "SELECT id, student_code, name, class_name FROM students ORDER BY class_name, name, id"
                ).fetchall()
        except sqlite3.Error as exc:
            raise VaultError(
                "existing_student_roster_unavailable",
                "现有学生库暂时无法读取，请稍后重试",
                status_code=503,
            ) from exc
        items = [
            ExistingStudent(
                source_key=str(row["id"]),
                student_code=str(row["student_code"] or ""),
                display_name=str(row["name"] or "").strip(),
                class_label=str(row["class_name"] or "").strip(),
            )
            for row in rows
            if str(row["name"] or "").strip()
        ]
        return items, self._revision(items)

    @staticmethod
    def _revision(items: list[ExistingStudent]) -> str:
        payload = [
            [item.source_key, item.student_code, item.display_name, item.class_label]
            for item in items
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


__all__ = [
    "ExistingStudent",
    "ExistingStudentRosterSource",
    "SqliteExistingStudentRosterSource",
]
