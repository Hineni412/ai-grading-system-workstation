"""Render a frozen personalized paper snapshot to LaTeX and compile it.

This module is the LaTeX counterpart of ``render_review_docx``: it consumes
the same frozen snapshot (rich Word blocks, frozen image assets, tagging
context) and produces the paper body PDF through a local tectonic engine.
The docx pipeline stays as the fallback and as the optional editable
download; this module never touches it.

Failure rule: any unrecognised content, missing asset or compile error
raises :class:`LatexRenderError`; the caller falls back to the DOCX path so
paper generation never breaks on LaTeX issues.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from question_bank.document_pipeline.word_renderer import answer_space_lines
from question_bank.personalized_papers.rendering import PaperRenderError
from question_bank.services.rich_content_service import strip_question_source_score_blocks

EMU_PER_MM = 36000
_IMAGE_BLOCK_MAX_WIDTH_MM = 90.0
_IMAGE_BLOCK_OBJECTIVE_MAX_WIDTH_MM = 55.0
_IMAGE_INLINE_MAX_HEIGHT_MM = 22.0
_IMAGE_OPTION_GRID_MAX_HEIGHT_MM = 16.0
_CONTENT_WIDTH_MM = 172.0
_IMAGE_MAX_EDGE_PX = 1000

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_OPTION_LETTER = re.compile(r"^\s*[A-DＡ-Ｄ][.、．]")
_LEADING_NUMBER = re.compile(r"^\s*\d+\s*[.、．]\s*")


class LatexRenderError(PaperRenderError):
    """The LaTeX path could not produce a paper body."""


def _escape_latex(text: str) -> str:
    return "".join(_LATEX_SPECIAL.get(char, char) for char in text)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _math_latex(node: ET.Element) -> str:
    """Translate supported Word equation structures, never flatten unknown ones."""
    name = _local_name(node.tag)

    def part(key: str) -> str:
        element = node.find(f"{{{_M}}}{key}")
        if element is None:
            raise LatexRenderError(f"Word math {name} is missing {key}")
        return _math_latex(element)

    def prop(key: str, default: str) -> str:
        element = node.find(f"{{{_M}}}{name}Pr/{{{_M}}}{key}")
        return default if element is None else str(element.get(f"{{{_M}}}val", default))

    properties = {
        "limUppPr": {"ctrlPr"}, "limLowPr": {"ctrlPr"},
        "radPr": {"degHide", "ctrlPr"}, "fPr": {"type", "ctrlPr"},
        "sSupPr": {"ctrlPr"}, "sSubPr": {"ctrlPr"}, "sSubSupPr": {"alnScr", "ctrlPr"},
        "sPrePr": {"ctrlPr"}, "dPr": {"begChr", "endChr", "sepChr", "grow", "shp", "ctrlPr"},
        "barPr": {"pos", "ctrlPr"}, "accPr": {"chr", "ctrlPr"}, "funcPr": {"ctrlPr"},
        "mPr": {"baseJc", "mcs", "plcHide", "rSp", "rSpRule", "cGp", "cGpRule", "cSp", "ctrlPr"},
        "eqArrPr": {"baseJc", "maxDist", "objDist", "rSp", "rSpRule", "ctrlPr"},
        "oMathParaPr": {"jc"}, "argPr": {"argSz"}, "ctrlPr": {"rPr"},
        "rPr": {"sty", "nor", "lit", "brk", "scr", "aln", "rFonts", "b", "bCs", "i", "iCs", "color", "sz", "szCs", "lang"},
    }
    if node.tag in {f"{{{_W}}}bookmarkStart", f"{{{_W}}}bookmarkEnd"}:
        return ""
    if name in properties:
        if any(_local_name(child.tag) not in properties[name] for child in node):
            raise LatexRenderError(f"unsupported Word math property in {name}")
        if name == "rPr" and any(
            _local_name(child.tag) == "scr" and child.get(f"{{{_M}}}val", "roman") != "roman"
            for child in node
        ):
            raise LatexRenderError("unsupported Word math alphabet")
        return ""
    for child in node:
        if _local_name(child.tag).endswith("Pr"):
            _math_latex(child)
    if name == "t":
        symbols = {
            "−": "-", "×": r"\times ", "÷": r"\div ", "±": r"\pm ",
            "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "≈": r"\approx ",
            "∞": r"\infty ", "∈": r"\in ", "∠": r"\angle ", "°": r"{}^{\circ}",
            "π": r"\pi ", "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ",
            "θ": r"\theta ", "Δ": r"\Delta ", "·": r"\cdot ", "…": r"\ldots ",
            "△": r"\triangle ", "∥": r"\parallel ", "⊥": r"\perp ", "∴": r"\therefore ", "∵": r"\because ",
            "′": "'", "＝": "=", "﹣": "-", "（": "(", "）": ")", "＞": ">", "＜": "<",
            "²": r"{}^{2}", "³": r"{}^{3}", "⋅": r"\cdot ",
            "⋯": r"\cdots ", "⊙": r"\odot ", "□": r"\square ",
            "★": r"\text{\fontspec{SimSun}★}",
            "，": r"\text{，}", "：": r"\text{：}", "．": r"\text{．}",
            "；": r"\text{；}", "？": r"\text{？}", "ㅤ": r"\quad ", " ": r"\;",
            **{char: r"\text{\fontspec{SimSun}" + char + "}" for char in "①②③④"},
            " ": r"\,", "{": r"\{", "}": r"\}", "_": r"\_", "^": r"\wedge ",
        }
        chunks = re.split(r"([\u3400-\u9fff]+)", node.text or "")
        if any(ord(char) > 127 and char not in symbols and not re.fullmatch(r"[\u3400-\u9fff]", char)
               for char in node.text or ""):
            raise LatexRenderError("unsupported Word math character")
        return "".join(
            r"\text{" + _escape_latex(chunk) + "}" if re.fullmatch(r"[\u3400-\u9fff]+", chunk)
            else "".join(symbols.get(char, _escape_latex(char)) for char in chunk)
            for chunk in chunks
        )
    if name in {"oMath", "oMathPara", "r", "e", "num", "den", "deg", "sub", "sup", "fName", "lim"}:
        return "".join(_math_latex(child) for child in node)
    if name == "rad":
        degree = node.find(f"{{{_M}}}deg")
        index = _math_latex(degree) if degree is not None else ""
        if prop("degHide", "0") in {"1", "true", "on"}:
            index = ""
        return r"\sqrt" + (f"[{index}]" if index else "") + "{" + part("e") + "}"
    if name == "f":
        style = prop("type", "bar")
        numerator, denominator = part("num"), part("den")
        if style == "bar":
            return rf"\frac{{{numerator}}}{{{denominator}}}"
        if style == "noBar":
            return rf"\genfrac{{}}{{}}{{0pt}}{{}}{{{numerator}}}{{{denominator}}}"
        if style == "lin":
            return rf"{{{numerator}}}/{{{denominator}}}"
        raise LatexRenderError(f"unsupported Word math fraction: {style}")
    if name in {"sSup", "sSub", "sSubSup", "sPre"}:
        base = "{" + part("e") + "}"
        sub = "_{" + part("sub") + "}" if name != "sSup" else ""
        sup = "^{" + part("sup") + "}" if name != "sSub" else ""
        return "{}" + sub + sup + base if name == "sPre" else base + sub + sup
    if name == "d":
        delimiters = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "": "."}
        left, right, separator = prop("begChr", "("), prop("endChr", ")"), prop("sepChr", "|")
        separators = {"|": "|", ",": ",", "，": ",", ";": ";", "；": ";", "": ""}
        if left not in delimiters or right not in delimiters:
            raise LatexRenderError("unsupported Word math delimiter")
        expressions = [_math_latex(child) for child in node if _local_name(child.tag) == "e"]
        if len(expressions) > 1 and separator not in separators:
            raise LatexRenderError("unsupported Word math separator")
        return r"\left" + delimiters[left] + separators.get(separator, "").join(expressions) + r"\right" + delimiters[right]
    if name in {"limUpp", "limLow"}:
        command = r"\overset" if name == "limUpp" else r"\underset"
        return command + "{" + part("lim") + "}{" + part("e") + "}"
    if name == "bar":
        command = r"\underline" if prop("pos", "top") == "bot" else r"\overline"
        return command + "{" + part("e") + "}"
    if name == "acc":
        command = {"̂": r"\hat", "̅": r"\overline", "⃗": r"\vec", "→": r"\vec"}.get(prop("chr", "̂"))
        if command is not None:
            return command + "{" + part("e") + "}"
    if name == "func":
        return part("fName") + r"\," + part("e")
    if name in {"eqArr", "m"}:
        if name == "eqArr":
            rows = [_math_latex(child) for child in node if _local_name(child.tag) == "e"]
            environment = "gathered"
        else:
            rows = [" & ".join(_math_latex(cell) for cell in row if _local_name(cell.tag) == "e")
                    for row in node if _local_name(row.tag) == "mr"]
            environment = "matrix"
        return rf"\begin{{{environment}}}" + r" \\ ".join(rows) + rf"\end{{{environment}}}"
    raise LatexRenderError(f"unsupported Word math structure: {name}")


def _run_content(
    run: ET.Element,
    relationships: Mapping[str, str],
    *,
    data_root: Path,
    inline_max_height_mm: float = _IMAGE_INLINE_MAX_HEIGHT_MM,
    escape_text: Callable[[str], str] = _escape_latex,
    render_image: Callable | None = None,
    line_break: str = "\\\\\n",
) -> str:
    parts: list[str] = []

    def visit(node: ET.Element) -> None:
        name = _local_name(node.tag)
        if name in {"oMath", "oMathPara"}:
            math_text = _math_latex(node)
            if math_text.strip():
                parts.append(r"\(" + math_text + r"\)")
            return
        if name == "t" and node.text:
            parts.append(escape_text(node.text))
        elif name == "br":
            parts.append(line_break)
        elif name == "tab":
            parts.append("\\hspace{2em}")
        elif name == "drawing":
            parts.append((render_image or _inline_image)(
                node,
                relationships,
                data_root=data_root,
                max_height_mm=inline_max_height_mm,
            ))
            return
        else:
            for child in node:
                visit(child)

    visit(run)
    return "".join(parts)


def _run_latex_segments(
    paragraph: ET.Element,
    relationships: Mapping[str, str],
    *,
    data_root: Path,
    inline_max_height_mm: float = _IMAGE_INLINE_MAX_HEIGHT_MM,
    escape_text: Callable[[str], str] = _escape_latex,
    render_image: Callable | None = None,
    line_break: str = "\\\\\n",
) -> str:
    """Walk one ``w:p`` and emit LaTeX for runs, breaks, OMML and drawings."""
    parts: list[str] = []
    for child in paragraph:
        name = _local_name(child.tag)
        if name in ("r", "hyperlink"):
            runs = [child] if name == "r" else list(child)
            for run in runs:
                if _local_name(run.tag) != "r":
                    continue
                content = _run_content(
                    run,
                    relationships,
                    data_root=data_root,
                    inline_max_height_mm=inline_max_height_mm,
                    escape_text=escape_text,
                    render_image=render_image,
                    line_break=line_break,
                )
                if not content:
                    continue
                rpr = run.find(f"{{{_W}}}rPr")
                if rpr is not None:
                    vert = rpr.find(f"{{{_W}}}vertAlign")
                    if vert is not None:
                        align = str(vert.get(f"{{{_W}}}val") or "")
                        if align == "superscript":
                            content = f"\\textsuperscript{{{content}}}"
                        elif align == "subscript":
                            content = f"\\textsubscript{{{content}}}"
                    underline = rpr.find(f"{{{_W}}}u")
                    if underline is not None and str(
                        underline.get(f"{{{_W}}}val") or "single"
                    ) not in ("none", ""):
                        content = f"\\underline{{{content}}}"
                parts.append(content)
        elif name in ("oMath", "oMathPara"):
            math_text = _math_latex(child)
            if math_text.strip():
                parts.append(r"\(" + math_text + r"\)")
    return "".join(parts)


def _inline_image(
    drawing: ET.Element,
    relationships: Mapping[str, str],
    *,
    data_root: Path,
    max_height_mm: float = _IMAGE_INLINE_MAX_HEIGHT_MM,
) -> str:
    embed = None
    for blip in drawing.iter(f"{{{_A}}}blip"):
        embed = blip.get(f"{{{_R}}}embed")
        if embed:
            break
    if not embed or embed not in relationships:
        raise LatexRenderError("question image is missing its frozen asset")
    asset = (data_root / relationships[embed]).resolve()
    root = data_root.resolve()
    if root not in asset.parents or not asset.is_file():
        raise LatexRenderError("question image is missing its frozen asset")
    asset = _prepare_image(asset, data_root=data_root)
    return (
        "\\raisebox{-0.72\\height}{"
        f"\\includegraphics[height={max_height_mm}mm]{{{_tex_path(asset)}}}"
        "}"
    )


def _standalone_image(
    paragraph: ET.Element,
    relationships: Mapping[str, str],
    *,
    data_root: Path,
    cap_mm: float = _IMAGE_BLOCK_MAX_WIDTH_MM,
) -> tuple[Path, float] | None:
    """Return (asset, width_mm) when the paragraph is exactly one picture."""
    texts = [
        node.text
        for node in paragraph.iter()
        if _local_name(node.tag) == "t" and (node.text or "").strip()
    ]
    if texts:
        return None
    drawings = [
        node for node in paragraph.iter() if _local_name(node.tag) == "drawing"
    ]
    if len(drawings) != 1:
        return None
    embed = None
    for blip in drawings[0].iter(f"{{{_A}}}blip"):
        embed = blip.get(f"{{{_R}}}embed")
        if embed:
            break
    if not embed or embed not in relationships:
        raise LatexRenderError("question image is missing its frozen asset")
    asset = (data_root / relationships[embed]).resolve()
    root = data_root.resolve()
    if root not in asset.parents or not asset.is_file():
        raise LatexRenderError("question image is missing its frozen asset")
    asset = _prepare_image(asset, data_root=data_root)
    width_mm = cap_mm
    for extent in drawings[0].iter():
        if _local_name(extent.tag) == "extent":
            try:
                width_mm = min(
                    cap_mm,
                    int(extent.get("cx") or 0) / EMU_PER_MM,
                )
            except (TypeError, ValueError):
                width_mm = cap_mm
            break
    return asset, max(width_mm, 10.0)


def _tex_path(path: Path) -> str:
    return path.as_posix()


def _prepare_image(asset: Path, *, data_root: Path) -> Path:
    """Return a print-ready copy of the frozen image (downscaled/recompressed).

    题库原图多为整页照片（单张约 1MB），直接进 PDF 会膨胀数倍；
    统一压缩到印刷足够的一千像素宽，按内容哈希缓存复用。
    """
    import hashlib as _hashlib

    from PIL import Image

    digest = _hashlib.sha256(asset.read_bytes()).hexdigest()
    cache_dir = (
        data_root.resolve() / "question_bank" / "personalized_papers" / "image_cache"
    )
    for extension in (".jpg", ".png"):
        cached = cache_dir / f"{digest}{extension}"
        if cached.is_file():
            return cached
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(asset) as source:
            has_alpha = source.mode in ("RGBA", "LA", "PA") or (
                source.mode == "P" and "transparency" in source.info
            )
            image = source.convert("RGBA" if has_alpha else "RGB")
            width, height = image.size
            if max(width, height) > _IMAGE_MAX_EDGE_PX:
                ratio = _IMAGE_MAX_EDGE_PX / max(width, height)
                image = image.resize(
                    (
                        max(1, int(round(width * ratio))),
                        max(1, int(round(height * ratio))),
                    ),
                    Image.LANCZOS,
                )
            if has_alpha:
                target = cache_dir / f"{digest}.png"
                image.save(target, format="PNG", optimize=True)
            else:
                target = cache_dir / f"{digest}.jpg"
                image.save(target, format="JPEG", quality=85, optimize=True)
        return target
    except Exception as exc:
        raise LatexRenderError("question image could not be recompressed") from exc


def _table_latex(
    table: ET.Element,
    relationships: Mapping[str, str],
    *,
    data_root: Path,
) -> str:
    rows: list[str] = []
    for row in table.iter(f"{{{_W}}}tr"):
        cells: list[str] = []
        for cell in row.iter(f"{{{_W}}}tc"):
            fragments: list[str] = []
            for paragraph in cell.iter(f"{{{_W}}}p"):
                fragment = _run_latex_segments(
                    paragraph,
                    relationships,
                    data_root=data_root,
                ).strip()
                if fragment:
                    fragments.append(fragment)
            # 单元格保留换行与段内图片，不再拍平成一段纯文本。
            cells.append(" \\newline ".join(fragments))
        if cells:
            rows.append(" & ".join(cells) + " \\\\")
    if not rows:
        raise LatexRenderError("table block has no readable rows")
    column_count = max(row.count("&") + 1 for row in rows)
    column_width = round(_CONTENT_WIDTH_MM / column_count, 1)
    spec = "|" + "|".join(
        f"p{{{column_width}mm}}" for _ in range(column_count)
    ) + "|"
    body = "\n\\hline\n".join(rows)
    return (
        f"\\par\\begin{{center}}\\begin{{tabular}}{{{spec}}}\n\\hline\n"
        f"{body}\n\\hline\n\\end{{tabular}}\\end{{center}}\n"
    )


def _is_option_picture_paragraph(element: ET.Element) -> bool:
    has_drawing = any(
        _local_name(node.tag) == "drawing" for node in element.iter()
    )
    if not has_drawing:
        return False
    visible = "".join(
        node.text or ""
        for node in element.iter()
        if _local_name(node.tag) == "t"
    ).strip()
    return bool(_OPTION_LETTER.match(visible))


def _question_blocks_latex(
    blocks: Sequence[Mapping[str, Any]],
    *,
    data_root: Path,
    number: int = 0,
    answer_lines: int = 0,
) -> str:
    """Convert frozen rich blocks to LaTeX, Word 式紧凑布局：图文并排不浮动。"""
    infos: list[tuple[str, ET.Element, Mapping[str, str], Any]] = []
    cap_mm = (
        _IMAGE_BLOCK_OBJECTIVE_MAX_WIDTH_MM
        if answer_lines == 0
        else _IMAGE_BLOCK_MAX_WIDTH_MM
    )
    for block in blocks:
        xml = str(block.get("xml") or "").strip()
        if not xml:
            if str(block.get("text") or "").strip():
                raise LatexRenderError("rich block has text but no frozen XML")
            continue
        try:
            element = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise LatexRenderError("rich block XML is unreadable") from exc
        relationships = {
            str(key): str(value)
            for key, value in _mapping(block.get("image_relationships")).items()
        }
        name = _local_name(element.tag)
        if name == "tbl":
            infos.append(("table", element, relationships, None))
            continue
        if name != "p":
            raise LatexRenderError(f"unsupported rich block element: {name}")
        infos.append((
            "paragraph",
            element,
            relationships,
            _standalone_image(
                element,
                relationships,
                data_root=data_root,
                cap_mm=cap_mm,
            ),
        ))

    # 带图解答题：末尾独立图先取出，作答区排在图的左侧。
    trailing_images: list[tuple[Path, float]] = []
    if answer_lines > 0:
        while infos and infos[-1][0] == "paragraph" and infos[-1][3] is not None:
            trailing_images.insert(0, infos.pop()[3])

    output: list[str] = []
    numbered = False
    text_cache: dict[int, str] = {}

    def text_of(
        entry: tuple[str, ET.Element, Mapping[str, str], Any],
        *,
        inline_max_height_mm: float = _IMAGE_INLINE_MAX_HEIGHT_MM,
    ) -> str:
        nonlocal numbered
        cache_key = (id(entry), inline_max_height_mm)
        if cache_key in text_cache:
            return text_cache[cache_key]
        _kind, element, relationships, _image = entry
        text = _run_latex_segments(
            element,
            relationships,
            data_root=data_root,
            inline_max_height_mm=inline_max_height_mm,
        )
        if not numbered and text.strip():
            # 去掉题干自带的原卷题号，统一用本卷连续编号。
            text = _LEADING_NUMBER.sub("", text, count=1)
            text = f"\\textbf{{{number}.}} {text}"
            numbered = True
        text_cache[cache_key] = text
        return text

    def emit_pair(
        image: tuple[Path, float],
        text_entry: tuple[str, ET.Element, Mapping[str, str], Any],
    ) -> None:
        asset, width = image
        text = text_of(text_entry)
        image_width = min(width, cap_mm)
        text_width = _CONTENT_WIDTH_MM - image_width - 8
        output.append(
            "\\par\\noindent"
            f"\\begin{{minipage}}[t]{{{text_width:.1f}mm}}\n\\vspace{{0pt}}\n{text}\n"
            "\\end{minipage}\\hfill\n"
            f"\\begin{{minipage}}[t]{{{image_width:.1f}mm}}\n\\vspace{{0pt}}\n\\centering\n"
            f"\\includegraphics[width=\\textwidth]{{{_tex_path(asset)}}}\n"
            "\\end{minipage}\\par\n"
        )

    def emit_image_group(group: list[tuple[Path, float]]) -> None:
        if len(group) == 1:
            asset, width = group[0]
            output.append(
                "\\par\\begin{center}"
                f"\\includegraphics[width={width:.1f}mm]{{{_tex_path(asset)}}}"
                "\\end{center}\n"
            )
            return
        fraction = round(0.96 / len(group), 3)
        row = "\\hfill\n".join(
            f"\\begin{{minipage}}[b]{{{fraction}\\textwidth}}\n\\vspace{{0pt}}\n"
            "\\centering\n"
            f"\\includegraphics[width=\\textwidth]{{{_tex_path(asset)}}}\n"
            "\\end{minipage}"
            for asset, _width in group
        )
        output.append(f"\\par\\begin{{center}}{row}\\end{{center}}\n")

    def emit_option_grid(
        run: list[tuple[str, ET.Element, Mapping[str, str], Any]],
    ) -> None:
        column = (_CONTENT_WIDTH_MM - 8) / 2
        rows: list[str] = []
        for start in range(0, len(run), 2):
            pair = run[start : start + 2]
            left = text_of(
                pair[0],
                inline_max_height_mm=_IMAGE_OPTION_GRID_MAX_HEIGHT_MM,
            )
            right = (
                text_of(
                    pair[1],
                    inline_max_height_mm=_IMAGE_OPTION_GRID_MAX_HEIGHT_MM,
                )
                if len(pair) == 2
                else ""
            )
            rows.append(f"{left} & {right} \\\\")
        body = "\n".join(rows)
        output.append(
            "\\par\\noindent"
            f"\\begin{{tabular}}{{@{{}}p{{{column:.1f}mm}}p{{{column:.1f}mm}}@{{}}}}\n"
            f"{body}\n\\end{{tabular}}\\par\n"
        )

    index = 0
    while index < len(infos):
        kind, element, _relationships, image = infos[index]
        if kind == "table":
            output.append(_table_latex(
                element,
                _relationships,
                data_root=data_root,
            ))
            index += 1
            continue
        # 选项带图：连续的 A./B./C./D. 段排成两列网格。
        if image is None and _is_option_picture_paragraph(element):
            run: list[tuple[str, ET.Element, Mapping[str, str], Any]] = []
            while (
                index < len(infos)
                and infos[index][0] == "paragraph"
                and infos[index][3] is None
                and _is_option_picture_paragraph(infos[index][1])
            ):
                run.append(infos[index])
                index += 1
            if len(run) >= 2:
                emit_option_grid(run)
            else:
                for entry in run:
                    text = text_of(entry)
                    if text.strip():
                        output.append(f"{text}\\par\n")
            continue
        next_entry = infos[index + 1] if index + 1 < len(infos) else None
        next_is_plain_text = (
            next_entry is not None
            and next_entry[0] == "paragraph"
            and next_entry[3] is None
        )
        # 图先文后：并排（文左图右），短文才并排，长文图居下。
        if (
            image is not None
            and next_is_plain_text
            and len(text_of(next_entry)) <= 500
        ):
            emit_pair(image, next_entry)
            index += 2
            continue
        # 文先图后（单图且短文）：并排。
        if (
            image is None
            and next_entry is not None
            and next_entry[0] == "paragraph"
            and next_entry[3] is not None
            and len(text_of(infos[index])) <= 500
        ):
            emit_pair(next_entry[3], infos[index])
            index += 2
            continue
        if image is not None:
            group = [image]
            while (
                index + 1 < len(infos)
                and infos[index + 1][0] == "paragraph"
                and infos[index + 1][3] is not None
            ):
                group.append(infos[index + 1][3])
                index += 1
            emit_image_group(group)
            index += 1
            continue
        text = text_of(infos[index])
        if text.strip():
            output.append(f"{text}\\par\n")
        index += 1
    if trailing_images:
        # 作答区排左列，末尾图排右侧；高度约为普通留白的一半。
        height = answer_lines * 4.5
        blank_width = _CONTENT_WIDTH_MM - sum(
            min(width, cap_mm) for _asset, width in trailing_images
        ) - 8 * len(trailing_images)
        blank_width = max(blank_width, 40.0)
        cells = [
            f"\\begin{{minipage}}[t]{{{blank_width:.1f}mm}}\n\\vspace{{0pt}}\n"
            f"\\rule{{0pt}}{{{height:.1f}mm}}\n\\end{{minipage}}"
        ]
        for asset, width in trailing_images:
            image_width = min(width, cap_mm)
            cells.append(
                f"\\begin{{minipage}}[t]{{{image_width:.1f}mm}}\n\\vspace{{0pt}}\n"
                "\\centering\n"
                f"\\includegraphics[width=\\textwidth]{{{_tex_path(asset)}}}\n"
                "\\end{minipage}"
            )
        output.append("\\par\\noindent" + "\\hfill\n".join(cells) + "\\par\n")
    elif answer_lines > 0:
        output.append(f"\\par\\vspace*{{{answer_lines * 9}mm}}\n")
    if not numbered and number:
        # 纯图题没有文本段落：题号补在最前。
        output.insert(0, f"\\textbf{{{number}.}}\\par\n")
    return "\n".join(output)


_HEADER = r"""\documentclass[11pt]{article}
\usepackage{xeCJK}
\usepackage{graphicx}
\usepackage{geometry}
\usepackage{fancyhdr}
\usepackage{amssymb}
\usepackage{amsmath}
\geometry{a4paper,top=20mm,bottom=28mm,left=18mm,right=18mm}
\setCJKmainfont[AutoFakeBold=2.5]{SimSun}
\setmainfont{SimSun}
\linespread{1.1}
\setlength{\parindent}{0pt}
\setlength{\parskip}{4pt}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[C]{\small __HEADER_LINE__}
\fancyfoot[C]{\small 第~\thepage~页}
\renewcommand{\headrulewidth}{0pt}
\begin{document}
"""

_FOOTER = "\\end{document}\n"


def render_training_tex(
    snapshot: Mapping[str, Any],
    *,
    data_root: Path,
) -> str:
    """Build the full LaTeX source for one frozen paper snapshot."""
    student = _mapping(snapshot.get("student"))
    items = _mappings(snapshot.get("items"))
    if not items:
        raise LatexRenderError("paper snapshot has no items")
    name = _escape_latex(str(
        student.get("student_name")
        or student.get("student_code")
        or student.get("student_id")
        or "学生"
    ))
    class_id = _escape_latex(str(student.get("class_id") or "________"))
    header_line = (
        f"姓名：{name}\\hspace{{2em}}班级：{class_id}"
        "\\hspace{2em}日期：\\_\\_\\_\\_年\\_\\_月\\_\\_日"
    )
    body: list[str] = []
    for index, item in enumerate(items, start=1):
        question = _mapping(item.get("question_snapshot"))
        context = _mapping(question.get("tagging_context"))
        question_type = str(context.get("question_type") or "")
        question_text = str(context.get("question_text") or "")
        blocks = _mappings(question.get("rich_question_blocks"))
        if not blocks:
            raise LatexRenderError("question has no frozen rich blocks")
        lines = answer_space_lines(question_type, question_text)
        content = _question_blocks_latex(
            strip_question_source_score_blocks(
                blocks, question_number=str(context.get("question_number") or ""),
            ),
            data_root=data_root,
            number=index,
            answer_lines=lines,
        )
        body.append(content)
    return (
        _HEADER.replace("__HEADER_LINE__", header_line)
        + "\n".join(body)
        + _FOOTER
    )


class TectonicCompiler:
    """Compile LaTeX sources through a bundled or system tectonic binary."""

    def __init__(
        self,
        executable: Path | None = None,
        *,
        timeout_seconds: int = 180,
    ) -> None:
        candidate = executable or _default_tectonic()
        self.executable = candidate if candidate and candidate.is_file() else None
        self.timeout_seconds = int(timeout_seconds)

    @property
    def available(self) -> bool:
        return self.executable is not None

    def compile(
        self, tex_source: str, output_pdf: Path, *,
        auxiliary_directory: Path | None = None, offline: bool = False,
        validate_layout: bool = False,
    ) -> None:
        if self.executable is None:
            raise LatexRenderError("no tectonic engine is available")
        destination = Path(output_pdf).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="latex-render-",
            ignore_cleanup_errors=True,
        ) as temporary:
            work = Path(temporary)
            tex_path = work / "paper.tex"
            tex_path.write_text(tex_source, encoding="utf-8")
            options = ["--only-cached", "--untrusted"] if offline else []
            if auxiliary_directory is not None:
                options.append("--keep-intermediates")
            environment = os.environ.copy()
            if offline and os.name == "nt":
                from xml.sax.saxutils import escape
                fonts = Path(environment.get("WINDIR", "C:/Windows")) / "Fonts"
                fontconfig = work / "fonts.conf"
                fontconfig.write_text(
                    '<fontconfig><dir>' + escape(fonts.as_posix()) + '</dir><cachedir>'
                    + escape((work / "font-cache").as_posix()) + '</cachedir></fontconfig>',
                    encoding="utf-8",
                )
                environment["FONTCONFIG_FILE"] = str(fontconfig)
            completed = subprocess.run(
                [
                    str(self.executable),
                    *options,
                    "--keep-logs",
                    "--outdir",
                    str(work),
                    str(tex_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=environment,
            )
            produced = work / "paper.pdf"
            if completed.returncode != 0 or not produced.is_file():
                log_tail = (completed.stdout or "")[-600:]
                raise LatexRenderError(
                    f"tectonic failed to compile the paper: {log_tail}"
                )
            diagnostics = (completed.stdout or "") + (completed.stderr or "")
            if validate_layout and any(marker in diagnostics for marker in (
                "Missing character:", r"Overfull \hbox", r"Overfull \vbox",
            )):
                raise LatexRenderError("LaTeX layout contains missing glyphs or overflowing content")
            shutil.copyfile(produced, destination)
            if auxiliary_directory is not None:
                auxiliary_directory.mkdir(parents=True, exist_ok=True)
                for suffix in (".positions", ".measures"):
                    source = work / ("paper" + suffix)
                    if source.is_file():
                        shutil.copyfile(source, auxiliary_directory / source.name)


def _default_tectonic() -> Path | None:
    bundled = (
        Path(__file__).resolve().parents[2]
        / "runtime"
        / "tectonic"
        / "tectonic.exe"
    )
    if bundled.is_file():
        return bundled
    found = shutil.which("tectonic")
    return Path(found) if found else None
