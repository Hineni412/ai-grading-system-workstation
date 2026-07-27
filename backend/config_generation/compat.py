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
    failed_grading_config_batches,
    failed_grading_config_question_ids,
)
from .policy import build_config_generation_policy
from .prompts import build_manual_structure_refinement_prompt
from .normalization import (
    normalize_generated_config_schema,
    strip_generated_config_knowledge_fields,
    validate_generated_config,
)


def _session_manager() -> Any:
    """Resolve P3-10 policy functions only when a workflow is constructed."""
    return importlib.import_module("session_manager")


def _policy():
    module = _session_manager()
    return build_config_generation_policy(
        validate_image_inputs=module._validate_image_semantic_inputs,
        word_block_image_blobs=lambda block, limit: module._word_block_image_blobs(
            block,
            limit=limit,
        ),
        is_transient_error=module._is_transient_config_generation_error,
        word_block_image_assets=(
            lambda block, limit, max_total_bytes: (
                module._word_block_image_assets(
                    block,
                    limit=limit,
                    max_total_bytes=max_total_bytes,
                )
            )
        ),
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
    validate_generated_config(working_payload)
    strip_generated_config_knowledge_fields(working_payload)
    prompt = build_manual_structure_refinement_prompt(working_payload)
    refined = llm_client.json_from_text(prompt, model=model_name)
    try:
        # Refinement is different from generating a new rubric: the teacher's
        # part/step identifiers are persistent editor identities.  Schema
        # repair is safe here, but canonicalising those identities would make
        # an unchanged model response look like a structural rewrite.
        strip_generated_config_knowledge_fields(refined)
        normalize_generated_config_schema(refined)
        strip_generated_config_knowledge_fields(refined)
        validate_generated_config(refined)
    except Exception:
        module._dump_failed_generated_payload(refined)
        raise
    return refined
