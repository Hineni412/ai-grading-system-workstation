from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from question_bank.recommendation.scoring import parse_difficulty


TEXT_CLEAN_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


def assign_training_stage(candidate: Mapping[str, Any]) -> str:
    difficulty = parse_difficulty(candidate.get("difficulty"))
    if candidate.get("model_tags"):
        return "典型模型"
    if difficulty is not None and difficulty >= 8:
        return "压轴迁移"
    if difficulty is not None and difficulty <= 3:
        return "基础回补"
    if difficulty is not None and 4 <= difficulty <= 6 and candidate.get("method_tags"):
        return "方法形成"
    if difficulty is not None and 6 <= difficulty <= 8:
        return "综合提升"
    return "方法形成" if candidate.get("method_tags") else "基础回补"


def text_similarity(left: object, right: object) -> float:
    normalized_left = normalize_question_text(left)
    normalized_right = normalize_question_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def normalize_question_text(value: object) -> str:
    return TEXT_CLEAN_PATTERN.sub("", _text(value)).casefold()


def practice_gradient_fit(stage: str, difficulty: object) -> float | None:
    normalized = parse_difficulty(difficulty)
    if normalized is None:
        return None
    preferred = {
        "prerequisite": (1, 4),
        "direct": (4, 7),
        "transfer": (7, 10),
    }
    lower, upper = preferred.get(str(stage), (4, 7))
    if lower <= normalized <= upper:
        return 1.0
    if normalized in {lower - 1, upper + 1}:
        return 0.6
    return 0.2


def _text(value: object) -> str:
    return str(value or "").strip()
