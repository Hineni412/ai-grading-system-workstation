from __future__ import annotations

import base64
import copy
import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from backend.document_parsing.question_blocks import rich_text_for_model

from .gateway import ConfigGenerationGateway
from .prompts import (
    build_batch_generation_prompt,
    build_score_allocation_prompt,
)


DEFAULT_CONFIG_GENERATION_BATCH_SIZE = 3

ProgressReporter = Callable[[float, str, str], None]
CheckpointWriter = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ConfigGenerationPolicy:
    """P3-10 business rules consumed explicitly by the P3-09 workflow."""

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
    word_block_image_blobs: Callable[[dict[str, Any], int], list[bytes]]
    is_transient_error: Callable[[Exception], bool]
    score_structure_summary: Callable[
        [dict[str, Any]], list[dict[str, Any]]
    ]
    validate_score_payload: Callable[
        [dict[str, Any], list[dict[str, Any]]], None
    ]
    apply_score_allocation: Callable[
        [dict[str, Any], dict[str, Any]], None
    ]


class _BatchSchemaMismatch(ValueError):
    pass


class ConfigGenerationOrchestrator:
    """Sequential batches, retry selection, merge, score and checkpoints."""

    def __init__(
        self,
        gateway: ConfigGenerationGateway,
        policy: ConfigGenerationPolicy,
        *,
        batch_size: int = DEFAULT_CONFIG_GENERATION_BATCH_SIZE,
        report: ProgressReporter | None = None,
    ) -> None:
        self._gateway = gateway
        self._policy = policy
        self._batch_size = max(1, min(20, int(batch_size)))
        self._report = report

    def generate(
        self,
        confirmed_blocks: list[dict[str, Any]],
        doc_text: str,
        *,
        q_images: dict[str, Any] | None = None,
        checkpoint: CheckpointWriter | None = None,
    ) -> dict[str, Any]:
        locked_blocks = [copy.deepcopy(block) for block in confirmed_blocks]
        if not locked_blocks:
            raise ValueError("至少需要一道已确认题目。")
        return self._run_batches(
            existing_payload=None,
            question_blocks=locked_blocks,
            doc_text=doc_text,
            q_images=q_images,
            batch_size=self._batch_size,
            retry_question_ids=None,
            checkpoint=checkpoint,
        )

    def retry(
        self,
        existing_payload: dict[str, Any],
        question_blocks: list[dict[str, Any]],
        doc_text: str,
        *,
        q_images: dict[str, Any] | None = None,
        retry_question_ids: Sequence[str] | None = None,
        checkpoint: CheckpointWriter | None = None,
    ) -> dict[str, Any]:
        meta = (
            existing_payload.get("meta")
            if isinstance(existing_payload, dict)
            else None
        )
        failed_batches = (
            meta.get("failed_batches") if isinstance(meta, dict) else None
        )
        locked_blocks = [copy.deepcopy(block) for block in question_blocks]
        if not isinstance(failed_batches, list) or not failed_batches:
            return self._score_completed_draft_once(
                existing_payload,
                locked_blocks,
                doc_text,
                q_images=q_images,
                checkpoint=checkpoint,
            )

        failed_by_id: dict[str, list[str]] = {}
        for item in failed_batches:
            if not isinstance(item, dict):
                continue
            batch_id = str(item.get("batch_id") or "").strip()
            raw_ids = item.get("question_ids")
            ids = [
                str(qid).strip()
                for qid in raw_ids or []
                if str(qid).strip()
            ]
            if batch_id and ids:
                failed_by_id[batch_id] = ids
        if not failed_by_id:
            raise ValueError("失败批次记录已损坏，请重新开始分批生成。")

        if retry_question_ids is None:
            selected_question_ids = {
                qid for ids in failed_by_id.values() for qid in ids
            }
        else:
            selected_ids = list(
                dict.fromkeys(
                    str(qid).strip()
                    for qid in retry_question_ids
                    if str(qid).strip()
                )
            )
            selected_set = set(selected_ids)
            selected_batch_ids = [
                batch_id
                for batch_id, ids in failed_by_id.items()
                if set(ids).issubset(selected_set)
            ]
            expected_set = {
                qid
                for batch_id in selected_batch_ids
                for qid in failed_by_id[batch_id]
            }
            if not selected_set or selected_set != expected_set:
                raise ValueError(
                    "只能按完整失败批次重试，不能只选择批次中的部分题目。"
                )
            selected_question_ids = expected_set

        existing_size = meta.get("batch_size") if isinstance(meta, dict) else None
        try:
            batch_size = int(existing_size)
        except (TypeError, ValueError):
            batch_size = DEFAULT_CONFIG_GENERATION_BATCH_SIZE
        return self._run_batches(
            existing_payload=existing_payload,
            question_blocks=locked_blocks,
            doc_text=doc_text,
            q_images=q_images,
            batch_size=batch_size,
            retry_question_ids=selected_question_ids,
            checkpoint=checkpoint,
        )

    def _run_batches(
        self,
        *,
        existing_payload: dict[str, Any] | None,
        question_blocks: list[dict[str, Any]],
        doc_text: str,
        q_images: dict[str, Any] | None,
        batch_size: int,
        retry_question_ids: set[str] | None,
        checkpoint: CheckpointWriter | None,
    ) -> dict[str, Any]:
        _validate_unique_question_ids(question_blocks)
        self._policy.validate_image_inputs(question_blocks, q_images)
        batches = _build_batches(question_blocks, batch_size)
        merged = (
            copy.deepcopy(existing_payload)
            if existing_payload is not None
            else {
                "rubric": {
                    "exam_title": "generated",
                    "total_score": 100,
                    "questions": [],
                },
                "answer_key": {"questions": []},
                "meta": {"warnings": []},
            }
        )
        states = _existing_batch_states(merged)
        if retry_question_ids is None:
            targets = list(batches)
        else:
            targets = [
                batch
                for batch in batches
                if set(batch["question_ids"]).issubset(retry_question_ids)
            ]
            targeted_question_ids = {
                qid for batch in targets for qid in batch["question_ids"]
            }
            if targeted_question_ids != retry_question_ids:
                raise ValueError(
                    "旧草稿的失败题目不能安全映射到新的分批规则。"
                )

        total_targets = len(targets)
        for completed, batch in enumerate(targets, start=1):
            batch_id = str(batch["batch_id"])
            question_ids = list(batch["question_ids"])
            if self._report:
                progress = 0.10 + 0.75 * (
                    (completed - 1) / max(1, total_targets)
                )
                self._report(
                    progress,
                    "分批生成评分标准",
                    f"正在生成批次 {batch_id}（{', '.join(question_ids)}）",
                )
            try:
                prompt, image_blobs = self._build_batch_request(
                    list(batch["blocks"]),
                    doc_text,
                    q_images,
                )
                batch_payload = (
                    self._gateway.request_images(prompt, image_blobs)
                    if image_blobs
                    else self._gateway.request_text(prompt)
                )
                _validate_exact_batch_payload(
                    batch_payload,
                    question_ids,
                    require_canonical_answer=False,
                )
                self._policy.normalize_payload(batch_payload)
                _validate_exact_batch_payload(batch_payload, question_ids)
                _replace_question_payloads(
                    merged,
                    batch_payload,
                    set(question_ids),
                    question_blocks,
                )
                batch_meta = (
                    batch_payload.get("meta")
                    if isinstance(batch_payload, dict)
                    else None
                )
                repair = (
                    batch_meta.get("local_json_repair")
                    if isinstance(batch_meta, dict)
                    else None
                )
                state: dict[str, Any] = {
                    "batch_id": batch_id,
                    "question_ids": question_ids,
                    "status": "succeeded",
                }
                if isinstance(repair, dict):
                    state["local_json_repair"] = {
                        "repaired": bool(repair.get("repaired")),
                        "operations": [
                            str(item)
                            for item in repair.get("operations") or []
                        ],
                        "response_chars": int(
                            repair.get("response_chars") or 0
                        ),
                        "response_sha256": str(
                            repair.get("response_sha256") or ""
                        ),
                    }
                states[batch_id] = state
            except Exception as exc:
                if isinstance(exc, _BatchSchemaMismatch):
                    category = "schema_mismatch"
                elif (
                    "响应字符数" in str(exc)
                    and "响应摘要" in str(exc)
                ):
                    category = "invalid_json"
                elif self._policy.is_transient_error(exc):
                    category = "transient_network"
                else:
                    category = "model_request"
                states[batch_id] = {
                    "batch_id": batch_id,
                    "question_ids": question_ids,
                    "status": "failed",
                    "category": category,
                    "error": _safe_batch_failure_message(exc),
                }
            _attach_batch_meta(merged, batches, states, batch_size)
            if checkpoint:
                checkpoint(copy.deepcopy(merged))

        self._policy.apply_local_question_facts(merged, question_blocks)
        self._policy.normalize_payload(merged)
        _attach_batch_meta(merged, batches, states, batch_size)
        failed = failed_grading_config_batches(merged)
        meta = merged.setdefault("meta", {})
        if failed:
            meta["score_allocation_mode"] = "pending_failed_batches"
            meta["score_allocation_ai_success"] = False
            meta["score_allocation_pending"] = False
            meta["score_allocation_failed"] = False
            meta.pop("score_allocation_error", None)
            meta.pop("score_allocation_failure_category", None)
            self._policy.attach_reference_answer_images(merged, q_images)
            self._policy.refresh_quality_warnings(merged)
            return merged

        merged = self._score_completed_draft_once(
            merged,
            question_blocks,
            doc_text,
            q_images=q_images,
            checkpoint=checkpoint,
        )
        if self._report:
            self._report(
                0.92,
                "分批生成完成",
                f"{len(batches)} 个批次和整卷 AI 统一配分均已完成。",
            )
        return merged

    def _score_completed_draft_once(
        self,
        existing_payload: dict[str, Any],
        question_blocks: list[dict[str, Any]],
        doc_text: str,
        *,
        q_images: dict[str, Any] | None,
        checkpoint: CheckpointWriter | None,
    ) -> dict[str, Any]:
        if failed_grading_config_batches(existing_payload):
            raise ValueError("仍有失败批次，不能进行整卷 AI 统一配分。")
        payload = copy.deepcopy(existing_payload)
        meta = payload.setdefault("meta", {})
        if (
            isinstance(meta, dict)
            and bool(meta.get("score_allocation_ai_success"))
            and not bool(meta.get("score_allocation_pending"))
        ):
            return payload
        if not isinstance(meta, dict):
            payload["meta"] = meta = {}
        meta["score_allocation_mode"] = "dedicated_ai_scoring"
        meta["score_allocation_ai_success"] = False
        meta["score_allocation_pending"] = True
        meta["score_allocation_failed"] = False
        meta.pop("score_allocation_error", None)
        meta.pop("score_allocation_failure_category", None)
        if checkpoint:
            checkpoint(copy.deepcopy(payload))

        structure_summary = self._policy.score_structure_summary(payload)
        question_ids = [
            str(item.get("question_id") or "")
            for item in structure_summary
        ]
        prompt = build_score_allocation_prompt(
            structure_summary,
            doc_text,
            include_document_text=not (
                bool(q_images)
                or any(
                    str(block.get("semantic_source") or "").strip()
                    == "images"
                    for block in question_blocks
                )
            ),
        )
        prompt = (
            "SCORE_QUESTION_IDS_JSON="
            f"{json.dumps(question_ids, ensure_ascii=False)}\n"
            + prompt
        )
        if self._report:
            self._report(
                0.88,
                "AI 统一配分",
                f"正在根据 {len(question_ids)} 道题的完整评分步骤统一配置 100 分。",
            )
        score_repair: dict[str, Any] | None = None
        try:
            score_data = self._gateway.request_text(prompt)
            score_meta = (
                score_data.get("meta")
                if isinstance(score_data, dict)
                else None
            )
            raw_score_repair = (
                score_meta.get("local_json_repair")
                if isinstance(score_meta, dict)
                else None
            )
            if isinstance(raw_score_repair, dict):
                score_repair = {
                    "repaired": bool(raw_score_repair.get("repaired")),
                    "operations": [
                        str(item)
                        for item in raw_score_repair.get("operations") or []
                    ],
                    "response_chars": int(
                        raw_score_repair.get("response_chars") or 0
                    ),
                    "response_sha256": str(
                        raw_score_repair.get("response_sha256") or ""
                    ),
                }
            self._policy.validate_score_payload(
                score_data,
                structure_summary,
            )
            self._policy.apply_score_allocation(payload, score_data)
        except Exception as exc:
            meta["score_allocation_failed"] = True
            meta["score_allocation_failure_category"] = (
                "transient_network"
                if self._policy.is_transient_error(exc)
                else "model_request"
            )
            meta["score_allocation_error"] = (
                _safe_score_allocation_failure_message(exc)
            )
            if self._report:
                self._report(
                    0.91,
                    "AI 统一配分失败",
                    "评分标准批次已保存在本机；没有自动重试，也没有使用本地分值替代。",
                )
            if checkpoint:
                checkpoint(copy.deepcopy(payload))
            return payload

        payload = self._finalize_completed_draft(
            payload,
            question_blocks,
            q_images=q_images,
        )
        meta = payload.setdefault("meta", {})
        meta["score_allocation_mode"] = "dedicated_ai_scoring"
        meta["score_allocation_ai_success"] = True
        meta["score_allocation_pending"] = False
        meta["score_allocation_failed"] = False
        meta.pop("score_allocation_error", None)
        meta.pop("score_allocation_failure_category", None)
        if score_repair is not None:
            meta["score_allocation_local_json_repair"] = score_repair
        else:
            meta.pop("score_allocation_local_json_repair", None)
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

    def _build_batch_request(
        self,
        blocks: list[dict[str, Any]],
        doc_text: str,
        q_images: dict[str, Any] | None,
    ) -> tuple[str, list[bytes]]:
        question_ids = [
            str(block.get("question_id") or "").strip()
            for block in blocks
        ]
        contexts: list[dict[str, Any]] = []
        image_blobs: list[bytes] = []
        image_map: list[str] = []
        for block in blocks:
            qid = str(block.get("question_id") or "").strip()
            image_semantic_source = (
                str(block.get("semantic_source") or "").strip() == "images"
            )
            contexts.append(
                {
                    "question_id": qid,
                    "question_type": str(
                        block.get("question_type") or ""
                    ),
                    "question_type_confirmed": (
                        block.get("question_type_confirmed") is True
                    ),
                    "question_text": (
                        ""
                        if image_semantic_source
                        else str(
                            block.get("text")
                            or block.get("question_text")
                            or ""
                        )
                    ),
                    "answer_text": (
                        ""
                        if image_semantic_source
                        else str(
                            block.get("answer_text")
                            or block.get("canonical_answer")
                            or ""
                        )
                    ),
                    "question_rich_text": (
                        ""
                        if image_semantic_source
                        else rich_text_for_model(block.get("question_html") or "")[
                            :50_000
                        ]
                    ),
                    "answer_rich_text": (
                        ""
                        if image_semantic_source
                        else rich_text_for_model(block.get("answer_html") or "")[
                            :50_000
                        ]
                    ),
                    "analysis": (
                        ""
                        if image_semantic_source
                        else str(block.get("analysis") or "")
                    ),
                    "analysis_rich_text": (
                        ""
                        if image_semantic_source
                        else rich_text_for_model(block.get("analysis_html") or "")[
                            :50_000
                        ]
                    ),
                }
            )
            image_data = (
                q_images.get(qid)
                if isinstance(q_images, dict)
                else None
            )
            if isinstance(image_data, dict) and image_data.get("question"):
                image_blobs.append(
                    base64.b64decode(
                        str(image_data["question"]),
                        validate=True,
                    )
                )
                image_map.append(f"图片{len(image_blobs)}={qid}题目")
                if image_data.get("answer"):
                    image_blobs.append(
                        base64.b64decode(
                            str(image_data["answer"]),
                            validate=True,
                        )
                    )
                    image_map.append(
                        f"图片{len(image_blobs)}={qid}答案解析"
                    )
            elif not image_semantic_source:
                for blob in self._policy.word_block_image_blobs(block, 4):
                    image_blobs.append(blob)
                    image_map.append(
                        f"图片{len(image_blobs)}={qid}内嵌图"
                    )
        fallback = (
            str(doc_text or "")[:4000]
            if any(
                str(block.get("semantic_source") or "").strip()
                != "images"
                and not item["answer_text"]
                and not item["analysis"]
                for block, item in zip(blocks, contexts)
            )
            else ""
        )
        return (
            build_batch_generation_prompt(
                question_ids,
                contexts,
                image_map,
                fallback,
            ),
            image_blobs,
        )


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


def _build_batches(
    question_blocks: list[dict[str, Any]],
    batch_size: int,
) -> list[dict[str, Any]]:
    batch_groups: list[list[dict[str, Any]]] = []
    objective_group: list[dict[str, Any]] = []
    for block in question_blocks:
        question_type = str(
            block.get("question_type") or ""
        ).strip().lower()
        if question_type in {"choice", "fill_blank"}:
            objective_group.append(block)
            if len(objective_group) >= batch_size:
                batch_groups.append(objective_group)
                objective_group = []
            continue
        if objective_group:
            batch_groups.append(objective_group)
            objective_group = []
        batch_groups.append([block])
    if objective_group:
        batch_groups.append(objective_group)
    return [
        {
            "batch_id": f"B{index + 1:03d}",
            "question_ids": [
                str(block.get("question_id") or "").strip()
                for block in group
            ],
            "blocks": group,
        }
        for index, group in enumerate(batch_groups)
    ]


def _validate_unique_question_ids(
    question_blocks: list[dict[str, Any]],
) -> None:
    ids = [
        str(block.get("question_id") or "").strip()
        for block in question_blocks
    ]
    if any(not qid for qid in ids) or len(set(ids)) != len(ids):
        raise ValueError("已确认题目必须包含唯一且非空的题号。")


def _existing_batch_states(
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    raw = meta.get("batches") if isinstance(meta, dict) else None
    return {
        str(item.get("batch_id")): copy.deepcopy(item)
        for item in raw or []
        if isinstance(item, dict)
        and str(item.get("batch_id") or "").strip()
    }


def _attach_batch_meta(
    payload: dict[str, Any],
    batches: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    batch_size: int,
) -> None:
    ordered_states: list[dict[str, Any]] = []
    for batch in batches:
        batch_id = str(batch["batch_id"])
        ordered_states.append(
            copy.deepcopy(
                states.get(batch_id)
                or {
                    "batch_id": batch_id,
                    "question_ids": list(batch["question_ids"]),
                    "status": "pending",
                }
            )
        )
    failures = [
        {
            "batch_id": str(item["batch_id"]),
            "question_ids": list(item["question_ids"]),
            "category": str(item.get("category") or "failed"),
            "error": str(item.get("error") or "批次生成失败"),
        }
        for item in ordered_states
        if item.get("status") in {"failed", "pending"}
    ]
    meta = payload.setdefault("meta", {})
    if not isinstance(meta, dict):
        payload["meta"] = meta = {}
    meta["generation_mode"] = "batched"
    meta["batch_size"] = int(batch_size)
    meta["batch_count"] = len(batches)
    meta["batches"] = ordered_states
    meta["failed_batches"] = failures
    meta["failed_question_ids"] = [
        qid for item in failures for qid in item["question_ids"]
    ]


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


def _replace_question_payloads(
    existing_payload: dict[str, Any],
    retry_payload: dict[str, Any],
    successful_qids: set[str],
    question_blocks: list[dict[str, Any]],
) -> None:
    existing_rubric = existing_payload.setdefault("rubric", {})
    existing_answer_key = existing_payload.setdefault("answer_key", {})
    retry_rubric = retry_payload.get("rubric", {})
    retry_answer_key = retry_payload.get("answer_key", {})
    existing_questions = (
        existing_rubric.setdefault("questions", [])
        if isinstance(existing_rubric, dict)
        else []
    )
    existing_answers = (
        existing_answer_key.setdefault("questions", [])
        if isinstance(existing_answer_key, dict)
        else []
    )
    retry_questions = (
        retry_rubric.get("questions", [])
        if isinstance(retry_rubric, dict)
        else []
    )
    retry_answers = (
        retry_answer_key.get("questions", [])
        if isinstance(retry_answer_key, dict)
        else []
    )
    if not isinstance(existing_questions, list) or not isinstance(
        existing_answers, list
    ):
        return
    retry_question_map = (
        {
            str(question.get("question_id") or "").strip(): question
            for question in retry_questions
            if isinstance(question, dict)
        }
        if isinstance(retry_questions, list)
        else {}
    )
    retry_answer_map = (
        {
            str(answer.get("question_id") or "").strip(): answer
            for answer in retry_answers
            if isinstance(answer, dict)
        }
        if isinstance(retry_answers, list)
        else {}
    )
    existing_questions[:] = [
        question
        for question in existing_questions
        if not isinstance(question, dict)
        or str(question.get("question_id") or "").strip()
        not in successful_qids
    ]
    existing_answers[:] = [
        answer
        for answer in existing_answers
        if not isinstance(answer, dict)
        or str(answer.get("question_id") or "").strip()
        not in successful_qids
    ]
    for qid in successful_qids:
        if qid in retry_question_map:
            existing_questions.append(
                copy.deepcopy(retry_question_map[qid])
            )
        if qid in retry_answer_map:
            existing_answers.append(
                copy.deepcopy(retry_answer_map[qid])
            )
    qid_order = {
        str(block.get("question_id") or "").strip(): index
        for index, block in enumerate(question_blocks)
        if str(block.get("question_id") or "").strip()
    }
    existing_questions.sort(
        key=lambda item: qid_order.get(
            str(item.get("question_id") or "").strip(),
            10_000,
        )
        if isinstance(item, dict)
        else 10_001
    )
    existing_answers.sort(
        key=lambda item: qid_order.get(
            str(item.get("question_id") or "").strip(),
            10_000,
        )
        if isinstance(item, dict)
        else 10_001
    )


def _safe_batch_failure_message(exc: Exception) -> str:
    message = str(exc or "").strip()
    if isinstance(exc, _BatchSchemaMismatch):
        return message[:300]
    if "响应字符数" in message and "响应摘要" in message:
        return message[:500]
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return f"模型请求失败（HTTP {status_code}），未自动重试。"
    return "模型请求或本地解析失败，未自动重试。"


def _safe_score_allocation_failure_message(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return f"AI 统一配分失败（HTTP {status_code}），未自动重试。"
    return "AI 统一配分或本地校验失败，未自动重试。"
