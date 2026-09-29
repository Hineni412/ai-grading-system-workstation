"""Read-only B revision: missing evidence, task intent, union retrieval, math text.

Reuse experiment_similar_questions for source validity, frozen current/B
rankers, sampling and assessment. No application route or source data changes.
Source text is transient review output only; never redirect review commands.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import html
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys
import time
import unicodedata
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from tools import experiment_similar_questions as original
from question_bank.parsers.type_detector import _SUBQ_CANDIDATE_RE, _is_subq_marker_context

VERSION = "local-similarity-b-v2.1"
HOLDOUT_SEED = 20260929
ROUTES = ("current", "b_original", "b_revised")
PAIRS = (("b_revised", "b_original"), ("b_revised", "current"), ("b_original", "current"))
PARAMETERS = {"rrf_k": 20, "rrf_window_per_channel": 80, "rrf_rule_weight": .7,
              "quality_weight": .65, "minimum_final_score": .25,
              "known_goal_mismatch_factor": .25, "unknown_goal_factor": .8,
              "holdout_template_dice_max": .72}
SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")


def math_text(value: object, *, compact: bool = True) -> str:
    """Drop HTML presentation while preserving explicit math structure."""
    text = original.IMAGE.sub("", str(value or ""))
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", text, flags=re.I | re.S)
    for tag, marker in (("sup", "^"), ("sub", "_")):
        text = re.sub(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>",
                      lambda m: marker + "(" + re.sub(r"<[^>]+>", "", m[1]).strip() + ")", text, flags=re.I | re.S)
    text = re.sub(r"<(?:/?(?:p|div|tr|td|th|table|br|li|ul|ol))\b[^>]*>", "\n", text, flags=re.I)
    # Only syntactically plausible tags, not an inequality such as x<3.
    text = re.sub(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*?)?\s*/?>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", lambda m: "^(" + m[0].translate(SUPERSCRIPT) + ")", text)
    text = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋]+", lambda m: "_(" + m[0].translate(SUBSCRIPT) + ")", text)
    text = unicodedata.normalize("NFC", text).lower()
    text = text.translate(str.maketrans({"（": "(", "）": ")", "−": "-", "﹣": "-", "＝": "=", "＋": "+", "：": ":"}))
    text = re.sub(r"^\s*(?:第\s*)?\d{1,3}\s*[.、．](?=\s|[\u3400-\u9fff])\s*", "", text)
    text = re.sub(r"^\s*\(\s*\d+(?:\.\d+)?\s*分\s*\)", "", text)
    return re.sub(r"\s+", "" if compact else " ", text).strip()


def math_tokens(value: str) -> list[str]:
    text = math_text(value)
    # Presentation phrases carry little teaching meaning. Actual mathematical
    # conditions and requested results remain in the lexical channel.
    text = re.sub(r"如图所示|如图\d*|请直接写出|直接写出|根据题意|温馨提示|不用写作法", "", text)
    result = []
    pattern = r"[\u3400-\u9fff]+|[a-z]+|\d+(?:\.\d+)?|[+\-*/=<>≤≥≠×÷√^_(){}]"
    for token in re.findall(pattern, text):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            if len(token) == 1:
                result.append(token)
            for width in (2, 3):
                result.extend(token[i:i + width] for i in range(len(token) - width + 1))
        else:
            result.append(token)
    return result


def aims_from_text(text: str, *, fallback: bool = False) -> frozenset[str]:
    """Conservative local task cues, not a semantic model or new stored tags."""
    value = math_text(text)
    if not value:
        return frozenset()
    # A standard-answer-only point provides no evidence about the task.
    if fallback:
        value = re.sub(r"作答为[^；。]*|与标准答案一致", "", value)
    aims = set()
    patterns = {
        "proof": r"求证|证明|说明理由|试说明",
        "drawing": r"画出|作出|作图|绘制|描点",
        "optimization": r"最大值|最小值|最短|最长|最远|最近|最省|至少要走|至少为",
        "range": r"取值范围|取值条件|解集",
        "formula": r"解析式|表达式|函数关系式",
        "equation": r"解.{0,4}方程|方程.{0,10}的解|方程组.{0,15}解|求.{0,12}(?:根|实数解)",
        "calculation": r"计算|运算|化简|求值|代数式.{0,30}的值",
        "coordinate": r"坐标为|坐标是|求.{0,15}坐标|写出.{0,15}坐标|表示.{0,10}位置",
        "angle": r"度数|几度|角.{0,5}大小|∠[^；。]{0,12}为[_ ]",
        "length": r"求.{0,20}(?:长度|的长|距离)|(?:线段|边|周长|路程|距离|高度).{0,15}(?:是|为|等于)|[a-z′']{2,4}的长|周长",
        "area": r"求.{0,20}面积|面积.{0,8}(?:是|为|等于)|面积之|面积比|面积的值",
        "recognition": r"下列.{0,16}(?:正确|错误|成立)|说法.{0,8}(?:正确|错误)|命题|是最简|为最简|判断|判定",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, value):
            aims.add(name)
    if re.search(r"函数图[象像]|图[象像].{0,12}函数|s.{0,6}随.{0,8}时间", value):
        if re.search(r"图[象像].{0,12}(?:大致|可以|表示为)|图[象像]大致", value):
            aims.add("graph_matching")
        elif "drawing" not in aims:
            aims.add("graph_reading")
    # The operation is calculation even if presented as a choice of correct
    # calculations. Concept identification remains a different task.
    if "calculation" in aims and re.search(r"(?:计算|运算).{0,8}(?:正确|错误)", value):
        aims.discard("recognition")
    return frozenset(aims)


def part_prompts(question: original.Question) -> list[str]:
    if len(question.parts) <= 1:
        return [question.text]
    text = math_text(question.text, compact=False)
    marks = [m for m in _SUBQ_CANDIDATE_RE.finditer(text)
             if _is_subq_marker_context(text, m.start(), m.end())]
    labels = [int(m[1]) for m in marks]
    if labels != list(range(1, len(question.parts) + 1)):
        return [""] * len(question.parts)
    # Only a shared operation heading is inherited. Do not assign the entire
    # multipart stem, or another part's requested result, to every part.
    heading = text[:marks[0].start()]
    shared = "计算：" if re.search(r"(?:计算|化简)\s*[:：]?\s*$", heading) else ""
    return [shared + text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            for i, m in enumerate(marks)]


@dataclass(frozen=True)
class Requirement:
    aims: frozenset[str]
    mode: str
    skills: frozenset[str]
    topics: frozenset[str]


class RevisedExperiment:
    """One concrete revised scorer beside the frozen experimental B."""
    def __init__(self, baseline: original.Experiment):
        self.base = baseline
        self.questions = baseline.questions
        self.positions = baseline.positions
        self.texts = {q.qid: math_text(q.text) for q in self.questions}
        self.grams = {qid: original.grams(text) for qid, text in self.texts.items()}
        self.requirements = {}
        for q in self.questions:
            prompts = part_prompts(q)
            parts = q.parts or (original.Part(frozenset(), frozenset(), "", frozenset()),)
            requirements = []
            for part, prompt in zip(parts, prompts):
                # Prefer what the student is asked to produce, not every
                # operation mentioned along the solution's reasoning chain.
                aims = aims_from_text(prompt) or aims_from_text(part.description, fallback=True)
                requirements.append(Requirement(aims, part.mode, part.skills, part.topics))
            self.requirements[q.qid] = tuple(requirements)
        documents = [Counter(math_tokens(q.text)) for q in self.questions]
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
            idf = math.log(1 + (len(self.questions) - len(rows) + .5) / (len(rows) + .5))
            # Variable names and literal numbers are supporting lexical cues.
            term_weight = .25 if re.fullmatch(r"[a-z]+|\d+(?:\.\d+)?", term) else 1.
            weights = term_weight * idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * lengths[pos] / avg))
            self.postings[term] = pos, weights

    def demand_score(self, a: original.Question, b: original.Question) -> float | None:
        left, right = self.requirements[a.qid], self.requirements[b.qid]
        if not any(p.aims for p in left) or not any(p.aims for p in right):
            return None
        matrix = []
        for p in left:
            row = []
            for q in right:
                if not p.aims or not q.aims:
                    row.append(0.)
                    continue
                mode = 1. if not p.mode or not q.mode or p.mode == q.mode else .9
                intent = original.dice(p.aims, q.aims)
                # When both parts have explicit content anchors, unrelated
                # topics cannot be converted into a full small-part match.
                if (p.skills or p.topics) and (q.skills or q.topics) and not (p.skills & q.skills or p.topics & q.topics):
                    intent *= .5
                row.append(mode * intent)
            matrix.append(row)
        return .5 * (statistics.mean(max(row) for row in matrix)
                     + statistics.mean(max(row[j] for row in matrix) for j in range(len(right))))

    def components(self, a: original.Question, b: original.Question) -> dict:
        scores = {dim: self.base.weighted_dice(a, b, dim) for dim in self.base.idf}
        scores["knowledge"] = max(original.dice(a.tags.get("topic", frozenset()), b.tags.get("topic", frozenset())),
                                  .5 * original.dice(a.tags.get("section", frozenset()), b.tags.get("section", frozenset())),
                                  .15 * original.dice(a.tags.get("chapter", frozenset()), b.tags.get("chapter", frozenset())))
        demand = self.demand_score(a, b)
        # Unknown and a known mismatch are different states. A missing skill
        # is not a zero-valued 42% component. Sparse evidence is still capped.
        active = {dim for dim in self.base.idf if a.tags.get(dim) and b.tags.get(dim)}
        if any(a.tags.get(k) for k in ("topic", "section", "chapter")) and any(b.tags.get(k) for k in ("topic", "section", "chapter")):
            active.add("knowledge")
        scores["demand"] = demand
        if demand is not None:
            active.add("demand")
        weight = sum(original.WEIGHTS[dim] for dim in active)
        semantic = sum(original.WEIGHTS[dim] * scores[dim] for dim in active) / weight if weight else 0.
        semantic *= .35 + .65 * min(1., weight / .7)
        wording = original.dice(self.grams[a.qid], self.grams[b.qid])
        intent_factor = PARAMETERS["unknown_goal_factor"] if demand is None else .25 + .75 * demand
        quality = max(.88 * semantic + .12 * wording, .55 * wording)
        quality *= original.smooth_difficulty(a.difficulty, b.difficulty) * intent_factor
        anchored = scores["skill"] > 0 or scores["knowledge"] >= .25 or scores["method"] > 0 or scores["model"] > 0
        return {**scores, "wording": wording, "known_weight": weight, "semantic": semantic,
                "intent_factor": intent_factor, "anchored": anchored, "quality": quality}

    def lexical_scores(self, target: original.Question) -> dict[int, float]:
        scores = np.zeros(len(self.questions))
        for term in set(math_tokens(target.text)):
            if term in self.postings:
                pos, weights = self.postings[term]
                scores[pos] += weights
        return {q.qid: float(scores[i]) for i, q in enumerate(self.questions) if q.qid != target.qid and scores[i] > 0}

    def scored(self, qid: int) -> list[tuple[int, float]]:
        target = self.questions[self.positions[qid]]
        components = {q.qid: self.components(target, q) for q in self.questions if q.qid != qid}
        ordered = original.Experiment.ordered({qid: c["quality"] for qid, c in components.items()})[:80]
        lexical = original.Experiment.ordered(self.lexical_scores(target))[:80]
        # Both channels enter the pool independently. Quality is checked after
        # union; a lexical rank cannot override an explicit task mismatch.
        fused = {qid: .7 * 21 / (20 + rank) for rank, qid in enumerate(ordered, 1)}
        for rank, qid in enumerate(lexical, 1):
            fused[qid] = fused.get(qid, 0.) + .3 * 21 / (20 + rank)
        results = []
        for candidate, rrf in fused.items():
            c = components[candidate]
            if not c["anchored"] and c["wording"] < .35:
                continue
            score = .65 * c["quality"] + .35 * rrf * c["intent_factor"]
            if score >= PARAMETERS["minimum_final_score"]:
                results.append((candidate, score))
        return sorted(results, key=lambda row: (-row[1], row[0]))

    def rank(self, qid: int, limit: int = 6) -> list[int]:
        return [qid for qid, _ in self.scored(qid)[:limit]]


def template_grams(question: original.Question) -> frozenset[str]:
    text = math_text(question.text)
    text = re.sub(r"\d+(?:\.\d+)?", "#", text)
    text = re.sub(r"[a-z]+", "v", text)
    return original.grams(text)


def holdout_questions(questions, development, size=60):
    """Stratify by type/difficulty/chapter; reject close textual templates."""
    excluded = {q.qid for q in development}
    selected_grams = [template_grams(q) for q in development]
    groups = defaultdict(list)
    for q in questions:
        if q.qid in excluded:
            continue
        band = "unknown" if q.difficulty is None else "low" if q.difficulty < 4 else "middle" if q.difficulty < 7 else "high"
        chapter = next(iter(sorted(q.tags.get("chapter", ()))), "unknown")
        groups[q.kind, band, chapter].append(q)
    rng = random.Random(HOLDOUT_SEED)
    for rows in groups.values():
        rng.shuffle(rows)
    chosen, rejected = [], 0
    while len(chosen) < size and any(groups.values()):
        for key in sorted(groups):
            if len(chosen) >= size:
                break
            while groups[key]:
                q = groups[key].pop()
                current = template_grams(q)
                if any(original.dice(current, prior) > PARAMETERS["holdout_template_dice_max"] for prior in selected_grams):
                    rejected += 1
                    continue
                chosen.append(q)
                selected_grams.append(current)
                break
    return chosen, {"excluded_development_queries": len(excluded), "rejected_near_template_queries": rejected,
                    "selected": len(chosen), "max_template_dice": PARAMETERS["holdout_template_dice_max"]}


def source_preview(question, expanded=False):
    # Answers/marking points may disambiguate missing source images, but are
    # never used as an automatic relevance label.
    raw = original.preview(question, expanded)
    return {"题面": math_text(raw["题面"], compact=False),
            "解析": math_text(raw["答案解析"], compact=False)[:2500 if expanded else 300],
            "难度": question.difficulty}


def duplicate_adjusted(samples, rankings, labels, questions):
    # This metric collapses equal visible text; differing diagrams can make
    # equal text non-identical. It is explicitly not a new duplicate truth.
    texts = {q.qid: math_text(q.text) for q in questions}
    result = {}
    for route in ROUTES:
        useful, duplicates = 0, 0
        for ordinal, target in enumerate(samples):
            pool = original.candidate_pool(rankings[target.qid], ordinal)
            grade = dict(zip(pool, labels[f"Q{ordinal+1:02}"]["grades"]))
            seen = {texts[target.qid]}
            for qid in rankings[target.qid][route]:
                if texts[qid] in seen:
                    duplicates += 1
                elif grade[qid] is not None and grade[qid] >= 2:
                    useful += 1
                seen.add(texts[qid])
        result[route] = {"useful_after_equal_visible_text_collapse": useful,
                         "equal_visible_text_slots": duplicates,
                         "useful_per_six": round(useful / (6 * len(samples)), 4)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("user_data/databases/question_bank.db"))
    parser.add_argument("--cohort", choices=("regression", "holdout"), required=True)
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--prior-ratings", type=Path)
    parser.add_argument("--ratings", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interactive-review", action="store_true")
    parser.add_argument("--verify-baseline", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 100:
        parser.error("sample-size must be 1..100")
    db = args.db.resolve()
    with patch("question_bank.knowledge_graph_release.repository.connect", original.readonly_connect):
        generation = original.reads._source_generation_token(db)
        taxonomy = original.reads._taxonomy_generation_token()
        started = time.perf_counter()
        questions, metadata = original.load_questions(db)
        load_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        baseline = original.Experiment(questions)
        revised = RevisedExperiment(baseline)
        prepare_ms = (time.perf_counter() - started) * 1000
        development = original.sample_questions(questions)
        if args.cohort == "regression":
            samples, sampling = development, {"selected": len(development)}
        else:
            samples, sampling = holdout_questions(questions, development, args.sample_size)
        all_rankings, timings, labels = {}, defaultdict(list), {}
        prior = json.loads(args.prior_ratings.read_text(encoding="utf-8")) if args.prior_ratings else None
        if prior:
            if args.cohort != "regression" or prior["signature"]["database_generation"] != json.loads(json.dumps(generation)) or prior["signature"]["taxonomy_generation"] != json.loads(json.dumps(taxonomy)):
                raise ValueError("Prior labels do not describe this source/cohort")
        print(f"Prepared {len(samples)} {args.cohort} queries; ranking frozen routes.", flush=True)
        for ordinal, target in enumerate(samples):
            rankings = {}
            routes = list(ROUTES)
            random.Random(original.SEED + ordinal).shuffle(routes)
            for route in routes:
                started = time.perf_counter()
                if route == "b_revised":
                    rankings[route] = revised.rank(target.qid)
                else:
                    rankings[route] = baseline.rank(target.qid, "current" if route == "current" else "rules_bm25_rrf")
                timings[route].append((time.perf_counter() - started) * 1000)
            all_rankings[target.qid] = rankings
            pool = original.candidate_pool(rankings, ordinal)
            known = {}
            if prior:
                old = {"current": rankings["current"], "skill_rules": baseline.rank(target.qid, "skill_rules"),
                       "rules_bm25_rrf": rankings["b_original"]}
                old_pool = original.candidate_pool(old, ordinal)
                old_grades = prior["ratings"][f"Q{ordinal+1:02}"]["grades"]
                if len(old_pool) != len(old_grades):
                    raise ValueError("Frozen B candidate pool changed")
                known = dict(zip(old_pool, old_grades))
            labels[f"Q{ordinal+1:02}"] = {"grades": [known.get(qid) for qid in pool], "note": "",
                                         "inherited_positions": [i+1 for i, qid in enumerate(pool) if qid in known]}
        verified = 0
        if args.verify_baseline:
            service = original.reads.QuestionBankReadService(db, data_root=db.parent.parent)
            for target in samples:
                actual = service._find_similar_questions(target.qid, limit=6)
                if [r["id"] for r in actual or []] != all_rankings[target.qid]["current"]:
                    raise AssertionError("Existing ranker replay differs from actual service")
                verified += 1
        signature = {"database_generation": generation, "taxonomy_generation": taxonomy, "version": VERSION,
                     "cohort": args.cohort, "seed": original.SEED if args.cohort == "regression" else HOLDOUT_SEED,
                     "sample_size": len(samples), "parameters": PARAMETERS}
        manifest = {"signature": signature, "corpus": metadata, "sampling": sampling,
                    "review": labels, "baseline_replay_verified_queries": verified,
                    "review_protocol": "Routes hidden; inherited grades fixed; null means unread or unassessable. Never export source text."}
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        if args.interactive_review:
            print("Review ready. Q01 shows unread candidates; Q01:3 expands one; done scores the ratings file.", flush=True)
            for line in sys.stdin:
                command = line.strip()
                if command == "done":
                    break
                if not re.fullmatch(r"Q\d{2,3}(?::\d+)?", command):
                    print("Expected Q01, Q01:3, or done.", flush=True)
                    continue
                ordinal = int(command.split(":")[0][1:]) - 1
                if not 0 <= ordinal < len(samples):
                    print("Query outside cohort.", flush=True)
                    continue
                target = samples[ordinal]
                pool = original.candidate_pool(all_rankings[target.qid], ordinal)
                key = f"Q{ordinal+1:02}"
                expanded = ":" in command
                slots = [int(command.split(":")[1])] if expanded else [i+1 for i in range(len(pool)) if i+1 not in labels[key]["inherited_positions"]]
                if not all(1 <= slot <= len(pool) for slot in slots):
                    print("Candidate outside pool.", flush=True)
                    continue
                print(json.dumps({"sample": key, "pool_size": len(pool), "target": source_preview(target, expanded),
                                  "candidates": [{"candidate": slot, **source_preview(questions[baseline.positions[pool[slot-1]]], expanded)} for slot in slots]}, ensure_ascii=False), flush=True)
        if generation != original.reads._source_generation_token(db) or taxonomy != original.reads._taxonomy_generation_token():
            raise RuntimeError("Source changed during comparison")
        if args.ratings:
            submitted = json.loads(args.ratings.read_text(encoding="utf-8"))
            if submitted["signature"] != json.loads(json.dumps(signature)):
                raise ValueError("Ratings belong to another experiment")
            labels = submitted["ratings"]
        else:
            labels = {}
        if labels and set(labels) != {f"Q{i+1:02}" for i in range(len(samples))}:
            raise ValueError("All sampled questions need assessor records")
        if prior and labels:
            for key, prior_row in manifest["review"].items():
                for slot in prior_row["inherited_positions"]:
                    if labels[key]["grades"][slot-1] != prior_row["grades"][slot-1]:
                        raise ValueError("Do not revise inherited judgments after seeing improved rankings")
        assessment = original.summary_metrics(samples, all_rankings, labels, routes=ROUTES)
        result = {"signature": signature, "corpus": metadata, "sampling": sampling,
                  "sample_types": dict(Counter(q.kind for q in samples)),
                  "load_and_validate_ms": round(load_ms, 2), "prepare_both_indexes_ms": round(prepare_ms, 2),
                  "ranking_ms": {k: {"median": round(statistics.median(v), 2), "p90": round(float(np.percentile(v,90)),2)} for k,v in timings.items()},
                  "timing_scope": "Same in-memory corpus; excludes shared loading, index construction, SQL and application result cache",
                  "baseline_replay_verified_queries": verified,
                  "assessment": assessment, "paired_comparisons": original.paired_comparisons(assessment, PAIRS),
                  "visible_text_duplicate_adjusted": duplicate_adjusted(samples, all_rankings, labels, questions) if labels else {},
                  "limitations": ["Codex judgment from text and existing explanations, not independent teacher validation",
                                  "Pooled judgments are not full-corpus recall; no source-answer correctness audit",
                                  "Visible-text duplicate collapse can conflate different diagrams; no new duplicate identities are stored",
                                  "Template exclusion is lexical and does not establish independent mathematical topic families",
                                  "No application integration, real model call, source write or source-text export"]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        compact = {k:v for k,v in result.items() if k not in {"signature", "assessment"}}
        compact["assessment"] = {k:{a:b for a,b in v.items() if a != "per_query"} for k,v in assessment.items()}
        print(json.dumps(compact, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
