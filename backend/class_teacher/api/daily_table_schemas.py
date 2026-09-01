from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ColumnType = Literal["text", "number", "check", "select", "date"]


class TableColumnSpec(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    col_type: ColumnType
    options: list[str] | None = Field(default=None, max_length=20)


class TableCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    # 按所选班级的名单快照生成行；无名单的班级允许，响应里带提示。
    class_labels: list[str] = Field(default_factory=list, max_length=50)
    columns: list[TableColumnSpec] = Field(default_factory=list, max_length=50)


class TablePatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=50)
    status: Literal["active", "archived"] | None = None


class TableColumnCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    col_type: ColumnType
    options: list[str] | None = Field(default=None, max_length=20)
    # 缺省追加到末尾；指定时插入该位置，后续列顺移。
    position: int | None = Field(default=None, ge=0)


class TableColumnPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=30)
    col_type: ColumnType | None = None
    options: list[str] | None = Field(default=None, max_length=20)
    position: int | None = Field(default=None, ge=0)


class CellUpdate(BaseModel):
    row_id: str = Field(min_length=1, max_length=64)
    column_id: str = Field(min_length=1, max_length=64)
    # 空字符串表示删除该单元格。
    value_text: str = Field(default="", max_length=500)


class CellsPutRequest(BaseModel):
    # 一次调用整体单事务提交。
    updates: list[CellUpdate] = Field(min_length=1, max_length=2000)


__all__ = [
    "CellUpdate",
    "CellsPutRequest",
    "TableColumnCreateRequest",
    "TableColumnPatchRequest",
    "TableColumnSpec",
    "TableCreateRequest",
    "TablePatchRequest",
]
