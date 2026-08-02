from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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


@dataclass(frozen=True, slots=True)
class ParsedMaterialText:
    extracted_text: str
    formula_review_required: bool
    printed_page_number: int | None


class MaterialParser:
    def __init__(
        self,
        *,
        ocr_engine_factory: Callable[[], object] | None = None,
    ) -> None:
        self._ocr_engine_factory = (
            ocr_engine_factory or _default_ocr_engine
        )

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

    def unit_count(self, path: Path, *, material_type: str) -> int:
        if material_type == "pdf":
            import fitz

            try:
                with fitz.open(path) as document:
                    return int(document.page_count)
            except Exception as exc:
                raise TeachingPrepValidationError(
                    "PDF could not be opened"
                ) from exc
        if material_type == "pptx":
            try:
                with zipfile.ZipFile(path) as archive:
                    count = sum(
                        1
                        for name in archive.namelist()
                        if _SLIDE_FILE.fullmatch(name)
                    )
            except (OSError, zipfile.BadZipFile) as exc:
                raise TeachingPrepValidationError(
                    "PPTX could not be opened"
                ) from exc
            if count <= 0:
                raise TeachingPrepValidationError(
                    "PPTX contains no readable slides"
                )
            return count
        if material_type == "image":
            return 1
        raise TeachingPrepValidationError("material type is unsupported")

    def iter_preview_units(
        self,
        path: Path,
        *,
        material_type: str,
        unit_indexes: Iterable[int] | None = None,
    ) -> Iterator[ParsedMaterialUnit]:
        requested = (
            None
            if unit_indexes is None
            else {int(index) for index in unit_indexes if int(index) > 0}
        )
        if material_type == "pdf":
            yield from self._iter_pdf_preview_units(path, requested=requested)
            return
        units = self.parse(path, material_type=material_type)
        for unit in units:
            if requested is None or unit.unit_index in requested:
                yield unit

    def create_ocr_engine(self) -> object:
        return self._ocr_engine_factory()

    @staticmethod
    def ocr_preview(
        preview_png: bytes,
        ocr_engine: object,
    ) -> ParsedMaterialText:
        text, printed_page_number = _ocr_page(preview_png, ocr_engine)
        return ParsedMaterialText(
            extracted_text=text,
            formula_review_required=bool(text and _FORMULA_HINT.search(text)),
            printed_page_number=printed_page_number,
        )

    @staticmethod
    def _iter_pdf_preview_units(
        path: Path,
        *,
        requested: set[int] | None,
    ) -> Iterator[ParsedMaterialUnit]:
        import fitz

        try:
            document = fitz.open(path)
        except Exception as exc:
            raise TeachingPrepValidationError(
                "PDF could not be opened"
            ) from exc
        try:
            for index, page in enumerate(document, start=1):
                if requested is not None and index not in requested:
                    continue
                text = str(page.get_text() or "").strip()
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(1.5, 1.5),
                    alpha=False,
                )
                printed_page_number = _visible_printed_page_number(page)
                object_summary: dict[str, object] = {
                    "preview_kind": "rendered",
                    "width": int(pixmap.width),
                    "height": int(pixmap.height),
                    "has_page_images": bool(page.get_images(full=True)),
                }
                if printed_page_number is not None:
                    object_summary.update(
                        {
                            "printed_page_number": printed_page_number,
                            "printed_page_number_source": (
                                "visible_footer_or_header"
                            ),
                        }
                    )
                yield ParsedMaterialUnit(
                    unit_kind="pdf_page",
                    unit_index=index,
                    title=_first_line(text),
                    extracted_text=text,
                    text_status="embedded" if text else "empty",
                    formula_review_required=bool(
                        text and _FORMULA_HINT.search(text)
                    ),
                    object_summary=object_summary,
                    preview_png=pixmap.tobytes("png"),
                )
        finally:
            document.close()

    def _parse_pdf(self, path: Path) -> tuple[ParsedMaterialUnit, ...]:
        import fitz

        try:
            document = fitz.open(path)
        except Exception as exc:
            raise TeachingPrepValidationError(
                "PDF could not be opened"
            ) from exc
        units: list[ParsedMaterialUnit] = []
        ocr_engine: object | None = None
        ocr_unavailable = False
        try:
            for index, page in enumerate(document, start=1):
                text = str(page.get_text() or "").strip()
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                preview_png = pixmap.tobytes("png")
                printed_page_number = _visible_printed_page_number(page)
                object_summary: dict[str, object] = {
                    "preview_kind": "rendered",
                    "width": int(pixmap.width),
                    "height": int(pixmap.height),
                }
                if (
                    not text
                    and page.get_images(full=True)
                    and not ocr_unavailable
                ):
                    if ocr_engine is None:
                        try:
                            ocr_engine = self._ocr_engine_factory()
                        except Exception:
                            ocr_unavailable = True
                    if ocr_engine is not None:
                        text, ocr_page_number = _ocr_page(
                            preview_png,
                            ocr_engine,
                        )
                        if text:
                            object_summary["text_source"] = "local_ocr"
                        if printed_page_number is None:
                            printed_page_number = ocr_page_number
                if printed_page_number is not None:
                    object_summary.update(
                        {
                            "printed_page_number": printed_page_number,
                            "printed_page_number_source": (
                                "local_ocr_footer_or_header"
                                if object_summary.get("text_source")
                                == "local_ocr"
                                else "visible_footer_or_header"
                            ),
                        }
                    )
                units.append(
                    ParsedMaterialUnit(
                        unit_kind="pdf_page",
                        unit_index=index,
                        title=_first_line(text),
                        extracted_text=text,
                        text_status=(
                            "embedded"
                            if text
                            and object_summary.get("text_source")
                            != "local_ocr"
                            else "empty"
                        ),
                        formula_review_required=bool(
                            text and _FORMULA_HINT.search(text)
                        ),
                        object_summary=object_summary,
                        preview_png=preview_png,
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


def _default_ocr_engine() -> object:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr_page(
    preview_png: bytes,
    ocr_engine: object,
) -> tuple[str, int | None]:
    try:
        import numpy as np

        with Image.open(io.BytesIO(preview_png)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            image = np.asarray(rgb)[:, :, ::-1]
        raw = ocr_engine(image)  # type: ignore[operator]
    except Exception:
        return "", None
    result: Any = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(result, list):
        return "", None
    text_lines: list[str] = []
    page_number: int | None = None
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        text = str(item[1] or "").strip()
        try:
            confidence = float(item[2])
        except (TypeError, ValueError):
            confidence = 0.0
        if text and confidence >= 0.35:
            text_lines.append(text)
        if page_number is None and confidence >= 0.55:
            page_number = _ocr_footer_page_number(
                item[0],
                text,
                width=width,
                height=height,
            )
    return "\n".join(text_lines), page_number


def _ocr_footer_page_number(
    raw_box: object,
    text: str,
    *,
    width: int,
    height: int,
) -> int | None:
    if not re.fullmatch(r"[0-9]{1,3}", text):
        return None
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
        return None
    try:
        xs = [float(point[0]) for point in raw_box]
        ys = [float(point[1]) for point in raw_box]
    except (TypeError, ValueError, IndexError):
        return None
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    if not (width * 0.25 <= center_x <= width * 0.75):
        return None
    if not (center_y <= height * 0.1 or center_y >= height * 0.9):
        return None
    value = int(text)
    return value if value > 0 else None


def _visible_printed_page_number(page: object) -> int | None:
    rect = getattr(page, "rect", None)
    height = float(getattr(rect, "height", 0) or 0)
    if height <= 0:
        return None
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return None
    candidates: set[int] = set()
    for block in blocks:
        if not isinstance(block, (tuple, list)) or len(block) < 5:
            continue
        try:
            y0 = float(block[1])
            y1 = float(block[3])
        except (TypeError, ValueError):
            continue
        if y1 > height * 0.14 and y0 < height * 0.86:
            continue
        value = str(block[4] or "").strip()
        match = re.fullmatch(
            r"[—\-·\s]*(?:第\s*)?([1-9]\d{0,3})(?:\s*页)?[—\-·\s]*",
            value,
        )
        if match:
            candidates.add(int(match.group(1)))
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:160]
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
