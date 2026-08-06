from __future__ import annotations

import json
import sqlite3
import wave
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake.ports import FakeWorkspaceAITaskPort
from backend.class_teacher.intake.triage_contract import parse_triage
from backend.class_teacher.local_speech import LocalSpeechTranscriber
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> tuple[VaultService, FakeWorkspaceAITaskPort]:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [(1, "A001", "合成学生甲", "一班"), (2, "B001", "合成学生乙", "二班")],
        )
        connection.commit()
    port = FakeWorkspaceAITaskPort()
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
            db_path=grading,
        ),
    )
    return VaultService(context, protection_enabled=False, workspace_ai_task_port=port), port


def _client(service: VaultService) -> TestClient:
    app = FastAPI()
    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code}})
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app)


def _synthetic_wav() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 4_000)
    return output.getvalue()


class _SpeechStream:
    def __init__(self) -> None:
        self.result = SimpleNamespace(text="合成本地语音转写。")

    def accept_waveform(self, _sample_rate, _samples) -> None:
        return None


class _SpeechRecognizer:
    def create_stream(self):
        return _SpeechStream()

    def decode_stream(self, _stream) -> None:
        return None


def test_local_speech_api_returns_text_without_creating_a_conversation(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    service.local_speech = LocalSpeechTranscriber(
        PROJECT_ROOT,
        recognizer_factory=lambda _model_dir: _SpeechRecognizer(),
    )
    client = _client(service)
    trusted = {"x-class-teacher-client": "class-teacher-browser-v1"}

    capabilities = client.get("/api/class-teacher/intake/speech/capabilities")
    transcription = client.post(
        "/api/class-teacher/intake/speech/transcriptions",
        headers={**trusted, "content-type": "audio/wav"},
        content=_synthetic_wav(),
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["available"] is True
    assert transcription.status_code == 200
    assert transcription.json()["text"] == "合成本地语音转写。"
    assert transcription.json()["audio_retained"] is False
    assert service.intake.list_conversations()["items"] == []


def test_local_speech_api_rejects_non_wav_without_invoking_engine(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    client = _client(service)

    response = client.post(
        "/api/class-teacher/intake/speech/transcriptions",
        headers={
            "x-class-teacher-client": "class-teacher-browser-v1",
            "content-type": "audio/webm",
        },
        content=b"synthetic-audio",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "speech_content_type_invalid"


class _CloudAudioModel:
    def __init__(self, *, available: bool = True, fail_after_dispatch: bool = False) -> None:
        self.available = available
        self.fail_after_dispatch = fail_after_dispatch
        self.messages: tuple[dict[str, object], ...] | None = None
        self.invocations = 0

    def audio_input_capabilities(self) -> dict[str, object]:
        return {
            "available": self.available,
            "status": "ready" if self.available else "model_unsupported",
            "provider": "volcengine_ark",
            "model": "doubao-seed-2-0-lite-260428",
            "destination_fingerprint": "synthetic-audio-fingerprint",
        }

    def invoke_workspace_audio(self, *, messages, **_kwargs) -> str:
        self.invocations += 1
        self.messages = messages
        if self.fail_after_dispatch:
            raise RuntimeError("synthetic response loss")
        return json.dumps(
            {
                "contract_version": "class_teacher_audio_triage.v1",
                "transcript": "明天下午三点准备合成班会。",
                "assistant_message": "已整理为班会计划草稿。",
                "clarification_questions": [],
                "work_items": [
                    {
                        "work_item_id": "synthetic-audio-work-001",
                        "domain": "activities_culture",
                        "primary_mode": "plan_calendar",
                        "secondary_modes": ["record"],
                        "intent": "plan",
                        "reason_summary": "需要安排班会",
                        "subject_refs": [],
                        "time_facts": [],
                        "safety_level": "normal",
                        "missing_fields": [],
                        "draft": {
                            "summary": "准备合成班会",
                            "start_at": "2026-08-07T15:00:00+08:00",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )

    def physical_request_count(self, _operation_id: str) -> int:
        return 1 if self.fail_after_dispatch else self.invocations


def test_cloud_audio_requires_confirmation_endpoint_and_persists_only_transcript(
    tmp_path: Path,
) -> None:
    service, _port = _service(tmp_path)
    model = _CloudAudioModel()
    service.intake.model_gateway = model
    client = _client(service)
    trusted = {"x-class-teacher-client": "class-teacher-browser-v1"}
    conversation = client.post(
        "/api/class-teacher/intake/conversations",
        headers=trusted,
    ).json()

    capabilities = client.get("/api/class-teacher/intake/speech/capabilities")
    response = client.post(
        f"/api/class-teacher/intake/conversations/{conversation['conversation_id']}/audio-turns",
        headers={
            **trusted,
            "content-type": "audio/wav",
            "x-class-teacher-conversation-revision": str(conversation["revision"]),
            "x-class-teacher-operation-id": "synthetic-cloud-audio-operation",
            "x-class-teacher-model-fingerprint": "synthetic-audio-fingerprint",
        },
        content=_synthetic_wav(),
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["cloud_audio"]["available"] is True
    assert response.status_code == 200
    result = response.json()
    assert result["turns"][-1]["teacher_message"] == "明天下午三点准备合成班会。"
    assert result["turns"][-1]["assistant_message"] == "已整理为班会计划草稿。"
    assert len(result["handoffs"]) == 1
    assert model.invocations == 1
    assert model.messages is not None
    user_content = model.messages[-1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "input_audio"
    assert user_content[0]["input_audio"]["format"] == "wav"
    assert not list(tmp_path.rglob("*.wav"))


def test_cloud_audio_rejects_unsupported_model_without_dispatch(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    model = _CloudAudioModel(available=False)
    service.intake.model_gateway = model
    client = _client(service)
    trusted = {"x-class-teacher-client": "class-teacher-browser-v1"}
    conversation = client.post(
        "/api/class-teacher/intake/conversations",
        headers=trusted,
    ).json()

    response = client.post(
        f"/api/class-teacher/intake/conversations/{conversation['conversation_id']}/audio-turns",
        headers={
            **trusted,
            "content-type": "audio/wav",
            "x-class-teacher-conversation-revision": str(conversation["revision"]),
            "x-class-teacher-operation-id": "unsupported-cloud-audio-operation",
            "x-class-teacher-model-fingerprint": "synthetic-audio-fingerprint",
        },
        content=_synthetic_wav(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "class_teacher_cloud_audio_unavailable"
    assert model.invocations == 0


def test_cloud_audio_lost_response_is_not_retried(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    model = _CloudAudioModel(fail_after_dispatch=True)
    service.intake.model_gateway = model
    client = _client(service)
    trusted = {"x-class-teacher-client": "class-teacher-browser-v1"}
    conversation = client.post(
        "/api/class-teacher/intake/conversations",
        headers=trusted,
    ).json()

    response = client.post(
        f"/api/class-teacher/intake/conversations/{conversation['conversation_id']}/audio-turns",
        headers={
            **trusted,
            "content-type": "audio/wav",
            "x-class-teacher-conversation-revision": str(conversation["revision"]),
            "x-class-teacher-operation-id": "lost-cloud-audio-operation",
            "x-class-teacher-model-fingerprint": "synthetic-audio-fingerprint",
        },
        content=_synthetic_wav(),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "class_teacher_cloud_audio_result_unknown"
    assert model.invocations == 1
    restored = client.get(
        f"/api/class-teacher/intake/conversations/{conversation['conversation_id']}"
    ).json()
    assert restored["turns"] == []


def _triage(*, subject_id: str | None = None, handoff_key: str | None = None) -> dict[str, object]:
    refs = [] if subject_id is None else [{"kind": "student", "id": subject_id, "revision": "1"}]
    item: dict[str, object] = {
        "work_item_id": "synthetic-work-item-001",
        "domain": "student_growth" if subject_id else "activities_culture",
        "primary_mode": "record" if subject_id else "plan_calendar",
        "secondary_modes": ["plan_calendar"] if subject_id else ["record"],
        "intent": "create" if subject_id else "plan",
        "reason_summary": "合成分诊理由",
        "subject_refs": refs,
        "time_facts": [],
        "safety_level": "normal",
        "missing_fields": [],
        "draft": {"summary": "合成草稿正文", "observed_at": "2026-08-05T08:00:00+08:00"},
    }
    if handoff_key is not None:
        item["handoff_key"] = handoff_key
    return {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理为一项合成事务。",
        "clarification_questions": [],
        "work_items": [item],
    }


def _conversation_with_turn(service: VaultService, operation_id: str, message: str = "合成事务正文") -> tuple[dict[str, object], dict[str, object]]:
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        message=message,
        operation_id=operation_id,
    )
    return conversation, conversation["turns"][-1]


def _work_item(
    work_item_id: str,
    *,
    domain: str,
    mode: str,
    intent: str,
    draft: dict[str, object],
    refs: list[dict[str, str]] | None = None,
    secondary: list[str] | None = None,
) -> dict[str, object]:
    return {
        "work_item_id": work_item_id,
        "domain": domain,
        "primary_mode": mode,
        "secondary_modes": secondary or [],
        "intent": intent,
        "reason_summary": "合成分诊理由",
        "subject_refs": refs or [],
        "time_facts": [],
        "safety_level": "teacher_review_required" if mode == "sop" else "normal",
        "missing_fields": [],
        "draft": draft,
    }


def test_homeroom_preference_changes_filter_without_mutating_roster_history(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    initial = service.intake.preferences.get()
    first = service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(initial["source_revision"]),
        operation_id="homeroom-preference-a",
    )
    second = service.intake.preferences.set(
        homeroom_class="二班",
        expected_revision=int(first["revision"]),
        expected_source_revision=str(first["source_revision"]),
        operation_id="homeroom-preference-b",
    )
    assert second["homeroom_class"] == "二班"
    assert not service.database.exists

    cleared = service.intake.preferences.set(
        homeroom_class=None,
        expected_revision=int(second["revision"]),
        expected_source_revision=str(second["source_revision"]),
        operation_id="homeroom-preference-clear",
    )
    assert cleared["homeroom_class"] is None
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 2


def test_conversation_turn_uses_safe_task_reference_and_replays_operation(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    conversation = service.intake.start_conversation()
    updated = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="合成学生正文不得进入公共任务",
        operation_id="conversation-turn-001",
    )
    replay = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="合成学生正文不得进入公共任务",
        operation_id="conversation-turn-001",
    )
    assert len(updated["turns"]) == 1
    assert len(replay["turns"]) == 1
    assert len(port.dispatch_calls) == 1
    assert "合成学生正文" not in str(port.prepare_calls)
    with pytest.raises(VaultError, match="不同输入"):
        service.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=1,
            message="同一操作编号下的不同合成正文",
            operation_id="conversation-turn-001",
        )


def test_conversation_waits_for_running_turn_and_stales_previous_handoffs_on_new_source(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    conversation, turn = _conversation_with_turn(service, "conversation-running-guard")
    with pytest.raises(VaultError, match="上一轮仍在整理"):
        service.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=int(conversation["revision"]),
            message="运行中不应该追加的合成内容",
            operation_id="conversation-running-guard-next",
        )
    assert len(port.dispatch_calls) == 1

    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=_triage(),
    )
    old_handoff = ready["handoffs"][0]
    continued = service.intake.append_turn(
        conversation_id=str(ready["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="改变原事项事实的合成补充",
        operation_id="conversation-after-handoff",
    )

    stale = next(item for item in continued["handoffs"] if item["handoff_id"] == old_handoff["handoff_id"])
    assert stale["adoption_state"] == "stale"
    assert {call["handoff_id"] for call in port.mark_calls if call["state"] == "stale"} == {old_handoff["handoff_id"]}


def test_triage_creates_allowlisted_handoff_and_rejects_model_url(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="黑板报两周后检查",
        operation_id="conversation-turn-plan",
    )
    turn = conversation["turns"][0]
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=_triage(),
    )
    assert completed["handoffs"][0]["destination_key"] == "class_teacher.plan.calendar"

    other = service.intake.start_conversation()
    other = service.intake.append_turn(
        conversation_id=str(other["conversation_id"]),
        expected_revision=1,
        message="合成任意网址",
        operation_id="conversation-turn-url",
    )
    with pytest.raises(VaultError, match="页面目标"):
        service.intake.apply_triage_result(
            turn_id=str(other["turns"][0]["turn_id"]),
            task_id=str(other["turns"][0]["task_id"]),
            payload=_triage(handoff_key="https://example.invalid/student"),
        )


def test_needs_input_result_replay_does_not_advance_conversation_twice(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-needs-input-replay")
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "还需要教师补充一项合成事实。",
        "clarification_questions": ["请确认合成时间。"],
        "work_items": [],
    }
    first = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    replay = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    assert first["state"] == "needs_input"
    assert replay["revision"] == first["revision"]
    assert replay["turns"][0]["clarification_questions"] == ["请确认合成时间。"]


def test_b_adapter_reads_full_domain_text_but_forces_metadata_only_and_zero_retry(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="合成学生甲需要补录课堂观察",
        operation_id="conversation-turn-adapter",
    )
    turn = conversation["turns"][0]
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref={"kind": "conversation", "id": conversation["conversation_id"], "revision": str(conversation["revision"])},
        context_refs=[{"kind": "turn", "id": turn["turn_id"], "revision": "1"}],
    )
    assert request.messages[-1]["content"] == "合成学生甲需要补录课堂观察"
    assert request.metadata_only is True
    assert request.automatic_retry is False
    assert request.max_send_attempts == 1


def test_first_global_roster_student_is_linked_only_inside_confirmed_record_transaction(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-first-record",
    )
    conversation, turn = _conversation_with_turn(service, "conversation-first-global-student")
    task_request = port.prepare_calls[-1]
    model_request = service.intake.ai_task_adapter.build_model_request(
        task_kind=str(task_request["task_kind"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
    )
    candidates = service.class_roster.ai_candidates(token="", class_label="一班")
    assert len(candidates) == 1
    assert any("合成学生甲" in item["content"] for item in model_request.messages)
    assert "合成学生甲" not in str(task_request)
    candidate = candidates[0]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成一份待教师核对的合成记录。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "first-global-student-item",
                domain="student_growth",
                mode="record",
                intent="create",
                refs=[{"kind": "student", "id": candidate["id"], "revision": candidate["revision"]}],
                draft={"summary": "合成学生首次记录", "observed_at": "2026-08-05T08:00:00+08:00"},
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_subject_links").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM class_roster_memberships").fetchone()[0] == 0
    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=candidate["revision"],
        operation_id="adopt-first-global-student",
    )
    assert receipt["formal_object_type"] == "student_record"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_subject_links").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM class_roster_memberships").fetchone()[0] == 0


def test_duplicate_student_names_require_teacher_choice_and_keep_student_destination(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            (3, "A002", "合成学生甲", "一班"),
        )
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-duplicate-name",
    )
    _conversation, turn = _conversation_with_turn(service, "conversation-duplicate-name")
    task_request = port.prepare_calls[-1]
    candidates = service.class_roster.ai_candidates(token="", class_label="一班")
    selected = candidates[0]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "同名学生需要教师选择。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "duplicate-student-item",
                domain="student_growth",
                mode="record",
                intent="create",
                refs=[{"kind": "student", "id": selected["id"], "revision": selected["revision"]}],
                draft={"summary": "合成同名学生记录"},
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    assert handoff["destination_key"] == "class_teacher.student.record"
    assert handoff["subject_refs"] == []
    assert "请选择一名同名学生" in handoff["missing_fields"]


def test_draft_revision_is_a_new_safe_task_and_never_adopts_formal_data(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-draft-revision")
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=_triage(),
    )
    handoff = service.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))
    snapshot = service.intake.request_draft_revision(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        instruction="把合成行动拆细，但不要改变截止时间",
        operation_id="draft-revision-operation-001",
    )
    replay = service.intake.request_draft_revision(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        instruction="把合成行动拆细，但不要改变截止时间",
        operation_id="draft-revision-operation-001",
    )
    assert replay["request_id"] == snapshot["request_id"]
    assert len(port.dispatch_calls) == 2
    task_request = port.prepare_calls[-1]
    assert task_request["task_kind"] == "class_teacher.draft_revision"
    assert "把合成行动拆细" not in str(task_request)
    model_request = service.intake.ai_task_adapter.build_model_request(
        task_kind=str(task_request["task_kind"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
    )
    assert model_request.prompt_contract_version == "class_teacher_draft_revision.v1"
    assert "把合成行动拆细" in model_request.messages[-1]["content"]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(snapshot["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_draft_revision.v1",
            "content": {"summary": "合成调整后的草稿", "final_deadline": "2026-08-20T16:00:00+08:00"},
        },
    )
    revised = service.intake.open_handoff(str(handoff["handoff_id"]))
    assert outcome["proposal_ref"]["revision"] == "2"
    assert revised["content"]["summary"] == "合成调整后的草稿"
    assert revised["adoption_state"] == "opened"
    assert not service.database.exists
    repeated = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(snapshot["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_draft_revision.v1",
            "content": {"summary": "合成调整后的草稿", "final_deadline": "2026-08-20T16:00:00+08:00"},
        },
    )
    assert repeated["proposal_ref"]["revision"] == "2"


def test_student_record_adoption_receipt_is_idempotent(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="subject-for-r7-record",
        source_student_id="SYNTHETIC-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="补录一条合成课堂观察",
        operation_id="conversation-turn-record",
    )
    turn = conversation["turns"][0]
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=_triage(subject_id=str(subject["subject_id"])),
    )
    handoff_id = str(completed["handoffs"][0]["handoff_id"])
    first = service.intake.adopt_handoff(
        token="",
        handoff_id=handoff_id,
        draft_revision=1,
        target_revision=str(subject["revision"]),
        operation_id="adopt-student-record-001",
    )
    replay = service.intake.adopt_handoff(
        token="",
        handoff_id=handoff_id,
        draft_revision=1,
        target_revision=str(subject["revision"]),
        operation_id="adopt-student-record-001",
    )
    assert first["formal_object_type"] == "student_record"
    assert replay["formal_object_id"] == first["formal_object_id"]
    assert replay["replayed"] is True
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1


def test_invalid_model_result_is_persisted_without_local_ai_conclusion(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation, turn = _conversation_with_turn(service, "conversation-invalid-result")
    with pytest.raises(VaultError, match="分诊版本"):
        service.intake.ai_task_adapter.persist_model_result(
            task_id=str(turn["task_id"]),
            source_ref={"kind": "conversation", "id": conversation["conversation_id"], "revision": str(conversation["revision"])},
            context_refs=[{"kind": "turn", "id": turn["turn_id"], "revision": "1"}],
            result={"contract_version": "unknown", "assistant_message": "不应保存", "work_items": []},
        )
    restored = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert restored["state"] == "failed"
    assert restored["turns"][0]["task_state"] == "invalid_result"
    assert restored["turns"][0]["assistant_message"] is None
    assert restored["handoffs"] == []


def test_manual_routing_after_failure_creates_no_second_ai_task(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    conversation, turn = _conversation_with_turn(service, "conversation-manual-route")
    service.intake.mark_task_outcome(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        task_state="result_unknown",
    )
    routed = service.intake.manual_route(turn_id=str(turn["turn_id"]), mode="sop")
    assert routed["state"] == "manual_routing"
    assert routed["handoffs"][0]["handling_mode"] == "sop"
    assert len(port.prepare_calls) == 1
    assert len(port.dispatch_calls) == 1

    handoff = service.intake.open_handoff(str(routed["handoffs"][0]["handoff_id"]))
    assert handoff["content"]["template_key"] == ""
    with pytest.raises(VaultError, match="选择与实际情况相符"):
        service.intake.adopt_handoff(
            token="",
            handoff_id=str(handoff["handoff_id"]),
            draft_revision=int(handoff["draft_revision"]),
            target_revision="new",
            operation_id="manual-sop-without-template",
        )
    failed = service.intake.open_handoff(str(handoff["handoff_id"]))
    service.intake.adoption.release_uncommitted(
        handoff_id=str(failed["handoff_id"]),
        adoption_id=str(failed["adoption_id"]),
        target_revision="new",
    )
    corrected = service.intake.update_draft(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        content={
            **dict(handoff["content"]),
            "template_key": "baseline.student_injury",
            "participant_refs": ["synthetic-injured-student"],
        },
    )
    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(corrected["handoff_id"]),
        draft_revision=int(corrected["draft_revision"]),
        target_revision="new",
        operation_id="manual-sop-with-injury-template",
    )
    affair = service.sop.get_affair(
        token="", affair_id=str(receipt["formal_object_id"])
    )
    assert affair["template_key"] == "baseline.student_injury"


def test_manual_route_cannot_race_a_running_task_or_downgrade_a_saved_result(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-manual-race")
    with pytest.raises(VaultError, match="仍在处理"):
        service.intake.manual_route(turn_id=str(turn["turn_id"]), mode="record")

    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=_triage(),
    )
    late = service.intake.mark_task_outcome(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), task_state="result_unknown",
    )
    assert completed["state"] == "handoff_ready"
    assert late["state"] == "handoff_ready"
    assert late["turns"][0]["task_state"] == "response_persisted"


def test_conversation_revision_rejects_concurrent_second_reply(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation = service.intake.start_conversation()
    service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="第一窗口合成内容",
        operation_id="conversation-concurrent-a",
    )
    with pytest.raises(VaultError, match="其他页面更新"):
        service.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=1,
            message="第二窗口合成内容",
            operation_id="conversation-concurrent-b",
        )


def test_multiple_work_items_are_adopted_or_discarded_independently(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation, turn = _conversation_with_turn(service, "conversation-multiple-items")
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已拆成两项独立事务。",
        "clarification_questions": [],
        "work_items": [
            _work_item("multi-record-001", domain="class_operations", mode="record", intent="create", draft={"summary": "合成事务登记一"}),
            _work_item("multi-record-002", domain="school_coordination", mode="record", intent="create", draft={"summary": "合成事务登记二"}),
        ],
    }
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    first, second = ready["handoffs"]
    receipt = service.intake.adopt_handoff(
        token="", handoff_id=str(first["handoff_id"]), draft_revision=1,
        target_revision="new", operation_id="adopt-multiple-record",
    )
    partial = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert receipt["formal_object_type"] == "affair_record"
    assert partial["state"] != "teacher_confirmed"
    assert [item["adoption_state"] for item in partial["handoffs"]] == ["adopted", "pending"]
    service.intake.discard_handoff(str(second["handoff_id"]))
    settled = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert settled["state"] == "teacher_confirmed"
    assert [item["adoption_state"] for item in settled["handoffs"]] == ["adopted", "discarded"]


def test_plan_adoption_writes_plan_and_receipt_in_domain_transaction(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-plan-adoption")
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理为计划草稿。",
        "clarification_questions": [],
        "work_items": [_work_item(
            "plan-adoption-001", domain="activities_culture", mode="plan_calendar", intent="plan",
            secondary=["record"],
            draft={
                "summary": "合成黑板报计划", "plan_title": "合成黑板报",
                "final_deadline": "2026-08-20T16:00:00+08:00",
                "actions": [{
                    "draft_action_id": "action-1", "title": "合成初稿检查", "details": "",
                    "due_at": "2026-08-15T16:00:00+08:00", "depends_on_draft_action_ids": [],
                }],
            },
        )],
    }
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    receipt = service.intake.adopt_handoff(
        token="", handoff_id=str(ready["handoffs"][0]["handoff_id"]), draft_revision=1,
        target_revision="new", operation_id="adopt-plan-receipt",
    )
    assert receipt["formal_object_type"] == "plan"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1
        action_id = str(connection.execute("SELECT action_id FROM actions").fetchone()[0])
    calendar = service.work.read(view="all", anchor="2026-08-15")
    assert {item["node_id"] for item in calendar["nodes"]} == {
        receipt["formal_object_id"],
        action_id,
    }
    assert {item["title"] for item in calendar["nodes"]} == {
        "合成黑板报",
        "合成初稿检查",
    }
    assert {item["relation"] for item in calendar["edges"]} == {"contains"}


def test_plan_receipt_recovers_calendar_projection_without_duplicate_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-plan-projection-recovery",
    )
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理为计划草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "plan-projection-recovery-001",
                domain="activities_culture",
                mode="plan_calendar",
                intent="plan",
                draft={
                    "summary": "合成计划投影恢复",
                    "final_deadline": "2026-08-20T16:00:00+08:00",
                    "actions": [{
                        "draft_action_id": "action-1",
                        "title": "合成投影行动",
                        "details": "",
                        "due_at": "2026-08-15T16:00:00+08:00",
                        "depends_on_draft_action_ids": [],
                    }],
                },
            )],
        },
    )
    handoff = ready["handoffs"][0]
    original = service.work.create_confirmed_plan
    calls = 0

    def interrupt_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise VaultError(
                "synthetic_calendar_projection_interrupted",
                "合成日历投影中断",
                status_code=503,
            )
        return original(**kwargs)

    monkeypatch.setattr(service.work, "create_confirmed_plan", interrupt_once)
    with pytest.raises(VaultError, match="合成日历投影中断"):
        service.intake.adopt_handoff(
            token="",
            handoff_id=str(handoff["handoff_id"]),
            draft_revision=1,
            target_revision="new",
            operation_id="ignored-projection-first",
        )

    recovered = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=1,
        target_revision="new",
        operation_id="ignored-projection-recovery",
    )
    assert recovered["formal_object_type"] == "plan"
    assert calls == 2
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 1
    assert len(service.work.read(view="all", anchor="2026-08-15")["nodes"]) == 2


def test_plan_validation_retry_uses_the_revised_deadline_in_plan_and_calendar(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-plan-validation-retry",
    )
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理为计划草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "plan-validation-retry-001",
                domain="activities_culture",
                mode="plan_calendar",
                intent="plan",
                draft={
                    "summary": "合成计划失败后修订",
                    "plan_title": "合成计划",
                    "final_deadline": "2026-08-20T16:00:00+08:00",
                    "actions": [{
                        "draft_action_id": "action-1",
                        "title": "待补日期行动",
                        "details": "保留这段行动说明",
                        "due_at": "",
                        "depends_on_draft_action_ids": [],
                    }],
                },
            )],
        },
    )
    handoff = ready["handoffs"][0]
    with pytest.raises(VaultError, match="行动缺少截止时间"):
        service.intake.adopt_handoff(
            token="",
            handoff_id=str(handoff["handoff_id"]),
            draft_revision=int(handoff["draft_revision"]),
            target_revision="new",
            operation_id="ignored-plan-validation-first",
        )
    failed = service.intake.open_handoff(str(handoff["handoff_id"]))
    service.intake.adoption.release_uncommitted(
        handoff_id=str(failed["handoff_id"]),
        adoption_id=str(failed["adoption_id"]),
        target_revision="new",
    )

    revised = service.intake.update_draft(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        content={
            "summary": "合成计划失败后修订",
            "plan_title": "合成计划",
            "final_deadline": "2026-08-25T16:00:00+08:00",
            "actions": [{
                "draft_action_id": "action-1",
                "title": "已补日期行动",
                "details": "保留这段行动说明",
                "due_at": "2026-08-23T16:00:00+08:00",
                "depends_on_draft_action_ids": [],
            }],
        },
    )
    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(revised["handoff_id"]),
        draft_revision=int(revised["draft_revision"]),
        target_revision="new",
        operation_id="ignored-plan-validation-second",
    )

    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT payload_object_id FROM work_plans WHERE plan_id=?",
            (str(receipt["formal_object_id"]),),
        ).fetchone()
        assert row is not None
        payload, _revision = service.repository.get(
            connection,
            vmk=service.session_key(""),
            object_id=str(row["payload_object_id"]),
        )
    assert str(payload["final_deadline"]).startswith("2026-08-25")
    calendar = service.work.read(view="all", anchor="2026-08-25")
    goal = next(item for item in calendar["nodes"] if item["kind"] == "goal")
    assert goal["due_date"] == "2026-08-25"


def test_sop_adoption_creates_unfinished_affair_without_decision_or_closure(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-sop-adoption")
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "先按安全流程核对。",
        "clarification_questions": [],
        "work_items": [_work_item(
            "sop-adoption-001", domain="conflict_safety", mode="sop", intent="create",
            secondary=["record", "plan_calendar"],
            draft={
                "summary": "两名合成参与人发生争执，目前已分开且无人受伤",
                "template_key": "baseline.student_conflict",
                "participant_refs": ["synthetic-participant-a", "synthetic-participant-b"],
            },
        )],
    }
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    receipt = service.intake.adopt_handoff(
        token="", handoff_id=str(ready["handoffs"][0]["handoff_id"]), draft_revision=1,
        target_revision="new", operation_id="adopt-sop-receipt",
    )
    assert receipt["formal_object_type"] == "sop_affair"
    affair = service.sop.get_affair(token="", affair_id=str(receipt["formal_object_id"]))
    assert affair["state"] != "closed"
    assert affair.get("teacher_decision") in (None, {})


def test_changed_student_target_marks_handoff_stale_and_writes_nothing(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="", operation_id="subject-for-stale", source_student_id="SYNTHETIC-STALE",
        display_name="合成学生旧名", class_label="一班",
    )
    _conversation, turn = _conversation_with_turn(service, "conversation-stale-target")
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]),
        payload=_triage(subject_id=str(subject["subject_id"])),
    )
    service.support.update_subject(
        token="", operation_id="update-subject-for-stale", subject_id=str(subject["subject_id"]),
        revision=int(subject["revision"]), display_name="合成学生新名", class_label="一班",
    )
    with pytest.raises(VaultError, match="学生资料已变化"):
        service.intake.adopt_handoff(
            token="", handoff_id=str(ready["handoffs"][0]["handoff_id"]), draft_revision=1,
            target_revision=str(subject["revision"]), operation_id="adopt-stale-student",
        )
    restored = service.intake.get_conversation(str(ready["conversation_id"]))
    assert restored["handoffs"][0]["adoption_state"] == "stale"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM handoff_adoption_receipts").fetchone()[0] == 0

    current = service.support.get_subject(token="", subject_id=str(subject["subject_id"]))
    rebound = service.intake.update_draft(
        handoff_id=str(ready["handoffs"][0]["handoff_id"]),
        expected_revision=1,
        content={"summary": "合成草稿正文已重新核对", "observed_at": "2026-08-05T08:00:00+08:00"},
        subject_refs=[{
            "kind": "student", "id": str(subject["subject_id"]), "revision": str(current["revision"]),
        }],
    )
    assert rebound["adoption_state"] == "opened"
    receipt = service.intake.adopt_handoff(
        token="", handoff_id=str(rebound["handoff_id"]), draft_revision=int(rebound["draft_revision"]),
        target_revision=str(current["revision"]), operation_id="adopt-rebound-student",
    )
    assert receipt["formal_object_type"] == "student_record"


@pytest.mark.parametrize(
    ("domain", "mode", "secondary", "refs", "destination"),
    [
        ("student_growth", "record", ["plan_calendar"], [{"kind": "student", "id": "student-opaque-01", "revision": "1"}], "class_teacher.student.record"),
        ("student_support", "record", ["sop"], [{"kind": "student", "id": "student-opaque-02", "revision": "1"}], "class_teacher.student.record"),
        ("conflict_safety", "sop", ["record", "plan_calendar"], [], "class_teacher.affair.sop"),
        ("class_operations", "plan_calendar", ["record"], [], "class_teacher.plan.calendar"),
        ("activities_culture", "plan_calendar", ["record"], [], "class_teacher.plan.calendar"),
        ("school_coordination", "record", ["sop"], [], "class_teacher.affair.record"),
    ],
)
def test_six_domains_accept_primary_and_secondary_styles(
    domain: str,
    mode: str,
    secondary: list[str],
    refs: list[dict[str, str]],
    destination: str,
) -> None:
    result = parse_triage({
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "合成六域分诊说明",
        "clarification_questions": [],
        "work_items": [_work_item(
            f"domain-{domain}", domain=domain, mode=mode,
            intent="plan" if mode == "plan_calendar" else "create",
            secondary=secondary, refs=refs, draft={"summary": "合成草稿"},
        )],
    })
    assert result.work_items[0].destination_key == destination
    assert result.work_items[0].secondary_modes == tuple(secondary)


def test_triage_rejects_unknown_fields_and_unknown_safety_level() -> None:
    base = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "合成说明",
        "clarification_questions": [],
        "work_items": [_work_item(
            "unknown-field-item", domain="class_operations", mode="record",
            intent="create", draft={"summary": "合成草稿"},
        )],
    }
    base["work_items"][0]["url"] = "https://example.invalid"
    with pytest.raises(VaultError, match="未知的事务字段"):
        parse_triage(base)
    base["work_items"][0].pop("url")
    base["work_items"][0]["safety_level"] = "auto_punish"
    with pytest.raises(VaultError, match="安全级别"):
        parse_triage(base)


def test_intake_api_requires_trusted_mutation_and_never_places_body_in_location(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    service, port = _service(tmp_path)
    client = _client(service)
    denied = client.post("/api/class-teacher/intake/conversations")
    assert denied.status_code == 403

    headers = {"x-class-teacher-client": "class-teacher-browser-v1"}
    created = client.post("/api/class-teacher/intake/conversations", headers=headers)
    assert created.status_code == 200
    assert created.headers["cache-control"] == "no-store, max-age=0"
    conversation = created.json()
    synthetic_body = "合成接口学生正文-不得进入公共任务或日志"
    appended = client.post(
        f"/api/class-teacher/intake/conversations/{conversation['conversation_id']}/turns",
        headers=headers,
        json={
            "expected_revision": conversation["revision"],
            "message": synthetic_body,
            "operation_id": "api-intake-turn-001",
        },
    )
    assert appended.status_code == 200
    assert appended.headers["cache-control"] == "no-store, max-age=0"
    assert synthetic_body not in str(port.prepare_calls)
    assert synthetic_body not in "\n".join(record.getMessage() for record in caplog.records)
    assert synthetic_body not in str(appended.request.url)
    assert client.post(
        "/api/class-teacher/support/current-roster/replace",
        headers=headers,
        json={
            "operation_id": "legacy-roster-replace",
            "expected_source_revision": "a" * 64,
            "class_label": "一班",
        },
    ).status_code == 404


def test_draft_update_rejects_untrusted_subject_refs_and_oversized_content(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(service, "conversation-draft-validation")
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=_triage(),
    )
    handoff_id = str(ready["handoffs"][0]["handoff_id"])
    with pytest.raises(VaultError, match="学生引用"):
        service.intake.update_draft(
            handoff_id=handoff_id,
            expected_revision=1,
            content={"summary": "合成草稿"},
            subject_refs=[{"kind": "url", "id": "https://example.invalid", "revision": "1"}],
        )
    with pytest.raises(VaultError, match="草稿内容过长"):
        service.intake.update_draft(
            handoff_id=handoff_id,
            expected_revision=1,
            content={"summary": "合" * (70 * 1024)},
        )
