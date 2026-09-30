from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backend.repositories import RepositoryError
from backend.repositories.students import (
    StudentBackupFailedError,
    StudentCodeConflictError,
    StudentGradingActiveError,
    StudentRecord,
    StudentRepositoryGateway,
    StudentRosterRevisionConflict,
    student_roster_revision,
)

FIELD_ALIASES = {
    "student_code": ("student_code", "学号", "考号", "id", "编号"),
    "name": ("name", "姓名", "学生姓名"),
    "class_name": ("class_name", "班级", "班别"),
}
ImportOperation = Literal["insert", "update", "unchanged", "invalid", "duplicate"]


class StudentRosterError(ValueError):
    code = "student_roster_invalid"


class StudentRosterUnreadable(StudentRosterError):
    code = "student_roster_unreadable"


class StudentRosterEmpty(StudentRosterError):
    code = "student_roster_empty"


class StudentRosterConflict(StudentRosterError):
    code = "student_roster_conflict"


class StudentCodeConflict(StudentRosterError):
    code = "student_code_conflict"


class StudentRosterNotFound(StudentRosterError):
    code = "student_not_found"


class StudentBackupFailed(StudentRosterError):
    code = "student_backup_failed"


class StudentDeleteFailed(StudentRosterError):
    code = "student_delete_failed"


class StudentGradingActive(StudentRosterError):
    code = "student_grading_active"


@dataclass(frozen=True)
class StudentImportRow:
    source_row: int
    student_code: str
    name: str
    class_name: str | None
    operation: ImportOperation
    selectable: bool
    issues: tuple[str, ...] = ()
    existing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "student_code": self.student_code,
            "name": self.name,
            "class_name": self.class_name,
            "operation": self.operation,
            "selectable": self.selectable,
            "issues": list(self.issues),
            "existing": self.existing,
        }


@dataclass(frozen=True)
class StudentImportPreview:
    filename: str
    columns: tuple[str, ...]
    mapping: dict[str, str | None]
    counts: dict[str, int]
    rows: tuple[StudentImportRow, ...]
    roster_revision: str
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "columns": list(self.columns),
            "mapping": dict(self.mapping),
            "counts": dict(self.counts),
            "rows": [row.to_dict() for row in self.rows],
            "roster_revision": self.roster_revision,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class StudentImportResult:
    inserted: int
    updated: int
    unchanged: int
    total: int
    roster_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "total": self.total,
            "roster_revision": self.roster_revision,
        }


@dataclass(frozen=True)
class StudentWorkspace:
    items: tuple[dict[str, Any], ...]
    total: int
    page: int
    page_size: int
    total_pages: int
    class_names: tuple[str, ...]
    roster_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [dict(item) for item in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
            "class_names": list(self.class_names),
            "roster_revision": self.roster_revision,
        }


@dataclass(frozen=True)
class StudentMutationResult:
    student: dict[str, Any]
    roster_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "student": dict(self.student),
            "roster_revision": self.roster_revision,
        }


@dataclass(frozen=True)
class StudentDeletionImpact:
    student: dict[str, Any]
    counts: dict[str, int]
    roster_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "student": dict(self.student),
            "counts": dict(self.counts),
            "roster_revision": self.roster_revision,
        }


@dataclass(frozen=True)
class StudentDeleteResult:
    counts: dict[str, int]
    backup_created: bool
    roster_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.counts,
            "backup_created": self.backup_created,
            "roster_revision": self.roster_revision,
        }


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _public_student(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "student_code": str(row["student_code"]),
        "name": str(row["name"]),
        "class_name": row.get("class_name"),
        "created_at": row.get("created_at"),
    }


class StudentRosterModule:
    def __init__(
        self,
        students: StudentRepositoryGateway,
        *,
        max_upload_bytes: int = 10 * 1024 * 1024,
    ):
        self._students = students
        self.max_upload_bytes = int(max_upload_bytes)

    def workspace(
        self,
        *,
        search: str = "",
        class_name: str = "",
        page: int = 1,
        page_size: int = 100,
    ) -> StudentWorkspace:
        if page < 1 or page_size < 1 or page_size > 200:
            raise StudentRosterError("学生名单分页参数无效")
        snapshot = self._students.student_workspace_snapshot(
            search=_clean_text(search),
            class_name=_clean_text(class_name),
            page=int(page),
            page_size=int(page_size),
        )
        total = int(snapshot["total"])
        total_pages = (total + page_size - 1) // page_size
        return StudentWorkspace(
            items=tuple(_public_student(row) for row in snapshot["items"]),
            total=total,
            page=int(page),
            page_size=int(page_size),
            total_pages=total_pages,
            class_names=tuple(snapshot["class_names"]),
            roster_revision=str(snapshot["roster_revision"]),
        )

    def preview_import(
        self,
        filename: str,
        content: bytes,
        mapping: dict[str, str | None] | None = None,
    ) -> StudentImportPreview:
        clean_filename = Path(str(filename or "")).name
        if not clean_filename:
            raise StudentRosterError("学生名单文件名不能为空")
        if not content:
            raise StudentRosterEmpty("学生名单为空")
        if len(content) > self.max_upload_bytes:
            raise StudentRosterError("学生名单文件过大")

        frame = self._read_frame(clean_filename, content)
        if frame.empty:
            raise StudentRosterEmpty("学生名单为空")

        columns = tuple(str(column).strip() for column in frame.columns)
        resolved_mapping = self._resolve_mapping(columns, mapping)
        existing_rows = self._students.list_students()
        if not resolved_mapping["student_code"] or not resolved_mapping["name"]:
            return StudentImportPreview(
                filename=clean_filename,
                columns=columns,
                mapping=resolved_mapping,
                counts={
                    key: 0
                    for key in ("insert", "update", "unchanged", "invalid", "duplicate")
                },
                rows=(),
                roster_revision=student_roster_revision(existing_rows),
                issues=("请选择学号列和姓名列",),
            )

        existing_by_code = {
            str(row["student_code"]): _public_student(row)
            for row in existing_rows
        }
        prepared_rows: list[tuple[int, str, str, str]] = []
        last_position_by_code: dict[str, int] = {}
        for position, (index, raw_row) in enumerate(frame.iterrows()):
            student_code = _clean_text(raw_row.get(resolved_mapping["student_code"]))
            name = _clean_text(raw_row.get(resolved_mapping["name"]))
            class_column = resolved_mapping["class_name"]
            class_name = _clean_text(raw_row.get(class_column)) if class_column else ""
            prepared_rows.append((int(index) + 2, student_code, name, class_name))
            if student_code and name:
                last_position_by_code[student_code] = position

        preview_rows: list[StudentImportRow] = []
        counts = {key: 0 for key in ("insert", "update", "unchanged", "invalid", "duplicate")}
        for position, (source_row, student_code, name, class_name) in enumerate(prepared_rows):
            if not student_code or not name:
                operation: ImportOperation = "invalid"
                issues = ("学号和姓名不能为空",)
                selectable = False
                existing = None
            elif last_position_by_code[student_code] != position:
                operation = "duplicate"
                issues = ("同一文件中后续行将覆盖此学号",)
                selectable = False
                existing = existing_by_code.get(student_code)
            else:
                existing = existing_by_code.get(student_code)
                if existing is None:
                    operation = "insert"
                elif (
                    existing["name"] == name
                    and (existing.get("class_name") or "") == class_name
                ):
                    operation = "unchanged"
                else:
                    operation = "update"
                issues = ()
                selectable = operation in {"insert", "update"}
            counts[operation] += 1
            preview_rows.append(
                StudentImportRow(
                    source_row=source_row,
                    student_code=student_code,
                    name=name,
                    class_name=class_name or None,
                    operation=operation,
                    selectable=selectable,
                    issues=issues,
                    existing=existing,
                )
            )
        return StudentImportPreview(
            filename=clean_filename,
            columns=columns,
            mapping=resolved_mapping,
            counts=counts,
            rows=tuple(preview_rows),
            roster_revision=student_roster_revision(existing_rows),
        )

    def commit_import(
        self,
        expected_revision: str,
        items: list[dict[str, Any]],
    ) -> StudentImportResult:
        if (
            len(str(expected_revision)) != 64
            or any(character not in "0123456789abcdef" for character in str(expected_revision))
        ):
            raise StudentRosterError("学生名单版本无效")
        if not items:
            raise StudentRosterError("请至少选择一名有效学生")
        records: list[StudentRecord] = []
        seen_codes: set[str] = set()
        for item in items:
            student_code = _clean_text(item.get("student_code"))
            name = _clean_text(item.get("name"))
            class_name = _clean_text(item.get("class_name"))
            if not student_code or not name:
                raise StudentRosterError("学号和姓名不能为空")
            if student_code in seen_codes:
                raise StudentRosterError("确认名单中不能包含重复学号")
            seen_codes.add(student_code)
            records.append(
                StudentRecord(
                    student_code=student_code,
                    name=name,
                    class_name=class_name or None,
                )
            )
        try:
            result = self._students.upsert_students_if_revision(
                records,
                expected_revision=expected_revision,
            )
        except StudentRosterRevisionConflict as exc:
            raise StudentRosterConflict("学生名单已变化，请重新预览") from exc
        changed = int(result["inserted"]) + int(result["updated"])
        return StudentImportResult(
            inserted=int(result["inserted"]),
            updated=int(result["updated"]),
            unchanged=len(records) - changed,
            total=changed,
            roster_revision=str(result["roster_revision"]),
        )

    def update_student(
        self,
        student_id: int,
        expected_revision: str,
        values: dict[str, Any],
    ) -> StudentMutationResult:
        student_code = _clean_text(values.get("student_code"))
        name = _clean_text(values.get("name"))
        class_name = _clean_text(values.get("class_name"))
        if not student_code or not name:
            raise StudentRosterError("学号和姓名不能为空")
        try:
            result = self._students.update_student_if_revision(
                int(student_id),
                student_code,
                name,
                class_name or None,
                expected_revision=expected_revision,
            )
        except StudentRosterRevisionConflict as exc:
            raise StudentRosterConflict("学生名单已变化，请刷新后重试") from exc
        except StudentCodeConflictError as exc:
            raise StudentCodeConflict("学号已存在，无法保存") from exc
        except ValueError as exc:
            raise StudentRosterNotFound("未找到学生记录") from exc
        return StudentMutationResult(
            student=_public_student(result["student"]),
            roster_revision=str(result["roster_revision"]),
        )

    def deletion_impact(self, student_id: int) -> StudentDeletionImpact:
        try:
            impact = self._students.student_deletion_impact(int(student_id))
        except ValueError as exc:
            raise StudentRosterNotFound("未找到学生记录") from exc
        return StudentDeletionImpact(
            student=_public_student(impact["student"]),
            counts={
                key: int(value)
                for key, value in impact["counts"].items()
            },
            roster_revision=str(impact["roster_revision"]),
        )

    def delete_student(
        self,
        student_id: int,
        expected_revision: str,
        *,
        confirmed: bool,
    ) -> StudentDeleteResult:
        if not confirmed:
            raise StudentRosterError("请先确认不可恢复删除")
        try:
            result = self._students.delete_student_hard(
                int(student_id),
                expected_revision=expected_revision,
            )
        except StudentRosterRevisionConflict as exc:
            raise StudentRosterConflict("学生名单已变化，请刷新后重试") from exc
        except StudentBackupFailedError as exc:
            raise StudentBackupFailed("备份失败，学生未删除") from exc
        except StudentGradingActiveError as exc:
            raise StudentGradingActive(
                "Student cannot be deleted while grading is active"
            ) from exc
        except (sqlite3.Error, RepositoryError) as exc:
            raise StudentDeleteFailed(
                "删除失败，学生和历史数据均未改变"
            ) from exc
        except ValueError as exc:
            raise StudentRosterNotFound("未找到学生记录") from exc
        count_keys = (
            "deleted_students",
            "deleted_results",
            "deleted_details",
            "deleted_annotations",
            "deleted_attendance",
            "unlinked_papers",
        )
        return StudentDeleteResult(
            counts={key: int(result[key]) for key in count_keys},
            backup_created=bool(result["backup_created"]),
            roster_revision=str(result["roster_revision"]),
        )

    @staticmethod
    def _read_frame(filename: str, content: bytes) -> pd.DataFrame:
        suffix = Path(filename).suffix.lower()
        try:
            if suffix == ".csv":
                if b"\x00" in content:
                    raise ValueError("CSV contains NUL bytes")
                return pd.read_csv(
                    io.BytesIO(content),
                    dtype=str,
                    keep_default_na=False,
                )
            if suffix == ".xlsx":
                if not content.startswith(b"PK"):
                    raise ValueError("XLSX signature mismatch")
                return pd.read_excel(
                    io.BytesIO(content),
                    dtype=str,
                    keep_default_na=False,
                )
        except (OSError, UnicodeError, ValueError) as exc:
            raise StudentRosterUnreadable(
                "无法读取学生名单，请检查文件格式"
            ) from exc
        raise StudentRosterError("仅支持 CSV / XLSX 文件")

    @staticmethod
    def _resolve_mapping(
        columns: tuple[str, ...],
        requested: dict[str, str | None] | None,
    ) -> dict[str, str | None]:
        normalized_columns = {
            column.casefold(): column
            for column in columns
        }
        resolved: dict[str, str | None] = {}
        for field, aliases in FIELD_ALIASES.items():
            selected = (requested or {}).get(field)
            if selected:
                resolved[field] = normalized_columns.get(str(selected).strip().casefold())
                continue
            resolved[field] = next(
                (
                    normalized_columns[alias.casefold()]
                    for alias in aliases
                    if alias.casefold() in normalized_columns
                ),
                None,
            )
        return resolved
