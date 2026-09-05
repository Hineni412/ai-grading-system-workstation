from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routers import ai_diagnostics as diagnostics_router
from backend.llm import diagnostics as diagnostics_module
from backend.llm.diagnostics import JsonlDiagnosticJournal


def _record_workspace_call(
    journal: JsonlDiagnosticJournal,
    *,
    operation_id: str,
    module: str,
    task_kind: str,
    request_text: str,
    response_text: str,
) -> str:
    sink = journal.for_workspace(
        workspace_module=module,
        workspace_task_kind=task_kind,
    )
    common = {
        "operation_id": operation_id,
        "request_id": operation_id,
        "attempt": 1,
        "request_kind": "workspace",
        "protocol": "chat_completions",
        "model": "synthetic-model",
        "endpoint_host": "model.invalid",
    }
    sink.record_request(
        **common,
        kwargs={"messages": [{"role": "user", "content": request_text}]},
        retry_limit=0,
        retry_index=0,
        timeout_seconds=30,
    )
    sink.record_response(
        **common,
        response={
            "choices": [{"message": {"content": response_text}}],
        },
        elapsed_ms=10,
    )
    call = journal.list_calls(
        limit=10,
        workspace_module=module,
    )["items"][0]
    return str(call["call_id"])


def test_clear_class_teacher_diagnostics_preserves_other_workspace_calls(
    tmp_path: Path,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    _record_workspace_call(
        journal,
        operation_id="synthetic-class-teacher-call",
        module="class_teacher",
        task_kind="class_teacher_intake",
        request_text="合成班主任请求正文",
        response_text='{"draft":"合成班主任响应正文"}',
    )
    other_call_id = _record_workspace_call(
        journal,
        operation_id="synthetic-other-workspace-call",
        module="other_workspace",
        task_kind="other_task",
        request_text="合成其他工作区请求正文",
        response_text='{"plan":"合成其他工作区响应正文"}',
    )

    result = journal.clear_workspace("class_teacher")

    assert result == {
        "workspace_module": "class_teacher",
        "deleted_event_count": 2,
        "retained_event_count": 2,
        "unclassified_event_count": 0,
    }
    assert journal.list_calls(
        limit=10,
        workspace_module="class_teacher",
    )["returned"] == 0
    assert journal.list_calls(
        limit=10,
        workspace_module="other_workspace",
    )["returned"] == 1
    retained = journal.get_call(other_call_id)
    assert retained is not None
    assert "合成其他工作区请求正文" in str(retained["request"])
    assert retained["raw_response"] == '{"plan":"合成其他工作区响应正文"}'


def test_diagnostic_event_is_bounded_and_excludes_secrets_binary_and_local_paths(
    tmp_path: Path,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    audio_base64 = base64.b64encode(b"synthetic-audio" * 200).decode("ascii")
    file_base64 = base64.b64encode(b"synthetic-attachment" * 200).decode("ascii")
    forbidden_values = {
        "plain-api-secret",
        "cookie-session-secret",
        "password-secret",
        "access-token-secret",
        "refresh-token-secret",
        audio_base64,
        file_base64,
        r"C:\Users\SyntheticTeacher\private\student.txt",
    }
    sink = journal.for_workspace(
        workspace_module="class_teacher",
        workspace_task_kind="class_teacher_intake",
    )
    common = {
        "operation_id": "synthetic-bounded-diagnostic",
        "request_id": "synthetic-bounded-diagnostic",
        "attempt": 1,
        "request_kind": "workspace",
        "protocol": "chat_completions",
        "model": "synthetic-model",
        "endpoint_host": "model.invalid",
    }
    sink.record_request(
        **common,
        kwargs={
            "api_key": "plain-api-secret",
            "Cookie": "session=cookie-session-secret",
            "password": "password-secret",
            "access_token": "access-token-secret",
            "refresh_token": "refresh-token-secret",
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_base64, "format": "wav"},
                    },
                    {
                        "type": "input_file",
                        "file_data": file_base64,
                        "filename": "synthetic.txt",
                    },
                    {
                        "type": "text",
                        "text": r"C:\Users\SyntheticTeacher\private\student.txt",
                    },
                    {
                        "type": "text",
                        "text": "合成班主任正文" * 180_000,
                    },
                ],
            }],
        },
        retry_limit=0,
        retry_index=0,
        timeout_seconds=30,
    )
    sink.record_response(
        **common,
        response={
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "access_token": "access-token-secret",
                        "attachment": file_base64,
                        "draft": "合成响应正文",
                    })
                },
            }],
        },
        elapsed_ms=10,
    )

    raw = journal.path.read_text(encoding="utf-8")
    for forbidden in forbidden_values:
        assert forbidden not in raw
    lines = raw.splitlines()
    assert len(lines) == 2
    assert all(len((line + "\n").encode("utf-8")) <= 1024 * 1024 for line in lines)
    events = [json.loads(line) for line in lines]
    assert all(event["workspace_module"] == "class_teacher" for event in events)
    assert events[0]["event_truncated"] is True
    assert events[0]["original_event_bytes"] > 1024 * 1024
    attachments = events[0]["attachments"]
    assert {item["purpose"] for item in attachments} >= {
        "input_audio",
        "input_file",
    }


def test_diagnostic_free_text_excludes_complete_cookie_header(
    tmp_path: Path,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    cookie_header = "Cookie: SID=secret-one; CSRF=secret-two"
    _record_workspace_call(
        journal,
        operation_id="synthetic-cookie-header-redaction",
        module="class_teacher",
        task_kind="class_teacher_intake",
        request_text=cookie_header,
        response_text=cookie_header,
    )

    raw = journal.path.read_text(encoding="utf-8")
    assert "secret-one" not in raw
    assert "secret-two" not in raw


def test_diagnostic_free_text_excludes_urlsafe_base64_without_hiding_words(
    tmp_path: Path,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    urlsafe_base64 = base64.urlsafe_b64encode(
        b"\xfb\xff\xff" * 40 + b"\xfb"
    ).decode("ascii")
    ordinary_text = "ordinary-name_with-hyphen token_budget stays readable"
    free_text = f"attachment={urlsafe_base64}\n{ordinary_text}"
    _record_workspace_call(
        journal,
        operation_id="synthetic-urlsafe-base64-redaction",
        module="class_teacher",
        task_kind="class_teacher_intake",
        request_text=free_text,
        response_text=free_text,
    )

    raw = journal.path.read_text(encoding="utf-8")
    assert urlsafe_base64 not in raw
    assert ordinary_text in raw


def test_rotation_stays_at_three_files_and_targeted_clear_rewrites_all_of_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        diagnostics_module,
        "DIAGNOSTIC_MAX_FILE_BYTES",
        1_500,
    )
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    for index in range(12):
        module = "class_teacher" if index % 2 == 0 else "other_workspace"
        _record_workspace_call(
            journal,
            operation_id=f"synthetic-rotation-call-{index:02d}",
            module=module,
            task_kind=(
                "class_teacher_intake"
                if module == "class_teacher"
                else "other_task"
            ),
            request_text=f"合成轮转请求 {index} " + "甲" * 240,
            response_text=f"合成轮转响应 {index} " + "乙" * 240,
        )

    assert all(
        journal.path.with_name(f"{journal.path.name}.{index}").exists()
        for index in range(1, 4)
    )
    assert not journal.path.with_name(f"{journal.path.name}.4").exists()

    cleared = journal.clear_workspace("class_teacher")
    retained_events = []
    for path in journal._read_paths():
        if path.exists():
            retained_events.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
    assert cleared["deleted_event_count"] > 0
    assert cleared["retained_event_count"] > 0
    assert retained_events
    assert {event["workspace_module"] for event in retained_events} == {
        "other_workspace"
    }


def test_ai_diagnostics_api_filters_and_clears_only_class_teacher_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    class_teacher_call_id = _record_workspace_call(
        journal,
        operation_id="api-class-teacher-call",
        module="class_teacher",
        task_kind="class_teacher_draft_revision",
        request_text="合成班主任 API 筛选正文",
        response_text='{"draft":"合成班主任 API 响应"}',
    )
    journal.record_validation(
        operation_id="api-class-teacher-call",
        validation_issue_codes=["student_revision_mismatch"],
        workspace_module="class_teacher",
        workspace_task_kind="class_teacher_draft_revision",
    )
    _record_workspace_call(
        journal,
        operation_id="api-other-workspace-call",
        module="other_workspace",
        task_kind="other_task",
        request_text="合成其他工作区 API 筛选正文",
        response_text='{"plan":"合成其他工作区 API 响应"}',
    )
    monkeypatch.setattr(diagnostics_router, "_JOURNAL", journal)
    app = FastAPI()
    app.include_router(diagnostics_router.router)
    client = TestClient(app)

    listed = client.get(
        "/api/ai-diagnostics",
        params={
            "workspace_module": "class_teacher",
            "workspace_task_kind": "class_teacher_draft_revision",
        },
    )
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store, max-age=0"
    assert listed.json()["matching"] == 1
    assert listed.json()["items"][0]["workspace_module"] == "class_teacher"
    assert listed.json()["items"][0]["workspace_task_kind"] == (
        "class_teacher_draft_revision"
    )
    detail = client.get(f"/api/ai-diagnostics/{class_teacher_call_id}")
    assert detail.status_code == 200
    assert detail.json()["validation_issue_codes"] == [
        "student_revision_mismatch"
    ]

    cleared = client.delete("/api/ai-diagnostics/class-teacher")
    assert cleared.status_code == 200
    assert cleared.headers["cache-control"] == "no-store, max-age=0"
    assert cleared.json() == {
        "workspace_module": "class_teacher",
        "deleted_event_count": 3,
        "retained_event_count": 2,
        "unclassified_event_count": 0,
    }
    assert client.get(
        "/api/ai-diagnostics",
        params={"workspace_module": "class_teacher"},
    ).json()["matching"] == 0
    assert client.get(
        "/api/ai-diagnostics",
        params={"workspace_module": "other_workspace"},
    ).json()["matching"] == 1


def test_diagnostic_list_omits_request_bodies_while_detail_keeps_them(
    tmp_path: Path,
) -> None:
    journal = JsonlDiagnosticJournal(tmp_path / "logs" / "llm_diagnostics.jsonl")
    huge_text = "甲" * 20_000
    call_id = _record_workspace_call(
        journal,
        operation_id="synthetic-large-diagnostic",
        module="other_workspace",
        task_kind="other_task",
        request_text=huge_text,
        response_text='{"plan":"合成其他工作区响应正文"}',
    )

    listed = journal.list_calls(limit=10, workspace_module="other_workspace")
    assert listed["returned"] == 1
    assert "request" not in listed["items"][0]
    assert "raw_response" not in listed["items"][0]
    detail = journal.get_call(call_id)
    assert detail is not None
    assert huge_text in str(detail["request"])
    assert detail["raw_response"] == '{"plan":"合成其他工作区响应正文"}'
