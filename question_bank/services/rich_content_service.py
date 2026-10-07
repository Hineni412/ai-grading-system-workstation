from __future__ import annotations

import json
import re
from pathlib import Path

from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml.etree import XMLSyntaxError

from question_bank.database.paths import project_data_root
from question_bank.services.file_cache import cached_parsed_file, file_is_file

RICH_CONTENT_VERSION = 3
_QUESTION_SECTION_HEADING = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+|\d+)\s*[、.．]\s*"
    r"(?:选择|填空|解答|计算|证明|作图)题[^\n]*\s*$"
)
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]", re.IGNORECASE | re.DOTALL)


_STEM_DISPLAY_MARKUP = re.compile(
    r"\[\[IMAGE:[^\]]+\]\]|</?(?:p|span|b|strong|i|em|u|sup|sub)\b[^>]*>",
    re.IGNORECASE,
)


def _source_score_span(text: str, question_number: str | None = None):
    # Frozen blocks normally carry their original number. For older blocks
    # without that metadata, accept an unambiguous main-question label only.
    number_prefix = r"(?:(?:第\s*)?\d+\s*(?:[.．、]|题)\s*)?"
    if question_number:
        number = re.escape(str(question_number))
        number_prefix = (
            rf"(?:(?:(?:第\s*)?{number}\s*(?:[.．、]|题)|"
            rf"[（(]\s*{number}\s*[）)])\s*)?"
        )
    return re.match(
        rf"^\s*{number_prefix}"
        r"(?P<score>[（(][ \t\u3000]*\d+(?:\.\d+)?[ \t\u3000]*分"
        r"[ \t\u3000]*[）)][ \t\u3000]*)",
        text,
    )


def _stem_visible_characters(text: str) -> tuple[str, list[int]]:
    offsets = []
    cursor = 0
    for marker in _STEM_DISPLAY_MARKUP.finditer(text):
        offsets.extend(range(cursor, marker.start()))
        cursor = marker.end()
    offsets.extend(range(cursor, len(text)))
    return "".join(text[index] for index in offsets), offsets


def strip_question_source_score(text: str, *, question_number: str | None = None) -> str:
    """Remove only a complete leading printed score; keep formatting tokens."""
    visible, offsets = _stem_visible_characters(text)
    match = _source_score_span(visible, question_number)
    if match is None:
        return text
    start, end = match.span("score")
    removed = set(offsets[start:end])
    return "".join(char for index, char in enumerate(text) if index not in removed)


def _source_score_xml_pieces(root):
    pieces = []
    for node in root.iter():
        if node.tag == qn("w:t"):
            pieces.append((node, node.text or ""))
        elif node.tag in {qn("m:oMath"), qn("m:oMathPara"), qn("w:br")}:
            pieces.append((None, "\0"))
        elif node.tag == qn("w:tab"):
            pieces.append((None, "\0"))
    return pieces


def _strip_source_score_xml(xml: str, question_number: str | None) -> str:
    # Parse a fresh element and touch only ordinary Word text. OMML formulas,
    # drawing relationships, run styles and paragraph structure stay intact.
    root = parse_xml(xml)
    pieces = _source_score_xml_pieces(root)
    match = _source_score_span("".join(text for _, text in pieces), question_number)
    if match is None:
        raise ValueError("题干分值与 Word 文字无法一致清理，请核对原文件")
    start, end = match.span("score")
    offset = 0
    for node, text in pieces:
        if node is not None:
            node.text = "".join(
                char for index, char in enumerate(text, offset)
                if not start <= index < end
            )
        offset += len(text)
    return root.xml


def strip_question_source_score_blocks(
    blocks: list[dict[str, object]], *, question_number: str | None = None,
) -> list[dict[str, object]]:
    """Clean the first visible stem block on copies; never change answers."""
    cleaned = [dict(block) for block in blocks]
    for block in cleaned:
        text = str(block.get("text") or "")
        xml = str(block.get("xml") or "")
        if not text.strip() and xml.strip():
            text = "".join(value for _, value in _source_score_xml_pieces(parse_xml(xml)))
        if not clean_question_blocks([{"text": text}]):
            continue
        visible, _ = _stem_visible_characters(text)
        if not visible.strip():
            continue
        stripped = strip_question_source_score(text, question_number=question_number)
        if stripped != text:
            if xml.strip():
                block["xml"] = _strip_source_score_xml(xml, question_number)
            if str(block.get("text") or "").strip():
                block["text"] = stripped
        break
    return cleaned


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


class RichContentReadError(ValueError):
    """An existing sidecar cannot supply the supported content structure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parse_rich_content(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RichContentReadError("rich_content_unreadable") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RichContentReadError("rich_content_invalid_json") from exc
    if not isinstance(payload, dict) or not any(
        key in payload for key in ("question_blocks", "answer_blocks")
    ):
        raise RichContentReadError("rich_content_unknown_structure")
    for key in ("question_blocks", "answer_blocks"):
        blocks = payload.get(key, [])
        if not isinstance(blocks, list):
            raise RichContentReadError("rich_content_invalid_blocks")
        normalized: list[dict[str, object]] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise RichContentReadError("rich_content_invalid_blocks")
            text = block.get("text", "")
            xml = block.get("xml", "")
            relationships = block.get("image_relationships", {})
            if (
                not isinstance(text, str)
                or not isinstance(xml, str)
                or not isinstance(relationships, dict)
                or any(
                    not isinstance(name, str) or not isinstance(source, str)
                    for name, source in relationships.items()
                )
                or not any(name in block for name in ("text", "xml", "image_relationships"))
            ):
                raise RichContentReadError("rich_content_invalid_blocks")
            if xml.strip():
                try:
                    element = parse_xml(xml.encode("utf-8"))
                except (XMLSyntaxError, UnicodeError) as exc:
                    raise RichContentReadError("rich_content_invalid_xml") from exc
                if element.tag not in {qn("w:p"), qn("w:tbl")}:
                    raise RichContentReadError("rich_content_unknown_structure")
            normalized.append({**block, "text": text})
        payload[key] = clean_question_blocks(normalized) if key == "question_blocks" else normalized
    return payload


def load_question_rich_content(
    question_id: int,
    root: str | Path | None = None,
    *,
    strict: bool = False,
) -> dict[str, object] | None:
    path = rich_content_path(question_id, root)
    try:
        if not file_is_file(path):
            return None
        # Format metadata does not decide whether the supported blocks can be read.
        payload = cached_parsed_file(path, _parse_rich_content)
        stored_id = payload.get("question_id")
        if stored_id is not None and (
            isinstance(stored_id, bool)
            or str(stored_id) != str(int(question_id))
        ):
            raise RichContentReadError("rich_content_question_mismatch")
        return payload
    except OSError as exc:
        if strict:
            raise RichContentReadError("rich_content_unreadable") from exc
        return None
    except RichContentReadError:
        if strict:
            raise
        return None


def is_question_rich_content_current(question_id: int, root: str | Path | None = None) -> bool:
    return load_question_rich_content(question_id, root) is not None


__all__ = [
    "RichContentReadError",
    "clean_question_blocks",
    "is_question_rich_content_current",
    "load_question_rich_content",
    "save_question_rich_content",
    "strip_question_source_score",
    "strip_question_source_score_blocks",
]
