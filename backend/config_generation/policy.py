from __future__ import annotations

from typing import Any, Callable

from .local_facts import (
    apply_local_question_facts,
    attach_reference_answer_images,
)
from .normalization import (
    force_payload_total_score,
    normalize_new_generated_config_payload,
    validate_generated_config,
)
from .orchestration import ConfigGenerationPolicy
from .quality import refresh_generated_config_quality_warnings


def build_config_generation_policy(
    *,
    validate_image_inputs: Callable[
        [list[dict[str, Any]], dict[str, Any] | None], None
    ],
) -> ConfigGenerationPolicy:
    """Bind P3-10 policy seams into the local score-allocation workflow."""
    return ConfigGenerationPolicy(
        validate_image_inputs=validate_image_inputs,
        normalize_payload=normalize_new_generated_config_payload,
        apply_local_question_facts=apply_local_question_facts,
        attach_reference_answer_images=attach_reference_answer_images,
        refresh_quality_warnings=refresh_generated_config_quality_warnings,
        validate_final_payload=validate_generated_config,
        force_total_score=force_payload_total_score,
    )
