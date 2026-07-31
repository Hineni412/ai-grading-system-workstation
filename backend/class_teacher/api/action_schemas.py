from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ActionStatus = Literal[
    "pending",
    "in_progress",
    "waiting",
    "partially_completed",
    "completed",
    "cancelled",
    "superseded",
]


class WorkPlanCreateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    final_deadline: str | None = None


class WorkPlanUpdateRequest(WorkPlanCreateRequest):
    revision: int = Field(ge=1)


class WorkPlanResponse(BaseModel):
    plan_id: str
    revision: int
    title: str
    description: str | None
    final_deadline: str | None
    created_at: str
    updated_at: str


class WorkPlanListResponse(BaseModel):
    items: list[WorkPlanResponse]


class ActionCreateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    plan_id: str
    title: str = Field(min_length=1, max_length=240)
    details: str | None = Field(default=None, max_length=8000)
    due_at: str | None = None
    depends_on_action_ids: list[str] = Field(default_factory=list, max_length=50)


class ActionUpdateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    status: ActionStatus
    due_at: str | None = None
    waiting_for_kind: str | None = Field(default=None, max_length=120)
    review_at: str | None = None
    completion_result: str | None = Field(default=None, max_length=8000)
    reason: str | None = Field(default=None, max_length=1000)


class ActionResponse(BaseModel):
    action_id: str
    plan_id: str
    revision: int
    title: str
    details: str | None
    status: ActionStatus
    due_at: str | None
    waiting_for_kind: str | None
    review_at: str | None
    completion_result: str | None
    completed_at: str | None
    reopened_count: int
    transition_history: list[dict[str, object]]
    depends_on_action_ids: list[str]
    created_at: str
    updated_at: str


class ActionListResponse(BaseModel):
    items: list[ActionResponse]


class DashboardResponse(BaseModel):
    as_of: str
    today: list[ActionResponse]
    overdue: list[ActionResponse]
    upcoming: list[ActionResponse]
    waiting: list[ActionResponse]
    unscheduled: list[ActionResponse]


class SchoolCalendarResponse(BaseModel):
    configured: bool
    revision: int
    school_day_end: str | None
    locked_dates: list[str]
    working_weekdays: list[int] | None


class SchoolCalendarSaveRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=0)
    school_day_end: str | None = None
    locked_dates: list[str] = Field(default_factory=list, max_length=400)
    working_weekdays: list[int] | None = Field(default=None, max_length=7)


__all__ = [
    "ActionCreateRequest",
    "ActionListResponse",
    "ActionResponse",
    "ActionUpdateRequest",
    "DashboardResponse",
    "SchoolCalendarResponse",
    "SchoolCalendarSaveRequest",
    "WorkPlanCreateRequest",
    "WorkPlanListResponse",
    "WorkPlanResponse",
    "WorkPlanUpdateRequest",
]
