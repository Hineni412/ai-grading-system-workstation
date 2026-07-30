from __future__ import annotations

from backend.jobs.manager import JobContext
from backend.teaching_prep.application import TeachingPrepService
from backend.workspaces.contracts import WorkspaceJobRegistrar


def register_jobs(
    registrar: WorkspaceJobRegistrar,
    service: object | None,
) -> None:
    if not isinstance(service, TeachingPrepService):
        raise TypeError("teaching-prep service is required for Job registration")

    def reconcile(context: JobContext) -> dict[str, object]:
        context.report(0.25, "checking", "Checking interrupted operations")
        context.raise_if_cancelled()
        interrupted = service.mark_interrupted_operations()
        context.report(1.0, "completed", "Interrupted operations reconciled")
        return {"interrupted_operations": interrupted}

    registrar.register("reconcile", reconcile)
