"""The adopted original B ranker for the teacher's similar-question list.

Fixed skill-aware rules plus BM25/RRF; independent of duplicate detection and
student practice eligibility. The experiment remains an independent oracle.
"""
from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from question_bank.recommendation.target_matching import part_facets, target_index
from question_bank.services.similarity_service import strip_leading_score
from question_bank.solution_evidence.knowledge_links import load_point_links
from question_bank.solution_evidence.part_assessments import load_profiles

WEIGHTS = {"skill": .42, "knowledge": .15, "method": .12, "model": .08,
           "special_type": .07, "thought": .04, "ability": .03, "demand": .09}
RRF_K = 20
RRF_RULE_WEIGHT = .7
RRF_WINDOW = 80
RULE_THRESHOLD = .25
IMAGE = re.compile(r"\[\[IMAGE:.*?\]\]", re.I | re.S)
OPERATIONS = {
    "proof": r"证明|求证|说明理由|推理依据",
    "calculation": r"计算|运算|求值|化简|解方程|解不等式",
    "parameter": r"参数|取值范围|取值条件",
    "graph": r"作图|画出|描点|绘制",
    "construction": r"辅助线|构造|作垂线|作平行线",
    "modelling": r"建立.*模型|列方程|列出方程|实际问题|函数关系式",
    "recognition": r"判断|判定|识别|辨别|定义|概念",
}


def clean_text(text: object) -> str:
    value = IMAGE.sub("", str(text or ""))
    value = re.sub(r"^\s*(?:第\s*)?\d+\s*[.、．]\s*", "", value)
    value = strip_leading_score(value)
    value = unicodedata.normalize("NFC", value).lower()
    value = value.translate(str.maketrans({"（": "(", "）": ")", "−": "-", "﹣": "-", "＝": "=", "＋": "+"}))
    return re.sub(r"\s+", "", value)


def grams(text: str) -> frozenset[str]:
    return frozenset(text[i:i + 3] for i in range(max(1, len(text) - 2))) if text else frozenset()


def tokens(text: str) -> list[str]:
    result = []
    for segment in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z]+|\d+(?:\.\d+)?|[+\-*/=<>≤≥≠×÷√^²³(){}]", clean_text(text)):
        if re.fullmatch(r"[\u3400-\u9fff]+", segment):
            if len(segment) == 1:
                result.append(segment)
            for width in (2, 3):
                result.extend(segment[i:i + width] for i in range(len(segment) - width + 1))
        else:
            result.append(segment)
    return result


def dice(a: frozenset[str], b: frozenset[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a or b else 0.0


def smooth_difficulty(a: float | None, b: float | None) -> float:
    return .55 + .45 * math.exp(-abs(a - b) / 2) if a is not None and b is not None else 1.0


@dataclass(frozen=True)
class SimilarityPart:
    skills: frozenset[str]
    topics: frozenset[str]
    mode: str
    operations: frozenset[str]


@dataclass(frozen=True)
class SimilarityQuestion:
    qid: int
    text: str
    difficulty: float | None
    tags: dict[str, frozenset[str]]
    parts: tuple[SimilarityPart, ...] = ()
    text_grams: frozenset[str] = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "text_grams", grams(clean_text(self.text)))


class SimilarQuestionIndex:
    """Reusable in-memory corpus. Requests do not mutate the prepared index."""

    def __init__(self, questions: list[SimilarityQuestion]):
        self.questions = questions
        self.positions = {q.qid: i for i, q in enumerate(questions)}
        self.idf = {}
        for dimension in ("skill", "method", "model", "special_type", "thought", "ability"):
            count = Counter(v for q in questions for v in q.tags.get(dimension, ()))
            self.idf[dimension] = {v: 1 + math.log((len(questions) + 1) / (df + 1)) for v, df in count.items()}
        self.weight_sums = {
            (q.qid, dim): sum(values.get(t, 1.0) for t in q.tags.get(dim, ()))
            for dim, values in self.idf.items() for q in questions
        }
        documents = [Counter(tokens(q.text)) for q in questions]
        lengths = np.array([sum(d.values()) for d in documents], dtype=float)
        avg = max(1., float(lengths.mean())) if documents else 1.
        postings = defaultdict(list)
        for i, document in enumerate(documents):
            for term, tf in document.items():
                postings[term].append((i, tf))
        self.postings = {}
        for term, rows in postings.items():
            pos = np.array([i for i, _ in rows], dtype=np.intp)
            tf = np.array([tf for _, tf in rows], dtype=float)
            idf = math.log(1 + (len(questions) - len(rows) + .5) / (len(rows) + .5))
            weights = idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * lengths[pos] / avg))
            self.postings[term] = pos, weights

    def weighted_dice(self, a: SimilarityQuestion, b: SimilarityQuestion, dimension: str) -> float:
        left, right = a.tags.get(dimension, frozenset()), b.tags.get(dimension, frozenset())
        total = self.weight_sums[a.qid, dimension] + self.weight_sums[b.qid, dimension]
        return 2 * sum(self.idf[dimension].get(t, 1) for t in left & right) / total if total else 0.0

    @staticmethod
    def demand_score(a: SimilarityQuestion, b: SimilarityQuestion) -> float:
        if not a.parts or not b.parts:
            return 0.0
        matrix = []
        for left in a.parts:
            row = []
            for right in b.parts:
                shared = left.skills & right.skills or left.topics & right.topics
                if not shared:
                    row.append(0.)
                    continue
                mode = 1. if left.mode and left.mode == right.mode else .35
                ops = dice(left.operations, right.operations)
                row.append(mode * (.5 + .5 * ops) if left.operations or right.operations else mode)
            matrix.append(row)
        return .5 * (statistics.mean(max(row) for row in matrix)
                     + statistics.mean(max(row[j] for row in matrix) for j in range(len(b.parts))))

    def components(self, a: SimilarityQuestion, b: SimilarityQuestion) -> dict[str, float]:
        scores = {dim: self.weighted_dice(a, b, dim) for dim in self.idf}
        scores["knowledge"] = max(
            dice(a.tags.get("topic", frozenset()), b.tags.get("topic", frozenset())),
            .5 * dice(a.tags.get("section", frozenset()), b.tags.get("section", frozenset())),
            .15 * dice(a.tags.get("chapter", frozenset()), b.tags.get("chapter", frozenset())),
        )
        scores["demand"] = self.demand_score(a, b)
        active = {"skill", "knowledge"}
        active.update(dim for dim in self.idf if a.tags.get(dim) or b.tags.get(dim))
        if a.parts and b.parts:
            active.add("demand")
        semantic = sum(WEIGHTS[d] * scores[d] for d in active) / sum(WEIGHTS[d] for d in active)
        wording = dice(a.text_grams, b.text_grams)
        scores["wording"] = wording
        scores["semantic"] = semantic
        scores["difficulty"] = smooth_difficulty(a.difficulty, b.difficulty)
        scores["final"] = max(.88 * semantic + .12 * wording, .55 * wording) * scores["difficulty"]
        return scores

    def bm25(self, target: SimilarityQuestion) -> dict[int, float]:
        score = np.zeros(len(self.questions))
        for token in set(tokens(target.text)):
            if token in self.postings:
                pos, weights = self.postings[token]
                score[pos] += weights
        return {q.qid: float(score[i]) for i, q in enumerate(self.questions) if q.qid != target.qid and score[i] > 0}

    @staticmethod
    def ordered(scores: dict[int, float], minimum: float = 0.) -> list[int]:
        return sorted((q for q in scores if scores[q] >= minimum), key=lambda q: (-scores[q], q))

    def rank(self, qid: int, limit: int = 6) -> list[tuple[int, float]]:
        target = self.questions[self.positions[qid]]
        rules = {q.qid: self.components(target, q)["final"] for q in self.questions if q.qid != qid}
        ordered = self.ordered(rules, RULE_THRESHOLD)
        lexical = self.ordered(self.bm25(target))[:RRF_WINDOW]
        allowed = set(ordered)
        fused = {q: RRF_RULE_WEIGHT / (RRF_K + i) for i, q in enumerate(ordered[:RRF_WINDOW], 1)}
        for i, q in enumerate(lexical, 1):
            if q in allowed:
                fused[q] = fused.get(q, 0) + (1 - RRF_RULE_WEIGHT) / (RRF_K + i)
        # Constant scaling preserves the frozen B ordering. This is a ranking
        # score in [0, 1], not a probability of two questions being identical.
        return [(q, min(1., fused[q] * (RRF_K + 1))) for q in self.ordered(fused)[:limit]]


def build_index(rows, public_tags, resolver, *, db_path, connection, data_root) -> SimilarQuestionIndex:
    """Project the same current, source-valid inputs used by original B."""
    ids = [int(row["id"]) for row in rows]
    index = target_index(resolver) if resolver is not None else {}
    profiles = load_profiles(db_path, ids, connection=connection, data_root=data_root) if resolver is not None else {}
    links = load_point_links(db_path, [p["evidence_version_id"] for p in profiles.values()],
                             resolver.release_id, connection=connection) if resolver is not None else {}
    questions = []
    for row in rows:
        qid = int(row["id"])
        tags = defaultdict(set)
        for tag in public_tags.get(qid, []):
            kind, value = tag["tag_type"], tag["tag_value"]
            if kind in {"knowledge_point", "skill"}:
                for identity in resolver.resolve(value) if resolver is not None else ():
                    key = identity.stable_key
                    tags["skill" if key.startswith("sk_") else "topic"].add(key)
                    anchor = index.get(key, {})
                    for dim in ("section", "chapter"):
                        if anchor.get(dim):
                            tags[dim].add(anchor[dim])
            elif kind != "prerequisite":
                tags[kind].add(value)
        profile = profiles.get(qid, {})
        parts = []
        if profile.get("available"):
            evidence = profile["evidence"]
            facets = part_facets(evidence, links.get(profile["evidence_version_id"], {}), resolver, index, list(tags["topic"]))
            for part, facet in zip(evidence.get("parts", []), facets):
                description = "；".join(str(point.get(name) or "") for point in part.get("evidence_points", [])
                                       for name in ("target", "observable_evidence", "justification"))
                parts.append(SimilarityPart(frozenset(facet["skill_keys"]), frozenset(facet["topic_keys"]),
                                            str(part.get("response_mode") or ""),
                                            frozenset(k for k, pattern in OPERATIONS.items() if re.search(pattern, description))))
        try:
            difficulty = float(row["difficulty"])
            if not math.isfinite(difficulty):
                difficulty = None
        except (TypeError, ValueError):
            difficulty = None
        questions.append(SimilarityQuestion(qid, row["question_text"] or "", difficulty,
                                            {k: frozenset(v) for k, v in tags.items()}, tuple(parts)))
    return SimilarQuestionIndex(questions)
