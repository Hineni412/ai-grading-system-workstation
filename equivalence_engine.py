from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any


_RELATION_RE = re.compile(r"^\s*([A-Za-z0-9_\u0370-\u03ff∠△]+)\s*=\s*([A-Za-z0-9_\u0370-\u03ff∠△]+)\s*$")
_FRACTION_RE = re.compile(r"^\s*([+-]?\d+)\s*/\s*([+-]?\d+)\s*$")
_DECIMAL_RE = re.compile(r"^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?\s*$")


def expand_equivalent_forms(answer: Any, *, max_forms: int = 16) -> list[str]:
    """Generate safe, common equivalent forms for short math answers."""
    text = _clean_answer(answer)
    if not text:
        return []

    forms: list[str] = [text]
    forms.extend(_numeric_equivalent_forms(text))
    forms.extend(_relation_equivalent_forms(text))
    forms.extend(_punctuation_variants(text))

    return _dedupe(forms)[:max_forms]


def merge_equivalent_forms(existing: Any, *answers: Any, max_forms: int = 24) -> list[str]:
    forms: list[str] = []
    forms.extend(_string_list(existing))
    for answer in answers:
        forms.extend(expand_equivalent_forms(answer, max_forms=max_forms))
    canonical = next((answer for answer in answers if _clean_answer(answer)), None)
    if canonical is not None:
        forms = filter_safe_equivalent_forms(canonical, forms)
    return _dedupe(forms)[:max_forms]


def filter_safe_equivalent_forms(canonical_answer: Any, forms: Any, *, max_forms: int = 32) -> list[str]:
    """Keep AI-proposed equivalents only when numeric values are actually equal."""
    canonical = _clean_answer(canonical_answer)
    values = _string_list(forms)
    if not canonical:
        return _dedupe(values)[:max_forms]

    canonical_number = _parse_number_like(canonical)
    result: list[str] = []
    for value in values:
        text = _clean_answer(value)
        if not text:
            continue
        candidate_number = _parse_number_like(text)
        if canonical_number is not None and candidate_number is not None and candidate_number != canonical_number:
            continue
        result.append(str(value).strip())
    return _dedupe(result)[:max_forms]


def _numeric_equivalent_forms(text: str) -> list[str]:
    fraction = _parse_number_like(text)
    if fraction is None:
        return []

    forms = [_format_fraction(fraction)]
    finite_decimal = _format_finite_decimal(fraction)
    if finite_decimal is not None:
        forms.append(finite_decimal)
        forms.append(_trim_leading_zero(finite_decimal))

    percent = _format_percent(fraction)
    if percent is not None and _should_emit_percent_variant(text, fraction):
        forms.append(percent)

    return forms


def _relation_equivalent_forms(text: str) -> list[str]:
    match = _RELATION_RE.match(text)
    if not match:
        return []

    left = match.group(1)
    right = match.group(2)
    forms = [
        f"{left}={right}",
        f"{right}={left}",
        f"{left} = {right}",
        f"{right} = {left}",
    ]

    left_reversed = _reverse_segment_name(left)
    right_reversed = _reverse_segment_name(right)
    if left_reversed or right_reversed:
        left_forms = [left]
        right_forms = [right]
        if left_reversed:
            left_forms.append(left_reversed)
        if right_reversed:
            right_forms.append(right_reversed)
        for lval in left_forms:
            for rval in right_forms:
                forms.append(f"{lval}={rval}")
                forms.append(f"{rval}={lval}")

    return forms


def _punctuation_variants(text: str) -> list[str]:
    variants: list[str] = []
    if "=" in text:
        variants.append(text.replace("=", "＝"))
    if "：" in text:
        variants.append(text.replace("：", ":"))
    if ":" in text:
        variants.append(text.replace(":", "："))
    return variants


def _parse_number_like(text: str) -> Fraction | None:
    normalized = text.strip().replace("％", "%")
    if not _DECIMAL_RE.match(normalized) and not _FRACTION_RE.match(normalized):
        return None

    try:
        fraction_match = _FRACTION_RE.match(normalized)
        if fraction_match:
            denominator = int(fraction_match.group(2))
            if denominator == 0:
                return None
            return Fraction(int(fraction_match.group(1)), denominator)

        if normalized.endswith("%"):
            return Fraction(Decimal(normalized[:-1].strip())) / 100
        return Fraction(Decimal(normalized))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return None


def _format_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _format_finite_decimal(value: Fraction) -> str | None:
    denominator = abs(value.denominator)
    while denominator % 2 == 0 and denominator > 1:
        denominator //= 2
    while denominator % 5 == 0 and denominator > 1:
        denominator //= 5
    if denominator != 1:
        return None

    decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    text = format(decimal_value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _format_percent(value: Fraction) -> str | None:
    percent_value = value * 100
    decimal = _format_finite_decimal(percent_value)
    if decimal is None:
        return None
    return f"{decimal}%"


def _should_emit_percent_variant(text: str, value: Fraction) -> bool:
    if "%" in text:
        return True
    compact = text.strip()
    if "/" in compact and abs(value) <= 1:
        return True
    if "." in compact and abs(value) <= 1:
        return True
    return False


def _trim_leading_zero(text: str) -> str:
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


def _reverse_segment_name(text: str) -> str | None:
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return text[::-1]
    return None


def _clean_answer(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.replace("；", ";").split(";") if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
