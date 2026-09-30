"""Project frozen Word block XML into a small, controlled HTML subset.

The preview UI used to rebuild layouts from the linearized block text with
layout heuristics (option grids, media pairing, …), which broke whenever a
document looked even slightly different.  This module instead renders the
stored Word XML faithfully: text runs keep their superscript/subscript/
underline styling, OMML math becomes KaTeX-hydratable ``data-latex`` spans,
drawings become ``<img>`` tags pointing at the question's controlled asset
URLs, and tables become real HTML tables — all in original document order.

Only a whitelisted tag set is ever emitted (``sup/sub/u/br/img/table/tr/td/
span``) and every text payload is escaped, so the output is safe to render
with ``v-html`` in the trusted local single-user context.
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Mapping
from xml.etree import ElementTree

from question_bank.services.inline_math import omml_parts

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]", re.IGNORECASE | re.DOTALL)

_MAX_ELEMENTS = 4_000


def block_preview_html(
    xml: str,
    rel_urls: Mapping[str, str],
    *,
    expected_text: str = "",
) -> str | None:
    """Render one stored block XML (paragraph or table) to controlled HTML.

    ``rel_urls`` maps Word relationship ids to the block's controlled asset
    URLs.  Returns ``None`` when the XML is missing/unparseable, or when
    ``expected_text`` is given and the rendered plain text diverges from it
    (the stored text is authoritative; divergence means the XML drifted and
    the caller must keep the legacy rendering).
    """
    source = str(xml or "").strip()
    if not source:
        return None
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return None

    budget = [_MAX_ELEMENTS]
    name = _local_name(root.tag)
    if name == "p":
        rendered = "".join(_paragraph_children_html(root, rel_urls, budget))
    elif name == "tbl":
        rendered = _table_html(root, rel_urls, budget)
    else:
        return None
    if budget[0] <= 0 or not rendered:
        return None
    if expected_text:
        if _normalize(_plain_text(rendered)) != _normalize_expected(expected_text):
            return None
    return rendered


def _local_name(tag: object) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _attribute_value(element: ElementTree.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return str(value)
    return None


# ---------------------------------------------------------------------------
# paragraphs
# ---------------------------------------------------------------------------

def _paragraph_children_html(
    paragraph: ElementTree.Element,
    rel_urls: Mapping[str, str],
    budget: list[int],
) -> list[str]:
    parts: list[str] = []
    for child in paragraph:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        name = _local_name(child.tag)
        if name == "r":
            parts.extend(_run_html(child, rel_urls, budget))
        elif name == "hyperlink":
            for run in child:
                if _local_name(run.tag) == "r":
                    parts.extend(_run_html(run, rel_urls, budget))
        elif name in ("oMath", "oMathPara"):
            formula = _formula_html(child, display=(name == "oMathPara"))
            if formula:
                parts.append(formula)
        # pPr, bookmarks, proof markers … carry no visible inline content.
    return parts


def _run_html(
    run: ElementTree.Element,
    rel_urls: Mapping[str, str],
    budget: list[int],
) -> list[str]:
    superscript = subscript = underline = False
    rpr = _child(run, "rPr")
    if rpr is not None:
        vert = _child(rpr, "vertAlign")
        if vert is not None:
            align = _attribute_value(vert, "val")
            superscript = align == "superscript"
            subscript = align == "subscript"
        underline_element = _child(rpr, "u")
        if underline_element is not None:
            underline = _attribute_value(underline_element, "val") not in (
                "none", "0", "false",
            )

    parts: list[str] = []
    for child in run:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        name = _local_name(child.tag)
        if name == "t":
            text = _html.escape(child.text or "")
            if not text:
                continue
            if underline:
                text = f"<u>{text}</u>"
            if superscript:
                text = f"<sup>{text}</sup>"
            elif subscript:
                text = f"<sub>{text}</sub>"
            parts.append(text)
        elif name == "tab":
            parts.append("\t")
        elif name == "br":
            parts.append("<br>")
        elif name in ("drawing", "pict", "object"):
            parts.extend(_drawing_html(child, rel_urls))
        elif name in ("oMath", "oMathPara"):
            formula = _formula_html(child, display=(name == "oMathPara"))
            if formula:
                parts.append(formula)
    return parts


def _drawing_html(
    drawing: ElementTree.Element,
    rel_urls: Mapping[str, str],
) -> list[str]:
    images: list[str] = []
    for node in drawing.iter():
        if _local_name(node.tag) != "blip":
            continue
        rel_id = _attribute_value(node, "embed") or _attribute_value(node, "link")
        if not rel_id:
            continue
        url = rel_urls.get(rel_id)
        if url:
            images.append(
                f'<img src="{_html.escape(url, quote=True)}" alt="题目图片" loading="lazy">'
            )
    return images


def _formula_html(element: ElementTree.Element, *, display: bool) -> str | None:
    parts = omml_parts(element)
    if parts is None:
        return None
    latex, plain = parts
    css_class = "qm qm--display" if display else "qm"
    return (
        f'<span class="{css_class}" data-latex="{_html.escape(latex, quote=True)}">'
        f"{_html.escape(plain)}</span>"
    )


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def _table_html(
    table: ElementTree.Element,
    rel_urls: Mapping[str, str],
    budget: list[int],
) -> str:
    rows: list[str] = []
    for row in table:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        if _local_name(row.tag) != "tr":
            continue
        cells: list[str] = []
        for cell in row:
            if _local_name(cell.tag) != "tc":
                continue
            cells.append(f"<td>{_cell_html(cell, rel_urls, budget)}</td>")
        if cells:
            rows.append("<tr>" + "".join(cells) + "</tr>")
    if not rows:
        return ""
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _cell_html(
    cell: ElementTree.Element,
    rel_urls: Mapping[str, str],
    budget: list[int],
) -> str:
    parts: list[str] = []
    for child in cell:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        name = _local_name(child.tag)
        if name == "p":
            paragraph_html = "".join(_paragraph_children_html(child, rel_urls, budget))
            if paragraph_html:
                parts.append(paragraph_html)
        elif name == "tbl":
            parts.append(_table_html(child, rel_urls, budget))
    return "<br>".join(parts)


# ---------------------------------------------------------------------------
# consistency check
# ---------------------------------------------------------------------------

def _plain_text(rendered_html: str) -> str:
    return _html.unescape(_TAG_PATTERN.sub("", rendered_html))


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _normalize_expected(text: str) -> str:
    stripped = _IMAGE_MARKER.sub("", str(text or ""))
    stripped = _TAG_PATTERN.sub("", stripped)
    return _normalize(_html.unescape(stripped))


__all__ = ["block_preview_html"]
