import pytest

from question_bank.services.ai_tagging_service import (
    TaggingRequestEvent,
    _TaggingRequestController,
    classify_tagging_error,
    sanitize_tagging_error,
)


def test_classify_tagging_error_categories() -> None:
    assert classify_tagging_error(RuntimeError("429 Too Many Requests")) == "rate_limit"
    assert classify_tagging_error(TimeoutError("request timed out")) == "timeout"
    assert classify_tagging_error(ConnectionError("connection refused")) == "network"
    assert classify_tagging_error(ValueError("failed to parse JSON")) == "parse"
    assert classify_tagging_error(ValueError("response did not include results")) == "validation"
    assert classify_tagging_error(RuntimeError("something odd")) == "unknown"


def test_sanitize_tagging_error_masks_secrets_and_truncates() -> None:
    message = "auth failed api_key=sk-secret-123 token: bearer-xyz rest of message"
    cleaned = sanitize_tagging_error(message)
    assert "sk-secret-123" not in cleaned
    assert "bearer-xyz" not in cleaned
    assert "api_key=***" in cleaned

    long = "x" * 900
    assert len(sanitize_tagging_error(long)) <= 501


def test_controller_run_emits_started_then_succeeded() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)

    result = controller.run("batch", (1, 2), lambda: "ok", model_name="m")

    assert result == "ok"
    assert [e.phase for e in events] == ["started", "succeeded"]
    assert events[-1].model_name == "m"


def test_controller_run_emits_failed_and_reraises_with_sanitized_error() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)

    def boom() -> None:
        raise RuntimeError("429 rate limit api_key=sk-topsecret")

    with pytest.raises(RuntimeError):
        controller.run("batch", (7,), boom, model_name="m")

    assert [e.phase for e in events] == ["started", "failed"]
    failed = events[-1]
    assert failed.error_category == "rate_limit"
    assert "sk-topsecret" not in failed.error_message
    assert failed.question_ids == (7,)


def test_controller_finish_helper_emits_failed_with_category() -> None:
    events: list[TaggingRequestEvent] = []
    controller = _TaggingRequestController(600, events.append)
    started = controller.begin("batch", (3, 4))
    controller.finish(started, "failed", ConnectionError("network unreachable"), model_name="m")

    assert events[-1].phase == "failed"
    assert events[-1].error_category == "network"
    assert events[-1].request_number == started.request_number
