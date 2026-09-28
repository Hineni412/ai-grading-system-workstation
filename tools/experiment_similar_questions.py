"""Read-only comparison of the current ranker and two local experiments.

This is an experiment, not an application entry point. No model, migration,
database write, or production routing change is involved. Reuse the current
knowledge resolver, tag projection, evidence validity and direct-link readers.
Only aggregate measurements and anonymous assessor records may be exported.
The --review option displays source material transiently; do not redirect it
to a report/log. Parameters are fixed before assessing the held-out sample.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from question_bank.recommendation.target_matching import part_facets, target_index
from question_bank.services import question_read_service as reads
from question_bank.services.question_frequency_service import (
    calculate_question_similarity, canonical_knowledge_containment,
)
from question_bank.services.similarity_service import (
    question_text_profile, profiled_text_similarity, wording_similarity_upper_bound,
)
from question_bank.solution_evidence.part_assessments import load_profiles
from question_bank.solution_evidence.knowledge_links import load_point_links


SEED = 20260928
VERSION = "local-similarity-experiment-v1"
ROUTES = ("current", "skill_rules", "rules_bm25_rrf")
# Experimental priors, not calibrated probabilities or production defaults.
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
    """Preserve mathematical operators, parentheses and exponents."""
    value = IMAGE.sub("", str(text or ""))
    value = re.sub(r"^\s*(?:第\s*)?\d+\s*[.、．]\s*", "", value)
    value = re.sub(r"^\s*[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", value)
    value = unicodedata.normalize("NFC", value).lower()
    value = value.translate(str.maketrans({"（": "(", "）": ")", "−": "-", "﹣": "-", "＝": "=", "＋": "+"}))
    return re.sub(r"\s+", "", value)


def grams(text: str) -> frozenset[str]:
    return frozenset(text[i:i + 3] for i in range(max(1, len(text) - 2))) if text else frozenset()


def tokens(text: str) -> list[str]:
    # Chinese bigrams/trigrams avoid another segmentation dependency. Numbers
    # and mathematical operators remain tokens; no formula equivalence claim.
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
class Part:
    skills: frozenset[str]
    topics: frozenset[str]
    mode: str
    operations: frozenset[str]
    # Optional raw part context for subsequent experiments. V1 does not read
    # these fields; its scoring and text preprocessing stay frozen.
    label: str = ""
    description: str = ""


@dataclass
class Question:
    qid: int
    text: str
    answer: str = ""
    kind: str = ""
    difficulty: float | None = None
    updated: str = ""
    tags: dict[str, frozenset[str]] = field(default_factory=dict)
    parts: tuple[Part, ...] = ()
    description: str = ""
    baseline_tags: list[dict] = field(default_factory=list)
    text_grams: frozenset[str] = field(default_factory=frozenset)
    baseline_profile: object = None

    def __post_init__(self):
        self.text_grams = grams(clean_text(self.text))
        self.baseline_profile = question_text_profile(self.text)


class Experiment:
    def __init__(self, questions: list[Question]):
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
        # BM25 (k1=1.2, b=.75). Posting arrays are held only in process memory.
        documents = [Counter(tokens(q.text)) for q in questions]
        lengths = np.array([sum(d.values()) for d in documents], dtype=float)
        avg = max(1., float(lengths.mean()))
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
            self.postings[term] = (pos, weights)

    def weighted_dice(self, a: Question, b: Question, dimension: str) -> float:
        left, right = a.tags.get(dimension, frozenset()), b.tags.get(dimension, frozenset())
        total = self.weight_sums[a.qid, dimension] + self.weight_sums[b.qid, dimension]
        return 2 * sum(self.idf[dimension].get(t, 1) for t in left & right) / total if total else 0.0

    @staticmethod
    def demand_score(a: Question, b: Question) -> float:
        if not a.parts or not b.parts:
            return 0.0
        matrix = []
        for left in a.parts:
            row = []
            for right in b.parts:
                # Do not pool targets from different small parts into a fake
                # joint match. Supporting/prerequisite links never enter Part.
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

    def components(self, a: Question, b: Question) -> dict[str, float]:
        scores = {dim: self.weighted_dice(a, b, dim) for dim in self.idf}
        scores["knowledge"] = max(
            dice(a.tags.get("topic", frozenset()), b.tags.get("topic", frozenset())),
            .5 * dice(a.tags.get("section", frozenset()), b.tags.get("section", frozenset())),
            .15 * dice(a.tags.get("chapter", frozenset()), b.tags.get("chapter", frozenset())),
        )
        scores["demand"] = self.demand_score(a, b)
        # Core dimensions always remain in the denominator. Inapplicable
        # auxiliary labels do not make simple exercises systematically worse.
        active = {"skill", "knowledge"}
        active.update(dim for dim in self.idf if a.tags.get(dim) or b.tags.get(dim))
        if a.parts and b.parts:
            active.add("demand")
        semantic = sum(WEIGHTS[d] * scores[d] for d in active) / sum(WEIGHTS[d] for d in active)
        wording = dice(a.text_grams, b.text_grams)
        scores["wording"] = wording
        scores["semantic"] = semantic
        scores["difficulty"] = smooth_difficulty(a.difficulty, b.difficulty)
        # Monotonic fallback: extra semantic evidence cannot lower a text-only
        # result. All branches share the same continuous difficulty factor.
        scores["final"] = max(.88 * semantic + .12 * wording, .55 * wording) * scores["difficulty"]
        return scores

    def rule_scores(self, target: Question) -> dict[int, float]:
        return {q.qid: self.components(target, q)["final"] for q in self.questions if q.qid != target.qid}

    def bm25(self, target: Question) -> dict[int, float]:
        score = np.zeros(len(self.questions))
        for token in set(tokens(target.text)):
            if token in self.postings:
                pos, weights = self.postings[token]
                score[pos] += weights
        return {q.qid: float(score[i]) for i, q in enumerate(self.questions) if q.qid != target.qid and score[i] > 0}

    @staticmethod
    def ordered(scores: dict[int, float], minimum: float = 0.) -> list[int]:
        return sorted((q for q in scores if scores[q] >= minimum), key=lambda q: (-scores[q], q))

    def rank(self, qid: int, route: str, limit: int = 6) -> list[int]:
        target = self.questions[self.positions[qid]]
        if route == "current":
            return self.current(target, limit)
        rules = self.rule_scores(target)
        ordered = self.ordered(rules, RULE_THRESHOLD)
        if route == "skill_rules":
            return ordered[:limit]
        if route != "rules_bm25_rrf":
            raise ValueError(route)
        lexical = self.ordered(self.bm25(target))[:RRF_WINDOW]
        # A text hit alone is not sufficient evidence. Use the same rule
        # eligibility as route B, then investigate whether lexical ranking adds
        # value. This is the controlled incremental comparison requested.
        allowed = set(ordered)
        fused = {q: RRF_RULE_WEIGHT / (RRF_K + i) for i, q in enumerate(ordered[:RRF_WINDOW], 1)}
        for i, q in enumerate(lexical, 1):
            if q in allowed:
                fused[q] = fused.get(q, 0) + (1 - RRF_RULE_WEIGHT) / (RRF_K + i)
        return self.ordered(fused)[:limit]

    def current(self, target: Question, limit: int) -> list[int]:
        scored = []
        for candidate in self.questions:
            if candidate.qid == target.qid:
                continue
            score = calculate_question_similarity(
                {"difficulty": target.difficulty, "tags": target.baseline_tags},
                {"difficulty": candidate.difficulty, "tags": candidate.baseline_tags},
                knowledge_overlap=canonical_knowledge_containment,
            )
            needed = (.35 - score * .8) / .2 if score > 0 else .7
            if needed > 1:
                continue
            if needed > 0 and wording_similarity_upper_bound(target.baseline_profile, candidate.baseline_profile) < needed - .001:
                continue
            wording = profiled_text_similarity(target.baseline_profile, candidate.baseline_profile)
            combined = reads._combined_similarity_score(score, wording)
            if combined >= .35:
                scored.append((combined, candidate.updated, candidate.qid))
        return [row[2] for row in sorted(scored, reverse=True)[:limit]]


@contextmanager
def readonly_connect(db_path):
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.close()


def load_questions(db_path: Path) -> tuple[list[Question], dict]:
    service = reads.QuestionBankReadService(db_path, data_root=db_path.parent.parent)
    with reads._read_connection(db_path) as conn:
        resolver = service.current_knowledge
        if resolver is None:
            raise RuntimeError("Active knowledge standard unavailable")
        rows = conn.execute("""SELECT q.* FROM questions q LEFT JOIN papers p ON p.id=q.paper_id
            WHERE COALESCE(q.is_deleted,0)=0 AND COALESCE(p.import_status,'')<>'deleted' ORDER BY q.id""").fetchall()
        ids = [int(r["id"]) for r in rows]
        public = reads._load_page_tags(conn, ids, current_knowledge=resolver)
        knowledge = reads._current_knowledge_key_tags(conn, ids, resolver)
        profiles = load_profiles(db_path, ids, connection=conn, data_root=db_path.parent.parent)
        links = load_point_links(db_path, [p["evidence_version_id"] for p in profiles.values()], resolver.release_id, connection=conn)
        index = target_index(resolver)
        questions = []
        valid_profiles = 0
        for row in rows:
            qid = int(row["id"])
            tags = defaultdict(set)
            for tag in public.get(qid, []):
                kind, value = tag["tag_type"], tag["tag_value"]
                if kind in {"knowledge_point", "skill"}:
                    for identity in resolver.resolve(value):
                        key = identity.stable_key
                        tags["skill" if key.startswith("sk_") else "topic"].add(key)
                        anchor = index.get(key, {})
                        for dim in ("section", "chapter"):
                            if anchor.get(dim):
                                tags[dim].add(anchor[dim])
                elif kind != "prerequisite":
                    tags[kind].add(value)
            profile = profiles.get(qid, {})
            parts, descriptions = [], []
            if profile.get("available"):
                valid_profiles += 1
                evidence = profile["evidence"]
                facets = part_facets(evidence, links.get(profile["evidence_version_id"], {}), resolver, index, list(tags["topic"]))
                for part, facet in zip(evidence.get("parts", []), facets):
                    description = "；".join(str(point.get(field) or "") for point in part.get("evidence_points", [])
                                           for field in ("target", "observable_evidence", "justification"))
                    descriptions.append(description)
                    parts.append(Part(frozenset(facet["skill_keys"]), frozenset(facet["topic_keys"]),
                                      str(part.get("response_mode") or ""),
                                      frozenset(k for k, pattern in OPERATIONS.items() if re.search(pattern, description)),
                                      str(part.get("label") or ""), description))
                # Valid current direct links refine the per-part requirement;
                # canonical question tags remain the whole-question skill set.
            questions.append(Question(qid, row["question_text"] or "", row["answer_text"] or "", row["question_type"] or "",
                                      reads._numeric_difficulty(row["difficulty"]), str(row["updated_at"] or ""),
                                      {k: frozenset(v) for k, v in tags.items()}, tuple(parts), "；".join(descriptions),
                                      [t for t in public.get(qid, []) if t["tag_type"] in {"method", "model"}] + knowledge.get(qid, [])))
        return questions, {"active_questions": len(questions), "source_valid_part_profiles": valid_profiles,
                           "release": resolver.release_id,
                           "type_counts": dict(Counter(q.kind for q in questions))}


def sample_questions(questions: list[Question], size: int = 30) -> list[Question]:
    rng = random.Random(SEED)
    groups = defaultdict(list)
    for q in questions:
        band = "unknown" if q.difficulty is None else "low" if q.difficulty < 4 else "middle" if q.difficulty < 7 else "high"
        groups[q.kind, band].append(q)
    for rows in groups.values():
        rng.shuffle(rows)
    chosen = []
    while len(chosen) < min(size, len(questions)):
        for key in sorted(groups):
            if groups[key] and len(chosen) < size:
                chosen.append(groups[key].pop())
    return chosen


def candidate_pool(rankings: dict[str, list[int]], ordinal: int) -> list[int]:
    pool = sorted({qid for values in rankings.values() for qid in values})
    random.Random(SEED + ordinal).shuffle(pool)
    return pool


def preview(q: Question, expanded: bool = False) -> dict:
    def compact(text: str, limit: int):
        text = IMAGE.sub("[题图]", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] + ("…" if len(text) > limit else "")
    answer = q.answer
    for marker in ("【解答】", "【解析】", "【分析】"):
        if marker in answer:
            answer = answer[answer.index(marker):]
            break
    return {"题面": compact(q.text, 3000 if expanded else 650), "答案解析": compact(answer, 4000 if expanded else 650),
            "小问作答依据": compact(q.description, 2000 if expanded else 420), "难度": q.difficulty}


def summary_metrics(samples, all_rankings, ratings: dict, routes=ROUTES) -> dict:
    """Assessor labels: 0 unrelated, 1 partial/background, 2 useful practice,
    3 close substitute; null means source insufficient. No tag-derived gold.
    nDCG ideal is the pooled candidates only, not a whole-bank relevance truth.
    """
    aggregate = {}
    for route in routes:
        sums, ndcg, unknown, present, per_query, query_results = [], [], 0, 0, [], []
        for ordinal, q in enumerate(samples):
            key = f"Q{ordinal + 1:02}"
            if key not in ratings:
                continue
            rankings = all_rankings[q.qid]
            pool = candidate_pool(rankings, ordinal)
            grades = ratings[key]["grades"]
            if len(grades) != len(pool):
                raise ValueError(f"{key}: assessor labels do not match pooled candidates")
            if any(g is not None and (type(g) is not int or g not in range(4)) for g in grades):
                raise ValueError(f"{key}: grades must be integers 0..3 or null")
            by_qid = dict(zip(pool, grades))
            selected = [by_qid[qid] for qid in rankings[route]]
            known = [g for g in selected if g is not None]
            unknown += len(selected) - len(known)
            present += len(selected)
            sums.extend(known)
            # Fixed denominator 6: a shorter list does not inflate success.
            per_query.append(sum(g is not None and g >= 2 for g in selected) / 6)
            query_results.append({"sample": key, "grades_in_rank_order": selected,
                                  "useful_count": sum(g is not None and g >= 2 for g in selected),
                                  "close_substitute_count": sum(g == 3 for g in selected)})
            if all(g is not None for g in grades):
                dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(selected))
                ideal = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(grades, reverse=True)[:6]))
                if ideal:
                    ndcg.append(dcg / ideal)
        aggregate[route] = {"assessed_queries": len(per_query), "returned_candidates": present,
                            "unassessable_candidates": unknown,
                            "useful_candidates": sum(g >= 2 for g in sums),
                            "close_substitute_candidates": sum(g == 3 for g in sums),
                            "useful_among_assessable": round(sum(g >= 2 for g in sums) / len(sums), 4) if sums else None,
                            "useful_per_six_lower_bound": round(statistics.mean(per_query), 4) if per_query else None,
                            "mean_grade": round(statistics.mean(sums), 4) if sums else None,
                            "pooled_ndcg_at_6": round(statistics.mean(ndcg), 4) if ndcg else None,
                            "ndcg_queries": len(ndcg), "per_query": query_results}
    return aggregate


def paired_comparisons(assessment: dict, pairs=None) -> dict:
    """Compare queries, not 180 falsely independent candidate slots."""
    comparisons = {}
    for left, right in (pairs if pairs is not None else (
            ("skill_rules", "current"), ("rules_bm25_rrf", "current"),
            ("rules_bm25_rrf", "skill_rules"))):
        reference = {row["sample"]: row["useful_count"] for row in assessment[right]["per_query"]}
        deltas = [row["useful_count"] - reference[row["sample"]]
                  for row in assessment[left]["per_query"] if row["sample"] in reference]
        if not deltas:
            continue
        rng = np.random.default_rng(SEED)
        bootstrap = np.mean(rng.choice(deltas, size=(4000, len(deltas)), replace=True), axis=1) * 100 / 6
        comparisons[f"{left}_vs_{right}"] = {
            "improved_queries": sum(d > 0 for d in deltas),
            "tied_queries": sum(d == 0 for d in deltas),
            "worse_queries": sum(d < 0 for d in deltas),
            "useful_per_six_delta_percentage_points": round(statistics.mean(deltas) * 100 / 6, 2),
            "paired_query_bootstrap_95_percentile_interval_pp": [round(float(v), 2) for v in np.percentile(bootstrap, [2.5, 97.5])],
        }
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("user_data/databases/question_bank.db"))
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--review", type=int, help="Zero-based batch of five blind review queries; displays private content, never redirect")
    parser.add_argument("--detail", help="Expand one pooled candidate, e.g. Q01:3; stdout only")
    parser.add_argument("--interactive-review", action="store_true", help="Reuse in-memory data: enter Q01 or Q01:3, then done; never redirect output")
    parser.add_argument("--ratings", type=Path)
    parser.add_argument("--output", type=Path, help="New aggregate-only JSON file; refuses to overwrite")
    parser.add_argument("--verify-baseline", action="store_true", help="Compare replay with the actual existing service for every sample")
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 100:
        parser.error("sample-size must be 1..100")
    if args.review is not None and args.output:
        parser.error("review output must not be saved")
    db = args.db.resolve()
    # The release repository's read helper otherwise opens a generic write-
    # capable connection. Borrow the same read-only boundary for this process.
    with patch("question_bank.knowledge_graph_release.repository.connect", readonly_connect):
        generation = reads._source_generation_token(db)
        start = time.perf_counter()
        questions, metadata = load_questions(db)
        read_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        experiment = Experiment(questions)
        build_ms = (time.perf_counter() - start) * 1000
        samples = sample_questions(questions, args.sample_size)
        all_rankings, timings = {}, defaultdict(list)
        for ordinal, question in enumerate(samples):
            rankings = {}
            route_order = list(ROUTES)
            random.Random(SEED + ordinal).shuffle(route_order)
            for route in route_order:
                start = time.perf_counter()
                rankings[route] = experiment.rank(question.qid, route)
                timings[route].append((time.perf_counter() - start) * 1000)
            all_rankings[question.qid] = rankings
        verified = 0
        actual_times = []
        if args.verify_baseline:
            service = reads.QuestionBankReadService(db, data_root=db.parent.parent)
            for q in samples:
                start = time.perf_counter()
                actual = service._find_similar_questions(q.qid, limit=6)
                actual_times.append((time.perf_counter() - start) * 1000)
                if [r["id"] for r in actual or []] != all_rankings[q.qid]["current"]:
                    raise AssertionError("Current replay differs from application service")
                verified += 1
        if generation != reads._source_generation_token(db):
            raise RuntimeError("Question-bank generation changed during comparison; rerun")
        signature = {"database_generation": generation, "taxonomy_generation": reads._taxonomy_generation_token(),
                     "version": VERSION, "seed": SEED, "sample_size": args.sample_size}
        if args.interactive_review:
            print("Blind review ready. Enter Q01..Q30, Q01:3 for expanded material, or done.", flush=True)
            for line in sys.stdin:
                command = line.strip()
                if command == "done":
                    break
                if not re.fullmatch(r"Q\d{2}(?::\d+)?", command):
                    print("Expected Q01 or Q01:3", flush=True)
                    continue
                ordinal = int(command.split(":")[0][1:]) - 1
                q = samples[ordinal]
                pool = candidate_pool(all_rankings[q.qid], ordinal)
                selected = list(enumerate(pool, 1))
                expanded = ":" in command
                if expanded:
                    slot = int(command.split(":")[1])
                    selected = [(slot, pool[slot - 1])]
                print(json.dumps({"sample": f"Q{ordinal + 1:02}", "target": preview(q, expanded), "candidates":
                                  [{"candidate": i, **preview(questions[experiment.positions[qid]], expanded)} for i, qid in selected]}, ensure_ascii=False), flush=True)
            if generation != reads._source_generation_token(db):
                raise RuntimeError("Source changed during review; do not score stale labels")
        if args.review is not None or args.detail:
            ordinals = range(args.review * 5, min(args.review * 5 + 5, len(samples))) if args.review is not None else [int(args.detail.split(":")[0][1:]) - 1]
            for ordinal in ordinals:
                q = samples[ordinal]
                pool = candidate_pool(all_rankings[q.qid], ordinal)
                selected = list(enumerate(pool, 1)) if not args.detail else [(int(args.detail.split(":")[1]), pool[int(args.detail.split(":")[1]) - 1])]
                print(json.dumps({"sample": f"Q{ordinal + 1:02}", "target": preview(q, bool(args.detail)), "candidates":
                                  [{"candidate": i, **preview(questions[experiment.positions[qid]], bool(args.detail))} for i, qid in selected]}, ensure_ascii=False), flush=True)
            return
        ratings = {}
        if args.ratings:
            payload = json.loads(args.ratings.read_text(encoding="utf-8"))
            if payload["signature"] != json.loads(json.dumps(signature)):
                raise ValueError("Ratings belong to a different source snapshot or experiment version")
            ratings = payload["ratings"]
        assessment = summary_metrics(samples, all_rankings, ratings)
        result = {"signature": signature, "corpus": metadata,
                  "parameters": {"weights": WEIGHTS, "rule_threshold": RULE_THRESHOLD, "rrf_k": RRF_K,
                                 "rrf_rule_weight": RRF_RULE_WEIGHT, "rrf_window": RRF_WINDOW},
                  "load_and_validate_ms": round(read_ms, 2), "index_build_ms": round(build_ms, 2),
                  "timing_scope": "Ranking over the same in-memory corpus, excluding SQL, shared preparation, HTML and result-cache hits",
                  "ranking_ms": {k: {"median": round(statistics.median(v), 2), "p90": round(float(np.percentile(v, 90)), 2)} for k, v in timings.items()},
                  "baseline_replay_verified_queries": verified,
                  "actual_current_uncached_service_ms": {"median": round(statistics.median(actual_times), 2)} if actual_times else None,
                  "blind_pool_sizes": {f"Q{i + 1:02}": len(candidate_pool(all_rankings[q.qid], i)) for i, q in enumerate(samples)},
                  "sample_types": dict(Counter(q.kind for q in samples)),
                  "results_changed_queries": {route: sum(all_rankings[q.qid][route] != all_rankings[q.qid]["current"] for q in samples) for route in ROUTES[1:]},
                  "assessment": assessment, "paired_comparisons": paired_comparisons(assessment),
                  "limitations": ["Assessor is Codex, not independent teacher validation", "Pooled relevance is not full-corpus recall",
                                  "Repeated source questions and template-related queries remain in the comparison; bootstrap is exploratory",
                                  "Assessment uses text and existing explanations; not all source images inspected; source-answer correctness not audited",
                                  "No trained model, real model call, production change or source-content export"]}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        # Database filenames/state paths are unnecessary in ordinary output.
        shown = {k: v for k, v in result.items() if k != "signature"}
        print(json.dumps(shown, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
