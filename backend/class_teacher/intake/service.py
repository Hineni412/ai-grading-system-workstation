from __future__ import annotations

import base64
import json
import threading
from contextlib import closing

from ..errors import VaultError
from ..model_approval import ModelDestinationChanged, ModelDispatchDisabled

from .adoption import HandoffAdoption
from .ai_task_adapter import ClassTeacherAITaskAdapter
from .conversations import ConversationStore
from .ports import UnavailableWorkspaceAITaskPort, WorkspaceAITaskPort
from .preferences import HomeroomPreference


class ClassTeacherIntake:
    def __init__(
        self,
        *,
        ordinary_database,
        class_roster,
        domain_database,
        repository,
        key_provider,
        support,
        work,
        sop,
        sop_baselines,
        student_cards,
        model_gateway,
        ai_tasks: WorkspaceAITaskPort | None = None,
    ) -> None:
        task_port = ai_tasks or UnavailableWorkspaceAITaskPort()
        self.preferences = HomeroomPreference(ordinary_database, class_roster.source)
        self.conversations = ConversationStore(
            ordinary_database,
            task_port,
        )
        self.adoption = HandoffAdoption(
            self.conversations,
            domain_database,
            repository,
            key_provider,
            support,
            work,
            sop,
            sop_baselines,
            class_roster,
            student_cards,
        )
        bind_adoption = getattr(task_port, "bind_adoption", None)
        if callable(bind_adoption):
            bind_adoption(self.adoption.adopt)
        self.sop = sop
        self.conversations.auto_adopt_sop = self._auto_adopt_sop_handoffs
        self.conversations.sop_followup = self._start_sop_followup
        self.ai_task_adapter = ClassTeacherAITaskAdapter(
            self.conversations,
            class_roster,
            self.adoption,
            model_gateway,
            student_cards,
            sop=sop,
        )
        self.model_gateway = model_gateway
        self._cloud_audio_lock = threading.Lock()

    def bind_ai_tasks(self, ai_tasks: WorkspaceAITaskPort) -> None:
        self.conversations.ai_tasks = ai_tasks

    def start_conversation(
        self,
        *,
        token: str = "",
        subject_id: str | None = None,
    ) -> dict[str, object]:
        preference = self.preferences.get()
        support = self.ai_task_adapter.student_cards.support
        # 入参为对外学生编号（稳定学籍标识「班级|学号」），兼容旧 uuid 档案编号；
        # 统一经 student_subject_links 解析为内部编号后走既有逻辑。
        internal_id: str | None = None
        if str(subject_id or "").strip():
            text = str(subject_id).strip()
            internal_id = support.subject_id_for_ref(student_ref=text) or text
        subject = (
            None
            if internal_id is None
            else support.get_subject(
                token=token,
                subject_id=internal_id,
            )
        )
        return self.conversations.start(
            homeroom_class=preference.get("homeroom_class"),
            focused_subject_id=None if subject is None else str(subject["subject_id"]),
            focused_subject_revision=None if subject is None else str(subject["revision"]),
        )

    def append_turn(self, **kwargs) -> dict[str, object]:
        with self._cloud_audio_lock:
            return self.conversations.append_turn(**kwargs)

    def cloud_audio_capabilities(self) -> dict[str, object]:
        if self.model_gateway is None:
            return {
                "available": False,
                "status": "profile_missing",
                "provider": "configured_model",
                "model": None,
                "destination_fingerprint": "",
            }
        return dict(self.model_gateway.audio_input_capabilities())

    def append_cloud_audio_turn(
        self,
        *,
        conversation_id: str,
        expected_revision: int,
        operation_id: str,
        expected_destination_fingerprint: str,
        wav_content: bytes,
    ) -> dict[str, object]:
        # The desktop service is single-process. Holding this lock through the one
        # provider call prevents two browser tabs from paying for the same
        # conversation revision before either result can be persisted.
        with self._cloud_audio_lock:
            return self._append_cloud_audio_turn_locked(
                conversation_id=conversation_id,
                expected_revision=expected_revision,
                operation_id=operation_id,
                expected_destination_fingerprint=expected_destination_fingerprint,
                wav_content=wav_content,
            )

    def _append_cloud_audio_turn_locked(
        self,
        *,
        conversation_id: str,
        expected_revision: int,
        operation_id: str,
        expected_destination_fingerprint: str,
        wav_content: bytes,
    ) -> dict[str, object]:
        conversation = self.conversations.get(conversation_id)
        if int(conversation["revision"]) != int(expected_revision):
            raise VaultError(
                "class_teacher_conversation_conflict",
                "会话已经变化；录音尚未发送",
                status_code=409,
            )
        if str(conversation["state"]) == "ai_running":
            raise VaultError(
                "class_teacher_turn_in_progress",
                "上一轮仍在整理；录音尚未发送",
                status_code=409,
            )
        capability = self.cloud_audio_capabilities()
        if not capability.get("available"):
            raise VaultError(
                "class_teacher_cloud_audio_unavailable",
                "当前班主任模型不支持直接接收语音",
                status_code=422,
            )
        if (
            not expected_destination_fingerprint
            or capability.get("destination_fingerprint")
            != expected_destination_fingerprint
        ):
            raise VaultError(
                "class_teacher_model_destination_changed",
                "模型配置已经变化；录音尚未发送",
                status_code=409,
            )
        messages = self.ai_task_adapter.build_audio_model_messages(
            conversation_id=conversation_id,
            audio_base64=base64.b64encode(wav_content).decode("ascii"),
        )
        try:
            raw = self.model_gateway.invoke_workspace_audio(
                messages=messages,
                operation_id=operation_id,
                expected_destination_fingerprint=expected_destination_fingerprint,
            )
        except ModelDestinationChanged as exc:
            raise VaultError(
                "class_teacher_model_destination_changed",
                "模型配置已经变化；录音尚未发送",
                status_code=409,
            ) from exc
        except ModelDispatchDisabled as exc:
            raise VaultError(
                "class_teacher_cloud_audio_unavailable",
                "当前班主任模型不支持直接接收语音",
                status_code=422,
            ) from exc
        except Exception as exc:
            physical_count = self.model_gateway.physical_request_count(operation_id)
            if physical_count:
                raise VaultError(
                    "class_teacher_cloud_audio_result_unknown",
                    "语音请求可能已经发出，但没有可靠结果；系统不会自动重发",
                    status_code=502,
                ) from exc
            raise VaultError(
                "class_teacher_cloud_audio_failed_before_dispatch",
                "语音请求尚未发出，可以改用本机转文字",
                status_code=503,
            ) from exc
        try:
            result = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(result, dict):
                raise TypeError("audio result is not an object")
            if result.get("contract_version") != "class_teacher_audio_triage.v1":
                raise ValueError("audio contract version is invalid")
            transcript = str(result.pop("transcript", "")).strip()
            result["contract_version"] = "class_teacher_triage.v1"
            normalized = self.ai_task_adapter.normalize_triage_result(
                conversation=conversation,
                result=result,
            )
        except (TypeError, ValueError, json.JSONDecodeError, VaultError) as exc:
            raise VaultError(
                "class_teacher_cloud_audio_invalid_result",
                "模型没有返回可用的语音文字和事务草稿；可以改用本机转文字",
                status_code=422,
            ) from exc
        return self.conversations.append_resolved_audio_turn(
            conversation_id=conversation_id,
            expected_revision=expected_revision,
            transcript=transcript,
            operation_id=operation_id,
            payload=normalized,
        )

    def get_conversation(self, conversation_id: str) -> dict[str, object]:
        return self.conversations.get(conversation_id)

    def start_affair_flow_revision(
        self,
        *,
        affair_id: str,
        affair_revision: int,
        sync_id: str,
        operation_id: str,
        turn_id: str | None = None,
    ) -> dict[str, object]:
        """为已建立事务的“同步新情况”发起一次流程修订 AI 任务（最多发送一次）。"""
        request = {
            "module": "class_teacher",
            "task_kind": "class_teacher.affair_flow_revision",
            "source_ref": {"kind": "affair", "id": affair_id, "revision": str(affair_revision)},
            "context_refs": [
                {"kind": "affair_sync", "id": sync_id, "revision": "1"},
                *(
                    [{"kind": "turn", "id": turn_id, "revision": "1"}]
                    if turn_id
                    else []
                ),
            ],
            "prompt_contract_version": "class_teacher_affair_flow_revision.v1",
            "model_destination_fingerprint": "configured-workspace-model",
            "return_target": "class_teacher.affair.sop",
        }
        prepared = self.conversations.ai_tasks.prepare(
            operation_id=operation_id,
            request=request,
        )
        snapshot = self.conversations.ai_tasks.dispatch(
            operation_id=operation_id,
            prepared_task_id=prepared.task_id,
            request_fingerprint=prepared.request_fingerprint,
        )
        return {
            "sync_id": sync_id,
            "task_id": snapshot.task_id,
            "task_state": snapshot.state,
        }

    def _auto_adopt_sop_handoffs(self, *, conversation_id: str, turn_id: str) -> None:
        """SOP 生成即生效：本轮可建单的 SOP 交接直接建成正式事务。

        模板缺失、参与人未确定或建单失败时保留待确认草稿页行为。
        采用收据幂等：已建单的交接不会重复建单。
        """
        conversation = self.conversations.get(conversation_id)
        for summary in list(conversation.get("handoffs") or []):
            if str(summary.get("turn_id") or "") != turn_id:
                continue
            if str(summary.get("destination_key") or "") != "class_teacher.affair.sop":
                continue
            if str(summary.get("adoption_state") or "") not in {"pending", "opened"}:
                continue
            handoff = self.conversations.handoff_for_adapter(str(summary["handoff_id"]))
            content = handoff.get("content")
            if not isinstance(content, dict):
                continue
            if not str(content.get("template_key") or "").strip():
                continue
            participant_refs = [
                str(item).strip()
                for item in list(content.get("participant_refs") or [])
                if str(item).strip()
            ]
            if not list(handoff.get("subject_refs") or []) and not participant_refs:
                continue
            handoff_id = str(handoff["handoff_id"])
            adoption_id = str(handoff["adoption_id"])
            try:
                self.adoption.adopt(
                    token="",
                    handoff_id=handoff_id,
                    draft_revision=int(handoff["draft_revision"]),
                    target_revision="auto",
                    operation_id=f"auto-adopt-{handoff_id}",
                )
            except Exception:
                try:
                    if self.adoption.find_receipt(adoption_id) is None:
                        self.adoption.release_uncommitted(
                            handoff_id=handoff_id,
                            adoption_id=adoption_id,
                            target_revision="auto",
                        )
                except Exception:
                    pass

    def _start_sop_followup(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        message: str,
        operation_id: str,
    ) -> dict[str, object] | None:
        """已建单会话的补充轮：同步到既有事务并发起流程修订。

        返回 None 表示本轮不改道（无生效事务或事务已结束），
        走常规分诊；返回字典时由调用方把修订任务绑定到会话轮次。
        """
        affair_id = self.conversations.adopted_sop_affair_id(conversation_id)
        if affair_id is None:
            return None
        try:
            with closing(self.sop.database.connect()) as connection:
                expected_revision = self.sop._workspace_revision(connection, affair_id)
            created = self.sop.create_sync_request(
                token="",
                affair_id=affair_id,
                expected_revision=expected_revision,
                text=message[:2000],
                operation_id=operation_id,
            )
        except VaultError:
            return None
        except Exception:
            return {"task_id": "", "task_state": "failed_before_dispatch"}
        try:
            return self.start_affair_flow_revision(
                affair_id=affair_id,
                affair_revision=int(created["affair_revision"]),
                sync_id=str(created["sync_id"]),
                operation_id=operation_id,
                turn_id=turn_id,
            )
        except Exception:
            try:
                self.sop.mark_sync_request_failed(
                    token="",
                    affair_id=affair_id,
                    sync_id=str(created["sync_id"]),
                    outcome="failed",
                )
            except Exception:
                pass
            return {"task_id": "", "task_state": "failed_before_dispatch"}

    def list_conversations(self, *, limit: int = 5) -> dict[str, object]:
        return self.conversations.list_recent(limit=limit)

    def pending_student_handoffs(self, student_ref: str) -> dict[str, object]:
        """对外学生编号统一为稳定学籍标识「班级|学号」；兼容输入旧 uuid 档案编号。
        对话交接 ref 本身就是稳定标识，直接相等即可命中；
        student_subject_links 只用于兼容历史 uuid 格式的交接与入参。"""
        support = self.ai_task_adapter.student_cards.support
        text = str(student_ref or "").strip()
        internal_id = support.subject_id_for_ref(student_ref=text)
        if internal_id is not None:
            accepted = {text, internal_id}
            canonical_ref = text
        else:
            fingerprints = support.subject_fingerprints(subject_id=text)
            accepted = {text, *fingerprints}
            canonical_ref = fingerprints[0] if fingerprints else text
        result = self.conversations.pending_student_handoffs(
            internal_id or text,
            accepted_ref_ids=accepted,
        )
        for item in result["items"]:
            item["student_ref"] = canonical_ref
        return result

    def delete_conversation(self, conversation_id: str) -> dict[str, object]:
        return self.conversations.delete(conversation_id)

    def apply_triage_result(self, **kwargs) -> dict[str, object]:
        return self.conversations.apply_triage_result(**kwargs)

    def mark_task_outcome(self, **kwargs) -> dict[str, object]:
        return self.conversations.mark_task_outcome(**kwargs)

    def manual_route(self, **kwargs) -> dict[str, object]:
        return self.conversations.manual_route(**kwargs)

    def open_handoff(self, handoff_id: str) -> dict[str, object]:
        return self.conversations.open_handoff(handoff_id)

    def update_draft(self, **kwargs) -> dict[str, object]:
        return self.conversations.update_draft(**kwargs)

    def request_draft_revision(self, **kwargs) -> dict[str, object]:
        return self.conversations.request_draft_revision(**kwargs)

    def get_draft_revision(self, request_id: str) -> dict[str, object]:
        return self.conversations.get_draft_revision(request_id)

    def discard_handoff(self, handoff_id: str) -> dict[str, object]:
        return self.conversations.discard_handoff(handoff_id)

    def adopt_handoff(self, **kwargs) -> dict[str, object]:
        kwargs.pop("token", None)
        kwargs.pop("operation_id", None)
        return self.conversations.ai_tasks.adopt(**kwargs)

    def revert_profile_adoption(self, **kwargs) -> dict[str, object]:
        kwargs.pop("token", None)
        return self.adoption.revert_profile_adoption(token="", **kwargs)


__all__ = ["ClassTeacherIntake"]
