"""Copy saved analysis products between exactly-duplicated questions.

When the importer detects that a newly imported question is identical
(question text + answer text) to an existing bank question, the two rows stay
separate but the new question can reuse the source's solution evidence and
training criteria.  Everything is re-anchored through the official write
paths so version ids and source-content hashes are recomputed for the new
question id; nothing is copied as raw rows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.solution_evidence.repository import (
    SolutionEvidenceRepository,
    _model_evidence_payload,
)
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
from question_bank.training_criteria.analysis import (
    solution_evidence_source_content_hash,
)
from question_bank.training_criteria.versioning import TrainingCriterionModule


LOGGER = logging.getLogger(__name__)

_EVIDENCE_MODEL_KEYS = (
    "schema_version",
    "question_id",
    "parts",
    "auxiliary_rules",
    "rationale",
    "confidence",
)
_USABLE_STATUSES = {"proposed", "approved"}


def copy_duplicate_analysis(
    db_path: str | Path,
    *,
    source_question_id: int,
    target_question_id: int,
    data_root: str | Path,
) -> dict[str, bool]:
    """Re-anchor the source question's usable analysis to the duplicate.

    Returns which products were actually copied.  Each product is independent
    and every failure is logged instead of raised, so a partial copy never
    blocks the surrounding import.
    """
    database = Path(db_path)
    loader = QuestionAnalysisInputLoader(
        db_path=database,
        data_root=Path(data_root),
    )
    try:
        loaded = loader.load((int(source_question_id), int(target_question_id)))
    except (KeyError, OSError, TypeError, ValueError):
        LOGGER.exception(
            "duplicate analysis copy cannot load questions %s -> %s",
            source_question_id,
            target_question_id,
        )
        return {"evidence": False, "criteria": False}
    source_input, target_input = loaded
    result = {"evidence": False, "criteria": False}
    try:
        result["evidence"] = _copy_evidence(
            database,
            source_input=source_input,
            target_input=target_input,
        )
    except Exception:  # noqa: BLE001 - reuse must never break an import
        LOGGER.exception(
            "duplicate evidence copy failed: %s -> %s",
            source_question_id,
            target_question_id,
        )
    try:
        result["criteria"] = _copy_criteria(
            database,
            source_input=source_input,
            target_input=target_input,
        )
    except Exception:  # noqa: BLE001 - reuse must never break an import
        LOGGER.exception(
            "duplicate criteria copy failed: %s -> %s",
            source_question_id,
            target_question_id,
        )
    return result


def _copy_evidence(
    db_path: Path,
    *,
    source_input: Any,
    target_input: Any,
) -> bool:
    repository = SolutionEvidenceRepository(db_path)
    latest = repository.latest(int(source_input.question_id))
    if not latest or str(latest.get("status")) not in _USABLE_STATUSES:
        return False
    payload = latest.get("evidence")
    if not isinstance(payload, dict):
        return False
    # Only reuse evidence that still matches the source question's current
    # content; a stale analysis must not leak onto the new question.
    source_hash = solution_evidence_source_content_hash(source_input)
    if str(latest.get("source_content_hash") or "") != source_hash:
        return False
    clean = _model_evidence_payload(payload)
    model_payload = {
        key: clean[key] for key in _EVIDENCE_MODEL_KEYS if key in clean
    }
    model_payload["question_id"] = int(target_input.question_id)
    resolver = CurrentFineTermResolver.from_active_database(db_path)
    evidence = QuestionSolutionEvidence.from_model_dict(
        model_payload,
        question_id=int(target_input.question_id),
        source_content_hash=solution_evidence_source_content_hash(target_input),
        resolver=resolver,
    )
    repository.save(
        evidence,
        source_kind="import",
        source_reference=f"duplicate-import:{int(source_input.question_id)}",
        created_by="question-import",
    )
    return True


def _copy_criteria(
    db_path: Path,
    *,
    source_input: Any,
    target_input: Any,
) -> bool:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT version.criteria_json AS criteria_json,
                   version.status AS status,
                   head.current_source_hash AS current_source_hash
            FROM training_criterion_heads head
            JOIN training_criterion_versions version
              ON version.version_id = head.current_version_id
            WHERE head.question_id = ?
            """,
            (int(source_input.question_id),),
        ).fetchone()
    if row is None or str(row["status"]) not in _USABLE_STATUSES:
        return False
    if str(row["current_source_hash"] or "") != (
        source_input.criterion_source_content_hash
    ):
        return False
    criteria = json.loads(str(row["criteria_json"]))
    if not isinstance(criteria, dict):
        return False
    # from_model_dict re-anchors source_content_hash to the target question;
    # the embedded question id is the only field that must be rewritten here.
    criteria["question_id"] = int(target_input.question_id)
    module = TrainingCriterionModule(db_path)
    module.propose(
        question=target_input,
        draft=criteria,
        source_kind="backfill",
        source_reference=f"duplicate-import:{int(source_input.question_id)}",
        actor_ref="question-import",
        reason="导入识别为完全相同题，复用已有判定点",
    )
    return True


__all__ = ["copy_duplicate_analysis"]
