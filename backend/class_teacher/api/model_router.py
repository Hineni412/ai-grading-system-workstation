from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from .model_schemas import (
    ModelConfirmRequest,
    ModelOperationResponse,
    ModelPreviewRequest,
    ModelPreviewResponse,
)


def create_model_router() -> APIRouter:
    from .router import (
        _call,
        _no_store,
        _require_trusted_mutation,
        _service,
        _token,
    )

    router = APIRouter(prefix="/model")

    @router.post("/previews", response_model=ModelPreviewResponse)
    def prepare_preview(
        request: Request,
        body: ModelPreviewRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def prepare():
            service = _service(request)
            identity_terms: tuple[str, ...] = ()
            if body.subject_id is not None:
                subject = service.support.get_subject(
                    token=_token(session_token),
                    subject_id=body.subject_id,
                )
                identity_terms = (str(subject["display_name"]),)
            return service.model_approval.prepare(
                token=_token(session_token),
                purpose=body.purpose,
                source_text=body.source_text,
                context=(
                    {
                        "subject_id": body.subject_id,
                        "teacher_quote": body.source_text,
                    }
                    if body.subject_id is not None
                    else None
                ),
                identity_terms=identity_terms,
            )

        return _call(prepare)

    @router.post(
        "/previews/{preview_id}/confirm",
        response_model=ModelOperationResponse,
    )
    def confirm_preview(
        preview_id: str,
        request: Request,
        body: ModelConfirmRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).model_approval.confirm(
                token=_token(session_token),
                preview_id=preview_id,
                fingerprint=body.fingerprint,
                operation_id=body.operation_id,
            )
        )

    @router.get("/operations/{operation_id}", response_model=ModelOperationResponse)
    def operation_status(
        operation_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).model_approval.status(
                token=_token(session_token),
                operation_id=operation_id,
            )
        )

    return router


__all__ = ["create_model_router"]
