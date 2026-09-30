# -*- coding: utf-8 -*-
"""Roster-constrained, fully local identity assignment for scan preflight.

Each uploaded file is one class, and each paper's 班级 box is readable, so the
local OCR recognizer only needs to score the handful of characters in the class
roster instead of asking a model to pick one name out of the whole school.
Scores become per-student posteriors under a class prior, and a Hungarian
assignment keeps the result one-to-one: a student can never receive two papers
silently, and duplicate roster names split the posterior and fall back to
teacher confirmation. No remote model is involved.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image

IDENTITY_METHOD = "roster"            # match_method written on auto groups
MIN_EVIDENCE = -20.0                  # min CTC log-likelihood of the chosen name for auto
AUTO_POSTERIOR = 0.9
LEAVE_PROBABILITY = 0.05              # cost of leaving a paper unassigned = -log(0.05)
FILE_CLASS_SHARE = 0.7
READING_PENALTY = math.log(0.01)      # student class != class read on the paper
FILE_PENALTY = math.log(0.05)         # student class != file's majority class (no reading)
MAX_ROUNDS = 6
NAME_PAD_X, NAME_PAD_Y, CLASS_BAND_WIDTH = 0.08, 0.15, 1.6
LABEL_CHARS = frozenset("姓名：:")
TAIL_CHARS = frozenset("班级")

_CONFIDENT_FRAME = 0.5


@dataclass(frozen=True)
class RosterEntry:
    student_id: int
    name: str
    class_name: str


@dataclass
class PaperEvidence:
    key: str
    file_key: str
    display_text: str
    class_name: str | None
    scores: np.ndarray  # len(roster), non-finite -> -1e4


@dataclass(frozen=True)
class IdentityDecision:
    key: str
    status: Literal["auto", "review"]
    student_id: int | None
    posterior: float
    evidence: float
    # <=3 (student_id, posterior) among free students, best first
    suggestions: tuple[tuple[int, float], ...]
    # class used for the prior (reading, else file class)
    class_name: str | None


def build_vocabulary(roster: Sequence[RosterEntry]) -> list[str]:
    chars: set[str] = set()
    for entry in roster:
        chars.update(entry.name)
    chars |= LABEL_CHARS | TAIL_CHARS
    return ["<blank>", *sorted(chars)]


def _softmax(row: np.ndarray) -> np.ndarray:
    shifted = row - np.max(row)
    e = np.exp(shifted)
    return e / e.sum()


def _handwriting_segment(probs: np.ndarray, column_chars: Sequence[str]) -> np.ndarray:
    """Drop frames of the printed '姓名：' prefix and the '班/级' tail when present."""
    arg = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    start, end = 0, probs.shape[0]
    for t in range(probs.shape[0]):
        if arg[t] != 0 and conf[t] > _CONFIDENT_FRAME and column_chars[arg[t]] in LABEL_CHARS:
            start = t + 1
    for t in range(probs.shape[0] - 1, start - 1, -1):
        if arg[t] != 0 and conf[t] > _CONFIDENT_FRAME and column_chars[arg[t]] in TAIL_CHARS:
            end = t
    segment = probs[start:end]
    return segment if segment.shape[0] >= 2 else probs


def _ctc_logprob_all(log_probs: np.ndarray, labels: list[list[int]]) -> np.ndarray:
    """Standard CTC log p(label | frames) for every label; blank is column 0.

    Same recurrence as the per-name loop, vectorized across labels: padded state
    positions never read back, so only each label's own S = 2*len+1 states matter.
    """
    count = len(labels)
    scores = np.full(count, -np.inf)
    if count == 0 or log_probs.shape[0] == 0:
        return scores
    state_count = np.array([2 * len(label) + 1 for label in labels], dtype=np.int64)
    states = int(state_count.max())
    ext = np.zeros((count, states), dtype=np.int64)
    for j, label in enumerate(labels):
        for k, char in enumerate(label):
            ext[j, 2 * k + 1] = char
    emit = np.take(log_probs, ext, axis=1)  # (T, N, S)
    skip = np.zeros((count, states), dtype=bool)
    if states >= 3:
        skip[:, 2:] = (ext[:, 2:] != 0) & (ext[:, 2:] != ext[:, :-2])
    alpha = np.full((count, states), -np.inf)
    alpha[:, 0] = emit[0, :, 0]
    if states > 1:
        alpha[:, 1] = emit[0, :, 1]
    neg = np.full((count, 1), -np.inf)
    for t in range(1, log_probs.shape[0]):
        from_self = alpha
        from_prev = np.concatenate([neg, alpha[:, :-1]], axis=1)
        from_skip = np.where(skip, np.concatenate([neg, neg, alpha[:, :-2]], axis=1), -np.inf)
        alpha = np.logaddexp(np.logaddexp(from_self, from_prev), from_skip) + emit[t]
    rows = np.arange(count)
    last = state_count - 1
    tail = alpha[rows, last]
    prev = np.where(state_count > 1, alpha[rows, np.maximum(last - 1, 0)], -np.inf)
    return np.where(state_count > 1, np.logaddexp(tail, prev), tail)


def _roster_labels(roster: Sequence[RosterEntry], vocabulary: Sequence[str]) -> list[list[int]]:
    char_col = {ch: i for i, ch in enumerate(vocabulary) if i > 0}
    return [[char_col[ch] for ch in entry.name if ch in char_col] for entry in roster]


def _matrix_scores(matrix: np.ndarray, labels: list[list[int]], vocabulary: Sequence[str]) -> np.ndarray:
    """CTC log p(name | frames) for every label over one detected line."""
    if matrix is None:
        return np.full(len(labels), -np.inf)
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(vocabulary):
        return np.full(len(labels), -np.inf)
    segment = _handwriting_segment(matrix, vocabulary)
    log_probs = np.log(np.clip(segment, 1e-8, 1.0))
    return _ctc_logprob_all(log_probs, labels)


def _stack_scores(
    matrices: Sequence[np.ndarray],
    roster: Sequence[RosterEntry],
    vocabulary: Sequence[str],
) -> np.ndarray:
    """(line, roster) CTC scores; rows are -inf for malformed matrices."""
    labels = _roster_labels(roster, vocabulary)
    if not matrices:
        return np.zeros((0, len(labels)))
    return np.stack([_matrix_scores(matrix, labels, vocabulary) for matrix in matrices])


def roster_scores(
    matrices: Sequence[np.ndarray],
    roster: Sequence[RosterEntry],
    vocabulary: Sequence[str],
) -> np.ndarray:
    """Best CTC log-likelihood of every roster name over the detected lines."""
    per_line = _stack_scores(matrices, roster, vocabulary)
    best = per_line.max(axis=0) if len(per_line) else np.full(len(roster), -np.inf)
    return np.where(np.isfinite(best), best, -1e4)


def read_class_name(
    box_texts: Sequence[str],
    band_text: str,
    class_names: Sequence[str],
) -> str | None:
    """Resolve the class number written on the paper to a roster class name.

    Detector box texts only count when they actually mention 级/班; the whole-band
    text always counts. Digits (八 normalized to 8) vote for the longest class
    name they end with, allowing at most two leading digits (grade prefix).
    """
    votes: Counter[str] = Counter()
    ordered = sorted((str(name) for name in class_names if name), key=len, reverse=True)
    candidates = [text for text in box_texts if "级" in str(text) or "班" in str(text)]
    candidates.append(band_text)
    for text in candidates:
        digits = re.sub(r"[^0-9]", "", str(text or "").replace("八", "8"))
        if not digits:
            continue
        for name in ordered:
            if digits.endswith(name) and len(digits) <= len(name) + 2:
                votes[name] += 1
                break
    return votes.most_common(1)[0][0] if votes else None


def extract_paper_evidence(
    page: Image.Image,
    name_region: dict | None,
    roster: Sequence[RosterEntry],
    vocabulary: Sequence[str],
    *,
    key: str,
    file_key: str,
    ocr=None,
) -> PaperEvidence:
    """Run local OCR on the padded name box and the class band to its right.

    Recognizer probabilities are restricted to the vocabulary columns (blank =
    column 0); vocabulary entries the recognizer does not know are dropped
    consistently from both the columns and the scoring alphabet.
    """
    from scanner import _student_name_crop_box  # local import: scanner imports us

    if ocr is None:
        from local_ocr import get_local_ocr
        ocr = get_local_ocr()
    width, height = page.size
    left, top, right, bottom = _student_name_crop_box(name_region, width, height)
    box_w, box_h = right - left, bottom - top
    name_box = (
        max(0, left - int(box_w * NAME_PAD_X)),
        max(0, top - int(box_h * NAME_PAD_Y)),
        min(width, right + int(box_w * NAME_PAD_X)),
        min(height, bottom + int(box_h * NAME_PAD_Y)),
    )
    class_box = (
        right,
        max(0, top - int(box_h * NAME_PAD_Y)),
        min(width, right + int(box_w * CLASS_BAND_WIDTH)),
        min(height, bottom + int(box_h * NAME_PAD_Y)),
    )
    rgb = page if page.mode == "RGB" else page.convert("RGB")
    name_bgr = np.asarray(rgb.crop(name_box))[:, :, ::-1].copy()
    class_bgr = np.asarray(rgb.crop(class_box))[:, :, ::-1].copy()

    column_map = ocr.character_columns(vocabulary)
    available = [vocabulary[0], *[c for c in vocabulary[1:] if c in column_map]]
    columns = [0, *[column_map[c] for c in available[1:]]]
    name_lines = ocr.line_probabilities(name_bgr, columns, detect=True)
    class_lines = (
        ocr.line_probabilities(class_bgr, [0], detect=True) if class_bgr.size else []
    )

    matrices = [probs for _text, _conf, probs in name_lines]
    per_line = _stack_scores(matrices, roster, available)
    best_per_name = per_line.max(axis=0) if len(per_line) else np.full(len(roster), -np.inf)
    scores = np.where(np.isfinite(best_per_name), best_per_name, -1e4)
    line_best = per_line.max(axis=1) if per_line.size else np.array([-np.inf])
    best_line = int(np.argmax(line_best)) if len(line_best) else -1
    raw_text = name_lines[best_line][0] if 0 <= best_line < len(name_lines) else ""
    display_text = "".join(
        ch for ch in str(raw_text) if ch not in LABEL_CHARS and ch not in TAIL_CHARS
    )
    class_name = read_class_name(
        [text for text, _conf, _probs in class_lines[:-1]],
        class_lines[-1][0] if class_lines else "",
        sorted({entry.class_name for entry in roster if entry.class_name}),
    )
    return PaperEvidence(
        key=key,
        file_key=file_key,
        display_text=display_text,
        class_name=class_name,
        scores=scores,
    )


def _hungarian(cost: np.ndarray) -> list[int]:
    """Min-cost assignment for an n x m matrix (n <= m); returns column per row."""
    n, m = cost.shape
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


def assign_identities(
    papers: Sequence[PaperEvidence],
    roster: Sequence[RosterEntry],
) -> list[IdentityDecision]:
    """One-to-one paper -> student assignment via per-file class prior + Hungarian.

    A paper is auto-assigned only when its posterior under the class prior is at
    least AUTO_POSTERIOR and the CTC evidence reaches MIN_EVIDENCE. Everything
    else goes to review with up to three suggestions among the still-free
    students, so a duplicate scan or a weak reading can never silently steal a
    student's slot.
    """
    ids = [int(entry.student_id) for entry in roster]
    classes = np.array([entry.class_name for entry in roster])
    n = len(papers)
    if n == 0 or not roster:
        return []
    raw = np.stack([
        np.where(np.isfinite(np.asarray(paper.scores, dtype=np.float64)),
                 np.asarray(paper.scores, dtype=np.float64), -1e4)
        for paper in papers
    ])
    readings = [paper.class_name for paper in papers]

    # File-level class prior from class readings plus clear name winners.
    file_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for i, paper in enumerate(papers):
        if readings[i]:
            file_votes[paper.file_key][str(readings[i])] += 1
        post = _softmax(raw[i])
        winner = int(post.argmax())
        if post[winner] > 0.9:
            file_votes[paper.file_key][str(classes[winner])] += 1
    file_class: dict[str, str] = {}
    for file_key, votes in file_votes.items():
        top, count = votes.most_common(1)[0]
        if count >= FILE_CLASS_SHARE * sum(votes.values()):
            file_class[file_key] = top

    logits = raw.copy()
    priors: list[str | None] = []
    for i, paper in enumerate(papers):
        cls = readings[i] or file_class.get(paper.file_key)
        priors.append(cls)
        if cls is not None:
            penalty = READING_PENALTY if readings[i] else FILE_PENALTY
            logits[i] += np.where(classes == cls, 0.0, penalty)

    m = len(roster)
    accepted: dict[int, int] = {}
    accepted_posterior: dict[int, float] = {}
    free_students: set[int] = set(range(m))
    leave_cost = -math.log(LEAVE_PROBABILITY)
    for _round in range(MAX_ROUNDS):
        pending = [i for i in range(n) if i not in accepted]
        if not pending:
            break
        cols = sorted(free_students)
        sub = logits[np.ix_(pending, cols)]
        posts = np.stack([_softmax(row) for row in sub])
        # Leaving a paper unassigned costs -log(leave_p); Hungarian keeps it one-to-one.
        cost = np.full((len(pending), len(cols) + len(pending)), 1e6)
        cost[:, :len(cols)] = -np.log(np.clip(posts, 1e-12, 1))
        for r in range(len(pending)):
            cost[r, len(cols) + r] = leave_cost
        assignment = _hungarian(cost)
        newly = 0
        for r, i in enumerate(pending):
            a = assignment[r]
            if a >= len(cols):
                continue
            j = cols[a]
            if posts[r, a] >= AUTO_POSTERIOR and raw[i, j] >= MIN_EVIDENCE:
                accepted[i] = j
                accepted_posterior[i] = float(posts[r, a])
                free_students.discard(j)
                newly += 1
        if newly == 0:
            break

    decisions: list[IdentityDecision] = []
    cols = sorted(free_students)
    for i, paper in enumerate(papers):
        cls = priors[i]
        if i in accepted:
            j = accepted[i]
            posterior = accepted_posterior[i]
            decisions.append(IdentityDecision(
                key=paper.key,
                status="auto",
                student_id=ids[j],
                posterior=posterior,
                evidence=float(raw[i, j]),
                suggestions=((ids[j], posterior),),
                class_name=cls,
            ))
            continue
        row = _softmax(logits[i, cols]) if cols else np.array([])
        top = np.argsort(-row)[:3]
        decisions.append(IdentityDecision(
            key=paper.key,
            status="review",
            student_id=None,
            posterior=float(row.max()) if len(row) else 0.0,
            evidence=float(raw[i].max()) if len(raw[i]) else float("-inf"),
            suggestions=tuple((ids[cols[p]], float(row[p])) for p in top),
            class_name=cls,
        ))
    return decisions
