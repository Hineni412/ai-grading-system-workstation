from __future__ import annotations


SHARED_GRADING_RULES = """
Shared grading rules for all grading modes:
1. Objective items (choice/fill_blank/judgement/true_false/direct_answer) are all-or-nothing. Award full credit only when the student's effective answer matches canonical_answer or accepted_forms; otherwise award 0.
2. Student-discarded content must not be read or scored. This includes black smudging, crossed-out content, deletion lines, boxed-out content, overwritten discarded work, or X-marked abandoned answers. If a discarded old answer has a clear replacement answer written beside it, read and score the clear replacement answer.
3. Red teacher marks, red ticks/check marks, red circles, red comments, existing annotations, and any non-student grading traces are not student answer evidence. Never use them as proof that an answer is correct or complete.
4. For proof/reasoning questions, extract evidence_steps from the student's visible, non-discarded work before scoring, list missing_steps, and award each step only when the corresponding evidence is actually present.
5. Do not score non-objective questions as all-or-nothing. For tables, multi-blank constructed-response items, and sub-steps such as Q11(1), grade each blank/step independently; one blank wrong only loses that blank's points.
6. Only a final answer/conclusion, a diagram label, teacher mark, or fragmented equation alone cannot receive 2-3 points by default and cannot receive full proof/process credit. Award conclusion-only credit only when the rubric explicitly defines answer_only_max_score or a conclusion step. Blank, mostly blank, or no-valid-work answers must be scored conservatively.
""".strip()
