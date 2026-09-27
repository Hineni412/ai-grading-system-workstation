"""Read-time Word XML lookup for config source previews.

Config source manifests only keep the linear block text (``question_html`` /
``answer_html`` lines); the Word paragraph XML that the question bank uses to
render formulas is dropped at parse time.  This module re-parses the stored
``.docx`` bytes once per source, indexes each paragraph/table record by its
normalized text and projects matched preview blocks through the existing
question-bank renderer so config previews can show formulas, underlines and
tables exactly like the question bank does.

Images deliberately stay out of this path: the block renderer is invoked with
an empty relationship map, so drawings render nothing and the existing
appended asset blocks remain the single source of ``<img>`` output.
"""

from __future__ import annotations

import io
import re
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from question_bank.importers.docx_importer import import_docx
from question_bank.services.preview_html import (
    _normalize,
    _normalize_expected,
    _plain_text,
    block_preview_html,
)

_IMAGE_MARKER = re.compile(r"\[\[IMAGE:[^\]\r\n]+\]\]", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_LEADING_NUMBER_PREFIX = re.compile(
    r"^(?:\[\[IMAGE:[^\r\n]+?\]\]\s*)*(?P<number>\d{1,3}\s*[.．、]\s*)"
)
_HTML_TOKEN = re.compile(r"<[^>]*>|[^<]+")

_INDEX_CACHE_LIMIT = 16
_INDEX_CACHE: "OrderedDict[tuple[str, str, str], dict[str, str]]" = OrderedDict()
_INDEX_CACHE_LOCK = threading.Lock()


def _normalized_key(text: str) -> str:
    return _WHITESPACE.sub("", _IMAGE_MARKER.sub("", str(text or "")))


def _split_leading_number(text: str) -> tuple[str, str]:
    """Split ``6. body`` into the html-removable prefix and the remainder."""
    stripped = str(text or "").lstrip()
    match = _LEADING_NUMBER_PREFIX.match(stripped)
    if not match:
        return "", stripped
    return match.group("number"), stripped[match.end():]


def load_paragraph_xml_index(source_docx: bytes) -> dict[str, str]:
    """Map normalized paragraph/table text → its frozen Word XML.

    Every record contributes two keys: its full normalized text and the same
    text with a leading question number stripped (``6. body`` → ``body``),
    mirroring how ``question_html`` drops the number before projection.
    Stripped keys never override a direct key, and the first record wins on
    duplicate text so the index stays deterministic.
    """
    index: dict[str, str] = {}
    stripped_entries: list[tuple[str, str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="config-src-xml-") as temp_root:
            temp_path = Path(temp_root)
            extracted = import_docx(
                io.BytesIO(source_docx),
                source_name=temp_path / "source.docx",
                asset_root=temp_path / "assets",
                asset_root_is_output_dir=True,
            )
    except Exception:
        return {}
    for record in getattr(extracted, "rich_paragraphs", None) or []:
        if not isinstance(record, dict):
            continue
        text = str(record.get("text") or "")
        xml = str(record.get("xml") or "")
        if not text.strip() or not xml.strip():
            continue
        key = _normalized_key(text)
        if key and key not in index:
            index[key] = xml
        _number, remainder = _split_leading_number(text)
        stripped_key = _normalized_key(remainder)
        if stripped_key and stripped_key != key:
            stripped_entries.append((stripped_key, xml))
    for key, xml in stripped_entries:
        index.setdefault(key, xml)
    return index


def paragraph_xml_index(
    session_id: int | str,
    source_id: str,
    source_revision: str,
    source_docx: bytes,
) -> dict[str, str]:
    """Cached ``load_paragraph_xml_index`` keyed by session/source/revision.

    ``paragraph_xml_index`` runs inside FastAPI threadpool workers, so all
    cache mutations happen under ``_INDEX_CACHE_LOCK`` while the expensive
    ``import_docx`` build stays outside it.  A concurrent build for the same
    key wins the insert and is returned to the loser unchanged.
    """
    key = (str(session_id), str(source_id), str(source_revision))
    with _INDEX_CACHE_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached is not None:
            _INDEX_CACHE.move_to_end(key)
            return cached
    index = load_paragraph_xml_index(source_docx)
    with _INDEX_CACHE_LOCK:
        existing = _INDEX_CACHE.get(key)
        if existing is not None:
            _INDEX_CACHE.move_to_end(key)
            return existing
        _INDEX_CACHE[key] = index
        _INDEX_CACHE.move_to_end(key)
        while len(_INDEX_CACHE) > _INDEX_CACHE_LIMIT:
            _INDEX_CACHE.popitem(last=False)
    return index


def preview_html_for_text(
    index: Mapping[str, str] | None,
    text: str,
) -> str:
    """Render the stored XML for one projected block, else ``""``.

    ``text`` is the block's authoritative text.  When the block text lacks the
    leading question number the record kept, the rendered prefix is removed as
    plain text (never inside ``.qm`` formula spans) and the result is
    re-verified against the block text before it is returned.
    """
    if not index:
        return ""
    xml = index.get(_normalized_key(text))
    if xml is None:
        return ""
    rendered = block_preview_html(xml, {})
    if not rendered:
        return ""
    expected = _normalize_expected(text)
    if _normalize(_plain_text(rendered)) == expected:
        return rendered
    plain = _plain_text(rendered)
    match = _LEADING_NUMBER_PREFIX.match(plain)
    if not match:
        return ""
    if _normalize(plain[match.end():]) != expected:
        return ""
    stripped = _strip_text_prefix(rendered, plain[: match.end()])
    if not stripped:
        return ""
    if _normalize(_plain_text(stripped)) != expected:
        return ""
    return stripped


def _strip_text_prefix(rendered: str, prefix: str) -> str:
    """Remove a plain-text prefix from rendered HTML without touching markup.

    Character consumption stops at any ``.qm`` formula span; reaching one
    while the prefix is unfinished aborts the strip entirely.
    """
    needed = _WHITESPACE.sub("", prefix)
    if not needed:
        return rendered
    parts: list[str] = []
    remaining = needed
    for token in _HTML_TOKEN.findall(rendered):
        if token.startswith("<"):
            parts.append(token)
            if remaining:
                lowered = token.lower()
                if lowered.startswith("<span") and "qm" in lowered:
                    return ""
                if lowered.startswith(("<img", "<br")):
                    return ""
            continue
        if not remaining:
            parts.append(token)
            continue
        buffer: list[str] = []
        for char in token:
            if not remaining:
                buffer.append(char)
            elif char == remaining[0]:
                remaining = remaining[1:]
            elif char.isspace():
                continue
            else:
                return ""
        parts.append("".join(buffer))
    if remaining:
        return ""
    return "".join(parts)


__all__ = [
    "load_paragraph_xml_index",
    "paragraph_xml_index",
    "preview_html_for_text",
]
