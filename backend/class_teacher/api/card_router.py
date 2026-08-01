from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from .card_schemas import (
    StudentCardConfirmRequest,
    StudentCardEntryResponse,
    StudentCardListResponse,
)
from .router import _call, _no_store, _require_trusted_mutation, _service, _token


def create_card_router() -> APIRouter:
    router = APIRouter(prefix="/student-cards")

    @router.get("", response_model=StudentCardListResponse)
    def list_cards(
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).student_cards.list_cards(
                token=_token(session_token),
            )
        )

    @router.post(
        "/{subject_id}/entries/confirm",
        response_model=StudentCardEntryResponse,
    )
    def confirm_entry(
        subject_id: str,
        request: Request,
        body: StudentCardConfirmRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).student_cards.confirm_structure(
                token=_token(session_token),
                subject_id=subject_id,
                model_operation_id=body.model_operation_id,
                operation_id=body.operation_id,
                portrait=body.portrait.model_dump(),
                sop=body.sop.model_dump(),
            )
        )

    return router


__all__ = ["create_card_router"]
