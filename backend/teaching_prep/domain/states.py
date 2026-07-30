from __future__ import annotations

from enum import StrEnum

from .errors import TeachingPrepStateError


class LessonPreparationState(StrEnum):
    SELECTING_SOURCES = "selecting_sources"
    SOURCES_LOCKED = "sources_locked"
    EVIDENCE_READY = "evidence_ready"
    DRAFT_READY = "draft_ready"
    PLAN_IN_REVIEW = "plan_in_review"
    PLAN_APPROVED = "plan_approved"
    EXECUTING = "executing"
    VERIFIED = "verified"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    ANALYSIS_FAILED = "analysis_failed"
    EXECUTION_FAILED = "execution_failed"
    VERIFICATION_FAILED = "verification_failed"
    CANCELLED = "cancelled"


_FORWARD_TRANSITIONS: dict[
    LessonPreparationState,
    frozenset[LessonPreparationState],
] = {
    LessonPreparationState.SELECTING_SOURCES: frozenset(
        {
            LessonPreparationState.SOURCES_LOCKED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.SOURCES_LOCKED: frozenset(
        {
            LessonPreparationState.EVIDENCE_READY,
            LessonPreparationState.ANALYSIS_FAILED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.EVIDENCE_READY: frozenset(
        {
            LessonPreparationState.DRAFT_READY,
            LessonPreparationState.ANALYSIS_FAILED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.DRAFT_READY: frozenset(
        {
            LessonPreparationState.PLAN_IN_REVIEW,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.PLAN_IN_REVIEW: frozenset(
        {
            LessonPreparationState.PLAN_APPROVED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.PLAN_APPROVED: frozenset(
        {
            LessonPreparationState.EXECUTING,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.EXECUTING: frozenset(
        {
            LessonPreparationState.VERIFIED,
            LessonPreparationState.EXECUTION_FAILED,
            LessonPreparationState.VERIFICATION_FAILED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.VERIFIED: frozenset(
        {
            LessonPreparationState.PUBLISHED,
            LessonPreparationState.VERIFICATION_FAILED,
            LessonPreparationState.CANCELLED,
        }
    ),
    LessonPreparationState.PUBLISHED: frozenset(
        {LessonPreparationState.ARCHIVED}
    ),
    LessonPreparationState.ARCHIVED: frozenset(),
    LessonPreparationState.ANALYSIS_FAILED: frozenset(
        {LessonPreparationState.CANCELLED}
    ),
    LessonPreparationState.EXECUTION_FAILED: frozenset(
        {LessonPreparationState.CANCELLED}
    ),
    LessonPreparationState.VERIFICATION_FAILED: frozenset(
        {LessonPreparationState.CANCELLED}
    ),
    LessonPreparationState.CANCELLED: frozenset(),
}


def require_transition(
    current: LessonPreparationState,
    target: LessonPreparationState,
) -> None:
    if target == current:
        return
    if target not in _FORWARD_TRANSITIONS[current]:
        raise TeachingPrepStateError(
            f"cannot transition preparation from {current} to {target}"
        )
