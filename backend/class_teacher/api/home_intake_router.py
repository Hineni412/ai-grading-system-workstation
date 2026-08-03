from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response

from .home_intake_schemas import (
    HomeIntakeAdoptRequest,
    HomeIntakeDispatchRequest,
    HomeIntakeFollowUpRequest,
    HomeIntakeManualFallbackRequest,
    HomeIntakeManualFallbackResponse,
    HomeIntakeOperationResponse,
    HomeIntakePreviewRequest,
    HomeIntakePreviewResponse,
)


def create_home_intake_router() -> APIRouter:
    from .router import (
        _call,
        _no_store,
        _require_trusted_mutation,
        _serialized_operation,
        _service,
        _token,
    )

    router = APIRouter(
        prefix="/home/intake",
        dependencies=[Depends(_serialized_operation)],
    )

    @router.post("/previews", response_model=HomeIntakePreviewResponse)
    def prepare(
        request: Request,
        body: HomeIntakePreviewRequest,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake.prepare(
                token=_token(session_token),
                text=body.text,
                due_date=body.due_date,
                reference_date=body.reference_date,
            )
        )

    @router.post(
        "/previews/{preview_id}/dispatch",
        response_model=HomeIntakeOperationResponse,
    )
    def dispatch(
        preview_id: str,
        request: Request,
        body: HomeIntakeDispatchRequest,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake.dispatch(
                token=_token(session_token),
                preview_id=preview_id,
                fingerprint=body.fingerprint,
                operation_id=body.operation_id,
            )
        )

    @router.get(
        "/operations/{operation_id}",
        response_model=HomeIntakeOperationResponse,
    )
    def status(
        operation_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake.status(
                token=_token(session_token),
                operation_id=operation_id,
            )
        )

    @router.post(
        "/operations/{operation_id}/follow-up-previews",
        response_model=HomeIntakePreviewResponse,
    )
    def follow_up(
        operation_id: str,
        request: Request,
        body: HomeIntakeFollowUpRequest,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake.prepare_follow_up(
                token=_token(session_token),
                operation_id=operation_id,
                answer=body.answer,
                reference_date=body.reference_date,
                selected_step_keys=body.selected_step_keys,
                selected_calendar_keys=body.selected_calendar_keys,
            )
        )

    @router.post(
        "/manual-fallback/confirm",
        response_model=HomeIntakeManualFallbackResponse,
    )
    def manual_fallback(
        request: Request,
        body: HomeIntakeManualFallbackRequest,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake.confirm_manual_fallback(
                token=_token(session_token),
                source_operation_id=body.source_operation_id,
                operation_id=body.operation_id,
                title=body.title,
                due_date=body.due_date,
            )
        )

    @router.post("/adopt")
    def adopt(
        request: Request,
        body: HomeIntakeAdoptRequest,
        response: Response,
        session_token: str | None = Header(default=None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).home_intake_finalizer.adopt(
                token=_token(session_token), **body.model_dump()
            )
        )

    return router


__all__ = ["create_home_intake_router"]
