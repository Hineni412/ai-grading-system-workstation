from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class StudentUpsertItem(BaseModel):
    student_code: str
    name: str
    class_name: str | None = None

    @field_validator("student_code", "name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError("must be nonblank")
        return clean

    @field_validator("class_name")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        clean = str(value or "").strip()
        return clean or None


class StudentUpsertRequest(BaseModel):
    items: list[StudentUpsertItem] = Field(min_length=1)


class StudentUpdateRequest(StudentUpsertItem):
    pass


class StudentResponse(BaseModel):
    id: int
    student_code: str
    name: str
    class_name: str | None = None
    created_at: str | None = None


class StudentListResponse(BaseModel):
    items: list[StudentResponse]
    total: int


class StudentUpsertResponse(BaseModel):
    inserted: int
    updated: int
    total: int
    items: list[StudentResponse]


class StudentDeleteResponse(BaseModel):
    deleted_students: int
    deleted_results: int
    deleted_details: int
    deleted_annotations: int
    deleted_attendance: int
    unlinked_papers: int
