from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, FastAPI

from backend.jobs.manager import JobHandler, JobManager
from backend.schema_migrations import ensure_schema_current

from .contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)


_MODULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_API_PREFIX = re.compile(r"^/api/[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_JOB_PREFIX = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_MIGRATION_TARGET = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JOB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class WorkspaceRegistrationError(RuntimeError):
    """A workspace feature could not be safely loaded or started."""


@dataclass(frozen=True, slots=True)
class WorkspaceFeatureSource:
    module_id: str
    factory_path: str


DEFAULT_FEATURE_SOURCES = (
    WorkspaceFeatureSource(
        "teaching-prep",
        "backend.teaching_prep.feature:create_workspace_feature",
    ),
    WorkspaceFeatureSource(
        "class-teacher",
        "backend.class_teacher.feature:create_workspace_feature",
    ),
)


class _PrefixedJobRegistrar:
    def __init__(self, manager: JobManager, prefix: str) -> None:
        self._manager = manager
        self._prefix = prefix

    def register(self, name: str, handler: JobHandler) -> None:
        clean_name = str(name or "").strip()
        if not _JOB_NAME.fullmatch(clean_name):
            raise ValueError("workspace Job name is invalid")
        if not callable(handler):
            raise TypeError("workspace Job handler must be callable")
        self._manager.register(f"{self._prefix}.{clean_name}", handler)


class WorkspaceRegistry:
    """Validated feature registry shared by app creation and startup."""

    def __init__(self, features: Iterable[WorkspaceFeature], *, paths: Any) -> None:
        self._paths = paths
        self._features = tuple(
            sorted(features, key=lambda feature: feature.module_id)
        )
        self._validate()
        enabled: list[WorkspaceFeature] = []
        for feature in self._features:
            try:
                if feature.is_enabled(paths):
                    enabled.append(feature)
            except Exception as exc:
                raise WorkspaceRegistrationError(
                    f"workspace {feature.module_id} enablement failed"
                ) from exc
        self._enabled_features = tuple(enabled)

    @property
    def features(self) -> tuple[WorkspaceFeature, ...]:
        return self._features

    @property
    def enabled_features(self) -> tuple[WorkspaceFeature, ...]:
        return self._enabled_features

    def include_routers(self, api: FastAPI) -> None:
        for feature in self._enabled_features:
            if feature.router_factory is None:
                continue
            try:
                router = feature.router_factory()
                if not isinstance(router, APIRouter):
                    raise TypeError("router_factory must return APIRouter")
                api.include_router(router, prefix=feature.api_prefix)
            except Exception as exc:
                raise WorkspaceRegistrationError(
                    f"workspace {feature.module_id} router registration failed"
                ) from exc

    def run_migrations(self) -> None:
        for feature in self._enabled_features:
            if feature.migration_provider is None:
                continue
            context = self._context(feature)
            try:
                plan = feature.migration_provider(context)
                if plan is None:
                    continue
                self._run_migration_plan(feature, context, plan)
            except WorkspaceRegistrationError:
                raise
            except Exception as exc:
                raise WorkspaceRegistrationError(
                    f"workspace {feature.module_id} migration failed"
                ) from exc

    def create_services(self) -> dict[str, object]:
        services: dict[str, object] = {}
        for feature in self._enabled_features:
            if feature.service_factory is None:
                continue
            try:
                services[feature.module_id] = feature.service_factory(
                    self._context(feature)
                )
            except Exception as exc:
                raise WorkspaceRegistrationError(
                    f"workspace {feature.module_id} service startup failed"
                ) from exc
        return services

    def register_jobs(
        self,
        manager: JobManager,
        services: dict[str, object],
    ) -> None:
        for feature in self._enabled_features:
            if feature.register_jobs is None:
                continue
            try:
                feature.register_jobs(
                    _PrefixedJobRegistrar(manager, feature.job_prefix),
                    services.get(feature.module_id),
                )
            except Exception as exc:
                raise WorkspaceRegistrationError(
                    f"workspace {feature.module_id} Job registration failed"
                ) from exc

    def _context(self, feature: WorkspaceFeature) -> WorkspaceContext:
        workspace_dir = getattr(self._paths, "workspace_dir", None)
        if not callable(workspace_dir):
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} controlled path is unavailable"
            )
        return WorkspaceContext(
            module_id=feature.module_id,
            root=Path(workspace_dir(feature.module_id, create=False)),
            paths=self._paths,
        )

    def _validate(self) -> None:
        seen_ids: set[str] = set()
        seen_api_prefixes: set[str] = set()
        seen_job_prefixes: set[str] = set()
        for feature in self._features:
            module_id = str(feature.module_id or "").strip()
            api_prefix = str(feature.api_prefix or "").strip()
            job_prefix = str(feature.job_prefix or "").strip()
            if not _MODULE_ID.fullmatch(module_id):
                raise WorkspaceRegistrationError("workspace module ID is invalid")
            if not _API_PREFIX.fullmatch(api_prefix):
                raise WorkspaceRegistrationError(
                    f"workspace {module_id} API prefix is invalid"
                )
            if not _JOB_PREFIX.fullmatch(job_prefix):
                raise WorkspaceRegistrationError(
                    f"workspace {module_id} Job prefix is invalid"
                )
            if module_id in seen_ids:
                raise WorkspaceRegistrationError(
                    f"duplicate workspace module ID: {module_id}"
                )
            if api_prefix in seen_api_prefixes:
                raise WorkspaceRegistrationError(
                    f"duplicate workspace API prefix: {api_prefix}"
                )
            if job_prefix in seen_job_prefixes:
                raise WorkspaceRegistrationError(
                    f"duplicate workspace Job prefix: {job_prefix}"
                )
            seen_ids.add(module_id)
            seen_api_prefixes.add(api_prefix)
            seen_job_prefixes.add(job_prefix)

    @staticmethod
    def _run_migration_plan(
        feature: WorkspaceFeature,
        context: WorkspaceContext,
        plan: WorkspaceMigrationPlan,
    ) -> None:
        if not _MIGRATION_TARGET.fullmatch(str(plan.target or "")):
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} migration target is invalid"
            )
        database_path = Path(plan.database_path)
        migrations_dir = Path(plan.migrations_dir)
        backup_dir = Path(plan.backup_dir)
        try:
            database_path.resolve(strict=False).relative_to(
                context.root.resolve(strict=False)
            )
            backup_dir.resolve(strict=False).relative_to(
                context.root.resolve(strict=False)
            )
        except ValueError as exc:
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} migration data path escaped"
            ) from exc
        project_root = Path(
            getattr(
                context.paths,
                "migration_project_root",
                getattr(context.paths, "project_root", Path.cwd()),
            )
        )
        try:
            migrations_dir.resolve(strict=False).relative_to(
                (project_root / "migrations").resolve(strict=False)
            )
        except ValueError as exc:
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} migration source escaped"
            ) from exc
        if not callable(plan.preflight):
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} migration preflight is required"
            )
        plan.preflight()
        result = ensure_schema_current(
            plan.target,
            database_path,
            migrations_dir=migrations_dir,
            backup_dir=backup_dir,
        )
        if result.target != plan.target:
            raise WorkspaceRegistrationError(
                f"workspace {feature.module_id} migration target changed"
            )


def _load_feature(source: WorkspaceFeatureSource) -> WorkspaceFeature:
    try:
        module_name, separator, factory_name = source.factory_path.partition(":")
        if not separator or not module_name or not factory_name:
            raise ValueError("feature factory path is invalid")
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        feature = factory()
        if not isinstance(feature, WorkspaceFeature):
            raise TypeError("feature factory must return WorkspaceFeature")
        if feature.module_id != source.module_id:
            raise ValueError("feature module ID does not match its source")
        return feature
    except Exception as exc:
        raise WorkspaceRegistrationError(
            f"workspace {source.module_id} manifest failed to load"
        ) from exc


def load_default_workspace_registry(paths: Any) -> WorkspaceRegistry:
    return WorkspaceRegistry(
        (_load_feature(source) for source in DEFAULT_FEATURE_SOURCES),
        paths=paths,
    )


__all__ = [
    "DEFAULT_FEATURE_SOURCES",
    "WorkspaceFeatureSource",
    "WorkspaceRegistrationError",
    "WorkspaceRegistry",
    "load_default_workspace_registry",
]
