from __future__ import annotations

import io
import re
import zipfile
from collections import OrderedDict
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from PIL import Image

from backend.ops.archive import (
    OpsArchiveInvalid,
    OpsArchivePolicy,
    OpsArchiveTooLarge,
    inspect_zip,
)
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.infrastructure.materials.pptx_preview import (
    PREVIEW_COMPOSITOR_VERSION,
    STRUCTURAL_PREVIEW_NOTICE,
    render_pptx_slide_preview,
)


PPT_OBJECT_SCHEMA_VERSION = 4


_FORMULA_HINT = re.compile(
    r"[=±×÷√∑∫∞≈≠≤≥]|\\(?:frac|sqrt|sum|int)\b"
)
_DIRECTORY_MARKER = re.compile(r"(?:听|作|活)\s*\d{1,4}")
_INLINE_DIRECTORY_PAGE = re.compile(
    r"(?:[/／]|[.·…⋯_]{2,}|\s)\s*\d{1,4}\s*$"
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
_PPTX_ARCHIVE_POLICY = OpsArchivePolicy(
    max_upload_bytes=256 * 1024 * 1024,
    max_members=5_000,
    max_expanded_bytes=512 * 1024 * 1024,
    max_member_bytes=128 * 1024 * 1024,
    max_compression_ratio=100.0,
)
_PPTX_ALLOWED_ROOTS = {
    "[Content_Types].xml",
    "_rels",
    "_xmlsignatures",
    "customXml",
    "docMetadata",
    "docProps",
    "metadata",
    "ppt",
}
_PPTX_INSPECTION_CACHE_SIZE = 32


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
    layout_items: tuple[dict[str, object], ...] = ()


class MaterialParser:
    def __init__(
        self,
        *,
        ocr_engine_factory: Callable[[], object] | None = None,
        pptx_archive_policy: OpsArchivePolicy | None = None,
    ) -> None:
        self._ocr_engine_factory = (
            ocr_engine_factory or _default_ocr_engine
        )
        self._pptx_archive_policy = (
            pptx_archive_policy or _PPTX_ARCHIVE_POLICY
        )
        self._pptx_inspection_cache: OrderedDict[
            tuple[str, int, int], tuple[str, ...]
        ] = OrderedDict()

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
            slide_names = self._inspect_pptx(path)
            try:
                count = len(slide_names)
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
        if material_type == "pptx":
            yield from self._iter_pptx_preview_units(path, requested=requested)
            return
        units = self.parse(path, material_type=material_type)
        for unit in units:
            if requested is None or unit.unit_index in requested:
                yield unit

    def create_ocr_engine(self) -> object:
        return self._ocr_engine_factory()

    @staticmethod
    def ocr_pdf_page(
        path: Path,
        *,
        unit_index: int,
        ocr_engine: object,
        render_scale: float = 4.0,
    ) -> ParsedMaterialText:
        import fitz

        if unit_index <= 0:
            raise TeachingPrepValidationError("PDF page index is invalid")
        try:
            with fitz.open(path) as document:
                if unit_index > document.page_count:
                    raise TeachingPrepValidationError(
                        "PDF page index is out of range"
                    )
                page = document[unit_index - 1]
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(render_scale, render_scale),
                    alpha=False,
                )
                preview_png = pixmap.tobytes("png")
        except TeachingPrepValidationError:
            raise
        except Exception as exc:
            raise TeachingPrepValidationError(
                "PDF directory page could not be rendered"
            ) from exc
        parsed = MaterialParser.ocr_preview(preview_png, ocr_engine)
        if (
            parsed.layout_items
            and pixmap.width > pixmap.height * 1.1
            and _DIRECTORY_MARKER.search(parsed.extracted_text)
        ):
            track_refs = _ocr_spread_track_columns(
                preview_png,
                ocr_engine,
            )
            if track_refs:
                parsed = ParsedMaterialText(
                    extracted_text=parsed.extracted_text,
                    formula_review_required=parsed.formula_review_required,
                    printed_page_number=parsed.printed_page_number,
                    layout_items=_merge_spread_track_refs(
                        parsed.layout_items,
                        track_refs,
                    ),
                )
        if (
            parsed.layout_items
            and pixmap.height > pixmap.width
            and not _DIRECTORY_MARKER.search(parsed.extracted_text)
        ):
            right_page_numbers = _ocr_right_page_number_column(
                preview_png,
                ocr_engine,
            )
            if right_page_numbers:
                parsed = ParsedMaterialText(
                    extracted_text=parsed.extracted_text,
                    formula_review_required=parsed.formula_review_required,
                    printed_page_number=parsed.printed_page_number,
                    layout_items=_merge_right_page_numbers(
                        parsed.layout_items,
                        right_page_numbers,
                    ),
                )
        return parsed

    @staticmethod
    def ocr_preview(
        preview_png: bytes,
        ocr_engine: object,
    ) -> ParsedMaterialText:
        text, printed_page_number, layout_items = _ocr_page(
            preview_png,
            ocr_engine,
        )
        return ParsedMaterialText(
            extracted_text=text,
            formula_review_required=bool(text and _FORMULA_HINT.search(text)),
            printed_page_number=printed_page_number,
            layout_items=layout_items,
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
                        text, ocr_page_number, layout_items = _ocr_page(
                            preview_png,
                            ocr_engine,
                        )
                        if text:
                            object_summary["text_source"] = "local_ocr"
                        if layout_items:
                            object_summary["ocr_layout"] = {
                                "version": 1,
                                "items": list(layout_items),
                            }
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

    def _parse_pptx(self, path: Path) -> tuple[ParsedMaterialUnit, ...]:
        return tuple(self._iter_pptx_preview_units(path, requested=None))

    def _iter_pptx_preview_units(
        self,
        path: Path,
        *,
        requested: set[int] | None,
    ) -> Iterator[ParsedMaterialUnit]:
        inspected_slide_names = set(self._inspect_pptx(path))
        try:
            with zipfile.ZipFile(path) as archive:
                slide_names = sorted(
                    (
                        (int(match.group(1)), name)
                        for name in archive.namelist()
                        if name in inspected_slide_names
                        and (match := _SLIDE_FILE.fullmatch(name))
                    ),
                    key=lambda item: item[0],
                )
                if requested is not None:
                    slide_names = [
                        item
                        for item in slide_names
                        if item[0] in requested
                    ]
                if not slide_names:
                    raise TeachingPrepValidationError(
                        "PPTX contains no readable slides"
                    )
                slide_width, slide_height = _pptx_slide_size(archive)
                for index, name in slide_names:
                    yield _pptx_slide_unit(
                        archive.read(name),
                        index=index,
                        slide_width=slide_width,
                        slide_height=slide_height,
                        archive=archive,
                        slide_name=name,
                    )
        except TeachingPrepValidationError:
            raise
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise TeachingPrepValidationError(
                "PPTX could not be opened"
            ) from exc

    def _inspect_pptx(self, path: Path) -> tuple[str, ...]:
        source = Path(path)
        try:
            stat = source.stat()
            cache_key = (
                str(source.resolve(strict=True)),
                int(stat.st_size),
                int(stat.st_mtime_ns),
            )
        except OSError as exc:
            raise TeachingPrepValidationError(
                "PPTX could not be opened"
            ) from exc
        cached = self._pptx_inspection_cache.get(cache_key)
        if cached is not None:
            self._pptx_inspection_cache.move_to_end(cache_key)
            return cached
        try:
            inspection = inspect_zip(
                source,
                policy=self._pptx_archive_policy,
                allowed_roots=_PPTX_ALLOWED_ROOTS,
            )
        except OpsArchiveTooLarge as exc:
            raise TeachingPrepValidationError(
                "PPTX exceeds the safe expansion budget"
            ) from exc
        except OpsArchiveInvalid as exc:
            raise TeachingPrepValidationError(
                "PPTX could not be opened"
            ) from exc
        slide_names = tuple(
            member.archive_name
            for member in inspection.members
            if not member.is_dir and _SLIDE_FILE.fullmatch(member.archive_name)
        )
        self._pptx_inspection_cache[cache_key] = slide_names
        self._pptx_inspection_cache.move_to_end(cache_key)
        while len(self._pptx_inspection_cache) > _PPTX_INSPECTION_CACHE_SIZE:
            self._pptx_inspection_cache.popitem(last=False)
        return slide_names

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
    archive: zipfile.ZipFile | None = None,
    slide_name: str = "",
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
    objects = _slide_objects(root, slide_width, slide_height)
    preview_png = render_pptx_slide_preview(
        xml_bytes,
        archive=archive,
        slide_name=slide_name,
        slide_width=slide_width,
        slide_height=slide_height,
    )
    title = _first_line(text) or f"Slide {index}"
    return ParsedMaterialUnit(
        unit_kind="ppt_slide",
        unit_index=index,
        title=title,
        extracted_text=text,
        text_status="embedded" if text else "empty",
        formula_review_required=bool(text and _FORMULA_HINT.search(text)),
        object_summary={
            "object_schema_version": PPT_OBJECT_SCHEMA_VERSION,
            "preview_kind": "structural",
            "preview_notice": STRUCTURAL_PREVIEW_NOTICE,
            "preview_render_status": "pending",
            "preview_compositor": PREVIEW_COMPOSITOR_VERSION,
            "object_count": sum(types.values()),
            "object_types": dict(sorted(types.items())),
            "objects": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "source_kind"
                }
                for item in objects
            ],
            "occupied_boxes": [
                {
                    "x": position["x"],
                    "y": position["y"],
                    "width": position["width"],
                    "height": position["height"],
                }
                for item in objects[:200]
                if isinstance((position := item.get("position")), dict)
            ],
        },
        preview_png=preview_png,
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


def _slide_objects(
    root: ElementTree.Element,
    slide_width: int,
    slide_height: int,
) -> list[dict[str, object]]:
    """Expose safe, stable PPT objects without leaking package internals."""
    result: list[dict[str, object]] = []
    slide_has_animation = any(
        _local_name(element.tag) == "timing" for element in root.iter()
    )
    animated_object_ids = {
        str(element.attrib.get("spid") or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "spTgt"
        and str(element.attrib.get("spid") or "").strip()
    }
    animation_targets_unknown = slide_has_animation and not animated_object_ids
    shape_tree = next(
        (
            item
            for item in root.iter()
            if _local_name(item.tag) == "spTree"
        ),
        None,
    )
    top_level_objects = (
        [
            item
            for item in list(shape_tree)
            if _local_name(item.tag) in _DRAWABLE_TAGS
        ]
        if shape_tree is not None
        else []
    )
    for ordinal, element in enumerate(top_level_objects, start=1):
        source_kind = _local_name(element.tag)
        offset = None
        extent = None
        object_id = None
        object_name = None
        texts: list[str] = []
        for child in element.iter():
            local = _local_name(child.tag)
            if local == "cNvPr" and object_id is None:
                object_id = str(child.attrib.get("id") or "").strip() or None
                object_name = str(child.attrib.get("name") or "").strip() or None
            elif local == "off" and offset is None:
                offset = child
            elif local == "ext" and extent is None:
                extent = child
            elif local == "t" and str(child.text or "").strip():
                texts.append(str(child.text or "").strip())
        if offset is None or extent is None:
            continue
        try:
            left = int(offset.attrib.get("x") or 0)
            top = int(offset.attrib.get("y") or 0)
            width = int(extent.attrib.get("cx") or 0)
            height = int(extent.attrib.get("cy") or 0)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        object_type = (
            "text_box"
            if source_kind == "sp" and texts
            else "shape"
            if source_kind in {"sp", "cxnSp"}
            else "static_image"
            if source_kind == "pic"
            else source_kind
        )
        stable_id = object_id or f"ordinal-{ordinal}"
        exact_locator = bool(object_name)
        has_animation = animation_targets_unknown or stable_id in animated_object_ids
        protected_descendants = Counter(
            _local_name(child.tag)
            for child in element.iter()
            if child is not element
            and _local_name(child.tag)
            in {
                "audio",
                "control",
                "graphicFrame",
                "grpSp",
                "oleObj",
                "pic",
                "video",
            }
        )
        result.append(
            {
                "source_kind": source_kind,
                "object_ref": f"shape:{stable_id}",
                "wps_object_id": object_name,
                "object_type": object_type,
                "text": "\n".join(texts)[:2_000],
                "position": {
                    "x": round(left / slide_width, 6),
                    "y": round(top / slide_height, 6),
                    "width": round(width / slide_width, 6),
                    "height": round(height / slide_height, 6),
                },
                "has_animation": has_animation,
                "protected_descendant_counts": dict(
                    sorted(protected_descendants.items())
                ),
                "safe_to_delete": (
                    exact_locator
                    and not animation_targets_unknown
                ),
            }
        )
    return result


def _default_ocr_engine() -> object:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr_page(
    preview_png: bytes,
    ocr_engine: object,
) -> tuple[str, int | None, tuple[dict[str, object], ...]]:
    try:
        import numpy as np

        with Image.open(io.BytesIO(preview_png)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            image = np.asarray(rgb)[:, :, ::-1]
        raw = ocr_engine(image)  # type: ignore[operator]
    except Exception:
        return "", None, ()
    result: Any = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(result, list):
        return "", None, ()
    text_lines: list[str] = []
    layout_items: list[dict[str, object]] = []
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
            layout_item = _normalised_ocr_layout_item(
                item[0],
                text,
                confidence,
                width=width,
                height=height,
            )
            if layout_item is not None:
                layout_items.append(layout_item)
        if page_number is None and confidence >= 0.55:
            page_number = _ocr_footer_page_number(
                item[0],
                text,
                width=width,
                height=height,
            )
    keep_layout = _looks_like_directory_layout(text_lines, layout_items)
    return (
        "\n".join(text_lines),
        page_number,
        tuple(layout_items) if keep_layout else (),
    )


def _normalised_ocr_layout_item(
    raw_box: object,
    text: str,
    confidence: float,
    *,
    width: int,
    height: int,
) -> dict[str, object] | None:
    if (
        width <= 0
        or height <= 0
        or not isinstance(raw_box, (list, tuple))
        or len(raw_box) < 4
    ):
        return None
    try:
        xs = [float(point[0]) for point in raw_box]
        ys = [float(point[1]) for point in raw_box]
    except (TypeError, ValueError, IndexError):
        return None

    def normalise(value: float, extent: int) -> float:
        return round(max(0.0, min(1.0, value / extent)), 6)

    return {
        "text": text[:240],
        "confidence": round(max(0.0, min(1.0, confidence)), 6),
        "x0": normalise(min(xs), width),
        "y0": normalise(min(ys), height),
        "x1": normalise(max(xs), width),
        "y1": normalise(max(ys), height),
    }


def _ocr_right_page_number_column(
    preview_png: bytes,
    ocr_engine: object,
) -> tuple[dict[str, object], ...]:
    import numpy as np

    try:
        with Image.open(io.BytesIO(preview_png)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            left = int(width * 0.90)
            crop = rgb.crop((left, 0, width, height))
            scale = 2
            crop = crop.resize((crop.width * scale, crop.height * scale))
            image = np.asarray(crop)[:, :, ::-1]
        raw = ocr_engine(image)  # type: ignore[operator]
    except Exception:
        return ()
    result: Any = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(result, list):
        return ()
    items: list[dict[str, object]] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        text = str(item[1] or "").strip()
        try:
            confidence = float(item[2])
            xs = [float(point[0]) for point in item[0]]
            ys = [float(point[1]) for point in item[0]]
        except (TypeError, ValueError, IndexError):
            continue
        if confidence < 0.55 or not re.fullmatch(r"\d{1,4}", text):
            continue
        items.append(
            {
                "text": text,
                "confidence": round(max(0.0, min(1.0, confidence)), 6),
                "x0": round((left + min(xs) / scale) / width, 6),
                "y0": round((min(ys) / scale) / height, 6),
                "x1": round((left + max(xs) / scale) / width, 6),
                "y1": round((max(ys) / scale) / height, 6),
            }
        )
    return tuple(items)


def _ocr_spread_track_columns(
    preview_png: bytes,
    ocr_engine: object,
) -> tuple[dict[str, object], ...]:
    import numpy as np

    items: list[dict[str, object]] = []
    try:
        with Image.open(io.BytesIO(preview_png)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            for left_ratio, right_ratio in ((0.35, 0.50), (0.85, 1.0)):
                left = int(width * left_ratio)
                right = int(width * right_ratio)
                crop = rgb.crop((left, 0, right, height))
                scale = 2
                crop = crop.resize((crop.width * scale, crop.height * scale))
                image = np.asarray(crop)[:, :, ::-1]
                raw = ocr_engine(image)  # type: ignore[operator]
                result: Any = raw[0] if isinstance(raw, tuple) else raw
                if not isinstance(result, list):
                    continue
                for item in result:
                    if not isinstance(item, (list, tuple)) or len(item) < 3:
                        continue
                    text = re.sub(r"\s+", "", str(item[1] or ""))
                    match = _PAGE_TRACK_TEXT.fullmatch(text)
                    if match is None:
                        continue
                    try:
                        confidence = float(item[2])
                        xs = [float(point[0]) for point in item[0]]
                        ys = [float(point[1]) for point in item[0]]
                    except (TypeError, ValueError, IndexError):
                        continue
                    if confidence < 0.55:
                        continue
                    items.append(
                        {
                            "text": (
                                f"{match.group('track')}"
                                f"{int(match.group('page'))}"
                            ),
                            "confidence": round(
                                max(0.0, min(1.0, confidence)),
                                6,
                            ),
                            "x0": round((left + min(xs) / scale) / width, 6),
                            "y0": round((min(ys) / scale) / height, 6),
                            "x1": round((left + max(xs) / scale) / width, 6),
                            "y1": round((max(ys) / scale) / height, 6),
                        }
                    )
    except Exception:
        return ()
    return tuple(items)


_PAGE_TRACK_TEXT = re.compile(
    r"(?P<track>听|作|活|评)(?P<page>\d{1,4})"
)


def _merge_spread_track_refs(
    layout_items: tuple[dict[str, object], ...],
    track_refs: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    merged = list(layout_items)
    for track_ref in track_refs:
        text = str(track_ref.get("text") or "")
        center_y = (
            float(track_ref.get("y0") or 0)
            + float(track_ref.get("y1") or 0)
        ) / 2
        duplicate = any(
            re.sub(r"\s+", "", str(item.get("text") or "")) == text
            and abs(
                (
                    float(item.get("y0") or 0)
                    + float(item.get("y1") or 0)
                )
                / 2
                - center_y
            )
            <= 0.012
            for item in merged
        )
        if not duplicate:
            merged.append(track_ref)
    return tuple(merged)


def _merge_right_page_numbers(
    layout_items: tuple[dict[str, object], ...],
    page_numbers: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    merged = list(layout_items)
    for page_number in page_numbers:
        center_y = (
            float(page_number.get("y0") or 0)
            + float(page_number.get("y1") or 0)
        ) / 2
        matching = [
            index
            for index, item in enumerate(merged)
            if float(item.get("x0") or 0) >= 0.72
            and re.fullmatch(
                r"\d{1,4}",
                str(item.get("text") or "").strip(),
            )
            and abs(
                (
                    float(item.get("y0") or 0)
                    + float(item.get("y1") or 0)
                )
                / 2
                - center_y
            )
            <= 0.012
        ]
        if matching:
            merged[matching[0]] = page_number
        else:
            merged.append(page_number)
    return tuple(merged)


def _looks_like_directory_layout(
    text_lines: list[str],
    layout_items: list[dict[str, object]],
) -> bool:
    joined = "\n".join(text_lines)
    if "目录" in joined or "CONTENTS" in joined.upper():
        return True
    if len(_DIRECTORY_MARKER.findall(joined)) >= 3:
        return True
    inline_pages = sum(
        bool(_INLINE_DIRECTORY_PAGE.search(line))
        and bool(re.search(r"[^\W\d_]", line))
        for line in text_lines
    )
    right_page_numbers = sum(
        bool(re.fullmatch(r"\d{1,4}", str(item.get("text") or "").strip()))
        and float(item.get("x0") or 0) >= 0.72
        for item in layout_items
    )
    return max(inline_pages, right_page_numbers) >= 3


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
