from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .contracts import MathExpression, ReviewState, SourceRegion


OMML_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_TOKEN = re.compile(
    r"\\[A-Za-z]+|[A-Za-z]+|[0-9]+(?:\.[0-9]+)?|[{}_^]|[+\-*/=<>(),.\[\]|:]|\S"
)
_SAFE_COMMANDS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "Delta": "Δ",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "phi": "φ",
    "omega": "ω",
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "pm": "±",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "in": "∈",
    "notin": "∉",
    "infty": "∞",
    "approx": "≈",
    "rightarrow": "→",
    "leftarrow": "←",
}
_OPERATOR_CHARS = set("+-*/=<>(),.[]|:×·÷±≤≥≠∈∉∞≈→←")


class RestrictedMathError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _MathNode:
    kind: str
    value: str = ""
    children: tuple["_MathNode", ...] = ()


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = _TOKEN.findall(source)
        self.index = 0
        joined = "".join(self.tokens)
        compact = re.sub(r"\s+", "", source)
        if joined != compact:
            raise RestrictedMathError("expression contains unsupported characters")

    def parse(self) -> _MathNode:
        if not self.tokens:
            raise RestrictedMathError("expression is empty")
        result = self._sequence(stop=None)
        if self.index != len(self.tokens):
            raise RestrictedMathError("expression was not fully consumed")
        return result

    def _sequence(self, stop: str | None) -> _MathNode:
        children: list[_MathNode] = []
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            if stop is not None and token == stop:
                self.index += 1
                return _MathNode("row", children=tuple(children))
            if token == "}":
                raise RestrictedMathError("unmatched closing brace")
            node = self._atom()
            subscript: _MathNode | None = None
            superscript: _MathNode | None = None
            while self.index < len(self.tokens) and self.tokens[self.index] in {"_", "^"}:
                marker = self.tokens[self.index]
                self.index += 1
                value = self._script_value()
                if marker == "_":
                    if subscript is not None:
                        raise RestrictedMathError("duplicate subscript")
                    subscript = value
                else:
                    if superscript is not None:
                        raise RestrictedMathError("duplicate superscript")
                    superscript = value
            if subscript is not None and superscript is not None:
                node = _MathNode("subsup", children=(node, subscript, superscript))
            elif subscript is not None:
                node = _MathNode("sub", children=(node, subscript))
            elif superscript is not None:
                node = _MathNode("sup", children=(node, superscript))
            children.append(node)
        if stop is not None:
            raise RestrictedMathError("unclosed group")
        return _MathNode("row", children=tuple(children))

    def _atom(self) -> _MathNode:
        token = self.tokens[self.index]
        self.index += 1
        if token == "{":
            return self._sequence("}")
        if token.startswith("\\"):
            command = token[1:]
            if command in {"left", "right"}:
                if self.index >= len(self.tokens):
                    raise RestrictedMathError(f"\\{command} requires a delimiter")
                delimiter = self.tokens[self.index]
                self.index += 1
                if delimiter not in {"(", ")", "[", "]", "|", "{" , "}"}:
                    raise RestrictedMathError("delimiter is not supported")
                return _MathNode("operator", value=delimiter)
            if command == "frac":
                return _MathNode(
                    "fraction",
                    children=(self._required_group("fraction numerator"), self._required_group("fraction denominator")),
                )
            if command == "sqrt":
                if self.index < len(self.tokens) and self.tokens[self.index] == "[":
                    raise RestrictedMathError("indexed roots are not supported yet")
                return _MathNode("sqrt", children=(self._required_group("radicand"),))
            if command in {"sum", "int"}:
                return _MathNode("operator", value="∑" if command == "sum" else "∫")
            value = _SAFE_COMMANDS.get(command)
            if value is None:
                raise RestrictedMathError(f"command \\{command} is not supported")
            return _MathNode("operator" if value in _OPERATOR_CHARS else "identifier", value=value)
        if token in {"_", "^"}:
            raise RestrictedMathError("script marker has no base")
        if token == "}":
            raise RestrictedMathError("unmatched closing brace")
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
            return _MathNode("number", value=token)
        if token in _OPERATOR_CHARS:
            return _MathNode("operator", value=token)
        if re.fullmatch(r"[A-Za-z]+", token):
            return _MathNode("identifier", value=token)
        raise RestrictedMathError(f"token {token!r} is not supported")

    def _required_group(self, label: str) -> _MathNode:
        if self.index >= len(self.tokens) or self.tokens[self.index] != "{":
            raise RestrictedMathError(f"{label} must be braced")
        self.index += 1
        return self._sequence("}")

    def _script_value(self) -> _MathNode:
        if self.index >= len(self.tokens):
            raise RestrictedMathError("script value is missing")
        if self.tokens[self.index] == "{":
            self.index += 1
            return self._sequence("}")
        return self._atom()


def parse_restricted_latex(source: str) -> _MathNode:
    clean = str(source or "").strip()
    if clean.startswith("$$") and clean.endswith("$$"):
        clean = clean[2:-2].strip()
    elif clean.startswith("$") and clean.endswith("$"):
        clean = clean[1:-1].strip()
    if len(clean) > 20_000:
        raise RestrictedMathError("expression is too long")
    if any(marker in clean for marker in ("\\begin", "\\end", "\\input", "\\include", "\\write", "\\def")):
        raise RestrictedMathError("executable or document-level LaTeX is forbidden")
    return _Parser(clean).parse()


def restricted_latex_to_mathml(source: str) -> str:
    return _mathml(parse_restricted_latex(source))


def restricted_latex_to_omml(source: str) -> str:
    body = _omml(parse_restricted_latex(source))
    return f'<m:oMath xmlns:m="{OMML_NAMESPACE}">{body}</m:oMath>'


def build_math_expression(
    *,
    expression_id: str,
    source: str,
    source_regions: tuple[SourceRegion, ...] = (),
    confidence: float = 1.0,
    review_state: ReviewState = ReviewState.TEACHER_VERIFIED,
    engine_version: str = "restricted-math-v1",
    fallback_asset: str | None = None,
    fallback_sha256: str | None = None,
) -> MathExpression:
    clean = str(source or "").strip()
    try:
        node = parse_restricted_latex(clean)
        return MathExpression(
            expression_id=expression_id,
            restricted_latex=clean,
            semantic_mathml=_mathml(node),
            omml=f'<m:oMath xmlns:m="{OMML_NAMESPACE}">{_omml(node)}</m:oMath>',
            source_regions=source_regions,
            review_state=review_state,
            confidence=confidence,
            engine_version=engine_version,
            fallback_asset=fallback_asset,
            fallback_sha256=fallback_sha256,
        )
    except RestrictedMathError as exc:
        return MathExpression(
            expression_id=expression_id,
            restricted_latex=clean,
            semantic_mathml="",
            omml="",
            source_regions=source_regions,
            review_state=ReviewState.CANDIDATE,
            confidence=confidence,
            engine_version=engine_version,
            fallback_asset=fallback_asset,
            fallback_sha256=fallback_sha256,
            validation_error=str(exc),
        )


def _mathml(node: _MathNode) -> str:
    if node.kind == "row":
        return f"<mrow>{''.join(_mathml(child) for child in node.children)}</mrow>"
    if node.kind == "number":
        return f"<mn>{html.escape(node.value)}</mn>"
    if node.kind == "identifier":
        return f"<mi>{html.escape(node.value)}</mi>"
    if node.kind == "operator":
        return f"<mo>{html.escape(node.value)}</mo>"
    if node.kind == "fraction":
        return f"<mfrac>{_mathml(node.children[0])}{_mathml(node.children[1])}</mfrac>"
    if node.kind == "sqrt":
        return f"<msqrt>{_mathml(node.children[0])}</msqrt>"
    if node.kind == "sup":
        return f"<msup>{_mathml(node.children[0])}{_mathml(node.children[1])}</msup>"
    if node.kind == "sub":
        return f"<msub>{_mathml(node.children[0])}{_mathml(node.children[1])}</msub>"
    if node.kind == "subsup":
        return (
            f"<msubsup>{_mathml(node.children[0])}{_mathml(node.children[1])}"
            f"{_mathml(node.children[2])}</msubsup>"
        )
    raise RestrictedMathError(f"unsupported semantic node {node.kind}")


def _omml(node: _MathNode) -> str:
    if node.kind == "row":
        return "".join(_omml(child) for child in node.children)
    if node.kind in {"number", "identifier", "operator"}:
        return f"<m:r><m:t>{html.escape(node.value)}</m:t></m:r>"
    if node.kind == "fraction":
        return (
            f"<m:f><m:num>{_omml(node.children[0])}</m:num>"
            f"<m:den>{_omml(node.children[1])}</m:den></m:f>"
        )
    if node.kind == "sqrt":
        return (
            '<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr>'
            f"<m:e>{_omml(node.children[0])}</m:e></m:rad>"
        )
    if node.kind == "sup":
        return (
            f"<m:sSup><m:e>{_omml(node.children[0])}</m:e>"
            f"<m:sup>{_omml(node.children[1])}</m:sup></m:sSup>"
        )
    if node.kind == "sub":
        return (
            f"<m:sSub><m:e>{_omml(node.children[0])}</m:e>"
            f"<m:sub>{_omml(node.children[1])}</m:sub></m:sSub>"
        )
    if node.kind == "subsup":
        return (
            f"<m:sSubSup><m:e>{_omml(node.children[0])}</m:e>"
            f"<m:sub>{_omml(node.children[1])}</m:sub>"
            f"<m:sup>{_omml(node.children[2])}</m:sup></m:sSubSup>"
        )
    raise RestrictedMathError(f"unsupported OMML node {node.kind}")


__all__ = [
    "OMML_NAMESPACE",
    "RestrictedMathError",
    "build_math_expression",
    "parse_restricted_latex",
    "restricted_latex_to_mathml",
    "restricted_latex_to_omml",
]
