"""Merge targeted regeneration results into the published grading config."""

from __future__ import annotations

import copy
from typing import Any, Sequence

from question_id_contract import canonical_parent_id

from .normalization import preserve_regenerated_question_scores


def merge_targeted_regeneration(
    *,
    existing_payload: dict[str, Any],
    regenerated_structure: dict[str, Any],
    targeted_question_ids: Sequence[str],
    targeted_source_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the published payload with only the targeted questions replaced.

    Every non-targeted question object (rubric and answer key) keeps its
    serialized form verbatim. Targeted questions carry the regenerated
    structure rescaled onto their published totals. Any alignment failure
    raises ``ValueError`` so the caller keeps the published rubric intact.
    """

    merged = copy.deepcopy(existing_payload)
    rubric = merged.get("rubric")
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    if not isinstance(questions, list):
        raise ValueError("当前评分依据缺少题目列表，无法合并重新生成结果。")

    old_index: dict[str, int] = {}
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        parent = canonical_parent_id(question.get("question_id"))
        if parent is not None and parent not in old_index:
            old_index[parent] = index

    targeted: set[str] = set()
    for raw in targeted_question_ids:
        parent = canonical_parent_id(raw)
        if parent is None:
            raise ValueError(f"重新生成的题目号无法解析: {raw!r}")
        targeted.add(parent)
    if not targeted:
        raise ValueError("没有可重新生成的题目。")

    structure_rubric = regenerated_structure.get("rubric")
    new_rubric_questions = (
        structure_rubric.get("questions")
        if isinstance(structure_rubric, dict)
        else None
    )
    if not isinstance(new_rubric_questions, list):
        raise ValueError("重新生成结果缺少题目列表。")

    replaced: set[str] = set()
    for new_question in new_rubric_questions:
        if not isinstance(new_question, dict):
            continue
        question_id = canonical_parent_id(new_question.get("question_id"))
        if question_id is None or question_id not in targeted:
            raise ValueError(
                "重新生成结果的题目号无法对齐已发布评分依据: "
                f"{new_question.get('question_id')!r}"
            )
        index = old_index.get(question_id)
        if index is None:
            raise ValueError(f"已发布评分依据缺少题目 {question_id}。")
        preserve_regenerated_question_scores(questions[index], new_question)
        questions[index] = new_question
        replaced.add(question_id)
    missing = targeted - replaced
    if missing:
        missing_list = "、".join(sorted(missing))
        raise ValueError(f"重新生成结果缺少题目 {missing_list} 的新结构。")

    answer_key = merged.get("answer_key")
    answer_questions = (
        answer_key.get("questions") if isinstance(answer_key, dict) else None
    )
    structure_answer_key = regenerated_structure.get("answer_key")
    new_answer_questions = (
        structure_answer_key.get("questions")
        if isinstance(structure_answer_key, dict)
        else None
    )
    if isinstance(answer_questions, list) and isinstance(
        new_answer_questions, list
    ):
        answer_index: dict[str, int] = {}
        for index, item in enumerate(answer_questions):
            if not isinstance(item, dict):
                continue
            parent = canonical_parent_id(item.get("question_id"))
            if parent is not None and parent not in answer_index:
                answer_index[parent] = index
        for new_answer in new_answer_questions:
            if not isinstance(new_answer, dict):
                continue
            question_id = canonical_parent_id(new_answer.get("question_id"))
            if question_id is None or question_id not in targeted:
                raise ValueError(
                    "重新生成的标准答案无法对齐已发布评分依据: "
                    f"{new_answer.get('question_id')!r}"
                )
            index = answer_index.get(question_id)
            if index is None:
                answer_questions.append(new_answer)
            else:
                answer_questions[index] = new_answer

    # Per-ref bookkeeping follows the regenerated questions; everything else
    # (quality warnings, repair state, failed markers) is recomputed by the
    # caller over the merged payload.
    meta = merged.get("meta")
    if not isinstance(meta, dict):
        merged["meta"] = meta = {}
    structure_meta = regenerated_structure.get("meta")
    if not isinstance(structure_meta, dict):
        structure_meta = {}
    targeted_refs = {
        str(ref).strip()
        for ref in targeted_source_refs
        if str(ref).strip()
    }
    if isinstance(structure_meta.get("reference_assessments"), list):
        meta["reference_assessments"] = merge_question_states(
            meta.get("reference_assessments"),
            structure_meta["reference_assessments"],
        )
        assessments = meta["reference_assessments"]
        kept_warnings = [
            str(warning)
            for warning in meta.get("warnings") or []
            if str(warning)
            and not str(warning).startswith("[来源提醒] ")
        ]
        conflict_warnings = [
            f"[来源提醒] {row.get('question_id')} 来源解析存在冲突，请教师核对"
            for row in assessments
            if isinstance(row, dict) and row.get("assessment") == "conflict"
        ]
        meta["warnings"] = list(
            dict.fromkeys([*kept_warnings, *conflict_warnings])
        )
    _merge_ref_list(
        meta, "taxonomy_review_question_ids", structure_meta, targeted_refs
    )
    _merge_ref_list(
        meta, "analysis_reused_question_ids", structure_meta, targeted_refs
    )
    merged["rubric"]["total_score"] = (
        existing_payload.get("rubric") or {}
    ).get("total_score", merged["rubric"].get("total_score"))
    if isinstance(structure_meta.get("local_structure_repairs"), list):
        repairs = [
            *list(meta.get("local_structure_repairs") or []),
            *[
                str(value)
                for value in structure_meta["local_structure_repairs"]
                if str(value)
            ],
        ]
        meta["local_structure_repairs"] = list(dict.fromkeys(repairs))
    return merged


def merge_targeted_failure_draft(
    *,
    existing_payload: dict[str, Any],
    failure_draft: dict[str, Any],
    targeted_source_refs: Sequence[str],
) -> dict[str, Any]:
    """Build the no-publish draft for a failed targeted regeneration.

    The published payload is copied verbatim so question bytes never
    change; only the analysis bookkeeping meta rows covering the targeted
    refs are replaced by the filtered bundle's draft rows.
    """

    payload = copy.deepcopy(existing_payload)
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        payload["meta"] = meta = {}
    draft_meta = failure_draft.get("meta")
    if not isinstance(draft_meta, dict):
        draft_meta = {}
    meta["question_states"] = merge_question_states(
        meta.get("question_states"),
        draft_meta.get("question_states"),
    )
    for key in (
        "failed_question_ids",
        "failed_batches",
        "uncertain_question_ids",
    ):
        value = draft_meta.get(key)
        meta[key] = copy.deepcopy(value) if isinstance(value, list) else []
    meta["needs_teacher_resolution"] = bool(
        draft_meta.get("needs_teacher_resolution")
    )
    targeted_refs = {
        str(ref).strip()
        for ref in targeted_source_refs
        if str(ref).strip()
    }
    _merge_ref_list(
        meta, "taxonomy_review_question_ids", draft_meta, targeted_refs
    )
    meta["taxonomy_review_count"] = len(
        meta.get("taxonomy_review_question_ids") or []
    )
    _merge_ref_list(
        meta, "analysis_reused_question_ids", draft_meta, targeted_refs
    )
    draft_warnings = [
        str(warning)
        for warning in draft_meta.get("warnings") or []
        if str(warning)
    ]
    if draft_warnings:
        existing_warnings = [
            str(warning)
            for warning in meta.get("warnings") or []
            if str(warning)
        ]
        meta["warnings"] = list(
            dict.fromkeys([*existing_warnings, *draft_warnings])
        )
    return payload


def merge_question_states(
    existing: Any,
    generated: Any,
) -> list[dict[str, Any]]:
    """Merge per-ref question state rows, generated rows winning by ref."""

    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in existing if isinstance(existing, list) else []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("question_id") or "").strip()
        row = copy.deepcopy(item)
        if ref and ref not in positions:
            positions[ref] = len(result)
            result.append(row)
        elif not ref:
            result.append(row)
    for item in generated if isinstance(generated, list) else []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("question_id") or "").strip()
        row = copy.deepcopy(item)
        if ref and ref in positions:
            result[positions[ref]] = row
        else:
            if ref:
                positions[ref] = len(result)
            result.append(row)
    return result


def _merge_ref_list(
    meta: dict[str, Any],
    key: str,
    structure_meta: dict[str, Any],
    targeted_refs: set[str],
) -> None:
    """Rewrite a per-ref meta list for the targeted refs only.

    Published rows for targeted refs are replaced by the structure's rows;
    rows for untouched refs are preserved in place. When the structure
    carries no value for the key the targeted refs are simply cleared.
    """

    structure_value = structure_meta.get(key)
    new_rows = (
        [
            str(item)
            for item in structure_value
            if str(item).strip() and str(item).strip() in targeted_refs
        ]
        if isinstance(structure_value, list)
        else []
    )
    old_value = meta.get(key)
    kept = (
        [
            str(item)
            for item in old_value
            if str(item).strip() and str(item).strip() not in targeted_refs
        ]
        if isinstance(old_value, list)
        else []
    )
    merged = [*kept, *new_rows]
    if merged:
        meta[key] = list(dict.fromkeys(merged))
    else:
        meta.pop(key, None)
