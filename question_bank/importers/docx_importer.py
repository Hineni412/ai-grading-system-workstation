from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import BinaryIO, Callable

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from question_bank.database.paths import project_data_root
from question_bank.importers.types import ExtractedDocument


_IMAGE_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]")
_NUMBERED_PARAGRAPH = re.compile(r"^[ \t]*(?:\d{1,3}[ \t]*[.．、]|[（(][ \t]*\d{1,3}[ \t]*[）)])")
_NUMPR_NUMID = re.compile(r'<w:numId w:val="(\d+)"\s*/>')
_NUMPR_ILVL = re.compile(r'<w:ilvl w:val="(\d+)"\s*/>')


def import_docx(
    source_file: str | Path | BinaryIO,
    *,
    source_name: str | Path | None = None,
    asset_root: str | Path | None = None,
    asset_root_is_output_dir: bool = False,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> ExtractedDocument:
    if isinstance(source_file, (str, Path)):
        document_source: str | Path | BinaryIO = source_file
        path = Path(source_name) if source_name is not None else Path(source_file)
    else:
        document_source = source_file
        path = Path(source_name or "document.docx")
    document = Document(document_source)
    image_dir = (
        Path(asset_root)
        if asset_root_is_output_dir and asset_root is not None
        else _image_output_dir(path, asset_root=asset_root)
    )
    saved_images: dict[str, str] = {}
    state: dict[str, dict[str, int]] = {"counters": {}}
    rich_paragraphs = [
        record
        for record in _iter_document_records(
            document,
            image_dir,
            saved_images,
            state,
            register_created_file=register_created_file,
            write_created_file=write_created_file,
        )
        if record["text"]
    ]
    rich_paragraphs = _move_floating_image_paragraphs_to_following_question(rich_paragraphs)
    paragraphs = [str(record["text"]) for record in rich_paragraphs]
    image_paths = list(saved_images.values())
    has_images = bool(image_paths) or bool(document.inline_shapes) or any(
        "image" in str(relationship.target_ref).lower()
        for relationship in document.part.rels.values()
    )
    return ExtractedDocument(
        source_file=str(path),
        page_range="document",
        text="\n".join(paragraphs),
        has_images=has_images,
        needs_image_review=has_images,
        image_paths=image_paths,
        rich_paragraphs=rich_paragraphs,
    )


def _paragraph_record(
    paragraph,
    document,
    image_dir: Path,
    saved_images: dict[str, str],
    state: dict[str, dict[str, int]] | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> dict[str, object]:
    parts: list[str] = []
    try:
        text = _get_paragraph_rich_text(paragraph).strip()
    except Exception:
        text = paragraph.text.strip()

    xml = paragraph._element.xml
    numbering_level: int | None = None
    if text and "<w:numPr>" in xml:
        ilvl_match = _NUMPR_ILVL.search(xml)
        # Word 缺省层级即 0；只有主层级自动编号才补题号前缀。
        numbering_level = int(ilvl_match.group(1)) if ilvl_match else 0
        if numbering_level == 0 and not _NUMBERED_PARAGRAPH.match(text):
            numid_match = _NUMPR_NUMID.search(xml)
            # 每个编号列表（numId）独立计数，答案区另起列表时从 1 重新开始。
            list_key = numid_match.group(1) if numid_match else "default"
            counters = state.setdefault("counters", {}) if state is not None else None
            num = counters.get(list_key, 1) if counters is not None else 1
            text = f"{num}. {text}"
            if counters is not None:
                counters[list_key] = num + 1

    if text:
        parts.append(text)
    image_relationships: dict[str, str] = {}
    for relationship_id in _paragraph_image_relationship_ids(paragraph):
        image_path = _save_related_image(
            document,
            relationship_id,
            image_dir,
            saved_images,
            register_created_file=register_created_file,
            write_created_file=write_created_file,
        )
        if image_path:
            image_relationships[relationship_id] = image_path
            parts.append(f"[[IMAGE:{image_path}]]")
    return {
        "text": "\n".join(parts).strip(),
        "xml": paragraph._element.xml,  # noqa: SLF001 - needed to preserve Word math and inline drawings.
        "image_relationships": image_relationships,
        "numbering_level": numbering_level,
    }


def _table_record(
    table: Table,
    document,
    image_dir: Path,
    saved_images: dict[str, str],
    state: dict[str, dict[str, int]] | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> dict[str, object]:
    rows_html: list[str] = []
    image_relationships: dict[str, str] = {}
    for row in table.rows:
        cells_html: list[str] = []
        for cell in row.cells:
            cell_parts: list[str] = []
            for child in cell._tc.iterchildren():  # noqa: SLF001 - needed for document-order table traversal.
                if child.tag == qn("w:p"):
                    record = _paragraph_record(
                        Paragraph(child, cell),
                        document,
                        image_dir,
                        saved_images,
                        state,
                        register_created_file=register_created_file,
                        write_created_file=write_created_file,
                    )
                elif child.tag == qn("w:tbl"):
                    record = _table_record(
                        Table(child, cell),
                        document,
                        image_dir,
                        saved_images,
                        state,
                        register_created_file=register_created_file,
                        write_created_file=write_created_file,
                    )
                else:
                    continue
                text = str(record.get("text") or "").strip()
                if text:
                    cell_parts.append(text)
                image_relationships.update(record.get("image_relationships") or {})
            cell_text = "<br>".join(cell_parts)
            cells_html.append(f"<td>{cell_text}</td>")
        rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    return {
        "text": "<table><tbody>" + "".join(rows_html) + "</tbody></table>" if rows_html else "",
        "xml": table._element.xml,  # noqa: SLF001 - needed to preserve source table shape.
        "image_relationships": image_relationships,
    }


def _get_paragraph_rich_text(paragraph) -> str:
    parts = []

    def traverse(element):
        local_name = _local_name(element)

        if local_name == "r":
            run = Run(element, paragraph)
            text = run.text
            if text:
                vert_align = _run_property_value(element, "vertAlign")
                if run.font.superscript or vert_align == "superscript":
                    text = f"<sup>{text}</sup>"
                elif run.font.subscript or vert_align == "subscript":
                    text = f"<sub>{text}</sub>"
                underline_value = _run_property_value(element, "u")
                if (
                    run.font.underline
                    or getattr(run, "underline", False)
                    or (underline_value is not None and underline_value not in ("none", "0", "false"))
                ):
                    text = _visible_underlined_text(text)
                    text = f"<u>{text}</u>"
                parts.append(text)
            for child in element.iterchildren():
                if _local_name(child) in ("oMath", "oMathPara"):
                    parts.append(_math_text(child))
        elif local_name in ("oMath", "oMathPara"):
            parts.append(_math_text(element))
        elif local_name == "hyperlink":
            for child in element.iterchildren():
                traverse(child)
        else:
            for child in element.iterchildren():
                traverse(child)

    for child in paragraph._element.iterchildren():
        traverse(child)

    return "".join(parts).strip()


def _math_text(element) -> str:
    local_name = _local_name(element)
    if local_name == "t":
        return element.text or ""
    if local_name == "sSup":
        base = _math_named_child_text(element, "e")
        superscript = _math_named_child_text(element, "sup")
        return f"{base}<sup>{superscript}</sup>" if superscript else base
    if local_name == "sSub":
        base = _math_named_child_text(element, "e")
        subscript = _math_named_child_text(element, "sub")
        return f"{base}<sub>{subscript}</sub>" if subscript else base
    if local_name == "sSubSup":
        base = _math_named_child_text(element, "e")
        subscript = _math_named_child_text(element, "sub")
        superscript = _math_named_child_text(element, "sup")
        if subscript:
            base = f"{base}<sub>{subscript}</sub>"
        if superscript:
            base = f"{base}<sup>{superscript}</sup>"
        return base
    if local_name == "f":
        numerator = _math_named_child_text(element, "num")
        denominator = _math_named_child_text(element, "den")
        if numerator or denominator:
            return f"({numerator or '□'})/({denominator or '□'})"
    if local_name == "rad":
        degree = _math_named_child_text(element, "deg")
        radicand = _math_named_child_text(element, "e")
        if not radicand:
            return ""
        if degree and degree.strip() not in {"2", "²"}:
            return f"<sup>{degree}</sup>√({radicand})"
        return f"√({radicand})"
    if local_name == "limLow":
        base = _math_named_child_text(element, "e")
        lower = _math_named_child_text(element, "lim")
        return f"{base}<sub>{lower}</sub>" if lower else base
    if local_name == "limUpp":
        base = _math_named_child_text(element, "e")
        upper = _math_named_child_text(element, "lim")
        return f"{base}<sup>{upper}</sup>" if upper else base
    if local_name == "d":
        expressions = [
            _math_text(child)
            for child in element.iterchildren()
            if _local_name(child) == "e"
        ]
        begin = _math_delimiter_character(element, "begChr", "(")
        end = _math_delimiter_character(element, "endChr", ")")
        separator = _math_delimiter_character(element, "sepChr", "，")
        return f"{begin}{separator.join(expressions)}{end}"
    return "".join(_math_text(child) for child in element.iterchildren())


def _math_named_child_text(element, name: str) -> str:
    for child in element.iterchildren():
        if _local_name(child) == name:
            return _math_text(child)
    return ""


def _math_delimiter_character(element, property_name: str, default: str) -> str:
    for child in element.iterchildren():
        if _local_name(child) != "dPr":
            continue
        for property_element in child.iterchildren():
            if _local_name(property_element) != property_name:
                continue
            for key, value in property_element.attrib.items():
                if str(key).split("}")[-1] == "val":
                    return str(value)
    return default


def _run_property_value(run_element, property_name: str) -> str | None:
    for child in run_element.iterchildren():
        if _local_name(child) != "rPr":
            continue
        for property_element in child.iterchildren():
            if _local_name(property_element) != property_name:
                continue
            return property_element.get(qn("w:val")) or "true"
    return None


def _visible_underlined_text(text: str) -> str:
    return str(text).replace(" ", "\u00a0").replace("\t", "\u00a0" * 4) if not str(text).strip() else text


def _local_name(element) -> str:
    tag = str(element.tag)
    return tag.split("}")[-1] if "}" in tag else tag


def _iter_paragraphs(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield from _iter_table_paragraphs(Table(child, document))


def _iter_table_paragraphs(table: Table):
    for row in table.rows:
        for cell in row.cells:
            for child in cell._tc.iterchildren():  # noqa: SLF001 - needed for document-order table traversal.
                if child.tag == qn("w:p"):
                    yield Paragraph(child, cell)
                elif child.tag == qn("w:tbl"):
                    yield from _iter_table_paragraphs(Table(child, cell))


def _iter_document_records(
    document,
    image_dir: Path,
    saved_images: dict[str, str],
    state: dict[str, dict[str, int]] | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield _paragraph_record(
                Paragraph(child, document),
                document,
                image_dir,
                saved_images,
                state,
                register_created_file=register_created_file,
                write_created_file=write_created_file,
            )
        elif child.tag == qn("w:tbl"):
            yield _table_record(
                Table(child, document),
                document,
                image_dir,
                saved_images,
                state,
                register_created_file=register_created_file,
                write_created_file=write_created_file,
            )


def _paragraph_image_relationship_ids(paragraph) -> list[str]:
    relationship_ids: list[str] = []
    for element in paragraph._element.iter():  # noqa: SLF001 - python-docx exposes drawing XML only here.
        if str(element.tag).endswith("}blip"):
            relationship_id = element.get(_IMAGE_REL_ATTR)
            if relationship_id and relationship_id not in relationship_ids:
                relationship_ids.append(relationship_id)
    return relationship_ids


def _move_floating_image_paragraphs_to_following_question(
    rich_paragraphs: list[dict[str, object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    pending_images: list[dict[str, object]] = []
    seen_question = False
    for record in rich_paragraphs:
        text = str(record.get("text") or "").strip()
        is_question_marker = _NUMBERED_PARAGRAPH.match(text) is not None
        if is_question_marker:
            seen_question = True
        if is_question_marker and pending_images:
            normalized.append(record)
            normalized.extend(pending_images)
            pending_images = []
            continue
        if _is_floating_image_only_record(record) and not seen_question:
            pending_images.append(record)
            continue
        if pending_images:
            normalized.extend(pending_images)
            pending_images = []
        normalized.append(record)
    normalized.extend(pending_images)
    return normalized


def _is_floating_image_only_record(record: dict[str, object]) -> bool:
    text = str(record.get("text") or "").strip()
    xml = str(record.get("xml") or "")
    return bool(text) and not _IMAGE_MARKER.sub("", text).strip() and "<wp:anchor" in xml


def _save_related_image(
    document,
    relationship_id: str,
    image_dir: Path,
    saved_images: dict[str, str],
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> str | None:
    if relationship_id in saved_images:
        return saved_images[relationship_id]
    related_part = document.part.related_parts.get(relationship_id)
    blob = getattr(related_part, "blob", None)
    if not blob:
        return None
    suffix = Path(str(getattr(related_part, "partname", ""))).suffix or ".png"
    digest = hashlib.sha1(blob).hexdigest()[:12]
    output_path = image_dir / f"{relationship_id}_{digest}{suffix}"
    if write_created_file is not None:
        write_created_file(output_path, bytes(blob))
    else:
        image_dir.mkdir(parents=True, exist_ok=True)
        if not output_path.exists():
            if register_created_file is not None:
                register_created_file(output_path)
            output_path.write_bytes(blob)
    saved_images[relationship_id] = str(output_path)
    return saved_images[relationship_id]


def _image_output_dir(
    source_file: Path,
    *,
    asset_root: str | Path | None = None,
) -> Path:
    digest = hashlib.sha1(str(source_file.resolve()).encode("utf-8")).hexdigest()[:10]
    root = (
        Path(asset_root)
        if asset_root is not None
        else project_data_root() / "question_bank" / "extracted_images"
    )
    return root / f"{source_file.stem}_{digest}"
