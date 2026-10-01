"""Isolated, read-only comparison of the adopted B ranker and local embeddings.

Reuses the existing source-valid reader, holdout sampling and blind assessment.
No application integration, source write, remote request or embedding export.
Model files must already exist locally; real inference needs session approval.
Review material goes only to the interactive terminal, never to report files.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
import json
import os
from pathlib import Path
import random
import re
import statistics
import sys
import time
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from tools import experiment_similar_questions as original
from tools.experiment_similar_questions_v2 import holdout_questions, math_text, source_preview
from question_bank.services.similar_question_ranker import (
    SimilarityPart, SimilarityQuestion, SimilarQuestionIndex,
)

VERSION = "local-vector-similarity-v1"
ROUTES = ("current", "vector", "hybrid")
PARAMETERS = {"rrf_k": 20, "window": 80, "current_weight": .75,
              "rule_minimum": .25, "max_tokens": 512,
              "field_token_budgets": [280, 100, 122], "batch_size": 8}


def adopted_index(questions):
    """Reuse the live ranker, with the same source-valid projections as v1."""
    return SimilarQuestionIndex([
        SimilarityQuestion(q.qid, q.text, q.difficulty, q.tags, tuple(
            SimilarityPart(p.skills, p.topics, p.mode, p.operations) for p in q.parts))
        for q in questions])


def held_out_sample(questions, size):
    # The old holdout helper orders type/chapter buckets; a small prefix can
    # contain no multiple-choice questions. Reuse its exclusion rules over the
    # complete eligible pool, then balance the final sample by type/difficulty.
    pool, stats = holdout_questions(questions, original.sample_questions(questions), len(questions))
    sample = original.sample_questions(pool, size)
    return sample, {**stats, "eligible_pool": len(pool), "selected": len(sample),
                    "method": "template_exclusion_then_type_difficulty_balancing"}


def boundary_questions():
    """Explicit synthetic task boundaries, fixed before looking at embeddings.

    Each triplet asks for a same-task practice ahead of a changed-task example.
    This is a small diagnostic set, not representative bank-wide quality gold.
    """
    cases = [
        ("solve_vs_root_condition", "solve", "quadratic", "解方程：x²-5x+6=0。",
         "求出满足(t-1)(t-4)=0的所有实数t。", "求k的范围，使方程x²-5x+k=0有两个不等实根。"),
        ("radical_calculation_vs_concept", "calculate", "radical", "计算：√18-√8。",
         "求√50-√2的值，写出化简过程。", "判断√18是否属于最简二次根式，并说明理由。"),
        ("minimum_vs_intersection", "minimum", "function", "当-2≤x≤0时，求y=2x+3的最小值。",
         "在1≤t≤4的范围内，求y=3t+7的最小值。", "求直线y=2x+3与两个坐标轴交点的坐标。"),
        ("prove_congruence_vs_angle", "proof", "triangle", "已知AB=DE，AC=DF，∠BAC=∠EDF，证明△ABC≌△DEF。",
         "在两三角形中，两组对应边和它们的夹角分别相等，请写出全等证明。", "三角形ABC中∠A=40°、∠B=60°，求∠C。"),
        ("theorem_vs_converse", "length", "pythagoras", "直角三角形斜边长13，一条直角边长5，求另一条直角边。",
         "直角三角形两条直角边为6和8，求斜边长度。", "三角形三边长为5、12、13，判断它是不是直角三角形。"),
        ("negative_inequality", "inequality", "algebra", "解不等式-2x+3>7，并说明除以负数时符号如何变化。",
         "求不等式5-3t≤11的解集，写出每一步。", "解方程-2x+3=7。"),
        ("factor_vs_expand", "factor", "polynomial", "将x²-9分解因式。",
         "把a²-16写成两个一次因式的乘积。", "展开并化简(x-3)(x+3)。"),
        ("rationalize_vs_identify", "rationalize", "radical", "把1/√3的分母有理化。",
         "消去分母中的根号：2/√5。", "判断√3是否为有理数。"),
        ("similarity_ratio_vs_congruence", "ratio", "triangle", "两个相似三角形对应边长为3和6，小三角形面积为5，求大三角形面积。",
         "两相似图形对应长度比为2:3，较小图形面积为8，求较大图形面积。", "两三角形对应两边及夹角相等，证明它们全等。"),
        ("probability_vs_mean", "probability", "statistics", "袋中有3个红球和2个白球，随机摸出一个，求摸到红球的概率。",
         "从装有4个黑球和6个白球的盒中随机抽一球，计算抽到白球的可能性。", "五个学生成绩为3、3、3、2、2，求平均数。"),
        ("distance_vs_slope", "distance", "coordinate", "求坐标平面上A(1,2)、B(4,6)两点间的距离。",
         "计算平面直角坐标系中P(-1,1)与Q(2,5)之间的长度。", "求经过A(1,2)、B(4,6)的直线解析式。"),
        ("two_tasks_vs_partial", "mixed", "radical", "(1)计算√18-√8；(2)把1/√3的分母有理化。",
         "(1)求√50-√2的值；(2)消去2/√5分母中的根号。", "计算√18-√8。"),
    ]
    questions, groups = [], []
    for index, (name, skill, topic, target, positive, distractor) in enumerate(cases):
        ids = [100000 + index * 3 + offset for offset in range(3)]
        for qid, text, actual_skill in zip(ids, (target, positive, distractor), (skill, skill, "other_" + skill)):
            questions.append(original.Question(qid, text, difficulty=4,
                tags={"skill": frozenset([actual_skill]), "topic": frozenset([topic])}))
        groups.append({"name": name, "target": ids[0], "positive": ids[1], "distractor": ids[2]})
    return questions, groups


def boundary_results(questions, groups, vectors):
    vectors = normalize_vectors(vectors, len(questions))
    positions = {q.qid: i for i, q in enumerate(questions)}
    rows = []
    for group in groups:
        target = vectors[positions[group["target"]]]
        positive = float(target @ vectors[positions[group["positive"]]])
        distractor = float(target @ vectors[positions[group["distractor"]]])
        rows.append({"case": group["name"], "same_task_cosine": round(positive, 5),
                     "changed_task_cosine": round(distractor, 5),
                     "same_task_ranked_first": positive > distractor})
    return {"cases": len(rows), "passed": sum(row["same_task_ranked_first"] for row in rows),
            "definition": "Explicit same-task practice must rank above the paired changed-task distractor",
            "rows": rows}


def normalize_vectors(vectors, expected_rows=None):
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2 or (expected_rows is not None and len(values) != expected_rows):
        raise ValueError("Embedding rows do not match the corpus")
    if not np.isfinite(values).all():
        raise ValueError("Embeddings must be finite")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Empty embeddings cannot be ranked")
    return values / norms


class VectorExperiment:
    def __init__(self, questions, vectors):
        self.questions = questions
        self.positions = {q.qid: i for i, q in enumerate(questions)}
        self.current = adopted_index(questions)
        self.vectors = normalize_vectors(vectors, len(questions))

    def vector_rank(self, qid, limit=6):
        scores = self.vectors @ self.vectors[self.positions[qid]]
        return sorted((q.qid for q in self.questions if q.qid != qid),
                      key=lambda key: (-float(scores[self.positions[key]]), key))[:limit]

    def rank(self, qid, route, limit=6):
        if route == "current":
            return [key for key, _ in self.current.rank(qid, limit)]
        if route == "vector":
            return self.vector_rank(qid, limit)
        if route != "hybrid":
            raise ValueError(route)
        base = [key for key, _ in self.current.rank(qid, PARAMETERS["window"])]
        semantic = self.vector_rank(qid, PARAMETERS["window"])
        target = self.current.questions[self.positions[qid]]
        eligible = {key for key in set(base) | set(semantic)
                    if self.current.components(target, self.current.questions[self.positions[key]])["final"]
                    >= PARAMETERS["rule_minimum"]}
        scores = defaultdict(float)
        for weight, ranking in ((PARAMETERS["current_weight"], base),
                                (1 - PARAMETERS["current_weight"], semantic)):
            for ordinal, key in enumerate(ranking, 1):
                if key in eligible:
                    scores[key] += weight / (PARAMETERS["rrf_k"] + ordinal)
        return sorted(scores, key=lambda key: (-scores[key], key))[:limit]


def input_fields(question):
    # No predicted error causes, mastery labels or skill tags in embedding input.
    return ["题目：" + math_text(question.text, compact=False),
            "作答要求：" + math_text(question.description, compact=False),
            "答案解析：" + math_text(question.answer, compact=False)]


class LocalBge:
    """ONNX BGE CLS pooling, normalized exactly as the model author's example.

    Uses local tokenizer/model files only; no download or remote fallback.
    Field budgets preserve the stem instead of letting long solutions hide it.
    """
    def __init__(self, directory):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        directory = Path(directory)
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / "model.onnx"), options,
                                           providers=["CPUExecutionProvider"])
        self.cls = self.tokenizer.token_to_id("[CLS]")
        self.sep = self.tokenizer.token_to_id("[SEP]")
        self.pad = self.tokenizer.token_to_id("[PAD]")
        if any(value is None for value in (self.cls, self.sep, self.pad)):
            raise ValueError("Expected BGE BERT special tokens")
        self.stats = {"encoded_texts": 0, "truncated_stems": 0,
                      "truncated_requirements": 0, "truncated_solutions": 0,
                      "tokens": 0}

    def encode_ids(self, question):
        ids = [self.cls]
        stat_names = ("truncated_stems", "truncated_requirements", "truncated_solutions")
        for text, budget, stat in zip(input_fields(question), PARAMETERS["field_token_budgets"], stat_names):
            field_ids = self.tokenizer.encode(text, add_special_tokens=False).ids
            self.stats[stat] += int(len(field_ids) > budget)
            ids.extend(field_ids[:budget])
        ids.append(self.sep)
        self.stats["encoded_texts"] += 1
        self.stats["tokens"] += len(ids)
        return ids

    def encode(self, questions):
        vectors = []
        names = {entry.name for entry in self.session.get_inputs()}
        for start in range(0, len(questions), PARAMETERS["batch_size"]):
            batch = [self.encode_ids(q) for q in questions[start:start + PARAMETERS["batch_size"]]]
            width = max(map(len, batch))
            input_ids = np.full((len(batch), width), self.pad, dtype=np.int64)
            mask = np.zeros_like(input_ids)
            for position, ids in enumerate(batch):
                input_ids[position, :len(ids)] = ids
                mask[position, :len(ids)] = 1
            inputs = {"input_ids": input_ids, "attention_mask": mask,
                      "token_type_ids": np.zeros_like(input_ids)}
            output = self.session.run(None, {name: value for name, value in inputs.items() if name in names})[0]
            if output.ndim != 3:
                raise ValueError("Expected last_hidden_state from BGE ONNX")
            vectors.append(output[:, 0, :])
            if start % 80 == 0:
                print(f"Local embedding: {min(start + len(batch), len(questions))}/{len(questions)}", flush=True)
        return normalize_vectors(np.concatenate(vectors), len(questions))


def timing_summary(values):
    return {"median": round(statistics.median(values), 3),
            "p90": round(float(np.percentile(values, 90)), 3)} if values else None


def peak_memory_mb():
    if os.name != "nt":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if get_memory(get_process(), ctypes.byref(counters), counters.cb):
        return round(counters.PeakWorkingSetSize / 1024 ** 2, 2)
    return None


def verify_baseline(db, experiment, samples):
    service = original.reads.QuestionBankReadService(db, data_root=db.parent.parent)
    for q in samples:
        actual = service._find_similar_questions(q.qid, limit=6)
        if [row["id"] for row in actual or []] != experiment.rank(q.qid, "current"):
            raise AssertionError("Adopted B replay differs from the live service")
    return len(samples)


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("user_data/databases/question_bank.db"))
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--interactive-review", action="store_true")
    parser.add_argument("--ratings", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 100:
        parser.error("sample-size must be 1..100")
    if not args.baseline_only and not args.model_dir:
        parser.error("Provide an existing local model directory or --baseline-only")
    if args.output_dir.exists():
        parser.error("output-dir must be a new directory")
    db = args.db.resolve()
    with patch("question_bank.knowledge_graph_release.repository.connect", original.readonly_connect):
        generation = original.reads._source_generation_token(db)
        taxonomy = original.reads._taxonomy_generation_token()
        started = time.perf_counter()
        questions, metadata = original.load_questions(db)
        load_ms = (time.perf_counter() - started) * 1000
        samples, sampling = held_out_sample(questions, args.sample_size)
        index = adopted_index(questions)
        baseline_times = []
        for q in samples:
            started = time.perf_counter()
            index.rank(q.qid)
            baseline_times.append((time.perf_counter() - started) * 1000)
        result = {"version": VERSION, "corpus": metadata, "sampling": sampling,
                  "sample_types": dict(Counter(q.kind for q in samples)),
                  "load_and_validate_ms": round(load_ms, 2),
                  "baseline_rank_ms": timing_summary(baseline_times),
                  "parameters": PARAMETERS, "model_calls": 0,
                  "source_content_exported": False}
        if args.baseline_only:
            class BaselineReplay:
                def rank(self, qid, route):
                    return [key for key, _ in index.rank(qid, 6)]
            result["baseline_replay_verified_queries"] = verify_baseline(db, BaselineReplay(), samples)
            if generation != original.reads._source_generation_token(db) or taxonomy != original.reads._taxonomy_generation_token():
                raise RuntimeError("Source changed during baseline preparation")
            result["status"] = "baseline_prepared_model_inference_not_run"
            result["peak_process_memory_mb"] = peak_memory_mb()
            write_new(args.output_dir / "baseline.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        started = time.perf_counter()
        encoder = LocalBge(args.model_dir)
        result["model_load_ms"] = round((time.perf_counter() - started) * 1000, 2)
        started = time.perf_counter()
        boundary, boundary_groups = boundary_questions()
        encoded = encoder.encode(questions + boundary)
        vectors, boundary_vectors = encoded[:len(questions)], encoded[len(questions):]
        result["embedding_ms"] = round((time.perf_counter() - started) * 1000, 2)
        result["embedding_stats"] = encoder.stats
        result["model_calls"] = {"local_batches": (len(questions) + len(boundary) + 7) // 8, "remote": 0}
        result["synthetic_boundaries"] = boundary_results(boundary, boundary_groups, boundary_vectors)
        result["vector_dimension"] = vectors.shape[1]
        result["raw_vector_memory_mb"] = round(vectors.nbytes / 1024 ** 2, 3)
        experiment = VectorExperiment(questions, vectors)
        all_rankings, timings = {}, defaultdict(list)
        for ordinal, q in enumerate(samples):
            rankings = {}
            routes = list(ROUTES)
            random.Random(original.SEED + ordinal).shuffle(routes)
            for route in routes:
                started = time.perf_counter()
                rankings[route] = experiment.rank(q.qid, route)
                timings[route].append((time.perf_counter() - started) * 1000)
            all_rankings[q.qid] = rankings
        verified = verify_baseline(db, experiment, samples)
        # Keep absolute source paths out of saved aggregate reports.
        signature = {"version": VERSION, "database_generation": generation[1:],
                     "release": metadata["release"], "parameters": PARAMETERS,
                     "sample_size": len(samples), "model": "Xenova/bge-small-zh-v1.5/model.onnx"}
        manifest = {"signature": signature, "review": {
            f"Q{i+1:02}": {"grades": [None] * len(original.candidate_pool(all_rankings[q.qid], i)), "note": ""}
            for i, q in enumerate(samples)},
            "protocol": "Routes hidden; 0 unrelated, 1 partial, 2 useful practice, 3 close substitute; null insufficient material"}
        write_new(args.output_dir / "manifest.json", manifest)
        if args.interactive_review:
            print("Blind review ready: Q01, Q01:3, or done. Source text is transient only.", flush=True)
            for line in sys.stdin:
                command = line.strip()
                if command == "done":
                    break
                if not re.fullmatch(r"Q\d{2,3}(?::\d+)?", command):
                    print("Expected Q01, Q01:3, or done", flush=True)
                    continue
                ordinal = int(command.split(":")[0][1:]) - 1
                if not 0 <= ordinal < len(samples):
                    print("Query outside sample", flush=True)
                    continue
                target = samples[ordinal]
                pool = original.candidate_pool(all_rankings[target.qid], ordinal)
                expanded = ":" in command
                slots = [int(command.split(":")[1])] if expanded else list(range(1, len(pool) + 1))
                if not all(1 <= slot <= len(pool) for slot in slots):
                    print("Candidate outside pool", flush=True)
                    continue
                print(json.dumps({"sample": f"Q{ordinal+1:02}", "target": source_preview(target, expanded),
                    "candidates": [{"candidate": slot, **source_preview(questions[experiment.positions[pool[slot-1]]], expanded)}
                                   for slot in slots]}, ensure_ascii=False), flush=True)
        if generation != original.reads._source_generation_token(db) or taxonomy != original.reads._taxonomy_generation_token():
            raise RuntimeError("Source changed during the experiment")
        labels = {}
        if args.ratings:
            submitted = json.loads(args.ratings.read_text(encoding="utf-8"))
            if submitted["signature"] != json.loads(json.dumps(signature)):
                raise ValueError("Ratings belong to a different source/model/experiment")
            labels = submitted["ratings"]
            if set(labels) != set(manifest["review"]):
                raise ValueError("All sampled questions need assessment records")
        assessment = original.summary_metrics(samples, all_rankings, labels, routes=ROUTES)
        result.update({"status": "assessed" if labels else "ranked_not_assessed",
            "signature": signature, "ranking_ms": {r: timing_summary(timings[r]) for r in ROUTES},
            "baseline_replay_verified_queries": verified,
            "results_changed_queries": {r: sum(all_rankings[q.qid][r] != all_rankings[q.qid]["current"] for q in samples)
                                        for r in ROUTES if r != "current"},
            "assessment": assessment,
            "paired_comparisons": original.paired_comparisons(assessment, (("hybrid", "current"), ("vector", "current"))),
            "peak_process_memory_mb": peak_memory_mb(),
            "limitations": ["Text-only, images not embedded; token truncation reported",
                "One small Chinese model; no embedding-model comparison or fine-tuning",
                "Exact NumPy scan only; no vector database or approximate index benchmark",
                "Codex pooled judgment, not independent teacher validation or full-corpus recall",
                "Teacher similar-question list only; student practice eligibility not evaluated",
                "Ranking timings exclude model encoding, shared loading and application caches"]})
        write_new(args.output_dir / "results.json", result)
        compact = {k: v for k, v in result.items() if k not in ("signature", "assessment")}
        compact["assessment"] = {r: {k: v for k, v in a.items() if k != "per_query"} for r, a in assessment.items()}
        print(json.dumps(compact, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
