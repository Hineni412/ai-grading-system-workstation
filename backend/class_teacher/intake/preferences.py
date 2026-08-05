from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime

from ..errors import VaultError
from ..existing_student_roster import ExistingStudentRosterSource
from ..ordinary_database import OrdinaryWorkDatabase


_OPERATION = re.compile(r"[A-Za-z0-9_-]{8,128}")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class HomeroomPreference:
    KEY = "homeroom_class"

    def __init__(self, database: OrdinaryWorkDatabase, roster_source: ExistingStudentRosterSource) -> None:
        self.database = database
        self.roster_source = roster_source

    def get(self) -> dict[str, object]:
        items, source_revision = self.roster_source.snapshot()
        classes = sorted({item.class_label for item in items if item.class_label})
        if not self.database.exists:
            return {"homeroom_class": None, "revision": 0, "classes": classes, "source_revision": source_revision}
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT value_json, revision, updated_at FROM class_teacher_preferences WHERE preference_key=?",
                (self.KEY,),
            ).fetchone()
        if row is None:
            return {"homeroom_class": None, "revision": 0, "classes": classes, "source_revision": source_revision}
        payload = json.loads(str(row["value_json"]))
        return {
            "homeroom_class": payload.get("homeroom_class"),
            "revision": int(row["revision"]),
            "updated_at": str(row["updated_at"]),
            "classes": classes,
            "source_revision": source_revision,
        }

    def set(
        self,
        *,
        homeroom_class: str | None,
        expected_revision: int,
        expected_source_revision: str,
        operation_id: str,
    ) -> dict[str, object]:
        if _OPERATION.fullmatch(str(operation_id or "")) is None:
            raise VaultError("class_teacher_identifier_invalid", "操作编号无效", status_code=422)
        selected = str(homeroom_class or "").strip()
        if len(selected) > 240:
            raise VaultError("class_teacher_homeroom_invalid", "班级名称过长", status_code=422)
        items, source_revision = self.roster_source.snapshot()
        if source_revision != str(expected_source_revision or ""):
            raise VaultError("class_teacher_roster_source_changed", "学生库已经变化，请刷新后重新选择", status_code=409)
        if selected and selected not in {item.class_label for item in items}:
            raise VaultError("class_teacher_homeroom_invalid", "所选班级已不在学生库中", status_code=422)
        timestamp = _iso()
        payload = json.dumps({"homeroom_class": selected or None}, ensure_ascii=False, separators=(",", ":"))
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT result_json FROM work_operations WHERE operation_id=? AND operation_type='class_teacher.preference.homeroom'",
                    (operation_id,),
                ).fetchone()
                if replay is not None:
                    saved = json.loads(str(replay["result_json"]))
                    if saved.get("homeroom_class") != (selected or None):
                        raise VaultError("class_teacher_operation_conflict", "同一操作编号对应了不同班级", status_code=409)
                    connection.rollback()
                    return self.get()
                current = connection.execute(
                    "SELECT revision FROM class_teacher_preferences WHERE preference_key=?",
                    (self.KEY,),
                ).fetchone()
                current_revision = int(current["revision"]) if current else 0
                if current_revision != int(expected_revision):
                    raise VaultError("class_teacher_homeroom_conflict", "班主任班级已在其他页面更新，请刷新后继续", status_code=409)
                next_revision = current_revision + 1
                connection.execute(
                    """
                    INSERT INTO class_teacher_preferences (preference_key, value_json, revision, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(preference_key) DO UPDATE SET
                        value_json=excluded.value_json,
                        revision=excluded.revision,
                        updated_at=excluded.updated_at
                    """,
                    (self.KEY, payload, next_revision, timestamp),
                )
                connection.execute(
                    "INSERT INTO work_operations (operation_id, operation_type, result_json, created_at) VALUES (?, 'class_teacher.preference.homeroom', ?, ?)",
                    (operation_id, payload, timestamp),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get()


__all__ = ["HomeroomPreference"]
