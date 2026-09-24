from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from question_bank.database.paths import project_data_root
from question_bank.services.file_cache import cached_parsed_file


LOGGER = logging.getLogger(__name__)
RICH_CONTENT_VERSION = 3
_QUESTION_SECTION_HEADING = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+|\d+)\s*[、.．]\s*"
    r"(?:选择|填空|解答|计算|证明|作图)题[^\n]*\s*$"
)
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]", re.IGNORECASE | re.DOTALL)


def clean_question_blocks(
    blocks: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    return [
        block
        for block in blocks or []
        if not _QUESTION_SECTION_HEADING.fullmatch(
            _IMAGE_MARKER.sub(
                "", str(block.get("text") or "").replace("\r", "")
            ).strip()
        )
    ]


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
        "question_blocks": clean_question_blocks(question_blocks),
        "answer_blocks": answer_blocks or [],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _parse_rich_content(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Unable to load rich question content sidecar %s", path)
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("version") or 0) != RICH_CONTENT_VERSION:
        return None
    question_blocks = payload.get("question_blocks")
    if isinstance(question_blocks, list):
        payload["question_blocks"] = clean_question_blocks(
            [item for item in question_blocks if isinstance(item, dict)]
        )
    return payload


def load_question_rich_content(question_id: int, root: str | Path | None = None) -> dict[str, object] | None:
    path = rich_content_path(question_id, root)
    if not path.exists():
        return None
    # Callers treat the payload as read-only, so the parsed value can be shared.
    return cached_parsed_file(path, _parse_rich_content)


def is_question_rich_content_current(question_id: int, root: str | Path | None = None) -> bool:
    return load_question_rich_content(question_id, root) is not None


__all__ = [
    "clean_question_blocks",
    "is_question_rich_content_current",
    "load_question_rich_content",
    "save_question_rich_content",
]
