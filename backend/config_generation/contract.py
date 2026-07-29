from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from question_id_contract import (
    canonical_parent_id,
    canonical_part_id,
    question_id_coordinates,
)


GENERATED_ID_CONTRACT_PROMPT = (
    "编号硬约束：question_id 只能使用本次给定的 Qn；"
    "单小问题的 part_id 必须等于父题号 Qn；"
    "多小问题的 part_id 必须依次为 Qn(P1)、Qn(P2)…；"
    "每个小问内部的 step_id 必须独立从 S1、S2… 顺序编号。"
    "rubric 与 answer_key 必须使用完全相同的 question_id、part_id；"
    "不得使用 P4_1、S4_1、Q4_1 等自创写法。"
)

TEACHER_TYPE_CONTRACT_PROMPT = (
    "question_type_confirmed 是教师事实：输入为 true 时必须保留教师题型；"
    "输入为 false 时必须保持 false，并结合题干、答案和图片重新判断题型；"
    "模型不得自行把 question_type_confirmed 改为 true。"
)


class GeneratedOutputContractError(ValueError):
    """模型结果无法在不猜测业务结构的前提下归一化。"""


def align_generated_question_ids(
    payload: dict[str, Any],
    expected_question_ids: Sequence[str],
) -> list[str]:
    """Align safe parent-id aliases to one already-known batch order.

    The batch plan owns question identity.  Missing or decorated spellings can
    be repaired by position, but a recognisable *different* parent remains a
    hard failure rather than being silently reassigned.
    """

    expected = [str(value or "").strip() for value in expected_question_ids]
    if any(canonical_parent_id(value) != value for value in expected):
        raise GeneratedOutputContractError("本地批次包含无效的规范题号。")
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    answer_key = payload.get("answer_key") if isinstance(payload, dict) else None
    rubric_questions = rubric.get("questions") if isinstance(rubric, dict) else None
    answer_questions = (
        answer_key.get("questions") if isinstance(answer_key, dict) else None
    )
    if not isinstance(rubric_questions, list) or not isinstance(
        answer_questions, list
    ):
        raise GeneratedOutputContractError(
            "模型结果缺少 rubric.questions 或 answer_key.questions。"
        )
    if len(rubric_questions) != len(expected) or len(answer_questions) != len(
        expected
    ):
        raise GeneratedOutputContractError(
            "模型返回的题目数量与当前批次不一致。"
        )

    operations: list[str] = []
    for collection_name, questions in (
        ("rubric", rubric_questions),
        ("answer_key", answer_questions),
    ):
        for index, (question, expected_id) in enumerate(
            zip(questions, expected),
            start=1,
        ):
            if not isinstance(question, dict):
                raise GeneratedOutputContractError(
                    f"{collection_name} 第 {index} 道题不是对象。"
                )
            raw = str(question.get("question_id") or "").strip()
            resolved = _generated_parent_alias(raw)
            if raw and resolved is None:
                raise GeneratedOutputContractError(
                    f"{collection_name} 的题号 {raw!r} 无法对应 {expected_id}。"
                )
            if resolved is not None and resolved != expected_id:
                raise GeneratedOutputContractError(
                    f"{collection_name} 返回了 {resolved}，当前应为 {expected_id}。"
                )
            if raw != expected_id:
                operations.append(
                    f"{collection_name}.question_id:{raw or '<missing>'}->{expected_id}"
                )
            question["question_id"] = expected_id
    return operations


def canonicalize_new_generated_structure_ids(
    payload: dict[str, Any],
) -> list[str]:
    """Assign canonical part/step identities to a newly generated payload.

    Model identifiers carry no business authority.  The model owns content and
    order; this function owns the stable identities derived from that order.
    """

    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    answer_key = payload.get("answer_key") if isinstance(payload, dict) else None
    rubric_questions = rubric.get("questions") if isinstance(rubric, dict) else None
    answer_questions = (
        answer_key.get("questions") if isinstance(answer_key, dict) else None
    )
    if not isinstance(rubric_questions, list) or not isinstance(
        answer_questions, list
    ):
        raise GeneratedOutputContractError(
            "模型结果缺少 rubric.questions 或 answer_key.questions。"
        )

    answers_by_parent: dict[str, dict[str, Any]] = {}
    for answer in answer_questions:
        if not isinstance(answer, dict):
            raise GeneratedOutputContractError("标准答案题目不是对象。")
        parent = canonical_parent_id(answer.get("question_id"))
        if parent is None:
            raise GeneratedOutputContractError(
                f"标准答案题号无法解析: {answer.get('question_id')!r}"
            )
        if parent in answers_by_parent:
            raise GeneratedOutputContractError(f"标准答案大题号重复: {parent}")
        answers_by_parent[parent] = answer

    operations: list[str] = []
    for question in rubric_questions:
        if not isinstance(question, dict):
            raise GeneratedOutputContractError("评分依据题目不是对象。")
        parent = canonical_parent_id(question.get("question_id"))
        if parent is None:
            raise GeneratedOutputContractError(
                f"评分依据题号无法解析: {question.get('question_id')!r}"
            )
        parts = question.get("parts")
        if not isinstance(parts, list) or not parts:
            raise GeneratedOutputContractError(f"{parent} 缺少评分小问。")
        canonical_parts = [
            parent if len(parts) == 1 else canonical_part_id(parent, index)
            for index in range(1, len(parts) + 1)
        ]
        for part, canonical in zip(parts, canonical_parts):
            if not isinstance(part, dict):
                raise GeneratedOutputContractError(f"{parent} 的评分小问不是对象。")
            raw_part = str(part.get("part_id") or "").strip()
            if raw_part != canonical:
                operations.append(
                    f"rubric.part_id:{raw_part or '<missing>'}->{canonical}"
                )
            part["part_id"] = canonical
            steps = part.get("steps")
            if not isinstance(steps, list) or not steps:
                raise GeneratedOutputContractError(f"{canonical} 缺少评分步骤。")
            for step_index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    raise GeneratedOutputContractError(
                        f"{canonical} 的评分步骤不是对象。"
                    )
                canonical_step = f"S{step_index}"
                raw_step = str(step.get("step_id") or "").strip()
                if raw_step != canonical_step:
                    operations.append(
                        f"{canonical}.step_id:{raw_step or '<missing>'}->{canonical_step}"
                    )
                step["step_id"] = canonical_step

        answer = answers_by_parent.get(parent)
        if answer is None:
            raise GeneratedOutputContractError(f"标准答案缺少 {parent}。")
        answer_parts = answer.get("parts")
        if not isinstance(answer_parts, list):
            raise GeneratedOutputContractError(f"标准答案 {parent} 缺少小问列表。")
        if len(answer_parts) != len(canonical_parts):
            raise GeneratedOutputContractError(
                f"{parent} 的评分小问与标准答案小问数量不一致。"
            )
        for answer_part, canonical in zip(answer_parts, canonical_parts):
            if not isinstance(answer_part, dict):
                raise GeneratedOutputContractError(
                    f"标准答案 {parent} 的小问不是对象。"
                )
            raw_part = str(answer_part.get("part_id") or "").strip()
            if raw_part != canonical:
                operations.append(
                    f"answer_key.part_id:{raw_part or '<missing>'}->{canonical}"
                )
            answer_part["part_id"] = canonical
    return operations


def align_score_allocation_ids(
    score_data: dict[str, Any],
    structure_summary: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Repair score-allocation identifiers against the retained rubric structure."""

    raw_scores = score_data.get("question_scores") if isinstance(score_data, dict) else None
    if not isinstance(raw_scores, list) or len(raw_scores) != len(structure_summary):
        return []
    operations: list[str] = []
    for index, (actual, expected) in enumerate(
        zip(raw_scores, structure_summary),
        start=1,
    ):
        if not isinstance(actual, dict):
            return operations
        expected_question_id = str(expected.get("question_id") or "").strip()
        raw_question_id = str(actual.get("question_id") or "").strip()
        resolved = _generated_parent_alias(raw_question_id)
        if raw_question_id and resolved is None:
            raise GeneratedOutputContractError(
                f"统一配分第 {index} 道题号 {raw_question_id!r} 无法对应 "
                f"{expected_question_id}。"
            )
        if resolved is not None and resolved != expected_question_id:
            raise GeneratedOutputContractError(
                f"统一配分返回了 {resolved}，当前应为 {expected_question_id}。"
            )
        if raw_question_id != expected_question_id:
            operations.append(
                "score.question_id:"
                f"{raw_question_id or '<missing>'}->{expected_question_id}"
            )
        actual["question_id"] = expected_question_id

        expected_parts = expected.get("parts")
        actual_parts = actual.get("parts")
        if not isinstance(expected_parts, list) or not isinstance(actual_parts, list):
            continue
        if len(expected_parts) != len(actual_parts):
            continue
        for actual_part, expected_part in zip(actual_parts, expected_parts):
            if not isinstance(actual_part, dict) or not isinstance(expected_part, Mapping):
                continue
            expected_part_id = str(expected_part.get("part_id") or "").strip()
            raw_part_id = str(actual_part.get("part_id") or "").strip()
            if raw_part_id != expected_part_id:
                operations.append(
                    f"score.part_id:{raw_part_id or '<missing>'}->{expected_part_id}"
                )
            actual_part["part_id"] = expected_part_id
            expected_steps = expected_part.get("steps")
            actual_steps = actual_part.get("steps")
            if not isinstance(expected_steps, list) or not isinstance(actual_steps, list):
                continue
            if len(expected_steps) != len(actual_steps):
                continue
            for actual_step, expected_step in zip(actual_steps, expected_steps):
                if not isinstance(actual_step, dict) or not isinstance(
                    expected_step, Mapping
                ):
                    continue
                expected_step_id = str(expected_step.get("step_id") or "").strip()
                raw_step_id = str(actual_step.get("step_id") or "").strip()
                if raw_step_id != expected_step_id:
                    operations.append(
                        "score.step_id:"
                        f"{raw_step_id or '<missing>'}->{expected_step_id}"
                    )
                actual_step["step_id"] = expected_step_id
    return operations


def attach_structure_repairs(
    payload: dict[str, Any],
    operations: Sequence[str],
) -> None:
    clean = list(dict.fromkeys(str(value) for value in operations if str(value)))
    if not clean:
        return
    meta = payload.setdefault("meta", {})
    if not isinstance(meta, dict):
        payload["meta"] = meta = {}
    existing = meta.get("local_structure_repairs")
    merged = [
        *(
            [str(value) for value in existing if str(value)]
            if isinstance(existing, list)
            else []
        ),
        *clean,
    ]
    meta["local_structure_repairs"] = list(dict.fromkeys(merged))


def is_simple_objective_question(question: Mapping[str, Any]) -> bool:
    question_type = str(question.get("question_type") or "").strip()
    if question_type not in {
        "choice",
        "fill_blank",
        "judgement",
        "true_false",
    }:
        return False
    parts = question.get("parts")
    if not isinstance(parts, list) or len(parts) != 1:
        return False
    part = parts[0]
    if not isinstance(part, Mapping):
        return False
    if str(part.get("response_mode") or "").strip() != "exact_objective":
        return False
    steps = part.get("steps")
    return isinstance(steps, list) and len(steps) == 1


def _generated_parent_alias(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    canonical = canonical_parent_id(text)
    if canonical is not None:
        return canonical
    coordinates = question_id_coordinates(text)
    if coordinates is not None and coordinates[1] in {None, 1}:
        return f"Q{coordinates[0]}"
    match = re.fullmatch(r"(?:第\s*)?0*(\d+)\s*(?:题)?", text)
    if match is None:
        return None
    number = int(match.group(1))
    return f"Q{number}" if number > 0 else None


__all__ = [
    "GENERATED_ID_CONTRACT_PROMPT",
    "TEACHER_TYPE_CONTRACT_PROMPT",
    "GeneratedOutputContractError",
    "align_generated_question_ids",
    "align_score_allocation_ids",
    "attach_structure_repairs",
    "canonicalize_new_generated_structure_ids",
    "is_simple_objective_question",
]
