from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from backend.api.app import ApiError, ErrorResponse
from backend.error_causes import display_error_category
from backend.api.dependencies import (
    get_grading_db,
    get_question_bank_db_path,
    get_student_repository,
    get_student_roster_module,
    get_job_manager,
)
from backend.api.schemas.students import (
    StudentDeleteResponse,
    StudentDeletionImpactResponse,
    StudentExamResultItem,
    StudentExamResultSession,
    StudentExamResultsResponse,
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
    WrongQuestionBookPreviewRequest,
    WrongQuestionBookSubmitRequest,
)
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.students import StudentRecord, StudentRepositoryGateway
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
from backend.students.exam_evidence import student_exam_evidence
from backend.students.wrong_question_book import build_wrong_question_books
from backend.jobs.manager import JobManager
from backend.api.schemas.jobs import JobResponse
from backend.api.routers.jobs import _job_response

router = APIRouter(prefix="/api", tags=["students"])


@router.post("/students/{student_id}/wrong-question-book/preview")
def preview_wrong_question_book(
    student_id: int,
    body: WrongQuestionBookPreviewRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
) -> dict:
    roster = db.students.list_students()
    student = next((row for row in roster if int(row["id"]) == student_id), None)
    if student is None:
        raise ApiError(404, "student_not_found", "Student not found")
    if body.include_class and not student.get("class_name"):
        raise ApiError(422, "student_class_required", "当前学生尚未分班，请选择当前学生")
    student_ids = [int(row["id"]) for row in roster if row.get("class_name") == student.get("class_name")] if body.include_class else [student_id]
    try:
        plan = build_wrong_question_books(db, question_bank_db_path, student_ids, body.curriculum_volume_id, body.session_ids)
    except ValueError as exc:
        raise ApiError(422, "wrong_question_scope_invalid", str(exc)) from exc
    return {key: value for key, value in plan.items() if key != "books"}


@router.get("/students/wrong-question-books/by-request/{request_token}", response_model=JobResponse)
def find_wrong_question_export(request_token: str, manager: JobManager = Depends(get_job_manager)) -> JobResponse:
    try:
        job = manager.store.find_export_job_by_request_token("wrong_question_export", request_token)
    except ValueError as exc:
        raise ApiError(422, "wrong_question_token_invalid", "请求令牌无效") from exc
    if job is None:
        raise ApiError(404, "wrong_question_request_not_found", "尚未查到此次导出请求，请稍后再次查询")
    return _job_response(job)


@router.post("/students/wrong-question-books", response_model=JobResponse, status_code=202)
def submit_wrong_question_books(
    body: WrongQuestionBookSubmitRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    # Replay the exact submitted scope even if marks/roster changed after submission.
    payload = body.model_dump()
    existing = manager.store.find_export_job_by_request_token("wrong_question_export", body.client_request_token)
    if existing is not None:
        if existing.payload != payload:
            raise ApiError(409, "wrong_question_request_conflict", "该请求令牌已用于另一份导出")
        return _job_response(existing)
    try:
        build_wrong_question_books(db, question_bank_db_path, body.student_ids, body.curriculum_volume_id, body.session_ids)
    except ValueError as exc:
        raise ApiError(422, "wrong_question_scope_invalid", str(exc)) from exc
    try:
        job, _created = manager.submit_idempotent_export("wrong_question_export", payload)
    except ValueError as exc:
        raise ApiError(409, "wrong_question_request_conflict", str(exc)) from exc
    return _job_response(job)


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
    "/students/{student_id}/exam-results",
    response_model=StudentExamResultsResponse,
    responses={404: {"model": ErrorResponse, "description": "Student not found"}},
)
def get_student_exam_results(
    student_id: int,
    only_deducted: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    curriculum_volume_id: str | None = Query(default=None, max_length=100),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    students: StudentRepositoryGateway = Depends(get_student_repository),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
) -> StudentExamResultsResponse:
    student_row = next(
        (
            row
            for row in students.list_students()
            if int(row["id"]) == int(student_id)
        ),
        None,
    )
    if student_row is None:
        raise ApiError(
            404,
            "student_not_found",
            "Student not found",
            {"student_id": int(student_id)},
        )
    rows = student_exam_evidence(db, question_bank_db_path, [int(student_id)], curriculum_volume_id)
    if only_deducted:
        rows = [row for row in rows if row.get("is_deducted")]

    sessions: dict[int, dict[str, Any]] = {}
    for row in rows:
        session_id = int(row["session_id"])
        result_id = int(row["result_id"])
        detail_id = int(row["detail_id"])
        bucket = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "session_name": str(row.get("session_name") or ""),
                "graded_at": row.get("graded_at"),
                "exam_created_at": row.get("exam_created_at"),
                "result_id": result_id,
                "student_score": float(row.get("student_score") or 0.0),
                "total_score": float(row.get("total_score") or 0.0),
                "items": [],
            },
        )
        bucket["items"].append(
            StudentExamResultItem(
                detail_id=detail_id,
                question_id=str(row.get("question_id") or ""),
                bank_question_id=row.get("bank_question_id"),
                score_awarded=float(row.get("score_awarded") or 0.0),
                max_score=row.get("max_score"),
                deduction_amount=row.get("deduction_amount"),
                deduction_reason=row.get("deduction_reason"),
                error_category=(
                    display_error_category(row.get("error_category")) or None
                ),
                error_summary=row.get("error_summary"),
                evidence_url=(
                    f"/api/sessions/{session_id}/results/{result_id}"
                    f"/details/{detail_id}/crop"
                ),
            )
        )
    ordered = sorted(
        sessions.values(),
        key=lambda bucket: (
            str(bucket.get("graded_at") or bucket.get("exam_created_at") or ""),
            int(bucket["session_id"]),
        ),
        reverse=True,
    )
    total = len(ordered)
    offset = (page - 1) * page_size
    return StudentExamResultsResponse(
        student=_student_response(student_row),
        sessions=[
            StudentExamResultSession(**bucket)
            for bucket in ordered[offset : offset + page_size]
        ],
        total_sessions=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


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
