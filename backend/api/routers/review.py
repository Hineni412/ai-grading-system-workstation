from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_review_application_service
from backend.api.routers.sessions import _require_session
from backend.api.schemas.review import (
    ReviewConfirmRequest,
    ReviewConfirmResponse,
    ReviewItemListResponse,
    ReviewItemResponse,
    ReviewQuestionListResponse,
    ReviewQuestionSummary,
)
from backend.api.schemas.media import ReviewMediaLinksResponse
from backend.review.service import (
    ReviewApplicationService,
    ReviewConfirmationInput,
    ReviewDetailNotFoundError,
    ReviewValidationError,
)
from db_manager import DBManager


router = APIRouter(prefix="/api", tags=["review"])


@router.get(
    "/sessions/{session_id}/review/questions",
    response_model=ReviewQuestionListResponse,
)
def list_review_questions(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
) -> ReviewQuestionListResponse:
    session = _require_session(db, session_id)
    questions = review_service.list_questions(session_id, session)
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
    db: DBManager = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
) -> ReviewItemListResponse:
    session = _require_session(db, session_id)
    review_items = review_service.list_items(
        session_id,
        session,
        requested_question_id=question_id,
        needs_review_only=needs_review_only,
    )
    items = [
        _review_item_response(item)
        for item in review_items
    ]
    return ReviewItemListResponse(items=items, total=len(items))


@router.post(
    "/sessions/{session_id}/review/questions/{question_id}/confirm",
    response_model=ReviewConfirmResponse,
    response_model_exclude_none=True,
)
def confirm_review_question_items(
    session_id: int,
    question_id: str,
    request: ReviewConfirmRequest,
    db: DBManager = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
) -> ReviewConfirmResponse:
    session = _require_session(db, session_id)
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
                    deduction_reason=item.deduction_reason,
                    error_category=item.error_category,
                    error_summary=item.error_summary,
                )
                for item in request.items
            ],
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
    return ReviewConfirmResponse.model_validate(result, from_attributes=True)


def _review_item_response(item: object) -> ReviewItemResponse:
    values = asdict(item)
    session_id = int(values["session_id"])
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
