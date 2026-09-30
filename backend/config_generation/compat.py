from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .orchestration import ConfigGenerationOrchestrator
from .policy import build_config_generation_policy


def _policy():
    return build_config_generation_policy()


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
