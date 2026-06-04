from __future__ import annotations

import json
import logging
from pathlib import Path

from question_bank.database.paths import project_data_root


LOGGER = logging.getLogger(__name__)
RICH_CONTENT_VERSION = 3


def rich_content_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else project_data_root() / "question_bank" / "rich_content"


def rich_content_path(question_id: int, root: str | Path | None = None) -> Path:
    return rich_content_root(root) / f"question_{int(question_id)}.json"


def save_question_rich_content(
    question_id: int,
    *,
    question_blocks: list[dict[str, object]] | None = None,
    answer_blocks: list[dict[str, object]] | None = None,
    root: str | Path | None = None,
) -> Path | None:
    if not question_blocks and not answer_blocks:
        return None
    output_path = rich_content_path(question_id, root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": RICH_CONTENT_VERSION,
        "question_id": int(question_id),
        "question_blocks": question_blocks or [],
        "answer_blocks": answer_blocks or [],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_question_rich_content(question_id: int, root: str | Path | None = None) -> dict[str, object] | None:
    path = rich_content_path(question_id, root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Unable to load rich question content sidecar %s", path)
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("version") or 0) != RICH_CONTENT_VERSION:
        return None
    return payload


def is_question_rich_content_current(question_id: int, root: str | Path | None = None) -> bool:
    return load_question_rich_content(question_id, root) is not None


__all__ = ["is_question_rich_content_current", "load_question_rich_content", "save_question_rich_content"]
