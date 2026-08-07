from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict

from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdapterResult,
    AdoptionResult,
    HandoffDraft,
    HandoffSnapshot,
    KnownAdapterFailure,
    OpaqueRef,
    RevisionConflictError,
    StoredTask,
)
from backend.teaching_prep.domain.errors import TeachingPrepRetryAvailableError

from .preparation_service import TeachingPrepService


_DESTINATIONS = {
    "teaching_prep.semester_mapping": "teaching_prep.library",
    "teaching_prep.lesson_plan": "teaching_prep.lesson.plan",
    "teaching_prep.exercise_suggestions": "teaching_prep.lesson.exercises",
    "teaching_prep.slide_change_proposal": "teaching_prep.lesson.slides",
}

_ADOPTION_COMMAND: ContextVar[Mapping[str, object] | None] = ContextVar(
    "teaching_prep_adoption_command",
    default=None,
)


class SemesterMappingRetryAvailableFailure(KnownAdapterFailure):
    code = "semester_mapping_retry_available"


@contextmanager
def bind_adoption_command(
    command: Mapping[str, object] | None,
) -> Iterator[None]:
    token = _ADOPTION_COMMAND.set(command)
    try:
        yield
    finally:
        _ADOPTION_COMMAND.reset(token)


class TeachingPrepAITaskAdapter:
    """Translate shared task metadata into A-owned proposals and handoffs."""

    module = "teaching_prep"

    def __init__(self, service: TeachingPrepService) -> None:
        self.service = service
        self.database_path = service.database_path

    def execute(
        self,
        task: StoredTask,
        *,
        model_gateway: WorkspaceAITaskModelGateway,
    ) -> AdapterResult:
        if task.task_kind == "teaching_prep.semester_mapping":
            material_ids = [ref.id for ref in task.context_refs if ref.kind == "material"]
            try:
                proposal, _created = self.service.generate_semester_mapping_proposal(
                    task.source_ref.id,
                    operation_id=task.operation_id,
                    material_record_ids=material_ids,
                    expected_source_state_sha256=task.source_ref.revision,
                    task_model_gateway=model_gateway,
                )
            except TeachingPrepRetryAvailableError as exc:
                raise SemesterMappingRetryAvailableFailure(str(exc)) from exc
            result = self._result(task, proposal.id, str(proposal.revision))
        elif task.task_kind == "teaching_prep.lesson_plan":
            pack_ref = _require_context(task, "resource_pack")
            proposal, _created = self.service.generate_lesson_draft(
                pack_ref.id,
                operation_id=task.operation_id,
                mode="model",
                confirmed=True,
                task_model_gateway=model_gateway,
            )
            result = self._result(task, proposal.id, str(proposal.version_number))
        elif task.task_kind == "teaching_prep.exercise_suggestions":
            snapshot_ref = _require_context(task, "reference_snapshot")
            run = self.service.start_exercise_suggestion_run(
                snapshot_ref.id,
                operation_id=task.operation_id,
                confirmed=True,
            )
            self.service.process_exercise_suggestion_run(
                run.id,
                task_model_gateway=model_gateway,
            )
            completed, _suggestions = self.service.get_exercise_suggestion_run(run.id)
            if completed.status != "succeeded":
                raise RuntimeError("exercise suggestion proposal was not persisted")
            result = self._result(task, completed.id, "1")
        elif task.task_kind == "teaching_prep.slide_change_proposal":
            draft_ref = _require_context(task, "lesson_draft")
            proposal, _created = self.service.create_slide_plan(
                draft_ref.id,
                request_token=task.operation_id,
            )
            result = self._result(task, proposal.id, str(proposal.version_number))
        else:
            raise ValueError("unsupported teaching-prep AI task kind")
        self._save_result(task, result)
        return result

    def recover(self, task: StoredTask) -> AdapterResult | None:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM teaching_prep_ai_task_results WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
        if row is None:
            result = self._recover_domain_result(task)
            if result is None:
                return None
            self._save_result(task, result)
            return result
        values = json.loads(str(row["handoffs_json"]))
        return AdapterResult(
            proposal_ref_id=str(row["proposal_ref_id"]),
            proposal_revision=str(row["proposal_revision"]),
            handoffs=tuple(_handoff_from_dict(item) for item in values),
        )

    def adopt(
        self,
        handoff: HandoffSnapshot,
        *,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        adopted = self.service.adopt_workspace_ai_result(
            source_task_id=handoff.source_task_id,
            handoff_id=handoff.handoff_id,
            adoption_id=adoption_id,
            proposal_ref_id=handoff.draft_ref.id,
            draft_revision=draft_revision,
            target_revision=target_revision,
            command=_ADOPTION_COMMAND.get(),
        )
        return AdoptionResult(
            adopted.adoption_id,
            adopted.object_ref,
            adopted.receipt_revision,
            adopted.target_revision,
        )

    def find_adoption(self, adoption_id: str) -> AdoptionResult | None:
        adopted = self.service.find_workspace_ai_adoption(adoption_id)
        if adopted is None:
            return None
        return AdoptionResult(
            adopted.adoption_id,
            adopted.object_ref,
            adopted.receipt_revision,
            adopted.target_revision,
        )

    def _result(self, task: StoredTask, proposal_id: str, revision: str) -> AdapterResult:
        destination = _DESTINATIONS[task.task_kind]
        return AdapterResult(
            proposal_ref_id=proposal_id,
            proposal_revision=revision,
            handoffs=(
                HandoffDraft(
                    work_item_id=f"{task.task_kind.rsplit('.', 1)[-1]}-review",
                    intent="review",
                    handling_mode="record",
                    destination_key=destination,
                    subject_refs=_subject_refs(task),
                    draft_ref=OpaqueRef("draft", proposal_id, revision),
                    return_destination_key=task.return_target,
                    return_focus_ref=proposal_id,
                ),
            ),
        )

    def _save_result(self, task: StoredTask, result: AdapterResult) -> None:
        handoffs = json.dumps([asdict(item) for item in result.handoffs], ensure_ascii=False)
        context_refs = json.dumps([asdict(item) for item in task.context_refs], ensure_ascii=False)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO teaching_prep_ai_task_results (
                    task_id, operation_id, task_kind, source_ref_id, source_revision,
                    context_refs_json, proposal_ref_id, proposal_revision, handoffs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO NOTHING
                """,
                (
                    task.task_id, task.operation_id, task.task_kind, task.source_ref.id,
                    task.source_ref.revision, context_refs, result.proposal_ref_id,
                    result.proposal_revision, handoffs,
                ),
            )

    def _recover_domain_result(self, task: StoredTask) -> AdapterResult | None:
        queries = {
            "teaching_prep.semester_mapping": (
                "SELECT id, revision FROM semester_mapping_proposals WHERE operation_id = ?",
                task.operation_id,
            ),
            "teaching_prep.lesson_plan": (
                "SELECT id, version_number FROM lesson_draft_versions WHERE operation_id = ?",
                task.operation_id,
            ),
            "teaching_prep.exercise_suggestions": (
                "SELECT id, 1 FROM exercise_suggestion_runs WHERE operation_id = ? AND status = 'succeeded'",
                task.operation_id,
            ),
            "teaching_prep.slide_change_proposal": (
                "SELECT id, version_number FROM slide_plan_versions WHERE request_token = ?",
                task.operation_id,
            ),
        }
        query = queries.get(task.task_kind)
        if query is None:
            return None
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(query[0], (query[1],)).fetchone()
        if row is None:
            return None
        return self._result(task, str(row[0]), str(row[1]))



def _handoff_from_dict(value: dict[str, object]) -> HandoffDraft:
    def ref(item: object) -> OpaqueRef:
        assert isinstance(item, dict)
        return OpaqueRef(str(item["kind"]), str(item["id"]), str(item["revision"]))

    return HandoffDraft(
        work_item_id=str(value["work_item_id"]),
        intent=str(value["intent"]),
        handling_mode=str(value["handling_mode"]),
        destination_key=str(value["destination_key"]),
        subject_refs=tuple(ref(item) for item in value.get("subject_refs", [])),
        draft_ref=ref(value["draft_ref"]),
        prefill_keys=tuple(map(str, value.get("prefill_keys", []))),
        missing_fields=tuple(map(str, value.get("missing_fields", []))),
        source_turn_id=value.get("source_turn_id") or None,  # type: ignore[arg-type]
        return_destination_key=str(value.get("return_destination_key") or ""),
        return_focus_ref=value.get("return_focus_ref") or None,  # type: ignore[arg-type]
        expires_on_source_change=bool(value.get("expires_on_source_change", True)),
    )


def _require_context(task: StoredTask, kind: str) -> OpaqueRef:
    reference = next((item for item in task.context_refs if item.kind == kind), None)
    if reference is None:
        raise ValueError(f"{kind} context is required")
    return reference


def _subject_refs(task: StoredTask) -> tuple[OpaqueRef, ...]:
    refs = [task.source_ref]
    refs.extend(item for item in task.context_refs if item.kind == "semester")
    return tuple({(item.kind, item.id, item.revision): item for item in refs}.values())


def register_ai_tasks(registrar, service: object | None) -> None:
    if not isinstance(service, TeachingPrepService):
        raise TypeError("teaching-prep service is unavailable")
    adapter = TeachingPrepAITaskAdapter(service)
    for task_kind in _DESTINATIONS:
        registrar.register_adapter(task_kind, adapter)


__all__ = [
    "TeachingPrepAITaskAdapter",
    "bind_adoption_command",
    "register_ai_tasks",
]
