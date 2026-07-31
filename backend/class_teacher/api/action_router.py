from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request, Response

from .action_schemas import (
    ActionCreateRequest,
    ActionListResponse,
    ActionResponse,
    ActionUpdateRequest,
    DashboardResponse,
    SchoolCalendarResponse,
    SchoolCalendarSaveRequest,
    WorkPlanCreateRequest,
    WorkPlanListResponse,
    WorkPlanResponse,
    WorkPlanUpdateRequest,
)
from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
    _token,
)


def create_action_router() -> APIRouter:
    router = APIRouter()

    @router.post("/plans", response_model=WorkPlanResponse)
    def create_plan(
        request: Request,
        body: WorkPlanCreateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).actions.create_plan(
                token=_token(session_token),
                operation_id=body.operation_id,
                title=body.title,
                description=body.description,
                final_deadline=body.final_deadline,
            )
        )

    @router.get("/plans", response_model=WorkPlanListResponse)
    def list_plans(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).actions.list_plans(
                token=_token(session_token),
            )
        )

    @router.patch("/plans/{plan_id}", response_model=WorkPlanResponse)
    def update_plan(
        plan_id: str,
        request: Request,
        body: WorkPlanUpdateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).actions.update_plan(
                token=_token(session_token),
                plan_id=plan_id,
                operation_id=body.operation_id,
                revision=body.revision,
                title=body.title,
                description=body.description,
                final_deadline=body.final_deadline,
            )
        )

    @router.post("/actions", response_model=ActionResponse)
    def create_action(
        request: Request,
        body: ActionCreateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).actions.create_action(
                token=_token(session_token),
                operation_id=body.operation_id,
                plan_id=body.plan_id,
                title=body.title,
                details=body.details,
                due_at=body.due_at,
                depends_on_action_ids=body.depends_on_action_ids,
            )
        )

    @router.get("/actions", response_model=ActionListResponse)
    def list_actions(
        request: Request,
        response: Response,
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).actions.list_actions(
                token=_token(session_token),
                date_from=date_from,
                date_to=date_to,
            )
        )

    @router.patch("/actions/{action_id}", response_model=ActionResponse)
    def update_action(
        action_id: str,
        request: Request,
        body: ActionUpdateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).actions.update_action(
                token=_token(session_token),
                action_id=action_id,
                operation_id=body.operation_id,
                revision=body.revision,
                status=body.status,
                due_at=body.due_at,
                waiting_for_kind=body.waiting_for_kind,
                review_at=body.review_at,
                completion_result=body.completion_result,
                reason=body.reason,
            )
        )

    @router.get("/dashboard", response_model=DashboardResponse)
    def dashboard(
        request: Request,
        response: Response,
        as_of: str | None = Query(default=None),
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).actions.dashboard(
                token=_token(session_token),
                as_of=as_of,
            )
        )

    @router.get("/calendar", response_model=SchoolCalendarResponse)
    def get_calendar(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).actions.get_calendar(
                token=_token(session_token),
            )
        )

    @router.put("/calendar", response_model=SchoolCalendarResponse)
    def save_calendar(
        request: Request,
        body: SchoolCalendarSaveRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).actions.save_calendar(
                token=_token(session_token),
                operation_id=body.operation_id,
                revision=body.revision,
                school_day_end=body.school_day_end,
                locked_dates=body.locked_dates,
                working_weekdays=body.working_weekdays,
            )
        )

    return router


__all__ = ["create_action_router"]
