from __future__ import annotations

from fastapi import APIRouter, Request, Response

from .planning_schemas import (
    PlanningConfirmResponse,
    PlanningDraftCancelRequest,
    PlanningDraftConfirmRequest,
    PlanningDraftCreateRequest,
    PlanningDraftListResponse,
    PlanningDraftResponse,
)
from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
)


def create_planning_router() -> APIRouter:
    router = APIRouter()

    @router.post("/planning/drafts", response_model=PlanningDraftResponse)
    def create_draft(
        request: Request,
        body: PlanningDraftCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).planning.create_draft(
                token="",
                operation_id=body.operation_id,
                raw_input=body.raw_input,
                reference_at=body.reference_at,
                final_deadline=body.final_deadline,
            )
        )

    @router.get("/planning/drafts", response_model=PlanningDraftListResponse)
    def list_drafts(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).planning.list_drafts(
                token="",
            )
        )

    @router.get(
        "/planning/drafts/{draft_id}",
        response_model=PlanningDraftResponse,
    )
    def get_draft(
        draft_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).planning.get_draft(
                token="",
                draft_id=draft_id,
            )
        )

    @router.post(
        "/planning/drafts/{draft_id}/cancel",
        response_model=PlanningDraftResponse,
    )
    def cancel_draft(
        draft_id: str,
        request: Request,
        body: PlanningDraftCancelRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).planning.cancel_draft(
                token="",
                draft_id=draft_id,
                operation_id=body.operation_id,
                revision=body.revision,
            )
        )

    @router.post(
        "/planning/drafts/{draft_id}/confirm",
        response_model=PlanningConfirmResponse,
    )
    def confirm_draft(
        draft_id: str,
        request: Request,
        body: PlanningDraftConfirmRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).planning.confirm_draft(
                token="",
                draft_id=draft_id,
                operation_id=body.operation_id,
                revision=body.revision,
                plan_title=body.plan_title,
                actions=[
                    item.model_dump()
                    for item in body.actions
                ],
            )
        )

    return router


__all__ = ["create_planning_router"]
