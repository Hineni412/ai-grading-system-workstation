from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from backend.workspaces.ai_tasks.model_gateway import WorkspaceAITaskModelGateway
from backend.workspaces.ai_tasks.models import (
    AdapterResult,
    AdoptionResult,
    HandoffDraft,
    HandoffSnapshot,
    InvalidAdapterResultError,
    OpaqueRef,
    RevisionConflictError,
    StoredTask,
)

from ..errors import VaultError
from .conversations import ConversationStore


_TRIAGE_INSTRUCTION = """你是班主任事务整理助手。只返回 JSON 对象，合同版本必须是 class_teacher_triage.v1。把输入分到 student_growth、student_support、conflict_safety、class_operations、activities_culture、school_coordination 六域，并选择 record、plan_calendar、sop 之一。返回 assistant_message、clarification_questions、work_items；每个 work_item 只含合同允许字段。你只能形成草稿，不得自动诊断、认定欺凌、决定惩戒、对外发送或结案。即时危险必须提醒教师先保护学生并联系有权角色。同名学生或无法唯一匹配时 subject_refs 留空并加入待核对项。"""
_AUDIO_TRIAGE_INSTRUCTION = """你是班主任事务整理助手。当前最后一条用户消息包含教师录音。只返回 json 对象，contract_version 必须是 class_teacher_audio_triage.v1。先在 transcript 字段逐字转写教师说话，保留姓名、日期、数字和否定词，不推断录音中没有的内容；再返回与 class_teacher_triage.v1 相同的 assistant_message、clarification_questions、work_items。把事务分到 student_growth、student_support、conflict_safety、class_operations、activities_culture、school_coordination 六域，并选择 record、plan_calendar、sop 之一。你只能形成草稿，不得自动诊断、分析情绪、认定欺凌、决定惩戒、对外发送或结案。即时危险必须提醒教师先保护学生并联系有权角色。同名学生或无法唯一匹配时 subject_refs 留空并加入待核对项。"""
_REVISION_INSTRUCTION = """你只调整现有班主任草稿。只返回 JSON 对象：contract_version 必须是 class_teacher_draft_revision.v1，content 必须是完整的新草稿对象。不得正式保存、外发、诊断、作欺凌认定、决定惩戒或结案。"""


@dataclass(frozen=True, slots=True)
class DomainModelRequest:
    task_kind: str
    prompt_contract_version: str
    messages: tuple[dict[str, str], ...]
    metadata_only: bool = True
    max_send_attempts: int = 1
    automatic_retry: bool = False


class ClassTeacherAITaskAdapter:
    """B Adapter for TW-F1; all full text stays in the B-owned stores/call seam."""

    module = "class_teacher"
    task_kinds = {"class_teacher.intake_triage", "class_teacher.draft_revision"}
    legacy_task_kind = "class_teacher.intake"

    def __init__(
        self,
        conversations: ConversationStore,
        class_roster,
        adoption,
        configured_model,
    ) -> None:
        self.conversations = conversations
        self.class_roster = class_roster
        self.adoption = adoption
        self.configured_model = configured_model

    def execute(
        self,
        task: StoredTask,
        *,
        model_gateway: WorkspaceAITaskModelGateway,
    ) -> AdapterResult:
        task_kind = self._domain_task_kind(task)
        source_ref = _ref_mapping(task.source_ref)
        context_refs = [_ref_mapping(item) for item in task.context_refs]
        request = self.build_model_request(
            task_kind=task_kind,
            source_ref=source_ref,
            context_refs=context_refs,
        )
        if self.configured_model is None:
            raise RuntimeError("class_teacher_model_unavailable")
        raw = self.configured_model.invoke_workspace_task(
            task_gateway=model_gateway,
            messages=request.messages,
            operation_id=task.operation_id,
            purpose="class_teacher_draft_revision" if task_kind.endswith("draft_revision") else "class_teacher_intake",
            expected_destination_fingerprint=task.model_destination_fingerprint,
        )
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(payload, dict):
                raise TypeError("model result is not an object")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._mark_invalid(task, task_kind)
            raise InvalidAdapterResultError("class-teacher model result is invalid") from exc
        try:
            self.persist_model_result(
                task_id=task.task_id,
                source_ref=source_ref,
                context_refs=context_refs,
                result=payload,
            )
        except VaultError as exc:
            if exc.code.endswith("invalid_result"):
                raise InvalidAdapterResultError("class-teacher model result is invalid") from exc
            raise
        recovered = self.recover(task)
        if recovered is None:
            raise RuntimeError("class_teacher_proposal_not_persisted")
        return recovered

    def recover(self, task: StoredTask) -> AdapterResult | None:
        revision_ref = next(
            (item for item in task.context_refs if item.kind == "draft_revision_request"),
            None,
        )
        proposal = self.conversations.proposal_for_task(
            task.task_id,
            draft_revision_request_id=revision_ref.id if revision_ref else None,
        )
        if proposal is None:
            return None
        reference = proposal["proposal_ref"]
        return AdapterResult(
            proposal_ref_id=str(reference["id"]),
            proposal_revision=str(reference["revision"]),
            handoffs=tuple(
                self._handoff(item, expires_on_source_change=revision_ref is None)
                for item in proposal["handoffs"]
            ),
            needs_input=bool(proposal["needs_input"]),
        )

    def adopt(
        self,
        handoff: HandoffSnapshot,
        *,
        adoption_id: str,
        draft_revision: str,
        target_revision: str,
    ) -> AdoptionResult:
        domain = self.conversations.handoff_by_draft_id(handoff.draft_ref.id)
        try:
            receipt = self.adoption.adopt(
                token="",
                handoff_id=str(domain["handoff_id"]),
                draft_revision=int(draft_revision),
                target_revision=target_revision,
                operation_id=adoption_id,
                adoption_id=adoption_id,
            )
        except VaultError as exc:
            persisted = self.find_adoption(adoption_id)
            if persisted is not None:
                return persisted
            self.adoption.release_uncommitted(
                handoff_id=str(domain["handoff_id"]),
                adoption_id=adoption_id,
                target_revision=target_revision,
            )
            raise RevisionConflictError(exc.message) from exc
        return _adoption_result(receipt)

    def find_adoption(self, adoption_id: str) -> AdoptionResult | None:
        receipt = self.adoption.find_receipt(adoption_id)
        return _adoption_result(receipt) if receipt is not None else None

    def build_model_request(
        self,
        *,
        task_kind: str,
        source_ref: Mapping[str, object],
        context_refs: list[Mapping[str, object]],
    ) -> DomainModelRequest:
        if task_kind not in self.task_kinds:
            raise VaultError("class_teacher_task_kind_invalid", "班主任 AI 任务类型无效", status_code=422)
        if task_kind == "class_teacher.draft_revision":
            revision_ref = next((item for item in context_refs if item.get("kind") == "draft_revision_request"), None)
            handoff_ref = next((item for item in context_refs if item.get("kind") == "handoff"), None)
            if revision_ref is None or handoff_ref is None:
                raise VaultError("class_teacher_draft_revision_not_found", "AI 任务缺少草稿调整引用", status_code=422)
            material = self.conversations.draft_revision_material(str(revision_ref.get("id") or ""))
            request = material["request"]
            handoff = material["handoff"]
            if not isinstance(request, Mapping) or not isinstance(handoff, Mapping):
                raise VaultError("class_teacher_draft_revision_not_found", "草稿调整任务不存在", status_code=404)
            if (
                str(request.get("handoff_id") or "") != str(handoff_ref.get("id") or "")
                or str(request.get("source_draft_revision") or "") != str(handoff_ref.get("revision") or "")
                or str(handoff.get("draft_revision") or "") != str(handoff_ref.get("revision") or "")
                or source_ref.get("kind") != "handoff"
                or str(source_ref.get("id") or "") != str(handoff_ref.get("id") or "")
                or str(source_ref.get("revision") or "") != str(handoff_ref.get("revision") or "")
            ):
                raise VaultError("class_teacher_draft_conflict", "草稿调整引用已经变化", status_code=409)
            return DomainModelRequest(
                task_kind=task_kind,
                prompt_contract_version="class_teacher_draft_revision.v1",
                messages=(
                    {"role": "system", "content": _REVISION_INSTRUCTION},
                    {"role": "user", "content": "当前草稿：" + json.dumps(handoff.get("content") or {}, ensure_ascii=False, separators=(",", ":"))},
                    {"role": "user", "content": "调整要求：" + str(request.get("instruction") or "")},
                ),
            )
        if source_ref.get("kind") != "conversation":
            raise VaultError("class_teacher_source_ref_invalid", "班主任 AI 任务来源无效", status_code=422)
        conversation = self.conversations.get(str(source_ref.get("id") or ""))
        if str(conversation["revision"]) != str(source_ref.get("revision") or ""):
            raise VaultError("class_teacher_source_revision_conflict", "会话已变化，请从最新内容重新整理", status_code=409)
        turn_ids = {str(item.get("id") or "") for item in context_refs if item.get("kind") == "turn"}
        messages: list[dict[str, str]] = [{"role": "system", "content": _TRIAGE_INSTRUCTION}]
        candidates = self.class_roster.ai_candidates(
            token="",
            class_label=str(conversation.get("homeroom_class") or "").strip() or None,
        )
        if candidates:
            messages.append({
                "role": "user",
                "content": "当前班学生候选（同名时不得自行选择，无唯一匹配时留空）："
                + json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
            })
        for turn in list(conversation["turns"]):
            if not isinstance(turn, Mapping):
                continue
            messages.append({"role": "user", "content": str(turn.get("teacher_message") or "")})
            if turn.get("assistant_message"):
                messages.append({"role": "assistant", "content": str(turn["assistant_message"])})
        if turn_ids and not any(str(turn.get("turn_id")) in turn_ids for turn in list(conversation["turns"])):
            raise VaultError("class_teacher_turn_not_found", "AI 任务轮次不存在", status_code=404)
        return DomainModelRequest(
            task_kind=task_kind,
            prompt_contract_version="class_teacher_triage.v1",
            messages=tuple(messages),
        )

    def build_audio_model_messages(
        self,
        *,
        conversation_id: str,
        audio_base64: str,
    ) -> tuple[dict[str, object], ...]:
        conversation = self.conversations.get(conversation_id)
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _AUDIO_TRIAGE_INSTRUCTION}
        ]
        candidates = self.class_roster.ai_candidates(
            token="",
            class_label=str(conversation.get("homeroom_class") or "").strip() or None,
        )
        if candidates:
            messages.append(
                {
                    "role": "user",
                    "content": "当前班学生候选（同名时不得自行选择，无唯一匹配时留空）："
                    + json.dumps(
                        candidates,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        for turn in list(conversation["turns"]):
            if not isinstance(turn, Mapping):
                continue
            messages.append(
                {
                    "role": "user",
                    "content": str(turn.get("teacher_message") or ""),
                }
            )
            if turn.get("assistant_message"):
                messages.append(
                    {
                        "role": "assistant",
                        "content": str(turn["assistant_message"]),
                    }
                )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_base64, "format": "wav"},
                    },
                    {
                        "type": "text",
                        "text": "转写这段普通话录音，并按合同整理班主任事务。",
                    },
                ],
            }
        )
        return tuple(messages)

    def normalize_triage_result(
        self,
        *,
        conversation: Mapping[str, object],
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate_items = self.class_roster.ai_candidates(
            token="",
            class_label=str(conversation.get("homeroom_class") or "").strip() or None,
        )
        candidates = {
            (item["id"], item["revision"]): item for item in candidate_items
        }
        name_counts: dict[str, int] = {}
        for item in candidate_items:
            name = item["display_name"]
            name_counts[name] = name_counts.get(name, 0) + 1
        normalized_result = deepcopy(dict(result))
        items = normalized_result.get("work_items")
        for raw_item in items if isinstance(items, list) else []:
            if not isinstance(raw_item, dict):
                continue
            refs = (
                raw_item.get("subject_refs", [])
                if isinstance(raw_item.get("subject_refs"), list)
                else []
            )
            selected: list[dict[str, object]] = []
            requires_teacher_choice = len(refs) > 1
            for ref in refs:
                key = (
                    (
                        str(ref.get("id") or ""),
                        str(ref.get("revision") or ""),
                    )
                    if isinstance(ref, Mapping)
                    else ("", "")
                )
                candidate = candidates.get(key)
                if candidate is None:
                    raise VaultError(
                        "class_teacher_triage_invalid_result",
                        "AI 返回了不在当前候选中的学生引用",
                        status_code=422,
                    )
                if name_counts.get(candidate["display_name"], 0) > 1:
                    requires_teacher_choice = True
                else:
                    selected.append(dict(ref))
            if requires_teacher_choice:
                raw_item["subject_refs"] = []
                missing = (
                    raw_item.get("missing_fields")
                    if isinstance(raw_item.get("missing_fields"), list)
                    else []
                )
                raw_item["missing_fields"] = [*missing, "请选择一名同名学生"]
            else:
                raw_item["subject_refs"] = selected
        return normalized_result

    def persist_model_result(
        self,
        *,
        task_id: str,
        source_ref: Mapping[str, object],
        context_refs: list[Mapping[str, object]],
        result: Mapping[str, Any],
    ) -> dict[str, object]:
        revision_ref = next((item for item in context_refs if item.get("kind") == "draft_revision_request"), None)
        if revision_ref is not None:
            request_id = str(revision_ref.get("id") or "")
            try:
                draft = self.conversations.apply_draft_revision_result(
                    request_id=request_id,
                    task_id=task_id,
                    payload=dict(result),
                )
            except VaultError as exc:
                if exc.code == "class_teacher_draft_revision_invalid_result":
                    self.conversations.mark_draft_revision_outcome(
                        request_id=request_id,
                        task_id=task_id,
                        task_state="invalid_result",
                    )
                raise
            return {
                "proposal_ref": {"kind": "draft", "id": draft["draft_id"], "revision": str(draft["draft_revision"])},
                "handoff_ids": [str(draft["handoff_id"])],
            }
        turn = next((item for item in context_refs if item.get("kind") == "turn"), None)
        if turn is None:
            raise VaultError("class_teacher_turn_not_found", "AI 任务缺少会话轮次", status_code=422)
        turn_id = str(turn.get("id") or "")
        try:
            conversation = self.conversations.get(str(source_ref.get("id") or ""))
            normalized_result = self.normalize_triage_result(
                conversation=conversation,
                result=result,
            )
            conversation = self.conversations.apply_triage_result(
                turn_id=turn_id,
                task_id=task_id,
                payload=normalized_result,
            )
        except VaultError as exc:
            if exc.code == "class_teacher_triage_invalid_result":
                self.conversations.mark_task_outcome(
                    turn_id=turn_id,
                    task_id=task_id,
                    task_state="invalid_result",
                )
            raise
        return {
            "proposal_ref": {"kind": "conversation", "id": conversation["conversation_id"], "revision": str(conversation["revision"])},
            "handoff_ids": [
                str(item["handoff_id"])
                for item in list(conversation["handoffs"])
                if isinstance(item, Mapping) and str(item.get("turn_id")) == turn_id
            ],
        }

    def _domain_task_kind(self, task: StoredTask) -> str:
        if task.task_kind in self.task_kinds:
            return task.task_kind
        if task.task_kind != self.legacy_task_kind:
            raise VaultError(
                "class_teacher_task_kind_invalid",
                "班主任 AI 任务类型无效",
                status_code=422,
            )
        return (
            "class_teacher.draft_revision"
            if any(item.kind == "draft_revision_request" for item in task.context_refs)
            else "class_teacher.intake_triage"
        )

    def _mark_invalid(self, task: StoredTask, task_kind: str) -> None:
        if task_kind == "class_teacher.draft_revision":
            request = next(item for item in task.context_refs if item.kind == "draft_revision_request")
            self.conversations.mark_draft_revision_outcome(
                request_id=request.id,
                task_id=task.task_id,
                task_state="invalid_result",
            )
            return
        turn = next((item for item in task.context_refs if item.kind == "turn"), None)
        if turn is not None:
            self.conversations.mark_task_outcome(
                turn_id=turn.id,
                task_id=task.task_id,
                task_state="invalid_result",
            )

    @staticmethod
    def _handoff(
        item: Mapping[str, object],
        *,
        expires_on_source_change: bool,
    ) -> HandoffDraft:
        missing = item.get("missing_fields") if isinstance(item.get("missing_fields"), list) else []
        refs = item.get("subject_refs") if isinstance(item.get("subject_refs"), list) else []
        return HandoffDraft(
            work_item_id=str(item["work_item_id"]),
            intent=str(item["intent"]),
            handling_mode=str(item["handling_mode"]),
            destination_key=str(item["destination_key"]),
            subject_refs=tuple(_mapping_ref(ref) for ref in refs if isinstance(ref, Mapping)),
            draft_ref=OpaqueRef(kind="draft", id=str(item["draft_id"]), revision=str(item["draft_revision"])),
            missing_fields=tuple(f"missing_{index + 1}" for index, _value in enumerate(missing)),
            source_turn_id=str(item["turn_id"]),
            return_destination_key="class_teacher.home",
            return_focus_ref=str(item["work_item_id"]),
            expires_on_source_change=expires_on_source_change,
        )


def _ref_mapping(value: OpaqueRef) -> dict[str, str]:
    return {"kind": value.kind, "id": value.id, "revision": value.revision}


def _mapping_ref(value: Mapping[str, object]) -> OpaqueRef:
    return OpaqueRef(
        kind=str(value.get("kind") or ""),
        id=str(value.get("id") or ""),
        revision=str(value.get("revision") or ""),
    )


def _adoption_result(receipt: Mapping[str, object]) -> AdoptionResult:
    adoption_id = str(receipt.get("adoption_id") or "")
    object_type = str(receipt.get("formal_object_type") or "object")
    object_id = str(receipt.get("formal_object_id") or "")
    return AdoptionResult(
        adoption_id=adoption_id,
        object_ref=f"class_teacher:{object_type}:{object_id}",
        receipt_revision=str(receipt.get("draft_revision") or "1"),
        target_revision=str(receipt.get("target_revision") or ""),
    )


__all__ = ["ClassTeacherAITaskAdapter", "DomainModelRequest"]
