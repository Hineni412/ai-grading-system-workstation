from __future__ import annotations

import os
from pathlib import Path

from api_profiles import ApiProfileStore
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.infrastructure.evidence import (
    ReadOnlyAssessmentEvidenceReader,
    ReadOnlyQuestionEvidenceReader,
)
from backend.teaching_prep.infrastructure.llm import (
    ActiveProfileExerciseSuggestionModelAdapter,
    ActiveProfileLessonModelAdapter,
    ActiveProfileSemesterMappingModelAdapter,
    ActiveProfileSlideAnimationModelAdapter,
)
from backend.teaching_prep.infrastructure.wps_adapter import (
    SubprocessWpsAdapter,
)
from backend.teaching_prep.jobs import register_jobs
from backend.teaching_prep.application.ai_task_adapter import register_ai_tasks
from backend.workspaces.contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)


_A00_GATE_PASSED = True


def _enabled(_paths: object) -> bool:
    raw = os.environ.get("AI_GRADING_TEACHING_PREP_ENABLED", "1")
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("AI_GRADING_TEACHING_PREP_ENABLED is invalid")


def _migration_plan(context: WorkspaceContext) -> WorkspaceMigrationPlan:
    project_root = Path(
        getattr(context.paths, "migration_project_root", context.paths.project_root)
    )
    migrations_dir = project_root / "migrations" / "teaching_prep"

    def preflight() -> None:
        if not _A00_GATE_PASSED:
            raise RuntimeError("A00 WPS Gate has not passed")
        if not migrations_dir.is_dir():
            raise RuntimeError("teaching-prep migrations are unavailable")

    return WorkspaceMigrationPlan(
        target="teaching_prep",
        database_path=context.root / "teaching_prep.db",
        migrations_dir=migrations_dir,
        backup_dir=context.root / "backups",
        preflight=preflight,
    )


def _service(context: WorkspaceContext) -> TeachingPrepService:
    project_root = Path(
        getattr(
            context.paths,
            "migration_project_root",
            context.paths.project_root,
        )
    )
    real_wps_enabled = _boolean_env(
        "AI_GRADING_TEACHING_PREP_WPS_ENABLED",
        default=False,
    )
    preview_wps_enabled = real_wps_enabled or _boolean_env(
        "AI_GRADING_TEACHING_PREP_WPS_PREVIEW",
        default=False,
    )
    helper_script = (
        project_root
        / "backend"
        / "teaching_prep"
        / "infrastructure"
        / "wps_helper.ps1"
    )
    helper = (
        SubprocessWpsAdapter(helper_script=helper_script)
        if helper_script.is_file()
        else None
    )
    profile_store = ApiProfileStore(
        Path(context.paths.api_profiles_path),
        legacy_paths=tuple(
            Path(item)
            for item in getattr(
                context.paths,
                "legacy_api_profiles_paths",
                (),
            )
        ),
    )
    return TeachingPrepService(
        context.root,
        question_evidence_reader=ReadOnlyQuestionEvidenceReader(
            Path(context.paths.qb_db_path)
        ),
        assessment_evidence_reader=ReadOnlyAssessmentEvidenceReader(
            Path(context.paths.db_path),
            Path(context.paths.qb_db_path),
        ),
        lesson_model_adapter=ActiveProfileLessonModelAdapter(
            context=context,
            profile_store=profile_store,
        ),
        lesson_model_label="当前启用的模型配置",
        semester_mapping_model_adapter=(
            ActiveProfileSemesterMappingModelAdapter(
                context=context,
                profile_store=profile_store,
            )
        ),
        semester_mapping_model_label="当前启用的模型配置",
        exercise_suggestion_model_adapter=(
            ActiveProfileExerciseSuggestionModelAdapter(
                context=context,
                profile_store=profile_store,
            )
        ),
        exercise_suggestion_model_label="当前启用的模型配置",
        slide_animation_model_adapter=(
            ActiveProfileSlideAnimationModelAdapter(
                context=context,
                profile_store=profile_store,
            )
        ),
        slide_animation_model_label="当前启用的模型配置",
        wps_adapter=helper if real_wps_enabled else None,
        preview_wps_adapter=helper if preview_wps_enabled else None,
        wps_adapter_is_real=real_wps_enabled,
    )


def _boolean_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} is invalid")


def create_workspace_feature() -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id="teaching-prep",
        api_prefix="/api/teaching-prep",
        job_prefix="teaching_prep",
        enabled=_enabled,
        router_factory=create_router,
        service_factory=_service,
        migration_provider=_migration_plan,
        register_jobs=register_jobs,
        register_ai_tasks=register_ai_tasks,
    )
