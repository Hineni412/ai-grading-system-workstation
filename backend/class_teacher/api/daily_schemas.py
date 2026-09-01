from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RegularCellRequest(BaseModel):
    day_of_week: int = Field(ge=1, le=5)
    slot_key: str = Field(min_length=1, max_length=80)
    # 空字符串表示删除该常规格。
    course_text: str = Field(default="", max_length=50)
    class_label: str = Field(default="", max_length=50)


class CustomSlotCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    start_text: str | None = Field(default=None, max_length=20)
    end_text: str | None = Field(default=None, max_length=20)
    # position 表示插在第几节课之后：0=第 1 节前，8=第 8 节后。
    position: int = Field(ge=0, le=8)


class OverrideOperation(BaseModel):
    day_of_week: int = Field(ge=1, le=5)
    slot_key: str = Field(min_length=1, max_length=80)
    action: Literal["set", "clear"]
    course_text: str | None = Field(default=None, max_length=50)
    class_label: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=500)


class OverridesApplyRequest(BaseModel):
    week_start: str = Field(min_length=10, max_length=10)
    # 一次调用多条整体原子提交（拖拽交换 = 两条 op）。
    ops: list[OverrideOperation] = Field(min_length=1, max_length=100)


class WeekAnchorPutRequest(BaseModel):
    # 该周任意一天，服务端规范化到周一存储。
    date: str = Field(min_length=10, max_length=10)
    week_no: int = Field(ge=1, le=40)


class NoteCreateRequest(BaseModel):
    note_date: str = Field(min_length=10, max_length=10)
    slot_key: str = Field(min_length=1, max_length=80)
    class_label: str = Field(min_length=1, max_length=50)
    content_text: str = Field(min_length=1, max_length=500)
    homework_text: str | None = Field(default=None, max_length=500)


__all__ = [
    "CustomSlotCreateRequest",
    "NoteCreateRequest",
    "OverrideOperation",
    "OverridesApplyRequest",
    "RegularCellRequest",
    "WeekAnchorPutRequest",
]
