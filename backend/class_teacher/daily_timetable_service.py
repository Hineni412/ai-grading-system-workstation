from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from .errors import VaultError
from .ordinary_database import OrdinaryWorkDatabase


_LESSON_SLOT = re.compile(r"lesson:([1-8])")
_CUSTOM_SLOT = re.compile(r"custom:([0-9a-f]{32})")
_LESSON_COUNT = 8
_DAY_COUNT = 5


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


class DailyTimetableService:
    """动态课表：周锚定、常规课表、当周临时覆盖、自定义时段与进度笔记。

    数据存普通工作库（class_teacher_work.db），全部为本机明文。
    常规课表按星期几复用；临时覆盖只对某一整周（week_start=周一）生效，
    下一周自动恢复常规。
    """

    def __init__(self, database: OrdinaryWorkDatabase) -> None:
        self.database = database

    # ------------------------------------------------------------------
    # 校验

    @staticmethod
    def _parse_date(value: object, *, label: str) -> date:
        try:
            return date.fromisoformat(str(value or "").strip())
        except ValueError:
            raise VaultError(
                "daily_timetable_date_invalid",
                f"{label}格式无效，请使用 YYYY-MM-DD",
                status_code=422,
            ) from None

    @staticmethod
    def _validate_day(day_of_week: object) -> int:
        try:
            day = int(day_of_week)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            day = 0
        if day < 1 or day > _DAY_COUNT:
            raise VaultError(
                "daily_timetable_day_invalid",
                "星期必须是 1 到 5",
                status_code=422,
            )
        return day

    @staticmethod
    def _validated_text(value: object, *, max_length: int, label: str) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise VaultError(
                "daily_timetable_text_too_long",
                f"{label}不能超过 {max_length} 个字符",
                status_code=422,
            )
        return text

    @staticmethod
    def _slot_key(connection: sqlite3.Connection, slot_key: object) -> str:
        key = str(slot_key or "").strip()
        if _LESSON_SLOT.fullmatch(key) is not None:
            return key
        custom = _CUSTOM_SLOT.fullmatch(key)
        if custom is not None:
            exists = connection.execute(
                "SELECT 1 FROM daily_custom_slots WHERE id = ?",
                (custom.group(1),),
            ).fetchone()
            if exists is not None:
                return key
            raise VaultError(
                "daily_timetable_slot_invalid",
                "该自定义时段已不存在，请刷新后重试",
                status_code=422,
            )
        raise VaultError(
            "daily_timetable_slot_invalid",
            "课节标识无效",
            status_code=422,
        )

    # ------------------------------------------------------------------
    # 周视图

    def get_week(
        self,
        week_start: str | None = None,
        *,
        today: date | None = None,
    ) -> dict[str, object]:
        current = today or date.today()
        if week_start is None or not str(week_start).strip():
            monday = _monday_of(current)
        else:
            monday = _monday_of(self._parse_date(week_start, label="周起始日期"))
        anchor: dict[str, object] | None = None
        custom_rows: list[sqlite3.Row] = []
        regular_rows: list[sqlite3.Row] = []
        override_rows: list[sqlite3.Row] = []
        if self.database.exists:
            with closing(self.database.connect()) as connection:
                anchor_row = connection.execute(
                    """
                    SELECT anchor_monday, anchor_week_no, updated_at
                    FROM daily_week_anchor WHERE singleton = 1
                    """
                ).fetchone()
                if anchor_row is not None:
                    anchor = {
                        "anchor_monday": str(anchor_row["anchor_monday"]),
                        "week_no": int(anchor_row["anchor_week_no"]),
                        "updated_at": str(anchor_row["updated_at"]),
                    }
                custom_rows = connection.execute(
                    """
                    SELECT id, label, start_text, end_text, position, created_at
                    FROM daily_custom_slots
                    ORDER BY position, created_at, rowid
                    """
                ).fetchall()
                regular_rows = connection.execute(
                    """
                    SELECT day_of_week, slot_key, course_text, class_label
                    FROM daily_regular_entries
                    """
                ).fetchall()
                override_rows = connection.execute(
                    """
                    SELECT id, day_of_week, slot_key, action,
                           course_text, class_label, note
                    FROM daily_overrides
                    WHERE week_start = ?
                    ORDER BY created_at, rowid
                    """,
                    (monday.isoformat(),),
                ).fetchall()
        slots = self._slot_views(custom_rows)
        week_no: int | None = None
        if anchor is not None:
            anchor_monday = date.fromisoformat(str(anchor["anchor_monday"]))
            week_no = int(anchor["week_no"]) + (monday - anchor_monday).days // 7
        merged: dict[tuple[int, str], dict[str, object]] = {}
        for row in regular_rows:
            day = int(row["day_of_week"])
            key = str(row["slot_key"])
            merged[(day, key)] = {
                "day_of_week": day,
                "slot_key": key,
                "course_text": str(row["course_text"]),
                "class_label": str(row["class_label"]),
                "source": "regular",
                "override_id": None,
                "note": None,
            }
        for row in override_rows:
            day = int(row["day_of_week"])
            key = str(row["slot_key"])
            action = str(row["action"])
            merged[(day, key)] = {
                "day_of_week": day,
                "slot_key": key,
                "course_text": str(row["course_text"] or "") if action == "set" else "",
                "class_label": str(row["class_label"] or "") if action == "set" else "",
                "source": "override",
                "override_id": str(row["id"]),
                "note": row["note"],
            }
        cells: list[dict[str, object]] = []
        for day in range(1, _DAY_COUNT + 1):
            for slot in slots:
                cell = merged.get((day, str(slot["slot_key"])))
                if cell is None:
                    cell = {
                        "day_of_week": day,
                        "slot_key": str(slot["slot_key"]),
                        "course_text": "",
                        "class_label": "",
                        "source": "empty",
                        "override_id": None,
                        "note": None,
                    }
                cells.append(cell)
        return {
            "week_start": monday.isoformat(),
            "week_no": week_no,
            "days": [
                {
                    "day_of_week": offset + 1,
                    "date": (monday + timedelta(days=offset)).isoformat(),
                }
                for offset in range(_DAY_COUNT)
            ],
            "slots": slots,
            "cells": cells,
            "today": {
                "date": current.isoformat(),
                "day_of_week": current.isoweekday(),
                "in_week": monday <= current <= monday + timedelta(days=_DAY_COUNT - 1),
            },
        }

    @staticmethod
    def _slot_views(custom_rows: list[sqlite3.Row]) -> list[dict[str, object]]:
        by_position: dict[int, list[sqlite3.Row]] = {}
        for row in custom_rows:
            by_position.setdefault(int(row["position"]), []).append(row)
        slots: list[dict[str, object]] = []
        for position in range(_LESSON_COUNT + 1):
            for row in by_position.get(position, []):
                slots.append(
                    {
                        "slot_key": f"custom:{row['id']}",
                        "kind": "custom",
                        "custom_slot_id": str(row["id"]),
                        "label": str(row["label"]),
                        "start_text": row["start_text"],
                        "end_text": row["end_text"],
                        "position": position,
                        "lesson_no": None,
                    }
                )
            if position < _LESSON_COUNT:
                lesson_no = position + 1
                slots.append(
                    {
                        "slot_key": f"lesson:{lesson_no}",
                        "kind": "lesson",
                        "custom_slot_id": None,
                        "label": f"第{lesson_no}节",
                        "start_text": None,
                        "end_text": None,
                        "position": None,
                        "lesson_no": lesson_no,
                    }
                )
        return slots

    # ------------------------------------------------------------------
    # 常规课表

    def set_regular_cell(
        self,
        *,
        day_of_week: object,
        slot_key: object,
        course_text: object,
        class_label: object = "",
    ) -> dict[str, object]:
        day = self._validate_day(day_of_week)
        course = self._validated_text(course_text, max_length=50, label="课程内容")
        label = self._validated_text(class_label, max_length=50, label="班级")
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                key = self._slot_key(connection, slot_key)
                if not course:
                    connection.execute(
                        """
                        DELETE FROM daily_regular_entries
                        WHERE day_of_week = ? AND slot_key = ?
                        """,
                        (day, key),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO daily_regular_entries
                            (day_of_week, slot_key, course_text, class_label, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(day_of_week, slot_key) DO UPDATE SET
                            course_text = excluded.course_text,
                            class_label = excluded.class_label,
                            updated_at = excluded.updated_at
                        """,
                        (day, key, course, label, _iso()),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "day_of_week": day,
            "slot_key": key,
            "course_text": course,
            "class_label": label if course else "",
            "source": "regular" if course else "empty",
            "override_id": None,
            "note": None,
        }

    # ------------------------------------------------------------------
    # 自定义时段

    def create_custom_slot(
        self,
        *,
        label: object,
        start_text: object = None,
        end_text: object = None,
        position: object,
    ) -> dict[str, object]:
        name = self._validated_text(label, max_length=20, label="时段名称")
        if not name:
            raise VaultError(
                "daily_timetable_label_required",
                "时段名称不能为空",
                status_code=422,
            )
        start = self._validated_text(start_text, max_length=20, label="开始时间")
        end = self._validated_text(end_text, max_length=20, label="结束时间")
        try:
            slot_position = int(position)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            slot_position = -1
        if slot_position < 0 or slot_position > _LESSON_COUNT:
            raise VaultError(
                "daily_timetable_position_invalid",
                "时段位置必须是 0 到 8",
                status_code=422,
            )
        slot_id = uuid4().hex
        created_at = _iso()
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO daily_custom_slots
                        (id, label, start_text, end_text, position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (slot_id, name, start or None, end or None, slot_position, created_at),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "slot_key": f"custom:{slot_id}",
            "kind": "custom",
            "custom_slot_id": slot_id,
            "label": name,
            "start_text": start or None,
            "end_text": end or None,
            "position": slot_position,
            "lesson_no": None,
            "created_at": created_at,
        }

    def delete_custom_slot(self, custom_slot_id: object) -> dict[str, object]:
        slot_id = str(custom_slot_id or "").strip()
        if not self.database.exists:
            raise VaultError(
                "daily_timetable_custom_slot_not_found",
                "该自定义时段已不存在，请刷新后重试",
                status_code=404,
            )
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT id FROM daily_custom_slots WHERE id = ?",
                    (slot_id,),
                ).fetchone()
                if existing is None:
                    raise VaultError(
                        "daily_timetable_custom_slot_not_found",
                        "该自定义时段已不存在，请刷新后重试",
                        status_code=404,
                    )
                slot_key = f"custom:{slot_id}"
                removed_regular = connection.execute(
                    "DELETE FROM daily_regular_entries WHERE slot_key = ?",
                    (slot_key,),
                ).rowcount
                removed_overrides = connection.execute(
                    "DELETE FROM daily_overrides WHERE slot_key = ?",
                    (slot_key,),
                ).rowcount
                connection.execute(
                    "DELETE FROM daily_custom_slots WHERE id = ?",
                    (slot_id,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "deleted_custom_slot_id": slot_id,
            "removed_regular_cells": int(removed_regular),
            "removed_overrides": int(removed_overrides),
        }

    # ------------------------------------------------------------------
    # 当周临时覆盖

    def apply_overrides(
        self,
        *,
        week_start: object,
        ops: list[dict[str, object]],
    ) -> dict[str, object]:
        monday = _monday_of(self._parse_date(week_start, label="周起始日期"))
        if not isinstance(ops, list) or not ops:
            raise VaultError(
                "daily_timetable_ops_required",
                "至少需要一条临时调整",
                status_code=422,
            )
        week = monday.isoformat()
        applied: list[dict[str, object]] = []
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for op in ops:
                    applied.append(self._apply_override_op(connection, week, op))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"week_start": week, "overrides": applied}

    def _apply_override_op(
        self,
        connection: sqlite3.Connection,
        week: str,
        op: dict[str, object],
    ) -> dict[str, object]:
        day = self._validate_day(op.get("day_of_week"))
        key = self._slot_key(connection, op.get("slot_key"))
        action = str(op.get("action") or "").strip()
        if action not in {"set", "clear"}:
            raise VaultError(
                "daily_timetable_action_invalid",
                "调整类型必须是 set 或 clear",
                status_code=422,
            )
        course = self._validated_text(op.get("course_text"), max_length=50, label="课程内容")
        label = self._validated_text(op.get("class_label"), max_length=50, label="班级")
        note = self._validated_text(op.get("note"), max_length=500, label="备注")
        if action == "set" and not course:
            raise VaultError(
                "daily_timetable_course_required",
                "临时课程内容不能为空",
                status_code=422,
            )
        # 同一 (week_start, day, slot) 后写覆盖先写：先删旧覆盖再插入。
        connection.execute(
            """
            DELETE FROM daily_overrides
            WHERE week_start = ? AND day_of_week = ? AND slot_key = ?
            """,
            (week, day, key),
        )
        override_id = uuid4().hex
        created_at = _iso()
        connection.execute(
            """
            INSERT INTO daily_overrides
                (id, week_start, day_of_week, slot_key, action,
                 course_text, class_label, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                override_id,
                week,
                day,
                key,
                action,
                course if action == "set" else None,
                label if action == "set" else None,
                note or None,
                created_at,
            ),
        )
        return {
            "override_id": override_id,
            "week_start": week,
            "day_of_week": day,
            "slot_key": key,
            "action": action,
            "course_text": course if action == "set" else "",
            "class_label": label if action == "set" else "",
            "note": note or None,
            "created_at": created_at,
        }

    def delete_override(self, override_id: object) -> dict[str, object]:
        target = str(override_id or "").strip()
        if not self.database.exists:
            raise VaultError(
                "daily_timetable_override_not_found",
                "这条临时调整已不存在，请刷新后重试",
                status_code=404,
            )
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                removed = connection.execute(
                    "DELETE FROM daily_overrides WHERE id = ?",
                    (target,),
                ).rowcount
                if not removed:
                    raise VaultError(
                        "daily_timetable_override_not_found",
                        "这条临时调整已不存在，请刷新后重试",
                        status_code=404,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"deleted_override_id": target}

    # ------------------------------------------------------------------
    # 周锚定

    def get_week_anchor(self) -> dict[str, object] | None:
        if not self.database.exists:
            return None
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT anchor_monday, anchor_week_no, updated_at
                FROM daily_week_anchor WHERE singleton = 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "anchor_monday": str(row["anchor_monday"]),
            "week_no": int(row["anchor_week_no"]),
            "updated_at": str(row["updated_at"]),
        }

    def set_week_anchor(
        self,
        *,
        anchor_date: object,
        week_no: object,
    ) -> dict[str, object]:
        monday = _monday_of(self._parse_date(anchor_date, label="锚定日期"))
        try:
            number = int(week_no)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            number = 0
        if number < 1 or number > 40:
            raise VaultError(
                "daily_timetable_week_no_invalid",
                "周次必须是 1 到 40 之间的数字",
                status_code=422,
            )
        updated_at = _iso()
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO daily_week_anchor
                        (singleton, anchor_monday, anchor_week_no, updated_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        anchor_monday = excluded.anchor_monday,
                        anchor_week_no = excluded.anchor_week_no,
                        updated_at = excluded.updated_at
                    """,
                    (monday.isoformat(), number, updated_at),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "anchor_monday": monday.isoformat(),
            "week_no": number,
            "updated_at": updated_at,
        }

    # ------------------------------------------------------------------
    # 进度笔记

    def add_note(
        self,
        *,
        note_date: object,
        slot_key: object,
        class_label: object,
        content_text: object,
        homework_text: object = None,
    ) -> dict[str, object]:
        day = self._parse_date(note_date, label="笔记日期")
        label = self._validated_text(class_label, max_length=50, label="班级")
        if not label:
            raise VaultError(
                "daily_timetable_class_required",
                "班级不能为空",
                status_code=422,
            )
        content = self._validated_text(content_text, max_length=500, label="内容")
        if not content:
            raise VaultError(
                "daily_timetable_content_required",
                "内容不能为空",
                status_code=422,
            )
        homework = self._validated_text(homework_text, max_length=500, label="作业")
        note_id = uuid4().hex
        created_at = _iso()
        with closing(self.database.connect(create=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                key = self._slot_key(connection, slot_key)
                connection.execute(
                    """
                    INSERT INTO daily_lesson_notes
                        (id, note_date, slot_key, class_label,
                         content_text, homework_text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (note_id, day.isoformat(), key, label, content, homework or None, created_at),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "id": note_id,
            "note_date": day.isoformat(),
            "slot_key": key,
            "class_label": label,
            "content_text": content,
            "homework_text": homework or None,
            "created_at": created_at,
        }

    def recent_notes(
        self,
        *,
        class_labels: list[str] | None,
        limit: object = 10,
    ) -> dict[str, object]:
        labels: list[str] = []
        for value in class_labels or []:
            text = str(value or "").strip()
            if text and text not in labels:
                labels.append(text)
        try:
            cap = int(limit)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            cap = 10
        cap = max(1, min(cap, 50))
        groups: list[dict[str, object]] = []
        if labels and self.database.exists:
            with closing(self.database.connect()) as connection:
                for label in labels:
                    rows = connection.execute(
                        """
                        SELECT id, note_date, slot_key, class_label,
                               content_text, homework_text, created_at
                        FROM daily_lesson_notes
                        WHERE class_label = ?
                        ORDER BY note_date DESC, created_at DESC, rowid DESC
                        LIMIT ?
                        """,
                        (label, cap),
                    ).fetchall()
                    groups.append(
                        {
                            "class_label": label,
                            "notes": [dict(row) for row in rows],
                        }
                    )
        else:
            groups = [{"class_label": label, "notes": []} for label in labels]
        return {"classes": groups}


__all__ = ["DailyTimetableService"]
