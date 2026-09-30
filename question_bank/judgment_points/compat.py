from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from question_bank.database.schema import connect
from question_bank.judgment_points.contracts import (
    JUDGMENT_POINTS_SCHEMA,
    LEGACY_CRITERIA_SCHEMA,
    judgment_points_from_legacy_criteria,
    judgment_points_from_solution_evidence,
)
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.training_criteria.analysis import (
    ProjectionValidationError,
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
    _reject_score_fields,
)


def judgment_points_from_stored_criteria(
    payload: Mapping[str, Any],
    *,
    question: QuestionAnalysisInput,
) -> TrainingCriteriaDraft:
    """Read a stored criterion JSON as the current or compatible 判定点."""

    _reject_score_fields(payload)
    schema = str(payload.get("schema_version") or "")
    if schema not in {JUDGMENT_POINTS_SCHEMA, LEGACY_CRITERIA_SCHEMA}:
        raise ProjectionValidationError("stored criterion schema is not readable")
    draft = TrainingCriteriaDraft.from_model_dict(payload, question=question)
    return judgment_points_from_legacy_criteria(draft)


def load_current_judgment_points(
    db_path,
    question: QuestionAnalysisInput,
    *,
    evidence: QuestionSolutionEvidence | None = None,
) -> TrainingCriteriaDraft | None:
    """Prefer the criterion version; fall back to stored solution evidence."""

    from pathlib import Path

    with connect(Path(db_path)) as connection:
        row = connection.execute(
            """
            SELECT v.criteria_json, v.schema_version
            FROM training_criterion_heads h
            JOIN training_criterion_versions v
              ON v.version_id = COALESCE(h.approved_version_id, h.current_version_id)
            WHERE h.question_id = ?
            """,
            (int(question.question_id),),
        ).fetchone()
    if row is not None:
        payload = json.loads(str(row["criteria_json"]))
        if isinstance(payload, Mapping):
            try:
                return judgment_points_from_stored_criteria(
                    payload,
                    question=question,
                )
            except (ProjectionValidationError, ValueError, TypeError):
                pass
    if evidence is not None:
        return judgment_points_from_solution_evidence(
            evidence,
            question=question,
        )
    return None
