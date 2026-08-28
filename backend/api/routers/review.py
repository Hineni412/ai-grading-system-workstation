from __future__ import annotations

from dataclasses import asdict
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_review_application_service,
    get_scan_grading_workspace,
)
from backend.api.routers.sessions import _require_session
from backend.api.schemas.review import (
    ReviewConfirmRequest,
    ReviewConfirmResponse,
    ReviewItemListResponse,
    ReviewItemResponse,
    ReviewQuestionListResponse,
    ReviewQuestionSummary,
)
from backend.api.schemas.review_rubric import ReviewRubricSectionResponse
from backend.api.schemas.media import ReviewMediaLinksResponse
from backend.review.rubric import (
    ReviewRubricConfigError,
    ReviewRubricQuestionConflictError,
    load_review_rubric_section,
)
from backend.review.service import (
    ReviewApplicationService,
    ReviewConfirmationInput,
    ReviewDetailNotFoundError,
    ReviewRevisionConflictError,
    ReviewValidationError,
)
from backend.repositories.access import GradingRepositoryAccess
from backend.review.manual_context import current_manual_context
from backend.scan_grading.workspace import ScanGradingWorkspace


router = APIRouter(prefix="/api", tags=["review"])


@router.get(
    "/sessions/{session_id}/review/questions",
    response_model=ReviewQuestionListResponse,
)
def list_review_questions(
    session_id: int,
    scope: Literal[
        "teacher_pending",
        "ungraded",
        "ai_review",
        "teacher_final",
        "all",
    ]
    | None = None,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ReviewQuestionListResponse:
    session = _require_session(db, session_id)
    manual_context = current_manual_context(session_id, workspace)
    questions = review_service.list_questions(
        session_id,
        session,
        scope=scope,
        manual_context=manual_context,
    )
    items = [
        ReviewQuestionSummary.model_validate(item, from_attributes=True)
        for item in questions
    ]
    return ReviewQuestionListResponse(items=items, total=len(items))


@router.get(
    "/sessions/{session_id}/review/questions/{question_id}/items",
    response_model=ReviewItemListResponse,
)
def list_review_question_items(
    session_id: int,
    question_id: str,
    needs_review_only: bool = True,
    scope: Literal[
        "teacher_pending",
        "ungraded",
        "ai_review",
        "teacher_final",
        "all",
    ]
    | None = None,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ReviewItemListResponse:
    session = _require_session(db, session_id)
    manual_context = current_manual_context(session_id, workspace)
    review_items = review_service.list_items(
        session_id,
        session,
        requested_question_id=question_id,
        needs_review_only=needs_review_only,
        scope=scope,
        manual_context=manual_context,
    )
    items = [
        _review_item_response(item)
        for item in review_items
    ]
    return ReviewItemListResponse(items=items, total=len(items))


@router.get(
    "/sessions/{session_id}/review/questions/{question_id}/rubric",
    response_model=ReviewRubricSectionResponse | None,
)
def get_review_question_rubric(
    session_id: int,
    question_id: str,
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> ReviewRubricSectionResponse | None:
    _require_session(db, session_id)
    try:
        section = load_review_rubric_section(
            db,
            session_id,
            question_id,
        )
    except ReviewRubricQuestionConflictError as exc:
        raise ApiError(
            409,
            "review_rubric_question_conflict",
            "The scoring configuration contains ambiguous question identifiers",
            {"session_id": int(session_id), "question_id": question_id},
        ) from exc
    except ReviewRubricConfigError as exc:
        raise ApiError(
            500,
            "review_rubric_unavailable",
            "The scoring standard is unavailable",
            {"session_id": int(session_id), "question_id": question_id},
        ) from exc
    if section is None:
        return None
    return ReviewRubricSectionResponse.model_validate(
        asdict(section),
    )


@router.post(
    "/sessions/{session_id}/review/questions/{question_id}/confirm",
    response_model=ReviewConfirmResponse,
    response_model_exclude_none=True,
)
def confirm_review_question_items(
    session_id: int,
    question_id: str,
    request: ReviewConfirmRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ReviewConfirmResponse:
    session = _require_session(db, session_id)
    manual_context = current_manual_context(session_id, workspace)
    try:
        result = review_service.confirm(
            session_id,
            session,
            question_id,
            [
                ReviewConfirmationInput(
                    result_id=item.result_id,
                    detail_id=item.detail_id,
                    score_awarded=item.score_awarded,
                    review_item_id=item.review_item_id,
                    expected_revision=item.expected_revision,
                    student_id=item.student_id,
                    deduction_reason=item.deduction_reason,
                    error_category=item.error_category,
                    error_summary=item.error_summary,
                )
                for item in request.items
            ],
            manual_context=manual_context,
        )
    except ReviewDetailNotFoundError as exc:
        raise ApiError(
            404,
            "review_detail_not_found",
            "Review detail not found",
            {
                "session_id": exc.session_id,
                "question_id": exc.question_id,
                "result_id": exc.result_id,
                "detail_id": exc.detail_id,
            },
        ) from exc
    except ReviewValidationError as exc:
        raise ApiError(
            422,
            "review_validation_error",
            "Invalid review confirmation",
            {"session_id": int(session_id), "question_id": question_id},
        ) from exc
    except ReviewRevisionConflictError as exc:
        raise ApiError(
            409,
            "review_revision_changed",
            "Review item changed in another window; refresh before saving",
            {"session_id": int(session_id), "question_id": question_id},
        ) from exc
    return ReviewConfirmResponse.model_validate(result, from_attributes=True)


def _review_item_response(item: object) -> ReviewItemResponse:
    values = asdict(item)
    session_id = int(values["session_id"])
    metadata = values.get("metadata")
    if (
        isinstance(metadata, dict)
        and metadata.get("preflight_target_type")
        and metadata.get("preflight_target_id")
    ):
        target_type = quote(
            str(metadata["preflight_target_type"]),
            safe="",
        )
        target_id = quote(
            str(metadata["preflight_target_id"]),
            safe="",
        )
        question_id = quote(str(values["question_id"]), safe="")
        result_id = values.get("result_id")
        result_root = (
            f"/api/sessions/{session_id}/results/{int(result_id)}"
            if result_id is not None
            else None
        )
        try:
            source_region_id = int(metadata.get("source_region_id") or 0)
        except (TypeError, ValueError):
            source_region_id = 0
        crop_url = (
            f"/api/sessions/{session_id}/review/preflight/"
            f"{target_type}/{target_id}/{question_id}/crop"
        )
        if source_region_id > 0:
            crop_url = (
                f"{crop_url}?source_region_id={source_region_id}"
            )
        values["media"] = ReviewMediaLinksResponse(
            crop_url=crop_url,
            original_front_url=str(
                metadata.get("preflight_front_media_url") or ""
            ),
            original_back_url=metadata.get(
                "preflight_back_media_url"
            ),
            annotated_front_url=(
                f"{result_root}/pages/front?variant=annotated"
                if result_root
                else None
            ),
            annotated_back_url=(
                f"{result_root}/pages/back?variant=annotated"
                if result_root
                else None
            ),
        )
    else:
        result_id = int(values["result_id"])
        detail_id = int(values["detail_id"])
        result_root = f"/api/sessions/{session_id}/results/{result_id}"
        values["media"] = ReviewMediaLinksResponse(
            crop_url=f"{result_root}/details/{detail_id}/crop",
            original_front_url=f"{result_root}/pages/front",
            original_back_url=f"{result_root}/pages/back",
            annotated_front_url=f"{result_root}/pages/front?variant=annotated",
            annotated_back_url=f"{result_root}/pages/back?variant=annotated",
        )
    return ReviewItemResponse.model_validate(values)
