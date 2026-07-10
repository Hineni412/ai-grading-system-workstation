from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db
from backend.api.schemas.students import (
    StudentDeleteResponse,
    StudentListResponse,
    StudentResponse,
    StudentUpdateRequest,
    StudentUpsertRequest,
    StudentUpsertResponse,
)
from db_manager import DBManager, StudentRecord


router = APIRouter(prefix="/api", tags=["students"])


def _student_response(row: dict[str, Any]) -> StudentResponse:
    return StudentResponse(
        id=int(row["id"]),
        student_code=str(row["student_code"]),
        name=str(row["name"]),
        class_name=row.get("class_name"),
        created_at=row.get("created_at"),
    )


def _require_student(db: DBManager, student_id: int) -> dict[str, Any]:
    for row in db.list_students():
        if int(row["id"]) == int(student_id):
            return row
    raise ApiError(
        404,
        "student_not_found",
        "Student not found",
        {"student_id": int(student_id)},
    )


def _find_student_by_code(db: DBManager, student_code: str) -> dict[str, Any] | None:
    clean_code = str(student_code or "").strip()
    for row in db.list_students():
        if str(row["student_code"]) == clean_code:
            return row
    return None


@router.get("/students", response_model=StudentListResponse)
def list_students(db: DBManager = Depends(get_grading_db)) -> StudentListResponse:
    items = [_student_response(row) for row in db.list_students()]
    return StudentListResponse(items=items, total=len(items))


@router.post("/students", response_model=StudentUpsertResponse, status_code=201)
def upsert_students(
    request: StudentUpsertRequest,
    db: DBManager = Depends(get_grading_db),
) -> StudentUpsertResponse:
    result = db.upsert_students(
        [
            StudentRecord(
                student_code=item.student_code,
                name=item.name,
                class_name=item.class_name,
            )
            for item in request.items
        ]
    )
    items = [_student_response(row) for row in db.list_students()]
    return StudentUpsertResponse(
        inserted=int(result["inserted"]),
        updated=int(result["updated"]),
        total=int(result["total"]),
        items=items,
    )


@router.patch("/students/{student_id}", response_model=StudentResponse)
def update_student(
    student_id: int,
    request: StudentUpdateRequest,
    db: DBManager = Depends(get_grading_db),
) -> StudentResponse:
    _require_student(db, student_id)
    existing_with_code = _find_student_by_code(db, request.student_code)
    if existing_with_code is not None and int(existing_with_code["id"]) != int(student_id):
        raise ApiError(
            409,
            "student_code_conflict",
            "Student code already exists",
            {"student_code": request.student_code, "student_id": int(student_id)},
        )
    try:
        db.update_student(
            int(student_id),
            request.student_code,
            request.name,
            request.class_name,
        )
    except ValueError as exc:
        raise ApiError(
            400,
            "invalid_student",
            str(exc),
            {"student_id": int(student_id)},
        ) from exc
    return _student_response(_require_student(db, student_id))


@router.delete("/students/{student_id}", response_model=StudentDeleteResponse)
def delete_student(
    student_id: int,
    db: DBManager = Depends(get_grading_db),
) -> StudentDeleteResponse:
    _require_student(db, student_id)
    result = db.delete_student_hard(int(student_id))
    return StudentDeleteResponse(**result)
