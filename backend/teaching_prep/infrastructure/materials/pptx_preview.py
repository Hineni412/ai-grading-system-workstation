from __future__ import annotations

import gzip
import io
import math
import os
import posixpath
import threading
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont


PREVIEW_WIDTH = 1600
PREVIEW_HEIGHT = 900
PREVIEW_COMPOSITOR_VERSION = 2
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
_VERTICAL_VERTS = {
    "eaVert",
    "vert",
    "mongolianVert",
    "wordArtVert",
    "vert270",
    "wordArtVertRtl",
}
_MAX_PREVIEW_IMAGE_PIXELS = 8_000_000
_MAX_MEDIA_BYTES = 32 * 1024 * 1024
_SCHEME_RGB = {
    "dk1": (0, 0, 0),
    "lt1": (255, 255, 255),
    "dk2": (31, 56, 72),
    "lt2": (238, 236, 225),
    "accent1": (68, 114, 196),
    "accent2": (237, 125, 49),
    "accent3": (165, 165, 165),
    "accent4": (255, 192, 0),
    "accent5": (91, 155, 213),
    "accent6": (112, 173, 71),
    "tx1": (28, 39, 51),
    "tx2": (80, 80, 80),
    "bg1": (255, 255, 255),
    "bg2": (238, 236, 225),
}
_SUPERSCRIPT = str.maketrans("0123456789+-=n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=n", "₀₁₂₃₄₅₆₇₈₉₊₋₌ₙ")
_MATH_SKIP = {
    "rPr",
    "ctrlPr",
    "dPr",
    "naryPr",
    "fPr",
    "radPr",
    "sSupPr",
    "sSubPr",
    "sSubSupPr",
    "mPr",
    "eqArrPr",
    "funcPr",
    "accPr",
    "barPr",
    "groupChrPr",
    "limLowPr",
    "limUppPr",
    "borderBoxPr",
    "boxPr",
}
_GDI_LOCK = threading.Lock()
_GDIPLUS_TOKEN = None
_GDIPLUS_READY = False


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
        emu_scale = PREVIEW_WIDTH / float(slide_width) if slide_width else 0.0
        for kind, element, box in _iter_preview_shapes(
            root,
            slide_width=slide_width,
            slide_height=slide_height,
        ):
            pasted = _paste_embedded_image(canvas, archive, rels, element, box)
            if pasted and kind in {"pic", "graphicFrame", "oleObj"}:
                continue
            _draw_shape_geometry(draw, element, box, emu_scale=emu_scale)
            fill = _shape_fill_rgb(element)
            regular = _shape_plain_text(element)
            math_nodes = _math_layout_nodes(element)
            if regular and _is_vertical_text(element):
                _draw_textbox(draw, regular, box, fill, vertical=True)
            else:
                text_box = box
                math_box = box
                if regular and math_nodes:
                    x0, y0, x1, y1 = box
                    split = y0 + max(18, (y1 - y0) // 4)
                    text_box = (x0, y0, x1, split)
                    math_box = (x0, split, x1, y1)
                if regular:
                    _draw_textbox(draw, regular, text_box, fill, vertical=False)
                if math_nodes:
                    _draw_math_nodes(draw, math_nodes, math_box, fill)
            if not pasted and not regular and not math_nodes and kind not in {
                "pic",
                "sp",
                "cxnSp",
            }:
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
        for item in list(child):
            local = _local_name(item.tag)
            if local == "noFill":
                return None
            if local == "solidFill":
                color = _first_solid_rgb(item)
                if color is not None:
                    return color
        return _first_solid_rgb(child)
    return None


def _first_solid_rgb(element: ElementTree.Element) -> tuple[int, int, int] | None:
    for child in element.iter():
        local = _local_name(child.tag)
        if local == "srgbClr":
            parsed = _parse_hex_rgb(child.attrib.get("val"))
            if parsed is not None:
                return parsed
        elif local == "sysClr":
            parsed = _parse_hex_rgb(child.attrib.get("lastClr"))
            if parsed is not None:
                return parsed
        elif local == "schemeClr":
            key = str(child.attrib.get("val") or "").strip().casefold()
            if key in _SCHEME_RGB:
                return _SCHEME_RGB[key]
    return None


def _parse_hex_rgb(raw: object) -> tuple[int, int, int] | None:
    value = str(raw or "").strip()
    if len(value) == 8:
        value = value[2:]
    if len(value) != 6:
        return None
    try:
        return (
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )
    except ValueError:
        return None


def _shape_plain_text(element: ElementTree.Element) -> str:
    parts: list[str] = []
    for child in element.iter():
        if _local_name(child.tag) != "t":
            continue
        if _inside_math(child, element):
            continue
        text = str(child.text or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)[:2_000]


def _inside_math(node: ElementTree.Element, root: ElementTree.Element) -> bool:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    current: ElementTree.Element | None = node
    while current is not None and current is not root:
        if _local_name(current.tag) in {"oMath", "oMathPara"}:
            return True
        current = parent_map.get(current)
    return False


def _linearize_math(element: ElementTree.Element) -> str:
    collected: list[str] = []
    parent_map = {child: parent for parent in element.iter() for child in parent}
    for child in element.iter():
        if child is element or _local_name(child.tag) not in {"oMath", "oMathPara"}:
            continue
        parent = parent_map.get(child)
        nested = False
        while parent is not None and parent is not element:
            if _local_name(parent.tag) in {"oMath", "oMathPara"}:
                nested = True
                break
            parent = parent_map.get(parent)
        if nested:
            continue
        text = _linearize_math_node(child).strip()
        if text:
            collected.append(text)
    return "\n".join(collected)[:2_000]


def _linearize_math_node(node: ElementTree.Element) -> str:
    kind = _local_name(node.tag)
    if kind == "t":
        return str(node.text or "")
    children = list(node)
    if kind == "sSup":
        return (
            f"{_math_child(node, 'e')}"
            f"{_to_script(_math_child(node, 'sup'), _SUPERSCRIPT, '^')}"
        )
    if kind == "sSub":
        return (
            f"{_math_child(node, 'e')}"
            f"{_to_script(_math_child(node, 'sub'), _SUBSCRIPT, '_')}"
        )
    if kind == "sSubSup":
        return (
            f"{_math_child(node, 'e')}"
            f"{_to_script(_math_child(node, 'sub'), _SUBSCRIPT, '_')}"
            f"{_to_script(_math_child(node, 'sup'), _SUPERSCRIPT, '^')}"
        )
    if kind == "f":
        return f"({_math_child(node, 'num')})/({_math_child(node, 'den')})"
    if kind == "rad":
        deg = _math_child(node, "deg")
        body = _math_child(node, "e")
        return f"{deg}√({body})" if deg else f"√({body})"
    if kind == "d":
        return f"({_join_math_children(node, skip=_MATH_SKIP | {'dPr'})})"
    if kind == "nary":
        operator = _math_chr(node) or "∑"
        return (
            f"{operator}"
            f"{_to_script(_math_child(node, 'sub'), _SUBSCRIPT, '_')}"
            f"{_to_script(_math_child(node, 'sup'), _SUPERSCRIPT, '^')}"
            f"{_math_child(node, 'e')}"
        )
    if kind in {"oMath", "oMathPara", "e", "r", "num", "den", "sub", "sup", "deg", "fName"}:
        return _join_math_children(node)
    if not children:
        return str(node.text or "")
    return _join_math_children(node)


def _math_child(node: ElementTree.Element, name: str) -> str:
    for child in list(node):
        if _local_name(child.tag) == name:
            return _linearize_math_node(child)
    return ""


def _join_math_children(
    node: ElementTree.Element,
    *,
    skip: set[str] | None = None,
) -> str:
    ignored = skip or set()
    ignored = ignored | _MATH_SKIP
    return "".join(
        _linearize_math_node(child)
        for child in list(node)
        if _local_name(child.tag) not in ignored
    )


def _math_chr(node: ElementTree.Element) -> str:
    for child in node.iter():
        if _local_name(child.tag) != "chr":
            continue
        for attr, value in child.attrib.items():
            if _local_name(attr) == "val" and str(value).strip():
                return str(value).strip()
    return ""


def _to_script(text: str, table: dict[int, str], fallback: str) -> str:
    clean = text.strip()
    if not clean:
        return ""
    mapped: list[str] = []
    for char in clean:
        translated = char.translate(table)
        if translated == char:
            return f"{fallback}({clean})"
        mapped.append(translated)
    return "".join(mapped)


def _is_vertical_text(element: ElementTree.Element) -> bool:
    for child in element.iter():
        if _local_name(child.tag) != "bodyPr":
            continue
        vert = str(child.attrib.get("vert") or "").strip()
        return vert in _VERTICAL_VERTS
    return False


class _MathNode:
    __slots__ = ("kind", "text", "kids")

    def __init__(
        self,
        kind: str,
        text: str = "",
        kids: tuple["_MathNode", ...] = (),
    ) -> None:
        self.kind = kind
        self.text = text
        self.kids = kids


def _math_layout_nodes(element: ElementTree.Element) -> list[_MathNode]:
    nodes: list[_MathNode] = []
    parent_map = {child: parent for parent in element.iter() for child in parent}
    for child in element.iter():
        if child is element or _local_name(child.tag) not in {"oMath", "oMathPara"}:
            continue
        parent = parent_map.get(child)
        nested = False
        while parent is not None and parent is not element:
            if _local_name(parent.tag) in {"oMath", "oMathPara"}:
                nested = True
                break
            parent = parent_map.get(parent)
        if nested:
            continue
        parsed = _parse_math_xml(child)
        if parsed is None:
            fallback = _linearize_math_node(child).strip()
            parsed = _MathNode("text", fallback) if fallback else None
        if parsed is not None:
            nodes.append(parsed)
    return nodes


def _parse_math_xml(node: ElementTree.Element) -> _MathNode | None:
    kind = _local_name(node.tag)
    if kind in _MATH_SKIP:
        return None
    if kind == "t":
        text = str(node.text or "")
        return _MathNode("text", text) if text else None
    if kind == "r":
        texts = [
            str(child.text or "")
            for child in node.iter()
            if _local_name(child.tag) == "t" and child.text
        ]
        joined = "".join(texts)
        return _MathNode("text", joined) if joined else None
    if kind == "sSup":
        return _MathNode(
            "script",
            kids=(
                _math_xml_child(node, "e"),
                _MathNode("text", ""),
                _math_xml_child(node, "sup"),
            ),
        )
    if kind == "sSub":
        return _MathNode(
            "script",
            kids=(
                _math_xml_child(node, "e"),
                _math_xml_child(node, "sub"),
                _MathNode("text", ""),
            ),
        )
    if kind == "sSubSup":
        return _MathNode(
            "script",
            kids=(
                _math_xml_child(node, "e"),
                _math_xml_child(node, "sub"),
                _math_xml_child(node, "sup"),
            ),
        )
    if kind == "f":
        return _MathNode(
            "frac",
            kids=(_math_xml_child(node, "num"), _math_xml_child(node, "den")),
        )
    if kind == "rad":
        return _MathNode(
            "rad",
            kids=(_math_xml_child(node, "e"), _math_xml_child(node, "deg")),
        )
    if kind == "d":
        beg = _math_named_val(node, "begChr") or "("
        end = _math_named_val(node, "endChr") or ")"
        inner = _math_row(
            [
                parsed
                for child in list(node)
                if _local_name(child.tag) == "e"
                for parsed in [_parse_math_xml(child)]
                if parsed is not None
            ]
        )
        return _MathNode("delim", text=f"{beg}{end}", kids=(inner,))
    if kind == "nary":
        return _MathNode(
            "nary",
            text=_math_chr(node) or "∑",
            kids=(
                _math_xml_child(node, "e"),
                _math_xml_child(node, "sub"),
                _math_xml_child(node, "sup"),
            ),
        )
    if kind == "func":
        return _MathNode(
            "row",
            kids=(
                _math_xml_child(node, "fName"),
                _MathNode("delim", text="()", kids=(_math_xml_child(node, "e"),)),
            ),
        )
    if kind == "m":
        rows = []
        for child in list(node):
            if _local_name(child.tag) != "mr":
                continue
            cells = tuple(
                _parse_math_xml(item) or _MathNode("text", "")
                for item in child
                if _local_name(item.tag) == "e"
            )
            if cells:
                rows.append(_MathNode("row", kids=cells))
        return _MathNode("matrix", kids=tuple(rows)) if rows else None
    if kind == "eqArr":
        rows = tuple(
            _parse_math_xml(child) or _MathNode("text", "")
            for child in list(node)
            if _local_name(child.tag) == "e"
        )
        return _MathNode("stack", kids=rows) if rows else None
    kids = tuple(
        parsed
        for child in list(node)
        if _local_name(child.tag) not in _MATH_SKIP
        for parsed in [_parse_math_xml(child)]
        if parsed is not None
    )
    if not kids:
        text = str(node.text or "").strip()
        return _MathNode("text", text) if text else None
    if len(kids) == 1:
        return kids[0]
    return _MathNode("row", kids=kids)


def _math_xml_child(node: ElementTree.Element, name: str) -> _MathNode:
    for child in list(node):
        if _local_name(child.tag) == name:
            return _parse_math_xml(child) or _MathNode("text", "")
    return _MathNode("text", "")


def _math_named_val(node: ElementTree.Element, name: str) -> str:
    for child in node.iter():
        if _local_name(child.tag) != name:
            continue
        for attr, value in child.attrib.items():
            if _local_name(attr) == "val" and str(value).strip():
                return str(value).strip()
    return ""


def _math_row(nodes: list[_MathNode]) -> _MathNode:
    if not nodes:
        return _MathNode("text", "")
    if len(nodes) == 1:
        return nodes[0]
    return _MathNode("row", kids=tuple(nodes))


def _draw_math_nodes(
    draw: ImageDraw.ImageDraw,
    nodes: list[_MathNode],
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int] | None,
) -> None:
    color = _ink_color(fill)
    x0, y0, x1, y1 = box
    inner_w = max(8, x1 - x0 - 10)
    inner_h = max(8, y1 - y0 - 8)
    root = nodes[0] if len(nodes) == 1 else _MathNode("stack", kids=tuple(nodes))
    size = max(14, min(52, inner_h // 2 or 14, max(14, inner_w // 6)))
    fitted = size
    width = height = 0
    while fitted >= 12:
        width, height = _measure_math(draw, root, fitted)
        if (width <= inner_w and height <= inner_h) or fitted == 12:
            break
        fitted -= 2
    x = x0 + 5 + max(0, (inner_w - width) // 2)
    y = y0 + 4 + max(0, (inner_h - height) // 2)
    _paint_math(draw, root, x, y, fitted, color)


def _measure_math(
    draw: ImageDraw.ImageDraw,
    node: _MathNode,
    size: int,
) -> tuple[int, int]:
    kind = node.kind
    if kind == "text":
        if not node.text:
            return (0, max(1, size))
        font = _preview_font(size)
        box = draw.textbbox((0, 0), node.text, font=font)
        return (max(1, box[2] - box[0]), max(size, box[3] - box[1]))
    if kind == "row":
        width = 0
        height = size
        for child in node.kids:
            child_w, child_h = _measure_math(draw, child, size)
            width += child_w + 1
            height = max(height, child_h)
        return (max(0, width - 1), height)
    if kind == "stack":
        width = 0
        height = 0
        gap = max(2, size // 8)
        for index, child in enumerate(node.kids):
            child_w, child_h = _measure_math(draw, child, size)
            width = max(width, child_w)
            height += child_h + (gap if index else 0)
        return (width, height)
    if kind == "frac":
        num_w, num_h = _measure_math(draw, node.kids[0], max(10, int(size * 0.82)))
        den_w, den_h = _measure_math(draw, node.kids[1], max(10, int(size * 0.82)))
        gap = max(4, size // 6)
        return (max(num_w, den_w) + max(8, size // 3), num_h + den_h + gap)
    if kind == "script":
        base_w, base_h = _measure_math(draw, node.kids[0], size)
        small = max(9, int(size * 0.62))
        sub_w, sub_h = _measure_math(draw, node.kids[1], small)
        sup_w, sup_h = _measure_math(draw, node.kids[2], small)
        return (
            base_w + max(sub_w, sup_w),
            max(base_h, int(base_h * 0.55) + sub_h, int(base_h * 0.45) + sup_h),
        )
    if kind == "rad":
        body_w, body_h = _measure_math(draw, node.kids[0], size)
        deg_w, deg_h = _measure_math(draw, node.kids[1], max(8, int(size * 0.5)))
        extra = max(10, int(size * 0.7))
        return (deg_w + extra + body_w + 4, max(body_h + 8, deg_h + body_h // 2))
    if kind == "delim":
        inner_w, inner_h = _measure_math(draw, node.kids[0], size) if node.kids else (0, size)
        pad = max(8, int(size * 0.45))
        return (inner_w + pad * 2, max(inner_h, size + 4))
    if kind == "nary":
        op_w, op_h = _measure_math(draw, _MathNode("text", node.text or "∑"), size + 4)
        small = max(9, int(size * 0.55))
        body_w, body_h = _measure_math(draw, node.kids[0], size)
        sub_w, sub_h = _measure_math(draw, node.kids[1], small)
        sup_w, sup_h = _measure_math(draw, node.kids[2], small)
        op_block_w = max(op_w, sub_w, sup_w)
        op_block_h = op_h + sub_h + sup_h
        return (op_block_w + 4 + body_w, max(op_block_h, body_h))
    if kind == "matrix":
        small = max(10, int(size * 0.9))
        col_count = max((len(row.kids) for row in node.kids), default=1)
        col_w = [0] * col_count
        row_h = []
        for row in node.kids:
            heights = []
            for index, cell in enumerate(row.kids):
                cell_w, cell_h = _measure_math(draw, cell, small)
                col_w[index] = max(col_w[index], cell_w)
                heights.append(cell_h)
            row_h.append(max(heights) if heights else small)
        pad = max(10, int(size * 0.5))
        return (
            sum(col_w) + 6 * max(0, col_count - 1) + pad * 2,
            sum(row_h) + 4 * max(0, len(row_h) - 1),
        )
    return (size, size)


def _paint_math(
    draw: ImageDraw.ImageDraw,
    node: _MathNode,
    x: int,
    y: int,
    size: int,
    color: tuple[int, int, int],
) -> None:
    kind = node.kind
    width, height = _measure_math(draw, node, size)
    if kind == "text":
        if node.text:
            draw.text((x, y), node.text, fill=color, font=_preview_font(size))
        return
    if kind == "row":
        cursor = x
        for child in node.kids:
            child_w, child_h = _measure_math(draw, child, size)
            _paint_math(draw, child, cursor, y + max(0, (height - child_h) // 2), size, color)
            cursor += child_w + 1
        return
    if kind == "stack":
        cursor = y
        gap = max(2, size // 8)
        for index, child in enumerate(node.kids):
            child_w, child_h = _measure_math(draw, child, size)
            _paint_math(
                draw,
                child,
                x + max(0, (width - child_w) // 2),
                cursor,
                size,
                color,
            )
            cursor += child_h + (gap if index < len(node.kids) - 1 else 0)
        return
    if kind == "frac":
        small = max(10, int(size * 0.82))
        num_w, num_h = _measure_math(draw, node.kids[0], small)
        den_w, den_h = _measure_math(draw, node.kids[1], small)
        gap = max(4, size // 6)
        _paint_math(
            draw,
            node.kids[0],
            x + max(0, (width - num_w) // 2),
            y,
            small,
            color,
        )
        bar_y = y + num_h + gap // 2
        draw.line((x + 2, bar_y, x + width - 2, bar_y), fill=color, width=max(1, size // 12))
        _paint_math(
            draw,
            node.kids[1],
            x + max(0, (width - den_w) // 2),
            y + num_h + gap,
            small,
            color,
        )
        return
    if kind == "script":
        small = max(9, int(size * 0.62))
        base_w, base_h = _measure_math(draw, node.kids[0], size)
        _sub_w, sub_h = _measure_math(draw, node.kids[1], small)
        _sup_w, sup_h = _measure_math(draw, node.kids[2], small)
        base_y = y + max(0, height - base_h - max(0, sub_h - int(base_h * 0.45)))
        if node.kids[2].text or node.kids[2].kids:
            base_y = y + max(0, int(sup_h * 0.55))
        _paint_math(draw, node.kids[0], x, base_y, size, color)
        if node.kids[2].text or node.kids[2].kids:
            _paint_math(draw, node.kids[2], x + base_w, y, small, color)
        if node.kids[1].text or node.kids[1].kids:
            _paint_math(
                draw,
                node.kids[1],
                x + base_w,
                y + height - sub_h,
                small,
                color,
            )
        return
    if kind == "rad":
        body = node.kids[0]
        deg = node.kids[1]
        deg_w, deg_h = _measure_math(draw, deg, max(8, int(size * 0.5)))
        extra = max(10, int(size * 0.7))
        body_x = x + deg_w + extra
        body_w, body_h = _measure_math(draw, body, size)
        if deg.text or deg.kids:
            _paint_math(draw, deg, x, y + max(0, height // 6), max(8, int(size * 0.5)), color)
        tick = x + deg_w + 2
        draw.line(
            [
                (tick, y + int(height * 0.55)),
                (tick + extra // 3, y + height - 2),
                (tick + extra - 2, y + 2),
                (body_x + body_w + 2, y + 2),
            ],
            fill=color,
            width=max(1, size // 10),
        )
        _paint_math(draw, body, body_x, y + 6, size, color)
        return
    if kind == "delim":
        pad = max(8, int(size * 0.45))
        inner_w, inner_h = _measure_math(draw, node.kids[0], size) if node.kids else (0, size)
        beg = (node.text or "()")[:1] or "("
        end = (node.text or "()")[-1:] or ")"
        font = _preview_font(max(size, inner_h - 2))
        draw.text((x, y), beg, fill=color, font=font)
        _paint_math(
            draw,
            node.kids[0],
            x + pad,
            y + max(0, (height - inner_h) // 2),
            size,
            color,
        )
        draw.text((x + pad + inner_w + 2, y), end, fill=color, font=font)
        return
    if kind == "nary":
        small = max(9, int(size * 0.55))
        op_w, op_h = _measure_math(draw, _MathNode("text", node.text or "∑"), size + 4)
        sub_w, sub_h = _measure_math(draw, node.kids[1], small)
        sup_w, sup_h = _measure_math(draw, node.kids[2], small)
        body_w, body_h = _measure_math(draw, node.kids[0], size)
        op_block_w = max(op_w, sub_w, sup_w)
        _paint_math(
            draw,
            _MathNode("text", node.text or "∑"),
            x + max(0, (op_block_w - op_w) // 2),
            y + sup_h,
            size + 4,
            color,
        )
        if node.kids[2].text or node.kids[2].kids:
            _paint_math(
                draw,
                node.kids[2],
                x + max(0, (op_block_w - sup_w) // 2),
                y,
                small,
                color,
            )
        if node.kids[1].text or node.kids[1].kids:
            _paint_math(
                draw,
                node.kids[1],
                x + max(0, (op_block_w - sub_w) // 2),
                y + sup_h + op_h,
                small,
                color,
            )
        _paint_math(
            draw,
            node.kids[0],
            x + op_block_w + 4,
            y + max(0, (height - body_h) // 2),
            size,
            color,
        )
        return
    if kind == "matrix":
        small = max(10, int(size * 0.9))
        col_count = max((len(row.kids) for row in node.kids), default=1)
        col_w = [0] * col_count
        row_h: list[int] = []
        for row in node.kids:
            heights = []
            for index, cell in enumerate(row.kids):
                cell_w, cell_h = _measure_math(draw, cell, small)
                col_w[index] = max(col_w[index], cell_w)
                heights.append(cell_h)
            row_h.append(max(heights) if heights else small)
        pad = max(10, int(size * 0.5))
        font = _preview_font(max(size, height - 2))
        draw.text((x, y), "[", fill=color, font=font)
        cursor_y = y
        for row_index, row in enumerate(node.kids):
            cursor_x = x + pad
            for index, cell in enumerate(row.kids):
                cell_w, cell_h = _measure_math(draw, cell, small)
                _paint_math(
                    draw,
                    cell,
                    cursor_x + max(0, (col_w[index] - cell_w) // 2),
                    cursor_y + max(0, (row_h[row_index] - cell_h) // 2),
                    small,
                    color,
                )
                cursor_x += col_w[index] + 6
            cursor_y += row_h[row_index] + 4
        draw.text((x + width - pad + 2, y), "]", fill=color, font=font)


def _ink_color(fill: tuple[int, int, int] | None) -> tuple[int, int, int]:
    fill_luma = (
        255
        if fill is None
        else (fill[0] * 299 + fill[1] * 587 + fill[2] * 114) / 1000
    )
    return (255, 255, 255) if fill_luma < 140 else (28, 39, 51)


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
        "rot": 0,
        "flip_h": 0,
        "flip_v": 0,
    }
    try:
        values["rot"] = int(xfrm.attrib.get("rot") or 0)
        values["flip_h"] = 1 if str(xfrm.attrib.get("flipH") or "") in {"1", "true"} else 0
        values["flip_v"] = 1 if str(xfrm.attrib.get("flipV") or "") in {"1", "true"} else 0
    except ValueError:
        return None
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


def _paste_embedded_image(
    canvas: Image.Image,
    archive: zipfile.ZipFile | None,
    rels: dict[str, str],
    element: ElementTree.Element,
    box: tuple[int, int, int, int],
) -> bool:
    if archive is None:
        return False
    embed_id = _blip_embed_id(element)
    member = rels.get(embed_id or "")
    if not member:
        return False
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    picture = _open_archive_image(archive, member, width=width, height=height)
    if picture is None:
        return False
    fitted = picture.resize((width, height), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x0, y0))
    return True


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
    *,
    width: int,
    height: int,
) -> Image.Image | None:
    try:
        info = archive.getinfo(member)
    except KeyError:
        return None
    if info.file_size <= 0 or info.file_size > _MAX_MEDIA_BYTES:
        return None
    try:
        payload = archive.read(member)
    except (OSError, zipfile.BadZipFile):
        return None
    payload = _maybe_decompress_metafile(member, payload)
    suffix = posixpath.splitext(member)[1].casefold()
    if suffix in {".emf", ".wmf", ".emz", ".wmz"}:
        return _rasterize_metafile(payload, width=width, height=height)
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            if source.width * source.height > _MAX_PREVIEW_IMAGE_PIXELS:
                return None
            return source.convert("RGB")
    except (OSError, ValueError):
        return _rasterize_metafile(payload, width=width, height=height)


def _maybe_decompress_metafile(member: str, payload: bytes) -> bytes:
    suffix = posixpath.splitext(member)[1].casefold()
    if suffix not in {".emz", ".wmz"}:
        return payload
    try:
        return gzip.decompress(payload)
    except OSError:
        return payload


def _rasterize_metafile(
    payload: bytes,
    *,
    width: int,
    height: int,
) -> Image.Image | None:
    if os.name != "nt" or not payload:
        return None
    with _GDI_LOCK:
        image = None
        try:
            image = _gdiplus_rasterize(payload, width=width, height=height)
        except Exception:
            image = None
        if image is None:
            try:
                image = _emf_play_rasterize(payload, width=width, height=height)
            except Exception:
                image = None
        if image is None:
            return None
        return _crop_visible_ink(image)


def _looks_like_emf(payload: bytes) -> bool:
    if len(payload) < 44:
        return False
    header_type = int.from_bytes(payload[0:4], "little")
    signature = int.from_bytes(payload[40:44], "little")
    return header_type == 1 and signature == 0x464D4520


def _ensure_gdiplus(ctypes_mod: object) -> bool:
    global _GDIPLUS_READY, _GDIPLUS_TOKEN
    if _GDIPLUS_READY:
        return True
    ctypes = ctypes_mod

    class StartupInput(ctypes.Structure):
        _fields_ = [
            ("GdiplusVersion", ctypes.c_uint32),
            ("DebugEventCallback", ctypes.c_void_p),
            ("SuppressBackgroundThread", ctypes.c_int),
            ("SuppressExternalCodecs", ctypes.c_int),
        ]

    class StartupOutput(ctypes.Structure):
        _fields_ = [
            ("NotificationHook", ctypes.c_void_p),
            ("NotificationUnhook", ctypes.c_void_p),
        ]

    gdiplus = ctypes.WinDLL("gdiplus")
    token = ctypes.c_size_t()
    startup = StartupInput(1, None, 0, 0)
    output = StartupOutput()
    gdiplus.GdiplusStartup.restype = ctypes.c_int
    status = gdiplus.GdiplusStartup(
        ctypes.byref(token),
        ctypes.byref(startup),
        ctypes.byref(output),
    )
    if status != 0:
        return False
    _GDIPLUS_TOKEN = token
    _GDIPLUS_READY = True
    return True


def _com_release(ctypes_mod: object, pointer: object) -> None:
    ctypes = ctypes_mod
    value = getattr(pointer, "value", pointer)
    if not value:
        return
    vtbl_ptr = ctypes.cast(value, ctypes.POINTER(ctypes.c_void_p))[0]
    release_ptr = ctypes.cast(vtbl_ptr, ctypes.POINTER(ctypes.c_void_p))[2]
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(release_ptr)(value)


def _gdiplus_rasterize(
    payload: bytes,
    *,
    width: int,
    height: int,
) -> Image.Image | None:
    import ctypes
    from ctypes import wintypes

    width = max(1, min(width, 8_192))
    height = max(1, min(height, 8_192))
    if width * height > _MAX_PREVIEW_IMAGE_PIXELS:
        return None
    if not _ensure_gdiplus(ctypes):
        return None
    ole32 = ctypes.WinDLL("ole32")
    gdiplus = ctypes.WinDLL("gdiplus")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    ole32.CreateStreamOnHGlobal.restype = ctypes.HRESULT
    ole32.CreateStreamOnHGlobal.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    gdiplus.GdipLoadImageFromStream.restype = ctypes.c_int
    gdiplus.GdipLoadImageFromStream.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    gdiplus.GdipCreateBitmapFromScan0.restype = ctypes.c_int
    gdiplus.GdipCreateBitmapFromScan0.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    gdiplus.GdipGetImageGraphicsContext.restype = ctypes.c_int
    gdiplus.GdipGetImageGraphicsContext.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    gdiplus.GdipGraphicsClear.restype = ctypes.c_int
    gdiplus.GdipGraphicsClear.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    gdiplus.GdipGetImageWidth.restype = ctypes.c_int
    gdiplus.GdipGetImageWidth.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    gdiplus.GdipGetImageHeight.restype = ctypes.c_int
    gdiplus.GdipGetImageHeight.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    gdiplus.GdipSetPageUnit.restype = ctypes.c_int
    gdiplus.GdipSetPageUnit.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdiplus.GdipDrawImageRectRectI.restype = ctypes.c_int
    gdiplus.GdipDrawImageRectRectI.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    gdiplus.GdipBitmapLockBits.restype = ctypes.c_int
    gdiplus.GdipBitmapLockBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    gdiplus.GdipBitmapUnlockBits.restype = ctypes.c_int
    gdiplus.GdipBitmapUnlockBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdiplus.GdipDeleteGraphics.restype = ctypes.c_int
    gdiplus.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
    gdiplus.GdipDisposeImage.restype = ctypes.c_int
    gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]

    class BitmapData(ctypes.Structure):
        _fields_ = [
            ("Width", ctypes.c_uint),
            ("Height", ctypes.c_uint),
            ("Stride", ctypes.c_int),
            ("PixelFormat", ctypes.c_int),
            ("Scan0", ctypes.c_void_p),
            ("Reserved", ctypes.c_void_p),
        ]

    class GpRect(ctypes.Structure):
        _fields_ = [
            ("X", ctypes.c_int),
            ("Y", ctypes.c_int),
            ("Width", ctypes.c_int),
            ("Height", ctypes.c_int),
        ]

    image = ctypes.c_void_p()
    bitmap = ctypes.c_void_p()
    graphics = ctypes.c_void_p()
    stream = ctypes.c_void_p()
    hglob = None
    locked_bits = False
    data = BitmapData()
    try:
        hglob = kernel32.GlobalAlloc(0x0002, len(payload))
        if not hglob:
            return None
        locked = kernel32.GlobalLock(hglob)
        if not locked:
            return None
        ctypes.memmove(locked, payload, len(payload))
        kernel32.GlobalUnlock(hglob)
        if ole32.CreateStreamOnHGlobal(hglob, True, ctypes.byref(stream)) != 0:
            return None
        hglob = None
        if gdiplus.GdipLoadImageFromStream(stream, ctypes.byref(image)) != 0:
            return None
        pixel_format = 2498570
        if (
            gdiplus.GdipCreateBitmapFromScan0(
                width,
                height,
                0,
                pixel_format,
                None,
                ctypes.byref(bitmap),
            )
            != 0
        ):
            return None
        if gdiplus.GdipGetImageGraphicsContext(bitmap, ctypes.byref(graphics)) != 0:
            return None
        gdiplus.GdipSetPageUnit(graphics, 2)
        gdiplus.GdipGraphicsClear(graphics, 0xFFFFFFFF)
        gdiplus.GdipSetInterpolationMode(graphics, 7)
        source_w = ctypes.c_uint()
        source_h = ctypes.c_uint()
        if (
            gdiplus.GdipGetImageWidth(image, ctypes.byref(source_w)) != 0
            or gdiplus.GdipGetImageHeight(image, ctypes.byref(source_h)) != 0
            or source_w.value == 0
            or source_h.value == 0
        ):
            return None
        if (
            gdiplus.GdipDrawImageRectRectI(
                graphics,
                image,
                0,
                0,
                width,
                height,
                0,
                0,
                int(source_w.value),
                int(source_h.value),
                2,
                None,
                None,
            )
            != 0
        ):
            return None
        lock_rect = GpRect(0, 0, width, height)
        if (
            gdiplus.GdipBitmapLockBits(
                bitmap,
                ctypes.byref(lock_rect),
                1,
                pixel_format,
                ctypes.byref(data),
            )
            != 0
        ):
            return None
        locked_bits = True
        stride = abs(int(data.Stride))
        raw = ctypes.string_at(data.Scan0, stride * height)
        gdiplus.GdipBitmapUnlockBits(bitmap, ctypes.byref(data))
        locked_bits = False
        return Image.frombytes(
            "RGBA",
            (width, height),
            raw,
            "raw",
            "BGRA",
            stride,
        ).convert("RGB")
    finally:
        if locked_bits and bitmap:
            gdiplus.GdipBitmapUnlockBits(bitmap, ctypes.byref(data))
        if graphics:
            gdiplus.GdipDeleteGraphics(graphics)
        if bitmap:
            gdiplus.GdipDisposeImage(bitmap)
        if image:
            gdiplus.GdipDisposeImage(image)
        if stream:
            _com_release(ctypes, stream)
        if hglob:
            kernel32.GlobalFree(hglob)


def _emf_play_rasterize(
    payload: bytes,
    *,
    width: int,
    height: int,
) -> Image.Image | None:
    if not _looks_like_emf(payload):
        return None
    import ctypes
    from ctypes import wintypes

    width = max(1, min(width, 8_192))
    height = max(1, min(height, 8_192))
    handle = ctypes.c_void_p
    gdi32 = ctypes.WinDLL("gdi32")
    user32 = ctypes.WinDLL("user32")
    user32.GetDC.restype = handle
    user32.GetDC.argtypes = [handle]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = [handle, handle]
    gdi32.SetEnhMetaFileBits.restype = handle
    gdi32.SetEnhMetaFileBits.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    gdi32.PlayEnhMetaFile.restype = wintypes.BOOL
    gdi32.PlayEnhMetaFile.argtypes = [handle, handle, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.PatBlt.restype = wintypes.BOOL
    gdi32.PatBlt.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        handle,
        handle,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    gdi32.DeleteEnhMetaFile.argtypes = [handle]
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteDC.argtypes = [handle]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER)]

    gdi32.SetEnhMetaFileBits.restype = ctypes.c_void_p
    gdi32.SetEnhMetaFileBits.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    gdi32.PlayEnhMetaFile.restype = wintypes.BOOL
    bits = (ctypes.c_char * len(payload)).from_buffer_copy(payload)
    hemf = gdi32.SetEnhMetaFileBits(len(payload), bits)
    if not hemf:
        return None
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        gdi32.PatBlt(hdc_mem, 0, 0, width, height, 0x00FF0062)
        dest = RECT(0, 0, width, height)
        if not gdi32.PlayEnhMetaFile(hdc_mem, hemf, ctypes.byref(dest)):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(
            hdc_mem,
            hbmp,
            0,
            height,
            buffer,
            ctypes.byref(info),
            0,
        )
        if copied == 0:
            return None
        return Image.frombytes(
            "RGB",
            (width, height),
            buffer,
            "raw",
            "BGRX",
        )
    finally:
        gdi32.DeleteEnhMetaFile(hemf)
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def _crop_visible_ink(image: Image.Image) -> Image.Image:
    mask = image.convert("L").point(lambda luma: 0 if luma > 248 else 255)
    bounds = mask.getbbox()
    if bounds is None:
        return image
    pad = 2
    cropped = image.crop(
        (
            max(0, bounds[0] - pad),
            max(0, bounds[1] - pad),
            min(image.width, bounds[2] + pad),
            min(image.height, bounds[3] + pad),
        )
    )
    if cropped.width < 2 or cropped.height < 2:
        return image
    return cropped


def _draw_shape_geometry(
    draw: ImageDraw.ImageDraw,
    element: ElementTree.Element,
    box: tuple[int, int, int, int],
    *,
    emu_scale: float,
) -> None:
    fill = _shape_fill_rgb(element)
    stroke = _shape_stroke(element, emu_scale=emu_scale)
    preset = _preset_geometry(element)
    path_sets = _custom_geometry_points(element, box)
    x0, y0, x1, y1 = box
    if path_sets:
        for points in path_sets:
            if len(points) < 2:
                continue
            if fill is not None and points[0] == points[-1] and len(points) >= 4:
                draw.polygon(points[:-1], fill=fill, outline=None)
            if stroke is not None:
                draw.line(points, fill=stroke[0], width=stroke[1])
        return
    if preset in {"line", "straightConnector1"} or _local_name(element.tag) == "cxnSp":
        start, end = _line_ends(element, box)
        if stroke is None:
            stroke = ((28, 39, 51), 2)
        draw.line([start, end], fill=stroke[0], width=stroke[1])
        return
    if preset in {"ellipse", "oval"}:
        if fill is not None:
            draw.ellipse(box, fill=fill, outline=None)
        if stroke is not None:
            draw.ellipse(box, outline=stroke[0], width=stroke[1])
        return
    if preset in {"triangle", "rtTriangle"}:
        if preset == "rtTriangle":
            points = [(x0, y1), (x0, y0), (x1, y1)]
        else:
            points = [((x0 + x1) // 2, y0), (x0, y1), (x1, y1)]
        if fill is not None:
            draw.polygon(points, fill=fill)
        if stroke is not None:
            draw.polygon(points, outline=stroke[0])
        return
    if fill is not None:
        draw.rectangle(box, fill=fill)
    if stroke is not None:
        draw.rectangle(box, outline=stroke[0], width=stroke[1])


def _preset_geometry(element: ElementTree.Element) -> str:
    for child in element.iter():
        if _local_name(child.tag) != "prstGeom":
            continue
        return str(child.attrib.get("prst") or "").strip()
    return ""


def _shape_stroke(
    element: ElementTree.Element,
    *,
    emu_scale: float,
) -> tuple[tuple[int, int, int], int] | None:
    for child in element.iter():
        if _local_name(child.tag) != "ln":
            continue
        if any(_local_name(item.tag) == "noFill" for item in child):
            return None
        color = _first_solid_rgb(child) or (28, 39, 51)
        try:
            width_emu = int(child.attrib.get("w") or 12700)
        except ValueError:
            width_emu = 12700
        width = max(1, min(12, round(width_emu * emu_scale))) if emu_scale else 2
        return (color, width)
    return None


def _line_ends(
    element: ElementTree.Element,
    box: tuple[int, int, int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    xfrm = _direct_xfrm(element) or {}
    x0, y0, x1, y1 = box
    start = (
        x1 if xfrm.get("flip_h") else x0,
        y1 if xfrm.get("flip_v") else y0,
    )
    end = (
        x0 if xfrm.get("flip_h") else x1,
        y0 if xfrm.get("flip_v") else y1,
    )
    rot = int(xfrm.get("rot") or 0)
    if rot:
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        start = _rotate_point(start[0], start[1], cx, cy, rot)
        end = _rotate_point(end[0], end[1], cx, cy, rot)
    return start, end


def _rotate_point(
    x: float,
    y: float,
    cx: float,
    cy: float,
    rot_emu: int,
) -> tuple[int, int]:
    theta = math.radians(rot_emu / 60_000.0)
    dx = x - cx
    dy = y - cy
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return (
        int(round(cx + dx * cos_t - dy * sin_t)),
        int(round(cy + dx * sin_t + dy * cos_t)),
    )


def _custom_geometry_points(
    element: ElementTree.Element,
    box: tuple[int, int, int, int],
) -> list[list[tuple[int, int]]]:
    x0, y0, x1, y1 = box
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    paths: list[list[tuple[int, int]]] = []
    for path in element.iter():
        if _local_name(path.tag) != "path":
            continue
        try:
            path_w = int(path.attrib.get("w") or 0)
            path_h = int(path.attrib.get("h") or 0)
        except ValueError:
            continue
        if path_w <= 0 or path_h <= 0:
            continue
        points: list[tuple[int, int]] = []
        current: tuple[int, int] | None = None

        def map_pt(pt: ElementTree.Element) -> tuple[int, int]:
            try:
                px = int(pt.attrib.get("x") or 0)
                py = int(pt.attrib.get("y") or 0)
            except ValueError:
                return (x0, y0)
            return (
                x0 + round(px / path_w * box_w),
                y0 + round(py / path_h * box_h),
            )

        for command in list(path):
            name = _local_name(command.tag)
            pts = [item for item in command.iter() if _local_name(item.tag) == "pt"]
            if name == "moveTo" and pts:
                current = map_pt(pts[0])
                if points:
                    paths.append(points)
                    points = []
                points.append(current)
            elif name == "lnTo" and pts:
                current = map_pt(pts[0])
                points.append(current)
            elif name == "cubicBezTo" and len(pts) >= 3 and current is not None:
                end = map_pt(pts[2])
                for index in range(1, 5):
                    t = index / 4
                    sampled = _cubic_point(
                        current,
                        map_pt(pts[0]),
                        map_pt(pts[1]),
                        end,
                        t,
                    )
                    points.append(sampled)
                current = end
            elif name == "close" and points:
                points.append(points[0])
        if len(points) >= 2:
            paths.append(points)
    return paths


def _cubic_point(
    start: tuple[int, int],
    control1: tuple[int, int],
    control2: tuple[int, int],
    end: tuple[int, int],
    t: float,
) -> tuple[int, int]:
    mt = 1 - t
    x = (
        mt ** 3 * start[0]
        + 3 * mt ** 2 * t * control1[0]
        + 3 * mt * t ** 2 * control2[0]
        + t ** 3 * end[0]
    )
    y = (
        mt ** 3 * start[1]
        + 3 * mt ** 2 * t * control1[1]
        + 3 * mt * t ** 2 * control2[1]
        + t ** 3 * end[1]
    )
    return (int(round(x)), int(round(y)))


def _draw_textbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int] | None,
    *,
    vertical: bool = False,
) -> None:
    x0, y0, x1, y1 = box
    inner_w = max(8, x1 - x0 - 12)
    inner_h = max(8, y1 - y0 - 8)
    color = _ink_color(fill)
    if vertical:
        _draw_vertical_text(draw, text, box, color)
        return
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


def _draw_vertical_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
) -> None:
    x0, y0, x1, y1 = box
    chars = [char for char in text.replace("\r", "") if char != "\n"]
    if not chars:
        return
    inner_w = max(8, x1 - x0 - 6)
    inner_h = max(8, y1 - y0 - 6)
    size = max(12, min(36, inner_w - 2, inner_h // max(1, len(chars))))
    font = _preview_font(size)
    line_h = size + 2
    col_w = size + 4
    columns = max(1, inner_w // col_w)
    rows = max(1, inner_h // line_h)
    x = x0 + 3
    index = 0
    for _column in range(columns):
        y = y0 + 3
        for _row in range(rows):
            if index >= len(chars):
                return
            draw.text((x, y), chars[index], fill=color, font=font)
            index += 1
            y += line_h
        x += col_w


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
