"""Convert frozen Word OMML math into LaTeX (plus a linear fallback text).

The question bank stores, for every imported paragraph, both a linearized
plain-text form (fractions as ``(a)/(b)``, radicals as ``√(x)``) and the
original Word paragraph XML which still carries the native OMML math.  The
preview pipeline (:mod:`question_bank.services.preview_html`) uses this module
to turn each ``m:oMath`` element into LaTeX for KaTeX rendering, keeping the
linear text as an always-readable fallback.

Anything unrecognized degrades to its linear text so a formula is never lost.
"""

from __future__ import annotations

from xml.etree import ElementTree

_MAX_DEPTH = 32
_MAX_NODES = 2_000

_PROPERTY_NAMES = {
    "rPr", "ctrlPr", "sSupPr", "sSubPr", "sSubSupPr", "fPr", "radPr",
    "dPr", "naryPr", "limLowPr", "limUppPr", "mPr", "eqArrPr", "oMathParaPr",
    "pPr", "numPr", "denPr", "degPr", "ePr", "mrPr",
}

_LATEX_SYMBOLS = {
    "α": "\\alpha ", "β": "\\beta ", "γ": "\\gamma ", "δ": "\\delta ",
    "Δ": "\\Delta ", "θ": "\\theta ", "λ": "\\lambda ", "μ": "\\mu ",
    "π": "\\pi ", "σ": "\\sigma ", "φ": "\\phi ", "ω": "\\omega ",
    "×": "\\times ", "·": "\\cdot ", "÷": "\\div ", "±": "\\pm ",
    "≤": "\\leq ", "≥": "\\geq ", "≠": "\\neq ", "∈": "\\in ",
    "∉": "\\notin ", "∞": "\\infty ", "≈": "\\approx ",
    "→": "\\rightarrow ", "←": "\\leftarrow ",
}

_LATEX_ESCAPES = {"%": "\\%", "&": "\\&", "#": "\\#", "_": "\\_"}


def omml_parts(element: ElementTree.Element) -> tuple[str, str] | None:
    """Return ``(latex, plain_text)`` for an ``m:oMath``/``m:oMathPara`` element.

    ``plain_text`` mirrors the importer's linear form so callers can compare
    against stored text.  Returns ``None`` when the element has no renderable
    math content.
    """
    budget = [_MAX_NODES]
    tree = _omml_tree(element, 0, budget)
    if tree is None:
        return None
    latex = _tree_to_latex(tree)
    if not latex.strip():
        return None
    return latex, _tree_plain_text(tree)


def _local_name(tag: object) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _attribute_value(element: ElementTree.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return str(value)
    return None


# ---------------------------------------------------------------------------
# OMML → tree
# ---------------------------------------------------------------------------

def _omml_tree(
    element: ElementTree.Element | None,
    depth: int,
    budget: list[int],
) -> dict | None:
    if element is None or depth > _MAX_DEPTH:
        return None
    budget[0] -= 1
    if budget[0] <= 0:
        return None
    name = _local_name(element.tag)

    if name in _PROPERTY_NAMES:
        return None
    if name == "t":
        text = element.text or ""
        return {"kind": "text", "text": text} if text else None
    if name in ("oMath", "oMathPara", "e", "num", "den", "deg", "sub", "sup", "lim"):
        return _row([_omml_tree(child, depth + 1, budget) for child in element])
    if name == "r":
        return _row([_omml_tree(child, depth + 1, budget) for child in element])
    if name == "sSup":
        return _script(
            _omml_tree(_child(element, "e"), depth + 1, budget),
            None,
            _omml_tree(_child(element, "sup"), depth + 1, budget),
        )
    if name == "sSub":
        return _script(
            _omml_tree(_child(element, "e"), depth + 1, budget),
            _omml_tree(_child(element, "sub"), depth + 1, budget),
            None,
        )
    if name == "sSubSup":
        return _script(
            _omml_tree(_child(element, "e"), depth + 1, budget),
            _omml_tree(_child(element, "sub"), depth + 1, budget),
            _omml_tree(_child(element, "sup"), depth + 1, budget),
        )
    if name == "f":
        numerator = _omml_tree(_child(element, "num"), depth + 1, budget)
        denominator = _omml_tree(_child(element, "den"), depth + 1, budget)
        if numerator is None and denominator is None:
            return None
        return {
            "kind": "fraction",
            "numerator": numerator or _empty_row(),
            "denominator": denominator or _empty_row(),
        }
    if name == "rad":
        degree = _omml_tree(_child(element, "deg"), depth + 1, budget)
        radicand = _omml_tree(_child(element, "e"), depth + 1, budget)
        if radicand is None:
            return None
        if degree is not None and _tree_plain_text(degree).strip() in ("", "2", "²"):
            degree = None
        return {"kind": "radical", "degree": degree, "radicand": radicand}
    if name == "d":
        dpr = _child(element, "dPr")
        begin = _delimiter_character(dpr, "begChr", "(")
        end = _delimiter_character(dpr, "endChr", ")")
        separator = _delimiter_character(dpr, "sepChr", "")
        children = [
            part
            for part in (
                _omml_tree(child, depth + 1, budget)
                for child in _children(element, "e")
            )
            if part is not None
        ]
        if not children:
            return None
        return {
            "kind": "delimiter",
            "begin": begin,
            "end": end,
            "separator": separator,
            "children": children,
        }
    if name == "eqArr":
        rows = [
            part
            for part in (
                _omml_tree(child, depth + 1, budget)
                for child in _children(element, "e")
            )
            if part is not None
        ]
        if not rows:
            return None
        return {"kind": "eqarray", "children": rows}
    if name in ("limLow", "limUpp"):
        base = _omml_tree(_child(element, "e"), depth + 1, budget)
        limit = _omml_tree(_child(element, "lim"), depth + 1, budget)
        if name == "limLow":
            return _script(base, limit, None)
        return _script(base, None, limit)
    if name == "nary":
        symbol = "∑"
        npr = _child(element, "naryPr")
        if npr is not None:
            chr_element = _child(npr, "chr")
            if chr_element is not None:
                symbol = _attribute_value(chr_element, "val") or symbol
        sub = _omml_tree(_child(element, "sub"), depth + 1, budget)
        sup = _omml_tree(_child(element, "sup"), depth + 1, budget)
        body = _omml_tree(_child(element, "e"), depth + 1, budget)
        return {
            "kind": "nary",
            "symbol": symbol,
            "lower": sub,
            "upper": sup,
            "body": body or _empty_row(),
        }

    # Unknown structure: keep its linear text so nothing disappears.
    fallback_text = "".join(
        node.text or ""
        for node in element.iter()
        if _local_name(node.tag) == "t"
    )
    return {"kind": "text", "text": fallback_text} if fallback_text else None


def _empty_row() -> dict:
    return {"kind": "row", "children": []}


def _script(base: dict | None, sub: dict | None, sup: dict | None) -> dict | None:
    if base is None and sub is None and sup is None:
        return None
    if sub is None and sup is None:
        return base
    return {
        "kind": "script",
        "base": base or _empty_row(),
        "sub": sub,
        "sup": sup,
    }


def _row(parts: list[dict | None]) -> dict | None:
    children: list[dict] = []
    for part in parts:
        if part is None:
            continue
        if part.get("kind") == "row":
            children.extend(part.get("children") or [])
            continue
        if (
            part.get("kind") == "text"
            and children
            and children[-1].get("kind") == "text"
        ):
            children[-1]["text"] += part.get("text") or ""
            continue
        children.append(part)
    if not children:
        return None
    return {"kind": "row", "children": children}


def _delimiter_character(
    dpr: ElementTree.Element | None,
    name: str,
    default: str,
) -> str:
    if dpr is None:
        return default
    element = _child(dpr, name)
    if element is None:
        return default
    value = _attribute_value(element, "val")
    # An explicitly empty value (e.g. endChr="" on equation systems) is a
    # real "no delimiter" choice, not a missing one — mirror the importer.
    return default if value is None else value


# ---------------------------------------------------------------------------
# tree → plain text (mirrors docx_importer._math_text for consistency checks)
# ---------------------------------------------------------------------------

def _tree_plain_text(node: dict | None) -> str:
    if node is None:
        return ""
    kind = node.get("kind")
    if kind == "text":
        return str(node.get("text") or "")
    if kind in ("row", "eqarray"):
        return "".join(_tree_plain_text(child) for child in node.get("children") or [])
    if kind == "fraction":
        return f"({_tree_plain_text(node.get('numerator'))})/({_tree_plain_text(node.get('denominator'))})"
    if kind == "radical":
        degree = _tree_plain_text(node.get("degree"))
        return f"{degree}√({_tree_plain_text(node.get('radicand'))})"
    if kind == "script":
        return (
            _tree_plain_text(node.get("base"))
            + _tree_plain_text(node.get("sub"))
            + _tree_plain_text(node.get("sup"))
        )
    if kind == "delimiter":
        separator = str(node.get("separator") or "")
        inner = separator.join(
            _tree_plain_text(child) for child in node.get("children") or []
        )
        return f"{node.get('begin') or ''}{inner}{node.get('end') or ''}"
    if kind == "nary":
        # The importer's linear form drops the operator symbol itself; mirror
        # that so the consistency check compares like with like.
        return (
            _tree_plain_text(node.get("lower"))
            + _tree_plain_text(node.get("upper"))
            + _tree_plain_text(node.get("body"))
        )
    return ""


# ---------------------------------------------------------------------------
# tree → LaTeX
# ---------------------------------------------------------------------------

def _tree_to_latex(node: dict | None) -> str:
    if node is None:
        return ""
    kind = node.get("kind")
    if kind == "text":
        return _latex_escape(str(node.get("text") or ""))
    if kind == "row":
        return "".join(_tree_to_latex(child) for child in node.get("children") or [])
    if kind == "eqarray":
        rows = [
            _tree_to_latex(child) for child in node.get("children") or []
        ]
        return "\\begin{array}{l}" + " \\\\ ".join(rows) + "\\end{array}"
    if kind == "fraction":
        return (
            "\\frac{"
            + _tree_to_latex(node.get("numerator"))
            + "}{"
            + _tree_to_latex(node.get("denominator"))
            + "}"
        )
    if kind == "radical":
        radicand = _tree_to_latex(node.get("radicand"))
        degree = _tree_to_latex(node.get("degree"))
        if degree:
            return f"\\sqrt[{degree}]{{{radicand}}}"
        return f"\\sqrt{{{radicand}}}"
    if kind == "script":
        base = _braced(node.get("base"))
        parts = [base]
        sub = _tree_to_latex(node.get("sub"))
        sup = _tree_to_latex(node.get("sup"))
        if sub:
            parts.append(f"_{{{sub}}}")
        if sup:
            parts.append(f"^{{{sup}}}")
        return "".join(parts)
    if kind == "delimiter":
        begin = _latex_delimiter(str(node.get("begin") or ""))
        end = _latex_delimiter(str(node.get("end") or ""))
        separator = str(node.get("separator") or "")
        children = [
            _tree_to_latex(child) for child in node.get("children") or []
        ]
        inner = (
            f" {separator} ".join(children) if separator else " ".join(children)
        )
        return f"\\left{begin}{inner}\\right{end}"
    if kind == "nary":
        symbol = _LATEX_SYMBOLS.get(str(node.get("symbol") or ""), "")
        if not symbol:
            symbol = {"∑": "\\sum", "∫": "\\int"}.get(
                str(node.get("symbol") or ""), "\\sum"
            )
        parts = [symbol]
        lower = _tree_to_latex(node.get("lower"))
        upper = _tree_to_latex(node.get("upper"))
        if lower:
            parts.append(f"_{{{lower}}}")
        if upper:
            parts.append(f"^{{{upper}}}")
        body = _tree_to_latex(node.get("body"))
        if body:
            parts.append(f" {body}")
        return "".join(parts)
    return ""


def _braced(node: dict | None) -> str:
    latex = _tree_to_latex(node)
    if node is not None and node.get("kind") == "row":
        children = node.get("children") or []
        if len(children) != 1:
            return "{" + latex + "}"
    return latex


def _latex_delimiter(value: str) -> str:
    if not value:
        return "."
    mapping = {"{": "\\{", "}": "\\}", "|": "|"}
    return mapping.get(value, value)


def _latex_escape(text: str) -> str:
    parts: list[str] = []
    for char in text:
        if char in _LATEX_SYMBOLS:
            parts.append(_LATEX_SYMBOLS[char])
        elif char in _LATEX_ESCAPES:
            parts.append(_LATEX_ESCAPES[char])
        else:
            parts.append(char)
    return "".join(parts)


__all__ = ["omml_parts"]
