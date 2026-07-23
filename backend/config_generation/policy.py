from __future__ import annotations

from typing import Any, Callable

from .local_facts import (
    apply_local_question_facts,
    attach_reference_answer_images,
)
from .normalization import (
    force_payload_total_score,
    normalize_generated_config_schema,
    validate_generated_config,
)
from .orchestration import ConfigGenerationPolicy
from .quality import refresh_generated_config_quality_warnings
from .score_allocation import (
    apply_score_allocation,
    score_allocation_structure_summary,
    validate_score_allocation_payload,
)


def build_config_generation_policy(
    *,
    validate_image_inputs: Callable[
        [list[dict[str, Any]], dict[str, Any] | None],
        None,
    ],
    word_block_image_blobs: Callable[
        [dict[str, Any], int],
        list[bytes],
    ],
    is_transient_error: Callable[[Exception], bool],
) -> ConfigGenerationPolicy:
    """Bind pure P3-10 policy to the three legacy generation-only callbacks."""
    return ConfigGenerationPolicy(
        validate_image_inputs=validate_image_inputs,
        normalize_payload=normalize_generated_config_schema,
        apply_local_question_facts=apply_local_question_facts,
        attach_reference_answer_images=attach_reference_answer_images,
        refresh_quality_warnings=refresh_generated_config_quality_warnings,
        validate_final_payload=validate_generated_config,
        force_total_score=lambda payload, target: force_payload_total_score(
            payload,
            target_total=target,
        ),
        word_block_image_blobs=word_block_image_blobs,
        is_transient_error=is_transient_error,
        score_structure_summary=score_allocation_structure_summary,
        validate_score_payload=validate_score_allocation_payload,
        apply_score_allocation=apply_score_allocation,
    )


__all__ = ["build_config_generation_policy"]
