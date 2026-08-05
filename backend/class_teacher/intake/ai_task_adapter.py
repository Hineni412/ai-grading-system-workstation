from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import VaultError
from .conversations import ConversationStore


@dataclass(frozen=True, slots=True)
class DomainModelRequest:
    task_kind: str
    prompt_contract_version: str
    messages: tuple[dict[str, str], ...]
    metadata_only: bool = True
    max_send_attempts: int = 1
    automatic_retry: bool = False


class ClassTeacherAITaskAdapter:
    """B-owned side of the frozen workspace AI task seam.

    The common task module receives only opaque references.  This adapter reads
    conversation text from the B store at execution time and writes the model
    result back to the B store after contract validation.
    """

    module = "class_teacher"
    task_kinds = {"class_teacher.intake_triage", "class_teacher.draft_revision"}

    def __init__(self, conversations: ConversationStore, class_roster) -> None:
        self.conversations = conversations
        self.class_roster = class_roster

    def build_model_request(
        self,
        *,
        task_kind: str,
        source_ref: Mapping[str, object],
        context_refs: list[Mapping[str, object]],
    ) -> DomainModelRequest:
        if task_kind not in self.task_kinds:
            raise VaultError("class_teacher_task_kind_invalid", "班主任 AI 任务类型无效", status_code=422)
        if source_ref.get("kind") != "conversation":
            raise VaultError("class_teacher_source_ref_invalid", "班主任 AI 任务来源无效", status_code=422)
        conversation = self.conversations.get(str(source_ref.get("id") or ""))
        if str(conversation["revision"]) != str(source_ref.get("revision") or ""):
            raise VaultError("class_teacher_source_revision_conflict", "会话已变化，请从最新内容重新整理", status_code=409)
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
            ):
                raise VaultError("class_teacher_draft_conflict", "草稿调整引用已经变化", status_code=409)
            return DomainModelRequest(
                task_kind=task_kind,
                prompt_contract_version="class_teacher_draft_revision.v1",
                messages=(
                    {"role": "teacher", "content": "当前草稿：" + json.dumps(handoff.get("content") or {}, ensure_ascii=False, separators=(",", ":"))},
                    {"role": "teacher", "content": "调整要求：" + str(request.get("instruction") or "")},
                ),
            )
        turn_ids = {str(item.get("id") or "") for item in context_refs if item.get("kind") == "turn"}
        messages: list[dict[str, str]] = []
        candidates = self.class_roster.ai_candidates(
            token="",
            class_label=str(conversation.get("homeroom_class") or "").strip() or None,
        )
        if candidates:
            messages.append({
                "role": "teacher",
                "content": "当前班学生候选（同名时不得自行选择，无唯一匹配时留空）："
                + json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
            })
        for turn in list(conversation["turns"]):
            if not isinstance(turn, Mapping):
                continue
            messages.append({"role": "teacher", "content": str(turn.get("teacher_message") or "")})
            if turn.get("assistant_message"):
                messages.append({"role": "assistant", "content": str(turn["assistant_message"])})
        if turn_ids and not any(str(turn.get("turn_id")) in turn_ids for turn in list(conversation["turns"])):
            raise VaultError("class_teacher_turn_not_found", "AI 任务轮次不存在", status_code=404)
        return DomainModelRequest(
            task_kind=task_kind,
            prompt_contract_version="class_teacher_triage.v1",
            messages=tuple(messages),
        )

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
            candidate_items = self.class_roster.ai_candidates(
                token="",
                class_label=str(conversation.get("homeroom_class") or "").strip() or None,
            )
            candidates = {(item["id"], item["revision"]): item for item in candidate_items}
            name_counts: dict[str, int] = {}
            for item in candidate_items:
                name = item["display_name"]
                name_counts[name] = name_counts.get(name, 0) + 1
            normalized_result = deepcopy(dict(result))
            for raw_item in normalized_result.get("work_items", []) if isinstance(normalized_result.get("work_items"), list) else []:
                if not isinstance(raw_item, Mapping):
                    continue
                refs = raw_item.get("subject_refs", []) if isinstance(raw_item.get("subject_refs"), list) else []
                selected: list[dict[str, object]] = []
                requires_teacher_choice = len(refs) > 1
                for ref in refs:
                    key = (str(ref.get("id") or ""), str(ref.get("revision") or "")) if isinstance(ref, Mapping) else ("", "")
                    candidate = candidates.get(key)
                    if candidate is None:
                        raise VaultError("class_teacher_triage_invalid_result", "AI 返回了不在当前候选中的学生引用", status_code=422)
                    if name_counts.get(candidate["display_name"], 0) > 1:
                        requires_teacher_choice = True
                    else:
                        selected.append(dict(ref))
                if requires_teacher_choice:
                    raw_item["subject_refs"] = []
                    missing = raw_item.get("missing_fields") if isinstance(raw_item.get("missing_fields"), list) else []
                    raw_item["missing_fields"] = [*missing, "请选择一名同名学生"]
                else:
                    raw_item["subject_refs"] = selected
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
            "handoff_ids": [str(item["handoff_id"]) for item in list(conversation["handoffs"]) if isinstance(item, Mapping)],
        }


__all__ = ["ClassTeacherAITaskAdapter", "DomainModelRequest"]
