# -*- coding: utf-8 -*-
"""Roster-constrained local identity extraction (scan_identity)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import scan_identity
from scan_identity import (
    MIN_EVIDENCE,
    IdentityDecision,
    PaperEvidence,
    RosterEntry,
    assign_identities,
    build_vocabulary,
    read_class_name,
    roster_scores,
)
from scanner import (
    PageRecord,
    Scanner,
    _identity_suggestions,
    _pair_pdf_pages_by_template_parity,
)


def _probs(vocab: list[str], chars: str, *, conf: float = 0.9) -> np.ndarray:
    """(T, K) probabilities spelling ``chars`` via blank-separated frames."""
    columns: list[int] = [0]
    for char in chars:
        index = vocab.index(char) if char else 0
        columns += [index, index, 0]
    probs = np.full((len(columns), len(vocab)), (1 - conf) / (len(vocab) - 1))
    for t, column in enumerate(columns):
        probs[t, column] = conf
    return probs


def _paper(
    key: str,
    scores: list[float],
    *,
    file_key: str = "paper.pdf",
    class_name: str | None = None,
) -> PaperEvidence:
    return PaperEvidence(
        key=key,
        file_key=file_key,
        display_text="",
        class_name=class_name,
        scores=np.asarray(scores, dtype=np.float64),
    )


def _ref_ctc(log_probs: np.ndarray, label: list[int]) -> float:
    """Scalar reference CTC forward pass (same recurrence as the prototype)."""
    T = log_probs.shape[0]
    ext = [0]
    for c in label:
        ext += [c, 0]
    S = len(ext)
    if T == 0:
        return -np.inf
    alpha = np.full(S, -np.inf)
    alpha[0] = log_probs[0, 0]
    if S > 1:
        alpha[1] = log_probs[0, ext[1]]
    for t in range(1, T):
        prev = alpha
        alpha = np.full(S, -np.inf)
        for s in range(S):
            best = prev[s]
            if s >= 1:
                best = np.logaddexp(best, prev[s - 1])
            if s >= 2 and ext[s] != 0 and ext[s] != ext[s - 2]:
                best = np.logaddexp(best, prev[s - 2])
            alpha[s] = best + log_probs[t, ext[s]]
    return float(np.logaddexp(alpha[S - 1], alpha[S - 2]) if S > 1 else alpha[S - 1])


def test_ctc_logprob_all_matches_scalar_reference() -> None:
    rng = np.random.default_rng(0)
    probs = rng.random((24, 12))
    probs /= probs.sum(axis=1, keepdims=True)
    log_probs = np.log(np.clip(probs, 1e-8, 1.0))
    labels = [[1, 2], [3], [4, 4, 5], [6, 7, 8, 9], []]
    got = scan_identity._ctc_logprob_all(log_probs, labels)
    expected = [_ref_ctc(log_probs, label) for label in labels]
    assert np.allclose(got, expected, atol=1e-6)


def test_roster_scores_prefers_the_spelled_name() -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    vocab = build_vocabulary(roster)
    scores = roster_scores([_probs(vocab, "张三")], roster, vocab)
    assert int(np.argmax(scores)) == 0
    assert scores[0] - scores[1] > 5


def test_roster_scores_strips_label_prefix_and_tail() -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    vocab = build_vocabulary(roster)
    scores = roster_scores([_probs(vocab, "姓名：张三班")], roster, vocab)
    assert int(np.argmax(scores)) == 0
    assert scores[0] > -5


@pytest.mark.parametrize(
    ("box_texts", "band_text", "expected"),
    [
        (["班级：809"], "", "9"),
        (["班级：8(10)"], "", "10"),
        ([], "八（10）班", "10"),
        ([], "309", "9"),
        ([], "704", None),
        ([], "80", None),
    ],
)
def test_read_class_name(box_texts, band_text, expected) -> None:
    assert read_class_name(box_texts, band_text, ["9", "10"]) == expected


def test_two_papers_never_auto_assign_one_student() -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    papers = [_paper("a", [10.0, -25.0]), _paper("b", [9.0, -25.0])]
    decisions = assign_identities(papers, roster)
    assert [d.status for d in decisions].count("auto") == 1
    auto = next(d for d in decisions if d.status == "auto")
    assert auto.student_id == 1
    review = next(d for d in decisions if d.status == "review")
    assert review.student_id is None
    assert [s[0] for s in review.suggestions] == [2]


def test_class_reading_blocks_cross_class_name() -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "10")]
    papers = [_paper("a", [0.0, 4.0], class_name="9")]
    (decision,) = assign_identities(papers, roster)
    assert decision.status == "review"
    assert decision.student_id is None
    assert decision.suggestions[0][0] == 1


def test_elimination_assigns_last_free_student_only_with_evidence() -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    # Weak paper prefers student 1, but student 1 is taken; leftover evidence for
    # student 2 is above MIN_EVIDENCE -> elimination auto-assigns it.
    papers = [_paper("a", [30.0, -30.0]), _paper("b", [-15.0, -18.0])]
    decisions = assign_identities(papers, roster)
    assert [d.status for d in decisions] == ["auto", "auto"]
    assert decisions[1].student_id == 2
    # Same shape with evidence below MIN_EVIDENCE -> review with suggestions.
    papers = [_paper("a", [30.0, -30.0]), _paper("b", [-15.0, MIN_EVIDENCE - 10.0])]
    decisions = assign_identities(papers, roster)
    assert decisions[0].status == "auto"
    assert decisions[1].status == "review"
    assert decisions[1].student_id is None
    assert [s[0] for s in decisions[1].suggestions] == [2]
    assert decisions[1].suggestions[0][1] == pytest.approx(1.0)


def test_duplicate_roster_names_split_posterior_into_review() -> None:
    roster = [
        RosterEntry(1, "张三", "9"),
        RosterEntry(2, "张三", "9"),
        RosterEntry(3, "李四", "9"),
    ]
    papers = [_paper("a", [10.0, 10.0, -10.0])]
    (decision,) = assign_identities(papers, roster)
    assert decision.status == "review"
    assert decision.student_id is None
    assert {s[0] for s in decision.suggestions[:2]} == {1, 2}
    assert decision.suggestions[0][1] == pytest.approx(0.5, abs=1e-3)


class _FakeIdentityOcr:
    """Stand-in for MineruOcr: character map + scripted line_probabilities."""

    def __init__(self, scripted_lines: list[list[tuple[str, float, np.ndarray]]]) -> None:
        self._lines = scripted_lines
        self.calls = 0

    def character_columns(self, chars: list[str]) -> dict[str, int]:
        return {char: index for index, char in enumerate(chars) if index > 0}

    def line_probabilities(
        self,
        image: np.ndarray,
        columns: list[int],
        *,
        detect: bool = True,
    ) -> list[tuple[str, float, np.ndarray]]:
        lines = self._lines[self.calls]
        self.calls += 1
        return lines


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[bytes]]] = []

    def text_from_images(self, prompt: str, images: list[bytes], **_kwargs: object) -> str:
        self.calls.append((prompt, images))
        raise AssertionError("remote model must not be called on the roster path")


def test_analyze_assigns_roster_identity_without_remote_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    vocab = build_vocabulary(roster)
    strong = _probs(vocab, "姓名：张三班")
    weak = np.full((12, len(vocab)), 1.0 / len(vocab))
    fake = _FakeIdentityOcr([
        [("张三", 0.9, strong)],          # paper 1 name box
        [("9班", 0.8, np.zeros((2, 1)))],  # paper 1 class band
        [("", 0.1, weak)],                # paper 2 name box
        [("", 0.1, np.zeros((2, 1)))],    # paper 2 class band
    ])
    fake_local_ocr = types.ModuleType("local_ocr")
    fake_local_ocr.get_local_ocr = lambda: fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "local_ocr", fake_local_ocr)

    for name in ("p1a.jpg", "p1b.jpg", "p2a.jpg", "p2b.jpg"):
        Image.new("RGB", (800, 1100), "white").save(tmp_path / name)

    client = _RecordingClient()
    scanner = Scanner(
        tmp_path,
        client,
        enhance_images=False,
        front_page_parity="odd",
        name_region={"x": 50, "y": 50, "w": 200, "h": 40},
    )
    analysis = scanner.analyze(
        students=[
            {"id": 1, "name": "张三", "class_name": "9"},
            {"id": 2, "name": "李四", "class_name": "9"},
        ]
    )

    assert client.calls == []
    assert analysis.identity == {
        "method": "roster_local",
        "auto": 1,
        "needs_confirmation": 1,
        "model_requests": 0,
    }
    assert len(analysis.groups) == 1
    assert len(analysis.issues) == 1

    group = analysis.groups[0]
    assert group.student_id == 1
    assert group.student_name == "张三"
    assert group.match_method == "roster"
    assert group.match_score == pytest.approx(1.0, abs=1e-3)
    assert group.detected_name == "张三"
    assert group.detected_class_name == "9"

    issue = analysis.issues[0]
    assert issue.issue_type == "needs_confirmation"
    assert issue.suggested_student_id == 2
    assert issue.suggested_student_name == "李四"
    assert issue.suggested_students == [
        {"student_id": 2, "student_name": "李四", "class_name": "9", "score": pytest.approx(1.0)}
    ]


_UNSET = object()


def _identity_record(
    decision: IdentityDecision,
    roster: list[RosterEntry],
    *,
    display_text: str = "",
    evidence_class: object = _UNSET,
) -> dict:
    by_id = {entry.student_id: entry for entry in roster}
    return {
        "evidence": PaperEvidence(
            key=decision.key,
            file_key="f.pdf",
            display_text=display_text,
            class_name=decision.class_name if evidence_class is _UNSET else evidence_class,
            scores=np.zeros(len(roster)),
        ),
        "decision": decision,
        "suggestions": _identity_suggestions(decision, by_id),
        "student": by_id.get(decision.student_id) if decision.student_id is not None else None,
    }


def test_pdf_parity_pairing_uses_roster_decisions(tmp_path: Path) -> None:
    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "9")]
    paths = {}
    for name in ("f_p1.png", "f_p2.png", "f_p3.png"):
        path = tmp_path / name
        Image.new("RGB", (800, 1100), "white").save(path)
        paths[name] = path
    pages = [
        PageRecord(image_path=paths["f_p1.png"], source_file="f.pdf", page_number=1),
        PageRecord(image_path=paths["f_p2.png"], source_file="f.pdf", page_number=2),
        PageRecord(image_path=paths["f_p3.png"], source_file="f.pdf", page_number=3),
    ]
    auto = _identity_record(
        IdentityDecision("k1", "auto", 1, 0.99, -1.0, ((1, 0.99),), "9"),
        roster,
        display_text="张三",
    )
    review = _identity_record(
        IdentityDecision("k2", "review", None, 0.6, -3.0, ((2, 0.6), (1, 0.3)), "9"),
        roster,
    )
    analysis = _pair_pdf_pages_by_template_parity(
        "f.pdf",
        pages,
        {},
        "odd",
        roster_identity={
            str(paths["f_p1.png"]): auto,
            str(paths["f_p3.png"]): review,
        },
    )
    assert len(analysis.groups) == 1 and len(analysis.issues) == 1
    group = analysis.groups[0]
    assert group.student_id == 1 and group.match_method == "roster"
    assert group.detected_class_name == "9"
    # Third page is a front without a back: structural issue still carries the
    # roster suggestions so the teacher can pick the student while resolving it.
    issue = analysis.issues[0]
    assert issue.issue_type == "missing_back"
    assert issue.suggested_students == [
        {"student_id": 2, "student_name": "李四", "class_name": "9", "score": 0.6},
        {"student_id": 1, "student_name": "张三", "class_name": "9", "score": 0.3},
    ]


def test_detected_class_reports_only_the_papers_own_reading() -> None:
    """File-majority prior class must not show up as the paper's 卷面班级."""
    from scanner import _roster_decision_paper

    roster = [RosterEntry(1, "张三", "9"), RosterEntry(2, "李四", "10")]
    papers = [
        _paper("a", [20.0, -20.0], class_name="9"),
        _paper("b", [-5.0, -30.0]),
    ]
    decisions = {d.key: d for d in assign_identities(papers, roster)}
    assert decisions["b"].class_name == "9"  # file-majority prior applied internally
    issue = _roster_decision_paper(
        _identity_record(decisions["b"], roster, evidence_class=None),
        issue_id="f_p3_4",
        front_image=Path("f_p3.png"),
        back_image=Path("f_p4.png"),
        source_label="f.pdf 第 3-4 页",
    )
    assert issue.detected_class_name == ""
    group = _roster_decision_paper(
        _identity_record(decisions["a"], roster, evidence_class="9"),
        issue_id="f_p1_2",
        front_image=Path("f_p1.png"),
        back_image=Path("f_p2.png"),
        source_label="f.pdf 第 1-2 页",
    )
    assert group.detected_class_name == "9"
