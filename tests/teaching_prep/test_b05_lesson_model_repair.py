"""lesson_model 修复轮：校验失败应触发唯一一次修复调用，而非直接失败。"""
from __future__ import annotations

import pytest

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.llm.lesson_model import (
    WorkspaceLessonModelAdapter,
)


class _FakeGateway:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls: list[dict[str, object]] = []

    def chat_completions(self, **kwargs: object) -> dict[str, object]:
        call_kwargs = kwargs.get("kwargs")
        self.calls.append(call_kwargs if isinstance(call_kwargs, dict) else {})
        text = self._texts.pop(0)
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def _adapter(gateway: _FakeGateway) -> WorkspaceLessonModelAdapter:
    return WorkspaceLessonModelAdapter(
        gateway=gateway,  # type: ignore[arg-type]
        client=object(),
        model="test-model",
    )


def _pack() -> dict[str, object]:
    return {"materials": [], "preparation_preferences": {}}


def test_validator_failure_triggers_single_repair_round() -> None:
    gateway = _FakeGateway(['{"bad": 1}', '{"ok": true}'])
    seen: list[str] = []

    def validator(payload: dict[str, object]) -> None:
        if "ok" not in payload:
            seen.append("bad")
            raise TeachingPrepValidationError(
                "draft contains an unknown source citation"
            )

    result = _adapter(gateway).generate(
        operation_id="op-repair-1",
        resource_pack=_pack(),
        validator=validator,
    )

    assert result == {"ok": True}
    assert seen == ["bad"]
    assert len(gateway.calls) == 2
    repair_messages = [
        str(item.get("content"))
        for item in gateway.calls[1].get("messages", [])
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    assert any("unknown source citation" in text for text in repair_messages)


def test_validator_persistent_failure_stops_after_two_rounds() -> None:
    gateway = _FakeGateway(['{"bad": 1}', '{"bad": 2}'])

    def validator(payload: dict[str, object]) -> None:
        raise TeachingPrepValidationError("still invalid")

    with pytest.raises(TeachingPrepValidationError, match="still invalid"):
        _adapter(gateway).generate(
            operation_id="op-repair-2",
            resource_pack=_pack(),
            validator=validator,
        )
    assert len(gateway.calls) == 2


def test_without_validator_first_valid_json_returns() -> None:
    gateway = _FakeGateway(['{"ok": true}'])
    result = _adapter(gateway).generate(
        operation_id="op-repair-3",
        resource_pack=_pack(),
    )
    assert result == {"ok": True}
    assert len(gateway.calls) == 1
