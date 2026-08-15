from __future__ import annotations

import io
import os
import posixpath
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont


PREVIEW_WIDTH = 1600
PREVIEW_HEIGHT = 900
PREVIEW_COMPOSITOR_VERSION = 1
STRUCTURAL_PREVIEW_NOTICE = "本机拼出的页，不是放映软件实拍"

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
_MAX_PREVIEW_IMAGE_PIXELS = 8_000_000
_MAX_MEDIA_BYTES = 32 * 1024 * 1024


def render_pptx_slide_preview(
    xml_bytes: bytes,
    *,
    archive: zipfile.ZipFile | None,
    slide_name: str,
    slide_width: int,
    slide_height: int,
) -> bytes:
    try:
        root = ElementTree.fromstring(xml_bytes)
        canvas = Image.new(
            "RGB",
            (PREVIEW_WIDTH, PREVIEW_HEIGHT),
            _slide_background_rgb(root),
        )
        draw = ImageDraw.Draw(canvas)
        rels = _slide_relationships(archive, slide_name)
        for kind, element, box in _iter_preview_shapes(
            root,
            slide_width=slide_width,
            slide_height=slide_height,
        ):
            if kind == "pic":
                _paste_picture(canvas, archive, rels, element, box)
                continue
            fill = _shape_fill_rgb(element)
            if fill is not None:
                draw.rectangle(box, fill=fill)
            text = _shape_text(element)
            if text:
                _draw_textbox(draw, text, box, fill)
            elif kind not in {"pic", "sp"}:
                draw.rectangle(box, outline=(180, 186, 192), width=1)
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return _fallback_boxes(xml_bytes, slide_width, slide_height)


def _fallback_boxes(
    xml_bytes: bytes,
    slide_width: int,
    slide_height: int,
) -> bytes:
    root = ElementTree.fromstring(xml_bytes)
    preview = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), "white")
    draw = ImageDraw.Draw(preview)
    draw.rectangle(
        (0, 0, PREVIEW_WIDTH - 1, PREVIEW_HEIGHT - 1),
        outline=(198, 203, 208),
        width=2,
    )
    count = 0
    for kind, _element, box in _iter_preview_shapes(
        root,
        slide_width=slide_width,
        slide_height=slide_height,
    ):
        color = {
            "pic": (73, 101, 121),
            "graphicFrame": (94, 98, 141),
            "grpSp": (154, 103, 24),
        }.get(kind, (19, 94, 107))
        draw.rectangle(box, outline=color, width=2)
        count += 1
        if count >= 80:
            break
    output = io.BytesIO()
    preview.save(output, format="PNG")
    return output.getvalue()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slide_background_rgb(root: ElementTree.Element) -> tuple[int, int, int]:
    for element in root.iter():
        if _local_name(element.tag) != "bg":
            continue
        color = _first_solid_rgb(element)
        if color is not None:
            return color
        return (255, 255, 255)
    return (255, 255, 255)


def _shape_fill_rgb(element: ElementTree.Element) -> tuple[int, int, int] | None:
    for child in list(element):
        if _local_name(child.tag) != "spPr":
            continue
        if _has_no_fill(child):
            return None
        return _first_solid_rgb(child)
    return None


def _has_no_fill(element: ElementTree.Element) -> bool:
    return any(_local_name(child.tag) == "noFill" for child in element.iter())


def _first_solid_rgb(element: ElementTree.Element) -> tuple[int, int, int] | None:
    for child in element.iter():
        if _local_name(child.tag) != "srgbClr":
            continue
        raw = str(child.attrib.get("val") or "").strip()
        if len(raw) == 8:
            raw = raw[2:]
        if len(raw) != 6:
            continue
        try:
            return (
                int(raw[0:2], 16),
                int(raw[2:4], 16),
                int(raw[4:6], 16),
            )
        except ValueError:
            continue
    return None


def _shape_text(element: ElementTree.Element) -> str:
    parts = [
        str(child.text or "").strip()
        for child in element.iter()
        if _local_name(child.tag) == "t" and str(child.text or "").strip()
    ]
    return "\n".join(parts)[:2_000]


def _iter_preview_shapes(
    root: ElementTree.Element,
    *,
    slide_width: int,
    slide_height: int,
) -> list[tuple[str, ElementTree.Element, tuple[int, int, int, int]]]:
    shape_tree = next(
        (
            item
            for item in root.iter()
            if _local_name(item.tag) == "spTree"
        ),
        None,
    )
    if shape_tree is None:
        return []
    collected: list[tuple[str, ElementTree.Element, tuple[int, int, int, int]]] = []

    def walk(
        element: ElementTree.Element,
        mapper: _CoordMap,
    ) -> None:
        kind = _local_name(element.tag)
        if kind == "grpSp":
            nested = mapper.nested(_direct_xfrm(element))
            for child in list(element):
                if _local_name(child.tag) in _DRAWABLE_TAGS:
                    walk(child, nested)
            return
        if kind not in _DRAWABLE_TAGS:
            return
        box = mapper.box(
            _direct_xfrm(element),
            slide_width=slide_width,
            slide_height=slide_height,
        )
        if box is None:
            return
        collected.append((kind, element, box))

    for child in list(shape_tree):
        if _local_name(child.tag) in _DRAWABLE_TAGS:
            walk(child, _CoordMap.identity())
    return collected


class _CoordMap:
    def __init__(
        self,
        *,
        origin_x: float,
        origin_y: float,
        scale_x: float,
        scale_y: float,
    ) -> None:
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.scale_x = scale_x
        self.scale_y = scale_y

    @classmethod
    def identity(cls) -> "_CoordMap":
        return cls(origin_x=0.0, origin_y=0.0, scale_x=1.0, scale_y=1.0)

    def nested(self, xfrm: dict[str, int] | None) -> "_CoordMap":
        if xfrm is None:
            return self
        child_w = float(xfrm.get("ch_cx") or xfrm.get("cx") or 0)
        child_h = float(xfrm.get("ch_cy") or xfrm.get("cy") or 0)
        if child_w <= 0 or child_h <= 0:
            return self
        group_x = self.origin_x + float(xfrm["x"]) * self.scale_x
        group_y = self.origin_y + float(xfrm["y"]) * self.scale_y
        group_w = float(xfrm["cx"]) * self.scale_x
        group_h = float(xfrm["cy"]) * self.scale_y
        return _CoordMap(
            origin_x=group_x - float(xfrm.get("ch_x") or 0) * (group_w / child_w),
            origin_y=group_y - float(xfrm.get("ch_y") or 0) * (group_h / child_h),
            scale_x=self.scale_x * (group_w / child_w),
            scale_y=self.scale_y * (group_h / child_h),
        )

    def box(
        self,
        xfrm: dict[str, int] | None,
        *,
        slide_width: int,
        slide_height: int,
    ) -> tuple[int, int, int, int] | None:
        if xfrm is None or slide_width <= 0 or slide_height <= 0:
            return None
        left = self.origin_x + float(xfrm["x"]) * self.scale_x
        top = self.origin_y + float(xfrm["y"]) * self.scale_y
        width = float(xfrm["cx"]) * self.scale_x
        height = float(xfrm["cy"]) * self.scale_y
        if width <= 0 or height <= 0:
            return None
        x0 = max(0, min(PREVIEW_WIDTH - 1, round(left / slide_width * PREVIEW_WIDTH)))
        y0 = max(0, min(PREVIEW_HEIGHT - 1, round(top / slide_height * PREVIEW_HEIGHT)))
        x1 = max(
            x0 + 1,
            min(
                PREVIEW_WIDTH - 1,
                round((left + width) / slide_width * PREVIEW_WIDTH),
            ),
        )
        y1 = max(
            y0 + 1,
            min(
                PREVIEW_HEIGHT - 1,
                round((top + height) / slide_height * PREVIEW_HEIGHT),
            ),
        )
        return (x0, y0, x1, y1)


def _direct_xfrm(element: ElementTree.Element) -> dict[str, int] | None:
    for child in list(element):
        local = _local_name(child.tag)
        if local in {"spPr", "grpSpPr"}:
            for grand in list(child):
                if _local_name(grand.tag) == "xfrm":
                    return _parse_xfrm(grand)
        if local == "xfrm":
            return _parse_xfrm(child)
    return None


def _parse_xfrm(xfrm: ElementTree.Element) -> dict[str, int] | None:
    values: dict[str, int] = {
        "x": 0,
        "y": 0,
        "cx": 0,
        "cy": 0,
        "ch_x": 0,
        "ch_y": 0,
        "ch_cx": 0,
        "ch_cy": 0,
    }
    for child in list(xfrm):
        local = _local_name(child.tag)
        try:
            if local == "off":
                values["x"] = int(child.attrib.get("x") or 0)
                values["y"] = int(child.attrib.get("y") or 0)
            elif local == "ext":
                values["cx"] = int(child.attrib.get("cx") or 0)
                values["cy"] = int(child.attrib.get("cy") or 0)
            elif local == "chOff":
                values["ch_x"] = int(child.attrib.get("x") or 0)
                values["ch_y"] = int(child.attrib.get("y") or 0)
            elif local == "chExt":
                values["ch_cx"] = int(child.attrib.get("cx") or 0)
                values["ch_cy"] = int(child.attrib.get("cy") or 0)
        except ValueError:
            return None
    if values["cx"] <= 0 or values["cy"] <= 0:
        return None
    if values["ch_cx"] <= 0:
        values["ch_cx"] = values["cx"]
    if values["ch_cy"] <= 0:
        values["ch_cy"] = values["cy"]
    return values


def _slide_relationships(
    archive: zipfile.ZipFile | None,
    slide_name: str,
) -> dict[str, str]:
    if archive is None or not slide_name:
        return {}
    rels_name = posixpath.join(
        posixpath.dirname(slide_name),
        "_rels",
        posixpath.basename(slide_name) + ".rels",
    )
    try:
        root = ElementTree.fromstring(archive.read(rels_name))
    except (KeyError, ElementTree.ParseError):
        return {}
    mapping: dict[str, str] = {}
    for child in root:
        if _local_name(child.tag) != "Relationship":
            continue
        if str(child.attrib.get("TargetMode") or "").casefold() == "external":
            continue
        rel_id = str(child.attrib.get("Id") or "").strip()
        target = str(child.attrib.get("Target") or "").strip().replace("\\", "/")
        member = _safe_media_member(slide_name, target)
        if rel_id and member:
            mapping[rel_id] = member
    return mapping


def _safe_media_member(slide_name: str, target: str) -> str | None:
    if not target or ":" in target or target.startswith("/"):
        return None
    combined = posixpath.normpath(
        posixpath.join(posixpath.dirname(slide_name), target)
    )
    if combined.startswith("../") or combined == "..":
        return None
    if not combined.startswith("ppt/media/"):
        return None
    return combined


def _paste_picture(
    canvas: Image.Image,
    archive: zipfile.ZipFile | None,
    rels: dict[str, str],
    element: ElementTree.Element,
    box: tuple[int, int, int, int],
) -> None:
    if archive is None:
        return
    embed_id = _blip_embed_id(element)
    member = rels.get(embed_id or "")
    if not member:
        return
    picture = _open_archive_image(archive, member)
    if picture is None:
        return
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    fitted = picture.resize((width, height), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x0, y0))


def _blip_embed_id(element: ElementTree.Element) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) != "blip":
            continue
        for attr, value in child.attrib.items():
            if _local_name(attr) == "embed" and str(value).strip():
                return str(value).strip()
    return None


def _open_archive_image(
    archive: zipfile.ZipFile,
    member: str,
) -> Image.Image | None:
    try:
        info = archive.getinfo(member)
    except KeyError:
        return None
    if info.file_size <= 0 or info.file_size > _MAX_MEDIA_BYTES:
        return None
    try:
        payload = archive.read(member)
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            if source.width * source.height > _MAX_PREVIEW_IMAGE_PIXELS:
                return None
            return source.convert("RGB")
    except (OSError, ValueError, zipfile.BadZipFile):
        return None


def _draw_textbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int] | None,
) -> None:
    x0, y0, x1, y1 = box
    inner_w = max(8, x1 - x0 - 12)
    inner_h = max(8, y1 - y0 - 8)
    fill_luma = (
        255
        if fill is None
        else (fill[0] * 299 + fill[1] * 587 + fill[2] * 114) / 1000
    )
    color = (255, 255, 255) if fill_luma < 140 else (28, 39, 51)
    size = max(12, min(42, inner_h // 3 or 12))
    while size >= 12:
        font = _preview_font(size)
        lines = _wrap_text(draw, text, font, inner_w)
        line_h = size + 4
        if len(lines) * line_h <= inner_h or size == 12:
            top = y0 + 4
            for index, line in enumerate(lines):
                if top + line_h > y1:
                    break
                draw.text((x0 + 6, top), line, fill=color, font=font)
                top += line_h
                if index >= 24:
                    break
            return
        size -= 2


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r", "").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            if draw.textlength(trial, font=font) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        if len(lines) >= 30:
            break
    return lines or [text[:20]]


@lru_cache(maxsize=16)
def _preview_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    path = _cjk_font_path()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size=size, index=0)
        except OSError:
            pass
    return ImageFont.load_default()


@lru_cache(maxsize=1)
def _cjk_font_path() -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windir / "Fonts" / "msyh.ttc",
        windir / "Fonts" / "msyh.ttf",
        windir / "Fonts" / "simhei.ttf",
        windir / "Fonts" / "simsun.ttc",
        windir / "Fonts" / "simsun.ttf",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None
