from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from answer_region_commit_service import AnswerRegionCommitService, AnswerRegionCommitResult
from answer_region_draft_service import AnswerRegionDraftService, DraftLoadResult
from backend.api.app import ApiError
from backend.api.dependencies import get_grading_db, get_templates_dir
from backend.api.routers.sessions import _require_session, _template_response
from backend.api.schemas.sessions import SessionTemplateResponse
from backend.api.schemas.templates import (
    RegionCommitRequest,
    RegionCommitResponse,
    RegionDraftRequest,
    RegionDraftResponse,
    RegionIssueResponse,
    TemplateUpdateRequest,
)
from db_manager import DBManager
from path_manager import resolve_stored_file_path


router = APIRouter(prefix="/api", tags=["templates"])


def _session_dir(templates_dir: Path, session_id: int) -> Path:
    path = Path(templates_dir) / f"session_{int(session_id)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_template(db: DBManager, session_id: int) -> dict[str, Any]:
    _require_session(db, session_id)
    template = db.get_session_template(int(session_id))
    if template is None:
        raise ApiError(
            404,
            "template_not_found",
            "Session template not found",
            {"session_id": int(session_id)},
        )
    return template


def _template_paths(template: dict[str, Any], session_dir: Path) -> tuple[Path, Path]:
    paths = []
    for field_name in ("front_template_path", "back_template_path"):
        raw_path = str(template.get(field_name) or "").strip()
        path = resolve_stored_file_path(
            raw_path,
            search_roots=[session_dir, session_dir.parent],
        )
        if not path.is_file():
            raise ApiError(
                404,
                "template_file_not_found",
                "Template file not found",
                {"field": field_name},
            )
        paths.append(path)
    return paths[0], paths[1]


def _draft_service(templates_dir: Path, session_id: int) -> AnswerRegionDraftService:
    return AnswerRegionDraftService(_session_dir(templates_dir, session_id))


def _template_fingerprint(
    template: dict[str, Any],
    draft_service: AnswerRegionDraftService,
) -> str:
    front_path, back_path = _template_paths(
        template,
        Path(draft_service.draft_path).parent,
    )
    return draft_service.compute_template_fingerprint(front_path, back_path)


def _draft_response(
    *,
    session_id: int,
    template: dict[str, Any],
    draft_service: AnswerRegionDraftService,
    load_result: DraftLoadResult,
    template_fingerprint: str,
) -> RegionDraftResponse:
    return RegionDraftResponse(
        status=load_result.status,
        session_id=int(session_id),
        template_id=int(template["id"]),
        template_fingerprint=template_fingerprint,
        draft_path=str(draft_service.draft_path),
        draft=load_result.draft,
        quarantined_path=str(load_result.quarantined_path) if load_result.quarantined_path else None,
    )


def _commit_response(result: AnswerRegionCommitResult, region_count: int) -> RegionCommitResponse:
    return RegionCommitResponse(
        committed=result.committed,
        snapshot_pending=result.snapshot_pending,
        snapshot_path=str(result.snapshot_path) if result.snapshot_path else None,
        error=result.error,
        issues=[
            RegionIssueResponse(
                code=issue.code,
                message=issue.message,
                region_uuid=issue.region_uuid,
                question_id=issue.question_id,
            )
            for issue in result.validation.issues
        ],
        region_count=region_count,
    )


@router.put("/sessions/{session_id}/template", response_model=SessionTemplateResponse)
def update_session_template(
    session_id: int,
    request: TemplateUpdateRequest,
    db: DBManager = Depends(get_grading_db),
) -> SessionTemplateResponse:
    _require_session(db, session_id)
    db.upsert_session_template(
        int(session_id),
        request.front_template_path,
        request.back_template_path,
    )
    if (
        request.ai_analysis_path is not None
        or request.template_config_path is not None
        or request.regions_path is not None
    ):
        db.update_session_template_analysis(
            int(session_id),
            ai_analysis_path=request.ai_analysis_path,
            template_config_path=request.template_config_path,
            regions_path=request.regions_path,
        )
    return _template_response(_require_template(db, session_id))


@router.get("/sessions/{session_id}/regions/draft", response_model=RegionDraftResponse)
def get_answer_region_draft(
    session_id: int,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionDraftResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    fingerprint = _template_fingerprint(template, draft_service)
    load_result = draft_service.load(expected_template_fingerprint=fingerprint)
    return _draft_response(
        session_id=session_id,
        template=template,
        draft_service=draft_service,
        load_result=load_result,
        template_fingerprint=fingerprint,
    )


@router.put("/sessions/{session_id}/regions/draft", response_model=RegionDraftResponse)
def save_answer_region_draft(
    session_id: int,
    request: RegionDraftRequest,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionDraftResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    fingerprint = _template_fingerprint(template, draft_service)
    draft_service.save(
        session_id=int(session_id),
        template_fingerprint=fingerprint,
        revision=request.revision,
        regions=request.regions,
    )
    load_result = draft_service.load(expected_template_fingerprint=fingerprint)
    return _draft_response(
        session_id=session_id,
        template=template,
        draft_service=draft_service,
        load_result=load_result,
        template_fingerprint=fingerprint,
    )


@router.post("/sessions/{session_id}/regions/commit", response_model=RegionCommitResponse)
def commit_answer_regions(
    session_id: int,
    request: RegionCommitRequest,
    db: DBManager = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
) -> RegionCommitResponse:
    template = _require_template(db, session_id)
    draft_service = _draft_service(templates_dir, session_id)
    _template_fingerprint(template, draft_service)
    service = AnswerRegionCommitService(
        db,
        Path(draft_service.draft_path).parent,
        draft_service,
    )
    result = service.commit(
        session_id=int(session_id),
        template_id=int(template["id"]),
        regions=request.regions,
        image_sizes=request.image_sizes,
        template_matches=request.template_matches,
        expected_template_fingerprint=request.expected_template_fingerprint,
    )
    region_count = len(db.list_answer_regions(int(session_id))) if result.committed else 0
    return _commit_response(result, region_count)
