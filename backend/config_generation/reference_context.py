from __future__ import annotations

import re
from typing import Any, Mapping

from backend.document_parsing.question_blocks import rich_text_for_model


_IMAGE_MARKER = re.compile(r"\[\[IMAGE:[^\]]+\]\]", re.IGNORECASE)


def build_reference_context(config_question_block: Mapping[str, Any]) -> dict[str, Any]:
    """Build the single path-safe answer/analysis context sent to the model."""

    answer = _text(
        config_question_block,
        *("answer_text", "answer_html", "canonical_answer", "answer")
        if config_question_block.get("answer_confirmed")
        else ("answer_html", "answer_text", "canonical_answer", "answer"),
    )
    analysis = _text(config_question_block, "analysis_html", "analysis")
    segments = [
        {"kind": kind, "text": value}
        for kind, value in (("answer", answer), ("analysis", analysis))
        if value
    ]
    rich_blocks = [
        {"kind": item["kind"], "text": item["text"]}
        for item in segments
    ]
    combined = "\n\n".join(item["text"] for item in segments)
    teacher_confirmed = bool(config_question_block.get("answer_confirmed"))
    trust_level = (
        "teacher_confirmed"
        if combined and teacher_confirmed
        else "source_extracted"
        if combined
        else "absent"
    )
    source_kind = (
        "answer_and_analysis"
        if answer and analysis
        else "answer"
        if answer
        else "analysis"
        if analysis
        else "absent"
    )
    return {
        "text": combined,
        "source_segments": segments,
        "rich_blocks": rich_blocks,
        "trust_level": trust_level,
        "source_kind": source_kind,
    }


def _text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        cleaned = rich_text_for_model(_IMAGE_MARKER.sub("[图片]", str(value)))
        if cleaned:
            return cleaned
    return ""


__all__ = ["build_reference_context"]
