from __future__ import annotations

import json
import re
from collections.abc import Mapping
from uuid import uuid4

from backend.teaching_prep.domain.errors import TeachingPrepNotFoundError
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase

_PAGE_SOURCE_REF = re.compile(r"^material:[0-9a-f]{32}:unit:\d+$")
_WINDOWS_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t<>|\"']+")
_FILE_URL = re.compile(r"(?i)\bfile://[^\s<>\"']+")
_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")


_PHASES = {
    "started",
    "thinking",
    "tool_call",
    "tool_result",
    "round_done",
    "round_retry",
    "findings_ready",
    "final_accepted",
    "failed",
}


class AdaptationTraceRepository:
    """Teacher-safe adaptation observation log; never stores paths or secrets."""

    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def append_event(
        self,
        *,
        lesson_node_id: str,
        operation_id: str,
        event: Mapping[str, object],
    ) -> dict[str, object]:
        payload = _teacher_safe_event(event)
        with self._database.connect(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS seq
                FROM teaching_prep_adaptation_trace_events
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            seq = int(row["seq"]) + 1 if row is not None else 1
            connection.execute(
                """
                INSERT INTO teaching_prep_adaptation_trace_events (
                    id, operation_id, lesson_node_id, seq, event_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    operation_id,
                    lesson_node_id,
                    seq,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
        return payload

    def list_for_operation(
        self,
        *,
        lesson_node_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json
                FROM teaching_prep_adaptation_trace_events
                WHERE lesson_node_id = ? AND operation_id = ?
                ORDER BY seq ASC
                """,
                (lesson_node_id, operation_id),
            ).fetchall()
        if not rows:
            raise TeachingPrepNotFoundError("adaptation trace was not found")
        events = [_load_event(row["event_json"]) for row in rows]
        last = events[-1]
        status = "running"
        phase = str(last.get("phase") or "")
        if phase == "final_accepted":
            status = "succeeded"
        elif phase == "failed":
            status = "failed"
        return {
            "operation_id": operation_id,
            "lesson_node_id": lesson_node_id,
            "status": status,
            "model_calls_used": int(last.get("model_calls_used") or 0),
            "model_calls_max": int(last.get("model_calls_max") or 6),
            "events": events,
        }

    def latest_for_lesson(self, lesson_node_id: str) -> dict[str, object]:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT operation_id
                FROM teaching_prep_adaptation_trace_events
                WHERE lesson_node_id = ?
                ORDER BY created_at DESC, seq DESC
                LIMIT 1
                """,
                (lesson_node_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError("adaptation trace was not found")
        return self.list_for_operation(
            lesson_node_id=lesson_node_id,
            operation_id=str(row["operation_id"]),
        )


def _teacher_safe_event(event: Mapping[str, object]) -> dict[str, object]:
    phase = str(event.get("phase") or "").strip()
    if phase not in _PHASES:
        phase = "thinking"
    tool = event.get("tool")
    safe_tool = None
    if isinstance(tool, Mapping):
        page = tool.get("page")
        safe_tool = {
            "name": "get_frozen_page",
            "purpose": _optional_text(tool.get("purpose"), 40),
            "page": page if isinstance(page, int) else None,
            "source_ref": _safe_source_ref(tool.get("source_ref")),
        }
    result = event.get("result")
    safe_result = None
    if isinstance(result, Mapping):
        preview_url = result.get("preview_url")
        safe_result = {
            "ok": result.get("ok") is True,
            "label": _optional_text(result.get("label"), 160) or "",
            "preview_url": (
                preview_url
                if isinstance(preview_url, str)
                and preview_url.startswith("/api/teaching-prep/")
                else None
            ),
        }
    round_number = event.get("round")
    used = event.get("model_calls_used")
    maximum = event.get("model_calls_max")
    return {
        "round": round_number if isinstance(round_number, int) else 1,
        "phase": phase,
        "summary": _optional_text(event.get("summary"), 240) or "",
        "thinking_excerpt": _safe_excerpt(event.get("thinking_excerpt"), 500),
        "tool": safe_tool,
        "result": safe_result,
        "findings": _safe_findings(event.get("findings")),
        "model_calls_used": used if isinstance(used, int) else 0,
        "model_calls_max": maximum if isinstance(maximum, int) else 6,
    }


def _safe_findings(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list):
        return None
    items: list[dict[str, object]] = []
    for raw in value[:20]:
        if not isinstance(raw, Mapping):
            continue
        finding = _safe_excerpt(raw.get("finding"), 240)
        if not finding:
            continue
        pages = [
            page
            for page in (
                raw.get("pages") if isinstance(raw.get("pages"), list) else []
            )
            if isinstance(page, int) and not isinstance(page, bool) and page > 0
        ][:40]
        items.append(
            {
                "finding": finding,
                "category": _optional_text(raw.get("category"), 40) or "",
                "pages": pages,
            }
        )
    return items or None


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _safe_source_ref(value: object) -> str:
    text = str(value or "").strip()
    if _PAGE_SOURCE_REF.fullmatch(text):
        return text
    return ""


def _safe_excerpt(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = _FILE_URL.sub("[LOCAL_PATH_REDACTED]", text)
    text = _HTTP_URL.sub("[URL_REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH_REDACTED]", text)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _load_event(raw: object) -> dict[str, object]:
    try:
        payload = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    return _teacher_safe_event(payload)
