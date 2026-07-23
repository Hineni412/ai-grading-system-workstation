from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
import threading

from fastapi import Depends, Request

from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.compat import open_grading_repositories
from backend.analytics import SessionAnalysisService
from backend.api.read_connections import (
    RequestReadContext,
    RequestReadContextCleanupError,
    request_read_context,
)
from backend.config_workspace.sources import ConfigSourceService
from backend.files.service import JobFileService
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.ops.jobs import register_ops_job_handlers
from backend.media.service import ReviewMediaService
from backend.ops.service import OpsSelfCheckService
from backend.ops.plan_store import OpsPlanStore
from backend.ops.write_service import OpsWriteService
from backend.review.service import ReviewApplicationService
from backend.students import StudentRosterModule
from backend.repositories.sessions import SessionRepositoryGateway
from backend.repositories.students import StudentRepositoryGateway
from backend.scan_grading.config_fingerprint import (
    session_grading_config_fingerprint,
)
from backend.scan_grading.workspace import ScanGradingWorkspace
from backend.workbench.service import WorkbenchService
from template_upload_service import TemplateUploadService
from manual_review_service import ManualReviewService
from path_manager import PathManager, get_path_manager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_read_service import (
    QuestionBankSnapshotError,
)
from question_bank.services.training_task_service import TrainingTaskService
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService


_TEMPLATE_UPLOAD_SERVICE_GUARD = threading.Lock()


def get_grading_db() -> GradingRepositoryAccess:
    """Compatibility dependency name; active callers receive repositories."""
    return open_grading_repositories(get_path_manager().db_path)


def get_student_repository(
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> StudentRepositoryGateway:
    return db.student_repository


def get_session_repository(
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> SessionRepositoryGateway:
    return db.session_repository


def get_student_roster_module(
    students: StudentRepositoryGateway = Depends(get_student_repository),
) -> StudentRosterModule:
    return StudentRosterModule(students)


def get_session_analysis_service(
    db: GradingRepositoryAccess = Depends(get_grading_db),
) -> SessionAnalysisService:
    return SessionAnalysisService(db)


def get_ops_self_check_service(
    paths: PathManager = Depends(get_path_manager),
) -> OpsSelfCheckService:
    return OpsSelfCheckService(paths)


def create_ops_write_service(
    path_manager: PathManager | None = None,
) -> OpsWriteService:
    paths = path_manager or get_path_manager()
    project_root = Path(
        getattr(paths, "project_root", Path(__file__).resolve().parents[2])
    )
    return OpsWriteService(
        paths,
        plan_store=OpsPlanStore(),
        migration_dirs={
            "grading": project_root / "migrations" / "grading",
            "question_bank": project_root / "migrations" / "question_bank",
        },
    )


def get_ops_write_service(request: Request) -> OpsWriteService:
    service = getattr(request.app.state, "ops_write_service", None)
    if service is None:
        raise RuntimeError("OpsWriteService is unavailable outside application lifespan")
    return service


def get_upload_config_dir() -> Path:
    return get_path_manager().upload_config_dir


def get_config_source_service(
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> ConfigSourceService:
    return ConfigSourceService(upload_config_dir)


def get_templates_dir() -> Path:
    return get_path_manager().templates_dir


def get_template_upload_service(
    request: Request,
    templates_dir: Path = Depends(get_templates_dir),
) -> TemplateUploadService:
    with _TEMPLATE_UPLOAD_SERVICE_GUARD:
        service = getattr(request.app.state, "template_upload_service", None)
        if service is None or service.templates_dir != Path(templates_dir):
            service = TemplateUploadService(templates_dir)
            request.app.state.template_upload_service = service
        return service


def get_config_mapping_output_dir(
    templates_dir: Path = Depends(get_templates_dir),
) -> Path:
    return templates_dir


def get_annotated_dir() -> Path:
    return get_path_manager().annotated_dir


def get_data_root() -> Path:
    return get_path_manager().data_root


def get_exams_dir() -> Path:
    return get_path_manager().exams_dir


def get_reports_dir() -> Path:
    return get_path_manager().reports_dir


def get_backups_dir() -> Path:
    return get_path_manager().backups_dir


def get_outputs_dir() -> Path:
    return get_path_manager().outputs_dir


def get_question_bank_read_service() -> QuestionBankReadService:
    paths = get_path_manager()
    return QuestionBankReadService(
        paths.qb_db_path,
        data_root=paths.data_root,
    )


def get_question_bank_write_service() -> QuestionBankWriteService:
    paths = get_path_manager()
    return QuestionBankWriteService(paths.qb_db_path, data_root=paths.data_root)


def get_assembly_workspace_service() -> AssemblyWorkspaceService:
    return AssemblyWorkspaceService(get_path_manager().data_root)


def get_diagnosis_profile_service() -> DiagnosisProfileService:
    paths = get_path_manager()
    return DiagnosisProfileService(paths.db_path, paths.qb_db_path)


def get_request_read_context(
    request: Request,
    paths: PathManager = Depends(get_path_manager),
) -> Iterator[RequestReadContext]:
    try:
        with request_read_context(paths) as context:
            yield context
    except (QuestionBankSnapshotError, RequestReadContextCleanupError) as exc:
        from backend.api.app import ApiError

        if request.url.path.startswith("/api/graph/"):
            raise ApiError(
                503,
                "graph_database_unavailable",
                "Graph data is temporarily unavailable",
            ) from exc
        raise ApiError(
            503,
            "training_database_unavailable",
            "Training data is temporarily unavailable",
        ) from exc


def get_request_diagnosis_profile_service(
    context: RequestReadContext = Depends(
        get_request_read_context,
        scope="function",
    ),
) -> DiagnosisProfileService:
    return context.diagnosis_service


def get_request_practice_plan_service(
    context: RequestReadContext = Depends(
        get_request_read_context,
        scope="function",
    ),
) -> PracticePlanService:
    return context.practice_service


def get_graph_diagnosis_profile_service(
    context: RequestReadContext = Depends(
        get_request_read_context,
        scope="function",
    ),
) -> DiagnosisProfileService:
    """Compatibility dependency retained for existing benchmark callers."""
    return context.diagnosis_service


def get_practice_plan_service() -> PracticePlanService:
    return PracticePlanService(get_path_manager().qb_db_path)


def get_training_task_service() -> TrainingTaskService:
    return TrainingTaskService(get_path_manager().qb_db_path)


def get_job_file_service(
    reports_dir: Path = Depends(get_reports_dir),
    backups_dir: Path = Depends(get_backups_dir),
    outputs_dir: Path = Depends(get_outputs_dir),
) -> JobFileService:
    return JobFileService(
        reports_dir,
        training_outputs_dir=outputs_dir / "training",
        assembly_outputs_dir=get_path_manager().data_root
        / "question_bank"
        / "assembly_exports",
        backups_dir=backups_dir,
        ops_outputs_dir=outputs_dir / "ops",
    )


def get_media_service(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    data_root: Path = Depends(get_data_root),
    exams_dir: Path = Depends(get_exams_dir),
    templates_dir: Path = Depends(get_templates_dir),
    annotated_dir: Path = Depends(get_annotated_dir),
) -> ReviewMediaService:
    return ReviewMediaService(
        db,
        data_root=data_root,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        annotated_dir=annotated_dir,
    )


def get_manual_review_service(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    annotated_dir: Path = Depends(get_annotated_dir),
) -> ManualReviewService:
    return ManualReviewService(db, annotated_dir)


def get_review_application_service(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manual_review_service: ManualReviewService = Depends(get_manual_review_service),
) -> ReviewApplicationService:
    return ReviewApplicationService(db, manual_review_service)


def create_job_manager(path_manager: PathManager | None = None) -> JobManager:
    paths = path_manager or get_path_manager()
    upload_config_dir = getattr(
        paths,
        "upload_config_dir",
        Path(paths.data_root) / "config" / "uploaded",
    )
    manager = JobManager(
        JobStore(paths.db_path),
        interrupted_input_root=Path(upload_config_dir),
    )
    try:
        register_default_job_handlers(
            manager,
            db_path=paths.db_path,
            reports_dir=paths.reports_dir,
            exams_dir=paths.exams_dir,
            templates_dir=paths.templates_dir,
            data_root=paths.data_root,
            question_bank_db_path=getattr(
                paths,
                "qb_db_path",
                Path(paths.data_root) / "databases" / "question_bank.db",
            ),
            upload_config_dir=upload_config_dir,
            training_output_root=getattr(
                paths,
                "outputs_dir",
                Path(paths.data_root) / "outputs",
            )
            / "training",
        )
        register_ops_job_handlers(manager, paths=paths)
    except Exception:
        manager.shutdown()
        raise
    return manager


def get_job_manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise RuntimeError("JobManager is unavailable outside application lifespan")
    return manager


def get_scan_grading_workspace(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    manager: JobManager = Depends(get_job_manager),
    exams_dir: Path = Depends(get_exams_dir),
    templates_dir: Path = Depends(get_templates_dir),
    data_root: Path = Depends(get_data_root),
) -> ScanGradingWorkspace:
    return ScanGradingWorkspace(
        exams_root=exams_dir,
        templates_root=templates_dir,
        grading_db_path=db.db_path,
        job_manager=manager,
        config_fingerprint_resolver=lambda session_id, grading_mode: (
            session_grading_config_fingerprint(
                db=db,
                data_root=data_root,
                session_id=session_id,
                grading_mode=grading_mode,
            )
        ),
    )


def get_workbench_service(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    review_service: ReviewApplicationService = Depends(get_review_application_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> WorkbenchService:
    return WorkbenchService(db, review_service, job_manager)
