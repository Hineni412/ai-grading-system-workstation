from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Request

from db_manager import DBManager
from backend.files.service import JobFileService
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.media.service import ReviewMediaService
from backend.review.service import ReviewApplicationService
from manual_review_service import ManualReviewService
from path_manager import PathManager, get_path_manager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.training_task_service import TrainingTaskService
from question_bank.services.question_write_service import QuestionBankWriteService


def get_grading_db() -> DBManager:
    return DBManager(get_path_manager().db_path)


def get_upload_config_dir() -> Path:
    return get_path_manager().upload_config_dir


def get_templates_dir() -> Path:
    return get_path_manager().templates_dir


def get_annotated_dir() -> Path:
    return get_path_manager().annotated_dir


def get_data_root() -> Path:
    return get_path_manager().data_root


def get_exams_dir() -> Path:
    return get_path_manager().exams_dir


def get_reports_dir() -> Path:
    return get_path_manager().reports_dir


def get_question_bank_read_service() -> QuestionBankReadService:
    paths = get_path_manager()
    return QuestionBankReadService(
        paths.qb_db_path,
        data_root=paths.data_root,
    )


def get_question_bank_write_service() -> QuestionBankWriteService:
    paths = get_path_manager()
    return QuestionBankWriteService(paths.qb_db_path, data_root=paths.data_root)


def get_diagnosis_profile_service() -> DiagnosisProfileService:
    paths = get_path_manager()
    return DiagnosisProfileService(paths.db_path, paths.qb_db_path)


def get_practice_plan_service() -> PracticePlanService:
    return PracticePlanService(get_path_manager().qb_db_path)


def get_training_task_service() -> TrainingTaskService:
    return TrainingTaskService(get_path_manager().qb_db_path)


def get_job_file_service(
    reports_dir: Path = Depends(get_reports_dir),
) -> JobFileService:
    return JobFileService(reports_dir)


def get_media_service(
    db: DBManager = Depends(get_grading_db),
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
    db: DBManager = Depends(get_grading_db),
    annotated_dir: Path = Depends(get_annotated_dir),
) -> ManualReviewService:
    return ManualReviewService(db, annotated_dir)


def get_review_application_service(
    db: DBManager = Depends(get_grading_db),
    manual_review_service: ManualReviewService = Depends(get_manual_review_service),
) -> ReviewApplicationService:
    return ReviewApplicationService(db, manual_review_service)


def create_job_manager(path_manager: PathManager | None = None) -> JobManager:
    paths = path_manager or get_path_manager()
    manager = JobManager(JobStore(paths.db_path))
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
            upload_config_dir=getattr(
                paths,
                "upload_config_dir",
                Path(paths.data_root) / "config" / "uploaded",
            ),
        )
    except Exception:
        manager.shutdown()
        raise
    return manager


def get_job_manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise RuntimeError("JobManager is unavailable outside application lifespan")
    return manager
