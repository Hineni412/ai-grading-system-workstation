from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from backend.llm.trace import (
    JsonlCallTraceSink,
    LLMCallTraceEvent,
    safe_host_label,
    safe_trace_label,
)


EXPECTED_TRACE_FIELDS = {
    "actual_model",
    "attempt",
    "cached_tokens",
    "completion_tokens",
    "elapsed_ms",
    "endpoint_host",
    "error_category",
    "event_type",
    "exception_type",
    "finish_reason",
    "http_status_code",
    "image_bytes_estimate",
    "image_count",
    "model",
    "network_phase",
    "outcome",
    "output_truncated",
    "pacer_wait_ms",
    "prompt_tokens",
    "protocol",
    "provider_request_id",
    "reasoning_tokens",
    "request_bytes_estimate",
    "request_id",
    "request_kind",
    "response_bytes_estimate",
    "response_chars",
    "response_sha256",
    "retry_delay_ms",
    "retry_index",
    "retry_limit",
    "text_chars",
    "timestamp_utc",
    "timeout_seconds",
    "total_tokens",
    "will_retry",
}


def test_jsonl_call_trace_sink_writes_only_sanitized_allowlisted_fields(tmp_path):
    path = tmp_path / "llm_api_calls.jsonl"
    sink = JsonlCallTraceSink(path)
    secret_request_id = "student answer C:\\private\\paper.png"
    secret_model = "model api_key=secret"

    sink.write(
        LLMCallTraceEvent(
            event_type="request_started",
            timestamp_utc="2026-07-22T08:00:00.000Z",
            request_id=secret_request_id,
            attempt=1,
            request_kind="grading",
            protocol="chat_completions",
            model=secret_model,
            endpoint_host="api.example.test/private?token=secret",
            provider_request_id="C:/private/provider-request",
            timeout_seconds=600.0,
            request_bytes_estimate=929_000,
            image_count=2,
        )
    )

    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    assert set(record) == EXPECTED_TRACE_FIELDS
    assert record["event_type"] == "request_started"
    assert record["request_id"].startswith("redacted-")
    assert record["model"].startswith("redacted-")
    assert record["endpoint_host"] == ""
    assert record["provider_request_id"] == ""
    assert text.endswith("\n")
    assert secret_request_id not in text
    assert secret_model not in text
    assert "student answer" not in text
    assert "api_key" not in text
    assert "C:\\private" not in text
    assert "/private" not in text


def test_trace_labels_reject_absolute_paths_and_endpoint_paths():
    assert safe_trace_label("provider/model-v1") == "provider/model-v1"
    assert safe_trace_label("C:/private/paper.png").startswith("redacted-")
    assert safe_trace_label("/private/paper.png").startswith("redacted-")
    assert safe_host_label("api.example.test") == "api.example.test"
    assert safe_host_label("api.example.test/private") == ""


def test_jsonl_call_trace_sink_keeps_concurrent_appends_as_complete_lines(
    tmp_path,
):
    path = tmp_path / "llm_api_calls.jsonl"
    sinks = [JsonlCallTraceSink(path), JsonlCallTraceSink(path)]

    def write(index: int) -> None:
        sinks[index % 2].write(
            LLMCallTraceEvent(
                event_type="request_started",
                timestamp_utc="2026-07-22T08:00:00.000Z",
                request_id=f"request-{index}",
                attempt=1,
                request_kind="grading",
                protocol="chat_completions",
                model="model-v1",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(80)))

    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 80
    assert {record["request_id"] for record in records} == {
        f"request-{index}" for index in range(80)
    }


def _attribute_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))


def test_first_party_sdk_calls_stay_inside_the_llm_gateway():
    project_root = Path(__file__).resolve().parents[1]
    excluded_parts = {
        ".git",
        ".pytest_cache",
        ".test-runs",
        ".worktrees",
        "node_modules",
        "runtime",
        "tests",
        "user_data",
    }
    allowed = Path("backend/llm/gateway.py")
    violations: list[str] = []

    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if excluded_parts.intersection(relative.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _attribute_chain(node.func)
            is_chat_call = chain[-3:] == ["chat", "completions", "create"]
            is_responses_call = chain[-2:] == ["responses", "create"]
            if (is_chat_call or is_responses_call) and relative != allowed:
                violations.append(f"{relative.as_posix()}:{node.lineno}")

    assert violations == []
