from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_grading_db,
    get_job_file_service,
    get_job_manager,
    get_reports_dir,
)
from backend.api.routers.jobs import _job_response, _require_job
from backend.api.routers.sessions import _require_session
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.reports import (
    AnalysisPreflightResponse,
    ClassAnalysisResponse,
    ClassAnalysisSettingsRequest,
    ClassAnalysisSettingsResponse,
    ReportExportContextResponse,
    ReportExportHistoryItem,
    ReportExportRequest,
    ReportFileDeleteResponse,
)
from backend.class_analysis import (
    CLASS_ANALYSIS_JOB_TYPE,
    ClassAnalysisStateStore,
    submit_class_analysis_generate,
)
from backend.file_access import (
    ControlledFileExpired,
    ControlledFileForbidden,
    ControlledFileTypeError,
)
from backend.files.service import JobFileNotFound, JobFileService, JobFileUnavailable
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from backend.report_exports import (
    ANALYSIS_REPORT_TYPES,
    score_revision,
    submit_report_export,
)
from backend.repositories.access import GradingRepositoryAccess


router = APIRouter(prefix="/api", tags=["reports"])

_ANALYSIS_NARRATIVE_CACHE_DIRNAME = ".analysis_narrative_cache"


@router.post(
    "/sessions/{session_id}/reports/export",
    response_model=JobResponse,
    status_code=202,
)
def export_session_report(
    session_id: int,
    request: ReportExportRequest | None = None,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> JobResponse:
    _require_session(db, session_id)
    has_results = bool(db.get_session_results(int(session_id)))
    has_teacher_locks = bool(
        db.review_repository.list_teacher_score_locks(int(session_id))
    )
    if request is not None and not has_results and not has_teacher_locks:
        raise ApiError(
            409,
            "report_results_missing",
            "The exam has no grading results to export",
        )
    if request is not None and request.report_type in ANALYSIS_REPORT_TYPES:
        # 兜底校验：未配置内容生成模型时不得静默改用阅卷模型或空跑计费。
        from analysis_report_exporter import resolve_content_generation_settings

        if resolve_content_generation_settings() is None:
            raise ApiError(
                422,
                "content_generation_model_not_configured",
                "Content generation model is not configured",
                {"report_type": request.report_type},
            )
    try:
        if request is None:
            job = manager.submit("report_export", {"session_id": int(session_id)})
        else:
            job = submit_report_export(
                manager=manager,
                file_service=file_service,
                session_id=session_id,
                report_type=request.report_type,
                revision=score_revision(db, session_id),
                force_regenerate=request.force_regenerate,
                score_excel_options=(
                    request.excel_options.model_dump()
                    if request.excel_options is not None
                    else None
                ),
            )
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "report_export"},
        ) from exc
    return _job_response(job)


@router.get(
    "/sessions/{session_id}/reports/analysis-preflight",
    response_model=AnalysisPreflightResponse,
)
def get_analysis_report_preflight(
    session_id: int,
    report_type: Literal["personal_analysis_html"] = Query(...),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    reports_dir: Path = Depends(get_reports_dir),
) -> AnalysisPreflightResponse:
    """生成前预估：目标服务/模型、实际调用次数与 token 粗估（费用取决于服务商定价）。"""
    from analysis_report_exporter import build_analysis_preflight

    _require_session(db, session_id)
    payload = build_analysis_preflight(
        db,
        session_id,
        report_type,
        score_revision=score_revision(db, session_id),
        cache_dir=Path(reports_dir) / _ANALYSIS_NARRATIVE_CACHE_DIRNAME,
    )
    return AnalysisPreflightResponse(**payload)


@router.get(
    "/sessions/{session_id}/reports/context",
    response_model=ReportExportContextResponse,
)
def get_session_report_context(
    session_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> ReportExportContextResponse:
    _require_session(db, session_id)
    revision = score_revision(db, session_id)
    has_results = bool(db.get_session_results(int(session_id))) or bool(
        db.review_repository.list_teacher_score_locks(int(session_id))
    )
    jobs, total = manager.list(
        session_id=int(session_id),
        job_types=("report_export",),
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return ReportExportContextResponse(
        score_revision=revision,
        has_results=has_results,
        jobs=[
            ReportExportHistoryItem(
                **_job_response(job).model_dump(),
                is_current_revision=(
                    str(job.payload.get("score_revision") or "") == revision
                ),
                file_status=_file_status(job, file_service),
            )
            for job in jobs
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, ceil(total / page_size)),
    )


def _file_status(job, file_service: JobFileService) -> str:
    if job.status in {"queued", "running", "paused"}:
        return "pending"
    if job.status == "failed":
        return "failed"
    if job.status == "cancelled":
        return "cancelled"
    try:
        file_service.resolve(job)
    except ControlledFileExpired:
        return "expired"
    except (
        ControlledFileForbidden,
        ControlledFileTypeError,
        JobFileNotFound,
        JobFileUnavailable,
    ):
        return "unavailable"
    return "available"


# ---------------------------------------------------------------------------
# 班级分析内嵌页（教师版）：状态读取 / 自动生成开关 / 手动重新生成
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/class-analysis",
    response_model=ClassAnalysisResponse,
)
def get_class_analysis(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    reports_dir: Path = Depends(get_reports_dir),
) -> ClassAnalysisResponse:
    from analysis_report_exporter import (
        assemble_session_analysis,
        build_class_page_data,
        class_narrative_with_student_names,
    )

    _require_session(db, session_id)
    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(session_id)
    # 页面数据按当前成绩实时装配；无成绩数据时整体降级为 no_data。
    data = assemble_session_analysis(db, int(session_id))
    has_data = bool(data.students)
    active_jobs, _total = manager.list(
        session_id=int(session_id),
        job_types=(CLASS_ANALYSIS_JOB_TYPE,),
        statuses=("queued", "running"),
        limit=1,
    )
    active_job_id = active_jobs[0].id if active_jobs else None
    current_revision = score_revision(db, session_id)
    stored_revision = str(state.get("score_revision") or "") if state else ""
    # 存储的成绩版本与当前不一致 → 提示「成绩已更新，可重新生成」。
    stale = bool(stored_revision) and stored_revision != current_revision
    narrative = None
    narrative_failed = False
    if state is not None:
        narrative_failed = state.get("status") in {"failed", "not_configured"}
        if state.get("status") == "ready" and isinstance(state.get("narrative"), dict):
            # 叙述中的 S1/S2… 代号在服务端映射回真实姓名（教师本人页面，不脱敏）。
            narrative = class_narrative_with_student_names(
                state["narrative"],
                data.students,
            )
    return ClassAnalysisResponse(
        status=(
            "generating"
            if active_job_id is not None
            else ("ready" if has_data else "no_data")
        ),
        auto_generate=bool(state.get("auto_generate", True)) if state else True,
        small_sample=data.small_sample if has_data else False,
        data=build_class_page_data(data) if has_data else None,
        narrative=narrative,
        narrative_failed=narrative_failed,
        generated_at=(
            (str(state.get("generated_at") or "") or None) if state else None
        ),
        stale=stale,
        active_job_id=active_job_id,
    )


@router.put(
    "/sessions/{session_id}/class-analysis/settings",
    response_model=ClassAnalysisSettingsResponse,
)
def put_class_analysis_settings(
    session_id: int,
    request: ClassAnalysisSettingsRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    reports_dir: Path = Depends(get_reports_dir),
) -> ClassAnalysisSettingsResponse:
    _require_session(db, session_id)
    store = ClassAnalysisStateStore(reports_dir)
    state = store.set_auto_generate(session_id, request.auto_generate)
    return ClassAnalysisSettingsResponse(auto_generate=bool(state["auto_generate"]))


@router.post(
    "/sessions/{session_id}/class-analysis/regenerate",
    response_model=JobResponse,
    status_code=202,
)
def regenerate_class_analysis(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    reports_dir: Path = Depends(get_reports_dir),
) -> JobResponse:
    _require_session(db, session_id)
    # 兜底校验：未配置内容生成模型时不得静默改用阅卷模型或空跑计费。
    from analysis_report_exporter import resolve_content_generation_settings

    if resolve_content_generation_settings() is None:
        raise ApiError(
            422,
            "content_generation_model_not_configured",
            "Content generation model is not configured",
        )
    job = submit_class_analysis_generate(
        manager=manager,
        session_id=int(session_id),
        revision=score_revision(db, session_id),
        force=True,
    )
    return _job_response(job)


@router.delete(
    "/sessions/{session_id}/reports/{job_id}/file",
    response_model=ReportFileDeleteResponse,
)
def delete_retained_report_file(
    session_id: int,
    job_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    file_service: JobFileService = Depends(get_job_file_service),
) -> ReportFileDeleteResponse:
    """删除一份留存在本机的个人学情报告文件；幂等，重复删除返回 deleted=false。"""
    _require_session(db, session_id)
    job = _require_job(manager, job_id)
    if int(job.payload.get("session_id") or 0) != int(session_id):
        raise ApiError(
            404,
            "job_not_found",
            "Job not found",
            {"job_id": int(job_id)},
        )
    if not file_service.is_retained_report(job):
        raise ApiError(
            422,
            "report_file_not_retained",
            "Only retained personal analysis reports can be deleted",
            {"job_id": int(job_id)},
        )
    try:
        freed = file_service.delete_retained_file(job, manager.store)
    except JobFileUnavailable as exc:
        raise ApiError(
            409,
            "job_file_unavailable",
            "Job file is not available",
            {"job_id": int(job_id)},
        ) from exc
    except ControlledFileForbidden as exc:
        raise ApiError(
            403,
            "job_file_forbidden",
            "Job file is outside the allowed storage boundary",
            {"job_id": int(job_id)},
        ) from exc
    except ControlledFileTypeError as exc:
        raise ApiError(
            415,
            "job_file_type_not_supported",
            "Job file type is not supported",
            {"job_id": int(job_id)},
        ) from exc
    return ReportFileDeleteResponse(
        job_id=int(job_id),
        deleted=freed is not None,
        freed_bytes=freed or 0,
    )
