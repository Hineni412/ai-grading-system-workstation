from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from fastapi import APIRouter

from backend.jobs.manager import JobHandler


class WorkspaceJobRegistrar(Protocol):
    def register(self, name: str, handler: JobHandler) -> None:
        """Register one handler below the feature's reserved Job prefix."""


class WorkspaceAITaskRegistrar(Protocol):
    def register_adapter(self, task_kind: str, adapter: object) -> None:
        """Register one domain Adapter with the shared AI Task service."""


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    module_id: str
    root: Path
    paths: Any


@dataclass(frozen=True, slots=True)
class WorkspaceMigrationPlan:
    target: str
    database_path: Path
    migrations_dir: Path
    backup_dir: Path
    preflight: Callable[[], None]


EnabledPredicate = bool | Callable[[Any], bool]
RouterFactory = Callable[[], APIRouter]
ServiceFactory = Callable[[WorkspaceContext], object]
MigrationProvider = Callable[[WorkspaceContext], WorkspaceMigrationPlan | None]
JobRegistration = Callable[[WorkspaceJobRegistrar, object | None], None]
AITaskRegistration = Callable[[WorkspaceAITaskRegistrar, object | None], None]


@dataclass(frozen=True, slots=True)
class WorkspaceFeature:
    module_id: str
    api_prefix: str
    job_prefix: str
    enabled: EnabledPredicate = False
    router_factory: RouterFactory | None = None
    service_factory: ServiceFactory | None = None
    migration_provider: MigrationProvider | None = None
    register_jobs: JobRegistration | None = None
    register_ai_tasks: AITaskRegistration | None = None

    def is_enabled(self, paths: Any) -> bool:
        value = self.enabled(paths) if callable(self.enabled) else self.enabled
        if not isinstance(value, bool):
            raise TypeError("enabled predicate must return bool")
        return value
