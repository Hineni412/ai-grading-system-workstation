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
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "Theta": "Θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "Lambda": "Λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "Pi": "Π",
    "rho": "ρ",
    "sigma": "σ",
    "Sigma": "Σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "Phi": "Φ",
    "chi": "χ",
    "psi": "ψ",
    "Psi": "Ψ",
    "omega": "ω",
    "Omega": "Ω",
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "in": "∈",
    "notin": "∉",
    "ni": "∋",
    "subset": "⊂",
    "supset": "⊃",
    "subseteq": "⊆",
    "supseteq": "⊇",
    "cap": "∩",
    "cup": "∪",
    "emptyset": "∅",
    "varnothing": "∅",
    "infty": "∞",
    "approx": "≈",
    "sim": "∼",
    "simeq": "≃",
    "cong": "≅",
    "equiv": "≡",
    "propto": "∝",
    "parallel": "∥",
    "nparallel": "∦",
    "perp": "⊥",
    "bot": "⊥",
    "triangle": "△",
    "angle": "∠",
    "measuredangle": "∡",
    "therefore": "∴",
    "because": "∵",
    "circ": "°",
    "wedge": "∧",
    "vee": "∨",
    "lnot": "¬",
    "neg": "¬",
    "forall": "∀",
    "exists": "∃",
    "nabla": "∇",
    "partial": "∂",
    "odot": "⊙",
    "oplus": "⊕",
    "otimes": "⊗",
    "rightarrow": "→",
    "leftarrow": "←",
    "to": "→",
    "gets": "←",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "mapsto": "↦",
    "ldots": "…",
    "cdots": "⋯",
    "dots": "…",
}
_OPERATOR_CHARS = set("+-*/=<>(),.[]|:×·÷±∓≤≥≠∈∉∋⊂⊃⊆⊇∩∪∅∞≈∼≃≅≡∝∥∦⊥∴∵∧∨¬∀∃⊙⊕⊗→←⇒⇐⇔↦…⋯")

# 纯文本包裹命令：内容按原样输出为直立体文本（不切分公式）。
_TEXT_GROUP_COMMANDS = {
    "text", "mathrm", "operatorname", "mbox", "textrm",
    "mathbf", "mathit", "mathnormal", "textbf", "textit", "boldsymbol",
}

# 反斜杠后跟单字符的转义/间距命令：\% \& \_ 等转义字符，\, \; \: \! 间距。
_ESCAPED_CHARS = {"%": "%", "&": "&", "#": "#", "_": "_", "$": "$",
                  "{": "{", "}": "}", ",": " ", ";": " ", ":": " ",
                  "!": "", " ": " ", "\\": "", "'": "′", "`": "‵",
                  "\"": "″"}
_SPACING_COMMANDS = {"quad": " ", "qquad": "  ", "enspace": " ",
                     "thinspace": " ", "hspace": " "}
_IGNORED_COMMANDS = {"displaystyle", "limits", "nolimits", "textstyle",
                     "scriptstyle", "mathstrut", "strut", "protect"}
# 吃掉一个 {...} 参数但不产出的命令（占位/间距尺寸）。
_GROUP_CONSUMING_EMPTY_COMMANDS = {"hspace", "vphantom", "hphantom", "phantom"}

# 上/下装饰命令 → OMML bar（上划线/下划线）或 accent（箭头、帽号）。
_BAR_COMMANDS = {"overline": "top", "underline": "bot", "underbar": "bot"}
_ACCENT_COMMANDS = {
    "vec": "⃗", "overrightarrow": "⃗", "overleftarrow": "⃖",
    "hat": "̂", "widehat": "̂", "dot": "̇",
    "ddot": "̈", "bar": "̄", "tilde": "̃", "widetilde": "̃",
}


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
        if token == "~":
            # LaTeX 中 ~ 是不间断空格。
            return _MathNode("text", value=" ")
        if token.startswith("\\"):
            command = token[1:]
            if command == "":
                # 裸反斜杠：转义字符或间距命令（\% \, \; \! 等）。
                if self.index >= len(self.tokens):
                    raise RestrictedMathError("dangling backslash")
                escaped = _ESCAPED_CHARS.get(self.tokens[self.index])
                if escaped is not None:
                    self.index += 1
                    return _MathNode("text", value=escaped)
                if re.fullmatch(r"[A-Za-z]+|[一-鿿]", self.tokens[self.index]):
                    # 形如 `20\ m`：tokenizer 吃掉了空格，按"转义空格+字母"处理。
                    return _MathNode("text", value=" ")
                raise RestrictedMathError("escaped token is not supported")
            if command in {"left", "right"}:
                if self.index >= len(self.tokens):
                    raise RestrictedMathError(f"\\{command} requires a delimiter")
                delimiter = self.tokens[self.index]
                self.index += 1
                if delimiter not in {"(", ")", "[", "]", "|", "{" , "}"}:
                    raise RestrictedMathError("delimiter is not supported")
                return _MathNode("operator", value=delimiter)
            if command == "frac" or command in {"dfrac", "tfrac"}:
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
            if command in _TEXT_GROUP_COMMANDS:
                return _MathNode("text", value=self._raw_group("text content"))
            if command in _SPACING_COMMANDS:
                value = _SPACING_COMMANDS[command]
                if command == "hspace":
                    # \hspace{1em}：吃掉尺寸参数，只留一个空格。
                    self._consume_group()
                return _MathNode("text", value=value)
            if command in _IGNORED_COMMANDS:
                return _MathNode("text", value="")
            if command in _GROUP_CONSUMING_EMPTY_COMMANDS:
                self._consume_group()
                return _MathNode("text", value="")
            if command in _BAR_COMMANDS:
                return _MathNode(
                    "bar",
                    value=_BAR_COMMANDS[command],
                    children=(self._required_group("barred content"),),
                )
            if command in _ACCENT_COMMANDS:
                return _MathNode(
                    "accent",
                    value=_ACCENT_COMMANDS[command],
                    children=(self._required_group("accented content"),),
                )
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
        if re.fullmatch(r"[一-鿿　-〿＀-￯]+", token):
            # 识别结果常把“厘米”“的”等中文包进 $…$，按直立体文本输出。
            return _MathNode("text", value=token)
        raise RestrictedMathError(f"token {token!r} is not supported")

    def _required_group(self, label: str) -> _MathNode:
        if self.index >= len(self.tokens) or self.tokens[self.index] != "{":
            raise RestrictedMathError(f"{label} must be braced")
        self.index += 1
        return self._sequence("}")

    def _raw_group(self, label: str) -> str:
        """消费 {…} 并把内部 token 原样拼回文本（供 \\text/\\mathrm 等使用）。"""
        if self.index >= len(self.tokens) or self.tokens[self.index] != "{":
            raise RestrictedMathError(f"{label} must be braced")
        self.index += 1
        parts: list[str] = []
        depth = 1
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            self.index += 1
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    return "".join(parts)
            parts.append(token)
        raise RestrictedMathError(f"{label} group is unclosed")

    def _consume_group(self) -> None:
        """吃掉一个可选的 {…} 参数，无括号时不动。"""
        if self.index >= len(self.tokens) or self.tokens[self.index] != "{":
            return
        depth = 0
        while self.index < len(self.tokens):
            token = self.tokens[self.index]
            self.index += 1
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    return
        raise RestrictedMathError("optional group is unclosed")

    def _script_value(self) -> _MathNode:
        if self.index >= len(self.tokens):
            raise RestrictedMathError("script value is missing")
        if self.tokens[self.index] == "{":
            self.index += 1
            return self._sequence("}")
        return self._atom()


# 可展开的行列式环境：拆掉环境壳，单元格按空格串联（不再保对齐结构）。
_MATRIX_ENV_BEGIN = re.compile(
    r"\\begin\{(?:array|matrix|pmatrix|bmatrix|vmatrix|cases|split|aligned|gathered)\}"
    r"(?:\s*\{[^{}]*\})?"
)
_MATRIX_ENV_END = re.compile(
    r"\\end\{(?:array|matrix|pmatrix|bmatrix|vmatrix|cases|split|aligned|gathered)\}"
)


def parse_restricted_latex(source: str) -> _MathNode:
    clean = str(source or "").strip()
    if clean.startswith("$$") and clean.endswith("$$"):
        clean = clean[2:-2].strip()
    elif clean.startswith("$") and clean.endswith("$"):
        clean = clean[1:-1].strip()
    # MinerU 偶发在公式内部混入多余的 $ 定界符（嵌套/空 $$ 段），剥掉再解析。
    clean = clean.replace("$", "").strip()
    if "\\begin" in clean:
        # MinerU 常用 array/cases 包裹简单内容（如 \begin{array}{rl}{BD=}&{6}），
        # 拆壳后交给普通解析，单元格分隔符 & 和换行 \\ 一律成空格。
        clean = _MATRIX_ENV_BEGIN.sub(" ", clean)
        clean = _MATRIX_ENV_END.sub(" ", clean)
        clean = clean.replace("\\\\", " ").replace("&", " ").strip()
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
    if node.kind == "text":
        return f"<mtext>{html.escape(node.value)}</mtext>"
    if node.kind == "bar":
        inner = _mathml(node.children[0])
        if node.value == "bot":
            return f'<munder accentunder="true">{inner}<mo>_</mo></munder>'
        return f'<mover accent="true">{inner}<mo>¯</mo></mover>'
    if node.kind == "accent":
        return (
            f'<mover accent="true">{_mathml(node.children[0])}'
            f"<mo>{html.escape(node.value)}</mo></mover>"
        )
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
    if node.kind == "text":
        # 直立体文本；保留空格。
        return (
            '<m:r><m:rPr><m:nor/></m:rPr>'
            f'<m:t xml:space="preserve">{html.escape(node.value)}</m:t></m:r>'
        )
    if node.kind == "bar":
        return (
            f'<m:bar><m:barPr><m:pos m:val="{node.value}"/></m:barPr>'
            f"<m:e>{_omml(node.children[0])}</m:e></m:bar>"
        )
    if node.kind == "accent":
        return (
            f'<m:acc><m:accPr><m:chr m:val="{html.escape(node.value)}"/>'
            f"</m:accPr><m:e>{_omml(node.children[0])}</m:e></m:acc>"
        )
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
