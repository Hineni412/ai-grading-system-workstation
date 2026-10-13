"""智学网成绩导入接口：上传 Excel、预览计划、确认写入。

导入考试（kind == "zhixue_import"）没有扫描批改环节：成绩在这里以
教师最终分的形式写入冻结批次上下文。同一批计划逻辑由
``build_import_plan`` 在 preview 与 confirm 中重算，靠 preview_revision
发现输入或评分结构变化。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_data_root,
    get_grading_db,
    get_job_manager,
    get_templates_dir,
    get_upload_config_dir,
)
from backend.api.routers.sessions import _require_active_session
from backend.api.schemas.score_import import (
    ScoreImportConfirmRequest,
    ScoreImportPlanRequest,
)
from backend.config_workspace.publish import ConfigRevisionConflict
from backend.config_workspace.editor import ConfigEditorValidationError
from backend.jobs.manager import JobManager
from backend.jobs.store import ConfigSessionBusyError
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.sessions import SessionDeletionActiveWork
from backend.score_import.context import (
    KIND_ZHIXUE_IMPORT,
    _atomic_write_json,
    list_upload_metas,
    read_import_context,
    read_receipt,
    session_kind,
    session_exam_date,
    upload_meta_path,
    upload_path,
)
from backend.score_import.parsing import parse_upload, sheet_inventory
from backend.score_import.planning import build_import_plan
from backend.score_import.writer import ScoreImportWriteError, confirm_import

router = APIRouter(prefix="/api", tags=["score-import"])

MAX_IMPORT_UPLOAD_BYTES = 50 * 1024 * 1024

SCORE_IMPORT_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Session or upload not found"},
    409: {"model": ErrorResponse, "description": "Import conflicts with current session state"},
    413: {"model": ErrorResponse, "description": "Upload too large"},
    422: {"model": ErrorResponse, "description": "Import input invalid or blocked"},
}


def reject_if_import_session(templates_dir: Path, session_id: int) -> None:
    """Shared guard: import-kind sessions have no scan/grading steps."""
    if session_kind(Path(templates_dir), int(session_id)) == KIND_ZHIXUE_IMPORT:
        raise ApiError(
            422,
            "session_kind_import",
            "这是智学网成绩导入考试，没有扫描和 AI 批改环节；成绩请通过 Excel 导入。",
            {"session_id": int(session_id)},
        )


def require_import_config_editable(
    db: GradingRepositoryAccess,
    templates_dir: Path,
    session_id: int,
) -> None:
    """Imported results freeze the scoring structure until re-import."""
    if session_kind(Path(templates_dir), int(session_id)) != KIND_ZHIXUE_IMPORT:
        return
    if db.results.get_session_results(int(session_id)):
        raise ApiError(
            422,
            "score_import_structure_locked",
            "已导入成绩，修改评分结构请重新导入。",
            {"session_id": int(session_id)},
        )


def _require_import_session(
    db: GradingRepositoryAccess,
    templates_dir: Path,
    session_id: int,
) -> dict[str, Any]:
    session = _require_active_session(db.sessions, session_id)
    if session_kind(templates_dir, int(session_id)) != KIND_ZHIXUE_IMPORT:
        raise ApiError(
            422,
            "session_kind_not_import",
            "这不是智学网成绩导入考试。",
            {"session_id": int(session_id)},
        )
    return session


def _has_results(db: GradingRepositoryAccess, session_id: int) -> bool:
    return bool(db.results.get_session_results(int(session_id)))


@router.get(
    "/sessions/{session_id}/score-import",
    responses=SCORE_IMPORT_ERROR_RESPONSES,
)
def get_score_import_state(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
    data_root: Path = Depends(get_data_root),
) -> dict[str, Any]:
    _require_active_session(db.sessions, session_id)
    return {
        "kind": session_kind(templates_dir, int(session_id)),
        "exam_date": session_exam_date(templates_dir, int(session_id)),
        "uploads": list_upload_metas(data_root, int(session_id)),
        "receipt": read_receipt(data_root, int(session_id)),
        "has_results": _has_results(db, session_id),
    }


@router.post(
    "/sessions/{session_id}/score-import/uploads",
    status_code=201,
    responses=SCORE_IMPORT_ERROR_RESPONSES,
)
async def upload_score_import_file(
    session_id: int,
    request: Request,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
    data_root: Path = Depends(get_data_root),
) -> dict[str, Any]:
    """Stream one .xlsx per request (same convention as scan/config uploads)."""
    _require_import_session(db, templates_dir, session_id)
    from backend.config_workspace.sources import decode_upload_filename

    filename = decode_upload_filename(request.headers.get("x-upload-filename"))
    if not str(filename).lower().endswith(".xlsx"):
        raise ApiError(
            422,
            "score_import_invalid_upload",
            "只支持 .xlsx 格式的智学网导出表。",
        )
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_IMPORT_UPLOAD_BYTES:
        raise ApiError(413, "score_import_upload_too_large", "导入文件过大。")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_IMPORT_UPLOAD_BYTES:
            raise ApiError(413, "score_import_upload_too_large", "导入文件过大。")
    if not body:
        raise ApiError(422, "score_import_invalid_upload", "上传内容为空。")
    upload_id = hashlib.sha256(bytes(body)).hexdigest()
    path = upload_path(data_root, int(session_id), upload_id)
    existing = None
    if path.exists():
        from backend.score_import.context import read_upload_meta

        existing = read_upload_meta(data_root, int(session_id), upload_id)
    if existing is not None:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(body))
    try:
        parsed = parse_upload(upload_id, str(filename), path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise ApiError(
            422,
            "score_import_invalid_upload",
            f"Excel 文件无法解析：{exc}",
        ) from exc
    meta = {
        "upload_id": upload_id,
        "filename": str(filename),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "sheets": sheet_inventory(parsed.sheets),
    }
    _atomic_write_json(
        upload_meta_path(data_root, int(session_id), upload_id),
        meta,
    )
    return meta


def _load_uploads(
    data_root: Path,
    session_id: int,
    upload_ids: list[str],
) -> list[Any]:
    uploads = []
    for upload_id in upload_ids:
        path = upload_path(data_root, int(session_id), upload_id)
        if not path.exists():
            raise ApiError(
                404,
                "score_import_upload_not_found",
                f"导入文件 {upload_id[:12]}… 不存在，请重新上传。",
                {"upload_id": upload_id},
            )
        meta = None
        from backend.score_import.context import read_upload_meta

        meta = read_upload_meta(data_root, int(session_id), upload_id)
        filename = str((meta or {}).get("filename") or f"{upload_id[:12]}.xlsx")
        try:
            uploads.append(parse_upload(upload_id, filename, path))
        except Exception as exc:
            raise ApiError(
                422,
                "score_import_invalid_upload",
                f"导入文件「{filename}」无法解析：{exc}",
            ) from exc
    return uploads


def _build_plan_or_raise(
    *,
    db: GradingRepositoryAccess,
    data_root: Path,
    session_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    if not params["upload_ids"]:
        raise ApiError(
            422,
            "score_import_no_uploads",
            "请先上传智学网成绩 Excel。",
        )
    uploads = _load_uploads(data_root, int(session_id), params["upload_ids"])
    try:
        return build_import_plan(
            session_id=int(session_id),
            db=db,
            data_root=data_root,
            uploads=uploads,
            raw_params=params,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            409,
            "score_import_config_not_ready",
            "评分依据尚未配置，请先完成「本场赋分」。",
        ) from exc


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


@router.post(
    "/sessions/{session_id}/score-import/preview",
    responses=SCORE_IMPORT_ERROR_RESPONSES,
)
def preview_score_import(
    session_id: int,
    request: ScoreImportPlanRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    templates_dir: Path = Depends(get_templates_dir),
    data_root: Path = Depends(get_data_root),
) -> dict[str, Any]:
    _require_import_session(db, templates_dir, session_id)
    plan = _build_plan_or_raise(
        db=db,
        data_root=data_root,
        session_id=session_id,
        params=request.model_dump(),
    )
    return _public_plan(plan)


@router.post(
    "/sessions/{session_id}/score-import/confirm",
    responses=SCORE_IMPORT_ERROR_RESPONSES,
)
def confirm_score_import(
    session_id: int,
    request: ScoreImportConfirmRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    templates_dir: Path = Depends(get_templates_dir),
    upload_config_dir: Path = Depends(get_upload_config_dir),
    data_root: Path = Depends(get_data_root),
) -> dict[str, Any]:
    _require_import_session(db, templates_dir, session_id)
    params = request.model_dump()
    plan = _build_plan_or_raise(
        db=db,
        data_root=data_root,
        session_id=session_id,
        params=params,
    )
    if str(plan["preview_revision"]) != str(request.preview_revision):
        raise ApiError(
            409,
            "score_import_preview_stale",
            "导入预览已过期（文件或评分结构有变化），请重新预览后再确认。",
        )
    try:
        result = confirm_import(
            db=db,
            job_store=manager.store,
            upload_config_dir=upload_config_dir,
            templates_dir=templates_dir,
            data_root=data_root,
            session_id=int(session_id),
            plan=plan,
            params=params,
            roster=db.students.list_students(),
            replace_existing=bool(request.replace_existing),
        )
    except ConfigSessionBusyError as exc:
        raise ApiError(
            409,
            "config_generation_in_progress",
            "这场考试还有进行中的配置或批改任务，结束后再导入。",
        ) from exc
    except SessionDeletionActiveWork as exc:
        raise ApiError(
            409,
            "score_import_active_work",
            "这场考试还有进行中的任务，结束后再导入。",
        ) from exc
    except ConfigRevisionConflict as exc:
        raise ApiError(
            409,
            "config_revision_conflict",
            "评分依据在导入期间被修改，请重新预览。",
        ) from exc
    except ConfigEditorValidationError as exc:
        raise ApiError(
            422,
            "invalid_config_editor",
            "合并小问后的评分结构未通过校验。",
            {"issues": [dict(issue) for issue in exc.issues]},
        ) from exc
    except ScoreImportWriteError as exc:
        raise ApiError(422, "score_import_rejected", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "score_import_rejected",
            f"导入写入校验未通过：{exc}",
        ) from exc
    return {"status": "imported", **result}
