from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.training_criteria.analysis import (
    JUDGMENT_POINTS_SCHEMA,
    LEGACY_CRITERIA_SCHEMA,
    ProjectionValidationError,
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
    _reject_score_fields,
    training_criteria_from_solution_evidence,
)

LEGACY_EVIDENCE_SCHEMAS = (
    "question-solution-evidence-v1",
    "question-solution-evidence-v2",
)


def judgment_points_from_solution_evidence(
    evidence: QuestionSolutionEvidence,
    *,
    question: QuestionAnalysisInput,
) -> TrainingCriteriaDraft:
    """Store the full unscored evidence document as the current 判定点."""

    return training_criteria_from_solution_evidence(evidence, question=question)


def judgment_points_from_legacy_criteria(
    draft: TrainingCriteriaDraft,
) -> TrainingCriteriaDraft:
    """Read a thin historical criterion as compatible input."""

    if draft.schema_version not in {LEGACY_CRITERIA_SCHEMA, JUDGMENT_POINTS_SCHEMA}:
        raise ProjectionValidationError("criterion schema version is invalid")
    _reject_score_fields(draft.to_dict())
    return draft


def solution_evidence_payload_from_draft(
    draft: TrainingCriteriaDraft,
) -> dict[str, Any] | None:
    raw = str(draft.embedded_evidence_json or "").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ProjectionValidationError("embedded judgment-point evidence is invalid")
    _reject_score_fields(payload)
    return dict(payload)
