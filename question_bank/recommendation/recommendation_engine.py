from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

TEXT_CLEAN_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


@lru_cache(maxsize=65536)
def _normalized_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def text_similarity(left: object, right: object) -> float:
    return _normalized_similarity(normalize_question_text(left), normalize_question_text(right))


def normalize_question_text(value: object) -> str:
    return TEXT_CLEAN_PATTERN.sub("", _text(value)).casefold()


def _text(value: object) -> str:
    return str(value or "").strip()
