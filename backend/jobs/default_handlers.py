from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from api_profiles import active_api_profile, get_api_profile_store
from backend.llm.policy import policy_overrides_from_profile
from db_manager import DBManager
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from original_paper_exporter import OriginalPaperExporter
from report import ReportGenerator
from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.question_write_service import QuestionBankWriteService

from .manager import JobContext, JobManager
from .config_generation import run_config_generation_job
from .assembly_export import run_assembly_export_job
from .grading_run import run_grading_job
from .question_import import run_question_import_job
from .scan_analysis import run_scan_analysis
from .tagging_sync import run_tagging_sync_job
from .training_export import run_training_export_job


class ReportGeneratorFactory(Protocol):
    def __call__(self, db_path: Path, reports_dir: Path) -> ReportGenerator:
        ...


class OriginalPaperExporterFactory(Protocol):
    def __call__(self, db: DBManager, output_dir: Path) -> OriginalPaperExporter:
        ...


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
    training_output_root: Path | None = None,
    report_generator_factory: ReportGeneratorFactory = ReportGenerator,
    original_paper_exporter_factory: OriginalPaperExporterFactory = OriginalPaperExporter,
    scan_runner: Callable[..., dict[str, object]] = run_scan_analysis,
    grading_runner: Callable[..., dict[str, object]] = run_grading_job,
    config_generation_runner: Callable[..., dict[str, object]] = run_config_generation_job,
    question_import_runner: Callable[..., dict[str, object]] = run_question_import_job,
    tagging_sync_runner: Callable[..., dict[str, object]] = run_tagging_sync_job,
    training_export_runner: Callable[..., dict[str, object]] = run_training_export_job,
    assembly_export_runner: Callable[..., dict[str, object]] = run_assembly_export_job,
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
    manager.register(
        "report_export",
        _build_report_export_handler(
            db_path=Path(db_path),
            reports_dir=Path(reports_dir),
            report_generator_factory=report_generator_factory,
            original_paper_exporter_factory=original_paper_exporter_factory,
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
        ),
    )
    manager.register(
        "config_generation",
        _build_config_generation_handler(
            db_path=Path(db_path),
            data_root=base_data_root,
            mapping_output_dir=(
                Path(templates_dir)
                if templates_dir is not None
                else base_data_root / "templates"
            ),
            upload_config_dir=(
                Path(upload_config_dir)
                if upload_config_dir is not None
                else base_data_root / "config" / "uploaded"
            ),
            config_generation_runner=config_generation_runner,
            llm_client_factory=scan_llm_client_factory,
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
            tagging_sync_runner=tagging_sync_runner,
            ai_service_factory=tagging_ai_service_factory,
        ),
    )
    manager.register(
        "training_export",
        _build_training_export_handler(
            question_bank_db_path=resolved_question_bank_db,
            output_root=(
                Path(training_output_root)
                if training_output_root is not None
                else base_data_root / "outputs" / "training"
            ),
            training_export_runner=training_export_runner,
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


def _build_training_export_handler(
    *,
    question_bank_db_path: Path,
    output_root: Path,
    training_export_runner: Callable[..., dict[str, object]],
):
    def handler(context: JobContext) -> dict[str, object]:
        return training_export_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            output_root=output_root,
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
        )

    return handler


def _build_tagging_sync_handler(
    *,
    question_bank_db_path: Path,
    tagging_sync_runner: Callable[..., dict[str, object]],
    ai_service_factory: Callable[[], Any],
):
    def handler(context: JobContext) -> dict[str, object]:
        return tagging_sync_runner(
            context=context,
            question_bank_db_path=question_bank_db_path,
            ai_service_factory=ai_service_factory,
        )

    return handler


def _build_config_generation_handler(
    *,
    db_path: Path,
    data_root: Path,
    mapping_output_dir: Path,
    upload_config_dir: Path,
    config_generation_runner: Callable[..., dict[str, object]],
    llm_client_factory: Callable[[], Any],
):
    def handler(context: JobContext) -> dict[str, object]:
        return config_generation_runner(
            context=context,
            db=DBManager(db_path),
            data_root=data_root,
            mapping_output_dir=mapping_output_dir,
            upload_config_dir=upload_config_dir,
            llm_client_factory=llm_client_factory,
        )

    return handler


def _build_report_export_handler(
    *,
    db_path: Path,
    reports_dir: Path,
    report_generator_factory: ReportGeneratorFactory,
    original_paper_exporter_factory: OriginalPaperExporterFactory,
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
        if report_type not in {"score_excel", "annotated_original_pdf"}:
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
            if report_type == "score_excel":
                staged_output = Path(
                    report_generator_factory(db_path, staging_dir).export_session(session_id)
                )
            else:
                staged_output = Path(
                    original_paper_exporter_factory(
                        DBManager(db_path),
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
            if explicit_report_type:
                result["report_type"] = report_type
            if score_revision:
                result["score_revision"] = score_revision
            return result

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
            db=DBManager(db_path),
            session_id=session_id,
            exams_dir=job_exams_dir,
            session_work_dir=templates_dir / f"session_{session_id}",
            data_root=data_root,
            question_bank_db_path=question_bank_db_path,
            llm_client_factory=llm_client_factory,
            report=context.report,
            grading_mode=str(context.payload.get("grading_mode") or "full_paper"),
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
        context.report(0.05, "scan_analysis", "starting")
        scan_kwargs: dict[str, Any] = {
            "db": DBManager(db_path),
            "session_id": session_id,
            "exams_dir": job_exams_dir,
            "session_work_dir": templates_dir / f"session_{session_id}",
            "data_root": data_root,
            "llm_client_factory": llm_client_factory,
            "enhance_images": bool(context.payload.get("enhance_images", True)),
            "ocr_workers": int(ocr_workers) if ocr_workers is not None else None,
            "front_page_parity": str(context.payload.get("front_page_parity") or "odd"),
            "raise_if_cancelled": context.raise_if_cancelled,
        }
        if context.payload.get("scan_batch_id"):
            scan_kwargs["scan_batch_id"] = str(context.payload["scan_batch_id"])
        result = scan_runner(
            **scan_kwargs,
        )
        summary = result.get("summary") if isinstance(result, dict) else {}
        if isinstance(summary, dict):
            detail = (
                f"matched={summary.get('auto_matched', 0)} "
                f"issues={summary.get('issues', 0)} "
                f"pages={summary.get('total_pages', 0)}"
            )
        else:
            detail = "scan analysis complete"
        context.report(0.95, "scan_analysis", detail)
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


def _active_llm_settings() -> LLMSettings | None:
    profile = active_api_profile(get_api_profile_store().load())
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
        config_model=str(profile.get("config_model") or os.getenv("LLM_CONFIG_MODEL") or grading_model),
        config_api_key=str(profile.get("config_api_key") or os.getenv("LLM_CONFIG_API_KEY") or api_key),
        config_base_url=normalize_openai_base_url(
            str(profile.get("config_base_url") or os.getenv("LLM_CONFIG_BASE_URL") or base_url)
        ),
        policy_profile=policy_overrides_from_profile(profile),
    )
