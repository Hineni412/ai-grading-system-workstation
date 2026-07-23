from __future__ import annotations

import importlib
import json
from typing import Any, Callable, Sequence

from .gateway import (
    LLMConfigGenerationGateway,
    config_generation_extra_kwargs,
)
from .orchestration import (
    DEFAULT_CONFIG_GENERATION_BATCH_SIZE,
    ConfigGenerationOrchestrator,
    ConfigGenerationPolicy,
    failed_grading_config_batches,
    failed_grading_config_question_ids,
)
from .prompts import build_manual_structure_refinement_prompt


def _session_manager() -> Any:
    """Resolve P3-10 policy functions only when a workflow is constructed."""
    return importlib.import_module("session_manager")


def _policy() -> ConfigGenerationPolicy:
    module = _session_manager()
    return ConfigGenerationPolicy(
        validate_image_inputs=module._validate_image_semantic_inputs,
        normalize_payload=module.normalize_generated_config_schema,
        apply_local_question_facts=module._apply_local_question_facts,
        attach_reference_answer_images=module._attach_reference_answer_images,
        refresh_quality_warnings=module.refresh_generated_config_quality_warnings,
        validate_final_payload=module.validate_generated_config,
        force_total_score=lambda payload, target: module.force_payload_total_score(
            payload,
            target_total=target,
        ),
        word_block_image_blobs=lambda block, limit: module._word_block_image_blobs(
            block,
            limit=limit,
        ),
        is_transient_error=module._is_transient_config_generation_error,
        score_structure_summary=module._score_allocation_structure_summary,
        validate_score_payload=module._validate_exact_score_allocation_payload,
        apply_score_allocation=module._apply_score_allocation,
    )


def _orchestrator(
    llm_client: Any,
    *,
    model_name: str | None,
    report: Any,
    batch_size: int,
) -> ConfigGenerationOrchestrator:
    return ConfigGenerationOrchestrator(
        LLMConfigGenerationGateway(
            llm_client,
            model_name=model_name,
            extra_kwargs=config_generation_extra_kwargs(),
        ),
        _policy(),
        batch_size=batch_size,
        report=report,
    )


def generate_grading_config_in_batches(
    confirmed_blocks: list[dict[str, Any]],
    doc_text: str,
    llm_client: Any,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, Any] | None = None,
    *,
    batch_size: int = DEFAULT_CONFIG_GENERATION_BATCH_SIZE,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return _orchestrator(
        llm_client,
        model_name=model_name,
        report=report,
        batch_size=batch_size,
    ).generate(
        confirmed_blocks,
        doc_text,
        q_images=q_images,
        checkpoint=checkpoint,
    )


def retry_failed_grading_config_batches(
    existing_payload: dict[str, Any],
    question_blocks: list[dict[str, Any]],
    doc_text: str,
    llm_client: Any,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, Any] | None = None,
    *,
    retry_question_ids: Sequence[str] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    meta = (
        existing_payload.get("meta")
        if isinstance(existing_payload, dict)
        else None
    )
    raw_size = meta.get("batch_size") if isinstance(meta, dict) else None
    try:
        batch_size = int(raw_size)
    except (TypeError, ValueError):
        batch_size = DEFAULT_CONFIG_GENERATION_BATCH_SIZE
    return _orchestrator(
        llm_client,
        model_name=model_name,
        report=report,
        batch_size=batch_size,
    ).retry(
        existing_payload,
        question_blocks,
        doc_text,
        q_images=q_images,
        retry_question_ids=retry_question_ids,
        checkpoint=checkpoint,
    )


def refine_grading_config_from_manual_structure(
    payload: dict[str, Any],
    llm_client: Any,
    model_name: str | None = None,
) -> dict[str, Any]:
    module = _session_manager()
    working_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    module.validate_generated_config(working_payload)
    prompt = build_manual_structure_refinement_prompt(working_payload)
    refined = llm_client.json_from_text(prompt, model=model_name)
    try:
        module.validate_generated_config(refined)
    except Exception:
        module._dump_failed_generated_payload(refined)
        raise
    return refined
