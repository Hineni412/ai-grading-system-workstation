from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from typing import Callable
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .existing_student_roster import ExistingStudentRosterSource
from .roster_ref import student_stable_ref


_COLUMN_TYPES = {"text", "number", "check", "select", "date"}
_DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
# CSV 防公式注入：以这些字符开头的单元格值前加单引号。
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _iso() -> str:
    return datetime.now(UTC).isoformat()


class DailyTableService:
    """日常管理-表格管理：以学生名单快照为行的自定义表格。

    数据存 student_affairs.db（班主任工作区本机明文）。建表时从只读
    名单源按所选班级快照生成行，之后名单变动不回写已建表格；单元格
    稀疏存储，空值即删除单元格行。
    """

    def __init__(
        self,
        database: EncryptedDatabase,
        key_provider: Callable[[str], bytes],
        roster_source: ExistingStudentRosterSource,
    ) -> None:
        self.database = database
        self._key_provider = key_provider
        self.roster_source = roster_source

    # ------------------------------------------------------------------
    # 校验

    @staticmethod
    def _validated_title(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise VaultError(
                "daily_table_title_required",
                "表格名称不能为空",
                status_code=422,
            )
        if len(text) > 50:
            raise VaultError(
                "daily_table_title_too_long",
                "表格名称不能超过 50 个字符",
                status_code=422,
            )
        return text

    @staticmethod
    def _validated_class_labels(value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise VaultError(
                "daily_table_class_labels_invalid",
                "班级列表格式无效",
                status_code=422,
            )
        labels: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            if len(text) > 50:
                raise VaultError(
                    "daily_table_class_label_too_long",
                    "班级名称不能超过 50 个字符",
                    status_code=422,
                )
            if text not in labels:
                labels.append(text)
        return labels

    @staticmethod
    def _validated_column_name(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise VaultError(
                "daily_table_column_name_required",
                "列名不能为空",
                status_code=422,
            )
        if len(text) > 30:
            raise VaultError(
                "daily_table_column_name_too_long",
                "列名不能超过 30 个字符",
                status_code=422,
            )
        return text

    @staticmethod
    def _validated_col_type(value: object) -> str:
        col_type = str(value or "").strip()
        if col_type not in _COLUMN_TYPES:
            raise VaultError(
                "daily_table_column_type_invalid",
                "列类型必须是 text、number、check、select、date 之一",
                status_code=422,
            )
        return col_type

    @staticmethod
    def _validated_options(value: object, col_type: str) -> list[str] | None:
        if col_type != "select":
            return None
        if value is None:
            return []
        if not isinstance(value, list):
            raise VaultError(
                "daily_table_options_invalid",
                "选项列表格式无效",
                status_code=422,
            )
        options: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            if len(text) > 30:
                raise VaultError(
                    "daily_table_option_too_long",
                    "每个选项不能超过 30 个字符",
                    status_code=422,
                )
            if text not in options:
                options.append(text)
        if len(options) > 20:
            raise VaultError(
                "daily_table_options_too_many",
                "选项最多 20 项",
                status_code=422,
            )
        return options

    def _validated_column_spec(self, spec: object) -> dict[str, object]:
        if not isinstance(spec, dict):
            raise VaultError(
                "daily_table_column_invalid",
                "列定义格式无效",
                status_code=422,
            )
        name = self._validated_column_name(spec.get("name"))
        col_type = self._validated_col_type(spec.get("col_type"))
        return {
            "name": name,
            "col_type": col_type,
            "options": self._validated_options(spec.get("options"), col_type),
        }

    @staticmethod
    def _validated_cell_value(
        col_type: str,
        options: list[str],
        value: object,
    ) -> str:
        """按列类型校验单元格值；返回规范化文本，空串表示删除单元格。"""
        text = str(value or "").strip()
        if len(text) > 500:
            raise VaultError(
                "daily_table_value_too_long",
                "单元格内容不能超过 500 个字符",
                status_code=422,
            )
        if not text:
            return ""
        if col_type == "check":
            if text not in {"0", "1"}:
                raise VaultError(
                    "daily_table_value_invalid",
                    "勾选列只能填 0 或 1",
                    status_code=422,
                )
            return text
        if col_type == "number":
            try:
                number = float(text)
            except ValueError:
                number = math.nan
            if not math.isfinite(number):
                raise VaultError(
                    "daily_table_value_invalid",
                    "数字列只能填写数字",
                    status_code=422,
                )
            return text
        if col_type == "date":
            valid = _DATE_TEXT.fullmatch(text) is not None
            if valid:
                try:
                    date.fromisoformat(text)
                except ValueError:
                    valid = False
            if not valid:
                raise VaultError(
                    "daily_table_value_invalid",
                    "日期列请使用 YYYY-MM-DD 格式",
                    status_code=422,
                )
            return text
        if col_type == "select":
            if text not in options:
                raise VaultError(
                    "daily_table_value_invalid",
                    "单选列的值不在可选范围内",
                    status_code=422,
                )
            return text
        return text

    # ------------------------------------------------------------------
    # 名单班级（只读快照）

    def roster_classes(self) -> dict[str, object]:
        items, _revision = self.roster_source.snapshot()
        counts: dict[str, int] = {}
        for item in items:
            label = str(item.class_label or "").strip()
            if label:
                counts[label] = counts.get(label, 0) + 1
        return {
            "classes": [
                {"class_label": label, "student_count": counts[label]}
                for label in sorted(counts)
            ]
        }

    # ------------------------------------------------------------------
    # 表格

    def list_tables(self, *, status: object = "all") -> dict[str, object]:
        normalized = str(status or "all").strip()
        if normalized not in {"all", "active", "archived"}:
            raise VaultError(
                "daily_table_status_invalid",
                "表格状态筛选只能是 active、archived 或 all",
                status_code=422,
            )
        if not self.database.exists:
            return {"tables": []}
        query = """
            SELECT t.id, t.title, t.status, t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM daily_table_columns c
                     WHERE c.table_id = t.id) AS column_count,
                   (SELECT COUNT(*) FROM daily_table_rows r
                     WHERE r.table_id = t.id) AS row_count
            FROM daily_tables t
        """
        params: tuple[str, ...] = ()
        if normalized != "all":
            query += " WHERE t.status = ?"
            params = (normalized,)
        query += " ORDER BY t.updated_at DESC, t.id"
        with closing(self.database.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return {
            "tables": [
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "status": str(row["status"]),
                    "column_count": int(row["column_count"]),
                    "row_count": int(row["row_count"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
                for row in rows
            ]
        }

    def create_table(
        self,
        *,
        title: object,
        class_labels: object,
        columns: object = None,
    ) -> dict[str, object]:
        name = self._validated_title(title)
        labels = self._validated_class_labels(class_labels)
        specs = [self._validated_column_spec(spec) for spec in (columns or [])]
        items, _revision = self.roster_source.snapshot()
        selected = [
            item for item in items if str(item.class_label or "").strip() in labels
        ]
        present = {str(item.class_label or "").strip() for item in selected}
        empty_labels = [label for label in labels if label not in present]
        table_id = uuid4().hex
        now = _iso()
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO daily_tables (id, title, status, created_at, updated_at)
                    VALUES (?, ?, 'active', ?, ?)
                    """,
                    (table_id, name, now, now),
                )
                for position, spec in enumerate(specs):
                    connection.execute(
                        """
                        INSERT INTO daily_table_columns
                            (id, table_id, name, col_type, options_json, position)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            table_id,
                            str(spec["name"]),
                            str(spec["col_type"]),
                            json.dumps(spec["options"], ensure_ascii=False)
                            if spec["options"] is not None
                            else None,
                            position,
                        ),
                    )
                for position, item in enumerate(selected):
                    connection.execute(
                        """
                        INSERT INTO daily_table_rows
                            (id, table_id, student_ref, display_name,
                             class_label, position)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            table_id,
                            student_stable_ref(
                                class_label=item.class_label,
                                student_code=item.student_code,
                                display_name=item.display_name,
                            ),
                            str(item.display_name or "").strip(),
                            str(item.class_label or "").strip(),
                            position,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        detail = self.get_table(table_id)
        detail["empty_class_labels"] = empty_labels
        return detail

    def get_table(self, table_id: object) -> dict[str, object]:
        target = str(table_id or "").strip()
        if not self.database.exists:
            raise self._table_not_found()
        with closing(self.database.connect()) as connection:
            table = connection.execute(
                """
                SELECT id, title, status, created_at, updated_at
                FROM daily_tables WHERE id = ?
                """,
                (target,),
            ).fetchone()
            if table is None:
                raise self._table_not_found()
            column_rows = connection.execute(
                """
                SELECT id, name, col_type, options_json, position
                FROM daily_table_columns
                WHERE table_id = ?
                ORDER BY position, rowid
                """,
                (target,),
            ).fetchall()
            student_rows = connection.execute(
                """
                SELECT id, student_ref, display_name, class_label, position
                FROM daily_table_rows
                WHERE table_id = ?
                ORDER BY position, rowid
                """,
                (target,),
            ).fetchall()
            cell_rows = connection.execute(
                """
                SELECT row_id, column_id, value_text
                FROM daily_table_cells
                WHERE table_id = ?
                """,
                (target,),
            ).fetchall()
        cells: dict[str, dict[str, str]] = {}
        for cell in cell_rows:
            cells.setdefault(str(cell["row_id"]), {})[str(cell["column_id"])] = str(
                cell["value_text"]
            )
        return {
            "table": {
                "id": str(table["id"]),
                "title": str(table["title"]),
                "status": str(table["status"]),
                "created_at": str(table["created_at"]),
                "updated_at": str(table["updated_at"]),
            },
            "columns": [self._column_view(row) for row in column_rows],
            "rows": [
                {
                    "id": str(row["id"]),
                    "student_ref": str(row["student_ref"]),
                    "display_name": str(row["display_name"]),
                    "class_label": str(row["class_label"]),
                    "position": int(row["position"]),
                }
                for row in student_rows
            ],
            "cells": cells,
        }

    def update_table(
        self,
        table_id: object,
        *,
        title: object = None,
        status: object = None,
    ) -> dict[str, object]:
        target = str(table_id or "").strip()
        new_title = self._validated_title(title) if title is not None else None
        new_status: str | None = None
        if status is not None:
            normalized = str(status or "").strip()
            if normalized not in {"active", "archived"}:
                raise VaultError(
                    "daily_table_status_invalid",
                    "表格状态只能是 active 或 archived",
                    status_code=422,
                )
            new_status = normalized
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT title, status FROM daily_tables WHERE id = ?",
                    (target,),
                ).fetchone()
                if current is None:
                    raise self._table_not_found()
                if new_title is not None or new_status is not None:
                    connection.execute(
                        """
                        UPDATE daily_tables
                        SET title = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            new_title
                            if new_title is not None
                            else str(current["title"]),
                            new_status
                            if new_status is not None
                            else str(current["status"]),
                            _iso(),
                            target,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_table(target)

    def delete_table(self, table_id: object) -> dict[str, object]:
        target = str(table_id or "").strip()
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_table(connection, target)
                connection.execute(
                    "DELETE FROM daily_table_cells WHERE table_id = ?", (target,)
                )
                connection.execute(
                    "DELETE FROM daily_table_columns WHERE table_id = ?", (target,)
                )
                connection.execute(
                    "DELETE FROM daily_table_rows WHERE table_id = ?", (target,)
                )
                connection.execute(
                    "DELETE FROM daily_tables WHERE id = ?", (target,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"deleted_table_id": target}

    # ------------------------------------------------------------------
    # 列

    def add_column(
        self,
        table_id: object,
        *,
        name: object,
        col_type: object,
        options: object = None,
        position: object = None,
    ) -> dict[str, object]:
        target = str(table_id or "").strip()
        spec = self._validated_column_spec(
            {"name": name, "col_type": col_type, "options": options}
        )
        column_id = uuid4().hex
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_table(connection, target)
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM daily_table_columns WHERE table_id = ?",
                        (target,),
                    ).fetchone()[0]
                )
                insert_at = self._clamp_position(position, upper=count, default=count)
                connection.execute(
                    """
                    UPDATE daily_table_columns
                    SET position = position + 1
                    WHERE table_id = ? AND position >= ?
                    """,
                    (target, insert_at),
                )
                connection.execute(
                    """
                    INSERT INTO daily_table_columns
                        (id, table_id, name, col_type, options_json, position)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        column_id,
                        target,
                        str(spec["name"]),
                        str(spec["col_type"]),
                        json.dumps(spec["options"], ensure_ascii=False)
                        if spec["options"] is not None
                        else None,
                        insert_at,
                    ),
                )
                self._touch_table(connection, target)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        column = self._column_by_id(target, column_id)
        if column is None:  # pragma: no cover - 防御：同事务刚插入
            raise self._column_not_found()
        return column

    def update_column(
        self,
        table_id: object,
        column_id: object,
        *,
        name: object = None,
        col_type: object = None,
        options: object = None,
        position: object = None,
    ) -> dict[str, object]:
        target = str(table_id or "").strip()
        column_key = str(column_id or "").strip()
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_table(connection, target)
                existing = connection.execute(
                    """
                    SELECT id, name, col_type, options_json, position
                    FROM daily_table_columns
                    WHERE table_id = ? AND id = ?
                    """,
                    (target, column_key),
                ).fetchone()
                if existing is None:
                    raise self._column_not_found()
                final_name = (
                    self._validated_column_name(name)
                    if name is not None
                    else str(existing["name"])
                )
                final_type = (
                    self._validated_col_type(col_type)
                    if col_type is not None
                    else str(existing["col_type"])
                )
                # 改类型只改元数据，不清空已有单元格值。
                if final_type == "select":
                    if options is not None:
                        final_options = self._validated_options(options, "select")
                    else:
                        final_options = self._parse_options(
                            existing["options_json"]
                        )
                else:
                    final_options = None
                connection.execute(
                    """
                    UPDATE daily_table_columns
                    SET name = ?, col_type = ?, options_json = ?
                    WHERE id = ?
                    """,
                    (
                        final_name,
                        final_type,
                        json.dumps(final_options, ensure_ascii=False)
                        if final_options is not None
                        else None,
                        column_key,
                    ),
                )
                if position is not None:
                    ordered = [
                        str(row[0])
                        for row in connection.execute(
                            """
                            SELECT id FROM daily_table_columns
                            WHERE table_id = ?
                            ORDER BY position, rowid
                            """,
                            (target,),
                        ).fetchall()
                    ]
                    ordered.remove(column_key)
                    insert_at = self._clamp_position(
                        position, upper=len(ordered), default=len(ordered)
                    )
                    ordered.insert(insert_at, column_key)
                    self._rewrite_column_positions(connection, target, ordered)
                self._touch_table(connection, target)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        column = self._column_by_id(target, column_key)
        if column is None:  # pragma: no cover - 防御：同事务刚更新
            raise self._column_not_found()
        return column

    def delete_column(self, table_id: object, column_id: object) -> dict[str, object]:
        target = str(table_id or "").strip()
        column_key = str(column_id or "").strip()
        self._key_provider("")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_table(connection, target)
                # 连带删除该列已有单元格（先删单元格，计数不受外键级联影响）。
                removed_cells = connection.execute(
                    "DELETE FROM daily_table_cells WHERE table_id = ? AND column_id = ?",
                    (target, column_key),
                ).rowcount
                removed = connection.execute(
                    "DELETE FROM daily_table_columns WHERE table_id = ? AND id = ?",
                    (target, column_key),
                ).rowcount
                if not removed:
                    raise self._column_not_found()
                remaining = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT id FROM daily_table_columns
                        WHERE table_id = ?
                        ORDER BY position, rowid
                        """,
                        (target,),
                    ).fetchall()
                ]
                self._rewrite_column_positions(connection, target, remaining)
                self._touch_table(connection, target)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "deleted_column_id": column_key,
            "removed_cells": int(removed_cells),
        }

    # ------------------------------------------------------------------
    # 单元格

    def put_cells(
        self,
        table_id: object,
        *,
        updates: object,
    ) -> dict[str, object]:
        target = str(table_id or "").strip()
        if not isinstance(updates, list) or not updates:
            raise VaultError(
                "daily_table_updates_required",
                "至少需要一条单元格修改",
                status_code=422,
            )
        self._key_provider("")
        changed = 0
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_table(connection, target)
                columns = {
                    str(row["id"]): (
                        str(row["col_type"]),
                        self._parse_options(row["options_json"]),
                    )
                    for row in connection.execute(
                        "SELECT id, col_type, options_json FROM daily_table_columns WHERE table_id = ?",
                        (target,),
                    ).fetchall()
                }
                row_ids = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT id FROM daily_table_rows WHERE table_id = ?",
                        (target,),
                    ).fetchall()
                }
                now = _iso()
                for update in updates:
                    if not isinstance(update, dict):
                        raise VaultError(
                            "daily_table_update_invalid",
                            "单元格修改格式无效",
                            status_code=422,
                        )
                    row_id = str(update.get("row_id") or "").strip()
                    column_id = str(update.get("column_id") or "").strip()
                    if row_id not in row_ids or column_id not in columns:
                        raise VaultError(
                            "daily_table_ref_invalid",
                            "该行或列已不存在，请刷新后重试",
                            status_code=422,
                        )
                    col_type, options = columns[column_id]
                    value = self._validated_cell_value(
                        col_type, options, update.get("value_text")
                    )
                    if not value:
                        connection.execute(
                            """
                            DELETE FROM daily_table_cells
                            WHERE table_id = ? AND row_id = ? AND column_id = ?
                            """,
                            (target, row_id, column_id),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO daily_table_cells
                                (table_id, row_id, column_id, value_text, updated_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(table_id, row_id, column_id) DO UPDATE SET
                                value_text = excluded.value_text,
                                updated_at = excluded.updated_at
                            """,
                            (target, row_id, column_id, value, now),
                        )
                    changed += 1
                self._touch_table(connection, target, at=now)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"updated": changed}

    # ------------------------------------------------------------------
    # CSV 导出

    def export_csv(self, table_id: object) -> tuple[str, str]:
        """返回 ``(文件名, 带 BOM 的 CSV 文本)``。"""
        detail = self.get_table(table_id)
        columns = detail["columns"]
        cells = detail["cells"]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(
            ["姓名", "班级", *[str(column["name"]) for column in columns]]
        )
        for row in detail["rows"]:
            row_cells = cells.get(str(row["id"]), {})
            writer.writerow(
                [
                    self._csv_safe(str(row["display_name"])),
                    self._csv_safe(str(row["class_label"])),
                    *[
                        self._csv_safe(row_cells.get(str(column["id"]), ""))
                        for column in columns
                    ],
                ]
            )
        title = str(detail["table"]["title"])
        safe_title = _UNSAFE_FILENAME.sub("_", title).strip() or "表格"
        return f"{safe_title}.csv", "\ufeff" + buffer.getvalue()

    @staticmethod
    def _csv_safe(value: str) -> str:
        if value.startswith(_FORMULA_PREFIXES):
            return "'" + value
        return value

    # ------------------------------------------------------------------
    # 内部工具

    @staticmethod
    def _table_not_found() -> VaultError:
        return VaultError(
            "daily_table_not_found",
            "表格不存在或已删除",
            status_code=404,
        )

    @staticmethod
    def _column_not_found() -> VaultError:
        return VaultError(
            "daily_table_column_not_found",
            "该列已不存在，请刷新后重试",
            status_code=404,
        )

    def _require_table(
        self, connection: sqlite3.Connection, table_id: str
    ) -> None:
        exists = connection.execute(
            "SELECT 1 FROM daily_tables WHERE id = ?", (table_id,)
        ).fetchone()
        if exists is None:
            raise self._table_not_found()

    @staticmethod
    def _touch_table(
        connection: sqlite3.Connection, table_id: str, *, at: str | None = None
    ) -> None:
        connection.execute(
            "UPDATE daily_tables SET updated_at = ? WHERE id = ?",
            (at or _iso(), table_id),
        )

    @staticmethod
    def _clamp_position(
        position: object, *, upper: int, default: int
    ) -> int:
        if position is None:
            return default
        try:
            value = int(position)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise VaultError(
                "daily_table_position_invalid",
                "列位置必须是非负整数",
                status_code=422,
            ) from None
        return max(0, min(value, upper))

    @staticmethod
    def _rewrite_column_positions(
        connection: sqlite3.Connection,
        table_id: str,
        ordered_ids: list[str],
    ) -> None:
        for index, column_id in enumerate(ordered_ids):
            connection.execute(
                "UPDATE daily_table_columns SET position = ? WHERE table_id = ? AND id = ?",
                (index, table_id, column_id),
            )

    @staticmethod
    def _parse_options(options_json: object) -> list[str]:
        if not options_json:
            return []
        try:
            parsed = json.loads(str(options_json))
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    def _column_view(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "col_type": str(row["col_type"]),
            "options": self._parse_options(row["options_json"]),
            "position": int(row["position"]),
        }

    def _column_by_id(
        self, table_id: str, column_id: str
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT id, name, col_type, options_json, position
                FROM daily_table_columns
                WHERE table_id = ? AND id = ?
                """,
                (table_id, column_id),
            ).fetchone()
        if row is None:
            return None
        return self._column_view(row)


__all__ = ["DailyTableService"]
