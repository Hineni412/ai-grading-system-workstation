from __future__ import annotations

import copy
from typing import Any

from score_policy import MAX_QUESTION_SCORE, enforce_integer_scores_by_type

from .contract import is_simple_objective_question


def collect_score_consistency_issues(
    payload: dict[str, Any],
    *,
    expected_total: float | None = 100.0,
) -> list[str]:
    """Return teacher-readable score conflicts without changing the payload."""

    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    if not isinstance(questions, list) or not questions:
        return ["评分标准缺少可校验的题目分值"]

    issues: list[str] = []
    question_total = 0.0
    for question_index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            issues.append(f"第 {question_index} 道题的分值结构无效")
            continue
        question_id = str(
            question.get("question_id") or f"第{question_index}题"
        ).strip()
        question_score = _score_number(question.get("max_score"))
        if question_score is None:
            issues.append(f"{question_id} 缺少有效的大题分值")
            continue
        question_total += question_score

        parts = question.get("parts")
        if not isinstance(parts, list) or not parts:
            issues.append(f"{question_id} 缺少可校验的小问分值")
            continue
        part_total = 0.0
        for part_index, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                issues.append(f"{question_id} 的第 {part_index} 小问分值结构无效")
                continue
            part_id = str(
                part.get("part_id") or f"第{part_index}小问"
            ).strip()
            part_score = _score_number(part.get("part_score"))
            if part_score is None:
                issues.append(f"{question_id}/{part_id} 缺少有效的小问分值")
                continue
            part_total += part_score

            steps = part.get("steps")
            if not isinstance(steps, list) or not steps:
                issues.append(f"{question_id}/{part_id} 缺少可校验的评分步骤分值")
                continue
            step_scores = [
                _score_number(step.get("step_score"))
                for step in steps
                if isinstance(step, dict)
            ]
            if len(step_scores) != len(steps) or any(
                value is None for value in step_scores
            ):
                issues.append(f"{question_id}/{part_id} 存在无效的评分步骤分值")
                continue
            comparable_scores = [
                float(value) for value in step_scores if value is not None
            ]
            if (
                len(comparable_scores) > 1
                and max(comparable_scores) - min(comparable_scores) > 2.01
            ):
                issues.append(
                    f"{question_id}/{part_id} 同一小问内的评分步骤分差超过2分"
                )
            step_total = sum(
                comparable_scores
            )
            if not _scores_equal(step_total, part_score):
                issues.append(
                    f"{question_id}/{part_id} 的步骤分合计为 {_format_score(step_total)} 分，"
                    f"与小问分值 {_format_score(part_score)} 分不一致"
                    f"（相差 {_format_score(abs(step_total - part_score))} 分）"
                )
        if not _scores_equal(part_total, question_score):
            issues.append(
                f"{question_id} 的小问分合计为 {_format_score(part_total)} 分，"
                f"与大题分值 {_format_score(question_score)} 分不一致"
                f"（相差 {_format_score(abs(part_total - question_score))} 分）"
            )

    if expected_total is not None and not _scores_equal(
        question_total,
        float(expected_total),
    ):
        issues.append(
            f"整卷各题合计为 {_format_score(question_total)} 分，"
            f"与设定总分 {_format_score(float(expected_total))} 分不一致"
            f"（相差 {_format_score(abs(question_total - float(expected_total)))} 分）"
        )
    return list(dict.fromkeys(issues))


def _score_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if score >= 0 else None


def _scores_equal(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= 0.01


def _format_score(value: float) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _validate_exact_score_allocation_payload(
    score_data: dict[str, Any],
    structure_summary: list[dict[str, Any]],
) -> None:
    rows = _validated_score_allocation_rows(score_data, structure_summary)
    total_score = 0
    objective_scores: dict[str, int] = {}
    for expected, actual in rows:
        question_score = _strict_positive_score(actual.get("max_score"))
        total_score += question_score
        question_type = str(expected.get("question_type") or "").strip()
        if is_simple_objective_question(expected):
            previous = objective_scores.setdefault(question_type, question_score)
            if previous != question_score:
                raise ValueError("AI 统一配分未保持同类型客观题同分。")

        part_total = 0
        for actual_part in actual["parts"]:
            part_score = _strict_positive_score(actual_part.get("part_score"))
            part_total += part_score
            step_total = sum(
                _strict_positive_score(item.get("step_score"))
                for item in actual_part["steps"]
            )
            step_scores = [
                _strict_positive_score(item.get("step_score"))
                for item in actual_part["steps"]
            ]
            if len(step_scores) > 1 and max(step_scores) - min(step_scores) > 2:
                raise ValueError("AI 统一配分中同一小问的评分步骤分差超过2分。")
            if step_total != part_score:
                raise ValueError("AI 统一配分的步骤分之和不等于分问分。")
        if part_total != question_score:
            raise ValueError("AI 统一配分的分问分之和不等于题目分。")
    if total_score != 100:
        raise ValueError("AI 统一配分总分不是 100。")


def _validated_score_allocation_rows(
    score_data: dict[str, Any],
    structure_summary: list[dict[str, Any]],
    *,
    enforce_question_cap: bool = True,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Validate identities and positive integer score fields without policy totals."""

    if not isinstance(score_data, dict):
        raise ValueError("AI 统一配分结果顶层不是 JSON 对象。")
    raw_scores = score_data.get("question_scores")
    if not isinstance(raw_scores, list):
        raise ValueError("AI 统一配分缺少 question_scores。")
    expected_ids = [str(item.get("question_id") or "") for item in structure_summary]
    actual_ids = [
        str(item.get("question_id") or "") if isinstance(item, dict) else ""
        for item in raw_scores
    ]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ValueError("AI 统一配分的题号缺失、重复、越界或顺序不一致。")

    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for expected, actual in zip(structure_summary, raw_scores):
        if not isinstance(actual, dict):
            raise ValueError("AI 统一配分的题目结构无效。")
        question_score = _strict_positive_score(actual.get("max_score"))
        if enforce_question_cap and question_score > MAX_QUESTION_SCORE:
            raise ValueError("AI 统一配分存在超过单题上限的分值。")

        expected_parts = expected.get("parts")
        actual_parts = actual.get("parts")
        if not isinstance(expected_parts, list) or not isinstance(actual_parts, list):
            raise ValueError("AI 统一配分的分问结构无效。")
        expected_part_ids = [
            str(item.get("part_id") or "") if isinstance(item, dict) else ""
            for item in expected_parts
        ]
        actual_part_ids = [
            str(item.get("part_id") or "") if isinstance(item, dict) else ""
            for item in actual_parts
        ]
        if actual_part_ids != expected_part_ids:
            raise ValueError("AI 统一配分改变了分问结构。")

        for expected_part, actual_part in zip(expected_parts, actual_parts):
            if not isinstance(expected_part, dict) or not isinstance(actual_part, dict):
                raise ValueError("AI 统一配分的分问结构无效。")
            _strict_positive_score(actual_part.get("part_score"))
            expected_steps = expected_part.get("steps")
            actual_steps = actual_part.get("steps")
            if not isinstance(expected_steps, list) or not isinstance(actual_steps, list):
                raise ValueError("AI 统一配分的步骤结构无效。")
            expected_step_ids = [
                str(item.get("step_id") or "") if isinstance(item, dict) else ""
                for item in expected_steps
            ]
            actual_step_ids = [
                str(item.get("step_id") or "") if isinstance(item, dict) else ""
                for item in actual_steps
            ]
            if actual_step_ids != expected_step_ids:
                raise ValueError("AI 统一配分改变了评分步骤结构。")
            for item in actual_steps:
                if not isinstance(item, dict):
                    raise ValueError("AI 统一配分的步骤结构无效。")
                _strict_positive_score(item.get("step_score"))
        rows.append((expected, actual))
    return rows


def normalize_score_allocation_payload(
    score_data: dict[str, Any],
    structure_summary: list[dict[str, Any]],
    *,
    target_total: int = 100,
) -> list[str]:
    """Safely converge score values after structure identity has been proven."""

    working = copy.deepcopy(score_data)
    rows = _validated_score_allocation_rows(
        working,
        structure_summary,
        enforce_question_cap=False,
    )
    questions: list[dict[str, Any]] = []
    before: dict[str, int] = {}
    for expected, actual in rows:
        question_id = str(expected.get("question_id") or "")
        before[question_id] = _strict_positive_score(actual.get("max_score"))
        questions.append(
            {
                "question_id": question_id,
                "question_type": str(expected.get("question_type") or ""),
                "max_score": actual["max_score"],
                "parts": copy.deepcopy(actual["parts"]),
            }
        )

    enforce_integer_scores_by_type(
        questions,
        target_total=int(target_total),
        max_question_score=MAX_QUESTION_SCORE,
    )
    for (_expected, actual), normalized in zip(rows, questions, strict=True):
        actual["max_score"] = int(normalized["max_score"])
        for actual_part, normalized_part in zip(
            actual["parts"],
            normalized["parts"],
            strict=True,
        ):
            actual_part["part_score"] = int(normalized_part["part_score"])
            for actual_step, normalized_step in zip(
                actual_part["steps"],
                normalized_part["steps"],
                strict=True,
            ):
                actual_step["step_score"] = int(normalized_step["step_score"])

    _validate_exact_score_allocation_payload(working, structure_summary)
    score_data.clear()
    score_data.update(working)
    repairs = []
    for question in questions:
        question_id = str(question.get("question_id") or "")
        previous = before[question_id]
        current = int(question["max_score"])
        if previous != current:
            repairs.append(f"{question_id}: {previous}→{current}")
    return repairs


def _strict_positive_score(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("AI 统一配分包含非整数分值。")
    score = int(value)
    if float(value) != float(score) or score <= 0:
        raise ValueError("AI 统一配分包含非正整数分值。")
    return score


def _score_allocation_structure_summary(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    summary: list[dict[str, Any]] = []
    for question in questions or []:
        if not isinstance(question, dict):
            continue
        parts: list[dict[str, Any]] = []
        for part in question.get("parts") or []:
            if not isinstance(part, dict):
                continue
            steps = [
                {
                    "step_id": str(step.get("step_id") or ""),
                    "core_goal": str(step.get("core_goal") or ""),
                    "required_elements": [
                        str(item) for item in step.get("required_elements") or []
                    ],
                }
                for step in part.get("steps") or []
                if isinstance(step, dict)
            ]
            parts.append(
                {
                    "part_id": str(part.get("part_id") or ""),
                    "response_mode": str(part.get("response_mode") or ""),
                    "steps": steps,
                }
            )
        summary.append(
            {
                "question_id": str(question.get("question_id") or ""),
                "question_type": str(question.get("question_type") or ""),
                "parts": parts,
            }
        )
    return summary

def _apply_score_allocation(merged: dict[str, Any], score_data: dict[str, Any]) -> None:
    """Write AI-assigned scores from the scoring step back into the merged rubric."""
    question_scores = score_data.get("question_scores")
    if not isinstance(question_scores, list):
        raise ValueError("score_data must contain a 'question_scores' list")

    rubric_questions: list[Any] = merged.get("rubric", {}).get("questions", [])
    if not isinstance(rubric_questions, list):
        return

    score_map: dict[str, dict[str, Any]] = {
        str(qs.get("question_id") or "").strip(): qs
        for qs in question_scores
        if isinstance(qs, dict) and str(qs.get("question_id") or "").strip()
    }

    for q in rubric_questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("question_id") or "").strip()
        score_entry = score_map.get(qid)
        if not score_entry:
            continue

        # Apply max_score
        raw_max = score_entry.get("max_score")
        if raw_max is not None:
            try:
                q["max_score"] = max(1, int(round(float(raw_max))))
            except (TypeError, ValueError):
                pass

        # Apply part and step scores
        parts: list[Any] = q.get("parts") or []
        score_parts: list[Any] = score_entry.get("parts") or []
        if not isinstance(parts, list) or not score_parts:
            continue

        part_score_map: dict[str, dict[str, Any]] = {
            str(sp.get("part_id") or "").strip(): sp
            for sp in score_parts
            if isinstance(sp, dict) and str(sp.get("part_id") or "").strip()
        }

        for part in parts:
            if not isinstance(part, dict):
                continue
            pid = str(part.get("part_id") or "").strip()
            sp = part_score_map.get(pid)
            if not sp:
                continue

            raw_part = sp.get("part_score")
            if raw_part is not None:
                try:
                    part["part_score"] = max(1, int(round(float(raw_part))))
                except (TypeError, ValueError):
                    pass
            part.pop("max_score", None)

            steps: list[Any] = part.get("steps") or []
            score_steps: list[Any] = sp.get("steps") or []
            if not isinstance(steps, list) or not score_steps:
                continue

            step_score_map: dict[str, dict[str, Any]] = {
                str(ss.get("step_id") or "").strip(): ss
                for ss in score_steps
                if isinstance(ss, dict) and str(ss.get("step_id") or "").strip()
            }

            for step in steps:
                if not isinstance(step, dict):
                    continue
                sid = str(step.get("step_id") or "").strip()
                ss = step_score_map.get(sid)
                if ss:
                    raw_step = ss.get("step_score")
                    if raw_step is not None:
                        try:
                            step["step_score"] = max(1, int(round(float(raw_step))))
                        except (TypeError, ValueError):
                            pass

SESSION_MANAGER_COMPAT_EXPORTS = (
    "_apply_score_allocation",
    "collect_score_consistency_issues",
    "_score_allocation_structure_summary",
    "_normalize_score_allocation_payload",
    "_strict_positive_score",
    "_validate_exact_score_allocation_payload",
)

apply_score_allocation = _apply_score_allocation
score_allocation_structure_summary = _score_allocation_structure_summary
validate_score_allocation_payload = _validate_exact_score_allocation_payload
_normalize_score_allocation_payload = normalize_score_allocation_payload

__all__ = [
    "apply_score_allocation",
    "collect_score_consistency_issues",
    "normalize_score_allocation_payload",
    "score_allocation_structure_summary",
    "validate_score_allocation_payload",
]
