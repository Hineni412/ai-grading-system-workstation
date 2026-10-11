"""错因整理与个人报告的统一整理流程。

默认补缺失。手动整理可重试失败项；明确勾选更新才重新生成已有内容。
查看与更新提醒不触发模型。成功替换一份主结果，失败保留已有内容。
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.class_analysis import (
    ClassAnalysisStateStore,
    _now_iso,
    run_cause_analysis,
    submit_class_analysis_generate,
)
from backend.jobs.manager import JobContext, JobManager
from backend.jobs.store import JobRecord
from backend.repositories.access import as_grading_repositories
from backend.repositories.grading_database import open_grading_repositories

LOGGER = logging.getLogger(__name__)

PIPELINE_KIND = "pipeline"
PERSONAL_ANALYSIS_REPORT_TYPE = "personal_analysis_html"


def run_report_pipeline(
    context: JobContext,
    *,
    db_path: Path,
    reports_dir: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None,
    exporter_factory: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """顺序执行错因整理与个人报告生成；每步只补缺失内容。"""

    session_id = int(context.payload["session_id"])
    mode = "auto" if str(context.payload.get("mode") or "") == "auto" else "manual"
    store = ClassAnalysisStateStore(Path(reports_dir))
    if (state := store.load(session_id)) and state.get("read_error"):
        raise ValueError(state["read_error"])
    db = open_grading_repositories(Path(db_path))

    client = llm_client_factory() if llm_client_factory is not None else None
    if client is None:
        # 未配置内容生成模型：不静默换模型，记 not_configured 供页面降级显示；
        # 只写状态字段，已有内容原样保留。
        store.save(
            session_id,
            status="not_configured",
            generated_at=_now_iso(),
        )
        return {
            "session_id": session_id,
            "kind": PIPELINE_KIND,
            "mode": mode,
            "status": "not_configured",
        }

    context.raise_if_cancelled()
    context.report(0.02, "report_pipeline", "正在整理错因")
    try:
        causes = run_cause_analysis(
            context,
            db=db,
            data_root=data_root,
            store=store,
            llm_client_factory=lambda: client,
            retry_failed=(mode == "manual"),
            regenerate_existing=context.payload.get("regenerate_existing") is True and mode == "manual",
            progress_band=(0.02, 0.45),
            progress_stage="report_pipeline",
        )
    except Exception:
        context.raise_if_cancelled()
        # 整理整体异常不阻断个人报告阶段，结果中如实记录。
        LOGGER.warning("report pipeline causes stage failed", exc_info=True)
        causes = {"kind": "causes", "status": "error"}

    context.raise_if_cancelled()
    context.report(0.5, "report_pipeline", "正在生成个人报告")
    personal_summary = generate_pending_personal_reports(
        context,
        db=db,
        session_id=session_id,
        reports_dir=Path(reports_dir),
        data_root=data_root,
        client=client,
        exporter_factory=exporter_factory,
        regenerate_existing=context.payload.get("regenerate_existing") is True and mode == "manual",
        progress_band=(0.5, 0.98),
        progress_stage="report_pipeline",
    )

    ready = (
        causes.get("status") == "ready"
        and int(personal_summary.get("failed") or 0) == 0
    )
    context.report(0.99, "report_pipeline", "ready" if ready else "partial")
    return {
        "session_id": session_id,
        "kind": PIPELINE_KIND,
        "mode": mode,
        "causes": causes,
        "personal_reports": {
            key: personal_summary.get(key, 0) for key in ("generated", "failed", "skipped")
        },
        "status": "ready" if ready else "partial",
    }


def generate_pending_personal_reports(
    context: JobContext,
    *,
    db: Any,
    session_id: int,
    reports_dir: Path,
    data_root: Path | None,
    client: Any,
    exporter_factory: Callable[..., Any] | None,
    regenerate_existing: bool = False,
    progress_band: tuple[float, float] = (0.0, 1.0),
    progress_stage: str = "report_pipeline",
) -> dict[str, int]:
    """默认补缺失，明确选择更新时生成新正文，失败保持上次成功结果。"""
    from backend.personal_reports import personal_report_states

    states = personal_report_states(db, int(session_id), Path(reports_dir))
    targets = sorted(
        int(entry["student_id"])
        for entry in states["students"]
        if (entry["status"] == "missing" and not entry.get("read_error")) or (regenerate_existing and entry["status"] != "unavailable")
    )
    if not targets:
        return {"generated": 0, "failed": 0, "skipped": 0}
    if exporter_factory is None:
        from backend.reporting.analysis_report_exporter import AnalysisReportGenerator

        exporter_factory = AnalysisReportGenerator
    lo, hi = progress_band
    total = len(targets)
    with tempfile.TemporaryDirectory(
        dir=Path(reports_dir), prefix=f".pipeline-{context.job_id}-"
    ) as staging:
        exporter = exporter_factory(
            db,
            Path(staging),
            llm_client_factory=lambda: client,
            data_root=data_root,
            reports_dir=Path(reports_dir),
        )
        # 叙述与索引写入受控缓存目录供在线读取；临时 HTML 出参随目录清理。
        exporter.export_session(
            int(session_id),
            PERSONAL_ANALYSIS_REPORT_TYPE,
            student_ids=set(targets),
            regenerate_existing=regenerate_existing,
            html_only=True,
            progress_callback=lambda done: context.report(
                lo + (hi - lo) * min(total, int(done)) / total,
                progress_stage,
                f"正在生成个人报告 {min(total, int(done))}/{total}",
            ),
            cancel_check=context.raise_if_cancelled,
        )
    return {
        key: int(exporter.last_personal_summary.get(key, 0))
        for key in ("generated", "failed", "skipped")
    }


def pending_review_count(
    db: Any,
    session_id: int,
    session: dict[str, Any],
    workspace: Any,
    *,
    review_service: Any | None = None,
) -> int:
    """待复核条目数：未评分 + 待复核 + 处理失败，与复核页 teacher_pending 同源。"""
    from backend.review.manual_context import current_manual_context
    from backend.review.service import ReviewApplicationService

    service = (
        review_service if review_service is not None
        else ReviewApplicationService(as_grading_repositories(db))
    )
    manual_context = current_manual_context(
        int(session_id), workspace, read_only=True
    )
    return sum(
        int(question.needs_review_count)
        + int(question.ungraded_count)
        + int(question.failed_count)
        for question in service.list_questions(
            int(session_id), session, manual_context=manual_context
        )
    )


def build_report_pipeline_status(
    *,
    db: Any,
    session_id: int,
    reports_dir: Path,
    review_pending: int,
    active_job_id: int | None,
    retry_failed: bool = True,
    regenerate_existing: bool = False,
) -> dict[str, Any]:
    """「AI 整理」状态：配置、复核待办与两阶段待补数量（默认按手动口径）。

    待补 = stale + missing；old_prompt 的结果仍可读，不计入待补、不自动重算。
    """
    from backend.model_profiles.content_generation import (
        content_generation_public_info,
        resolve_content_generation_settings,
    )
    from backend.personal_reports import personal_report_states
    from backend.reporting.analysis_report_exporter import (
        build_analysis_preflight,
        plan_cause_calls,
    )

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(Path(reports_dir))
    state = store.load(int(session_id)) or {}
    configured = resolve_content_generation_settings() is not None
    service_name, model_name = content_generation_public_info()

    cause_plan = plan_cause_calls(
        repositories,
        int(session_id),
        reports_dir=Path(reports_dir),
        retry_failed=retry_failed,
        regenerate_existing=regenerate_existing,
    )

    personal_states = personal_report_states(
        repositories, int(session_id), Path(reports_dir)
    )["students"]
    personal_pending_ids = {
        int(entry["student_id"])
        for entry in personal_states
        if (entry["status"] == "missing" and not entry.get("read_error")) or (regenerate_existing and entry["status"] != "unavailable")
    }
    personal_total = sum(
        1 for entry in personal_states if entry["status"] != "unavailable"
    )
    personal_plan = build_analysis_preflight(
        repositories,
        int(session_id),
        PERSONAL_ANALYSIS_REPORT_TYPE,
        reports_dir=Path(reports_dir),
        student_ids=personal_pending_ids,
        # 错因预估已在上面按手动口径算过，这里不重复计算。
        include_causes=False,
        regenerate_existing=regenerate_existing,
    ) if personal_pending_ids else {
        "call_count": 0,
        "cache_hits": 0,
        "estimated_total_tokens": 0,
    }
    complete = (
        int(cause_plan["pending_questions"]) == 0
        and not personal_pending_ids
    )
    return {
        "auto_generate": bool(state.get("auto_generate", True)),
        "configured": configured,
        "service_name": service_name if configured else None,
        "model_name": model_name if configured else None,
        "active_job_id": active_job_id,
        "review_pending": int(review_pending),
        "causes": {
            "pending_questions": int(cause_plan["pending_questions"]),
            "total_questions": int(cause_plan["total_questions"]),
            "call_count": int(cause_plan["call_count"]),
            "estimated_tokens": int(cause_plan["estimated_tokens"]),
        },
        "personal_reports": {
            "pending": len(personal_pending_ids),
            "total": personal_total,
            "call_count": int(personal_plan["call_count"]),
            "cache_hits": int(personal_plan["cache_hits"]),
            "estimated_tokens": int(personal_plan["estimated_total_tokens"]),
        },
        "complete": complete,
    }


def maybe_auto_generate_report_pipeline(
    *,
    manager: JobManager,
    db: Any,
    session_id: int,
    reports_dir: Path,
    workspace: Any,
) -> JobRecord | None:
    """阅卷+复核完成后的自动触发；任何校验失败都不产生模型调用。

    开关关、仍有未批完答卷或待复核条目时不提交；判定待复核来源失败时
    同样不提交（不冒进产生费用）。全部内容已当前、或已有进行中 job 时
    幂等跳过；未配置模型时记 not_configured 状态供页面降级显示。
    """
    from backend.model_profiles.content_generation import (
        resolve_content_generation_settings,
    )

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(Path(reports_dir))
    existing = store.load(int(session_id))
    if existing is not None and not bool(existing.get("auto_generate", True)):
        return None
    if repositories.results.list_incomplete_results(int(session_id)):
        return None
    session = repositories.sessions.get_grading_session(int(session_id))
    if session is None:
        return None
    try:
        if pending_review_count(
            repositories, int(session_id), session, workspace
        ) > 0:
            return None
    except Exception:
        LOGGER.warning(
            "report pipeline review check failed for session %s",
            session_id,
            exc_info=True,
        )
        return None
    # 自动口径（不重试失败）下无可补内容时不再排队空跑。
    status = build_report_pipeline_status(
        db=repositories,
        session_id=int(session_id),
        reports_dir=Path(reports_dir),
        review_pending=0,
        active_job_id=None,
        retry_failed=False,
    )
    if status["complete"]:
        return None
    if not status["configured"]:
        # 只写状态字段，已有内容原样保留。
        store.save(
            int(session_id),
            status="not_configured",
            generated_at=_now_iso(),
        )
        return None
    return submit_class_analysis_generate(
        manager=manager,
        session_id=int(session_id),
        mode="auto",
    )


def build_report_pipeline_auto_trigger(
    *,
    manager: JobManager,
    db_path: Path,
    reports_dir: Path,
    exams_dir: Path,
    templates_dir: Path,
    data_root: Path | None,
) -> Callable[[int], None]:
    """供 grading_run handler 在批改完成后调用的闭包；任何失败不外抛。

    job 环境没有 API 依赖注入，按只读口径自建最小 workspace 读取复核待办。
    """
    from backend.scan_grading.workspace import ScanGradingWorkspace

    workspace = ScanGradingWorkspace(
        exams_root=Path(exams_dir),
        templates_root=Path(templates_dir),
        grading_db_path=Path(db_path),
        data_root=Path(data_root) if data_root is not None else None,
    )

    def trigger(session_id: int) -> None:
        maybe_auto_generate_report_pipeline(
            manager=manager,
            db=open_grading_repositories(Path(db_path)),
            session_id=int(session_id),
            reports_dir=Path(reports_dir),
            workspace=workspace,
        )

    return trigger
