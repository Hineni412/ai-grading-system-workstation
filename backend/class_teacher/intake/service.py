from __future__ import annotations

import base64
import json
import threading

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
        planning,
        work,
        sop,
        sop_baselines,
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
            planning,
            work,
            sop,
            sop_baselines,
            class_roster,
        )
        bind_adoption = getattr(task_port, "bind_adoption", None)
        if callable(bind_adoption):
            bind_adoption(self.adoption.adopt)
        self.ai_task_adapter = ClassTeacherAITaskAdapter(
            self.conversations,
            class_roster,
            self.adoption,
            model_gateway,
        )
        self.model_gateway = model_gateway
        self._cloud_audio_lock = threading.Lock()

    def bind_ai_tasks(self, ai_tasks: WorkspaceAITaskPort) -> None:
        self.conversations.ai_tasks = ai_tasks

    def start_conversation(self) -> dict[str, object]:
        preference = self.preferences.get()
        return self.conversations.start(homeroom_class=preference.get("homeroom_class"))

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

    def list_conversations(self, *, limit: int = 12) -> dict[str, object]:
        return self.conversations.list_recent(limit=limit)

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


__all__ = ["ClassTeacherIntake"]
