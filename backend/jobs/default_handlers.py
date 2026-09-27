from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from analysis_report_exporter import (
    ANALYSIS_REPORT_TYPES,
    AnalysisReportGenerator,
    resolve_content_generation_settings,
)
from api_profiles import get_api_profile_store, resolve_profile_for_task
from backend.llm.policy import policy_overrides_from_profile
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.compat import open_grading_repositories
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from original_paper_exporter import OriginalPaperExporter
from report import ReportGenerator
from question_bank.document_pipeline import QuestionDocumentPipeline
from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.taxonomy.governance import get_taxonomy_governance

from .manager import JobContext, JobManager
from .ai_assembly import run_ai_assembly_spec_job
from .answer_draft import run_answer_draft_job
from .config_generation import run_config_generation_job
from .criterion_backfill import run_criterion_backfill_job
from .knowledge_link_job import run_knowledge_link_job, build_knowledge_link_gateway
from .assembly_export import run_assembly_export_job
from .grading_run import run_grading_job
from .question_import import run_question_import_job
from .question_bank_sync import run_session_question_bank_sync_job
from .scan_analysis import run_scan_analysis
from .tagging_sync import run_tagging_sync_job
from .taxonomy_suggestions import run_taxonomy_suggestion_job


class ReportGeneratorFactory(Protocol):
    def __call__(
        self,
        db: GradingRepositoryAccess,
        reports_dir: Path,
    ) -> ReportGenerator:
        ...


class OriginalPaperExporterFactory(Protocol):
    def __call__(
        self,
        db: GradingRepositoryAccess,
        output_dir: Path,
    ) -> OriginalPaperExporter:
        ...


class AnalysisReportExporterFactory(Protocol):
    def __call__(
        self,
        db: GradingRepositoryAccess,
        output_dir: Path,
        *,
        llm_client_factory: Callable[[], Any] | None = None,
        narrative_cache_dir: Path | None = None,
        data_root: Path | None = None,
        reports_dir: Path | None = None,
    ) -> AnalysisReportGenerator:
        ...


def _content_generation_llm_client() -> LLMClient | None:
    """设置页「内容生成」任务绑定的模型；未配置时返回 None（报告降级，不换模型）。"""
    settings = resolve_content_generation_settings()
    if settings is None:
        return None
    return LLMClient(settings)


def register_default_job_handlers(
    manager: JobManager,
    *,
    db_path: Path,
    reports_dir: Path,
    exams_dir: Path | None = None,
    templates_dir: Path | None = None,
    data_root: Path | None = None,
    question_bank_db_path: Path | None = None,
    upload_config_dir: Path | None = None,
    report_generator_factory: ReportGeneratorFactory = ReportGenerator,
    original_paper_exporter_factory: OriginalPaperExporterFactory = OriginalPaperExporter,
    analysis_report_exporter_factory: AnalysisReportExporterFactory = AnalysisReportGenerator,
    analysis_llm_client_factory: Callable[[], Any] | None = None,
    scan_runner: Callable[..., dict[str, object]] = run_scan_analysis,
    grading_runner: Callable[..., dict[str, object]] = run_grading_job,
    config_generation_runner: Callable[..., dict[str, object]] = run_config_generation_job,
    question_import_runner: Callable[..., dict[str, object]] = run_question_import_job,
    question_bank_sync_runner: Callable[
        ..., dict[str, object]
    ] = run_session_question_bank_sync_job,
    tagging_sync_runner: Callable[..., dict[str, object]] = run_tagging_sync_job,
    answer_draft_runner: Callable[..., dict[str, object]] = run_answer_draft_job,
    taxonomy_suggestion_runner: Callable[
        ..., dict[str, object]
    ] = run_taxonomy_suggestion_job,
    assembly_export_runner: Callable[..., dict[str, object]] = run_assembly_export_job,
    ai_assembly_spec_runner: Callable[
        ..., dict[str, object]
    ] = run_ai_assembly_spec_job,
    criterion_backfill_runner: Callable[
        ..., dict[str, object]
    ] = run_criterion_backfill_job,
    knowledge_link_runner: Callable[
        ..., dict[str, object]
    ] = run_knowledge_link_job,
    knowledge_link_gateway_factory: Callable[[], Any] | None = None,
    taxonomy_governance: Any | None = None,
    tagging_ai_service_factory: Callable[[], Any] = AITaggingService,
    llm_client_factory: Callable[[], Any] | None = None,
) -> None:
    base_data_root = Path(data_root) if data_root is not None else _infer_data_root(Path(db_path))
    scan_llm_client_factory = llm_client_factory or _active_llm_client
    resolved_question_bank_db = (
        Path(question_bank_db_path)
        if question_bank_db_path is not None
        else base_data_root / "databases" / "question_bank.db"
    )
    resolved_upload_config_dir = (
        Path(upload_config_dir)
        if upload_config_dir is not None
        else base_data_root / "config" / "uploaded"
    )
    resolved_taxonomy_governance = (
        taxonomy_governance
        if taxonomy_governance is not None
        else get_taxonomy_governance()
    )
    resolved_tagging_factory = (
        (
            lambda: AITaggingService(
                taxonomy_governance=resolved_taxonomy_governance
            )
        )
        if tagging_ai_service_factory is AITaggingService
        else tagging_ai_service_factory
    )
    resolved_link_gateway_factory = knowledge_link_gateway_factory or (
        lambda: build_knowledge_link_gateway(resolved_tagging_factory())
    )
    manager.register(
        "report_export",
        _build_report_export_handler(
            db_path=Path(db_path),
            reports_dir=Path(reports_dir),
            report_generator_factory=report_generator_factory,
            original_paper_exporter_factory=original_paper_exporter_factory,
            analysis_report_exporter_factory=analysis_report_exporter_factory,
            analysis_llm_client_factory=(
                analysis_llm_client_factory or _content_generation_llm_client
            ),
            data_root=base_data_root,
        ),
    )
    manager.register(
        "class_analysis_generate",
        _build_class_analysis_generate_handler(
            db_path=Path(db_path),
            reports_dir=Path(reports_dir),
            data_root=base_data_root,
            llm_client_factory=(
                analysis_llm_client_factory or _content_generation_llm_client
            ),
        ),
    )
    manager.register(
        "scan_analysis",
        _build_scan_analysis_handler(
            db_path=Path(db_path),
            exams_dir=Path(exams_dir) if exams_dir is not None else base_data_root / "exams",
            templates_dir=Path(templates_dir) if templates_dir is not None else base_data_root / "templates",
            data_root=base_data_root,
            scan_runner=scan_runner,
            llm_client_factory=scan_llm_client_factory,
        ),
    )
    manager.register(
        "grading_run",
        _build_grading_run_handler(
            db_path=Path(db_path),
            exams_dir=Path(exams_dir) if exams_dir is not None else base_data_root / "exams",
            templates_dir=Path(templates_dir) if templates_dir is not None else base_data_root / "templates",
            data_root=base_data_root,
            question_bank_db_path=resolved_question_bank_db,
            grading_runner=grading_runner,
            llm_client_factory=scan_llm_client_factory,
            class_analysis_completed_trigger=_build_class_analysis_trigger(
                manager=manager,
                db_path=Path(db_path),
                reports_dir=Path(reports_dir),
            ),
        ),
    )
    manager.register(
        "config_generation",
        _build_config_generation_handler(
            db_path=Path(db_path),
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            mapping_output_dir=(
                Path(templates_dir)
                if templates_dir is not None
                else base_data_root / "templates"
            ),
            upload_config_dir=resolved_upload_config_dir,
            config_generation_runner=config_generation_runner,
            llm_client_factory=scan_llm_client_factory,
            tagging_ai_service_factory=resolved_tagging_factory,
            taxonomy_governance=resolved_taxonomy_governance,
        ),
    )
    manager.register(
        "question_import",
        _build_question_import_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            question_import_runner=question_import_runner,
        ),
    )
    manager.register(
        "tagging_sync",
        _build_tagging_sync_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            tagging_sync_runner=tagging_sync_runner,
            ai_service_factory=resolved_tagging_factory,
            taxonomy_governance=resolved_taxonomy_governance,
        ),
    )
    manager.register(
        "criterion_backfill",
        _build_criterion_backfill_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            criterion_backfill_runner=criterion_backfill_runner,
            ai_service_factory=resolved_tagging_factory,
            link_job_submitter=(
                (
                    lambda payload: manager.submit(
                        "knowledge_link", payload
                    )
                )
                if resolved_link_gateway_factory is not None
                else None
            ),
        ),
    )
    manager.register(
        "knowledge_link",
        _build_knowledge_link_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            knowledge_link_runner=knowledge_link_runner,
            gateway_factory=resolved_link_gateway_factory,
            taxonomy_governance=resolved_taxonomy_governance,
        ),
    )
    manager.register(
        "taxonomy_suggestion",
        _build_taxonomy_suggestion_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            taxonomy_suggestion_runner=taxonomy_suggestion_runner,
            ai_service_factory=resolved_tagging_factory,
            taxonomy_governance=resolved_taxonomy_governance,
        ),
    )
    manager.register(
        "question_bank_sync",
        _build_question_bank_sync_handler(
            db_path=Path(db_path),
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            question_bank_sync_runner=question_bank_sync_runner,
            question_import_runner=question_import_runner,
            tagging_sync_runner=tagging_sync_runner,
            ai_service_factory=resolved_tagging_factory,
            taxonomy_governance=resolved_taxonomy_governance,
            analysis_artifact_root=resolved_upload_config_dir,
        ),
    )
    manager.register(
        "assembly_export",
        _build_assembly_export_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            assembly_export_runner=assembly_export_runner,
        ),
    )
    manager.register(
        "ai_assembly_spec",
        _build_ai_assembly_spec_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            ai_assembly_spec_runner=ai_assembly_spec_runner,
            llm_client_factory=(
                analysis_llm_client_factory or _content_generation_llm_client
            ),
        ),
    )
    manager.register(
        "answer_draft",
        _build_answer_draft_handler(
            question_bank_db_path=resolved_question_bank_db,
            data_root=base_data_root,
            answer_draft_runner=answer_draft_runner,
            llm_client_factory=(
                analysis_llm_client_factory or _content_generation_llm_client
            ),
        ),
    )


def _build_answer_draft_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    answer_draft_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any] | None,
):
    def handler(context: JobContext) -> dict[str, object]:
        return answer_draft_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            llm_client_factory=llm_client_factory,
        )

    return handler


def _build_ai_assembly_spec_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    ai_assembly_spec_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any],
):
    def handler(context: JobContext) -> dict[str, object]:
        return ai_assembly_spec_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            llm_client_factory=llm_client_factory,
        )

    return handler


def _build_assembly_export_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    assembly_export_runner: Callable[..., dict[str, object]],
):
    def handler(context: JobContext) -> dict[str, object]:
        return assembly_export_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
        )

    return handler


def _build_question_import_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    question_import_runner: Callable[..., dict[str, object]],
):
    def handler(context: JobContext) -> dict[str, object]:
        return question_import_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            write_service=QuestionBankWriteService(
                question_bank_db_path,
                data_root=data_root,
            ),
            document_pipeline=QuestionDocumentPipeline(
                workspace_root=Path(data_root) / "question_bank" / "document_pipeline",
            ),
        )

    return handler


def _build_tagging_sync_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    tagging_sync_runner: Callable[..., dict[str, object]],
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
):
    def handler(context: JobContext) -> dict[str, object]:
        return tagging_sync_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            ai_service_factory=ai_service_factory,
            taxonomy_governance=taxonomy_governance,
            data_root=data_root,
        )

    return handler


def _build_criterion_backfill_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    criterion_backfill_runner: Callable[..., dict[str, object]],
    ai_service_factory: Callable[[], Any],
    link_job_submitter: Callable[[dict[str, Any]], Any] | None = None,
):
    def handler(context: JobContext) -> dict[str, object]:
        return criterion_backfill_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            ai_service_factory=ai_service_factory,
            link_job_submitter=link_job_submitter,
        )

    return handler


def _build_knowledge_link_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    knowledge_link_runner: Callable[..., dict[str, object]],
    gateway_factory: Callable[[], Any] | None,
    taxonomy_governance: Any,
):
    def handler(context: JobContext) -> dict[str, object]:
        return knowledge_link_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            link_gateway=(
                gateway_factory() if gateway_factory is not None else None
            ),
            taxonomy_governance=taxonomy_governance,
        )

    return handler


def _build_taxonomy_suggestion_handler(
    *,
    question_bank_db_path: Path,
    data_root: Path,
    taxonomy_suggestion_runner: Callable[..., dict[str, object]],
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
):
    read_service = QuestionBankReadService(
        question_bank_db_path,
        data_root=data_root,
    )
    taxonomy_state_path = Path(taxonomy_governance.state_path)
    taxonomy_state_suffix = taxonomy_state_path.suffix or ".json"
    suggestion_state_path = taxonomy_state_path.with_name(
        f"{taxonomy_state_path.stem}.suggestions"
        f"{taxonomy_state_suffix}"
    )

    def handler(context: JobContext) -> dict[str, object]:
        return taxonomy_suggestion_runner(
            context=context,
            suggestion_state_path=suggestion_state_path,
            taxonomy_governance=taxonomy_governance,
            question_loader=read_service.get_questions,
            ai_service_factory=ai_service_factory,
        )

    return handler


def _build_question_bank_sync_handler(
    *,
    db_path: Path,
    question_bank_db_path: Path,
    data_root: Path,
    question_bank_sync_runner: Callable[..., dict[str, object]],
    question_import_runner: Callable[..., dict[str, object]],
    tagging_sync_runner: Callable[..., dict[str, object]],
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
    analysis_artifact_root: Path,
):
    write_service = QuestionBankWriteService(
        question_bank_db_path,
        data_root=data_root,
    )

    def handler(context: JobContext) -> dict[str, object]:
        return question_bank_sync_runner(
            context=context,
            grading_db=open_grading_repositories(db_path),
            question_bank_db_path=question_bank_db_path,
            data_root=data_root,
            write_service=write_service,
            question_import_runner=question_import_runner,
            tagging_sync_runner=tagging_sync_runner,
            ai_service_factory=ai_service_factory,
            taxonomy_governance=taxonomy_governance,
            analysis_artifact_root=analysis_artifact_root,
        )

    return handler


def _build_config_generation_handler(
    *,
    db_path: Path,
    question_bank_db_path: Path,
    data_root: Path,
    mapping_output_dir: Path,
    upload_config_dir: Path,
    config_generation_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any],
    tagging_ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
):
    def handler(context: JobContext) -> dict[str, object]:
        return config_generation_runner(
            context=context,
            db=open_grading_repositories(db_path),
            data_root=data_root,
            mapping_output_dir=mapping_output_dir,
            upload_config_dir=upload_config_dir,
            llm_client_factory=llm_client_factory,
            question_bank_db_path=question_bank_db_path,
            tagging_ai_service_factory=tagging_ai_service_factory,
            taxonomy_governance=taxonomy_governance,
        )

    return handler


def _build_report_export_handler(
    *,
    db_path: Path,
    reports_dir: Path,
    report_generator_factory: ReportGeneratorFactory,
    original_paper_exporter_factory: OriginalPaperExporterFactory,
    analysis_report_exporter_factory: AnalysisReportExporterFactory = AnalysisReportGenerator,
    analysis_llm_client_factory: Callable[[], Any] | None = None,
    data_root: Path | None = None,
):
    def handler(context: JobContext) -> dict[str, object]:
        raw_session_id = context.payload.get("session_id")
        if raw_session_id is None:
            raise ValueError("session_id is required")
        try:
            session_id = int(raw_session_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("session_id must be an integer") from exc
        explicit_report_type = "report_type" in context.payload
        report_type = str(
            context.payload.get("report_type") or "score_excel"
        ).strip()
        if report_type not in {
            "score_excel",
            "annotated_original_pdf",
            *ANALYSIS_REPORT_TYPES,
        }:
            raise ValueError("report_type is not supported")
        score_revision = str(context.payload.get("score_revision") or "").strip()
        reports_dir.mkdir(parents=True, exist_ok=True)
        context.raise_if_cancelled()
        context.report(0.05, "report_export", "starting")
        with tempfile.TemporaryDirectory(
            dir=reports_dir,
            prefix=f".job-{context.job_id}-",
        ) as staging_dir_value:
            staging_dir = Path(staging_dir_value)
            cause_summary: dict[str, object] | None = None
            if report_type == "score_excel":
                generator = report_generator_factory(
                    open_grading_repositories(db_path),
                    staging_dir,
                )
                raw_excel_options = context.payload.get(
                    "score_excel_options"
                )
                staged_output = Path(
                    generator.export_session(
                        session_id,
                        **(
                            {"score_excel_options": dict(raw_excel_options)}
                            if isinstance(raw_excel_options, dict)
                            else {}
                        ),
                    )
                )
            elif report_type in ANALYSIS_REPORT_TYPES:
                if report_type == "personal_analysis_html":
                    # 已确认流程：生成个人报告前先整理错因；失败题不重发，
                    # 报告照常生成、该题不显示错误类型。
                    from backend.class_analysis import (
                        ClassAnalysisStateStore,
                        run_cause_analysis,
                    )
                    try:
                        cause_summary = run_cause_analysis(
                            context,
                            db=open_grading_repositories(db_path),
                            data_root=data_root,
                            store=ClassAnalysisStateStore(reports_dir),
                            llm_client_factory=analysis_llm_client_factory,
                            retry_failed=False,
                            progress_band=(0.05, 0.45),
                            progress_stage="report_export",
                        )
                    except Exception:
                        context.raise_if_cancelled()
                        # 整理整体异常不阻断报告导出，结果中如实记录。
                        cause_summary = {"kind": "causes", "status": "error"}
                    context.report(0.5, "report_export", "generating_reports")
                # AI 叙述缓存放在受控 reports 目录下，跨 job 命中不重复调用模型。
                staged_output = Path(
                    analysis_report_exporter_factory(
                        open_grading_repositories(db_path),
                        staging_dir,
                        llm_client_factory=analysis_llm_client_factory,
                        narrative_cache_dir=(
                            reports_dir / ".analysis_narrative_cache"
                        ),
                        data_root=data_root,
                        reports_dir=reports_dir,
                    ).export_session(
                        session_id,
                        report_type,
                        score_revision=score_revision,
                    )
                )
            else:
                staged_output = Path(
                    original_paper_exporter_factory(
                        open_grading_repositories(db_path),
                        staging_dir,
                    ).export_session_originals(session_id)
                )
            try:
                staged_output.resolve().relative_to(staging_dir.resolve())
            except ValueError as exc:
                raise ValueError("report output must stay inside the job staging directory") from exc
            if not staged_output.is_file():
                raise FileNotFoundError(f"report output was not created: {staged_output}")
            context.report(0.95, "report_export", staged_output.name)
            context.raise_if_cancelled()
            output_path = reports_dir / (
                f"{staged_output.stem}_job-{context.job_id}{staged_output.suffix}"
            )
            os.replace(staged_output, output_path)
            result: dict[str, object] = {
                "session_id": session_id,
                "file_path": str(output_path),
                "filename": output_path.name,
            }
            if cause_summary is not None:
                result["cause_analysis"] = cause_summary
            if explicit_report_type:
                result["report_type"] = report_type
            if score_revision:
                result["score_revision"] = score_revision
            return result

    return handler


def _build_class_analysis_trigger(
    *,
    manager: JobManager,
    db_path: Path,
    reports_dir: Path,
):
    # 惰性导入避免 backend.class_analysis ↔ backend.jobs 循环导入
    from backend.class_analysis import build_class_analysis_auto_trigger

    return build_class_analysis_auto_trigger(
        manager=manager,
        db_path=db_path,
        reports_dir=reports_dir,
    )


def _build_class_analysis_generate_handler(
    *,
    db_path: Path,
    reports_dir: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None,
):
    def handler(context: JobContext) -> dict[str, object]:
        from backend.class_analysis import run_class_analysis_generate

        return run_class_analysis_generate(
            context,
            db_path=Path(db_path),
            reports_dir=Path(reports_dir),
            data_root=data_root,
            llm_client_factory=llm_client_factory,
        )

    return handler


def _build_grading_run_handler(
    *,
    db_path: Path,
    exams_dir: Path,
    templates_dir: Path,
    data_root: Path,
    question_bank_db_path: Path,
    grading_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any],
    class_analysis_completed_trigger: Callable[[int], None] | None = None,
):
    def handler(context: JobContext) -> dict[str, object]:
        session_id = _required_int(context.payload, "session_id")
        job_exams_dir = Path(
            str(context.payload.get("exams_dir") or exams_dir / f"session_{session_id}" / "uploaded_scans")
        )
        max_workers = context.payload.get("max_workers")
        requests_per_minute = context.payload.get("requests_per_minute")
        resume_run_id = context.payload.get("resume_run_id")
        supplement_run_id = context.payload.get("supplement_run_id")
        result = grading_runner(
            db=open_grading_repositories(db_path),
            session_id=session_id,
            exams_dir=job_exams_dir,
            session_work_dir=templates_dir / f"session_{session_id}",
            data_root=data_root,
            question_bank_db_path=question_bank_db_path,
            llm_client_factory=llm_client_factory,
            report=context.report,
            grading_mode=str(context.payload.get("grading_mode") or "ai"),
            scan_batch_id=(
                str(context.payload["scan_batch_id"])
                if context.payload.get("scan_batch_id")
                else None
            ),
            failed_only=bool(context.payload.get("failed_only", False)),
            enhance_images=bool(context.payload.get("enhance_images", True)),
            max_workers=int(max_workers) if max_workers is not None else None,
            requests_per_minute=int(requests_per_minute) if requests_per_minute is not None else None,
            resume_run_id=int(resume_run_id) if resume_run_id is not None else None,
            supplement_only=bool(context.payload.get("supplement_only", False)),
            supplement_run_id=(
                int(supplement_run_id) if supplement_run_id is not None else None
            ),
            raise_if_cancelled=context.raise_if_cancelled,
            should_cancel=context.is_cancel_requested,
        )
        summary = result.get("summary") if isinstance(result, dict) else {}
        detail = "grading run complete"
        if isinstance(summary, dict):
            detail = f"graded={summary.get('graded', 0)} failed={summary.get('failed', 0)}"
        # 阅卷完成（session_completed）是班级分析自动生成的挂钩点；
        # 触发失败不影响批改任务本身的结果。
        if (
            isinstance(result, dict)
            and result.get("state") == "completed"
            and class_analysis_completed_trigger is not None
        ):
            try:
                class_analysis_completed_trigger(session_id)
            except Exception:
                pass
        context.report(0.98, "grading_run", detail)
        return result

    return handler


def _build_scan_analysis_handler(
    *,
    db_path: Path,
    exams_dir: Path,
    templates_dir: Path,
    data_root: Path,
    scan_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any],
):
    def handler(context: JobContext) -> dict[str, object]:
        session_id = _required_int(context.payload, "session_id")
        job_exams_dir = Path(
            str(context.payload.get("exams_dir") or exams_dir / f"session_{session_id}" / "uploaded_scans")
        )
        ocr_workers = context.payload.get("ocr_workers")
        context.report(0.05, "准备文件", "正在启动扫描预检")
        scan_kwargs: dict[str, Any] = {
            "db": open_grading_repositories(db_path),
            "session_id": session_id,
            "exams_dir": job_exams_dir,
            "session_work_dir": templates_dir / f"session_{session_id}",
            "data_root": data_root,
            "llm_client_factory": llm_client_factory,
            "enhance_images": bool(context.payload.get("enhance_images", True)),
            "ocr_workers": int(ocr_workers) if ocr_workers is not None else None,
            "front_page_parity": (
                str(context.payload["front_page_parity"])
                if context.payload.get("front_page_parity")
                else None
            ),
            "raise_if_cancelled": context.raise_if_cancelled,
            "report": context.report,
        }
        for key in (
            "template_id",
            "template_fingerprint",
            "template_first_page_role",
            "config_revision",
        ):
            if context.payload.get(key) is not None:
                scan_kwargs[key] = context.payload[key]
        if context.payload.get("scan_batch_id"):
            scan_kwargs["scan_batch_id"] = str(context.payload["scan_batch_id"])
        result = scan_runner(
            **scan_kwargs,
        )
        summary = result.get("summary") if isinstance(result, dict) else {}
        if isinstance(summary, dict):
            detail = (
                f"预检完成：自动匹配 {summary.get('auto_matched', 0)} 份，"
                f"待处理 {summary.get('issues', 0)} 份，"
                f"共 {summary.get('total_pages', 0)} 页"
            )
        else:
            detail = "扫描预检已完成"
        context.report(0.98, "生成结果", detail)
        return result

    return handler


def _required_int(payload: dict[str, Any], field_name: str) -> int:
    raw_value = payload.get(field_name)
    if raw_value is None:
        raise ValueError(f"{field_name} is required")
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _infer_data_root(db_path: Path) -> Path:
    parent = Path(db_path).parent
    if parent.name == "databases":
        return parent.parent
    return parent


def _active_llm_client() -> LLMClient:
    settings = _active_llm_settings()
    if settings is None:
        raise ValueError("active LLM API configuration is missing")
    return LLMClient(settings)


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _active_llm_settings() -> LLMSettings | None:
    store = get_api_profile_store()
    profile = resolve_profile_for_task(store, "grading")
    content_profile = resolve_profile_for_task(store, "content_generation")
    api_key = str(profile.get("api_key") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        return None
    base_url = normalize_openai_base_url(
        str(profile.get("base_url") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1")
    )
    grading_model = str(profile.get("grading_model") or os.getenv("LLM_GRADING_MODEL") or "gpt-4o")
    return LLMSettings(
        api_key=api_key,
        base_url=base_url,
        ocr_model=str(profile.get("ocr_model") or os.getenv("LLM_OCR_MODEL") or grading_model),
        grading_model=grading_model,
        config_model=str(content_profile.get("config_model") or os.getenv("LLM_CONFIG_MODEL") or grading_model),
        config_api_key=str(content_profile.get("config_api_key") or content_profile.get("api_key") or os.getenv("LLM_CONFIG_API_KEY") or api_key),
        config_base_url=normalize_openai_base_url(
            str(content_profile.get("config_base_url") or content_profile.get("base_url") or os.getenv("LLM_CONFIG_BASE_URL") or base_url)
        ),
        policy_profile=policy_overrides_from_profile(profile),
    )
