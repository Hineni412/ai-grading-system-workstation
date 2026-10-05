from __future__ import annotations

from typing import Any

from backend.scan_grading.equivalence_engine import merge_equivalent_forms


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
        type_confirmed = fact.get("question_type_confirmed") is True
        single_blank_fact = (
            fact.get("response_form_fact") == "single_blank"
            and not type_confirmed
        )
        question["question_type_confirmed"] = type_confirmed
        if type_confirmed and local_type in {
            "choice",
            "fill_blank",
            "calculation",
            "proof",
            "comprehensive",
        }:
            question["question_type"] = local_type
            question["grading_mode"] = (
                "direct_answer"
                if local_type in {"choice", "fill_blank"}
                else "deductive_obligation"
            )
        elif single_blank_fact:
            question["question_type"] = "fill_blank"
            question["grading_mode"] = "direct_answer"
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
        teacher_confirmed = fact.get("answer_confirmed_by_teacher") is True
        if canonical and (not image_semantic_source or teacher_confirmed):
            current_canonical = str(answer.get("canonical_answer") or "").strip()
            local_answer_selected = (
                teacher_confirmed
                or bool(fact.get("local_answer_trusted"))
                or not current_canonical
            )
            if local_answer_selected:
                answer["canonical_answer"] = canonical
                base_answer = canonical
            else:
                base_answer = current_canonical
            answer["accepted_forms"] = merge_equivalent_forms(
                [] if teacher_confirmed else answer.get("accepted_forms"),
                *(accepted if local_answer_selected else ()),
                base_answer,
                max_forms=32,
            )
            parts = answer.get("parts")
            if not isinstance(parts, list) or not parts:
                answer["parts"] = [{"part_id": qid, "answer": base_answer, "analysis": str(fact.get("answer_text") or ""), "step_milestones": []}]
            else:
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if teacher_confirmed:
                        part["answer"] = base_answer
                        part["accepted_forms"] = [base_answer]
                        part["answer_values"] = [base_answer]
                        for step in part.get("steps") or []:
                            if not isinstance(step, dict):
                                continue
                            for alias in (
                                "answer_value",
                                "correct_value",
                                "answer",
                                "standard_answer",
                                "canonical_answer",
                            ):
                                step.pop(alias, None)
                    elif not str(part.get("answer") or "").strip():
                        part["answer"] = base_answer
        if single_blank_fact:
            score = question.get("max_score", 1)
            answer_value = str(answer.get("canonical_answer") or canonical).strip()
            question["parts"] = [
                {
                    "part_id": qid,
                    "part_score": score,
                    "response_mode": "exact_objective",
                    "presentation_rules": [],
                    "require_final_answer": False,
                    "steps": [
                        {
                            "step_id": "S1",
                            "step_score": score,
                            "core_goal": "填写正确或等价的答案",
                            "required_elements": [answer_value] if answer_value else [],
                            "allow_alternative_methods": True,
                        }
                    ],
                }
            ]
            answer["parts"] = [
                {
                    "part_id": qid,
                    "answer": answer_value,
                    "analysis": str(fact.get("answer_text") or ""),
                    "step_milestones": [],
                }
            ]

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

    def first_image(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return next(
                (item.strip() for item in value if isinstance(item, str) and item.strip()),
                "",
            )
        return ""

    for qid, image_data in q_images.items():
        if not isinstance(image_data, dict):
            continue
        answer_image = first_image(image_data.get("answer"))
        answer_item = answer_map.get(str(qid))
        if answer_image and isinstance(answer_item, dict):
            answer_item["answer_image_base64"] = answer_image
            answer_item["answer_image_role"] = "perfect_standard_answer"

        question_image = first_image(image_data.get("question"))
        rubric_item = rubric_map.get(str(qid))
        if question_image and isinstance(rubric_item, dict):
            rubric_item["question_image_base64"] = question_image

apply_local_question_facts = _apply_local_question_facts
attach_reference_answer_images = _attach_reference_answer_images

__all__ = [
    "apply_local_question_facts",
    "attach_reference_answer_images",
]
