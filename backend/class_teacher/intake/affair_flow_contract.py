from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from ..errors import VaultError


CONTRACT_VERSION = "class_teacher_affair_flow_revision.v1"
ITEM_KINDS = {"add_step", "revise_step", "note"}
_TOP_LEVEL_FIELDS = {"contract_version", "assistant_message", "items"}
_ITEM_FIELDS = {"item_id", "kind", "target_step_key", "title", "details", "depends_on", "reason", "text"}
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{2,64}")


@dataclass(frozen=True, slots=True)
class FlowRevisionItem:
    item_id: str
    kind: str
    target_step_key: str | None
    title: str
    details: str
    depends_on: tuple[str, ...]
    reason: str
    text: str


@dataclass(frozen=True, slots=True)
class FlowRevisionResult:
    assistant_message: str
    items: tuple[FlowRevisionItem, ...]


def _text(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise VaultError(
            "class_teacher_affair_flow_revision_invalid_result",
            f"AI 返回的{label}无效",
            status_code=422,
        )
    return value.strip()


def _optional_text(value: object, label: str, *, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise VaultError(
            "class_teacher_affair_flow_revision_invalid_result",
            f"AI 返回的{label}无效",
            status_code=422,
        )
    return value.strip()


def parse_affair_flow_revision(payload: Mapping[str, object]) -> FlowRevisionResult:
    if set(payload) - _TOP_LEVEL_FIELDS:
        raise VaultError(
            "class_teacher_affair_flow_revision_invalid_result",
            "AI 返回了未知的流程修订字段",
            status_code=422,
        )
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise VaultError(
            "class_teacher_affair_flow_revision_invalid_result",
            "AI 返回的流程修订版本不受支持",
            status_code=422,
        )
    assistant_message = _text(payload.get("assistant_message"), "说明", maximum=2000)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 8:
        raise VaultError(
            "class_teacher_affair_flow_revision_invalid_result",
            "AI 返回的流程修订清单无效",
            status_code=422,
        )
    items: list[FlowRevisionItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping) or set(raw) - _ITEM_FIELDS:
            raise VaultError(
                "class_teacher_affair_flow_revision_invalid_result",
                "AI 返回的流程修订条目无效",
                status_code=422,
            )
        item_id = _text(raw.get("item_id"), "条目编号", maximum=64)
        if _OPAQUE_ID.fullmatch(item_id) is None or item_id in seen:
            raise VaultError(
                "class_teacher_affair_flow_revision_invalid_result",
                "AI 返回了重复的流程修订条目",
                status_code=422,
            )
        seen.add(item_id)
        kind = _text(raw.get("kind"), "条目类型", maximum=32)
        if kind not in ITEM_KINDS:
            raise VaultError(
                "class_teacher_affair_flow_revision_invalid_result",
                "AI 返回了未知的流程修订类型",
                status_code=422,
            )
        target_step_key = _optional_text(raw.get("target_step_key"), "目标步骤", maximum=128) or None
        raw_depends = raw.get("depends_on", [])
        if not isinstance(raw_depends, list) or len(raw_depends) > 20:
            raise VaultError(
                "class_teacher_affair_flow_revision_invalid_result",
                "AI 返回的步骤依赖无效",
                status_code=422,
            )
        depends_on = tuple(
            _text(item, "步骤依赖", maximum=128) for item in raw_depends
        )
        items.append(FlowRevisionItem(
            item_id=item_id,
            kind=kind,
            target_step_key=target_step_key,
            title=_optional_text(raw.get("title"), "步骤名称", maximum=240),
            details=_optional_text(raw.get("details"), "步骤说明", maximum=4000),
            depends_on=depends_on,
            reason=_optional_text(raw.get("reason"), "调整理由", maximum=800),
            text=_optional_text(raw.get("text"), "核对建议", maximum=2000),
        ))
    return FlowRevisionResult(assistant_message=assistant_message, items=tuple(items))


__all__ = [
    "CONTRACT_VERSION",
    "ITEM_KINDS",
    "FlowRevisionItem",
    "FlowRevisionResult",
    "parse_affair_flow_revision",
]
