from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from backend.api.dependencies import (
    get_grading_db,
    get_results_center_service,
    get_scan_grading_workspace,
)
from backend.api.routers.sessions import _require_session
from backend.api.schemas.results_center import ResultsCenterResponse
from backend.grading_workflow import effective_preflight_papers
from backend.repositories.access import GradingRepositoryAccess
from backend.results_center.service import ResultsCenterService
from backend.scan_grading.workspace import (
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
)


router = APIRouter(prefix="/api", tags=["results-center"])


@router.get(
    "/sessions/{session_id}/results-center",
    response_model=ResultsCenterResponse,
)
def get_results_center(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    results_service: ResultsCenterService = Depends(
        get_results_center_service
    ),
    workspace: ScanGradingWorkspace = Depends(get_scan_grading_workspace),
) -> ResultsCenterResponse:
    session = _require_session(db, session_id)
    snapshot = results_service.get_snapshot(
        session_id,
        session,
        manual_context=_current_manual_context(session_id, workspace),
    )
    return ResultsCenterResponse.model_validate(asdict(snapshot))


def _current_manual_context(
    session_id: int,
    workspace: ScanGradingWorkspace,
) -> dict[str, object] | None:
    try:
        state = workspace.get_workspace(session_id)
        upload_batch = state.get("upload_batch")
        if (
            not isinstance(upload_batch, dict)
            or upload_batch.get("state") != "frozen"
        ):
            return None
        preflight = workspace.get_preflight(session_id)
    except ScanGradingWorkspaceError:
        return None
    return {
        "scan_batch_id": str(upload_batch["batch_id"]),
        "papers": effective_preflight_papers(preflight),
    }
