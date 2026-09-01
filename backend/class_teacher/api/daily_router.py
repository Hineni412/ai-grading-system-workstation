from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from .daily_schemas import (
    CustomSlotCreateRequest,
    NoteCreateRequest,
    OverridesApplyRequest,
    RegularCellRequest,
    WeekAnchorPutRequest,
)
from .router import _call, _no_store, _require_trusted_mutation, _service


def create_daily_router() -> APIRouter:
    router = APIRouter(prefix="/daily")

    @router.get("/timetable")
    def get_timetable(
        request: Request,
        response: Response,
        week_start: str | None = None,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.get_week(
                week_start=week_start,
            )
        )

    @router.put("/timetable/regular-cell")
    def put_regular_cell(
        request: Request,
        body: RegularCellRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.set_regular_cell(
                **body.model_dump()
            )
        )

    @router.post("/timetable/custom-slots")
    def post_custom_slot(
        request: Request,
        body: CustomSlotCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.create_custom_slot(
                **body.model_dump()
            )
        )

    @router.delete("/timetable/custom-slots/{custom_slot_id}")
    def delete_custom_slot(
        custom_slot_id: str,
        request: Request,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.delete_custom_slot(
                custom_slot_id
            )
        )

    @router.post("/timetable/overrides")
    def post_overrides(
        request: Request,
        body: OverridesApplyRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.apply_overrides(
                **body.model_dump()
            )
        )

    @router.delete("/timetable/overrides/{override_id}")
    def delete_override(
        override_id: str,
        request: Request,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.delete_override(override_id)
        )

    @router.get("/timetable/week-anchor")
    def get_week_anchor(request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).daily_timetable.get_week_anchor())

    @router.put("/timetable/week-anchor")
    def put_week_anchor(
        request: Request,
        body: WeekAnchorPutRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.set_week_anchor(
                anchor_date=body.date,
                week_no=body.week_no,
            )
        )

    @router.post("/notes")
    def post_note(
        request: Request,
        body: NoteCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).daily_timetable.add_note(**body.model_dump())
        )

    @router.get("/notes/recent")
    def get_recent_notes(
        request: Request,
        response: Response,
        class_labels: str = "",
        limit: int = Query(default=10, ge=1, le=50),
    ):
        _no_store(response)
        labels = [
            part.strip()
            for part in str(class_labels or "").split(",")
            if part.strip()
        ]
        return _call(
            lambda: _service(request).daily_timetable.recent_notes(
                class_labels=labels,
                limit=limit,
            )
        )

    return router


__all__ = ["create_daily_router"]
