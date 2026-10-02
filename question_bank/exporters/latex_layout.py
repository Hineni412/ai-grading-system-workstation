"""Measured LaTeX layout for ordinary question papers.

Consumes published rich blocks without rewriting assets. All measurements and
choices belong to one export; no question-specific rules or global patches.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from functools import partial
from itertools import pairwise

from PIL import Image

from question_bank.personalized_papers import latex_render as lr

W = lr._W


def tag(node):
    return lr._local_name(node.tag)


def escape_text(text):
    return "".join(
        "{\\LayoutGlyph " + lr._escape_latex(c) + "}"
        if ord(c) > 127
        and not (
            0x3400 <= ord(c) <= 0x9FFF
            or 0x3000 <= ord(c) <= 0x303F
            or 0xFF00 <= ord(c) <= 0xFFEF
        )
        else lr._escape_latex(c)
        for c in text
    )


def asset_info(drawing, relationships, data_root):
    if tag(drawing) == "pict":
        raise lr.LatexRenderError(
            "old VML picture requires preservation before PDF use"
        )
    embed = next(
        (
            n.get("{" + lr._R + "}embed")
            for n in drawing.iter("{" + lr._A + "}blip")
            if n.get("{" + lr._R + "}embed")
        ),
        None,
    )
    if not embed or embed not in relationships:
        raise lr.LatexRenderError("missing image relationship")
    asset = (data_root / relationships[embed]).resolve()
    if data_root.resolve() not in asset.parents or not asset.is_file():
        raise lr.LatexRenderError("missing frozen image")
    extent = next((n for n in drawing.iter() if tag(n) == "extent"), None)
    if any(c in asset.as_posix() for c in "{}%#^~\n\r"):
        raise lr.LatexRenderError("image path cannot be represented safely")
    with Image.open(asset) as im:
        if im.format not in {"PNG", "JPEG"}:
            raise lr.LatexRenderError("unsupported picture format")
        pw, ph = im.size
    width = float(extent.get("cx", 0)) / lr.EMU_PER_MM if extent is not None else 0
    height = float(extent.get("cy", 0)) / lr.EMU_PER_MM if extent is not None else 0
    if width <= 0:
        width = min(174, pw * 25.4 / 200)
    if height <= 0:
        height = width * ph / pw
    return asset, width, height, pw / ph


def image_tex(info, *, height=230):
    asset, width, _h, _ratio = info
    # TeX measures the actual cell/minipage width; never enlarge smaller source art.
    return (
        r"\LayoutImage{"
        + f"{width:.3f}mm"
        + "}{"
        + f"{height:.3f}mm"
        + "}{"
        + asset.as_posix()
        + "}"
    )


def inline_image(
    drawing, relationships, *, data_root, max_height_mm=22, force_inline=False
):
    info = asset_info(drawing, relationships, data_root)
    inline = force_inline or info[2] <= 22
    graphic = image_tex(info, height=230 if force_inline or not inline else 22)
    return (
        r"\raisebox{-0.72\height}{" + graphic + "}"
        if inline
        else r"\par\noindent\makebox[\linewidth][c]{" + graphic + r"}\par "
    )


def run_segments(
    paragraph,
    relationships,
    *,
    data_root,
    inline_max_height_mm=22,
    table_cell=False,
    option=False,
):
    if any(tag(n) == "pict" for n in paragraph.iter()):
        raise lr.LatexRenderError("unsupported VML picture")
    supported = {
        "pPr",
        "r",
        "oMath",
        "oMathPara",
        "hyperlink",
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "permStart",
        "permEnd",
    }
    if any(tag(n) not in supported for n in paragraph):
        raise lr.LatexRenderError("unsupported paragraph content")
    return lr._run_latex_segments(
        paragraph,
        relationships,
        data_root=data_root,
        inline_max_height_mm=inline_max_height_mm,
        escape_text=escape_text,
        render_image=partial(inline_image, force_inline=option),
        line_break=r"\newline{}" if table_cell else "\\\\\n",
    )


def split_options(paragraph, rels, data_root):
    visible = "".join(n.text or "" for n in paragraph.iter("{" + W + "}t"))
    if not lr._OPTION_LETTER.match(visible):
        return []
    rendered = run_segments(
        paragraph, rels, data_root=data_root, inline_max_height_mm=22, option=True
    )
    # Only split an option paragraph with an explicit leading option marker.
    if not lr._OPTION_LETTER.match(rendered):
        return []
    depth = 0
    eligible = set()
    for i, c in enumerate(rendered):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if depth == 0:
            eligible.add(i)
    cuts = [
        m
        for m in re.finditer(r"(?<![A-Za-z])[A-DＡ-Ｄ][.、．]", rendered)
        if m.start() in eligible
    ]
    return [
        rendered[
            m.start() : (cuts[i + 1].start() if i + 1 < len(cuts) else len(rendered))
        ].strip()
        for i, m in enumerate(cuts)
    ]


MACROS = r"""
\usepackage{longtable}
\newfontfamily\LayoutGlyph{SimSun}
\newsavebox{\LayoutBox}
\newsavebox{\ChoiceA}\newsavebox{\ChoiceB}\newsavebox{\ChoiceC}\newsavebox{\ChoiceD}
\newdimen\LayoutWidth
\newdimen\LayoutHeight
\newcommand{\LayoutNeed}[1]{\par\begingroup
  \setbox\LayoutBox=\vbox{\hsize=\linewidth #1\par}
  \LayoutHeight=\dimexpr\ht\LayoutBox+\dp\LayoutBox+2\baselineskip\relax
  \ifdim\LayoutHeight<.85\textheight
    \ifdim\pagegoal<\maxdimen
      \ifdim\LayoutHeight>\dimexpr\pagegoal-\pagetotal\relax \newpage\fi
    \fi
  \fi\endgroup}
\newcommand{\LayoutImage}[3]{\begingroup
  \LayoutWidth=#1\relax
  \ifdim\LayoutWidth>\linewidth \LayoutWidth=\linewidth\fi
  \includegraphics[width=\LayoutWidth,height=#2,keepaspectratio]{#3}\endgroup}
\newcommand{\LayoutKeep}[1]{\par\begingroup
  \setbox\LayoutBox=\vbox{\hsize=\linewidth #1\par}
  \LayoutHeight=\dimexpr\ht\LayoutBox+\dp\LayoutBox+\baselineskip\relax
  \ifdim\LayoutHeight<110mm
    \ifinner\else
    \ifdim\pagegoal<\maxdimen
      \ifdim\LayoutHeight>\dimexpr\pagegoal-\pagetotal\relax \newpage\fi
    \fi
    \fi
  \fi
  \ifdim\LayoutHeight<110mm \box\LayoutBox\else\unvbox\LayoutBox\fi\endgroup\par}
\newcommand{\LayoutChoices}[4]{\begingroup
  \sbox{\ChoiceA}{#1}\sbox{\ChoiceB}{#2}\sbox{\ChoiceC}{#3}\sbox{\ChoiceD}{#4}
  \LayoutWidth=\wd\ChoiceA
  \ifdim\wd\ChoiceB>\LayoutWidth \LayoutWidth=\wd\ChoiceB\fi
  \ifdim\wd\ChoiceC>\LayoutWidth \LayoutWidth=\wd\ChoiceC\fi
  \ifdim\wd\ChoiceD>\LayoutWidth \LayoutWidth=\wd\ChoiceD\fi
  \ifdim\LayoutWidth<.235\linewidth
    \noindent\makebox[.245\linewidth][l]{\usebox{\ChoiceA}}\hfill
    \makebox[.245\linewidth][l]{\usebox{\ChoiceB}}\hfill
    \makebox[.245\linewidth][l]{\usebox{\ChoiceC}}\hfill
    \makebox[.245\linewidth][l]{\usebox{\ChoiceD}}\par
  \else\ifdim\LayoutWidth<.48\linewidth
    \noindent\makebox[.49\linewidth][l]{\usebox{\ChoiceA}}\hfill\makebox[.49\linewidth][l]{\usebox{\ChoiceB}}\par
    \noindent\makebox[.49\linewidth][l]{\usebox{\ChoiceC}}\hfill\makebox[.49\linewidth][l]{\usebox{\ChoiceD}}\par
  \else #1\par #2\par #3\par #4\par\fi\fi\endgroup}


\usepackage[longtable]{multirow}
\newsavebox{\LayoutCellBox}
\newdimen\LayoutRowHeight
\newdimen\LayoutSpanHeight
\newcommand{\LayoutRowInit}[1]{\expandafter\def\csname LayoutRow#1\endcsname{0pt}}
\newcommand{\LayoutRowMeasure}[4]{%
  \setbox\LayoutCellBox=\vbox{\hsize=#2\relax\linewidth=#2\relax #4\par}%
  \LayoutRowHeight=\dimexpr\ht\LayoutCellBox+\dp\LayoutCellBox+2mm\relax
  \ifnum#3>1\relax
    \advance\LayoutRowHeight by 2\baselineskip
  \fi
  \divide\LayoutRowHeight by #3\relax
  \ifdim\LayoutRowHeight>\csname LayoutRow#1\endcsname\relax
    \expandafter\edef\csname LayoutRow#1\endcsname{\the\LayoutRowHeight}%
  \fi}
\newcommand{\LayoutRowStrut}[1]{%
  \LayoutRowHeight=\csname LayoutRow#1\endcsname\relax
  \ifdim\LayoutRowHeight<\dimexpr\ht\strutbox+\dp\strutbox\relax
    \LayoutRowHeight=\dimexpr\ht\strutbox+\dp\strutbox\relax\fi
  \rule[-\dimexpr\LayoutRowHeight-\ht\strutbox\relax]{0pt}{\LayoutRowHeight}}
\ExplSyntaxOn
\cs_new_protected:Npn \LayoutMerged #1#2#3 {
  \LayoutSpanHeight=0pt\relax
  \clist_map_inline:nn {#1} {
    \LayoutRowHeight=\csname LayoutRow##1\endcsname\relax
    \dim_compare:nNnT {\LayoutRowHeight} < {\ht\strutbox+\dp\strutbox}
      {\LayoutRowHeight=\dimexpr\ht\strutbox+\dp\strutbox\relax}
    \advance\LayoutSpanHeight by \LayoutRowHeight
  }
  \multirow[t]{\fp_eval:n {
    \dim_to_fp:n {\LayoutSpanHeight} /
    \dim_to_fp:n {\ht\strutbox+\dp\strutbox} / 1.15
  }}{#2}{\begin{minipage}[t]{#2}\vspace{0pt}#3\end{minipage}}
}
\ExplSyntaxOff


\clubpenalty=10000\widowpenalty=10000
\newwrite\LayoutPositions
\immediate\openout\LayoutPositions=\jobname.positions
\newcount\LayoutSlot
\newcommand{\LayoutPosition}[1]{%
  \pdfsavepos\write\LayoutPositions{#1|\thepage|\number\pdflastxpos|\number\pdflastypos}}
\newcommand{\LayoutSpacePiece}[3]{%
  \hrule height0pt\relax\LayoutPosition{A:#1:#2:start}%
  \nobreak\vskip#3\relax\nobreak
  \hrule height0pt\relax\LayoutPosition{A:#1:#2:end}\penalty0}
\newcommand{\LayoutAnswerSpace}[2]{\par\begingroup
  \LayoutSlot=0\relax
  \loop\ifnum\LayoutSlot<#2\relax
    \advance\LayoutSlot by 1\relax
    \edef\LayoutCall{\noexpand\LayoutSpacePiece{#1}{\the\LayoutSlot}{9mm}}%
    \LayoutCall
  \repeat\endgroup}
\newcommand{\LayoutSideSpace}[2]{\par\noindent
  \hbox{\vbox{\LayoutPosition{A:#1:side:start}\vskip#2\relax\LayoutPosition{A:#1:side:end}}}\par}
"""


class PaperLatexLayout:
    """One paper's table probes and actual page positions."""

    def __init__(self, table_choices=None):
        self.table_choices = table_choices or {}
        self.probes = []
        self.table_count = 0
        self.merge_count = 0
        self.number = 0
        self.mode = "question"

    def _simple_table(self, table, relationships, *, data_root):
        if table.find(".//{" + W + "}tbl") is not None:
            raise lr.LatexRenderError("nested table requires separate layout")
        raw = []
        boundaries = {0}
        totals = []
        for tr in table.findall("{" + W + "}tr"):
            if (
                tr.find("{" + W + "}trPr/{" + W + "}gridBefore") is not None
                or tr.find("{" + W + "}trPr/{" + W + "}gridAfter") is not None
            ):
                raise lr.LatexRenderError("offset table row requires separate layout")
            row = []
            index = 0
            for tc in tr.findall("{" + W + "}tc"):
                if tc.find("{" + W + "}tcPr/{" + W + "}vMerge") is not None:
                    raise lr.LatexRenderError(
                        "vertical merge requires preservation before PDF use"
                    )
                if tc.find("{" + W + "}tcPr/{" + W + "}hMerge") is not None:
                    raise lr.LatexRenderError(
                        "legacy horizontal merge requires preservation before PDF use"
                    )
                sp = tc.find("{" + W + "}tcPr/{" + W + "}gridSpan")
                span = int(sp.get("{" + W + "}val", "1")) if sp is not None else 1
                if span < 1:
                    raise lr.LatexRenderError("invalid table span")
                width = tc.find("{" + W + "}tcPr/{" + W + "}tcW")
                units = (
                    float(width.get("{" + W + "}w", "0"))
                    if width is not None and width.get("{" + W + "}type") == "dxa"
                    else 0
                )
                fragments = [
                    run_segments(
                        p,
                        relationships,
                        data_root=data_root,
                        inline_max_height_mm=65,
                        table_cell=True,
                    ).strip()
                    for p in tc.findall("{" + W + "}p")
                ]
                body = r"\par ".join(f for f in fragments if f)
                row.append((index, index + span, units, body))
                index += span
                boundaries.add(index)
            if row:
                raw.append(row)
                totals.append(index)
        if not raw or len(set(totals)) != 1:
            raise lr.LatexRenderError("inconsistent table grid")
        edges = sorted(boundaries)
        count = len(edges) - 1
        weights = []
        for a, b in pairwise(edges):
            candidates = [
                (y - x, u * (b - a) / (y - x))
                for row in raw
                for x, y, u, _ in row
                if u > 0 and x <= a and y >= b
            ]
            weights.append(min(candidates)[1] if candidates else b - a)
        # Include both side padding and vertical rules INSIDE the available width.
        padding = 1.5
        rule = 0.4 * 25.4 / 72.27
        available = 174 - 2 * padding * count - rule * (count + 1)
        if available <= 0:
            raise lr.LatexRenderError("too many table columns")
        widths = [available * w / sum(weights) for w in weights]
        widths = self._allocate(
            raw, edges, widths, [0.0] * count, padding, rule, available
        )
        spec = "|" + "|".join(f"p{{{w:.3f}mm}}" for w in widths) + "|"
        rows = []
        for row in raw:
            cells = []
            for start, end, _units, body in row:
                i, j = edges.index(start), edges.index(end)
                span = j - i
                if span > 1:
                    width = (
                        sum(widths[i:j]) + 2 * padding * (span - 1) + rule * (span - 1)
                    )
                    body = rf"\multicolumn{{{span}}}{{|p{{{width:.3f}mm}}|}}{{{body}}}"
                cells.append(body)
            rows.append(" & ".join(cells) + r" \\ \hline")
        measure = (
            "\\setlength{\\tabcolsep}{1.5mm}\\renewcommand{\\arraystretch}{1.15}"
            + rf"\begin{{tabular}}{{{spec}}}\hline "
            + "\n".join(rows[:3])
            + r"\end{tabular}"
        )
        result = (
            "\n\\par{\\setlength{\\tabcolsep}{1.5mm}\\renewcommand{\\arraystretch}{1.15}\n"
            + rf"\begin{{longtable}}[l]{{{spec}}}"
            + "\n\\hline\n"
            + "\n".join(rows)
            + "\n\\end{longtable}}\n"
        )
        return result, measure

    def _merged_table(self, table, relationships, *, data_root):
        if table.find(".//{" + W + "}vMerge") is None:
            return self._simple_table(table, relationships, data_root=data_root)
        if table.find(".//{" + W + "}tbl") is not None:
            raise lr.LatexRenderError("nested table requires separate layout")
        raw = []
        edges = {0}
        totals = []
        merges = []
        active = {}
        for ri, tr in enumerate(table.findall("{" + W + "}tr")):
            if any(
                tr.find("{" + W + "}trPr/{" + W + "}" + k) is not None
                for k in ["gridBefore", "gridAfter"]
            ):
                raise lr.LatexRenderError("offset table row requires separate layout")
            row = []
            index = 0
            continued = {}
            for tc in tr.findall("{" + W + "}tc"):
                if tc.find("{" + W + "}tcPr/{" + W + "}hMerge") is not None:
                    raise lr.LatexRenderError(
                        "legacy horizontal merge requires preservation before PDF use"
                    )
                sp = tc.find("{" + W + "}tcPr/{" + W + "}gridSpan")
                span = int(sp.get("{" + W + "}val", "1")) if sp is not None else 1
                if span < 1:
                    raise lr.LatexRenderError("invalid table span")
                width = tc.find("{" + W + "}tcPr/{" + W + "}tcW")
                units = (
                    float(width.get("{" + W + "}w", "0"))
                    if width is not None and width.get("{" + W + "}type") == "dxa"
                    else 0
                )
                fragments = [
                    run_segments(
                        p,
                        relationships,
                        data_root=data_root,
                        inline_max_height_mm=65,
                        table_cell=True,
                    ).strip()
                    for p in tc.findall("{" + W + "}p")
                ]
                body = r"\par ".join(f for f in fragments if f)
                vm = tc.find("{" + W + "}tcPr/{" + W + "}vMerge")
                state = vm.get("{" + W + "}val", "continue") if vm is not None else None
                key = (index, index + span)
                pictures = [
                    asset_info(d, relationships, data_root)
                    for d in tc.iter()
                    if tag(d) == "drawing"
                ]
                cell = {
                    "a": index,
                    "b": index + span,
                    "units": units,
                    "body": body,
                    "merge": None,
                    "continuation": False,
                    "picture_widths": [p[1] for p in pictures],
                }
                if state == "restart":
                    merge = {
                        "start": ri,
                        "end": ri,
                        "a": index,
                        "b": index + span,
                        "body": body,
                    }
                    merges.append(merge)
                    cell["merge"] = merge
                    continued[key] = merge
                elif state == "continue":
                    if key not in active:
                        raise lr.LatexRenderError("orphan vertical merge continuation")
                    if body:
                        raise lr.LatexRenderError(
                            "vertical merge continuation contains content"
                        )
                    merge = active[key]
                    merge["end"] = ri
                    cell["continuation"] = True
                    continued[key] = merge
                elif state is not None:
                    raise lr.LatexRenderError("unknown vertical merge state")
                row.append(cell)
                index += span
                edges.add(index)
            if not row:
                raise lr.LatexRenderError("empty vertical merge table row")
            raw.append(row)
            totals.append(index)
            active = continued
        if not raw or len(set(totals)) != 1:
            raise lr.LatexRenderError("inconsistent table grid")
        edges = sorted(edges)
        count = len(edges) - 1
        weights = []
        for a, b in pairwise(edges):
            candidates = [
                (c["b"] - c["a"], c["units"] * (b - a) / (c["b"] - c["a"]))
                for row in raw
                for c in row
                if c["units"] > 0 and c["a"] <= a and c["b"] >= b
            ]
            weights.append(min(candidates)[1] if candidates else b - a)
        padding = 1.5
        rule = 0.4 * 25.4 / 72.27
        available = 174 - 2 * padding * count - rule * (count + 1)
        if available <= 0:
            raise lr.LatexRenderError("too many table columns")
        widths = [available * w / sum(weights) for w in weights]
        floors = [0.0] * count
        for row in raw:
            for c in row:
                i, j = edges.index(c["a"]), edges.index(c["b"])
                if j - i == 1 and c["picture_widths"]:
                    floors[i] = max(
                        floors[i],
                        min(max(c["picture_widths"]) + 1.0, available / count * 2),
                    )
        fixed = set()
        while True:
            tight = {
                i for i in range(count) if i not in fixed and widths[i] < floors[i]
            }
            if not tight:
                break
            fixed |= tight
            if sum(floors[i] for i in fixed) >= available:
                raise lr.LatexRenderError("table pictures cannot fit readable columns")
            free = [i for i in range(count) if i not in fixed]
            remainder = available - sum(floors[i] for i in fixed)
            for i in fixed:
                widths[i] = floors[i]
            for i in free:
                widths[i] = remainder * weights[i] / sum(weights[j] for j in free)
        widths = self._allocate(raw, edges, widths, floors, padding, rule, available)
        spec = "|" + "|".join(f"p{{{w:.3f}mm}}" for w in widths) + "|"
        # All boundaries touched by a vertical span must remain on the same page.
        forbidden = {ri for m in merges for ri in range(m["start"], m["end"])}
        identity = "T" + str(self.merge_count)
        self.merge_count += 1
        setup = []
        for ri in range(len(raw)):
            setup.append(rf"\LayoutRowInit{{{identity}-{ri}}}")
        for ri, row in enumerate(raw):
            for c in row:
                i, j = edges.index(c["a"]), edges.index(c["b"])
                n = j - i
                c["width"] = sum(widths[i:j]) + 2 * padding * (n - 1) + rule * (n - 1)
                if c["merge"] is not None:
                    m = c["merge"]
                    span = m["end"] - m["start"] + 1
                    for target in range(m["start"], m["end"] + 1):
                        setup.append(
                            rf"\LayoutRowMeasure{{{identity}-{target}}}{{{c['width']:.3f}mm}}{{{span}}}{{{c['body']}}}"
                        )
                elif not c["continuation"]:
                    setup.append(
                        rf"\LayoutRowMeasure{{{identity}-{ri}}}{{{c['width']:.3f}mm}}{{1}}{{{c['body']}}}"
                    )
        rows = []
        for ri, row in enumerate(raw):
            cells = []
            for ci, c in enumerate(row):
                i, j = edges.index(c["a"]), edges.index(c["b"])
                span = j - i
                body = "" if c["continuation"] else c["body"]
                if c["merge"] is not None:
                    m = c["merge"]
                    n = m["end"] - m["start"] + 1
                    keys = ",".join(
                        f"{identity}-{r}" for r in range(m["start"], m["end"] + 1)
                    )
                    body = rf"\LayoutMerged{{{keys}}}{{{c['width']:.3f}mm}}{{{body}}}"
                if ci == 0:
                    body = rf"\LayoutRowStrut{{{identity}-{ri}}}" + body
                if span > 1:
                    body = rf"\multicolumn{{{span}}}{{|p{{{c['width']:.3f}mm}}|}}{{{body}}}"
                cells.append(body)
            blocked = {
                col
                for m in merges
                if m["start"] <= ri < m["end"]
                for col in range(edges.index(m["a"]), edges.index(m["b"]))
            }
            if not blocked:
                line = r"\hline"
            else:
                stretches = []
                start = None
                for col in range(count + 1):
                    visible = col < count and col not in blocked
                    if visible and start is None:
                        start = col
                    elif not visible and start is not None:
                        stretches.append(rf"\cline{{{start + 1}-{col}}}")
                        start = None
                line = "".join(stretches)
            ending = r" \\* " if ri in forbidden else r" \\ "
            rows.append(" & ".join(cells) + ending + line)
        # Prefix measurement must contain any complete group that touches its rows.
        end = min(3, len(rows))
        while end < len(rows) and end - 1 in forbidden:
            end += 1
        init = "\n".join(setup) + "\n"
        style = r"\setlength{\tabcolsep}{1.5mm}\renewcommand{\arraystretch}{1.15}"
        measure = (
            style
            + init
            + rf"\begin{{tabular}}{{{spec}}}\hline "
            + "\n".join(rows[:end])
            + r"\end{tabular}"
        )
        result = (
            "\n"
            + r"\par{"
            + style
            + init
            + rf"\begin{{longtable}}[l]{{{spec}}}\hline "
            + "\n".join(rows)
            + r"\end{longtable}}"
            + "\n"
        )
        return result, measure

    def _allocate(self, raw, edges, widths, floors, padding, rule, available):
        if self.variant == 0:
            return widths
        scores = [0.0] * len(widths)
        for row in raw:
            for c in row:
                a, b, body = (
                    (c["a"], c["b"], c["body"])
                    if isinstance(c, dict)
                    else (c[0], c[1], c[3])
                )
                i, j = edges.index(a), edges.index(b)
                if j - i != 1:
                    continue
                text = re.sub(
                    r"\\[A-Za-z]+|\{[^{}]*\}|[^\u3400-\u9fffA-Za-z0-9]", "", body
                )
                scores[i] = max(scores[i], math.sqrt(len(text) + 1))
        target = [max(1.5, s) for s in scores]
        target = [available * s / sum(target) for s in target]
        if self.variant == 1:
            target = [(a + b) / 2 for a, b in zip(widths, target)]
        # Preserve baseline picture widths within the reallocated cells, including
        # spans. This first step changes columns, never scales table artwork down.
        fixed = set()
        lower = [max(9.0, f) for f in floors]
        while True:
            tight = {i for i, w in enumerate(target) if i not in fixed and w < lower[i]}
            if not tight:
                break
            fixed |= tight
            if sum(lower[i] for i in fixed) >= available:
                return widths
            free = [i for i in range(len(widths)) if i not in fixed]
            total = sum(target[i] for i in free)
            remain = available - sum(lower[i] for i in fixed)
            for i in free:
                target[i] *= remain / total
            for i in fixed:
                target[i] = lower[i]
        for row in raw:
            for c in row:
                a, b, body = (
                    (c["a"], c["b"], c["body"])
                    if isinstance(c, dict)
                    else (c[0], c[1], c[3])
                )
                i, j = edges.index(a), edges.index(b)
                cell_width = (
                    sum(target[i:j]) + 2 * padding * (j - i - 1) + rule * (j - i - 1)
                )
                for path in re.findall(
                    r"\\LayoutImage\{[^{}]+\}\{[^{}]+\}\{([^{}]+)\}", body
                ):
                    source_width = float(
                        re.search(
                            r"\\LayoutImage\{([0-9.]+)mm\}\{[^{}]+\}\{"
                            + re.escape(path)
                            + r"\}",
                            body,
                        ).group(1)
                    )
                    baseline_width = (
                        sum(widths[i:j])
                        + 2 * padding * (j - i - 1)
                        + rule * (j - i - 1)
                    )
                    if cell_width + 0.05 < min(source_width, baseline_width):
                        return widths
        return target

    def table_latex(self, table, relationships, *, data_root):
        key = str(self.table_count)
        self.table_count += 1
        choices = []
        for self.variant in range(3):
            choices.append(
                self._merged_table(table, relationships, data_root=data_root)
            )
        for candidate, (body, _) in enumerate(choices):
            probe = body.replace(r"\begin{longtable}[l]", r"\begin{tabular}").replace(
                r"\end{longtable}", r"\end{tabular}"
            )
            self.probes.append(
                r"\LayoutTableProbe{" + key + "}{" + str(candidate) + "}{" + probe + "}"
            )
        body, measure = choices[self.table_choices.get(key, 0)]
        columns = len(
            re.findall(
                r"p\{", body.split(r"\begin{longtable}[l]", 1)[1].split(r"\hline", 1)[0]
            )
        )
        # A natural-width spanning header can inflate longtable's last column
        # before its first width pass, especially with many narrow columns.
        cue = (
            rf"\endfirsthead\multicolumn{{{columns}}}{{l}}"
            rf"{{\makebox[0pt][l]{{\small 第 {self.number} 题 · 表格续页}}}}\\\hline\endhead "
        )
        return body.replace(r"\hline", r"\hline " + cue, 1), measure

    def question_entries(self, blocks, *, data_root, number=0):
        entries = []
        numbered = False
        for b in blocks:
            xml = str(b.get("xml") or "").strip()
            if not xml:
                if str(b.get("text") or "").strip():
                    raise lr.LatexRenderError("rich block has text but no frozen XML")
                continue
            el = ET.fromstring(xml)
            rels = b.get("image_relationships") or {}
            if not numbered and tag(el) == "p":
                for node in el.findall(".//{" + W + "}t"):
                    if node.text and node.text.strip():
                        node.text = lr._LEADING_NUMBER.sub("", node.text, count=1)
                        break
            if tag(el) == "tbl":
                body, measure = self.table_latex(el, rels, data_root=data_root)
                entries.append(("table", body, measure))
                continue
            if tag(el) != "p":
                raise lr.LatexRenderError("unsupported block")
            if any(tag(n) == "pict" for n in el.iter()):
                raise lr.LatexRenderError(
                    "old VML picture requires preservation before PDF use"
                )
            visible = "".join(n.text or "" for n in el.iter() if tag(n) == "t").strip()
            drawings = [n for n in el.iter() if tag(n) == "drawing"]
            if not visible and drawings:
                if (
                    entries
                    and entries[-1][0] == "option"
                    and re.fullmatch(r"[A-DＡ-Ｄ][.、．]\s*", entries[-1][1])
                ):
                    graphic = "".join(
                        r"\raisebox{-0.72\height}{"
                        + image_tex(asset_info(drawing, rels, data_root))
                        + "}"
                        for drawing in drawings
                    )
                    entries[-1] = ("option", entries[-1][1] + graphic, None)
                    continue
                for drawing in drawings:
                    entries.append(("image", "", asset_info(drawing, rels, data_root)))
                continue
            options = split_options(el, rels, data_root)
            if options:
                for option in options:
                    entries.append(("option", option, None))
                continue
            text = run_segments(
                el,
                rels,
                data_root=data_root,
                inline_max_height_mm=90 if drawings else 22,
            )
            if text.strip():
                first = not numbered
                if first:
                    text = lr._LEADING_NUMBER.sub("", text, count=1)
                    text = rf"\textbf{{{number}.}} " + text
                    numbered = True
                entries.append(
                    ("text", text + "\\par\n", {"plain": visible, "first": first})
                )
        if not numbered:
            entries.insert(0, ("text", rf"\textbf{{{number}.}}\par" + "\n", None))
        return entries

    def flow(self, blocks, *, data_root, number=0, answer_lines=0):
        self.number = number
        entries = self.question_entries(blocks, data_root=data_root, number=number)
        chunks = []
        pending = []
        i = 0
        space_used = False

        def heading(e):
            text = (e[2] or {}).get("plain", "")
            return bool(re.match(r"^(【|素材|任务)", text)) or (
                0 < len(text) <= 20 and not re.match(r"^[（(]\d+[)）]", text)
            )

        def emit(parts):
            j = 0
            while j < len(parts):
                body = parts[j][1]
                if (
                    heading(parts[j]) or (parts[j][2] or {}).get("first")
                ) and j + 1 < len(parts):
                    body += parts[j + 1][1]
                    j += 1
                chunks.append(self._guard(body))
                j += 1

        while i < len(entries):
            kind, body, info = entries[i]
            if kind == "text":
                pending.append(entries[i])
                i += 1
                continue
            if kind == "table":
                suffix = ""
                if i + 1 < len(entries) and entries[i + 1][0] == "text":
                    suffix = entries[i + 1][1]
                    if (
                        heading(entries[i + 1])
                        and i + 2 < len(entries)
                        and entries[i + 2][0] == "text"
                    ):
                        suffix += entries[i + 2][1]
                prefix = "".join(e[1] for e in pending)
                chunks.append(
                    r"\LayoutNeed{"
                    + prefix
                    + info
                    + r"\par "
                    + suffix
                    + "}\n"
                    + prefix
                    + body
                )
                pending = []
                i += 1
                continue
            if kind == "option":
                options = []
                while i < len(entries) and entries[i][0] == "option":
                    options.append(entries[i][1])
                    i += 1
                grid = (
                    r"\LayoutChoices" + "".join("{" + s + "}" for s in options)
                    if len(options) == 4
                    else "".join(s + "\\par\n" for s in options)
                )
                chunks.append(self._guard("".join(e[1] for e in pending) + grid))
                pending = []
                continue
            images = []
            while i < len(entries) and entries[i][0] == "image":
                images.append(entries[i][2])
                i += 1
            keep = (
                2
                if len(pending) > 1
                and (heading(pending[-2]) or (pending[-2][2] or {}).get("first"))
                else 1
            )
            if len(pending) > keep:
                emit(pending[:-keep])
                pending = pending[-keep:]
            prefix = "".join(e[1] for e in pending)
            group, used = self._block_images(
                prefix, images, number, answer_lines, i == len(entries)
            )
            chunks.append(group)
            space_used |= used
            pending = []
        if pending:
            emit(pending)
        if answer_lines > 0 and not space_used:
            chunks.append(
                rf"\LayoutPosition{{Q:{self.mode}-{number}:0:bodyend}}"
                + rf"\LayoutAnswerSpace{{{number}}}{{{answer_lines}}}"
            )
        result = "\n".join(chunks)
        prefix = rf"\textbf{{{number}.}}"
        # Measurement boxes repeat the prefix and are never shipped out. Mark every
        # occurrence so the actual printed prefix, rather than only a probe, writes
        # its final position. Unused variant boxes discard their delayed writes.
        result = result.replace(
            prefix, rf"\LayoutPosition{{Q:{self.mode}-{number}:0:start}}" + prefix
        )
        return result + rf"\LayoutPosition{{Q:{self.mode}-{number}:0:end}}"

    def _block_images(self, prefix, images, number, answer_lines, last):
        side = (
            len(images) == 1
            and bool(prefix)
            and images[0][3] < 2.5
            and images[0][1] <= 85
            and images[0][2] <= 110
        )
        if side:
            info = images[0]
            right = info[1]
            left = 174 - right - 6
            blank = (
                rf"\LayoutSideSpace{{{number}}}{{{answer_lines * 4.5:.3f}mm}}"
                if answer_lines > 0 and last
                else ""
            )
            group = (
                rf"\noindent\begin{{minipage}}[t]{{{left:.3f}mm}}\vspace{{0pt}}"
                + prefix
                + blank
                + r"\end{minipage}\hfill"
                + rf"\begin{{minipage}}[t]{{{right:.3f}mm}}\vspace{{0pt}}\centering "
                + image_tex(info)
                + r"\end{minipage}\par"
            )
        else:
            # Put protected large figures on separate rows if parallel columns
            # would make labels smaller. Never resample original raster data.
            each = (174 - 6 * (len(images) - 1)) / len(images)
            if len(images) > 1 and all(info[1] <= each for info in images):
                group = (
                    prefix
                    + r"\par\noindent"
                    + r"\hfill".join(
                        rf"\begin{{minipage}}[t]{{{each:.3f}mm}}\vspace{{0pt}}\centering "
                        + image_tex(info)
                        + r"\end{minipage}"
                        for info in images
                    )
                    + r"\par"
                )
            else:
                group = prefix + "".join(
                    r"\par\noindent\makebox[\linewidth][c]{"
                    + image_tex(info)
                    + r"}\par"
                    for info in images
                )
        return self._guard(group), side and answer_lines > 0 and last

    @staticmethod
    def _guard(body):
        return "\n" + r"\LayoutKeep{" + body + "}\n"


TABLE_PROBE_MACROS = r"""
\newwrite\LayoutMeasures
\immediate\openout\LayoutMeasures=\jobname.measures
\newsavebox{\LayoutProbeBox}
\newsavebox{\LayoutFitA}\newsavebox{\LayoutFitB}\newsavebox{\LayoutFitC}
\newdimen\LayoutRemaining
\newcommand{\LayoutTableProbe}[3]{\begingroup
  \setbox\LayoutProbeBox=\vbox{\hsize=\linewidth #3\par}
  \immediate\write\LayoutMeasures{T|#1|#2|\number\dimexpr\ht\LayoutProbeBox+\dp\LayoutProbeBox\relax}
  \endgroup}
"""


def table_choices(measurements: str) -> dict[str, int]:
    costs = {}
    for line in measurements.splitlines():
        kind, key, index, height = line.split("|")
        if kind == "T":
            costs.setdefault(key, {})[int(index)] = int(height)
    result = {}
    for key, variants in costs.items():
        best = min(variants, key=lambda index: (variants[index], index))
        result[key] = best if variants[0] - variants[best] > 65536 else 0
    return result


def continuation_footer(positions: str) -> str:
    ranges = {}
    answer_pages = {}
    for line in positions.splitlines():
        label, page, *_ = line.split("|")
        kind, key, _slot, edge = label.split(":")
        page = int(page)
        if kind == "Q":
            ranges.setdefault(key, {})[edge] = page
        if kind == "A":
            answer_pages.setdefault(int(key), set()).add(page)
    labels = {}
    for key, pair in ranges.items():
        if "start" not in pair or "end" not in pair:
            continue
        mode, number = key.split("-")
        number = int(number)
        for page in range(pair["start"] + 1, pair["end"] + 1):
            label = (
                f"第 {number} 题解析续页" if mode == "answer" else f"第 {number} 题续题"
            )
            if mode == "question" and page in answer_pages.get(number, set()):
                label = (
                    f"第 {number} 题续题及作答区"
                    if page <= pair.get("bodyend", pair["end"])
                    else f"第 {number} 题续答区"
                )
            labels.setdefault(page, []).append(label)
    return (
        r"\fancyfoot[L]{\parbox[b]{.43\textwidth}{\scriptsize "
        + "".join(
            rf"\ifnum\value{{page}}={page}\relax " + ("；".join(labels[page])) + r"\fi "
            for page in sorted(labels)
        )
        + "}}"
    )
