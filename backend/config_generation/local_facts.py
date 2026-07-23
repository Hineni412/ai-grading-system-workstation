from __future__ import annotations

from typing import Any

def _apply_local_question_facts(payload: dict[str, Any], question_blocks: list[dict[str, Any]]) -> None:
    if not isinstance(payload, dict):
        return
    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    if not isinstance(rubric, dict) or not isinstance(answer_key, dict):
        return
    questions = rubric.setdefault("questions", [])
    answers = answer_key.setdefault("questions", [])
    if not isinstance(questions, list) or not isinstance(answers, list):
        return

    facts = {
        str(block.get("question_id") or "").strip(): block
        for block in question_blocks
        if str(block.get("question_id") or "").strip()
    }
    answer_map = {
        str(answer.get("question_id") or "").strip(): answer
        for answer in answers
        if isinstance(answer, dict)
    }

    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        fact = facts.get(qid)
        if not fact:
            continue
        local_type = str(fact.get("question_type") or "").strip()
        type_confirmed = bool(fact.get("question_type_confirmed"))
        if local_type in {"choice", "fill_blank"} or (
            type_confirmed and local_type in {"calculation", "proof", "comprehensive"}
        ):
            question["question_type"] = local_type
            question["grading_mode"] = (
                "direct_answer"
                if local_type in {"choice", "fill_blank"}
                else "deductive_obligation"
            )
        if type_confirmed:
            question["question_type_confirmed"] = True
        image_semantic_source = str(fact.get("semantic_source") or "").strip() == "images"
        if (
            not image_semantic_source
            and str(fact.get("question_text") or "").strip()
            and not str(question.get("stem_summary") or "").strip()
        ):
            question["stem_summary"] = str(fact.get("question_text") or "").strip().splitlines()[0][:120]

        answer = answer_map.get(qid)
        if answer is None:
            answer = {"question_id": qid, "canonical_answer": "", "accepted_forms": [], "method_variants": [], "parts": []}
            answers.append(answer)
            answer_map[qid] = answer
        canonical = str(fact.get("canonical_answer") or "").strip()
        accepted = [str(item).strip() for item in fact.get("accepted_forms") or [] if str(item).strip()]
        if canonical and not image_semantic_source:
            current_canonical = str(answer.get("canonical_answer") or "").strip()
            if bool(fact.get("local_answer_trusted")) or not current_canonical:
                answer["canonical_answer"] = canonical
                base_answer = canonical
            else:
                base_answer = current_canonical
            answer["accepted_forms"] = merge_equivalent_forms(answer.get("accepted_forms"), *accepted, base_answer, max_forms=32)
            parts = answer.get("parts")
            if not isinstance(parts, list) or not parts:
                answer["parts"] = [{"part_id": qid, "answer": base_answer, "analysis": str(fact.get("answer_text") or ""), "step_milestones": []}]
            else:
                for part in parts:
                    if isinstance(part, dict) and not str(part.get("answer") or "").strip():
                        part["answer"] = base_answer

def _attach_reference_answer_images(
    payload: dict[str, Any],
    q_images: dict[str, Any] | None,
) -> None:
    """Persist clean PDF answer and question stem crops for image-aware grading."""
    if not q_images:
        return
    answer_key = payload.get("answer_key") if isinstance(payload, dict) else None
    answer_questions = answer_key.get("questions") if isinstance(answer_key, dict) else None
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    rubric_questions = rubric.get("questions") if isinstance(rubric, dict) else None

    answer_map = {
        str(item.get("question_id") or ""): item
        for item in answer_questions
        if isinstance(item, dict)
    } if isinstance(answer_questions, list) else {}

    rubric_map = {
        str(item.get("question_id") or ""): item
        for item in rubric_questions
        if isinstance(item, dict)
    } if isinstance(rubric_questions, list) else {}

    for qid, image_data in q_images.items():
        if not isinstance(image_data, dict):
            continue
        answer_image = str(image_data.get("answer") or "").strip()
        answer_item = answer_map.get(str(qid))
        if answer_image and isinstance(answer_item, dict):
            answer_item["answer_image_base64"] = answer_image
            answer_item["answer_image_role"] = "perfect_standard_answer"

        question_image = str(image_data.get("question") or "").strip()
        rubric_item = rubric_map.get(str(qid))
        if question_image and isinstance(rubric_item, dict):
            rubric_item["question_image_base64"] = question_image

SESSION_MANAGER_COMPAT_EXPORTS = (
    "_apply_local_question_facts",
    "_attach_reference_answer_images",
)

apply_local_question_facts = _apply_local_question_facts
attach_reference_answer_images = _attach_reference_answer_images

__all__ = [
    "apply_local_question_facts",
    "attach_reference_answer_images",
]
