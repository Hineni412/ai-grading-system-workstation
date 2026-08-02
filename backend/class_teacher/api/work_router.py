from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from .work_schemas import (
    WorkNodeResponse,
    WorkNodeDetailResponse,
    WorkCommandRequest,
    WorkNodeUpdateRequest,
    WorkPlanConfirmRequest,
    WorkPlanConfirmResponse,
    WorkPlanInvokeRequest,
    WorkPlanPreviewRequest,
    WorkPlanPreviewResponse,
    WorkPlanResultResponse,
    WorkProgressPlanPreviewRequest,
    WorkSnapshotResponse,
)


def create_work_router() -> APIRouter:
    # Imported here so this router reuses the class-teacher local-origin and
    # safe-error policy without creating a second security implementation.
    from .router import _call, _no_store, _require_trusted_mutation, _service

    router = APIRouter(prefix="/work")

    @router.get("", response_model=WorkSnapshotResponse)
    def query_work(
        request: Request,
        response: Response,
        start_date: str | None = Query(default=None),
        end_date: str | None = Query(default=None),
        as_of: str | None = Query(default=None),
        view: str | None = Query(default=None),
        anchor: str | None = Query(default=None),
        cursor: str | None = Query(default=None),
    ):
        _no_store(response)
        return _call(
            lambda: (
                _service(request).work.read(
                    view=view,
                    anchor=anchor,
                    cursor=cursor,
                )
                if view is not None
                else _service(request).work.query(
                    start_date=start_date,
                    end_date=end_date,
                    as_of=as_of,
                )
            )
        )

    @router.get("/nodes/{node_id}", response_model=WorkNodeDetailResponse)
    def node_detail(node_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).work.detail(node_id=node_id))

    @router.post("/nodes/{node_id}/commands")
    def node_command(
        node_id: str,
        request: Request,
        body: WorkCommandRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.command(
                node_id=node_id,
                **body.model_dump(),
            )
        )

    @router.post("/plans/previews", response_model=WorkPlanPreviewResponse)
    def preview_work_plan(
        request: Request,
        body: WorkPlanPreviewRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.prepare_plan(
                text=body.text,
                due_date=body.due_date,
            )
        )

    @router.post(
        "/plans/previews/{preview_id}/confirm",
        response_model=WorkPlanResultResponse,
    )
    def invoke_work_plan(
        preview_id: str,
        request: Request,
        body: WorkPlanInvokeRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.invoke_plan(
                preview_id=preview_id,
                fingerprint=body.fingerprint,
                operation_id=body.operation_id,
            )
        )

    @router.get(
        "/plans/operations/{operation_id}",
        response_model=WorkPlanResultResponse,
    )
    def work_plan_status(
        operation_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).work.plan_status(
                operation_id=operation_id,
            )
        )

    @router.post("/plans/confirm", response_model=WorkPlanConfirmResponse)
    def confirm_work_plan(
        request: Request,
        body: WorkPlanConfirmRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.confirm_plan(
                model_operation_id=body.model_operation_id,
                plan_fingerprint=body.plan_fingerprint,
                operation_id=body.operation_id,
            )
        )

    @router.patch("/nodes/{node_id}", response_model=WorkNodeResponse)
    def update_node(
        node_id: str,
        request: Request,
        body: WorkNodeUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.update_node(
                node_id=node_id,
                revision=body.revision,
                status=body.status,
                due_date=body.due_date,
                operation_id=body.operation_id,
            )
        )

    @router.post(
        "/nodes/{node_id}/plans/previews",
        response_model=WorkPlanPreviewResponse,
    )
    def preview_progress_plan(
        node_id: str,
        request: Request,
        body: WorkProgressPlanPreviewRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).work.prepare_plan(
                text=body.text,
                due_date=body.due_date,
                parent_node_id=node_id,
                parent_revision=body.revision,
            )
        )

    return router


__all__ = ["create_work_router"]
