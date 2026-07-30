from __future__ import annotations

import os
from pathlib import Path

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.infrastructure.evidence import (
    ReadOnlyAssessmentEvidenceReader,
    ReadOnlyQuestionEvidenceReader,
)
from backend.teaching_prep.infrastructure.wps_adapter import (
    SubprocessWpsAdapter,
)
from backend.teaching_prep.jobs import register_jobs
from backend.workspaces.contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)


_A00_GATE_PASSED = True


def _enabled(_paths: object) -> bool:
    raw = os.environ.get("AI_GRADING_TEACHING_PREP_ENABLED", "0")
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
    helper = (
        SubprocessWpsAdapter(
            helper_script=(
                project_root
                / "backend"
                / "teaching_prep"
                / "infrastructure"
                / "wps_helper.ps1"
            ),
        )
        if real_wps_enabled
        else None
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
        wps_adapter=helper,
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
    )
