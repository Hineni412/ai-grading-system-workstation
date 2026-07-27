from __future__ import annotations


SHARED_GRADING_RULES = """
Shared grading rules for all grading modes:
1. Objective items (choice/fill_blank/judgement/true_false/direct_answer) are all-or-nothing. Award full credit only when the student's effective answer matches canonical_answer or accepted_forms; otherwise award 0.
2. Student-discarded content must not be read or scored. This includes black smudging, crossed-out content, deletion lines, boxed-out content, overwritten discarded work, or X-marked abandoned answers. If a discarded old answer has a clear replacement answer written beside it, read and score the clear replacement answer.
3. Red teacher marks, red ticks/check marks, red circles, red comments, existing annotations, and any non-student grading traces are not student answer evidence. Never use them as proof that an answer is correct or complete.
4. Follow rubric.parts[].response_mode for every sub-question. Never inherit the parent question's process requirement onto a different response mode.
5. For short_answer_points, award each independent answer/blank/result step separately. A correct answer item can receive its full step score without proof or derivation.
6. For visual_construction, compare the student's drawing with the provided standard-answer image and visual_requirements. Do not require a written proof unless the rubric explicitly defines a separate process_required step.
7. Only process_required parts require proof/reasoning evidence. Extract evidence_steps, list missing_steps, and award each process step only when its evidence is actually present.
8. Do not score non-objective questions as all-or-nothing. For tables, multi-blank constructed-response items, and sub-steps such as Q11(P1), grade each blank/step independently; one blank wrong only loses that blank's points.
9. For process_required parts, a final answer/conclusion, diagram label, teacher mark, or fragmented equation alone cannot receive full proof/process credit. Apply answer_only_max_score only to process_required parts.
10. Copying only the sub-question stem or adding only a tick/check without required proof work is not valid process evidence. This rule must not erase a valid answer for short_answer_points or exact_objective.
""".strip()
