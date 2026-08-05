from __future__ import annotations

from .models import StoredTask, TaskSnapshot
from .registry import message_for, presentation
from .store import WorkspaceAITaskStore


def project_task(store: WorkspaceAITaskStore, task: StoredTask) -> TaskSnapshot:
    task_presentation = presentation(task.module, task.task_kind)
    teacher_message, next_action = message_for(task.status, task.dispatch_evidence)
    handoffs = store.list_handoffs(task.task_id)
    states = [handoff.adoption_state for handoff in handoffs]
    return TaskSnapshot(
        contract_version="teacher_workspace_ai_task.v1",
        task_id=task.task_id,
        operation_id=task.operation_id,
        module=task.module,
        task_kind=task.task_kind,
        source_ref=task.source_ref,
        return_target=task.return_target,
        status=task.status,
        phase=task.phase,
        progress=task.progress,
        send_attempt_count=task.send_attempt_count,
        dispatch_evidence=task.dispatch_evidence,
        cancel_requested=task.cancel_requested,
        job_id=task.job_id,
        proposal_ref_id=task.proposal_ref_id,
        proposal_revision=task.proposal_revision,
        error_code=task.error_code,
        revision=task.revision,
        safe_title=task_presentation.safe_title,
        safe_source=task_presentation.safe_source,
        teacher_message=teacher_message,
        next_action=next_action,
        handoffs=handoffs,
        handoff_total=len(handoffs),
        adopted_count=states.count("adopted"),
        discarded_count=states.count("discarded"),
        stale_count=states.count("stale"),
        pending_count=sum(
            state in {"pending", "opened", "adoption_started"}
            for state in states
        ),
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


__all__ = ["project_task"]
