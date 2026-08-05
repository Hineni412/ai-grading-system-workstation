from __future__ import annotations

from backend.jobs.manager import JobCancellationRequested, JobContext, JobManager

from .service import WorkspaceAITaskService


def register_workspace_ai_job(
    manager: JobManager,
    service: WorkspaceAITaskService,
) -> None:
    def run(context: JobContext) -> dict[str, object]:
        task_id = str(context.payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("workspace AI task_id is required")
        context.report(0.02, "workspace_ai", "workspace AI task started")
        result = service.run_task(task_id)
        status = str(result.get("status") or "")
        if status in {"cancelled_before_dispatch", "discarded"}:
            raise JobCancellationRequested("workspace AI task cancelled")
        if status in {
            "failed_before_dispatch",
            "failed",
            "result_unknown",
            "invalid_result",
        }:
            raise RuntimeError("workspace AI task did not complete")
        return result

    manager.register("workspace_ai.run", run)


__all__ = ["register_workspace_ai_job"]
