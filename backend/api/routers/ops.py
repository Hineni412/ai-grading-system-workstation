from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_ops_self_check_service
from backend.api.schemas.ops import OpsBackupListResponse, OpsSelfCheckResponse
from backend.ops.service import OpsSelfCheckService


router = APIRouter(prefix="/api/ops", tags=["ops"])
OPS_UNAVAILABLE_RESPONSE = {
    503: {
        "model": ErrorResponse,
        "description": "Ops status is temporarily unavailable",
    }
}


@router.get(
    "/self-check",
    response_model=OpsSelfCheckResponse,
    responses=OPS_UNAVAILABLE_RESPONSE,
)
def get_self_check(
    service: OpsSelfCheckService = Depends(get_ops_self_check_service),
) -> OpsSelfCheckResponse:
    try:
        return OpsSelfCheckResponse.model_validate(service.build_snapshot())
    except Exception as exc:
        raise ApiError(
            503,
            "ops_self_check_unavailable",
            "System self-check is temporarily unavailable",
        ) from exc


@router.get(
    "/backups",
    response_model=OpsBackupListResponse,
    responses=OPS_UNAVAILABLE_RESPONSE,
)
def get_backups(
    limit: int = Query(default=50, ge=1, le=100),
    service: OpsSelfCheckService = Depends(get_ops_self_check_service),
) -> OpsBackupListResponse:
    try:
        return OpsBackupListResponse.model_validate(service.list_backups(limit))
    except Exception as exc:
        raise ApiError(
            503,
            "ops_backup_list_unavailable",
            "Backup list is temporarily unavailable",
        ) from exc


__all__ = ["router"]
