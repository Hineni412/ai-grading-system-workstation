from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping

from backend.jobs.manager import JobManager

from .models import (
    AdoptionResult,
    HandoffSnapshot,
    InvalidAdapterResultError,
    KnownAdapterFailure,
    OpaqueRef,
    PrepareRequest,
    RevisionConflictError,
    StoredTask,
    TaskNotFoundError,
    TaskSnapshot,
)
from .ports import WorkspaceAITaskAdapter
from .model_gateway import WorkspaceAITaskModelGateway
from .public_projection import project_task
from .registry import assert_destination, is_recovery_only_task, presentation
from .store import WorkspaceAITaskStore


_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOGGER = logging.getLogger(__name__)


class WorkspaceAITaskService:
    def __init__(
        self,
        *,
        store: WorkspaceAITaskStore,
        manager: JobManager,
        adapters: Iterable[tuple[str, WorkspaceAITaskAdapter]] = (),
        model_gateway: WorkspaceAITaskModelGateway | None = None,
        recover_interrupted: bool = False,
    ) -> None:
        self.store = store
        self.manager = manager
        self._adapters: dict[str, WorkspaceAITaskAdapter] = {}
        self.model_gateway = model_gateway or WorkspaceAITaskModelGateway()
        for task_kind, adapter in adapters:
            self.register_adapter(task_kind, adapter)
        if recover_interrupted:
            self.recover_interrupted()

    def register_adapter(
        self,
        task_kind: str,
        adapter: WorkspaceAITaskAdapter,
    ) -> None:
        presentation(adapter.module, task_kind)
        if task_kind in self._adapters:
            raise ValueError("workspace AI adapter is already registered")
        self._adapters[task_kind] = adapter

    def prepare(self, operation_id: str, request: PrepareRequest) -> TaskSnapshot:
        self._validate_prepare(operation_id, request)
        if is_recovery_only_task(request.module, request.task_kind):
            raise ValueError("workspace AI task kind is recovery-only")
        request_fingerprint = _fingerprint(
            {
                "module": request.module,
                "task_kind": request.task_kind,
                "source_ref": _ref_dict(request.source_ref),
                "context_refs": [_ref_dict(item) for item in request.context_refs],
                "prompt_contract_version": request.prompt_contract_version,
                "model_destination_fingerprint": request.model_destination_fingerprint,
                "return_target": request.return_target,
            }
        )
        task = self.store.prepare(
            operation_id=operation_id,
            module=request.module,
            task_kind=request.task_kind,
            source_ref=request.source_ref,
            context_refs=request.context_refs,
            prompt_contract_version=request.prompt_contract_version,
            request_fingerprint=request_fingerprint,
            model_destination_fingerprint=request.model_destination_fingerprint,
            return_target=request.return_target,
        )
        return project_task(self.store, task)

    def dispatch(
        self,
        operation_id: str,
        *,
        prepared_task_id: str,
        request_fingerprint: str | None = None,
    ) -> TaskSnapshot:
        task = self.store.find_by_operation(operation_id)
        if task is None or task.task_id != prepared_task_id:
            raise TaskNotFoundError("prepared workspace AI task was not found")
        if is_recovery_only_task(task.module, task.task_kind):
            raise ValueError("workspace AI task kind is recovery-only")
        if request_fingerprint and task.request_fingerprint != request_fingerprint:
            from .models import OperationConflictError

            raise OperationConflictError("prepared task fingerprint changed")
        task, created = self.store.create_dispatch_job(task.task_id)
        if created and task.job_id is not None:
            try:
                self.manager.start_existing(task.job_id)
            except Exception:
                self.store.fail(
                    task.task_id,
                    status="failed_before_dispatch",
                    error_code="job_scheduling_failed",
                )
        return self.get(task_id=task.task_id)

    def get(
        self,
        *,
        task_id: str | None = None,
        operation_id: str | None = None,
    ) -> TaskSnapshot:
        if task_id:
            task = self.store.require_task(task_id)
        elif operation_id:
            task = self.store.find_by_operation(operation_id)
            if task is None:
                raise TaskNotFoundError("workspace AI task was not found")
        else:
            raise ValueError("task_id or operation_id is required")
        return project_task(self.store, task)

    def list_module_tasks(self, module: str) -> tuple[TaskSnapshot, ...]:
        clean_module = str(module or "").strip()
        if not clean_module:
            raise ValueError("workspace AI task module is required")
        return tuple(
            project_task(self.store, task)
            for task in self.store.list_tasks(clean_module)
        )

    def list_actionable_module_tasks(self, module: str) -> tuple[TaskSnapshot, ...]:
        clean_module = str(module or "").strip()
        if not clean_module:
            raise ValueError("workspace AI task module is required")
        return tuple(
            project_task(self.store, task)
            for task in self.store.list_actionable_tasks(clean_module)
        )

    def cancel(self, operation_id: str) -> TaskSnapshot:
        task = self.store.find_by_operation(operation_id)
        if task is None:
            raise TaskNotFoundError("workspace AI task was not found")
        next_task = self.store.request_cancel(task.task_id)
        if task.job_id is not None and task.status in {"queued", "running"}:
            self.manager.cancel(task.job_id)
        return project_task(self.store, next_task)

    def discard_result_unknown(self, operation_id: str) -> TaskSnapshot:
        task = self.store.find_by_operation(operation_id)
        if task is None:
            raise TaskNotFoundError("workspace AI task was not found")
        if task.status == "result_unknown":
            adapter = self._adapters.get(task.task_kind)
            discard_domain_result = getattr(
                adapter,
                "discard_result_unknown",
                None,
            )
            if callable(discard_domain_result):
                # Clear the domain-owned indeterminate guard first. If that
                # fails, keep the shared task blocked instead of presenting a
                # false "discarded" state that still cannot be retried.
                discard_domain_result(task)
        return project_task(
            self.store,
            self.store.discard_result_unknown(task.task_id),
        )

    def run_task(self, task_id: str) -> dict[str, object]:
        task = self.store.claim(task_id)
        if task.status == "cancelled_before_dispatch":
            return {"task_id": task_id, "status": task.status}
        adapter = self._adapters.get(task.task_kind)
        if adapter is None or adapter.module != task.module:
            failed = self.store.fail(
                task_id,
                status="failed_before_dispatch",
                error_code="adapter_unavailable",
            )
            return {"task_id": task_id, "status": failed.status}
        if is_recovery_only_task(task.module, task.task_kind):
            return self._recover_task_without_send(task, adapter)
        task = self.store.reserve_send_attempt(task_id)
        if task.status == "cancelled_before_dispatch":
            return {"task_id": task_id, "status": task.status}
        result = None
        try:
            result = adapter.execute(task, model_gateway=self.model_gateway)
            self.store.mark_validating(task_id)
            self._validate_adapter_result(task, result)
            completed = self.store.complete(
                task_id,
                proposal_ref_id=result.proposal_ref_id,
                proposal_revision=result.proposal_revision,
                handoffs=result.handoffs,
                needs_input=result.needs_input,
            )
        except InvalidAdapterResultError as exc:
            completed = self.store.fail(
                task_id,
                status="invalid_result",
                error_code=exc.code,
                response_persisted=True,
            )
        except KnownAdapterFailure as exc:
            completed = self.store.fail(
                task_id,
                status="failed",
                error_code=exc.code,
                response_persisted=True,
            )
        except Exception:
            LOGGER.exception("Workspace AI adapter failed for task %s", task_id)
            if result is not None:
                # The domain proposal exists. Leave it recoverable; never resend.
                return {"task_id": task_id, "status": "running"}
            completed = self.store.fail(
                task_id,
                status="result_unknown",
                error_code="adapter_result_unknown",
            )
        return {"task_id": task_id, "status": completed.status}

    def recover_interrupted(self) -> tuple[str, ...]:
        recovered: list[str] = []
        for task in self.store.interrupted_tasks():
            if task.send_attempt_count == 0 and task.dispatch_evidence == "not_started":
                self.store.fail(
                    task.task_id,
                    status="failed_before_dispatch",
                    error_code="interrupted_before_dispatch",
                )
                recovered.append(task.task_id)
                continue
            adapter = self._adapters.get(task.task_kind)
            result = adapter.recover(task) if adapter is not None else None
            if result is None:
                self.store.fail(
                    task.task_id,
                    status="result_unknown",
                    error_code="interrupted_after_dispatch",
                )
            else:
                self._validate_adapter_result(task, result)
                self.store.mark_validating(task.task_id)
                self.store.complete(
                    task.task_id,
                    proposal_ref_id=result.proposal_ref_id,
                    proposal_revision=result.proposal_revision,
                    handoffs=result.handoffs,
                    needs_input=result.needs_input,
                )
            recovered.append(task.task_id)
        return tuple(recovered)

    def _recover_task_without_send(
        self,
        task: StoredTask,
        adapter: WorkspaceAITaskAdapter,
    ) -> dict[str, object]:
        """Finish legacy persisted work from its domain receipt without a model send."""

        try:
            result = adapter.recover(task)
        except Exception:
            result = None
        if result is None:
            failed = self.store.fail(
                task.task_id,
                status="result_unknown",
                error_code="legacy_task_recovery_unavailable",
            )
            return {"task_id": task.task_id, "status": failed.status}
        self._validate_adapter_result(task, result)
        self.store.mark_validating(task.task_id)
        completed = self.store.complete(
            task.task_id,
            proposal_ref_id=result.proposal_ref_id,
            proposal_revision=result.proposal_revision,
            handoffs=result.handoffs,
            needs_input=result.needs_input,
        )
        return {"task_id": task.task_id, "status": completed.status}

    def adopt(
        self,
        handoff_id: str,
        *,
        module: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        handoff = self.store.require_handoff(handoff_id)
        if handoff.module != module:
            raise ValueError("handoff module does not match domain endpoint")
        task = self.store.require_task(handoff.source_task_id)
        adapter = self._adapters.get(task.task_kind)
        if adapter is None or adapter.module != module:
            raise ValueError("workspace AI adapter is unavailable")
        started = self.store.begin_adoption(
            handoff_id,
            draft_revision=draft_revision,
            target_revision=target_revision,
        )
        if started.adoption_id is None:
            raise RuntimeError("adoption claim was not persisted")
        existing = adapter.find_adoption(started.adoption_id)
        if existing is not None:
            result = existing
        else:
            try:
                result = adapter.adopt(
                    started,
                    adoption_id=started.adoption_id,
                    draft_revision=draft_revision,
                    target_revision=target_revision,
                )
            except RevisionConflictError:
                persisted = adapter.find_adoption(started.adoption_id)
                if persisted is None:
                    self.store.release_uncommitted_adoption(
                        handoff_id,
                        adoption_id=started.adoption_id,
                        target_revision=target_revision,
                    )
                    raise
                result = persisted
        if (
            result.adoption_id != started.adoption_id
            or result.target_revision != target_revision
        ):
            raise RevisionConflictError("adoption receipt does not match the claim")
        self.store.finish_adoption(handoff_id, object_ref=result.object_ref)
        return result

    def rebind_handoff_draft(
        self,
        handoff_id: str,
        *,
        module: str,
        expected_draft_revision: str,
        draft_revision: str,
        subject_refs: tuple[OpaqueRef, ...],
    ) -> HandoffSnapshot:
        handoff = self.store.require_handoff(handoff_id)
        if handoff.module != module:
            raise ValueError("handoff module does not match domain endpoint")
        if not _SAFE_REF.fullmatch(draft_revision):
            raise ValueError("workspace AI draft revision is invalid")
        for reference in subject_refs:
            if not all(
                _SAFE_REF.fullmatch(value)
                for value in (reference.kind, reference.id, reference.revision)
            ):
                raise ValueError("workspace AI subject reference is invalid")
        return self.store.rebind_handoff_draft(
            handoff_id,
            module=module,
            expected_draft_revision=expected_draft_revision,
            draft_revision=draft_revision,
            subject_refs=subject_refs,
        )

    def mark_handoff(self, handoff_id: str, state: str) -> HandoffSnapshot:
        return self.store.set_handoff_state(handoff_id, state)

    @staticmethod
    def _validate_prepare(operation_id: str, request: PrepareRequest) -> None:
        if not _OPERATION_ID.fullmatch(operation_id):
            raise ValueError("operation_id is invalid")
        presentation(request.module, request.task_kind)
        assert_destination(request.module, request.return_target)
        if not _SHA256.fullmatch(request.model_destination_fingerprint):
            raise ValueError("model destination fingerprint is invalid")
        if not _SAFE_REF.fullmatch(request.prompt_contract_version):
            raise ValueError("prompt contract version is invalid")
        for reference in (request.source_ref, *request.context_refs):
            if not all(
                _SAFE_REF.fullmatch(value)
                for value in (reference.kind, reference.id, reference.revision)
            ):
                raise ValueError("workspace AI reference is invalid")

    @staticmethod
    def _validate_adapter_result(task: StoredTask, result: object) -> None:
        from .models import AdapterResult

        if not isinstance(result, AdapterResult):
            raise InvalidAdapterResultError("adapter result type is invalid")
        if not _SAFE_REF.fullmatch(result.proposal_ref_id) or not _SAFE_REF.fullmatch(
            result.proposal_revision
        ):
            raise InvalidAdapterResultError("proposal reference is invalid")
        seen: set[str] = set()
        for handoff in result.handoffs:
            if handoff.work_item_id in seen:
                raise InvalidAdapterResultError("work item is repeated")
            seen.add(handoff.work_item_id)
            scalar_refs = [
                handoff.work_item_id,
                handoff.draft_ref.kind,
                handoff.draft_ref.id,
                handoff.draft_ref.revision,
            ]
            if handoff.source_turn_id:
                scalar_refs.append(handoff.source_turn_id)
            if handoff.return_focus_ref:
                scalar_refs.append(handoff.return_focus_ref)
            scalar_refs.extend(
                value
                for reference in handoff.subject_refs
                for value in (reference.kind, reference.id, reference.revision)
            )
            if not all(_SAFE_REF.fullmatch(value) for value in scalar_refs):
                raise InvalidAdapterResultError("handoff reference is invalid")
            field_token = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
            if not all(
                field_token.fullmatch(value)
                for value in (*handoff.prefill_keys, *handoff.missing_fields)
            ):
                raise InvalidAdapterResultError("handoff field key is invalid")
            try:
                assert_destination(task.module, handoff.destination_key)
            except ValueError as exc:
                raise InvalidAdapterResultError(str(exc)) from exc
            if handoff.return_destination_key:
                try:
                    assert_destination(task.module, handoff.return_destination_key)
                except ValueError as exc:
                    raise InvalidAdapterResultError(str(exc)) from exc
            if handoff.intent not in {"create", "append", "follow_up", "plan", "review"}:
                raise InvalidAdapterResultError("handoff intent is invalid")
            if handoff.handling_mode not in {"record", "plan_calendar", "sop"}:
                raise InvalidAdapterResultError("handoff handling mode is invalid")


def _fingerprint(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ref_dict(value: OpaqueRef) -> dict[str, str]:
    return {"kind": value.kind, "id": value.id, "revision": value.revision}


__all__ = ["WorkspaceAITaskService"]
