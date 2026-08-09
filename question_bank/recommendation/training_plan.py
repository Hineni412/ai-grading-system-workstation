from __future__ import annotations

from collections.abc import Mapping


DEFAULT_STAGE_RATIOS = {
    "direct": 0.60,
    "prerequisite": 0.30,
    "transfer": 0.10,
}


def allocate_stage_counts(
    question_count: int,
    ratios: Mapping[str, object] | None = None,
) -> dict[str, int]:
    count = int(question_count)
    if count <= 0:
        raise ValueError("question_count must be positive")
    source = dict(DEFAULT_STAGE_RATIOS if ratios is None else ratios)
    if set(source) != set(DEFAULT_STAGE_RATIOS):
        raise ValueError(f"stage ratios must contain exactly: {', '.join(DEFAULT_STAGE_RATIOS)}")
    resolved = {stage: float(source[stage]) for stage in DEFAULT_STAGE_RATIOS}
    if any(value < 0 or value > 1 for value in resolved.values()):
        raise ValueError("stage ratios must be between 0 and 1")
    if abs(sum(resolved.values()) - 1.0) > 1e-9:
        raise ValueError("stage ratios must sum to 1")
    raw = {stage: count * resolved[stage] for stage in DEFAULT_STAGE_RATIOS}
    result = {stage: int(raw[stage]) for stage in DEFAULT_STAGE_RATIOS}
    remaining = count - sum(result.values())
    order = sorted(
        DEFAULT_STAGE_RATIOS,
        key=lambda stage: (-(raw[stage] - result[stage]), list(DEFAULT_STAGE_RATIOS).index(stage)),
    )
    for stage in order[:remaining]:
        result[stage] += 1
    return result
