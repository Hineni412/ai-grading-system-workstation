from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:.+?\]\]")
PUNCTUATION_PATTERN = re.compile(r"[\s\u3000，。！？；：、,.!?;:（）()【】\[\]{}《》<>“”\"'`~·…—_\-]+")


LEADING_SCORE_PATTERN = re.compile(r"^\s*(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)+")


def strip_leading_score(text: str) -> str:
    """Leading score labels such as （3分） never identify a question."""
    return LEADING_SCORE_PATTERN.sub("", text, count=1)


@dataclass(frozen=True)
class SimilarQuestionGroup:
    question_ids: list[int]
    representative_id: int
    max_similarity: float
    pairs: list[tuple[int, int, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_ids": self.question_ids,
            "representative_id": self.representative_id,
            "max_similarity": self.max_similarity,
            "pairs": [list(item) for item in self.pairs],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SimilarQuestionGroup:
        return cls(
            question_ids=[int(item) for item in payload.get("question_ids", [])],
            representative_id=int(payload.get("representative_id")),
            max_similarity=float(payload.get("max_similarity") or 0),
            pairs=[(int(left), int(right), float(score)) for left, right, score in payload.get("pairs", [])],
        )


@dataclass(frozen=True)
class SimilarityPlan:
    upload_question_ids: list[int]
    high_duplicate_groups: list[SimilarQuestionGroup]
    review_groups: list[SimilarQuestionGroup]
    high_threshold: float
    review_threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "upload_question_ids": self.upload_question_ids,
            "high_duplicate_groups": [group.to_dict() for group in self.high_duplicate_groups],
            "review_groups": [group.to_dict() for group in self.review_groups],
            "high_threshold": self.high_threshold,
            "review_threshold": self.review_threshold,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SimilarityPlan:
        return cls(
            upload_question_ids=[int(item) for item in payload.get("upload_question_ids", [])],
            high_duplicate_groups=[SimilarQuestionGroup.from_dict(item) for item in payload.get("high_duplicate_groups", [])],
            review_groups=[SimilarQuestionGroup.from_dict(item) for item in payload.get("review_groups", [])],
            high_threshold=float(payload.get("high_threshold") or 0.9),
            review_threshold=float(payload.get("review_threshold") or 0.5),
        )


def build_ai_upload_similarity_plan(
    questions: Iterable[dict[str, Any]],
    *,
    high_threshold: float = 0.9,
    review_threshold: float = 0.7,
) -> SimilarityPlan:
    items = sorted(
        ((int(item["id"]), _normalize_question_text(item.get("question_text"))) for item in questions if item.get("id")),
        key=lambda item: item[0],
    )
    ids = [question_id for question_id, _ in items]
    text_by_id = dict(items)
    pair_scores: list[tuple[int, int, float]] = []
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            score = text_similarity(text_by_id[left_id], text_by_id[right_id])
            if score >= review_threshold:
                pair_scores.append((left_id, right_id, score))

    high_pairs = [pair for pair in pair_scores if pair[2] >= high_threshold]
    high_groups = _groups_from_pairs(ids, high_pairs)
    skipped_high_ids = {
        question_id
        for group in high_groups
        for question_id in group.question_ids
        if question_id != group.representative_id
    }
    remaining_ids = [question_id for question_id in ids if question_id not in skipped_high_ids]

    review_pairs = [
        pair
        for pair in pair_scores
        if review_threshold <= pair[2] < high_threshold
        and pair[0] in remaining_ids
        and pair[1] in remaining_ids
    ]
    review_groups = _groups_from_pairs(remaining_ids, review_pairs)
    return SimilarityPlan(
        upload_question_ids=remaining_ids,
        high_duplicate_groups=high_groups,
        review_groups=review_groups,
        high_threshold=high_threshold,
        review_threshold=review_threshold,
    )


def text_similarity(left: object, right: object) -> float:
    return profiled_text_similarity(
        question_text_profile(left),
        question_text_profile(right),
    )


@dataclass(frozen=True)
class QuestionTextProfile:
    normalized: str
    ngrams: frozenset[str]
    char_counts: Counter[str]


def question_text_profile(value: object) -> QuestionTextProfile:
    """一次性算出文本相似度需要的全部派生量，供批量比较复用。"""

    normalized = _normalize_question_text(value)
    return QuestionTextProfile(
        normalized=normalized,
        ngrams=frozenset(_char_ngrams(normalized)),
        char_counts=Counter(normalized),
    )


class WordingCandidateIndex:
    """Exact upper bounds in batches; never excludes a possible threshold hit.

    Character and ngram postings reuse the existing score's two bounds.
    There is no top-k cap or label filter: candidates keep their original
    position for deterministic ties, even when visited by strongest bound.
    """

    def __init__(self, profiles: list[QuestionTextProfile]) -> None:
        import numpy as np

        self.size = len(profiles)
        self.lengths = np.array([len(p.normalized) for p in profiles], dtype=np.int64)
        self.gram_sizes = np.array([len(p.ngrams) for p in profiles], dtype=np.int64)
        characters: dict[str, list[tuple[int, int]]] = {}
        grams: dict[str, list[int]] = {}
        for position, profile in enumerate(profiles):
            for char, count in profile.char_counts.items():
                characters.setdefault(char, []).append((position, count))
            for gram in profile.ngrams:
                grams.setdefault(gram, []).append(position)
        self.characters = {
            char: (np.array([p for p, _ in rows], dtype=np.intp),
                   np.array([n for _, n in rows], dtype=np.int64))
            for char, rows in characters.items()
        }
        self.grams = {gram: np.array(rows, dtype=np.intp) for gram, rows in grams.items()}

    def candidates(self, profile: QuestionTextProfile, threshold: float = 0.7) -> list[tuple[int, float]]:
        import numpy as np

        if not profile.normalized:
            return []
        shared = np.zeros(self.size, dtype=np.int64)
        for char, count in profile.char_counts.items():
            posting = self.characters.get(char)
            if posting is not None:
                positions, counts = posting
                shared[positions] += np.minimum(counts, count)
        bounds = 2.0 * shared / (self.lengths + len(profile.normalized))
        shared.fill(0)
        for gram in profile.ngrams:
            positions = self.grams.get(gram)
            if positions is not None:
                shared[positions] += 1
        denominator = self.gram_sizes + len(profile.ngrams) - shared
        jaccard = np.divide(shared, denominator, out=np.zeros(self.size), where=denominator > 0)
        bounds = np.maximum(bounds, jaccard)
        positions = np.flatnonzero((self.lengths > 0) & (bounds >= threshold - 0.00005))
        positions = positions[np.argsort(-bounds[positions], kind="stable")]
        return [(int(p), float(bounds[p])) for p in positions]


def profiled_text_similarity(
    left: QuestionTextProfile,
    right: QuestionTextProfile,
) -> float:
    if not left.normalized or not right.normalized:
        return 0.0
    if left.normalized == right.normalized:
        return 1.0
    sequence_score = SequenceMatcher(None, left.normalized, right.normalized).ratio()
    return round(max(sequence_score, _ngram_jaccard(left, right)), 4)


def wording_similarity_upper_bound(
    left: QuestionTextProfile,
    right: QuestionTextProfile,
) -> float:
    """不走 SequenceMatcher 的 wording 分上界，用于跳过不可能达标的候选。

    ngram Jaccard 本身就是 wording 分的一部分；SequenceMatcher 的匹配字符数
    又不可能超过两侧字符多重集的交集，二者取 max 即安全上界。
    """

    if not left.normalized or not right.normalized:
        return 0.0
    if left.normalized == right.normalized:
        return 1.0
    smaller, larger = (left.char_counts, right.char_counts) if len(left.char_counts) <= len(right.char_counts) else (right.char_counts, left.char_counts)
    shared = sum(min(count, larger.get(char, 0)) for char, count in smaller.items())
    sequence_bound = (2.0 * shared) / (len(left.normalized) + len(right.normalized))
    return max(sequence_bound, _ngram_jaccard(left, right))


def _ngram_jaccard(left: QuestionTextProfile, right: QuestionTextProfile) -> float:
    if left.ngrams and right.ngrams:
        shared = len(left.ngrams & right.ngrams)
        return shared / (len(left.ngrams) + len(right.ngrams) - shared)
    return 0.0


def _groups_from_pairs(candidate_ids: list[int], pairs: list[tuple[int, int, float]]) -> list[SimilarQuestionGroup]:
    if not pairs:
        return []
    parent = {question_id: question_id for question_id in candidate_ids}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left, right, _ in pairs:
        union(left, right)

    components: dict[int, list[int]] = {}
    for question_id in candidate_ids:
        components.setdefault(find(question_id), []).append(question_id)

    groups: list[SimilarQuestionGroup] = []
    for question_ids in components.values():
        if len(question_ids) < 2:
            continue
        group_pairs = [
            (left, right, score)
            for left, right, score in pairs
            if left in question_ids and right in question_ids
        ]
        groups.append(
            SimilarQuestionGroup(
                question_ids=sorted(question_ids),
                representative_id=min(question_ids),
                max_similarity=max((score for _, _, score in group_pairs), default=0.0),
                pairs=sorted(group_pairs, key=lambda item: item[2], reverse=True),
            )
        )
    return sorted(groups, key=lambda group: group.max_similarity, reverse=True)


def _normalize_question_text(value: object) -> str:
    text = IMAGE_MARKER_PATTERN.sub("", str(value or ""))
    text = strip_leading_score(text)
    text = re.sub(r"^\s*(?:第\s*)?\d+\s*(?:[\.、．]|题|\)|）)?", "", text)
    text = re.sub(r"^\s*[（(]\s*\d+\s*[)）]\s*", "", text)
    text = strip_leading_score(text)
    text = re.sub(r"\d{4}\s*[-—]\s*\d{4}\s*学年.*?(?:数学)?(?:试卷|真题)", "", text)
    return PUNCTUATION_PATTERN.sub("", text).lower()


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


__all__ = [
    "WordingCandidateIndex",
    "QuestionTextProfile",
    "SimilarQuestionGroup",
    "SimilarityPlan",
    "build_ai_upload_similarity_plan",
    "profiled_text_similarity",
    "question_text_profile",
    "strip_leading_score",
    "text_similarity",
    "wording_similarity_upper_bound",
]
