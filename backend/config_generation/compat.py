from __future__ import annotations

import importlib
from typing import Any, Callable

from .orchestration import (
    ConfigGenerationOrchestrator,
    failed_grading_config_batches,
    failed_grading_config_question_ids,
)
from .policy import build_config_generation_policy


def _session_manager() -> Any:
    """Resolve P3-10 policy functions only when a workflow is constructed."""
    return importlib.import_module("session_manager")


def _policy():
    module = _session_manager()
    return build_config_generation_policy(
        validate_image_inputs=module._validate_image_semantic_inputs,
    )


def allocate_grading_config_scores(
    structure_payload: dict[str, Any],
    confirmed_blocks: list[dict[str, Any]],
    doc_text: str,
    report: Any = None,
    q_images: dict[str, Any] | None = None,
    *,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run only the local whole-paper score allocation over a fixed structure."""

    return ConfigGenerationOrchestrator(
        _policy(),
        report=report,
    ).allocate_scores_for_structure(
        structure_payload,
        confirmed_blocks,
        doc_text,
        q_images=q_images,
        checkpoint=checkpoint,
    )
