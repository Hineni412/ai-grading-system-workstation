"""本地执行产物的持久化仓储。

产物指 python-pptx 在隔离副本上生成的改编成片及其审计报告。
"""

from __future__ import annotations

import json
from typing import Any

from backend.teaching_prep.domain.errors import (
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)


def _clean_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if len(text) != 32 or not all(c in "0123456789abcdef" for c in text):
        raise TeachingPrepValidationError(f"{label} is invalid")
    return text


class PptxLocalOutputRepository:
    def __init__(self, database: Any) -> None:
        self._database = database

    def create(self, **fields: Any) -> dict[str, Any]:
        required = (
            "id",
            "lesson_node_id",
            "slide_plan_id",
            "draft_id",
            "resource_pack_id",
            "output_relpath",
            "output_filename",
            "output_sha256",
            "source_material_version_id",
            "source_file_name",
            "source_page_count",
            "final_page_count",
            "lesson_kind",
            "execution_report_json",
            "audit_report_json",
            "inserted_question_pages_json",
        )
        clean: dict[str, Any] = {}
        for name in required:
            value = fields.get(name)
            if value is None:
                raise TeachingPrepValidationError(f"{name} is required")
            clean[name] = value
        worksheet_relpath = fields.get("worksheet_relpath")
        worksheet_filename = fields.get("worksheet_filename")
        with self._database.connect(immediate=True) as connection:
            next_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM pptx_local_outputs
                    WHERE lesson_node_id = ?
                    """,
                    (clean["lesson_node_id"],),
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE pptx_local_outputs
                SET status = 'superseded'
                WHERE lesson_node_id = ? AND status = 'ready'
                """,
                (clean["lesson_node_id"],),
            )
            connection.execute(
                """
                INSERT INTO pptx_local_outputs (
                    id, lesson_node_id, slide_plan_id, draft_id,
                    resource_pack_id, version_number, status,
                    output_relpath, output_filename, output_sha256,
                    source_material_version_id, source_file_name,
                    source_page_count, final_page_count, lesson_kind,
                    execution_report_json, audit_report_json,
                    inserted_question_pages_json,
                    worksheet_relpath, worksheet_filename
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean["id"],
                    clean["lesson_node_id"],
                    clean["slide_plan_id"],
                    clean["draft_id"],
                    clean["resource_pack_id"],
                    next_number,
                    clean["output_relpath"],
                    clean["output_filename"],
                    clean["output_sha256"],
                    clean["source_material_version_id"],
                    clean["source_file_name"],
                    int(clean["source_page_count"]),
                    int(clean["final_page_count"]),
                    str(clean["lesson_kind"]),
                    clean["execution_report_json"],
                    clean["audit_report_json"],
                    clean["inserted_question_pages_json"],
                    worksheet_relpath,
                    worksheet_filename,
                ),
            )
            row = connection.execute(
                "SELECT * FROM pptx_local_outputs WHERE id = ?",
                (clean["id"],),
            ).fetchone()
        return dict(row)

    def attach_worksheet(
        self,
        output_id: str,
        *,
        worksheet_relpath: str,
        worksheet_filename: str,
    ) -> dict[str, Any]:
        clean_id = _clean_id(output_id, "output_id")
        with self._database.connect(immediate=True) as connection:
            connection.execute(
                """
                UPDATE pptx_local_outputs
                SET worksheet_relpath = ?, worksheet_filename = ?
                WHERE id = ?
                """,
                (worksheet_relpath, worksheet_filename, clean_id),
            )
            row = connection.execute(
                "SELECT * FROM pptx_local_outputs WHERE id = ?",
                (clean_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("local output was not found")
        return dict(row)

    def get(self, output_id: str) -> dict[str, Any]:
        clean_id = _clean_id(output_id, "output_id")
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pptx_local_outputs WHERE id = ?",
                (clean_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("local output was not found")
        return dict(row)

    def list_for_lesson(self, lesson_node_id: str) -> tuple[dict[str, Any], ...]:
        clean_id = _clean_id(lesson_node_id, "lesson_node_id")
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pptx_local_outputs
                WHERE lesson_node_id = ?
                ORDER BY version_number DESC
                """,
                (clean_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def execution_report(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(str(row.get("execution_report_json") or "{}"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def audit_report(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(str(row.get("audit_report_json") or "{}"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def inserted_question_pages(row: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            value = json.loads(
                str(row.get("inserted_question_pages_json") or "[]")
            )
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []
