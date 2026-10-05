from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from backend.question_id_contract import (
    canonical_parent_id,
    canonical_part_id,
)


class GeneratedOutputContractError(ValueError):
    """生成结果无法在不猜测业务结构的前提下归一化。"""


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


def iter_effective_rubric_items(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, Mapping[str, object], Mapping[str, object]]]:
    for item_ref, _parent_ref, raw_question, raw_item in iter_effective_rubric_item_refs(payload):
        yield item_ref, raw_question, raw_item


def iter_effective_rubric_item_refs(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, str, Mapping[str, object], Mapping[str, object]]]:
    rubric = payload.get("rubric") if isinstance(payload.get("rubric"), Mapping) else payload
    questions = rubric.get("questions") if isinstance(rubric, Mapping) else None
    if not isinstance(questions, list):
        return
    for question_index, raw_question in enumerate(questions, start=1):
        if not isinstance(raw_question, Mapping):
            continue
        question_ref = str(
            raw_question.get("question_id")
            or raw_question.get("id")
            or raw_question.get("number")
            or f"Q{question_index}"
        ).strip()
        parts = raw_question.get("parts")
        emitted = False
        if isinstance(parts, list) and parts:
            for part_index, raw_part in enumerate(parts, start=1):
                if not isinstance(raw_part, Mapping):
                    continue
                part_ref = str(
                    raw_part.get("part_id")
                    or raw_part.get("question_id")
                    or f"{question_ref}.{part_index}"
                ).strip()
                emitted = True
                yield part_ref, question_ref, raw_question, raw_part
        if not emitted:
            yield question_ref, question_ref, raw_question, raw_question


__all__ = [
    "GeneratedOutputContractError",
    "attach_structure_repairs",
    "canonicalize_new_generated_structure_ids",
    "is_simple_objective_question",
    "iter_effective_rubric_items",
    "iter_effective_rubric_item_refs",
]
