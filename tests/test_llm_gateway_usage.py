from __future__ import annotations

import json
from types import SimpleNamespace

from backend.llm.usage import (
    JsonlUsageSink,
    LLMUsageEvent,
    NullUsageSink,
    response_diagnostics,
    usage_fields,
)
from usage_logger import log_llm_usage


def _event(**overrides: object) -> LLMUsageEvent:
    values: dict[str, object] = {
        "request_id": "req-1",
        "attempt": 1,
        "request_kind": "grading",
        "protocol": "chat_completions",
        "model": "model",
        "latency_ms": 25,
        "success": True,
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    values.update(overrides)
    return LLMUsageEvent(**values)


def test_usage_fields_supports_chat_and_responses_shapes():
    chat = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    responses = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}

    assert usage_fields(chat)["prompt_tokens"] == 10
    assert usage_fields(responses)["completion_tokens"] == 3


def test_usage_fields_supports_cached_and_reasoning_detail_objects():
    usage = {
        "input_tokens": 8,
        "output_tokens": 5,
        "total_tokens": 13,
        "input_tokens_details": SimpleNamespace(cached_tokens=6),
        "output_tokens_details": SimpleNamespace(reasoning_tokens=2),
    }

    assert usage_fields(usage) == {
        "prompt_tokens": 8,
        "completion_tokens": 5,
        "cached_tokens": 6,
        "reasoning_tokens": 2,
        "total_tokens": 13,
    }


def test_usage_fields_supports_nested_mapping_responses_usage_and_details():
    response = {
        "id": "response-1",
        "usage": {
            "input_tokens": 13,
            "output_tokens": 8,
            "total_tokens": 21,
            "input_tokens_details": {"cached_tokens": 5},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }

    assert usage_fields(response) == {
        "prompt_tokens": 13,
        "completion_tokens": 8,
        "cached_tokens": 5,
        "reasoning_tokens": 3,
        "total_tokens": 21,
    }


def test_response_diagnostics_records_length_stop_without_retaining_content():
    response = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"ok":true}'},
            }
        ]
    }

    diagnostics = response_diagnostics(response)

    assert diagnostics == {
        "finish_reason": "length",
        "output_truncated": True,
        "response_chars": 11,
        "response_sha256": (
            "4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93"
        ),
    }
    assert '{"ok":true}' not in repr(diagnostics)


def test_response_diagnostics_sanitizes_untrusted_finish_reason():
    response = {
        "choices": [
            {
                "finish_reason": "length\nstudent answer C:\\private",
                "message": {"content": "{}"},
            }
        ]
    }

    diagnostics = response_diagnostics(response)

    assert diagnostics["finish_reason"] == "unknown"
    assert diagnostics["output_truncated"] is False
    assert "student answer" not in repr(diagnostics)
    assert "C:\\" not in repr(diagnostics)


def test_response_diagnostics_distinguishes_unclosed_from_mismatched_json():
    unclosed = response_diagnostics(
        {
            "choices": [
                {
                    "finish_reason": None,
                    "message": {"content": '{"rubric":{"questions":['},
                }
            ]
        }
    )
    mismatched = response_diagnostics(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"a":1]'},
                }
            ]
        }
    )

    assert unclosed["output_truncated"] is True
    assert mismatched["output_truncated"] is False


def test_failed_request_event_defaults_to_zero_usage():
    event = _event(
        success=False,
        error_category="timeout",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )

    assert event.prompt_tokens == 0
    assert event.completion_tokens == 0
    assert event.cached_tokens == 0
    assert event.reasoning_tokens == 0
    assert event.total_tokens == 0


def test_jsonl_sink_writes_only_allowlisted_metadata(tmp_path):
    path = tmp_path / "usage.jsonl"
    sink = JsonlUsageSink(path)

    sink.write(_event(model="model", request_id="req-1"))

    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    assert record["request_id"] == "req-1"
    assert "secret-key" not in text
    assert "student answer" not in text
    assert "C:\\" not in text


def test_jsonl_sink_write_failure_does_not_raise(monkeypatch, caplog, tmp_path):
    def fail_to_write(*args, **kwargs):
        raise OSError("secret-key student answer C:\\private\\usage.jsonl")

    monkeypatch.setattr("backend.llm.usage.log_llm_usage", fail_to_write)

    JsonlUsageSink(tmp_path / "usage.jsonl").write(_event())

    assert "Failed to record LLM usage metadata" in caplog.text
    assert "secret-key" not in caplog.text
    assert "student answer" not in caplog.text
    assert "C:\\" not in caplog.text


def test_compatibility_logger_sanitizes_destination_failure(capsys, tmp_path):
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    log_llm_usage({}, log_file=blocked_parent / "usage.jsonl")

    warning = capsys.readouterr().out
    assert "Failed to log usage" in warning
    assert str(tmp_path) not in warning
    assert blocked_parent.name not in warning


def test_null_sink_ignores_events():
    assert NullUsageSink().write(_event()) is None


def test_compatibility_logger_retains_default_record_shape(tmp_path):
    path = tmp_path / "legacy.jsonl"

    log_llm_usage({}, log_file=path)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {
        "timestamp",
        "request_id",
        "session_id",
        "exam_id",
        "student_id",
        "question_id",
        "question_type",
        "chain_type",
        "flow_type",
        "model",
        "actual_model_used",
        "api_base_url_masked",
        "thinking_type",
        "temperature",
        "max_tokens",
        "cache_mode",
        "context_id",
        "objective_config_used",
        "main_grading_config_used",
        "production_grading_model_used",
        "prompt_tokens",
        "cached_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "image_count",
        "image_width",
        "image_height",
        "image_file_size_kb",
        "max_image_width",
        "max_image_height",
        "total_image_file_size_kb",
        "prompt_chars",
        "system_prompt_chars",
        "user_prompt_chars",
        "response_chars",
        "latency_ms",
        "success",
        "json_valid",
        "need_review",
        "auto_scored",
        "score",
        "max_score",
        "error_type",
        "error_message",
    }
    assert record["request_id"] == ""
    assert record["temperature"] == 0.0
    assert record["success"] is False
