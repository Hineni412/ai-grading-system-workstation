from backend.workspaces.contracts import WorkspaceFeature


def create_workspace_feature() -> WorkspaceFeature:
    return WorkspaceFeature(
        module_id="teaching-prep",
        api_prefix="/api/teaching-prep",
        job_prefix="teaching_prep",
        enabled=False,
    )
