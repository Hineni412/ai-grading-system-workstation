from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize(value: object) -> str:
    return _NON_WORD.sub("", str(value or "")).casefold()


def fine_skill_match_score(
    target_skills: Iterable[object],
    candidate_tags: Iterable[object],
) -> float:
    targets = [
        normalized
        for value in target_skills
        if (normalized := _normalize(value))
    ]
    candidates = [
        normalized
        for value in candidate_tags
        if (normalized := _normalize(value))
    ]
    best = 0.0
    for target in targets:
        for candidate in candidates:
            if target == candidate:
                best = max(best, 1.0)
            elif min(len(target), len(candidate)) >= 2 and (
                target in candidate or candidate in target
            ):
                best = max(best, 0.8)
            else:
                ratio = SequenceMatcher(None, target, candidate).ratio()
                if ratio >= 0.72:
                    best = max(best, round(ratio, 4))
    return best


__all__ = ["fine_skill_match_score"]
