from backend.workspaces.contracts import WorkspaceFeature


def create_workspace_feature() -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id="class-teacher",
        api_prefix="/api/class-teacher",
        job_prefix="class_teacher",
        enabled=False,
    )
