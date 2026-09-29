from __future__ import annotations

import hashlib
import html
import io
import re
from copy import deepcopy
from pathlib import Path
from typing import BinaryIO, Callable

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from question_bank.database.paths import project_data_root
from question_bank.importers.types import ExtractedDocument


_IMAGE_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]")
_NUMBERED_PARAGRAPH = re.compile(r"^[ \t]*(?:\d{1,3}[ \t]*[.．、]|[（(][ \t]*\d{1,3}[ \t]*[）)])")
_PUBLISHER_FOOTER = re.compile(r"声明\s*[:：].*(?:试题解析著作权|著作权属)")

def _numbering_prefix(paragraph, document, state: dict) -> tuple[str, int | None]:
    """Resolve the visible Word list label, including cancellation and restarts."""
    properties = []
    direct = paragraph._element.find(qn("w:pPr"))
    if direct is not None:
        properties.append(direct)
    style = paragraph.style
    seen_styles: set[str] = set()
    while style is not None and style.style_id not in seen_styles:
        seen_styles.add(style.style_id)
        if style.element.pPr is not None:
            properties.append(style.element.pPr)
        style = style.base_style
    values: dict[str, str] = {}
    for properties_element in properties:
        num_pr = properties_element.find(qn("w:numPr"))
        if num_pr is not None:
            for key in ("numId", "ilvl"):
                child = num_pr.find(qn(f"w:{key}"))
                if child is not None:
                    values.setdefault(key, child.get(qn("w:val"), ""))
    num_id = values.get("numId")
    if not num_id or num_id == "0":
        return "", None
    level = int(values.get("ilvl") or 0)
    definition = None
    override = None
    try:
        root = document.part.numbering_part.element
        instance = next((n for n in root.findall(qn("w:num")) if n.get(qn("w:numId")) == num_id), None)
        if instance is not None:
            abstract_ref = instance.find(qn("w:abstractNumId"))
            abstract_id = abstract_ref.get(qn("w:val")) if abstract_ref is not None else None
            abstract = next((n for n in root.findall(qn("w:abstractNum")) if n.get(qn("w:abstractNumId")) == abstract_id), None)
            if abstract is not None:
                definition = next((n for n in abstract.findall(qn("w:lvl")) if n.get(qn("w:ilvl")) == str(level)), None)
            override = next((n for n in instance.findall(qn("w:lvlOverride")) if n.get(qn("w:ilvl")) == str(level)), None)
            if override is not None and override.find(qn("w:lvl")) is not None:
                definition = override.find(qn("w:lvl"))
    except (AttributeError, KeyError, NotImplementedError):
        pass
    # Some older files have an unresolved list reference. Retain the previous
    # decimal fallback only for their top-level lists.
    if definition is None and level > 0:
        return "", level

    def value(name: str, default: str) -> str:
        node = definition.find(qn(f"w:{name}")) if definition is not None else None
        return node.get(qn("w:val"), default) if node is not None else default

    start = int(value("start", "1"))
    if override is not None:
        start_override = override.find(qn("w:startOverride"))
        if start_override is not None:
            start = int(start_override.get(qn("w:val"), str(start)))
    counters = state.setdefault("counters", {})
    key = (num_id, level)
    number = counters.get(key, start)
    counters[key] = number + 1
    for child_key in list(counters):
        if isinstance(child_key, tuple) and child_key[0] == num_id and child_key[1] > level:
            del counters[child_key]
    fmt = value("numFmt", "decimal")
    pattern = value("lvlText", f"%{level + 1}.")
    if fmt in {"none", "bullet"}:
        return (pattern if fmt == "bullet" else ""), level

    def format_number(n: int) -> str:
        if fmt in {"upperLetter", "lowerLetter"}:
            letters = ""
            while n > 0:
                n, digit = divmod(n - 1, 26)
                letters = chr(65 + digit) + letters
            return letters.lower() if fmt == "lowerLetter" else letters
        if fmt in {"chineseCounting", "chineseCountingThousand", "ideographTraditional"} and 0 < n < 100:
            digits = "零一二三四五六七八九"
            return digits[n] if n < 10 else (digits[n // 10] if n >= 20 else "") + "十" + (digits[n % 10] if n % 10 else "")
        return str(n)

    label = re.sub(r"%([1-9])", lambda m: format_number(number if int(m[1]) == level + 1 else counters.get((num_id, int(m[1]) - 1), 2) - 1), pattern)
    return label, level


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
    state: dict = {"counters": {}}
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
        # 图片本身是正常题目内容；具体缺图/转换异常由导入任务定位。
        needs_image_review=False,
        image_paths=image_paths,
        rich_paragraphs=rich_paragraphs,
    )


def _paragraph_record(
    paragraph,
    document,
    image_dir: Path,
    saved_images: dict[str, str],
    state: dict | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> dict[str, object]:
    display_element = deepcopy(paragraph._element)
    display_paragraph = Paragraph(display_element, paragraph._parent)
    image_relationships: dict[str, str] = {}
    floating_paths: list[str] = []
    warnings: list[str] = []

    def render_image(element) -> str:
        relationship_id = _image_relationship_id(element)
        if not relationship_id:
            return ""
        try:
            image_path = _save_related_image(
                document,
                relationship_id,
                image_dir,
                saved_images,
                register_created_file=register_created_file,
                write_created_file=write_created_file,
                image_element=element,
            )
        except (OSError, ValueError):
            image_path = None
        if image_path:
            display_id = relationship_id
            if relationship_id in image_relationships and image_relationships[relationship_id] != image_path:
                display_id = f"{relationship_id}_display_{len(image_relationships)}"
                element.set(_IMAGE_REL_ATTR if _local_name(element) == "blip" else qn("r:id"), display_id)
            image_relationships[display_id] = image_path
            picture = next((p for p in element.iterancestors() if _local_name(p) == "pic"), None)
            if picture is not None:
                # These display operations are already baked into the image.
                # Clear them in the retained XML so Word export applies them once.
                for node in list(picture.iter()):
                    if _local_name(node) == "srcRect":
                        node.getparent().remove(node)
                    elif _local_name(node) == "xfrm":
                        for key in ("rot", "flipH", "flipV"):
                            node.attrib.pop(key, None)
            if any(_local_name(parent) == "anchor" or (_local_name(parent) == "shape" and "position:absolute" in parent.get("style", "").replace(" ", "")) for parent in element.iterancestors()):
                floating_paths.append(image_path)
            return f"[[IMAGE:{image_path}]]"
        warnings.append("有一张配图无法读取或转换，请核对原文件中的图片格式。")
        return "[图片未能读取]"

    text = _get_paragraph_rich_text(display_paragraph, render_image=render_image).strip()
    prefix, numbering_level = _numbering_prefix(paragraph, document, state if state is not None else {}) if text else ("", None)
    if prefix and not _NUMBERED_PARAGRAPH.match(_IMAGE_MARKER.sub("", text).strip()):
        text = f"{prefix} {text}"
    return {
        "text": text,
        "xml": display_element.xml,
        "image_relationships": image_relationships,
        "numbering_level": numbering_level,
        "floating_image_paths": list(dict.fromkeys(floating_paths)),
        "images_in_text_order": True,
        "parse_warnings": warnings,
    }


def _table_record(
    table: Table,
    document,
    image_dir: Path,
    saved_images: dict[str, str],
    state: dict | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
) -> dict[str, object]:
    table = Table(deepcopy(table._tbl), table._parent)
    rows_html: list[str] = []
    image_relationships: dict[str, str] = {}
    warnings: list[str] = []
    for row in table.rows:
        cells_html: list[str] = []
        seen_cells: set = set()
        for cell in row.cells:
            if cell._tc in seen_cells:
                continue
            seen_cells.add(cell._tc)
            cell_parts: list[str] = []
            for child in list(cell._tc.iterchildren()):  # noqa: SLF001 - needed for document-order table traversal.
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
                display_child = parse_xml(str(record["xml"]))
                for relationship_id, path in (record.get("image_relationships") or {}).items():
                    display_id = relationship_id
                    if display_id in image_relationships and image_relationships[display_id] != path:
                        display_id = f"{relationship_id}_cell_{len(image_relationships)}"
                        for element in display_child.iter():
                            for attribute in (_IMAGE_REL_ATTR, qn("r:id")):
                                if element.get(attribute) == relationship_id:
                                    element.set(attribute, display_id)
                    image_relationships[display_id] = path
                cell._tc.replace(child, display_child)
                warnings.extend(record.get("parse_warnings") or [])
            cell_text = "<br>".join(cell_parts)
            cells_html.append(f"<td>{cell_text}</td>")
        rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    return {
        "text": "<table><tbody>" + "".join(rows_html) + "</tbody></table>" if rows_html else "",
        "xml": table._element.xml,  # noqa: SLF001 - needed to preserve source table shape.
        "image_relationships": image_relationships,
        "parse_warnings": warnings,
        "images_in_text_order": True,
    }


def _visible_children(element):
    """Read a single displayed branch of OOXML compatibility content."""
    if _local_name(element) == "AlternateContent":
        branches = list(element)
        selected = next((b for b in branches if _local_name(b) == "Choice" and any(_local_name(n) in {"blip", "oMath", "imagedata", "txbxContent"} for n in b.iter())), None)
        if selected is None:
            selected = next((b for b in branches if _local_name(b) == "Fallback"), None)
        return list(selected) if selected is not None else []
    return list(element)


def _image_relationship_id(element) -> str | None:
    if _local_name(element) == "blip":
        return element.get(_IMAGE_REL_ATTR)
    if _local_name(element) == "imagedata":
        return element.get(qn("r:id"))
    return None


def _get_paragraph_rich_text(paragraph, *, render_image: Callable | None = None) -> str:
    parts = []

    def traverse(element):
        local_name = _local_name(element)

        if element.tag == qn("w:r"):
            run = Run(element, paragraph)
            for child in _visible_children(element):
                name = _local_name(child)
                if name not in {"t", "tab", "br", "cr"}:
                    if name != "rPr":
                        traverse(child)
                    continue
                text = html.escape(child.text or "", quote=False) if name == "t" else ("\t" if name == "tab" else "\n")
                vert_align = _run_property_value(element, "vertAlign")
                if text.strip() and (run.font.superscript or vert_align == "superscript"):
                    text = f"<sup>{text}</sup>"
                elif text.strip() and (run.font.subscript or vert_align == "subscript"):
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
        elif local_name in ("oMath", "oMathPara"):
            parts.append(_math_text(element))
        elif local_name in {"blip", "imagedata"}:
            if render_image is not None:
                parts.append(render_image(element))
        elif local_name in {"del", "instrText", "pPr"}:
            return
        else:
            for child in _visible_children(element):
                traverse(child)

    for child in paragraph._element.iterchildren():
        traverse(child)

    return "".join(parts).strip()


def _math_text(element) -> str:
    local_name = _local_name(element)
    if local_name == "t":
        return html.escape(element.text or "", quote=False)
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
        if degree.strip() and degree.strip() not in {"2", "²"}:
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
    state: dict | None = None,
    *,
    register_created_file: Callable[[Path], None] | None = None,
    write_created_file: Callable[[Path, bytes], None] | None = None,
):
    in_publisher_footer = False
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            visible = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
            if _PUBLISHER_FOOTER.search(visible):
                in_publisher_footer = True
                continue
            if in_publisher_footer:
                if re.match(r"^\d{1,3}\s*[.．、]\s*\S", visible):
                    in_publisher_footer = False
                else:
                    continue
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
    def walk(element):
        relationship_id = _image_relationship_id(element)
        if relationship_id and relationship_id not in relationship_ids:
            relationship_ids.append(relationship_id)
        for child in _visible_children(element):
            walk(child)
    walk(paragraph._element)
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
    image_element=None,
) -> str | None:
    related_part = document.part.related_parts.get(relationship_id)
    blob = getattr(related_part, "blob", None)
    if not blob:
        return None
    suffix = Path(str(getattr(related_part, "partname", ""))).suffix or ".png"
    crop = None
    transform = None
    if image_element is not None:
        picture = next((p for p in image_element.iterancestors() if _local_name(p) == "pic"), None)
        if picture is not None:
            crop = next((n for n in picture.iter() if _local_name(n) == "srcRect"), None)
            transform = next((n for n in picture.iter() if _local_name(n) == "xfrm"), None)
    crop_values = tuple(int(crop.get(key, "0")) for key in ("l", "t", "r", "b")) if crop is not None else (0, 0, 0, 0)
    rotation = int(transform.get("rot", "0")) if transform is not None else 0
    flip_h = transform is not None and transform.get("flipH", "0") in {"1", "true"}
    flip_v = transform is not None and transform.get("flipV", "0") in {"1", "true"}
    cache_key = relationship_id
    if any(crop_values) or rotation or flip_h or flip_v:
        cache_key += ":" + repr((crop_values, rotation, flip_h, flip_v))
    if cache_key in saved_images:
        return saved_images[cache_key]
    if cache_key != relationship_id or suffix.lower() in {".emf", ".wmf", ".bmp", ".tif", ".tiff"}:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(blob)) as source:
            picture_image = source.convert("RGBA")
            width, height = picture_image.size
            left, top, right, bottom = crop_values
            box = (round(width * left / 100000), round(height * top / 100000), round(width * (1 - right / 100000)), round(height * (1 - bottom / 100000)))
            if box[2] > box[0] and box[3] > box[1]:
                picture_image = picture_image.crop(box)
            if flip_h:
                picture_image = ImageOps.mirror(picture_image)
            if flip_v:
                picture_image = ImageOps.flip(picture_image)
            if rotation:
                picture_image = picture_image.rotate(-rotation / 60000, expand=True)
            output = io.BytesIO()
            picture_image.save(output, format="PNG")
            blob = output.getvalue()
            suffix = ".png"
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
    saved_images[cache_key] = str(output_path)
    return saved_images[cache_key]


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
