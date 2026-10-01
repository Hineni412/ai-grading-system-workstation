from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.analytics import SessionAnalysisService
from backend.api.dependencies import get_grading_db, get_session_analysis_service
from backend.api.routers.sessions import _require_session
from backend.api.schemas.analytics import (
    AnalysisScope,
    QuestionAnalysisItem,
    QuestionAnalysisListResponse,
    StudentAnalysisItem,
    StudentAnalysisListResponse,
)
from backend.repositories.access import GradingRepositoryAccess
from session_originals import originals_state

router = APIRouter(prefix="/api", tags=["analytics"])

OptionalTextQuery = Annotated[
    str | None,
    Query(min_length=1, pattern=r"\S"),
]


@router.get(
    "/sessions/{session_id}/analysis/questions",
    response_model=QuestionAnalysisListResponse,
)
def list_question_analysis(
    session_id: int,
    class_name: OptionalTextQuery = None,
    question_id: OptionalTextQuery = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    service: SessionAnalysisService = Depends(get_session_analysis_service),
) -> QuestionAnalysisListResponse:
    _require_session(db.sessions, session_id)
    clean_class_name = _clean_optional_text(class_name)
    clean_question_id = _clean_optional_text(question_id)
    rows = service.list_questions(session_id, class_name=clean_class_name)
    if clean_question_id is not None:
        rows = [row for row in rows if row.question_id == clean_question_id]
    total = len(rows)
    offset = (page - 1) * page_size
    return QuestionAnalysisListResponse(
        scope=AnalysisScope(
            session_id=session_id,
            class_name=clean_class_name,
            question_id=clean_question_id,
        ),
        classes=_session_classes(db, session_id),
        items=[
            QuestionAnalysisItem(**asdict(row))
            for row in rows[offset : offset + page_size]
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=_total_pages(total, page_size),
    )


@router.get(
    "/sessions/{session_id}/analysis/questions/{question_id}/students",
    response_model=StudentAnalysisListResponse,
)
def list_student_analysis(
    session_id: int,
    question_id: Annotated[str, Path(min_length=1, pattern=r"\S")],
    class_name: OptionalTextQuery = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    service: SessionAnalysisService = Depends(get_session_analysis_service),
) -> StudentAnalysisListResponse:
    _require_session(db.sessions, session_id)
    clean_question_id = question_id.strip()
    clean_class_name = _clean_optional_text(class_name)
    rows = service.list_students(
        session_id,
        clean_question_id,
        class_name=clean_class_name,
    )
    total = len(rows)
    offset = (page - 1) * page_size
    items = []
    for row in rows[offset : offset + page_size]:
        public_row = asdict(row)
        public_row["evidence_url"] = (
            f"/api/sessions/{session_id}/results/{row.result_id}"
            f"/details/{row.detail_id}/crop"
        ) if originals_state(db.db_path.parent.parent, session_id) not in {"clearing", "cleared"} else None
        items.append(StudentAnalysisItem(**public_row))
    return StudentAnalysisListResponse(
        scope=AnalysisScope(
            session_id=session_id,
            class_name=clean_class_name,
            question_id=clean_question_id,
        ),
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=_total_pages(total, page_size),
    )


def _session_classes(
    db: GradingRepositoryAccess,
    session_id: int,
) -> list[str]:
    return sorted(
        {
            str(row.get("class_name") or "未分班")
            for row in db.results.get_session_results(int(session_id))
        }
    )


def _clean_optional_text(value: str | None) -> str | None:
    return value.strip() if value is not None else None


def _total_pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size
