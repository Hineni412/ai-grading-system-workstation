from backend.workspaces.contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)

from .api import create_router
from .encrypted_database import EncryptedDatabase
from .vault_service import VaultService


def _create_service(context: WorkspaceContext) -> VaultService:
    return VaultService(context)


def _migration_plan(
    context: WorkspaceContext,
) -> WorkspaceMigrationPlan | None:
    database = EncryptedDatabase(context)
    database_path = database.database_path
    if not database_path.is_file():
        return None
    return WorkspaceMigrationPlan(
        target="student_affairs",
        database_path=database_path,
        migrations_dir=database.migrations_dir,
        backup_dir=database.backup_dir,
        preflight=lambda: None,
    )


def create_workspace_feature() -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id="class-teacher",
        api_prefix="/api/class-teacher",
        job_prefix="class_teacher",
        enabled=True,
        router_factory=create_router,
        service_factory=_create_service,
        migration_provider=_migration_plan,
    )
