from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


_FORMULA_HINT = re.compile(
    r"[=±×÷√∑∫∞≈≠≤≥]|\\(?:frac|sqrt|sum|int)\b"
)
_SLIDE_FILE = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")
_DRAWABLE_TAGS = {
    "sp",
    "pic",
    "graphicFrame",
    "grpSp",
    "cxnSp",
    "oleObj",
    "video",
    "audio",
    "control",
}


@dataclass(frozen=True, slots=True)
class ParsedMaterialUnit:
    unit_kind: str
    unit_index: int
    title: str | None
    extracted_text: str
    text_status: str
    formula_review_required: bool
    object_summary: dict[str, object]
    preview_png: bytes


class MaterialParser:
    def parse(
        self,
        path: Path,
        *,
        material_type: str,
    ) -> tuple[ParsedMaterialUnit, ...]:
        if material_type == "pdf":
            return self._parse_pdf(path)
        if material_type == "pptx":
            return self._parse_pptx(path)
        if material_type == "image":
            return self._parse_image(path)
        raise TeachingPrepValidationError("material type is unsupported")

    @staticmethod
    def _parse_pdf(path: Path) -> tuple[ParsedMaterialUnit, ...]:
        import fitz

        try:
            document = fitz.open(path)
        except Exception as exc:
            raise TeachingPrepValidationError(
                "PDF could not be opened"
            ) from exc
        units: list[ParsedMaterialUnit] = []
        try:
            for index, page in enumerate(document, start=1):
                text = str(page.get_text() or "").strip()
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                units.append(
                    ParsedMaterialUnit(
                        unit_kind="pdf_page",
                        unit_index=index,
                        title=_first_line(text),
                        extracted_text=text,
                        text_status="embedded" if text else "empty",
                        formula_review_required=bool(
                            text and _FORMULA_HINT.search(text)
                        ),
                        object_summary={
                            "preview_kind": "rendered",
                            "width": int(pixmap.width),
                            "height": int(pixmap.height),
                        },
                        preview_png=pixmap.tobytes("png"),
                    )
                )
        finally:
            document.close()
        return tuple(units)

    @staticmethod
    def _parse_pptx(path: Path) -> tuple[ParsedMaterialUnit, ...]:
        try:
            with zipfile.ZipFile(path) as archive:
                slide_names = sorted(
                    (
                        (int(match.group(1)), name)
                        for name in archive.namelist()
                        if (match := _SLIDE_FILE.fullmatch(name))
                    ),
                    key=lambda item: item[0],
                )
                if not slide_names:
                    raise TeachingPrepValidationError(
                        "PPTX contains no readable slides"
                    )
                slide_width, slide_height = _pptx_slide_size(archive)
                units = [
                    _pptx_slide_unit(
                        archive.read(name),
                        index=index,
                        slide_width=slide_width,
                        slide_height=slide_height,
                    )
                    for index, name in slide_names
                ]
        except TeachingPrepValidationError:
            raise
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise TeachingPrepValidationError(
                "PPTX could not be opened"
            ) from exc
        return tuple(units)

    @staticmethod
    def _parse_image(path: Path) -> tuple[ParsedMaterialUnit, ...]:
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((1600, 1600))
                output = io.BytesIO()
                image.save(output, format="PNG")
                width, height = image.size
        except (OSError, ValueError) as exc:
            raise TeachingPrepValidationError(
                "image could not be opened"
            ) from exc
        return (
            ParsedMaterialUnit(
                unit_kind="image",
                unit_index=1,
                title=path.stem,
                extracted_text="",
                text_status="not_applicable",
                formula_review_required=False,
                object_summary={
                    "preview_kind": "rendered",
                    "width": width,
                    "height": height,
                },
                preview_png=output.getvalue(),
            ),
        )


def _pptx_slide_size(archive: zipfile.ZipFile) -> tuple[int, int]:
    try:
        root = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
        for element in root.iter():
            if _local_name(element.tag) != "sldSz":
                continue
            width = int(element.attrib.get("cx") or 0)
            height = int(element.attrib.get("cy") or 0)
            if width > 0 and height > 0:
                return width, height
    except (KeyError, ValueError, ElementTree.ParseError):
        pass
    return 12_192_000, 6_858_000


def _pptx_slide_unit(
    xml_bytes: bytes,
    *,
    index: int,
    slide_width: int,
    slide_height: int,
) -> ParsedMaterialUnit:
    root = ElementTree.fromstring(xml_bytes)
    texts = [
        str(element.text or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "t" and str(element.text or "").strip()
    ]
    text = "\n".join(texts)
    types = Counter(
        _local_name(element.tag)
        for element in root.iter()
        if _local_name(element.tag) in _DRAWABLE_TAGS
    )
    preview = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(preview)
    draw.rectangle((0, 0, 959, 539), outline=(198, 203, 208), width=2)
    rectangles = _shape_rectangles(root, slide_width, slide_height)
    for position, (kind, box) in enumerate(rectangles):
        color = {
            "pic": (73, 101, 121),
            "graphicFrame": (94, 98, 141),
            "grpSp": (154, 103, 24),
        }.get(kind, (19, 94, 107))
        draw.rectangle(box, outline=color, width=2)
        if position >= 80:
            break
    title = _first_line(text) or f"Slide {index}"
    safe_title = title.encode("ascii", "replace").decode("ascii")[:100]
    draw.text((20, 14), safe_title, fill=(28, 39, 51))
    output = io.BytesIO()
    preview.save(output, format="PNG")
    return ParsedMaterialUnit(
        unit_kind="ppt_slide",
        unit_index=index,
        title=title,
        extracted_text=text,
        text_status="embedded" if text else "empty",
        formula_review_required=bool(text and _FORMULA_HINT.search(text)),
        object_summary={
            "preview_kind": "structural",
            "object_count": sum(types.values()),
            "object_types": dict(sorted(types.items())),
            "occupied_boxes": [
                {
                    "x": round(box[0] / 960, 6),
                    "y": round(box[1] / 540, 6),
                    "width": round((box[2] - box[0]) / 960, 6),
                    "height": round((box[3] - box[1]) / 540, 6),
                }
                for _kind, box in rectangles[:200]
            ],
        },
        preview_png=output.getvalue(),
    )


def _shape_rectangles(
    root: ElementTree.Element,
    slide_width: int,
    slide_height: int,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    rectangles: list[tuple[str, tuple[int, int, int, int]]] = []
    for element in root.iter():
        kind = _local_name(element.tag)
        if kind not in _DRAWABLE_TAGS:
            continue
        offset = None
        extent = None
        for child in element.iter():
            local = _local_name(child.tag)
            if local == "off" and offset is None:
                offset = child
            elif local == "ext" and extent is None:
                extent = child
        if offset is None or extent is None:
            continue
        try:
            left = int(offset.attrib.get("x") or 0)
            top = int(offset.attrib.get("y") or 0)
            width = int(extent.attrib.get("cx") or 0)
            height = int(extent.attrib.get("cy") or 0)
        except ValueError:
            continue
        x0 = max(0, min(959, round(left / slide_width * 960)))
        y0 = max(0, min(539, round(top / slide_height * 540)))
        x1 = max(x0 + 1, min(959, round((left + width) / slide_width * 960)))
        y1 = max(y0 + 1, min(539, round((top + height) / slide_height * 540)))
        rectangles.append((kind, (x0, y0, x1, y1)))
    return rectangles


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:160]
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
