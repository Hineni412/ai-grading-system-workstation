"""「错因整理 → 班级报告 → 个人报告」统一整理管线。

阅卷完成且没有待复核条目时自动运行；成绩中心「AI 整理」手动补做缺失、
过期与失败的内容。三个阶段顺序执行，已有内容不重复生成；未配置内容
生成模型时不产生任何模型调用。

- 手动（manual）：重试此前整理失败的题目，并把按步骤整理前的 v3 结果升级；
- 自动（auto）：失败题不重发、v3 兼容结果不升级，只补齐尚未整理的内容。
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.class_analysis import (
    CLASS_ANALYSIS_RENDITION_VERSION,
    NARRATIVE_CACHE_DIRNAME,
    ClassAnalysisStateStore,
    _class_narrative,
    _now_iso,
    run_cause_analysis,
    session_error_records,
    submit_class_analysis_generate,
)
from backend.jobs.manager import JobContext, JobManager
from backend.jobs.store import JobRecord
from backend.repositories.access import as_grading_repositories
from backend.repositories.grading_database import open_grading_repositories

LOGGER = logging.getLogger(__name__)

PIPELINE_KIND = "pipeline"
PERSONAL_ANALYSIS_REPORT_TYPE = "personal_analysis_html"


def class_cause_digest(
    error_records: dict[int, dict[str, Any]],
    student_ids,
) -> str:
    """某班学生物化错因记录的稳定摘要；该班无记录时为空串（兼容旧缓存键）。"""
    subset = {
        int(sid): error_records[int(sid)]
        for sid in student_ids
        if int(sid) in error_records
    }
    if not subset:
        return ""
    canonical = json.dumps(
        subset, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def class_report_entry_current(entry: Any, digest: str) -> bool:
    """单班叙述是否与当前错因口径一致（成绩版本/版式由调用方先行判定）。"""
    return (
        isinstance(entry, dict)
        and entry.get("status") == "ready"
        and isinstance(entry.get("narrative"), dict)
        and str(entry.get("cause_digest") or "") == digest
    )


def run_report_pipeline(
    context: JobContext,
    *,
    db_path: Path,
    reports_dir: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None,
    exporter_factory: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """顺序执行错因整理、班级叙述与个人叙述；每步只补缺失内容。"""
    from backend.report_exports import score_revision

    session_id = int(context.payload["session_id"])
    mode = "auto" if str(context.payload.get("mode") or "") == "auto" else "manual"
    store = ClassAnalysisStateStore(Path(reports_dir))
    db = open_grading_repositories(Path(db_path))

    client = llm_client_factory() if llm_client_factory is not None else None
    if client is None:
        # 未配置内容生成模型：不静默换模型，记 not_configured 供页面降级显示；
        # 只写状态字段，已有叙述与班级条目原样保留。
        store.save(
            session_id,
            status="not_configured",
            narrative_error="内容生成模型未配置",
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
            upgrade_pre_step=(mode == "manual"),
            progress_band=(0.02, 0.35),
            progress_stage="report_pipeline",
        )
    except Exception:
        context.raise_if_cancelled()
        # 整理整体异常不阻断班级与个人阶段，结果中如实记录。
        LOGGER.warning("report pipeline causes stage failed", exc_info=True)
        causes = {"kind": "causes", "status": "error"}

    context.raise_if_cancelled()
    context.report(0.4, "report_pipeline", "正在生成班级报告")
    revision = str(context.payload.get("score_revision") or "").strip() or score_revision(
        db, session_id, include_question_bank=False
    )
    class_summary = generate_session_class_reports(
        context,
        db=db,
        session_id=session_id,
        revision=revision,
        reports_dir=Path(reports_dir),
        data_root=data_root,
        client=client,
        progress_band=(0.4, 0.65),
        progress_stage="report_pipeline",
    )

    context.raise_if_cancelled()
    context.report(0.7, "report_pipeline", "正在生成个人报告")
    personal_summary = generate_pending_personal_reports(
        context,
        db=db,
        session_id=session_id,
        reports_dir=Path(reports_dir),
        data_root=data_root,
        client=client,
        exporter_factory=exporter_factory,
        progress_band=(0.7, 0.98),
        progress_stage="report_pipeline",
    )

    ready = (
        causes.get("status") == "ready"
        and class_summary.get("status") == "ready"
        and int(personal_summary.get("failed") or 0) == 0
    )
    context.report(0.99, "report_pipeline", "ready" if ready else "partial")
    return {
        "session_id": session_id,
        "kind": PIPELINE_KIND,
        "mode": mode,
        "causes": causes,
        "class_reports": {
            key: class_summary[key] for key in ("generated", "failed", "skipped")
        },
        "personal_reports": {
            key: personal_summary.get(key, 0) for key in ("generated", "failed", "skipped")
        },
        "status": "ready" if ready else "partial",
    }


def generate_session_class_reports(
    context: JobContext,
    *,
    db: Any,
    session_id: int,
    revision: str,
    reports_dir: Path,
    data_root: Path | None,
    client: Any,
    progress_band: tuple[float, float] = (0.0, 1.0),
    progress_stage: str = "report_pipeline",
) -> dict[str, Any]:
    """为叙述不当前的班级生成 AI 叙述；已当前的班级保留原条目。"""
    from backend.reporting.analysis_report_exporter import (
        AnalysisNarrativeCache,
        build_class_payload,
        build_report_prompt,
    )
    from backend.reporting.analysis_report_prompts import CLASS_SYSTEM_PROMPT
    from backend.session_analysis import (
        assemble_session_analysis,
        split_session_analysis_by_class,
    )

    store = ClassAnalysisStateStore(Path(reports_dir))
    context.raise_if_cancelled()
    data = assemble_session_analysis(db, int(session_id), data_root=data_root)
    # 已整理的错因记录随班级叙述入参，并计入班级摘要：错因变化 → 该班重新生成。
    error_records = session_error_records(
        db, int(session_id), Path(reports_dir), data_root=data_root
    )
    generated_at = _now_iso()
    state = store.load(int(session_id)) or {}
    revision_current = (
        str(state.get("score_revision") or "") == str(revision)
        and str(state.get("rendition_version") or "")
        == CLASS_ANALYSIS_RENDITION_VERSION
    )
    stored_reports = (
        state.get("class_reports") if isinstance(state.get("class_reports"), dict) else {}
    )
    groups = split_session_analysis_by_class(data)
    if not data.students:
        store.save(
            int(session_id),
            status="failed",
            narrative=None,
            narrative_error="该场次暂无可分析的成绩数据",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        )
        return {
            "status": "failed",
            "generated": 0,
            "failed": 0,
            "skipped": 0,
            "generated_at": generated_at,
        }

    class_reports = dict(stored_reports) if revision_current else {}
    pending: list[tuple[str, Any, str]] = []
    skipped = 0
    for name, group in groups.items():
        if not group.students:
            continue
        digest = class_cause_digest(
            error_records, [student.student_id for student in group.students]
        )
        if revision_current and class_report_entry_current(
            stored_reports.get(name), digest
        ):
            skipped += 1
            continue
        pending.append((name, group, digest))

    lo, hi = progress_band
    cache = AnalysisNarrativeCache(Path(reports_dir) / NARRATIVE_CACHE_DIRNAME)
    generated = 0
    failed = 0
    # 各班叙述的模型调用按设置页并发数并行；缓存与状态写回留在主线程按
    # 完成顺序处理（ClassAnalysisStateStore 的读改写不是线程安全的）。
    execution = getattr(
        getattr(client, "config_gateway", None), "execution_snapshot", None
    )
    parallel_limit = max(1, int(getattr(execution, "max_in_flight", 1)))
    if pending:

        def _call(item: tuple[str, Any, str]) -> tuple[str, str, Any]:
            name, group, digest = item
            narrative = _class_narrative(
                client=client,
                cache=cache,
                session_id=int(session_id),
                revision=str(revision),
                class_name=name,
                cause_digest=digest,
                prompt=build_report_prompt(
                    CLASS_SYSTEM_PROMPT, build_class_payload(group, error_records)
                ),
            )
            return name, digest, narrative

        done = 0
        with ThreadPoolExecutor(
            max_workers=min(len(pending), parallel_limit),
            thread_name_prefix="class-report",
        ) as executor:
            inflight = {executor.submit(_call, item): None for item in pending}
            while inflight:
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in finished:
                    inflight.pop(future)
                    name, digest, narrative = future.result()
                    class_reports[name] = {
                        "status": "ready" if narrative is not None else "failed",
                        "narrative": narrative,
                        "cause_digest": digest,
                    }
                    if narrative is not None:
                        generated += 1
                    else:
                        failed += 1
                    done += 1
                    context.report(
                        lo + (hi - lo) * done / max(1, len(pending)),
                        progress_stage,
                        f"正在生成班级报告 {name}",
                    )
                context.raise_if_cancelled()
    context.raise_if_cancelled()
    status = (
        "ready"
        if all(
            class_reports.get(name, {}).get("status") == "ready"
            for name, group in groups.items()
            if group.students
        )
        else "failed"
    )
    store.save(
        int(session_id),
        status=status,
        narrative=(
            next(iter(class_reports.values()))["narrative"]
            if len(class_reports) == 1
            else None
        ),
        narrative_error=None if status == "ready" else "AI 分析生成失败，可重新生成",
        score_revision=revision,
        generated_at=generated_at,
        small_sample=data.small_sample,
        class_reports=class_reports,
        rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
    )
    return {
        "status": status,
        "generated": generated,
        "failed": failed,
        "skipped": skipped,
        "generated_at": generated_at,
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
    progress_band: tuple[float, float] = (0.0, 1.0),
    progress_stage: str = "report_pipeline",
) -> dict[str, int]:
    """只为状态缺失或过期的学生生成叙述；已当前的报告保持不动。"""
    from backend.personal_reports import personal_report_states

    states = personal_report_states(db, int(session_id), Path(reports_dir))
    targets = sorted(
        int(entry["student_id"])
        for entry in states["students"]
        if entry["status"] in {"missing", "stale"}
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
            narrative_cache_dir=Path(reports_dir) / NARRATIVE_CACHE_DIRNAME,
            data_root=data_root,
            reports_dir=Path(reports_dir),
        )
        # 叙述与索引写入受控缓存目录供在线读取；临时 HTML 出参随目录清理。
        exporter.export_session(
            int(session_id),
            PERSONAL_ANALYSIS_REPORT_TYPE,
            student_ids=set(targets),
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
    upgrade_pre_step: bool = True,
) -> dict[str, Any]:
    """「AI 整理」状态：配置、复核待办与三阶段待补数量（默认按手动口径）。"""
    from backend.model_profiles.content_generation import (
        content_generation_public_info,
        resolve_content_generation_settings,
    )
    from backend.personal_reports import personal_report_states
    from backend.report_exports import score_revision
    from backend.reporting.analysis_report_exporter import (
        build_analysis_preflight,
        plan_cause_calls,
    )
    from backend.session_analysis import (
        assemble_session_analysis,
        split_session_analysis_by_class,
    )

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(Path(reports_dir))
    state = store.load(int(session_id)) or {}
    revision = score_revision(
        repositories, int(session_id), include_question_bank=False
    )
    configured = resolve_content_generation_settings() is not None
    service_name, model_name = content_generation_public_info()

    cause_plan = plan_cause_calls(
        repositories,
        int(session_id),
        reports_dir=Path(reports_dir),
        retry_failed=retry_failed,
        upgrade_pre_step=upgrade_pre_step,
    )
    causes_pending = int(cause_plan["pending_questions"]) > 0

    data = assemble_session_analysis(repositories, int(session_id), page_only=True)
    error_records = session_error_records(
        repositories, int(session_id), Path(reports_dir)
    )
    revision_current = (
        str(state.get("score_revision") or "") == revision
        and str(state.get("rendition_version") or "")
        == CLASS_ANALYSIS_RENDITION_VERSION
    )
    stored_reports = (
        state.get("class_reports") if isinstance(state.get("class_reports"), dict) else {}
    )
    class_total = 0
    class_pending = 0
    for name, group in split_session_analysis_by_class(data).items():
        if not group.students:
            continue
        class_total += 1
        digest = class_cause_digest(
            error_records, [student.student_id for student in group.students]
        )
        current = (
            revision_current
            and class_report_entry_current(stored_reports.get(name), digest)
        )
        # 错因有待整理时各班摘要都会变化，全部视为待生成。
        if causes_pending or not current:
            class_pending += 1

    personal_states = personal_report_states(
        repositories, int(session_id), Path(reports_dir)
    )["students"]
    personal_pending_ids = {
        int(entry["student_id"])
        for entry in personal_states
        if entry["status"] in {"missing", "stale"}
    }
    personal_total = sum(
        1 for entry in personal_states if entry["status"] != "unavailable"
    )
    personal_plan = build_analysis_preflight(
        repositories,
        int(session_id),
        PERSONAL_ANALYSIS_REPORT_TYPE,
        score_revision=revision,
        cache_dir=Path(reports_dir) / NARRATIVE_CACHE_DIRNAME,
        reports_dir=Path(reports_dir),
        student_ids=personal_pending_ids,
        # 错因预估已在上面按手动口径算过，这里不重复计算。
        include_causes=False,
    ) if personal_pending_ids else {
        "call_count": 0,
        "cache_hits": 0,
        "estimated_total_tokens": 0,
    }
    complete = (
        int(cause_plan["pending_questions"]) == 0
        and class_pending == 0
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
        "class_reports": {"pending": class_pending, "total": class_total},
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
    from backend.report_exports import score_revision

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
    # 自动口径（不重试失败、不升级旧版）下无可补内容时不再排队空跑。
    status = build_report_pipeline_status(
        db=repositories,
        session_id=int(session_id),
        reports_dir=Path(reports_dir),
        review_pending=0,
        active_job_id=None,
        retry_failed=False,
        upgrade_pre_step=False,
    )
    if status["complete"]:
        return None
    if not status["configured"]:
        # 只写状态字段，已有叙述与班级条目原样保留。
        store.save(
            int(session_id),
            status="not_configured",
            narrative_error="内容生成模型未配置",
            generated_at=_now_iso(),
        )
        return None
    return submit_class_analysis_generate(
        manager=manager,
        session_id=int(session_id),
        revision=score_revision(
            repositories, int(session_id), include_question_bank=False
        ),
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
