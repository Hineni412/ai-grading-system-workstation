from __future__ import annotations

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
        sop,
        sop_baselines,
        ai_tasks: WorkspaceAITaskPort | None = None,
    ) -> None:
        self.preferences = HomeroomPreference(ordinary_database, class_roster.source)
        self.conversations = ConversationStore(
            ordinary_database,
            ai_tasks or UnavailableWorkspaceAITaskPort(),
        )
        self.ai_task_adapter = ClassTeacherAITaskAdapter(self.conversations, class_roster)
        self.adoption = HandoffAdoption(
            self.conversations,
            domain_database,
            repository,
            key_provider,
            support,
            planning,
            sop,
            sop_baselines,
            class_roster,
        )

    def start_conversation(self) -> dict[str, object]:
        preference = self.preferences.get()
        return self.conversations.start(homeroom_class=preference.get("homeroom_class"))

    def append_turn(self, **kwargs) -> dict[str, object]:
        return self.conversations.append_turn(**kwargs)

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
        return self.adoption.adopt(**kwargs)


__all__ = ["ClassTeacherIntake"]
