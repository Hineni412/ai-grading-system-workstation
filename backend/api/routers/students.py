from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_student_repository, get_student_roster_module
from backend.api.schemas.students import (
    StudentDeleteResponse,
    StudentDeletionImpactResponse,
    StudentImportCommitRequest,
    StudentImportCommitResponse,
    StudentImportPreviewResponse,
    StudentListResponse,
    StudentMutationResponse,
    StudentResponse,
    StudentUpdateRequest,
    StudentUpsertRequest,
    StudentUpsertResponse,
    StudentWorkspaceResponse,
)
from backend.students import (
    StudentBackupFailed,
    StudentCodeConflict,
    StudentDeleteFailed,
    StudentGradingActive,
    StudentRosterConflict,
    StudentRosterError,
    StudentRosterModule,
    StudentRosterNotFound,
)
from backend.repositories.students import StudentRecord, StudentRepositoryGateway


router = APIRouter(prefix="/api", tags=["students"])


def _student_response(row: dict) -> StudentResponse:
    return StudentResponse(
        id=int(row["id"]),
        student_code=str(row["student_code"]),
        name=str(row["name"]),
        class_name=row.get("class_name"),
        created_at=row.get("created_at"),
    )

@router.get("/students", response_model=StudentListResponse)
def list_students(
    students: StudentRepositoryGateway = Depends(get_student_repository),
) -> StudentListResponse:
    items = [_student_response(row) for row in students.list_students()]
    return StudentListResponse(items=items, total=len(items))


@router.get(
    "/students/workspace",
    response_model=StudentWorkspaceResponse,
)
def get_student_workspace(
    search: str = Query(default="", max_length=200),
    class_name: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentWorkspaceResponse:
    workspace = roster.workspace(
        search=search,
        class_name=class_name,
        page=page,
        page_size=page_size,
    )
    return StudentWorkspaceResponse.model_validate(workspace.to_dict())


@router.post(
    "/students/import/preview",
    response_model=StudentImportPreviewResponse,
)
async def preview_student_import(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    student_code_column: str | None = Query(default=None, max_length=255),
    name_column: str | None = Query(default=None, max_length=255),
    class_name_column: str | None = Query(default=None, max_length=255),
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentImportPreviewResponse:
    content_length = request.headers.get("content-length")
    try:
        if (
            content_length is not None
            and int(content_length) > roster.max_upload_bytes
        ):
            raise ApiError(
                413,
                "student_roster_too_large",
                "Student roster file is too large",
            )
    except ValueError as exc:
        raise ApiError(
            422,
            "student_roster_invalid_length",
            "Student roster content length is invalid",
        ) from exc
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > roster.max_upload_bytes:
            raise ApiError(
                413,
                "student_roster_too_large",
                "Student roster file is too large",
            )
    mapping = {
        "student_code": student_code_column,
        "name": name_column,
        "class_name": class_name_column,
    }
    try:
        preview = roster.preview_import(filename, bytes(content), mapping)
    except StudentRosterError as exc:
        message = (
            "Student roster file is unreadable"
            if exc.code == "student_roster_unreadable"
            else "Student roster preview is invalid"
        )
        raise ApiError(422, exc.code, message) from exc
    return StudentImportPreviewResponse.model_validate(preview.to_dict())


@router.post(
    "/students/import/commit",
    response_model=StudentImportCommitResponse,
    responses={
        409: {
            "model": ErrorResponse,
            "description": "Student roster changed after preview",
        }
    },
)
def commit_student_import(
    request: StudentImportCommitRequest,
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentImportCommitResponse:
    try:
        result = roster.commit_import(
            request.expected_revision,
            [item.model_dump() for item in request.items],
        )
    except StudentRosterConflict as exc:
        raise ApiError(
            409,
            exc.code,
            "Student roster changed; preview it again",
        ) from exc
    except StudentRosterError as exc:
        raise ApiError(
            422,
            exc.code,
            "Student roster import is invalid",
        ) from exc
    return StudentImportCommitResponse.model_validate(result.to_dict())


@router.post("/students", response_model=StudentUpsertResponse, status_code=201)
def upsert_students(
    request: StudentUpsertRequest,
    students: StudentRepositoryGateway = Depends(get_student_repository),
) -> StudentUpsertResponse:
    result = students.upsert_students(
        [
            StudentRecord(
                student_code=item.student_code,
                name=item.name,
                class_name=item.class_name,
            )
            for item in request.items
        ]
    )
    items = [_student_response(row) for row in students.list_students()]
    return StudentUpsertResponse(
        inserted=int(result["inserted"]),
        updated=int(result["updated"]),
        total=int(result["total"]),
        items=items,
    )


@router.patch(
    "/students/{student_id}",
    response_model=StudentMutationResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Student not found"},
        409: {"model": ErrorResponse, "description": "Student roster conflict"},
    },
)
def update_student(
    student_id: int,
    request: StudentUpdateRequest,
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentMutationResponse:
    try:
        result = roster.update_student(
            int(student_id),
            request.expected_revision,
            request.model_dump(exclude={"expected_revision"}),
        )
    except StudentRosterNotFound as exc:
        raise ApiError(
            404,
            exc.code,
            "Student not found",
            {"student_id": int(student_id)},
        ) from exc
    except StudentCodeConflict as exc:
        raise ApiError(
            409,
            exc.code,
            "Student code already exists",
            {"student_code": request.student_code, "student_id": int(student_id)},
        ) from exc
    except StudentRosterConflict as exc:
        raise ApiError(
            409,
            exc.code,
            "Student roster changed; refresh it and try again",
        ) from exc
    except StudentRosterError as exc:
        raise ApiError(422, exc.code, "Student update is invalid") from exc
    return StudentMutationResponse.model_validate(result.to_dict())


@router.get(
    "/students/{student_id}/deletion-impact",
    response_model=StudentDeletionImpactResponse,
    responses={404: {"model": ErrorResponse, "description": "Student not found"}},
)
def get_student_deletion_impact(
    student_id: int,
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentDeletionImpactResponse:
    try:
        impact = roster.deletion_impact(int(student_id))
    except StudentRosterNotFound as exc:
        raise ApiError(
            404,
            exc.code,
            "Student not found",
            {"student_id": int(student_id)},
        ) from exc
    return StudentDeletionImpactResponse.model_validate(impact.to_dict())


@router.delete(
    "/students/{student_id}",
    response_model=StudentDeleteResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Student not found"},
        409: {
            "model": ErrorResponse,
            "description": "Student roster changed or grading is still active",
        },
        500: {"model": ErrorResponse, "description": "Student deletion failed"},
    },
)
def delete_student(
    student_id: int,
    expected_revision: str = Query(pattern=r"^[0-9a-f]{64}$"),
    confirmed: bool = Query(),
    roster: StudentRosterModule = Depends(get_student_roster_module),
) -> StudentDeleteResponse:
    try:
        result = roster.delete_student(
            int(student_id),
            expected_revision,
            confirmed=confirmed,
        )
    except StudentRosterNotFound as exc:
        raise ApiError(
            404,
            exc.code,
            "Student not found",
            {"student_id": int(student_id)},
        ) from exc
    except StudentRosterConflict as exc:
        raise ApiError(
            409,
            exc.code,
            "Student roster changed; refresh the impact before deleting",
        ) from exc
    except StudentGradingActive as exc:
        raise ApiError(
            409,
            exc.code,
            "Wait for grading to finish before deleting this student",
        ) from exc
    except StudentBackupFailed as exc:
        raise ApiError(
            500,
            exc.code,
            "Student backup failed; no student data was deleted",
        ) from exc
    except StudentDeleteFailed as exc:
        raise ApiError(
            500,
            exc.code,
            "Student deletion failed; no student data was changed",
        ) from exc
    except StudentRosterError as exc:
        raise ApiError(422, exc.code, "Student deletion is not confirmed") from exc
    return StudentDeleteResponse.model_validate(result.to_dict())
