from __future__ import annotations

import json
import sqlite3
import wave
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake.ai_task_adapter import (
    _normalize_triage_payload_compatibility,
    _parse_model_payload,
    _prompt_candidates,
)
from backend.class_teacher.intake.conversations import _merge_draft_revision_content
from backend.class_teacher.intake.preferences import HomeroomPreference
from backend.class_teacher.intake.ports import FakeWorkspaceAITaskPort
from backend.class_teacher.intake.triage_contract import parse_triage
from backend.class_teacher.local_speech import LocalSpeechTranscriber
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_class_teacher_uses_diagnostics_local_json_repair_for_terminal_closers() -> None:
    expected = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理",
        "clarification_questions": [],
        "work_items": [{"draft": {"summary": "合成事务"}}],
    }
    serialized = json.dumps(expected, ensure_ascii=False)
    malformed = serialized[:-3] + serialized[-2:]

    assert _parse_model_payload(malformed) == expected


@pytest.mark.parametrize("extra_field", ["name", "description", "additionalProp1", "missing_fields", "profile_update", "profile_base_revision", "ai_disclaimer"])
def test_class_teacher_ignores_observed_schema_description_fields(
    extra_field: str,
) -> None:
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理，请核对。",
        "clarification_questions": [],
        "work_items": [{
            "work_item_id": "item_001",
            "domain": "class_operations",
            "primary_mode": "plan_calendar",
            "secondary_modes": [],
            "intent": "plan",
            "reason_summary": "需要安排返校日程",
            "subject_refs": [],
            "time_facts": [],
            "safety_level": "normal",
            "missing_fields": [],
            "draft": {"summary": "合成返校安排"},
        }],
        extra_field: [] if extra_field == "missing_fields" else "schema description",
    }

    result = parse_triage(_normalize_triage_payload_compatibility(payload))

    assert result.work_items[0].primary_mode == "plan_calendar"


def test_class_teacher_drops_unknown_semantic_top_level_fields(tmp_path: Path) -> None:
    """模型在顶层多写的未知字段（哪怕看起来像语义字段）在归一层被丢弃，
    不再让整轮作废，也不会进入草稿内容。"""
    service, port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service, "unknown-top-level", message="月底提醒复查合成事项"
    )
    task = port.prepare_calls[-1]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理，请核对。",
            "clarification_questions": [],
            "diagnosis": "模型自行作出的结论",
            "work_items": [_work_item(
                "unknown-top-level-item",
                domain="class_operations",
                mode="record",
                intent="create",
                draft={"summary": "月底复查合成事项"},
            )],
        },
    )

    assert outcome["handoff_ids"]
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    assert "diagnosis" not in handoff["content"]


def test_triage_shape_defects_are_softened_instead_of_failing(tmp_path: Path) -> None:
    """形状瑕疵（超长、超量、多余字段、不认识的安全级别、空说明）就地修复，
    语义有效的事务照常落定；语义枚举仍不猜测。"""
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="shape-softening-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service, "shape-softening", message="合成班务"
    )
    task = port.prepare_calls[-1]
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    items: list[dict[str, object]] = []
    for index in range(9):  # 9 个事务，超过合同上限 8
        items.append({
            "work_item_id": "dup",  # 过短且全部重复
            "domain": "class_operations",
            "primary_mode": "record",
            "secondary_modes": ["record", "归档", "plan_calendar", "sop"],
            "intent": "create",
            "reason_summary": "",
            "subject_refs": [],
            "time_facts": ["今天提醒", {"text": "对象"}, 42],
            "safety_level": "critical",
            "missing_fields": ["x" * 500],
            "draft": {"summary": f"合成草稿{index}"},
            "model_extra": "模型多写的字段",
            "handoff_key": "class_teacher.plan.calendar",
        })
    # 第一项带一个形状不合格（多键）但指向真实候选的学生引用。
    items[0]["subject_refs"] = [{
        "kind": "student",
        "id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
        "name": "多余键",
    }]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "",
            "clarification_questions": [],
            "work_items": items,
            "model_note": "顶层多写的字段",
        },
    )

    assert len(outcome["handoff_ids"]) == 8
    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    assert saved["turns"][-1]["assistant_message"] == "已整理，请核对草稿。"
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    content = handoff["content"]
    assert content["safety_level"] == "teacher_review_required"
    assert content["reason_summary"] == "模型未说明整理理由，采用前请核对"
    assert content["time_facts"] == [{"text": "今天提醒"}, {"text": "对象"}]
    assert handoff["missing_fields"] == ["x" * 400]
    assert "model_extra" not in content
    assert handoff["subject_refs"] == [{
        "kind": "student",
        "id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
    }]


def test_ai_sop_revision_preserves_safety_and_each_students_profile_draft() -> None:
    current = {
        "summary": "原流程",
        "steps": [
            {
                "key": "confirm_safety",
                "title": "确认即时安全",
                "details": "先分开并检查伤情",
                "safety_required": True,
            },
            {
                "key": "separate_interviews",
                "title": "分别了解",
                "details": "分别听取陈述",
                "safety_required": False,
            },
        ],
        "student_profile_updates": [
            {
                "subject_ref": {"kind": "student", "id": "subject-a", "revision": "3"},
                "record_summary": "甲的原记录",
                "profile_update": {"summary": "甲的原档案", "open_questions": ["甲待核对"]},
            },
            {
                "subject_ref": {"kind": "student", "id": "subject-b", "revision": "3"},
                "record_summary": "乙的原记录",
                "profile_update": {"summary": "乙的原档案", "open_questions": ["乙待核对"]},
            },
        ],
    }

    revised = _merge_draft_revision_content(
        handling_mode="sop",
        current=current,
        proposed={
            "summary": "补充细节后的流程",
            "steps": [{
                "key": "confirm_safety",
                "title": "再次确认即时安全",
                "details": "补充检查是否有身体不适",
                "safety_required": False,
            }],
            "student_profile_updates": [{
                "subject_ref": {"kind": "student", "id": "subject-a", "revision": "3"},
                "record_summary": "甲的补充记录",
                "profile_update": {"summary": "甲的丰富后档案"},
            }],
        },
    )

    assert revised["summary"] == "补充细节后的流程"
    assert revised["steps"][0]["safety_required"] is True
    assert {item["key"] for item in revised["steps"]} == {
        "confirm_safety",
        "separate_interviews",
    }
    assert revised["student_profile_updates"][0]["profile_update"] == {
        "summary": "甲的丰富后档案",
        "open_questions": ["甲待核对"],
    }
    assert revised["student_profile_updates"][1]["record_summary"] == "乙的原记录"


def test_ai_sop_revision_cannot_replace_the_teacher_route_decision_with_a_verdict() -> None:
    revised = _merge_draft_revision_content(
        handling_mode="sop",
        current={
            "template_key": "baseline.student_conflict",
            "steps": [{
                "key": "route",
                "title": "由教师重新检查安全与疑似欺凌信号",
                "details": "系统不作欺凌认定；教师只选择当前工作分流。",
            }],
        },
        proposed={
            "steps": [{
                "key": "route",
                "title": "冲突分流判断",
                "details": "当前事件判定为普通学生矛盾，无疑似欺凌线索。",
            }],
        },
    )

    route = revised["steps"][0]
    assert route["title"] == "由教师重新检查安全与疑似欺凌信号"
    assert route["details"] == "系统不作欺凌认定；教师只选择当前工作分流。"


@pytest.mark.parametrize(
    ("mode", "domain", "intent"),
    [
        ("record", "student_support", "follow_up"),
        ("plan_calendar", "class_operations", "plan"),
        ("sop", "school_coordination", "review"),
    ],
)
def test_class_teacher_accepts_three_workflows_with_empty_provider_schema_artifacts(
    mode: str,
    domain: str,
    intent: str,
) -> None:
    payload = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理，请核对。",
        "clarification_questions": [],
        "work_items": [{
            "work_item_id": "item_001",
            "domain": domain,
            "primary_mode": mode,
            "secondary_modes": [],
            "intent": intent,
            "reason_summary": "需要记录并跟进家长反馈",
            "subject_refs": [],
            "time_facts": [],
            "safety_level": "teacher_review_required",
            "missing_fields": ["student_ref"],
            "draft": {"summary": "合成家校沟通记录"},
        }],
        "school_coordination": [],
    }

    result = parse_triage(_normalize_triage_payload_compatibility(payload))

    assert result.work_items[0].primary_mode == mode


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
    return VaultService(context, workspace_ai_task_port=port), port


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


def test_empty_home_conversation_is_reused_until_it_has_content(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    first = service.intake.start_conversation()
    second = service.intake.start_conversation()
    assert first["conversation_id"] == second["conversation_id"]
    assert service.intake.list_conversations()["items"] == []

    service.intake.append_turn(
        conversation_id=str(first["conversation_id"]),
        expected_revision=1,
        message="余天策需要跟进家访",
        operation_id="conversation-turn-reuse-001",
    )
    listed = service.intake.list_conversations()["items"]
    assert [item["first_message"] for item in listed] == ["余天策需要跟进家访"]

    third = service.intake.start_conversation()
    assert third["conversation_id"] != first["conversation_id"]
    assert [item["first_message"] for item in service.intake.list_conversations()["items"]] == [
        "余天策需要跟进家访"
    ]


def test_recent_conversations_hide_empty_and_keep_five(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    for index in range(7):
        conversation = service.intake.start_conversation()
        service.intake.append_turn(
            conversation_id=str(conversation["conversation_id"]),
            expected_revision=1,
            message=f"合成事项{index}",
            operation_id=f"conversation-turn-recent-{index:03d}",
        )

    items = service.intake.list_conversations()["items"]
    assert len(items) == 5
    assert all(str(item["first_message"]).startswith("合成事项") for item in items)
    extra = service.intake.list_conversations(limit=10)["items"]
    assert len(extra) == 7
    assert {item["first_message"] for item in extra} == {f"合成事项{index}" for index in range(7)}


def test_delete_conversation_removes_turns_and_hides_it_from_recent(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    conversation = service.intake.start_conversation()
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="需要删除的合成对话",
        operation_id="conversation-turn-delete-001",
    )
    deleted = service.intake.delete_conversation(str(conversation["conversation_id"]))
    assert deleted == {
        "conversation_id": conversation["conversation_id"],
        "deleted": True,
    }
    assert service.intake.list_conversations()["items"] == []
    with pytest.raises(VaultError, match="会话不存在"):
        service.intake.get_conversation(str(conversation["conversation_id"]))


def test_focused_student_conversation_does_not_reuse_empty_home_desk(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="empty-home-not-reused-subject",
        source_student_id="SYN-EMPTY-HOME-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    home = service.intake.start_conversation()
    focused = service.intake.start_conversation(
        token="",
        subject_id=str(subject["subject_id"]),
    )
    assert focused["conversation_id"] != home["conversation_id"]
    assert focused["focused_subject_id"] == subject["subject_id"]


def test_student_profile_conversation_keeps_the_selected_student_in_every_task(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="profile-conversation-subject",
        source_student_id="SYN-PROFILE-001",
        display_name="合成学生甲",
        class_label="一班",
    )

    conversation = service.intake.start_conversation(
        token="",
        subject_id=str(subject["subject_id"]),
    )
    queued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        message="最近更愿意在小组里主动分工。",
        operation_id="profile-conversation-turn-001",
    )

    assert queued["focused_subject_id"] == subject["subject_id"]
    assert queued["focused_subject_revision"] == str(subject["revision"])
    assert port.prepare_calls[-1]["context_refs"] == [
        {
            "kind": "turn",
            "id": queued["turns"][-1]["turn_id"],
            "revision": "1",
        },
        {
            "kind": "student_profile",
            "id": subject["subject_id"],
            "revision": str(subject["revision"]),
        },
    ]
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref={
            "kind": "conversation",
            "id": queued["conversation_id"],
            "revision": str(queued["revision"]),
        },
        context_refs=list(port.prepare_calls[-1]["context_refs"]),
    )
    joined = "\n".join(str(item["content"]) for item in request.messages)
    assert "当前会话从一个已选学生的档案页发起" in joined
    assert "当前学生档案与支持情况" in joined
    assert str(subject["subject_id"]) in joined

    normalized = service.intake.ai_task_adapter.normalize_triage_result(
        conversation=queued,
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到当前档案。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": "profile-work-item-001",
                "domain": "student_growth",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "append",
                "reason_summary": "补充学生当前档案",
                "subject_refs": [],
                "time_facts": [],
                "safety_level": "normal",
                "missing_fields": [],
                "draft": {
                    "summary": "最近更愿意在小组里主动分工。",
                    "profile_update": {
                        "summary": "在小组合作中开始表现出主动分工意愿。",
                        "dimensions": [{
                            "key": "peer_relationships",
                            "label": "同伴与人际关系",
                            "items": ["近期更愿意在小组中主动分工"],
                        }],
                        "open_questions": ["遇到意见不同时能否继续参与讨论"],
                        "support_focus": [],
                    },
                },
            }],
        },
    )
    item = normalized["work_items"][0]
    assert item["subject_refs"] == [{
        "kind": "student",
        "id": subject["subject_id"],
        "revision": str(subject["revision"]),
    }]
    assert item["draft"]["profile_base_revision"] == 0


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
            "status": "ready" if self.available else "profile_missing",
            "provider": "configured_model",
            "model": "synthetic-audio-model" if self.available else None,
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


def test_cloud_audio_requires_a_configured_model_without_dispatch(tmp_path: Path) -> None:
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
        "draft": {
            "summary": "合成草稿正文",
            "observed_at": "2026-08-05T08:00:00+08:00",
            **(
                {"record_kind": "fact", "source": "合成教师核对"}
                if subject_id
                else {}
            ),
        },
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
    attribution_defaults: bool = True,
) -> dict[str, object]:
    normalized_draft = dict(draft)
    if mode == "record" and attribution_defaults:
        normalized_draft.setdefault("record_kind", "fact")
        normalized_draft.setdefault("source", "合成教师核对")
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
        "draft": normalized_draft,
    }


@pytest.mark.parametrize(
    ("attribution", "expected_code"),
    [
        ({"source": "合成教师观察"}, "class_teacher_record_kind_invalid"),
        (
            {"record_kind": "ai_draft", "source": "合成模型草稿"},
            "class_teacher_record_kind_invalid",
        ),
        (
            {"record_kind": "fact", "source": "   "},
            "class_teacher_record_source_required",
        ),
    ],
)
def test_record_adoption_rejects_missing_or_unconfirmed_attribution(
    tmp_path: Path,
    attribution: dict[str, object],
    expected_code: str,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        f"record-attribution-{expected_code}",
    )
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成一份待教师核对的合成记录。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "record-attribution-item",
                domain="school_coordination",
                mode="record",
                intent="create",
                draft={"summary": "合成待审事实", **attribution},
                attribution_defaults=False,
            )],
        },
    )

    with pytest.raises(VaultError) as error:
        service.intake.adopt_handoff(
            token="",
            handoff_id=str(ready["handoffs"][0]["handoff_id"]),
            draft_revision=1,
            target_revision="new",
            operation_id=f"adopt-{expected_code}",
        )

    assert error.value.status_code == 422
    assert error.value.code == expected_code
    assert service.database.exists is False


def test_record_adoption_preserves_supplied_professional_attribution_until_teacher_confirms(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "record-professional-attribution",
    )
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成一份待教师核对的合成记录。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "record-professional-item",
                domain="school_coordination",
                mode="record",
                intent="create",
                draft={
                    "summary": "医院已经提供书面诊断，等待教师核对来源。",
                    "record_kind": "professional_conclusion",
                    "source": "合成医院书面材料",
                    "basis": "合成书面材料编号与出具机构",
                    "observed_at": "2026-08-05T08:00:00+08:00",
                    "teacher_confirmed": False,
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(
        str(ready["handoffs"][0]["handoff_id"])
    )
    assert handoff["content"]["teacher_confirmed"] is False

    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision="new",
        operation_id="adopt-professional-attribution",
    )
    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT payload_object_id FROM class_teacher_affair_records "
            "WHERE record_id=?",
            (str(receipt["formal_object_id"]),),
        ).fetchone()
        assert row is not None
        payload, _revision = service.repository.get(
            connection,
            vmk=service.ensure_plaintext_ready(),
            object_id=str(row["payload_object_id"]),
        )
    assert payload["record_kind"] == "professional_conclusion"
    assert payload["source"] == "合成医院书面材料"
    assert payload["basis"] == "合成书面材料编号与出具机构"
    assert payload["teacher_confirmed"] is True


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


def test_saved_homeroom_remains_available_when_roster_is_temporarily_unavailable(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    initial = service.intake.preferences.get()
    saved = service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(initial["source_revision"]),
        operation_id="homeroom-startup-recovery",
    )

    class UnavailableRoster:
        def snapshot(self):
            raise VaultError(
                "existing_student_roster_unavailable",
                "现有学生库暂时无法读取，请稍后重试",
                status_code=503,
            )

    restarted = HomeroomPreference(service.ordinary_database, UnavailableRoster())

    restored = restarted.get()

    assert restored["homeroom_class"] == saved["homeroom_class"] == "一班"
    assert restored["revision"] == saved["revision"]
    assert restored["classes"] == ["一班"]
    assert restored["source_revision"] == initial["source_revision"]


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


def test_reused_model_work_item_id_is_namespaced_per_turn(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    first, first_turn = _conversation_with_turn(
        service,
        "reused-model-item-first",
        message="第一段合成事务",
    )
    first_task = port.prepare_calls[-1]
    first_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result=_triage(),
    )

    second, second_turn = _conversation_with_turn(
        service,
        "reused-model-item-second",
        message="第二段合成事务",
    )
    second_task = port.prepare_calls[-1]
    second_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(second_turn["task_id"]),
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
        result=_triage(),
    )

    first_saved = service.intake.conversations.get(str(first["conversation_id"]))
    second_saved = service.intake.conversations.get(str(second["conversation_id"]))
    assert first_outcome["handoff_ids"]
    assert second_outcome["handoff_ids"]
    assert first_saved["handoffs"][0]["work_item_id"] != second_saved["handoffs"][0]["work_item_id"]


def test_explicit_teacher_sop_request_promotes_model_secondary_sop(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    conversation, turn = _conversation_with_turn(
        service,
        "explicit-sop-request",
        message="请按 SOP 方式整理这次合成争执，不作欺凌认定。",
    )
    task = port.prepare_calls[-1]
    result = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理合成争执。",
        "clarification_questions": [],
        "work_items": [_work_item(
            "model-record-with-sop-secondary",
            domain="conflict_safety",
            mode="record",
            intent="create",
            secondary=["sop"],
            draft={"summary": "合成争执，当前没有即时危险。"},
        )],
    }

    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result=result,
    )

    saved = service.intake.conversations.get(str(conversation["conversation_id"]))
    assert saved["handoffs"][0]["handling_mode"] == "sop"
    assert saved["handoffs"][0]["destination_key"] == "class_teacher.affair.sop"


def test_sop_adoption_reuses_legacy_student_code_subject_for_roster_candidate(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-sop-subject",
    )
    candidate = service.class_roster.ai_candidates(
        token="",
        class_label="一班",
    )[0]
    source = service.class_roster.resolve_roster_ref(
        roster_ref=candidate["id"],
        expected_revision=candidate["revision"],
    )
    subject = service.support.create_subject(
        token="",
        operation_id="existing-subject-for-sop",
        source_student_id=source.student_code,
        display_name=source.display_name,
        class_label=source.class_label,
    )
    conversation, turn = _conversation_with_turn(
        service,
        "sop-current-roster-candidate",
        message="请按 SOP 方式处理合成学生甲的争执。",
    )
    task = port.prepare_calls[-1]
    result = {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已整理合成 SOP。",
        "clarification_questions": [],
        "work_items": [_work_item(
            "sop-current-roster-item",
            domain="conflict_safety",
            mode="sop",
            intent="follow_up",
            refs=[{
                "kind": "student",
                "id": candidate["id"],
                "revision": candidate["revision"],
            }],
            draft={
                "summary": "合成学生争执，目前没有即时危险。",
                "template_key": "baseline.student_conflict",
            },
        )],
    }
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result=result,
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    # 新语义：可建单的 SOP 交接在结果落库时已自动建成正式事务。
    assert handoff["adoption_state"] == "adopted"
    affair = service.sop.get_affair(
        token="",
        affair_id=str(handoff["affair_id"]),
    )
    assert affair["participants"][0]["subject_id"] == subject["subject_id"]


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


def test_conflict_follow_up_revises_the_previous_sop_without_reasking_known_safety_facts(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    conversation, first_turn = _conversation_with_turn(
        service,
        "conflict-revision-first-turn",
        message="两名合成学生发生了矛盾。",
    )
    first_task = port.prepare_calls[-1]
    first_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "先形成安全处理初稿，再继续核对事实。",
            "clarification_questions": ["双方目前是否已经分开？"],
            "work_items": [_work_item(
                "conflict-revision-first-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                draft={
                    "summary": "第一轮冲突处理摘要，后续修订必须保留。",
                    "template_key": "baseline.student_conflict",
                    "teacher_note": "已确认需要分别听取双方陈述。",
                },
            )],
        },
    )
    first_handoff = service.intake.open_handoff(
        str(first_outcome["handoff_ids"][0])
    )
    ready = service.intake.conversations.get(str(conversation["conversation_id"]))
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="双方已经分开，目前无人受伤，起因是座位和小组分工意见不一致；请分别陈述并共同修复。",
        operation_id="conflict-revision-second-turn",
    )
    second_task = port.prepare_calls[-1]
    model_request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
    )
    joined = "\n".join(str(message["content"]) for message in model_request.messages)

    assert "上一轮待核对草稿" in joined
    assert "第一轮冲突处理摘要，后续修订必须保留" in joined

    second_turn = continued["turns"][-1]
    second_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(second_turn["task_id"]),
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已根据补充事实调整方案。",
            "clarification_questions": ["双方对小组分工分别如何描述？"],
            "work_items": [_work_item(
                "conflict-revision-second-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                draft={
                    "summary": "双方已分开且无人受伤，继续核对分工争议。",
                    "template_key": "baseline.student_conflict",
                    "steps": [{
                        "key": "route",
                        "title": "冲突分流判断",
                        "details": "当前事件判定为普通学生矛盾，无疑似欺凌线索。",
                    }],
                },
            )],
        },
    )
    second_handoff = service.intake.open_handoff(
        str(second_outcome["handoff_ids"][0])
    )
    saved = service.intake.conversations.get(str(conversation["conversation_id"]))

    assert second_handoff["content"]["revision_of_draft_id"] == first_handoff["draft_id"]
    assert second_handoff["content"]["teacher_note"] == "已确认需要分别听取双方陈述。"
    step_by_key = {
        item["key"]: item
        for item in second_handoff["content"]["steps"]
    }
    assert "座位使用规则" in step_by_key["fact_check"]["details"]
    assert "共同修复方式" in step_by_key["ordinary_support"]["details"]
    assert "分别确认修复是否有效" in step_by_key["follow_up"]["details"]
    assert step_by_key["route"]["title"] == "由教师重新检查安全与疑似欺凌信号"
    assert step_by_key["route"]["details"] == "系统不作欺凌认定；教师只选择当前工作分流。"
    assert saved["turns"][-1]["clarification_questions"] == [
        "双方对小组分工分别如何描述？",
    ]
    assert sum(
        item["adoption_state"] in {"pending", "opened"}
        for item in saved["handoffs"]
    ) == 1


def test_explicit_new_topic_does_not_revise_previous_conflict_draft(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    conversation, first_turn = _conversation_with_turn(
        service,
        "conflict-new-topic-first-turn",
        message="两名合成学生发生了矛盾。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "先形成第一件事的处理初稿。",
            "clarification_questions": ["双方目前是否已经分开？"],
            "work_items": [_work_item(
                "conflict-new-topic-first-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                draft={
                    "summary": "第一件冲突的独有摘要，不应带入新事项。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )
    ready = service.intake.conversations.get(str(conversation["conversation_id"]))
    service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="另外，再说一件新的学生冲突：两名合成学生在操场争执。",
        operation_id="conflict-new-topic-second-turn",
    )
    second_task = port.prepare_calls[-1]
    model_request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
    )
    joined = "\n".join(str(message["content"]) for message in model_request.messages)

    assert "第一件冲突的独有摘要，不应带入新事项" not in joined


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


def test_general_affair_keeps_mentioned_student_profile_context_on_follow_up(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="general-affair-profile-subject",
        source_student_id="SYN-GENERAL-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    with closing(service.database.connect()) as connection:
        with connection:
            service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=service.student_cards._key_provider(""),
                subject_id=str(subject["subject_id"]),
                profile_update={
                    "summary": "遇到分歧时需要先给出安静表达的时间。",
                    "dimensions": [{
                        "key": "peer_relationships",
                        "label": "同伴与人际关系",
                        "items": ["在多人争论中容易暂时退出"],
                    }],
                    "open_questions": [],
                    "support_focus": [],
                },
                expected_revision=0,
                operation_id="general_affair_profile_create",
                model_operation_id="synthetic-model-context",
                teacher_quote="合成档案原话",
                model_draft="合成档案草稿",
            )

    conversation, first_turn = _conversation_with_turn(
        service,
        "general-affair-first-turn",
        message="合成学生甲和同学发生分歧。",
    )
    ready = service.intake.apply_triage_result(
        turn_id=str(first_turn["turn_id"]),
        task_id=str(first_turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "还需要了解后续情况。",
            "clarification_questions": ["之后发生了什么？"],
            "work_items": [],
        },
    )
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="他后来愿意重新回到讨论中。",
        operation_id="general-affair-follow-up",
    )
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref={
            "kind": "conversation",
            "id": continued["conversation_id"],
            "revision": str(continued["revision"]),
        },
        context_refs=[{
            "kind": "turn",
            "id": continued["turns"][-1]["turn_id"],
            "revision": "1",
        }],
    )
    joined = "\n".join(str(item["content"]) for item in request.messages)

    assert "本机找到的涉事学生当前档案" in joined
    assert "遇到分歧时需要先给出安静表达的时间" in joined
    assert "他后来愿意重新回到讨论中" in joined


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


def test_distinct_students_in_one_conflict_keep_both_subjects(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute(
            "UPDATE students SET class_name='一班' WHERE id=2"
        )
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-distinct-conflict-students",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-distinct-conflict-students",
        message="合成学生甲和合成学生乙发生了矛盾。",
    )
    task_request = port.prepare_calls[-1]
    candidates = service.class_roster.ai_candidates(
        token="",
        class_label="一班",
    )

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理两名学生的冲突处理草稿。",
            "clarification_questions": ["双方目前是否已经分开？"],
            "work_items": [_work_item(
                "distinct-conflict-students-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                refs=[
                    {
                        "kind": "student",
                        "id": str(candidate["id"]),
                        "revision": str(candidate["revision"]),
                    }
                    for candidate in candidates
                ],
                draft={
                    "summary": "两名不同学生发生冲突，待教师继续核对。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    assert len(handoff["subject_refs"]) == 2
    assert handoff["missing_fields"] == []
    assert len(handoff["content"]["student_profile_updates"]) == 2
    assert {
        update["subject_ref"]["id"]
        for update in handoff["content"]["student_profile_updates"]
    } == {ref["id"] for ref in handoff["subject_refs"]}
    assert all(
        update["include"] is False
        for update in handoff["content"]["student_profile_updates"]
    )


def test_conflict_record_with_profile_questions_becomes_actionable_sop(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute(
            "UPDATE students SET class_name='一班' WHERE id=2"
        )
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-conflict-sop-promotion",
    )
    conversation, turn = _conversation_with_turn(
        service,
        "conversation-conflict-sop-promotion",
        message="合成学生甲和合成学生乙在信息课发生了矛盾。",
    )
    task_request = port.prepare_calls[-1]
    candidates = service.class_roster.ai_candidates(
        token="",
        class_label="一班",
    )

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "两名学生发生矛盾，需要继续核对处理。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "conflict-record-needs-sop-item",
                domain="conflict_safety",
                mode="record",
                intent="follow_up",
                refs=[
                    {
                        "kind": "student",
                        "id": str(candidate["id"]),
                        "revision": str(candidate["revision"]),
                    }
                    for candidate in candidates
                ],
                draft={
                    "summary": "两名学生在信息课发生矛盾，待教师核实。",
                    "profile_update": {
                        "summary": "本轮只掌握到一项待核对的同伴冲突线索。",
                        "dimensions": [],
                        "open_questions": [
                            "矛盾发生的具体原因和经过是什么？",
                            "双方目前的状态如何？",
                        ],
                        "support_focus": [{
                            "key": "conflict_resolution",
                            "title": "冲突解决支持",
                            "need": "核实事实并避免矛盾升级",
                            "effective_methods": [],
                            "next_actions": [
                                "分别与双方学生核对事实",
                                "根据核实结果安排后续观察",
                            ],
                        }],
                    },
                },
            )],
        },
    )
    saved = service.intake.conversations.get(str(conversation["conversation_id"]))
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    assert handoff["handling_mode"] == "sop"
    assert handoff["destination_key"] == "class_teacher.affair.sop"
    assert len(handoff["subject_refs"]) == 2
    assert handoff["content"]["template_key"] == "baseline.student_conflict"
    assert len(handoff["content"]["steps"]) >= 5
    assert saved["turns"][-1]["clarification_questions"] == [
        "双方目前是否已经分开，是否仍有即时冲突风险？",
        "是否有人受伤或需要立即联系校医、学校负责人？",
        "矛盾发生的具体原因和经过是什么？",
    ]


def test_conflict_sop_includes_each_mentioned_students_current_profile(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute(
            "UPDATE students SET class_name='一班' WHERE id=2"
        )
        connection.commit()
    for index, (name, summary) in enumerate((
        ("合成学生甲", "表达分歧时需要先获得安静陈述的时间。"),
        ("合成学生乙", "面对误解时愿意在教师引导下重新说明经过。"),
    ), start=1):
        subject = service.support.create_subject(
            token="",
            operation_id=f"conflict-profile-subject-{index}",
            source_student_id=f"SYN-CONFLICT-{index:03d}",
            display_name=name,
            class_label="一班",
        )
        with closing(service.database.connect()) as connection:
            with connection:
                service.student_cards.upsert_current_profile_in_connection(
                    connection,
                    vmk=service.student_cards._key_provider(""),
                    subject_id=str(subject["subject_id"]),
                    profile_update={
                        "summary": summary,
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                    expected_revision=0,
                    operation_id=f"conflict_profile_create_{index}",
                    model_operation_id=f"synthetic-profile-context-{index}",
                    teacher_quote="合成档案原话",
                    model_draft="合成档案草稿",
                )
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-conflict-profile-context",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-conflict-profile-context",
        message="合成学生甲和合成学生乙在信息课发生了矛盾。",
    )
    task_request = port.prepare_calls[-1]
    candidates = service.class_roster.ai_candidates(token="", class_label="一班")
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task_request["source_ref"],
        context_refs=task_request["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已结合两名学生的当前档案整理冲突 SOP。",
            "clarification_questions": ["双方目前是否已经分开？"],
            "work_items": [_work_item(
                "conflict-profile-context-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                refs=[
                    {
                        "kind": "student",
                        "id": str(candidate["id"]),
                        "revision": str(candidate["revision"]),
                    }
                    for candidate in candidates
                ],
                draft={
                    "summary": "两名学生发生矛盾，待教师继续核对。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    profiles = handoff["content"]["student_profiles"]
    assert {profile["display_name"] for profile in profiles} == {
        "合成学生甲",
        "合成学生乙",
    }
    assert {profile["profile"]["summary"] for profile in profiles} == {
        "表达分歧时需要先获得安静陈述的时间。",
        "面对误解时愿意在教师引导下重新说明经过。",
    }
    updates = handoff["content"]["student_profile_updates"]
    assert {update["profile_base_revision"] for update in updates} == {1}
    assert {update["profile_update"]["summary"] for update in updates} == {
        "表达分歧时需要先获得安静陈述的时间。",
        "面对误解时愿意在教师引导下重新说明经过。",
    }


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
    assert "content 只返回需要新增或改动的顶层字段" in model_request.messages[0]["content"]
    assert "不要重复未改字段" in model_request.messages[0]["content"]
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


def test_student_record_handoff_is_not_auto_open_allowed(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="subject-for-no-auto-open",
        source_student_id="SYNTHETIC-NO-AUTO-OPEN",
        display_name="合成学生甲",
        class_label="一班",
    )
    conversation, turn = _conversation_with_turn(
        service,
        "student-record-no-auto-open",
        message="补录一条合成课堂观察",
    )
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=_triage(subject_id=str(subject["subject_id"])),
    )
    handoff = completed["handoffs"][0]
    assert handoff["destination_key"] == "class_teacher.student.record"
    assert handoff["auto_open_allowed"] is False


def test_student_record_follow_up_revises_previous_draft_without_clarification(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="homeroom-for-student-record-revision",
    )
    selected = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    conversation, first_turn = _conversation_with_turn(
        service,
        "student-record-revision-first-turn",
        message="合成学生甲家庭情况需要记入当前档案。",
    )
    first_task = port.prepare_calls[-1]
    first_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到当前学生档案。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "student-record-revision-first-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[{
                    "kind": "student",
                    "id": str(selected["id"]),
                    "revision": str(selected["revision"]),
                }],
                draft={
                    "summary": "第一轮学生档案摘要，后续修订必须保留。",
                    "profile_update": {
                        "summary": "第一轮学生档案摘要，后续修订必须保留。",
                        "dimensions": [{
                            "key": "family_communication",
                            "label": "家庭沟通与身心状态",
                            "items": ["家庭情况待核对"],
                        }],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    first_handoff = service.intake.open_handoff(
        str(first_outcome["handoff_ids"][0])
    )
    assert first_handoff["destination_key"] == "class_teacher.student.record"
    ready = service.intake.conversations.get(str(conversation["conversation_id"]))
    assert ready["handoffs"][0]["auto_open_allowed"] is False
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="把档案摘要写短一点，不要写成诊断。",
        operation_id="student-record-revision-second-turn",
    )
    second_task = port.prepare_calls[-1]
    model_request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
    )
    joined = "\n".join(str(message["content"]) for message in model_request.messages)
    assert "上一轮待核对草稿" in joined
    assert "第一轮学生档案摘要，后续修订必须保留" in joined

    second_turn = continued["turns"][-1]
    second_outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(second_turn["task_id"]),
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已按要求缩短档案摘要。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "student-record-revision-second-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[{
                    "kind": "student",
                    "id": str(selected["id"]),
                    "revision": str(selected["revision"]),
                }],
                draft={
                    "summary": "缩短后的学生档案摘要。",
                    "profile_update": {
                        "summary": "缩短后的学生档案摘要。",
                        "dimensions": [{
                            "key": "family_communication",
                            "label": "家庭沟通与身心状态",
                            "items": ["家庭情况待核对"],
                        }],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    saved = service.intake.conversations.get(str(conversation["conversation_id"]))
    assert sum(
        item["adoption_state"] in {"pending", "opened"}
        for item in saved["handoffs"]
    ) == 1
    second_handoff = service.intake.open_handoff(str(second_outcome["handoff_ids"][0]))
    assert second_handoff["content"]["summary"] == "缩短后的学生档案摘要。"
    assert first_handoff["draft_id"] != second_handoff["draft_id"]


def test_focused_student_handoff_updates_the_one_current_profile_atomically(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="",
        operation_id="subject-for-current-profile",
        source_student_id="SYNTHETIC-PROFILE-001",
        display_name="合成学生甲",
        class_label="一班",
    )
    conversation = service.intake.start_conversation(
        token="",
        subject_id=str(subject["subject_id"]),
    )
    conversation = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=1,
        message="最近开始主动承担小组分工。",
        operation_id="current-profile-turn-001",
    )
    turn = conversation["turns"][0]
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到当前学生档案。",
            "clarification_questions": ["意见不同时通常会怎样？"],
            "work_items": [_work_item(
                "current-profile-item-001",
                domain="student_growth",
                mode="record",
                intent="append",
                refs=[{
                    "kind": "student",
                    "id": str(subject["subject_id"]),
                    "revision": str(subject["revision"]),
                }],
                draft={
                    "summary": "最近开始主动承担小组分工。",
                    "observed_at": "2026-08-08T09:00:00+08:00",
                    "profile_base_revision": 0,
                    "profile_update": {
                        "summary": "在小组任务中开始表现出主动承担分工的意愿。",
                        "dimensions": [{
                            "key": "peer_relationships",
                            "label": "同伴与人际关系",
                            "items": ["近期开始主动承担小组分工"],
                        }],
                        "open_questions": ["遇到意见不同时能否继续参与"],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(completed["handoffs"][0]["handoff_id"]))

    service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=str(subject["revision"]),
        operation_id="adopt-current-profile-001",
    )
    card = service.student_cards.get_card(
        token="",
        subject_id=str(subject["subject_id"]),
    )

    assert card["current_profile"]["summary"] == "在小组任务中开始表现出主动承担分工的意愿。"
    assert card["current_profile"]["revision"] == 1
    assert len(card["entries"]) == 1
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 1
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
            vmk=service.ensure_plaintext_ready(),
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
                "steps": [{
                    "key": "ordinary_support",
                    "title": "共同约定先询问再使用座位",
                    "details": "教师引导双方形成约定，不自动决定惩戒。",
                }, {
                    "key": "follow_up",
                    "title": "一周后分别回访两名学生",
                    "details": "分别确认约定执行情况和仍需支持的事项。",
                }],
            },
        )],
    }
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]), payload=payload,
    )
    # 新语义：可建单的 SOP 交接在结果落库时已自动建成正式事务。
    handoff = ready["handoffs"][0]
    assert handoff["adoption_state"] == "adopted"
    affair = service.sop.get_affair(token="", affair_id=str(handoff["affair_id"]))
    assert affair["state"] != "closed"
    assert affair.get("teacher_decision") in (None, {})
    steps = [
        *affair["current_steps"],
        *affair["completed_steps"],
        *affair["preview_steps"],
    ]
    ordinary = next(item for item in steps if item["key"] == "ordinary_support")
    follow_up = next(item for item in steps if item["key"] == "follow_up")
    route = next(item for item in steps if item["key"] == "route")
    assert ordinary["title"] == "共同约定先询问再使用座位"
    assert follow_up["title"] == "一周后分别回访两名学生"
    assert route["title"] == "由教师重新检查安全与疑似欺凌信号"


def test_teacher_selected_sop_template_no_longer_appears_as_missing_after_save_and_adoption(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-sop-template-resolved",
    )
    work_item = _work_item(
        "sop-template-resolved-001",
        domain="conflict_safety",
        mode="sop",
        intent="create",
        draft={
            "summary": "两名合成参与人发生争执，目前已经分开",
            "template_key": "",
            "participant_refs": ["synthetic-participant-a", "synthetic-participant-b"],
        },
    )
    work_item["missing_fields"] = ["请由教师选择学校流程模板"]
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "请由教师选择学校流程模板。",
            "clarification_questions": [],
            "work_items": [work_item],
        },
    )
    handoff = service.intake.open_handoff(
        str(ready["handoffs"][0]["handoff_id"])
    )

    saved = service.intake.update_draft(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        content={
            **dict(handoff["content"]),
            "template_key": "baseline.student_conflict",
        },
    )
    assert saved["missing_fields"] == []

    service.intake.adopt_handoff(
        token="",
        handoff_id=str(saved["handoff_id"]),
        draft_revision=int(saved["draft_revision"]),
        target_revision="new",
        operation_id="adopt-sop-template-resolved",
    )
    restored = service.intake.get_conversation(str(ready["conversation_id"]))
    assert restored["state"] == "teacher_confirmed"
    assert restored["handoffs"][0]["adoption_state"] == "adopted"
    assert restored["handoffs"][0]["missing_fields"] == []


def test_saving_a_review_draft_does_not_clear_student_revision_warning(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "conversation-student-revision-warning-preserved",
    )
    work_item = _work_item(
        "student-revision-warning-preserved-001",
        domain="student_support",
        mode="record",
        intent="append",
        draft={"summary": "合成待审学生支持记录"},
    )
    work_item["missing_fields"] = ["学生版本信息不一致，请重新选择"]
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "学生版本信息不一致，请重新选择。",
            "clarification_questions": [],
            "work_items": [work_item],
        },
    )
    handoff = service.intake.open_handoff(
        str(ready["handoffs"][0]["handoff_id"])
    )

    saved = service.intake.update_draft(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        content={**dict(handoff["content"]), "scene": "合成教师再次核对"},
        subject_refs=[],
    )

    assert saved["missing_fields"] == ["学生版本信息不一致，请重新选择"]


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
        content={
            "summary": "合成草稿正文已重新核对",
            "observed_at": "2026-08-05T08:00:00+08:00",
            "record_kind": "fact",
            "source": "合成教师核对",
        },
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


def test_triage_request_contract_carries_local_date_and_complete_plan_action_schema(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _conversation, _turn = _conversation_with_turn(
        service,
        "requested-opening-plan-contract",
        message=(
            "9月1日开学，前一天学生返校，需要准备班级值周、"
            "积分结算、积分兑奖和学生值日。"
        ),
    )
    task = port.prepare_calls[-1]

    request = service.intake.ai_task_adapter.build_model_request(
        task_kind=str(task["task_kind"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
    )
    joined = "\n".join(str(item["content"]) for item in request.messages)
    local_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    assert f"本机参考日期：{local_date}" in joined
    assert "Asia/Shanghai" in joined
    for field in (
        "plan_title",
        "final_deadline",
        "actions",
        "draft_action_id",
        "due_at",
        "depends_on_draft_action_ids",
    ):
        assert field in joined
    assert "缺少具体日期" in joined
    assert "不得编造" in joined


def test_triage_request_contract_requires_professional_source_and_support_follow_up(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _conversation, _turn = _conversation_with_turn(
        service,
        "requested-professional-conclusion-contract",
        message="合成学生甲已有专业机构结论，需要更新学生档案。",
    )
    task = port.prepare_calls[-1]

    request = service.intake.ai_task_adapter.build_model_request(
        task_kind=str(task["task_kind"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
    )
    system_instruction = str(request.messages[0]["content"])

    for requirement in (
        "professional_conclusion",
        "observed_at",
        "source",
        "basis",
        "专业结论来源",
        "在校支持",
    ):
        assert requirement in system_instruction


def test_professional_report_is_saved_as_bounded_review_draft_until_evidence_is_supplied(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="professional-review-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "professional-review-bounded",
        message="合成学生甲确诊有多动症和情绪障碍。",
    )
    task = port.prepare_calls[-1]
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    ref = {
        "kind": "student",
        "id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
    }

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理专业信息草稿。",
            "clarification_questions": [
                "来源？", "日期？", "依据？", "在校支持？", "是否复查？",
            ],
            "work_items": [_work_item(
                "professional-review-item",
                domain="student_support",
                mode="record",
                intent="create",
                refs=[ref],
                draft={
                    "summary": "教师报告合成学生甲已有相关专业结论。",
                    "record_kind": "professional_conclusion",
                    "source": "模型猜测的医院",
                    "basis": "模型猜测的报告",
                    "observed_at": "2026-08-10T00:00:00+08:00",
                    "current_school_support": "模型猜测的支持措施",
                    "professional_recommendations": "模型猜测的专业建议",
                    "avoidances": "模型猜测的禁忌",
                    "profile_update": "invalid-provider-shape",
                },
            )],
        },
    )

    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    content = handoff["content"]

    assert len(saved["turns"][-1]["clarification_questions"]) == 3
    assert saved["turns"][-1]["clarification_questions"] == [
        "专业结论由哪家机构或哪位专业人员出具，是否有可核对的书面材料？",
        "这份专业结论的出具日期是什么时候？",
        "学生当前在校已采用哪些支持方式，哪些有效，哪些做法需要避免？",
    ]
    assert content["record_kind"] == "reported_statement"
    assert content["source"] == "教师当前输入，采用前核对"
    assert content["basis"] == ""
    assert content["observed_at"] == ""
    assert content["current_school_support"] == ""
    assert content["professional_recommendations"] == ""
    assert content["avoidances"] == ""
    assert content["profile_base_revision"] == 0
    assert content["profile_update"]["summary"].startswith("教师转述")
    assert content["profile_update"]["dimensions"][0]["key"] == "professional_support_context"
    # 专业核对提醒不再进入 missing_fields 拦截自动并入，但仍保留在档案"仍需了解"。
    assert handoff["missing_fields"] == []
    open_questions = content["profile_update"]["open_questions"]
    assert "专业结论由哪家机构或哪位专业人员出具，是否有可核对的书面材料？" in open_questions
    assert "这份专业结论的出具日期是什么时候？" in open_questions
    assert "学生当前在校已采用哪些支持方式，哪些有效，哪些做法需要避免？" in open_questions


def test_student_record_model_missing_fields_move_to_open_questions(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="missing-fields-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "missing-fields-move",
        message="合成学生甲最近上课容易走神。",
    )
    task = port.prepare_calls[-1]
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    ref = {
        "kind": "student",
        "id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
    }

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到学生档案。",
            "clarification_questions": [],
            "work_items": [{
                "work_item_id": "missing-fields-item",
                "domain": "student_growth",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "append",
                "reason_summary": "补充学生课堂表现",
                "subject_refs": [ref],
                "time_facts": [],
                "safety_level": "normal",
                "missing_fields": ["已尝试的支持措施及效果"],
                "draft": {
                    "summary": "合成学生甲最近上课容易走神。",
                    "record_kind": "fact",
                    "source": "合成教师核对",
                    "profile_update": {
                        "summary": "合成学生甲最近上课容易走神。",
                        "dimensions": [],
                        "open_questions": ["走神是否集中在特定课程？"],
                        "support_focus": [],
                    },
                },
            }],
        },
    )

    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    # 模型自报的缺失项不拦截自动并入，转写进档案"仍需了解"。
    assert handoff["missing_fields"] == []
    open_questions = handoff["content"]["profile_update"]["open_questions"]
    assert "走神是否集中在特定课程？" in open_questions
    assert "已尝试的支持措施及效果" in open_questions


def test_complete_professional_material_uses_explicit_date_and_does_not_repeat_answered_questions(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="professional-complete-homeroom",
    )
    message = (
        "合成学生甲的结论由合成市儿童医院李医生出具，有2026年7月15日书面诊断报告。"
        "当前在校采用前排座位、任务分段和简短提醒，其中任务分段有效；"
        "报告建议固定规则并预告转换，避免公开责备。"
    )
    conversation, turn = _conversation_with_turn(
        service,
        "professional-complete-material",
        message=message,
    )
    task = port.prepare_calls[-1]
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    ref = {"kind": "student", "id": str(candidate["id"]), "revision": str(candidate["revision"])}

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已补充专业材料草稿。",
            "clarification_questions": ["材料来源？", "结论日期？", "在校支持是否有效？"],
            "work_items": [_work_item(
                "professional-complete-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[ref],
                draft={
                    "summary": "教师补充了专业材料和在校支持。",
                    "record_kind": "reported_statement",
                    "source": "教师当前输入",
                    "basis": "合成市儿童医院书面诊断报告",
                    "observed_at": "2026-08-10T00:00:00+08:00",
                    "current_school_support": "",
                    "professional_recommendations": "固定规则并预告转换",
                    "avoidances": "避免公开责备",
                    "profile_update": {
                        "summary": "已核对完整的诊断。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    saved = service.intake.get_conversation(str(conversation["conversation_id"]))
    content = handoff["content"]

    assert saved["turns"][-1]["clarification_questions"] == []
    assert content["record_kind"] == "professional_conclusion"
    assert content["source"] == "教师补充的专业书面材料，采用前核对"
    assert content["observed_at"] == "2026-07-15"
    assert content["current_school_support"].startswith("当前在校采用前排座位")
    assert "正式采用前仍由教师核对原始材料" in content["profile_update"]["summary"]
    assert "已核对完整" not in content["profile_update"]["summary"]

    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=str(handoff["subject_refs"][0]["revision"]),
        operation_id="adopt-professional-complete-material",
    )
    assert receipt["formal_object_type"] == "student_record"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 1
        subject_id = str(
            connection.execute(
                "SELECT subject_id FROM student_subject_links LIMIT 1"
            ).fetchone()[0]
        )
        observed_at = str(
            connection.execute(
                "SELECT observed_at FROM support_records LIMIT 1"
            ).fetchone()[0]
        )
    card = service.student_cards.get_card(token="", subject_id=subject_id)
    saved_record = card["existing_records"][0]
    assert saved_record["record_kind"] == "professional_conclusion"
    assert datetime.fromisoformat(observed_at).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat() == "2026-07-15"
    assert card["current_profile"]["revision"] == 1


def test_sop_adoption_drafts_profile_updates_until_teacher_confirms(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    # 稳定学籍标识下，既有档案按「班级|学号」挂靠，与花名册候选一致。
    subjects = [
        service.support.create_subject_for_roster_source(
            token="",
            operation_id=f"requested-conflict-subject-{index}",
            source_student_id=str(index),
            legacy_student_code=code,
            display_name=name,
            class_label="一班",
        )
        for index, (code, name) in enumerate(
            (("A001", "合成学生甲"), ("B001", "合成学生乙")), start=1
        )
    ]
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="requested-conflict-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "requested-conflict-profile-adoption",
        message="合成学生甲和合成学生乙今天在信息课发生矛盾。",
    )
    task = port.prepare_calls[-1]
    candidates = service.class_roster.ai_candidates(token="", class_label="一班")
    refs = [
        {
            "kind": "student",
            "id": str(candidate["id"]),
            "revision": str(candidate["revision"]),
        }
        for candidate in candidates
    ]
    profile_updates = [
        {
            "subject_ref": ref,
            "include": True,
            "record_kind": "reported_statement",
            "source": "合成教师补充",
            "observed_at": "2026-08-10T10:00:00+08:00",
            "record_summary": f"{subject['display_name']}参与了一次待继续核对的同伴矛盾。",
            "profile_base_revision": 0,
            "profile_update": {
                "summary": f"正在持续了解{subject['display_name']}处理同伴分歧时需要的支持。",
                "dimensions": [],
                "open_questions": ["后续是否能在教师支持下清楚表达经过？"],
                "support_focus": [{
                    "key": "peer_conflict_follow_up",
                    "title": "同伴矛盾后的支持",
                    "need": "继续核对事实并观察后续互动",
                    "effective_methods": [],
                    "next_actions": ["分别听取陈述", "安排后续观察"],
                }],
            },
        }
        for subject, ref in zip(subjects, refs, strict=True)
    ]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成待核对的冲突处理草稿。",
            "clarification_questions": ["双方目前是否已经分开且无人受伤？"],
            "work_items": [_work_item(
                "requested-conflict-profile-item",
                domain="conflict_safety",
                mode="sop",
                intent="follow_up",
                refs=refs,
                draft={
                    "summary": "两名合成学生发生矛盾，事实仍待教师核对。",
                    "template_key": "baseline.student_conflict",
                    "student_profile_updates": profile_updates,
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    # 新语义：可建单的 SOP 交接在结果落库时已自动建成正式事务，
    # 建单只生成逐人待确认的档案草稿，不直接写支持记录与档案。
    assert handoff["adoption_state"] == "adopted"
    affair_id = str(handoff["affair_id"])
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    drafts = list(affair["profile_update_drafts"])
    assert len(drafts) == 2
    assert all(draft["state"] == "pending" for draft in drafts)
    assert {
        str(draft["subject_id"]) for draft in drafts
    } == {str(subject["subject_id"]) for subject in subjects}
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 0

    # 教师逐人确认后，支持记录与当前档案才真正写入。
    for index, draft in enumerate(drafts, start=1):
        service.sop.confirm_profile_update_draft(
            token="",
            affair_id=affair_id,
            draft_id=str(draft["draft_id"]),
            operation_id=f"confirm-requested-conflict-profile-{index}",
        )

    cards = [
        service.student_cards.get_card(token="", subject_id=str(subject["subject_id"]))
        for subject in subjects
    ]
    assert [card["current_profile"]["revision"] for card in cards] == [1, 1]
    assert all("同伴分歧" in card["current_profile"]["summary"] for card in cards)
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM student_card_entries").fetchone()[0] == 2


def test_denied_professional_diagnosis_does_not_trigger_professional_review_flow(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="professional-denied-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "professional-denied-turn",
        message="合成学生甲没有专业诊断。补充：他篮球打得特别好，是班级篮球队主力。",
    )
    task = port.prepare_calls[-1]
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    ref = {"kind": "student", "id": str(candidate["id"]), "revision": str(candidate["revision"])}

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已补充合成学生甲的兴趣优势。",
            "clarification_questions": ["他在哪些课堂上更能坐得住？"],
            "work_items": [_work_item(
                "professional-denied-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[ref],
                draft={
                    "summary": "教师明确合成学生甲没有专业诊断，补充其篮球特长。",
                    "profile_update": {
                        "summary": "合成学生甲无专业诊断，篮球特长突出。",
                        "dimensions": [{
                            "key": "interests_strengths",
                            "label": "兴趣与优势",
                            "items": ["篮球打得特别好，是班级篮球队主力"],
                        }],
                        "open_questions": ["他在哪些课堂上更能坐得住？"],
                        "support_focus": [{
                            "key": "strength_channel",
                            "title": "优势引导",
                            "need": "通过篮球优势建立自信与班级认同感",
                            "effective_methods": [],
                            "next_actions": ["安排其承担篮球活动组织任务"],
                        }],
                    },
                },
            )],
        },
    )
    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    content = handoff["content"]

    assert content["profile_update"]["summary"] == "合成学生甲无专业诊断，篮球特长突出。"
    assert content["profile_update"]["dimensions"][0]["key"] == "interests_strengths"
    assert not any(
        "专业结论" in str(field) or "书面依据" in str(field)
        for field in handoff.get("missing_fields") or []
    )
    assert not any(
        "专业结论" in str(question) or "书面材料" in str(question)
        for question in saved["turns"][-1]["clarification_questions"]
    )


def test_near_miss_domain_alias_does_not_fail_the_whole_turn(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="domain-alias-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "domain-alias-turn",
        message="合成学生甲和合成学生乙课间发生了矛盾。",
    )
    task = port.prepare_calls[-1]
    refs = [
        {
            "kind": "student",
            "id": str(candidate["id"]),
            "revision": str(candidate["revision"]),
        }
        for candidate in service.class_roster.ai_candidates(token="", class_label="一班")
    ]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已按冲突处理流程整理。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "domain-alias-item",
                domain="conflict_support",
                mode="sop",
                intent="follow_up",
                refs=refs,
                draft={
                    "summary": "两名合成学生课间矛盾，待教师核对。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )
    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    assert handoff["domain"] == "conflict_safety"
    assert handoff["handling_mode"] == "sop"
    assert handoff["content"]["template_key"] == "baseline.student_conflict"
    assert saved["state"] != "failed"


def test_conflict_questions_skip_separation_and_injury_already_reported(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="conflict-answered-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "conflict-answered-turn",
        message=(
            "合成学生甲和合成学生乙打架，我先让他们分开了，"
            "合成学生乙膝盖擦破已经去校医室处理了。"
        ),
    )
    task = port.prepare_calls[-1]
    refs = [
        {
            "kind": "student",
            "id": str(candidate["id"]),
            "revision": str(candidate["revision"]),
        }
        for candidate in service.class_roster.ai_candidates(token="", class_label="一班")
    ]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已记录冲突并启动处理流程。",
            "clarification_questions": ["矛盾的起因是什么？"],
            "work_items": [_work_item(
                "conflict-answered-item",
                domain="conflict_safety",
                mode="sop",
                intent="create",
                refs=refs,
                draft={
                    "summary": "两名合成学生冲突，已分开，伤者已就医。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )
    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    questions = saved["turns"][-1]["clarification_questions"]

    assert service.intake.open_handoff(str(outcome["handoff_ids"][0]))["handling_mode"] == "sop"
    assert "矛盾的起因是什么？" in questions
    assert not any("已经分开" in str(question) for question in questions)
    assert not any("受伤" in str(question) or "校医" in str(question) for question in questions)


def test_conflict_profile_update_with_bare_string_dimensions_does_not_fail_turn(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="bare-dim-homeroom",
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "bare-dim-turn",
        message="合成学生甲和合成学生乙课间发生了矛盾。",
    )
    task = port.prepare_calls[-1]
    refs = [
        {
            "kind": "student",
            "id": str(candidate["id"]),
            "revision": str(candidate["revision"]),
        }
        for candidate in service.class_roster.ai_candidates(token="", class_label="一班")
    ]
    profile_updates = [
        {
            "subject_ref": ref,
            "include": True,
            "record_kind": "reported_statement",
            "source": "合成教师输入",
            "observed_at": "2026-08-10T10:00:00+08:00",
            "record_summary": "参与课间矛盾，待核对。",
            "profile_base_revision": 0,
            "profile_update": {
                "summary": "冲突情况待教师核对。",
                "dimensions": [
                    "personality_behavior",
                    {"key": "health", "label": "健康状态", "items": ["膝盖擦伤已处理"]},
                    {"key": "peer_relationships", "label": "同伴关系", "items": []},
                ],
                "open_questions": ["冲突起因是什么？"],
                "support_focus": [],
            },
        }
        for ref in refs
    ]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成冲突处理草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "bare-dim-item",
                domain="conflict_safety",
                mode="sop",
                intent="create",
                refs=refs,
                draft={
                    "summary": "两名合成学生课间矛盾。",
                    "template_key": "baseline.student_conflict",
                    "student_profile_updates": profile_updates,
                },
            )],
        },
    )
    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    assert saved["state"] != "failed"
    updates = handoff["content"]["student_profile_updates"]
    assert len(updates) == 2
    for update in updates:
        dims = update["profile_update"]["dimensions"]
        assert dims == [{"key": "health", "label": "健康状态", "items": ["膝盖擦伤已处理"]}]


def test_plan_draft_keeps_candidates_and_anchors_final_deadline_to_teacher_date(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "plan-anchor-turn",
        message="9月1日开学，要准备值周生安排和积分结算。",
    )
    task = port.prepare_calls[-1]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成开学筹备计划草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "plan-anchor-item",
                domain="class_operations",
                mode="plan_calendar",
                intent="plan",
                draft={
                    "plan_title": "开学筹备计划",
                    "summary": "围绕开学完成筹备任务。",
                    "final_deadline": "2026-08-25T18:00:00+08:00",
                    "candidates": [
                        {"label": "值周生安排", "reason": "开学初需要明确值周", "suggested": True},
                        {"label": "积分结算", "reason": "上学期积分需结算", "suggested": True},
                        {"label": "学生奖品采购", "reason": "积分兑换需要奖品", "suggested": False},
                        {"label": "x" * 300, "reason": "超长条目", "suggested": True},
                        "纯字符串候选",
                    ],
                    "actions": [
                        {
                            "draft_action_id": "action-1",
                            "title": "结算上学期积分",
                            "details": "核对积分数据",
                            "due_at": "2026-08-18T18:00:00+08:00",
                            "depends_on_draft_action_ids": [],
                        },
                        {
                            "draft_action_id": "action-2",
                            "title": "公示积分结果",
                            "details": "向学生公示",
                            "due_at": "2026-09-05T18:00:00+08:00",
                            "depends_on_draft_action_ids": ["action-1"],
                        },
                    ],
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    content = handoff["content"]

    assert content["final_deadline"].startswith("2026-09-01")
    assert any(
        "2026-09-01" in str(fact.get("text")) and "校正" in str(fact.get("text"))
        for fact in content["time_facts"]
        if isinstance(fact, dict)
    )
    due_dates = [action["due_at"] for action in content["actions"]]
    assert due_dates[0].startswith("2026-08-18")
    assert due_dates[1].startswith("2026-09-01")
    labels = [candidate["label"] for candidate in content["candidates"]]
    assert labels == ["值周生安排", "积分结算", "学生奖品采购"]
    assert content["candidates"][0]["suggested"] is True
    assert content["candidates"][2]["suggested"] is False


def test_plan_draft_with_multiple_teacher_dates_keeps_model_deadline(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "plan-multi-date-turn",
        message="9月1日开学，9月5日前要收齐回执。",
    )
    task = port.prepare_calls[-1]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成计划草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "plan-multi-date-item",
                domain="class_operations",
                mode="plan_calendar",
                intent="plan",
                draft={
                    "plan_title": "开学与回执计划",
                    "summary": "开学筹备与回执收集。",
                    "final_deadline": "2026-09-05T18:00:00+08:00",
                    "actions": [],
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))

    assert handoff["content"]["final_deadline"] == "2026-09-05T18:00:00+08:00"


def _service_with_two_student_roster(
    tmp_path: Path,
) -> tuple[VaultService, FakeWorkspaceAITaskPort]:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [(1, "A001", "合成学生甲", "一班"), (2, "A002", "合成学生丙", "一班")],
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
    return VaultService(context, workspace_ai_task_port=port), port


def _set_homeroom(service: VaultService, operation_id: str) -> None:
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=int(preference["revision"]),
        expected_source_revision=str(preference["source_revision"]),
        operation_id=operation_id,
    )


def _prompt_candidate_payload(
    service: VaultService,
    prepare_call: dict[str, object],
) -> list[dict[str, object]]:
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=prepare_call["source_ref"],
        context_refs=prepare_call["context_refs"],
    )
    candidate_message = next(
        str(message["content"])
        for message in request.messages
        if "当前班学生候选" in str(message["content"])
    )
    return json.loads(candidate_message.split("：", 1)[1])


def test_prompt_candidates_keep_only_named_or_referenced_students() -> None:
    candidates = [
        {"id": "a" * 64, "revision": "r1", "display_name": "合成学生甲", "class_label": "一班"},
        {"id": "b" * 64, "revision": "r2", "display_name": "合成学生甲", "class_label": "一班"},
        {"id": "c" * 64, "revision": "r3", "display_name": "合成学生丙", "class_label": "一班"},
    ]

    named = _prompt_candidates(
        candidates,
        conversation={"turns": [{"teacher_message": "合成学生甲今天主动帮助同学。"}]},
        previous_handoff=None,
    )
    assert [item["id"] for item in named] == ["a" * 64, "b" * 64]

    referenced = _prompt_candidates(
        candidates,
        conversation={"turns": [{"teacher_message": "他这周又进步了。"}]},
        previous_handoff={
            "subject_refs": [{"kind": "student", "id": "c" * 64, "revision": "r3"}]
        },
    )
    assert [item["id"] for item in referenced] == ["c" * 64]

    fallback = _prompt_candidates(
        candidates,
        conversation={"turns": [{"teacher_message": "班会通知已发。"}]},
        previous_handoff=None,
    )
    assert fallback == candidates


def test_triage_prompt_trims_candidates_to_named_student(tmp_path: Path) -> None:
    service, port = _service_with_two_student_roster(tmp_path)
    _set_homeroom(service, "candidate-trim-homeroom")
    _conversation, _turn = _conversation_with_turn(
        service,
        "candidate-trim-turn",
        message="合成学生甲今天主动帮助同学。",
    )

    payload = _prompt_candidate_payload(service, port.prepare_calls[-1])

    assert [item["display_name"] for item in payload] == ["合成学生甲"]


def test_triage_prompt_keeps_full_candidates_when_nobody_named(
    tmp_path: Path,
) -> None:
    service, port = _service_with_two_student_roster(tmp_path)
    _set_homeroom(service, "candidate-full-homeroom")
    _conversation, _turn = _conversation_with_turn(
        service,
        "candidate-full-turn",
        message="班会通知已发。",
    )

    payload = _prompt_candidate_payload(service, port.prepare_calls[-1])

    assert {item["display_name"] for item in payload} == {"合成学生甲", "合成学生丙"}


def test_triage_prompt_keeps_previous_draft_referenced_student(
    tmp_path: Path,
) -> None:
    service, port = _service_with_two_student_roster(tmp_path)
    _set_homeroom(service, "candidate-continue-homeroom")
    selected = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == "合成学生甲"
    )
    conversation, first_turn = _conversation_with_turn(
        service,
        "candidate-continue-first",
        message="合成学生甲今天主动帮助同学。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到当前学生档案。",
            "clarification_questions": ["还需要补充什么？"],
            "work_items": [_work_item(
                "candidate-continue-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[{
                    "kind": "student",
                    "id": str(selected["id"]),
                    "revision": str(selected["revision"]),
                }],
                draft={
                    "summary": "第一轮学生档案摘要。",
                    "profile_update": {
                        "summary": "第一轮学生档案摘要。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    ready = service.intake.conversations.get(str(conversation["conversation_id"]))
    service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="再补充一点这周的进展。",
        operation_id="candidate-continue-second",
    )

    second_task = port.prepare_calls[-1]
    payload = _prompt_candidate_payload(service, second_task)
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
    )

    assert [item["display_name"] for item in payload] == ["合成学生甲"]
    assert any(
        "上一轮待核对草稿" in str(message["content"])
        for message in request.messages
    )


def test_plan_handoff_with_unanswered_clarifications_is_not_auto_opened(
    tmp_path: Path,
) -> None:
    service, _port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "auto-open-pending-questions",
        message="合成计划事务",
    )
    payload = _triage()
    payload["clarification_questions"] = ["具体是哪一天？"]
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=payload,
    )

    handoff = completed["handoffs"][0]
    assert handoff["destination_key"] == "class_teacher.plan.calendar"
    assert handoff["auto_open_allowed"] is False


def test_plan_handoff_without_pending_questions_is_auto_open_allowed(
    tmp_path: Path,
) -> None:
    service, _port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "auto-open-no-questions",
        message="合成计划事务",
    )
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=_triage(),
    )

    handoff = completed["handoffs"][0]
    assert handoff["destination_key"] == "class_teacher.plan.calendar"
    assert handoff["auto_open_allowed"] is True


def test_answered_clarifications_no_longer_block_auto_open(
    tmp_path: Path,
) -> None:
    service, _port = _service(tmp_path)
    conversation, turn = _conversation_with_turn(
        service,
        "auto-open-answered-questions",
        message="合成计划事务",
    )
    payload = _triage()
    payload["clarification_questions"] = ["具体是哪一天？"]
    completed = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]),
        task_id=str(turn["task_id"]),
        payload=payload,
    )
    assert completed["handoffs"][0]["auto_open_allowed"] is False

    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(completed["revision"]),
        message="下周一放学前。",
        operation_id="auto-open-answered-follow-up",
    )

    handoffs = {
        str(item["turn_id"]): item for item in continued["handoffs"]
    }
    assert handoffs[str(turn["turn_id"])]["auto_open_allowed"] is True


def test_adopting_draft_with_legacy_hex_ref_marks_stale(tmp_path: Path) -> None:
    """兼容输入：旧格式 64 位十六进制临时编号按「引用已失效」优雅报错。"""
    service, _ = _service(tmp_path)
    subject = service.support.create_subject(
        token="", operation_id="subject-for-legacy-ref", source_student_id="SYNTHETIC-LEGACY",
        display_name="合成学生旧编号", class_label="一班",
    )
    _conversation, turn = _conversation_with_turn(service, "conversation-legacy-ref")
    ready = service.intake.apply_triage_result(
        turn_id=str(turn["turn_id"]), task_id=str(turn["task_id"]),
        payload=_triage(subject_id=str(subject["subject_id"])),
    )
    handoff = service.intake.open_handoff(str(ready["handoffs"][0]["handoff_id"]))
    rebound = service.intake.update_draft(
        handoff_id=str(handoff["handoff_id"]),
        expected_revision=int(handoff["draft_revision"]),
        content={
            "summary": "合成草稿正文已重新核对",
            "observed_at": "2026-08-05T08:00:00+08:00",
            "record_kind": "fact",
            "source": "合成教师核对",
        },
        subject_refs=[{"kind": "student", "id": "a" * 64, "revision": "1"}],
    )
    with pytest.raises(VaultError, match="学生引用已失效") as excinfo:
        service.intake.adopt_handoff(
            token="", handoff_id=str(rebound["handoff_id"]),
            draft_revision=int(rebound["draft_revision"]),
            target_revision="1", operation_id="adopt-legacy-ref",
        )
    assert excinfo.value.code == "class_teacher_subject_ref_invalid"
    assert excinfo.value.status_code == 409
    restored = service.intake.get_conversation(str(ready["conversation_id"]))
    assert restored["handoffs"][0]["adoption_state"] == "stale"
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM support_records").fetchone()[0] == 0


def _conflict_roster_service(
    tmp_path: Path,
) -> tuple[VaultService, FakeWorkspaceAITaskPort, list[dict[str, object]]]:
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    subjects = [
        service.support.create_subject_for_roster_source(
            token="",
            operation_id=f"auto-adopt-subject-{index}",
            source_student_id=str(index),
            legacy_student_code=code,
            display_name=name,
            class_label="一班",
        )
        for index, (code, name) in enumerate(
            (("A001", "合成学生甲"), ("B001", "合成学生乙")), start=1
        )
    ]
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id="auto-adopt-homeroom",
    )
    return service, port, subjects


def _conflict_roster_refs(service: VaultService) -> list[dict[str, str]]:
    return [
        {
            "kind": "student",
            "id": str(candidate["id"]),
            "revision": str(candidate["revision"]),
        }
        for candidate in service.class_roster.ai_candidates(
            token="", class_label="一班"
        )
    ]


def _auto_adoptable_sop_result(
    refs: list[dict[str, str]],
    *,
    template_key: str = "baseline.student_conflict",
) -> dict[str, object]:
    return {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已按冲突流程建立事务。",
        "clarification_questions": [],
        "work_items": [_work_item(
            "auto-adopt-sop-item",
            domain="conflict_safety",
            mode="sop",
            intent="create",
            refs=refs,
            draft={
                "summary": "两名合成学生因座位起了争执，目前已分开且无人受伤。",
                "template_key": template_key,
                "to_verify": ["双方分别陈述的经过仍待核对"],
                "observed_at": "2026-08-10T10:00:00+08:00",
            },
        )],
    }


def test_sop_handoff_is_auto_adopted_into_an_affair(tmp_path: Path) -> None:
    service, port, subjects = _conflict_roster_service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "auto-adopt-first-turn",
        message="合成学生甲和合成学生乙已经分开，无人受伤，刚才因座位起了争执。",
    )
    task = port.prepare_calls[-1]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result=_auto_adoptable_sop_result(_conflict_roster_refs(service)),
    )

    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = next(
        item for item in saved["handoffs"]
        if str(item["handoff_id"]) == str(outcome["handoff_ids"][0])
    )
    assert handoff["adoption_state"] == "adopted"
    affair_id = str(handoff["affair_id"] or "")
    assert affair_id
    assert saved["state"] == "teacher_confirmed"

    affair = service.sop.get_affair(token="", affair_id=affair_id)
    assert affair["state"] == "active"
    assert "双方分别陈述的经过仍待核对" in list(affair["to_verify"])
    assert {
        str(item["subject_id"]) for item in affair["participants"] if item["subject_id"]
    } == {str(subject["subject_id"]) for subject in subjects}
    assert affair["profile_update_drafts"] == []

    detail = service.intake.open_handoff(str(handoff["handoff_id"]))
    assert detail["affair_id"] == affair_id

    # 幂等：再次触发自动建单与重复采用都指向同一事务。
    service.intake._auto_adopt_sop_handoffs(
        conversation_id=str(saved["conversation_id"]),
        turn_id=str(turn["turn_id"]),
    )
    replay = service.intake.adoption.adopt(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision="auto",
        operation_id=f"auto-adopt-{handoff['handoff_id']}",
    )
    assert replay["replayed"] is True
    assert str(replay["formal_object_id"]) == affair_id
    assert len(service.sop.list_affairs(token="")["items"]) == 1


def test_sop_auto_adoption_falls_back_to_pending_draft_on_failure(
    tmp_path: Path,
) -> None:
    service, _port = _service(tmp_path)
    conversation = service.intake.start_conversation()
    payload = _audio_sop_payload(summary="两名合成学生发生争执，目前已分开。")
    payload["work_items"][0]["draft"]["template_key"] = "baseline.not_exists"

    saved = service.intake.conversations.append_resolved_audio_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        transcript="合成学生甲和合成学生乙已经分开，无人受伤。",
        operation_id="audio-auto-adopt-bad-template",
        payload=payload,
    )

    # 模板无法解析时回退为待确认草稿，不影响本轮落库。
    assert len(saved["handoffs"]) == 1
    handoff = saved["handoffs"][0]
    assert handoff["adoption_state"] in {"pending", "opened"}
    assert handoff["affair_id"] is None
    assert service.sop.list_affairs(token="")["items"] == []


def test_sop_auto_adoption_is_skipped_without_template_or_participants(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _conversation, turn = _conversation_with_turn(
        service,
        "auto-adopt-skip-turn",
        message="两名合成学生已经分开，无人受伤，刚才起了争执。",
    )
    task = port.prepare_calls[-1]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "模板与参与人未定，先保留草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "auto-adopt-skip-item",
                domain="conflict_safety",
                mode="sop",
                intent="create",
                draft={
                    "summary": "两名合成学生起了争执，模板与参与人未定。",
                    "template_key": "",
                },
            )],
        },
    )

    saved = service.intake.get_conversation(str(turn["conversation_id"]))
    handoff = next(
        item for item in saved["handoffs"]
        if str(item["handoff_id"]) == str(outcome["handoff_ids"][0])
    )
    assert handoff["adoption_state"] == "pending"
    assert handoff["affair_id"] is None
    assert service.sop.list_affairs(token="")["items"] == []


def _audio_sop_payload(*, summary: str) -> dict[str, object]:
    return {
        "contract_version": "class_teacher_triage.v1",
        "assistant_message": "已按语音整理冲突流程。",
        "clarification_questions": [],
        "work_items": [{
            "work_item_id": "audio-sop-item-001",
            "domain": "conflict_safety",
            "primary_mode": "sop",
            "secondary_modes": [],
            "intent": "create",
            "reason_summary": "合成语音冲突分诊",
            "subject_refs": [],
            "time_facts": [],
            "safety_level": "teacher_review_required",
            "missing_fields": [],
            "draft": {
                "summary": summary,
                "template_key": "baseline.student_conflict",
                "participant_refs": ["合成学生甲", "合成学生乙"],
            },
        }],
    }


def test_audio_turn_auto_adopts_sop_handoff(tmp_path: Path) -> None:
    service, _port = _service(tmp_path)
    conversation = service.intake.start_conversation()

    saved = service.intake.conversations.append_resolved_audio_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        transcript="合成学生甲和合成学生乙已经分开，无人受伤。",
        operation_id="audio-auto-adopt-001",
        payload=_audio_sop_payload(summary="两名合成学生发生争执，目前已分开。"),
    )

    assert len(saved["handoffs"]) == 1
    handoff = saved["handoffs"][0]
    assert handoff["adoption_state"] == "adopted"
    affair_id = str(handoff["affair_id"] or "")
    assert affair_id
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    assert {str(item["reference"]) for item in affair["participants"]} == {
        "合成学生甲",
        "合成学生乙",
    }


def test_follow_up_turn_diverts_to_flow_revision_without_second_affair(
    tmp_path: Path,
) -> None:
    service, port, subjects = _conflict_roster_service(tmp_path)
    conversation, first_turn = _conversation_with_turn(
        service,
        "divert-first-turn",
        message="合成学生甲和合成学生乙已经分开，无人受伤，刚才因座位起了争执。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result=_auto_adoptable_sop_result(_conflict_roster_refs(service)),
    )
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))
    affair_id = str(ready["handoffs"][0]["affair_id"])

    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="双方已经约定先询问再使用座位。",
        operation_id="divert-second-turn",
    )

    # 补充轮照常分诊；分诊结果落库时冲突类补充改道既有事务的流程修订。
    triage_task = port.prepare_calls[-1]
    assert triage_task["task_kind"] == "class_teacher.intake_triage"
    triage_turn = continued["turns"][-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(triage_turn["task_id"]),
        source_ref=triage_task["source_ref"],
        context_refs=triage_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理本轮补充。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "divert-second-item",
                domain="conflict_safety",
                mode="sop",
                intent="append",
                refs=_conflict_roster_refs(service),
                draft={
                    "summary": "双方已经约定先询问再使用座位。",
                    "template_key": "baseline.student_conflict",
                },
            )],
        },
    )

    # 不再建第二个交接，也不产生第二个事务；本轮改道既有事务的流程修订。
    continued = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert len(continued["handoffs"]) == 1
    assert continued["handoffs"][0]["adoption_state"] == "adopted"
    assert len(service.sop.list_affairs(token="")["items"]) == 1
    second_task = port.prepare_calls[-1]
    assert second_task["task_kind"] == "class_teacher.affair_flow_revision"
    assert str(second_task["source_ref"]["id"]) == affair_id
    second_turn = continued["turns"][-1]
    assert str(second_turn["task_id"] or "")
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    sync_requests = list(affair.get("sync_requests") or [])
    assert [str(item["text"]) for item in sync_requests] == [
        "双方已经约定先询问再使用座位。"
    ]

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(second_turn["task_id"]),
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
        result={
            "contract_version": "class_teacher_affair_flow_revision.v1",
            "assistant_message": "已根据补充更新后续步骤。",
            "items": [{
                "item_id": "rev-1",
                "kind": "note",
                "text": "本周课间继续留意两人互动",
                "reason": "预防升级",
            }],
            "profile_update_suggestions": [{
                "suggestion_id": "prof-1",
                "subject_id": str(subjects[0]["subject_id"]),
                "record_summary": "合成学生甲在座位争执后已能约定先询问再使用。",
                "profile_update": {
                    "summary": "合成学生甲在同伴分歧中正学习先询问再使用。",
                    "dimensions": [],
                    "open_questions": [],
                    "support_focus": [],
                },
            }],
        },
    )

    assert outcome["proposal_ref"]["kind"] == "flow_revision"
    saved = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert saved["turns"][-1]["assistant_message"] == "已根据补充更新后续步骤。"
    assert saved["turns"][-1]["task_state"] == "response_persisted"
    assert saved["state"] == "teacher_confirmed"

    affair = service.sop.get_affair(token="", affair_id=affair_id)
    drafts = list(affair["profile_update_drafts"])
    assert len(drafts) == 1
    assert drafts[0]["state"] == "pending"
    assert str(drafts[0]["subject_id"]) == str(subjects[0]["subject_id"])
    sync_id = str(sync_requests[0]["sync_id"])
    entry = service.sop.flow_revision_for_sync(
        token="", affair_id=affair_id, sync_id=sync_id
    )
    assert entry is not None
    assert list(entry["profile_update_draft_ids"]) == [str(drafts[0]["draft_id"])]


def test_audio_follow_up_turn_diverts_to_flow_revision(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    conversation = service.intake.start_conversation()
    first = service.intake.conversations.append_resolved_audio_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(conversation["revision"]),
        transcript="合成学生甲和合成学生乙已经分开，无人受伤。",
        operation_id="audio-divert-001",
        payload=_audio_sop_payload(summary="两名合成学生发生争执，目前已分开。"),
    )
    affair_id = str(first["handoffs"][0]["affair_id"])

    second = service.intake.conversations.append_resolved_audio_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(first["revision"]),
        transcript="双方已经和好，约定轮流使用座位。",
        operation_id="audio-divert-002",
        payload=_audio_sop_payload(summary="双方已和好，约定轮流使用座位。"),
    )

    # 冲突类补充不再产生第二份 SOP 交接，改为既有事务的一次同步修订。
    assert len(second["handoffs"]) == 1
    assert port.prepare_calls[-1]["task_kind"] == "class_teacher.affair_flow_revision"
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    sync_requests = list(affair.get("sync_requests") or [])
    assert [str(item["text"]) for item in sync_requests] == [
        "双方已经和好，约定轮流使用座位。"
    ]
    assert len(service.sop.list_affairs(token="")["items"]) == 1


def test_mixed_follow_up_turn_diverts_only_the_sop_item(tmp_path: Path) -> None:
    service, port, _subjects = _conflict_roster_service(tmp_path)
    conversation, first_turn = _conversation_with_turn(
        service,
        "divert-mixed-first-turn",
        message="合成学生甲和合成学生乙已经分开，无人受伤，刚才因座位起了争执。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result=_auto_adoptable_sop_result(_conflict_roster_refs(service)),
    )
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))
    affair_id = str(ready["handoffs"][0]["affair_id"])

    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="双方已约定先询问再使用座位；另外合成学生甲今天主动帮助同学。",
        operation_id="divert-mixed-second-turn",
    )
    triage_turn = continued["turns"][-1]
    triage_task = port.prepare_calls[-1]
    assert triage_task["task_kind"] == "class_teacher.intake_triage"
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(triage_turn["task_id"]),
        source_ref=triage_task["source_ref"],
        context_refs=triage_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理一条冲突补充和一条学生记录。",
            "clarification_questions": [],
            "work_items": [
                _work_item(
                    "divert-mixed-sop-item",
                    domain="conflict_safety",
                    mode="sop",
                    intent="append",
                    refs=_conflict_roster_refs(service),
                    draft={
                        "summary": "双方已约定先询问再使用座位。",
                        "template_key": "baseline.student_conflict",
                    },
                ),
                _work_item(
                    "divert-mixed-record-item",
                    domain="student_growth",
                    mode="record",
                    intent="append",
                    refs=[_conflict_roster_refs(service)[0]],
                    draft={
                        "summary": "合成学生甲今天主动帮助同学。",
                        "profile_update": {
                            "summary": "合成学生甲乐于帮助同学。",
                            "dimensions": [],
                            "open_questions": [],
                            "support_focus": [],
                        },
                    },
                ),
            ],
        },
    )

    saved = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert len(saved["handoffs"]) == 2
    record_handoff = next(
        item for item in saved["handoffs"]
        if str(item["destination_key"]) == "class_teacher.student.record"
    )
    assert record_handoff["adoption_state"] == "pending"
    assert str(record_handoff["turn_id"]) == str(triage_turn["turn_id"])
    assert saved["state"] == "handoff_ready"
    revision_task = port.prepare_calls[-1]
    assert revision_task["task_kind"] == "class_teacher.affair_flow_revision"
    assert str(revision_task["source_ref"]["id"]) == affair_id
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    assert [str(item["text"]) for item in list(affair.get("sync_requests") or [])] == [
        "双方已约定先询问再使用座位；另外合成学生甲今天主动帮助同学。"
    ]
    assert len(service.sop.list_affairs(token="")["items"]) == 1


def test_record_only_follow_up_turn_still_creates_a_record_handoff(
    tmp_path: Path,
) -> None:
    service, port, _subjects = _conflict_roster_service(tmp_path)
    conversation, first_turn = _conversation_with_turn(
        service,
        "divert-record-first-turn",
        message="合成学生甲和合成学生乙已经分开，无人受伤，刚才因座位起了争执。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result=_auto_adoptable_sop_result(_conflict_roster_refs(service)),
    )
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))

    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="合成学生乙今天按时完成了值日。",
        operation_id="divert-record-second-turn",
    )
    triage_turn = continued["turns"][-1]
    triage_task = port.prepare_calls[-1]
    assert triage_task["task_kind"] == "class_teacher.intake_triage"
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(triage_turn["task_id"]),
        source_ref=triage_task["source_ref"],
        context_refs=triage_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理一条学生记录。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "divert-record-only-item",
                domain="student_growth",
                mode="record",
                intent="append",
                refs=[_conflict_roster_refs(service)[1]],
                draft={
                    "summary": "合成学生乙今天按时完成了值日。",
                    "profile_update": {
                        "summary": "合成学生乙值日负责。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )

    # 纯记录诉求不触发流程修订，照常建 record 交接。
    assert port.prepare_calls[-1] is triage_task
    saved = service.intake.get_conversation(str(conversation["conversation_id"]))
    assert len(saved["handoffs"]) == 2
    record_handoff = saved["handoffs"][-1]
    assert str(record_handoff["destination_key"]) == "class_teacher.student.record"
    assert record_handoff["adoption_state"] == "pending"
    assert saved["state"] == "handoff_ready"
    affair_id = str(ready["handoffs"][0]["affair_id"])
    affair = service.sop.get_affair(token="", affair_id=affair_id)
    assert list(affair.get("sync_requests") or []) == []
    assert len(service.sop.list_affairs(token="")["items"]) == 1
