from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable


ProgressReporter = Callable[[float, str, str], None]
CheckpointWriter = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ConfigGenerationPolicy:
    """P3-10 business rules consumed explicitly by the score-allocation path."""

    validate_image_inputs: Callable[
        [list[dict[str, Any]], dict[str, Any] | None], None
    ]
    normalize_payload: Callable[[dict[str, Any]], None]
    apply_local_question_facts: Callable[
        [dict[str, Any], list[dict[str, Any]]], None
    ]
    attach_reference_answer_images: Callable[
        [dict[str, Any], dict[str, Any] | None], None
    ]
    refresh_quality_warnings: Callable[[dict[str, Any]], None]
    validate_final_payload: Callable[[dict[str, Any]], None]
    force_total_score: Callable[[dict[str, Any], float], None]


class _BatchSchemaMismatch(ValueError):
    pass


class ConfigGenerationOrchestrator:
    """Local step-weighted score allocation over a fixed rubric structure."""

    def __init__(
        self,
        policy: ConfigGenerationPolicy,
        *,
        report: ProgressReporter | None = None,
    ) -> None:
        self._policy = policy
        self._report = report

    def allocate_scores_for_structure(
        self,
        structure_payload: dict[str, Any],
        question_blocks: list[dict[str, Any]],
        doc_text: str,
        *,
        q_images: dict[str, Any] | None = None,
        checkpoint: CheckpointWriter | None = None,
    ) -> dict[str, Any]:
        """Allocate scores without asking the model to regenerate structure.

        The caller owns the complete, unscored rubric and answer projection.
        This seam is used by solution-evidence analysis so no model request is
        needed for whole-paper score allocation.
        """

        if not isinstance(structure_payload, dict):
            raise TypeError("评分结构必须是一个对象。")
        locked_blocks = [copy.deepcopy(block) for block in question_blocks]
        if not locked_blocks:
            raise ValueError("至少需要一道已确认题目。")
        _validate_unique_question_ids(locked_blocks)
        self._policy.validate_image_inputs(locked_blocks, q_images)

        payload = copy.deepcopy(structure_payload)
        self._policy.apply_local_question_facts(payload, locked_blocks)
        self._policy.normalize_payload(payload)
        self._policy.apply_local_question_facts(payload, locked_blocks)
        expected_ids = [
            str(block.get("question_id") or "").strip()
            for block in locked_blocks
        ]
        _validate_exact_batch_payload(payload, expected_ids)
        self._policy.refresh_quality_warnings(payload)
        if failed_grading_config_batches(payload):
            raise ValueError("证据结构仍有失败题目，不能进行整卷统一配分。")

        meta = payload.setdefault("meta", {})
        if not isinstance(meta, dict):
            payload["meta"] = meta = {}
        existing_source = str(meta.get("structure_source") or "").strip()
        meta["structure_source"] = (
            existing_source if existing_source else "solution_evidence"
        )
        meta["structure_generation_model_requests"] = 0
        return self._score_completed_draft_once(
            payload,
            locked_blocks,
            q_images=q_images,
            checkpoint=checkpoint,
        )

    def _score_completed_draft_once(
        self,
        existing_payload: dict[str, Any],
        question_blocks: list[dict[str, Any]],
        *,
        q_images: dict[str, Any] | None,
        checkpoint: CheckpointWriter | None,
    ) -> dict[str, Any]:
        """Local step-weighted allocation; no whole-paper AI scoring request.

        本场配分只使用本卷判分结构的步骤数作权重：原卷分值前缀不作为依据，
        重复题也不会再被送给模型赋分。约束无解时按旧的配分失败契约
        保留批次结果，交给教师调整后重试，不丢弃已生成结构。
        """
        if failed_grading_config_batches(existing_payload):
            raise ValueError("仍有失败批次，不能完成本地配分。")
        incoming_meta = (
            existing_payload.get("meta")
            if isinstance(existing_payload, dict)
            else None
        )
        if isinstance(incoming_meta, dict) and not bool(
            incoming_meta.get("score_allocation_pending")
        ):
            if bool(incoming_meta.get("score_allocation_ai_success")) or str(
                incoming_meta.get("score_allocation_mode") or ""
            ) == "local_step_weighted":
                return copy.deepcopy(existing_payload)
        if self._report:
            self._report(
                0.88,
                "本地统一配分",
                "正在根据评分步骤结构为整卷配置 100 分。",
            )
        try:
            payload = self._finalize_completed_draft(
                existing_payload,
                question_blocks,
                q_images=q_images,
            )
        except Exception as exc:
            payload = copy.deepcopy(existing_payload)
            meta = payload.setdefault("meta", {})
            if not isinstance(meta, dict):
                payload["meta"] = meta = {}
            meta["score_allocation_mode"] = "local_step_weighted"
            meta["score_allocation_ai_success"] = False
            meta["score_allocation_pending"] = True
            meta["score_allocation_failed"] = True
            meta["score_allocation_failure_category"] = "local_validation"
            meta["score_allocation_error"] = _safe_local_allocation_message(exc)
            if self._report:
                self._report(
                    0.91,
                    "本地统一配分失败",
                    "评分标准批次已保存在本机；没有自动重试。",
                )
            if checkpoint:
                checkpoint(copy.deepcopy(payload))
            return payload
        meta = payload.setdefault("meta", {})
        if not isinstance(meta, dict):
            payload["meta"] = meta = {}
        meta["score_allocation_mode"] = "local_step_weighted"
        meta["score_allocation_ai_success"] = False
        meta["score_allocation_pending"] = False
        meta["score_allocation_failed"] = False
        for key in (
            "score_allocation_error",
            "score_allocation_failure_category",
            "score_allocation_local_json_repair",
            "score_allocation_local_score_repairs",
            "score_allocation_local_structure_repairs",
        ):
            meta.pop(key, None)
        if checkpoint:
            checkpoint(copy.deepcopy(payload))
        return payload

    def _finalize_completed_draft(
        self,
        existing_payload: dict[str, Any],
        question_blocks: list[dict[str, Any]],
        *,
        q_images: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(existing_payload)
        if failed_grading_config_batches(payload):
            raise ValueError("仍有失败批次，不能完成本地发布。")
        _validate_unique_question_ids(question_blocks)
        self._policy.apply_local_question_facts(payload, question_blocks)
        self._policy.normalize_payload(payload)
        self._policy.apply_local_question_facts(payload, question_blocks)
        expected_ids = [
            str(block.get("question_id") or "").strip()
            for block in question_blocks
        ]
        _validate_exact_batch_payload(payload, expected_ids)
        self._policy.force_total_score(payload, 100.0)
        meta = payload.setdefault("meta", {})
        meta["score_allocation_mode"] = "local_normalization"
        meta["score_allocation_ai_success"] = False
        meta["score_allocation_pending"] = False
        self._policy.attach_reference_answer_images(payload, q_images)
        self._policy.refresh_quality_warnings(payload)
        self._policy.validate_final_payload(payload)
        return payload


def failed_grading_config_batches(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    value = meta.get("failed_batches") if isinstance(meta, dict) else None
    return copy.deepcopy(value) if isinstance(value, list) else []


def failed_grading_config_question_ids(
    payload: dict[str, Any],
) -> list[str]:
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    if not isinstance(meta, dict):
        return []
    raw_ids = meta.get("failed_question_ids")
    if isinstance(raw_ids, list):
        return list(
            dict.fromkeys(
                str(qid).strip()
                for qid in raw_ids
                if str(qid).strip()
            )
        )
    failures = meta.get("failed_questions")
    if not isinstance(failures, list):
        return []
    return list(
        dict.fromkeys(
            str(failure.get("question_id") or "").strip()
            for failure in failures
            if isinstance(failure, dict)
            and str(failure.get("question_id") or "").strip()
        )
    )


def _validate_unique_question_ids(
    question_blocks: list[dict[str, Any]],
) -> None:
    ids = [
        str(block.get("question_id") or "").strip()
        for block in question_blocks
    ]
    if any(not qid for qid in ids) or len(set(ids)) != len(ids):
        raise ValueError("已确认题目必须包含唯一且非空的题号。")


def _validate_exact_batch_payload(
    payload: dict[str, Any],
    question_ids: list[str],
    *,
    require_canonical_answer: bool = True,
) -> None:
    if not isinstance(payload, dict):
        raise _BatchSchemaMismatch("批次结果顶层不是 JSON 对象。")
    rubric = payload.get("rubric")
    answer_key = payload.get("answer_key")
    questions = (
        rubric.get("questions") if isinstance(rubric, dict) else None
    )
    answers = (
        answer_key.get("questions")
        if isinstance(answer_key, dict)
        else None
    )
    if not isinstance(questions, list) or not isinstance(answers, list):
        raise _BatchSchemaMismatch(
            "批次结果缺少 rubric.questions 或 answer_key.questions。"
        )
    rubric_ids = [
        str(item.get("question_id") or "").strip()
        for item in questions
        if isinstance(item, dict)
    ]
    answer_ids = [
        str(item.get("question_id") or "").strip()
        for item in answers
        if isinstance(item, dict)
    ]
    if (
        len(rubric_ids) != len(questions)
        or len(answer_ids) != len(answers)
        or rubric_ids != question_ids
        or answer_ids != question_ids
        or len(set(rubric_ids)) != len(rubric_ids)
    ):
        raise _BatchSchemaMismatch(
            "批次结果题号缺失、重复、越界或顺序不一致。"
        )
    for question in questions:
        if (
            not isinstance(question.get("parts"), list)
            or not question["parts"]
        ):
            raise _BatchSchemaMismatch("批次评分结构不完整。")
    for answer in answers:
        if (
            require_canonical_answer
            and "canonical_answer" not in answer
        ):
            raise _BatchSchemaMismatch("批次答案结构不完整。")


def _safe_local_allocation_message(exc: Exception) -> str:
    message = str(exc or "").strip()
    if isinstance(exc, ValueError) and message:
        return f"本地统一配分未满足约束：{message[:300]}"
    return "本地统一配分失败，未自动重试。"
