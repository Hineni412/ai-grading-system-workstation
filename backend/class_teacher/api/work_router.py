from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from .work_schemas import (
    WorkNodeResponse,
    WorkNodeDetailResponse,
    WorkCommandRequest,
    WorkNodeUpdateRequest,
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

        def _read() -> dict[str, object]:
            service = _service(request)
            # 惰性评估「仍需了解」老化提醒；幂等，不会重复造卡。
            service.student_cards.evaluate_followup_reminders(token="")
            if view is not None:
                return service.work.read(view=view, anchor=anchor, cursor=cursor)
            return service.work.query(
                start_date=start_date,
                end_date=end_date,
                as_of=as_of,
            )

        return _call(_read)

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

    return router


__all__ = ["create_work_router"]
