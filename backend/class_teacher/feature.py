from backend.workspaces.contracts import (
    WorkspaceContext,
    WorkspaceFeature,
    WorkspaceMigrationPlan,
)

from .api import create_router
from .configured_model import create_active_profile_model_gateway
from .encrypted_database import EncryptedDatabase
from .intake.ports import SharedWorkspaceAITaskPort
from .vault_service import VaultService


def _create_service(context: WorkspaceContext) -> VaultService:
    service = VaultService(
        context,
        model_gateway=create_active_profile_model_gateway(context),
    )
    service.prepare_existing_plaintext_runtime()
    if service.ordinary_database.exists:
        service.ordinary_database.initialize_schema()
    return service


def _migration_plan(
    context: WorkspaceContext,
) -> WorkspaceMigrationPlan | None:
    database = EncryptedDatabase(context)
    database_path = database.database_path
    if (
        not database_path.is_file()
        or database.requires_plaintext_migration()
    ):
        return None
    return WorkspaceMigrationPlan(
        target="student_affairs",
        database_path=database_path,
        migrations_dir=database.migrations_dir,
        backup_dir=database.backup_dir,
        preflight=lambda: None,
    )


def _register_ai_tasks(registrar, service: object | None) -> None:
    if not isinstance(service, VaultService):
        raise TypeError("class-teacher service is unavailable")
    port = SharedWorkspaceAITaskPort(
        registrar,
        model_identity=service.workspace_model_gateway,
        conversations=service.intake.conversations,
        adoption=service.intake.adoption,
    )
    service.intake.bind_ai_tasks(port)
    for task_kind in (
        "class_teacher.intake_triage",
        "class_teacher.draft_revision",
        "class_teacher.intake",
    ):
        registrar.register_adapter(task_kind, service.intake.ai_task_adapter)


def create_workspace_feature() -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id="class-teacher",
        api_prefix="/api/class-teacher",
        job_prefix="class_teacher",
        enabled=True,
        router_factory=create_router,
        service_factory=_create_service,
        migration_provider=_migration_plan,
        register_ai_tasks=_register_ai_tasks,
    )
