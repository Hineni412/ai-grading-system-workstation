from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlsplit


_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
    }
)
_OMIT = object()
_FILE_URI_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])file:", re.IGNORECASE)
_WINDOWS_PATH_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
_UNC_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_:/\\])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+"
)
_ROOTED_WINDOWS_PATH_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])\\(?:users|windows|programdata|program files|documents and settings|system volume information)(?![A-Za-z0-9_])(?:[\\/][^\\/\s]+)*",
    re.IGNORECASE,
)
_ROOTED_WINDOWS_FILE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])\\[^\\/\s{}]+\.[A-Za-z0-9]{1,16}(?![A-Za-z0-9])"
)
_GENERIC_ROOTED_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])\\(?!\\)[^\\/\s{}$()\[\]^]+(?:[\\/][^\\/\s{}$()\[\]^]+)+"
)
_POSIX_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w/])/(?!/)(?:[^\s/\\]+/)+[^\s/\\]+")
_POSIX_ROOT_DIRECTORY_TOKEN_PATTERN = re.compile(
    r"(?<![\w/])/(?:etc|var|usr|home|tmp|opt|root|bin|sbin|dev|proc|sys|mnt|media|run)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_POSIX_ROOT_FILE_TOKEN_PATTERN = re.compile(
    r"(?<![\w/])/(?!/)[^\s/\\{}]+\.[A-Za-z0-9]{1,16}(?![A-Za-z0-9])"
)
_GENERIC_POSIX_ROOT_TOKEN_PATTERN = re.compile(
    r"(?<![\w/])/(?!/)[A-Za-z0-9._-]{2,}(?![\w/])"
)
_PUBLIC_API_PATH_PATTERN = re.compile(
    r"^/api(?:/[A-Za-z0-9._~!$&'()*+,;=@%\-]+)*/?$"
)
_LATEX_COMMAND_PATTERN = re.compile(r"\\([A-Za-z]+)")
_LATEX_COMMAND_SEQUENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:\\[A-Za-z]+){2,}(?![A-Za-z0-9._-])"
)
_LATEX_COMMANDS = frozenset(
    {
        "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
        "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
        "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau",
        "upsilon", "phi", "varphi", "chi", "psi", "omega", "sin", "cos", "tan",
        "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
        "tanh", "log", "ln", "exp", "max", "min", "lim", "sup", "inf", "gcd",
        "det", "mod", "deg", "arg", "frac", "dfrac", "tfrac", "sqrt", "sum",
        "prod", "int", "iint", "iiint", "oint", "cdot", "times", "div", "pm",
        "mp", "circ", "le", "leq", "ge", "geq", "neq", "approx", "sim", "equiv",
        "in", "notin", "subset", "subseteq", "supset", "supseteq", "ldots", "cdots",
        "vdots", "ddots", "overline", "underline", "hat", "widehat", "tilde",
        "widetilde", "vec", "dot", "ddot", "bar", "left", "right", "bigl", "bigr",
        "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr", "mathbf", "mathrm",
        "mathit", "mathsf", "mathtt", "mathcal", "mathbb", "mathfrak", "text", "textrm",
        "textbf", "textit", "operatorname", "quad", "qquad", "space", "displaystyle", "limits",
        "to", "infty", "partial", "nabla", "rightarrow", "leftarrow", "leftrightarrow",
        "Rightarrow", "Leftarrow", "Leftrightarrow", "mapsto", "longrightarrow",
        "longleftarrow", "longleftrightarrow", "forall", "exists", "neg", "land", "lor",
        "wedge", "vee", "cap", "cup", "emptyset", "re", "im", "angle", "triangle",
        "perp", "parallel", "propto", "therefore", "because", "dots",
    }
)


def canonical_public_key(value: object) -> str:
    return "".join(
        character
        for character in str(value or "").casefold()
        if character.isalnum()
    )


def is_sensitive_public_key(value: object) -> bool:
    key = canonical_public_key(value)
    return (
        key in _SENSITIVE_KEYS
        or key.endswith("apikey")
        or key.endswith("secret")
        or key.endswith("token")
    )


def is_path_public_key(value: object) -> bool:
    key = canonical_public_key(value)
    return key in {"path", "paths"} or key.endswith(("path", "paths"))


def contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_public_key(key) or contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_key(item) for item in value)
    return False


def sanitize_public_mapping(value: dict[Any, Any]) -> dict[str, Any]:
    sanitized = _sanitize_public_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_public_key(key) or is_path_public_key(key):
                continue
            sanitized_item = _sanitize_public_value(item)
            if sanitized_item is not _OMIT:
                result[str(key)] = sanitized_item
        return _OMIT if value and not result else result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            sanitized_item = _sanitize_public_value(item)
            if sanitized_item is not _OMIT:
                result.append(sanitized_item)
        return result
    if isinstance(value, str) and _contains_filesystem_token(value):
        return _OMIT
    return value


def _is_filesystem_location(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.casefold().startswith("file:"):
        return True
    if _is_public_api_path(text):
        return False
    windows_path = PureWindowsPath(text)
    if windows_path.drive:
        return True
    if windows_path.root and _is_rooted_windows_filesystem_location(text):
        return True
    return PurePosixPath(text).is_absolute()


def _is_rooted_windows_filesystem_location(value: str) -> bool:
    return bool(
        _ROOTED_WINDOWS_PATH_TOKEN_PATTERN.fullmatch(value)
        or _ROOTED_WINDOWS_FILE_TOKEN_PATTERN.fullmatch(value)
        or (
            _GENERIC_ROOTED_WINDOWS_PATH_PATTERN.fullmatch(value)
            and not _looks_like_latex(value)
        )
    )


def _contains_filesystem_token(
    value: str,
    *,
    _decode_rounds_remaining: int = 3,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _decode_rounds_remaining > 0:
        decoded = unquote(text)
        if decoded != text and _contains_filesystem_token(
            decoded,
            _decode_rounds_remaining=_decode_rounds_remaining - 1,
        ):
            return True
    if _is_public_api_url(text):
        parsed = urlsplit(text)
        return _contains_filesystem_token(
            parsed.query,
            _decode_rounds_remaining=_decode_rounds_remaining,
        ) or _contains_filesystem_token(
            parsed.fragment,
            _decode_rounds_remaining=_decode_rounds_remaining,
        )
    generic_rooted_windows_path = _GENERIC_ROOTED_WINDOWS_PATH_PATTERN.search(text)
    if (
        _is_filesystem_location(text)
        or _FILE_URI_TOKEN_PATTERN.search(text)
        or _WINDOWS_PATH_TOKEN_PATTERN.search(text)
        or _UNC_PATH_TOKEN_PATTERN.search(text)
        or _ROOTED_WINDOWS_PATH_TOKEN_PATTERN.search(text)
        or _ROOTED_WINDOWS_FILE_TOKEN_PATTERN.search(text)
        or _POSIX_ROOT_DIRECTORY_TOKEN_PATTERN.search(text)
        or _POSIX_ROOT_FILE_TOKEN_PATTERN.search(text)
        or _GENERIC_POSIX_ROOT_TOKEN_PATTERN.search(text)
        or (
            generic_rooted_windows_path
            and not _looks_like_latex(generic_rooted_windows_path.group(0))
        )
    ):
        return True
    return any(
        not _is_public_api_url(match.group(0))
        for match in _POSIX_PATH_TOKEN_PATTERN.finditer(text)
    )


def _looks_like_latex(value: str) -> bool:
    text = str(value or "").strip()
    if not _LATEX_COMMAND_SEQUENCE_PATTERN.fullmatch(text):
        return False
    commands = _LATEX_COMMAND_PATTERN.findall(text)
    return bool(commands) and all(
        command.casefold() in _LATEX_COMMANDS for command in commands
    )


def _is_public_api_url(value: str) -> bool:
    parsed = urlsplit(str(value or "").strip())
    return (
        not parsed.scheme
        and not parsed.netloc
        and _is_public_api_path(parsed.path)
    )


def _is_public_api_path(value: str) -> bool:
    return bool(_PUBLIC_API_PATH_PATTERN.fullmatch(str(value or "").strip()))
