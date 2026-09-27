from __future__ import annotations

import re
from difflib import SequenceMatcher


TEXT_CLEAN_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


def text_similarity(left: object, right: object) -> float:
    normalized_left = normalize_question_text(left)
    normalized_right = normalize_question_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def normalize_question_text(value: object) -> str:
    return TEXT_CLEAN_PATTERN.sub("", _text(value)).casefold()


def _text(value: object) -> str:
    return str(value or "").strip()
