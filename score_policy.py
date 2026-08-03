from __future__ import annotations

from collections import OrderedDict
from typing import Any

OBJECTIVE_TYPES = {"choice", "fill_blank", "judgement", "true_false"}
SOLUTION_TYPES = {"calculation", "proof", "comprehensive", "solution", "general_solution"}
MAX_QUESTION_SCORE = 18


def enforce_integer_scores_by_type(
    questions: list[Any],
    *,
    target_total: int = 100,
    max_question_score: int = MAX_QUESTION_SCORE,
) -> None:
    """Force integer question scores.

    * Objective questions (choice / fill_blank): equal score within each type.
    * Solution / proof / comprehensive questions: preserve individual proportional
      scores (scaled to integers) — do NOT force same score across the group.
    """
    valid_questions = [q for q in questions if isinstance(q, dict)]
    if not valid_questions:
        return

    normalize_solution_question_types(valid_questions)
    score_by_question = _solve_global_question_scores(
        valid_questions,
        target_total=int(target_total),
        max_question_score=int(max_question_score),
    )
    for question, score in zip(valid_questions, score_by_question):
        apply_integer_question_score(question, score)


def _solve_global_question_scores(
    questions: list[dict[str, Any]],
    *,
    target_total: int,
    max_question_score: int,
) -> list[int]:
    """Find the closest globally feasible integer allocation.

    Objective questions share one score within each normalized type. All other
    questions are independent. The search may move the objective/solution
    budget boundary when the AI-proposed boundary is not mathematically
    feasible.
    """
    if not questions:
        return []
    if target_total < len(questions) or target_total > len(questions) * max_question_score:
        raise ValueError(
            f"Cannot allocate {target_total} points across {len(questions)} questions "
            f"with per-question range 1..{max_question_score}."
        )

    raw_scores = [max(0.0, _safe_float(question.get("max_score"), 0.0)) for question in questions]
    raw_total = sum(raw_scores)
    if raw_total <= 0:
        ideals = [target_total / len(questions)] * len(questions)
    else:
        ideals = [score / raw_total * target_total for score in raw_scores]

    objective_indexes: "OrderedDict[str, list[int]]" = OrderedDict()
    solution_units: list[list[int]] = []
    for index, question in enumerate(questions):
        normalized_type = _normalize_type(str(question.get("question_type") or "comprehensive"))
        if normalized_type in OBJECTIVE_TYPES:
            objective_indexes.setdefault(normalized_type, []).append(index)
        else:
            solution_units.append([index])
    # Building process questions from fewer to more independently scorable
    # steps lets the search discard an imbalanced partial allocation early,
    # instead of hoping it remains among the nearest 500 raw-model paths.
    solution_units.sort(key=lambda indexes: _scorable_step_count(questions[indexes[0]]))
    units = list(objective_indexes.values()) + solution_units
    candidate_limit = (
        1
        if not objective_indexes
        and len(
            {
                _scorable_step_count(questions[indexes[0]])
                for indexes in solution_units
            }
        ) <= 1
        else 500
    )

    # Keep several candidates per total.  A numerically closest allocation can
    # still violate the paper's teaching hierarchy, so retaining only one path
    # would discard the closest valid alternative before the final check.
    states: dict[int, list[tuple[float, list[int]]]] = {0: [(0.0, [])]}
    for indexes in units:
        weight = len(indexes)
        next_states: dict[int, list[tuple[float, list[int]]]] = {}
        for total_so_far, candidates in states.items():
            for cost_so_far, path_so_far in candidates:
                for score in range(1, max_question_score + 1):
                    new_total = total_so_far + weight * score
                    if new_total > target_total:
                        break
                    cost = cost_so_far + sum(
                        (score - ideals[index]) ** 2 for index in indexes
                    )
                    next_path = [*path_so_far, score]
                    if _partial_score_path_is_balanced(
                        questions,
                        units,
                        next_path,
                    ):
                        next_states.setdefault(new_total, []).append(
                            (cost, next_path)
                        )
        states = {
            total: sorted(candidates, key=lambda item: item[0])[:candidate_limit]
            for total, candidates in next_states.items()
        }

    solved_candidates = states.get(target_total, [])
    solved: tuple[float, list[int]] | None = None
    for candidate in sorted(solved_candidates, key=lambda item: item[0]):
        proposed = _scores_from_unit_path(
            len(questions),
            units,
            candidate[1],
        )
        if _respects_paper_score_quality(questions, proposed):
            solved = candidate
            break
    if solved is None:
        counts = ", ".join(
            f"{qtype}:{len(indexes)}题" for qtype, indexes in objective_indexes.items()
        )
        raise ValueError(
            f"Cannot allocate {target_total} integer points while preserving the paper score "
            f"hierarchy, same score per objective type, and max {max_question_score} pts each. "
            f"Objective groups: {counts or 'none'}."
        )

    return _scores_from_unit_path(len(questions), units, solved[1])


def _scores_from_unit_path(
    question_count: int,
    units: list[list[int]],
    path: list[int],
) -> list[int]:
    result = [0] * question_count
    for indexes, score in zip(units, path, strict=True):
        for index in indexes:
            result[index] = int(score)
    return result


def _partial_score_path_is_balanced(
    questions: list[dict[str, Any]],
    units: list[list[int]],
    path: list[int],
) -> bool:
    objective_scores: list[int] = []
    choice_score: int | None = None
    fill_score: int | None = None
    solution_rows: list[tuple[int, int]] = []
    for indexes, score in zip(units, path):
        question = questions[indexes[0]]
        qtype = _normalize_type(str(question.get("question_type") or ""))
        if qtype in OBJECTIVE_TYPES:
            objective_scores.append(int(score))
            if qtype == "choice":
                choice_score = int(score)
            elif qtype == "fill_blank":
                fill_score = int(score)
        else:
            solution_rows.append((_scorable_step_count(question), int(score)))

    if choice_score is not None and fill_score is not None:
        if choice_score > fill_score or choice_score * 2 < fill_score:
            return False
    if objective_scores and solution_rows:
        if any(score < max(objective_scores) for _count, score in solution_rows):
            return False
    for left_count, left_score in solution_rows:
        for right_count, right_score in solution_rows:
            if left_count > right_count and left_score < right_score:
                return False
    return True


def _respects_paper_score_quality(
    questions: list[dict[str, Any]],
    scores: list[int],
) -> bool:
    type_scores: dict[str, list[int]] = {}
    solution_rows: list[tuple[int, int]] = []
    for question, score in zip(questions, scores, strict=True):
        qtype = _normalize_type(str(question.get("question_type") or ""))
        type_scores.setdefault(qtype, []).append(int(score))
        if qtype in SOLUTION_TYPES or qtype not in OBJECTIVE_TYPES:
            solution_rows.append((_scorable_step_count(question), int(score)))

    choice = type_scores.get("choice", [])
    fill = type_scores.get("fill_blank", [])
    if choice and fill:
        choice_score = choice[0]
        fill_score = fill[0]
        if choice_score > fill_score or choice_score * 2 < fill_score:
            return False

    objective_scores = [
        score
        for qtype, values in type_scores.items()
        if qtype in OBJECTIVE_TYPES
        for score in values
    ]
    solution_scores = [score for _count, score in solution_rows]
    if objective_scores and solution_scores and max(objective_scores) > min(solution_scores):
        return False

    # More independently scorable work must not receive a lower question score.
    # Equal step counts remain free to differ because mathematical difficulty is
    # not reducible to a local step count.
    for left_count, left_score in solution_rows:
        for right_count, right_score in solution_rows:
            if left_count > right_count and left_score < right_score:
                return False
    return True


def _scorable_step_count(question: dict[str, Any]) -> int:
    parts = question.get("parts")
    if not isinstance(parts, list):
        return 0
    return sum(
        len(part.get("steps") or [])
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("steps"), list)
    )


def _apply_solution_group_scores(
    group_questions: list[dict],
    group_scores: dict[str, int],
    qtype: str,
    target_total: int,
    all_groups: "OrderedDict[str, list[dict[str, Any]]]",
) -> None:
    """Scale each solution-type question to an integer, preserving their relative weights."""
    # Determine the total budget for all solution questions combined
    sol_budget = sum(
        group_scores[qt] * len(qs)
        for qt, qs in all_groups.items()
        if _normalize_type(qt) in SOLUTION_TYPES
    )
    # Within this group, allocate proportionally
    raw_scores = [max(0.0, _safe_float(q.get("max_score"), 0.0)) for q in group_questions]
    raw_sum = sum(raw_scores)
    # Budget for this specific qtype group
    group_budget = group_scores[qtype] * len(group_questions)
    if raw_sum <= 0:
        per = max(1, group_budget // max(len(group_questions), 1))
        int_scores = [per] * len(group_questions)
    else:
        int_scores = _allocate_integer_scores(raw_scores, group_budget)

    for question, new_score in zip(group_questions, int_scores):
        apply_integer_question_score(question, max(1, new_score))


def normalize_solution_question_types(questions: list[Any]) -> None:
    """Correct only high-confidence type mistakes without collapsing all geometry into proof."""
    for question in questions:
        if not isinstance(question, dict):
            continue
        qtype = _normalize_type(str(question.get("question_type") or ""))
        if qtype not in {"calculation", "comprehensive", "solution", "general_solution"}:
            continue
        if _looks_like_proof_question(question):
            question["question_type"] = "proof"
        elif _looks_like_calculation_question(question):
            question["question_type"] = "calculation"
        elif qtype in {"solution", "general_solution"}:
            question["question_type"] = "comprehensive"


def apply_integer_question_score(question: dict[str, Any], new_score: int) -> None:
    new_score = int(round(float(new_score)))
    question["max_score"] = new_score
    _integerize_parts(question, new_score)
    _integerize_deductions(question, new_score)


def _solve_group_scores(
    groups: "OrderedDict[str, list[dict[str, Any]]]",
    target_total: int,
    *,
    max_question_score: int,
) -> dict[str, int]:
    """Return a per-qtype representative score used for budget allocation.

    For objective types (choice/fill_blank): the returned score is applied
    equally to every question in the group.
    For solution types: the returned score is a *per-question average* used
    only to determine the group's proportional share of target_total;
    actual per-question values are set by _apply_solution_group_scores.
    """
    group_items = list(groups.items())
    current_total = sum(_safe_float(q.get("max_score"), 0.0) for _, qs in group_items for q in qs)
    total_question_count = sum(len(qs) for _, qs in group_items)

    # Split into objective vs solution
    obj_groups = [(qt, qs) for qt, qs in group_items if _normalize_type(qt) in OBJECTIVE_TYPES]
    sol_groups = [(qt, qs) for qt, qs in group_items if _normalize_type(qt) not in OBJECTIVE_TYPES]

    obj_raw = sum(_safe_float(q.get("max_score"), 0.0) for _, qs in obj_groups for q in qs)
    sol_raw = sum(_safe_float(q.get("max_score"), 0.0) for _, qs in sol_groups for q in qs)
    raw_sum = (obj_raw + sol_raw) if (obj_raw + sol_raw) > 0 else 1.0

    sol_target = int(round(sol_raw / raw_sum * target_total)) if sol_groups else 0
    obj_target = target_total - sol_target

    # --- Objective groups: find equal integer score per type via DP ---
    if obj_groups:
        obj_current = obj_raw if obj_raw > 0 else 1.0
        obj_ideals: dict[str, float] = {}
        for qtype, qs in obj_groups:
            group_current = sum(_safe_float(q.get("max_score"), 0.0) for q in qs)
            obj_ideals[qtype] = (group_current / obj_current * obj_target) / max(len(qs), 1)

        obj_states: dict[int, list[tuple[float, dict[str, int]]]] = {0: [(0.0, {})]}
        for qtype, qs in obj_groups:
            count = len(qs)
            ideal = obj_ideals[qtype]
            next_states: dict[int, list[tuple[float, dict[str, int]]]] = {}
            max_s = max(1, min(int(max_question_score), obj_target // count if count else 1))
            for total_so_far, candidates in obj_states.items():
                for cost_so_far, path_so_far in candidates:
                    for score in range(1, max_s + 1):
                        new_total = total_so_far + count * score
                        if new_total > obj_target:
                            continue
                        cost = cost_so_far + ((score - ideal) ** 2) * count
                        next_path = dict(path_so_far)
                        next_path[qtype] = score
                        next_states.setdefault(new_total, []).append((cost, next_path))
            obj_states = {
                total: sorted(candidates, key=lambda item: item[0])[:500]
                for total, candidates in next_states.items()
            }

        obj_valid = [
            c for c in obj_states.get(obj_target, [])
            if _respects_objective_solution_order(c[1])
        ]
        if not obj_valid:
            counts = ", ".join(f"{qt}:{len(qs)}题" for qt, qs in obj_groups)
            raise ValueError(
                f"Cannot satisfy: obj total={obj_target}, integer per question, same score per type, "
                f"max {max_question_score} pts each. Current obj groups: {counts}. Adjust question counts."
            )

        obj_scores: dict[str, int] = sorted(obj_valid, key=lambda item: item[0])[0][1]
    else:
        obj_scores = {}

    # --- Solution groups: compute per-type average for budget tracking ---
    sol_scores: dict[str, int] = {}
    for qtype, qs in sol_groups:
        group_raw = sum(_safe_float(q.get("max_score"), 0.0) for q in qs)
        if group_raw <= 0 or sol_raw <= 0:
            avg = max(1, sol_target // max(len(qs), 1))
        else:
            avg = max(1, int(round(sol_target * (group_raw / sol_raw) / max(len(qs), 1))))
        sol_scores[qtype] = avg

    return {**obj_scores, **sol_scores}


def _respects_objective_solution_order(group_scores: dict[str, int]) -> bool:
    choice = _score_for_type(group_scores, "choice")
    fill = _score_for_type(group_scores, "fill_blank")
    if choice is not None and fill is not None:
        if choice > fill or choice * 2 < fill:
            return False
    objective = [
        score
        for qtype, score in group_scores.items()
        if _normalize_type(qtype) in OBJECTIVE_TYPES
    ]
    solution = [
        score
        for qtype, score in group_scores.items()
        if _normalize_type(qtype) not in OBJECTIVE_TYPES
    ]
    return not objective or not solution or max(objective) <= min(solution)


def _score_for_type(group_scores: dict[str, int], target_type: str) -> int | None:
    for qtype, score in group_scores.items():
        if _normalize_type(qtype) == target_type:
            return score
    return None


def _normalize_type(qtype: str) -> str:
    raw = str(qtype or "").strip().lower()
    if raw in {"blank", "short_answer", "fill-in", "fill_in_blank"}:
        return "fill_blank"
    if raw in {"select", "single_choice", "multiple_choice"}:
        return "choice"
    if raw in {"solve", "解答题", "计算题"}:
        return "calculation"
    if raw in {"证明", "证明题"}:
        return "proof"
    if raw in {"综合题", "一般解答题"}:
        return "comprehensive"
    return raw


def _looks_like_proof_question(question: dict[str, Any]) -> bool:
    text_parts: list[str] = []
    # Do not inspect generated deduction_policy/proof_obligations here. Those
    # contain generic process-scoring text for every solution question, and would
    # incorrectly turn geometry calculations into proof questions.
    for key in ("stem_summary", "canonical_answer", "knowledge_name", "knowledge_label"):
        text_parts.append(str(question.get(key) or ""))
    text = " ".join(text_parts)

    # Calculation markers should win only when the task is purely asking for a
    # numeric/algebraic result. If the same stem explicitly asks to prove or
    # explain a conclusion, treat it as proof/comprehensive instead.
    proof_first_patterns = [
        "证明",
        "求证",
        "证得",
        "证出",
        "请证",
        "给出证明",
        "证明过程",
        "补全证明",
        "补充完整",
        "说明理由",
        "说明.*成立",
        "说明.*为什么",
        "使得.*推出",
        "能够推出",
        "推出.*成立",
        "推导.*结论",
        "判断.*并说明",
    ]
    if any(re_search(pattern, text) for pattern in proof_first_patterns):
        return True

    calculation_first_markers = [
        "求角",
        "求∠",
        "求角度",
        "求长度",
        "求线段",
        "求周长",
        "求面积",
        "求值",
        "计算",
        "化简",
        "解方程",
        "求出",
        "度数",
        "长度",
    ]
    if any(marker in text for marker in calculation_first_markers):
        return False

    proof_goal_markers = ["全等", "相似", "平行", "垂直", "相等", "互补", "互余"]
    proof_action_markers = ["判定", "推出", "成立", "说明"]
    if any(goal in text for goal in proof_goal_markers) and any(action in text for action in proof_action_markers):
        return True
    return False


def _looks_like_calculation_question(question: dict[str, Any]) -> bool:
    text = " ".join(
        str(question.get(key) or "")
        for key in ("stem_summary", "canonical_answer", "knowledge_name", "knowledge_label")
    )
    markers = ["求角", "求∠", "求角度", "求长度", "求线段", "求周长", "求面积", "求值", "计算", "化简", "解方程", "度数"]
    return any(marker in text for marker in markers)


def re_search(pattern: str, text: str) -> bool:
    try:
        import re

        return re.search(pattern, text) is not None
    except Exception:
        return pattern in text


def _collect_nested_text(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _collect_nested_text(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _collect_nested_text(nested, output)
    elif value is not None:
        output.append(str(value))


def _integerize_parts(question: dict[str, Any], question_score: int) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        return

    part_scores = _allocate_integer_scores(
        [_safe_float(part.get("part_score"), 0.0) if isinstance(part, dict) else 0.0 for part in parts],
        question_score,
    )
    for part, part_score in zip(parts, part_scores):
        if not isinstance(part, dict):
            continue
        part["part_score"] = part_score
        _integerize_steps(part, part_score)


def _integerize_steps(part: dict[str, Any], part_score: int) -> None:
    steps = part.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    step_scores = _allocate_integer_scores(
        [_safe_float(step.get("step_score"), 0.0) if isinstance(step, dict) else 0.0 for step in steps],
        part_score,
    )
    for step, step_score in zip(steps, step_scores):
        if isinstance(step, dict):
            step["step_score"] = step_score


def _integerize_deductions(question: dict[str, Any], question_score: int) -> None:
    policies = question.get("deduction_policy")
    if isinstance(policies, list):
        for policy in policies:
            if isinstance(policy, dict) and "max_deduction" in policy:
                value = int(round(_safe_float(policy.get("max_deduction"), 0.0)))
                policy["max_deduction"] = max(0, min(question_score, value))
    obligations = question.get("proof_obligations")
    if isinstance(obligations, list):
        for obligation in obligations:
            if isinstance(obligation, dict) and "weight" in obligation:
                value = int(round(_safe_float(obligation.get("weight"), 0.0)))
                obligation["weight"] = max(0, min(question_score, value))


def _allocate_integer_scores(weights: list[float], total: int) -> list[int]:
    total = int(total)
    if not weights:
        return []
    if total <= 0:
        return [0 for _ in weights]

    positive_sum = sum(max(0.0, weight) for weight in weights)
    if positive_sum <= 0:
        base = total // len(weights)
        scores = [base for _ in weights]
        for idx in range(total - sum(scores)):
            scores[idx % len(scores)] += 1
        return scores

    exact = [max(0.0, weight) / positive_sum * total for weight in weights]
    scores = [int(value) for value in exact]
    remainder = total - sum(scores)
    order = sorted(range(len(weights)), key=lambda idx: exact[idx] - scores[idx], reverse=True)
    for idx in order[:remainder]:
        scores[idx] += 1
    return scores


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
