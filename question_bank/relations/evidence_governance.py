from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
    find_confirmation_conflicts,
)
from question_bank.relations.repository import (
    KnowledgeRelationDuplicate,
    KnowledgeRelationRepository,
)


@dataclass(frozen=True, slots=True)
class EvidenceRelationPolicy:
    suggestion_confidence: float = 0.80
    automatic_confidence: float = 0.98
    minimum_independent_evidence: int = 2

    def __post_init__(self) -> None:
        if not 0.0 <= self.suggestion_confidence <= 1.0:
            raise ValueError("suggestion confidence is invalid")
        if not self.suggestion_confidence <= self.automatic_confidence <= 1.0:
            raise ValueError("automatic confidence is invalid")
        if self.minimum_independent_evidence < 2:
            raise ValueError("automatic evidence threshold must be at least two")


class EvidenceRelationGovernanceService:
    """Govern prerequisite hints already present in solution evidence.

    The combined tagging model marks fine terms as direct or supporting
    prerequisites. This service never invents another semantic relation. It
    projects only unambiguous one-to-one core mappings, aggregates independent
    evidence points and lets deterministic graph constraints gate activation.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        policy: EvidenceRelationPolicy = EvidenceRelationPolicy(),
    ) -> None:
        self.repository = KnowledgeRelationRepository(Path(db_path))
        self.policy = policy

    def govern(
        self,
        hints: Sequence[Mapping[str, Any]],
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        skipped_ambiguous = 0
        skipped_low_confidence = 0
        for raw in hints:
            source_keys = _text_list(raw.get("source_keys"))
            target_keys = _text_list(raw.get("target_keys"))
            if not source_keys or not target_keys:
                skipped_ambiguous += 1
                continue
            confidence = _confidence(raw.get("confidence"))
            if confidence < self.policy.suggestion_confidence:
                skipped_low_confidence += 1
            exception_codes: list[str] = []
            if len(source_keys) != 1 or len(target_keys) != 1:
                skipped_ambiguous += 1
                exception_codes.append("ambiguous_mapping")
            if confidence < self.policy.suggestion_confidence:
                exception_codes.append("low_confidence")
            for source_key in source_keys:
                for target_key in target_keys:
                    if source_key == target_key:
                        continue
                    grouped[(source_key, target_key)].append(
                        {
                            "question_id": int(raw.get("question_id") or 0),
                            "evidence_point_id": str(raw.get("evidence_point_id") or ""),
                            "confidence": confidence,
                            "model_name": str(raw.get("model_name") or "unknown"),
                            "exception_codes": tuple(exception_codes),
                        }
                    )

        active = tuple(
            KnowledgeRelation(
                source_key=item.source_key,
                target_key=item.target_key,
                relation_type=item.relation_type,
                status=RelationStatus.CONFIRMED,
            )
            for item in self.repository.list_active_relations()
        )
        outcomes: list[dict[str, Any]] = []
        for (source_key, target_key), evidence in sorted(grouped.items()):
            try:
                relation = KnowledgeRelation(
                    source_key=source_key,
                    target_key=target_key,
                    relation_type=RelationType.PREREQUISITE,
                )
                conflicts = find_confirmation_conflicts(relation, active)
                question_ids = {
                    int(item["question_id"])
                    for item in evidence
                    if item["question_id"]
                }
                confidence = max(float(item["confidence"]) for item in evidence)
                custom_conflicts = {
                    str(code)
                    for item in evidence
                    for code in item["exception_codes"]
                }
                conflict_codes = {
                    *(item.value for item in conflicts),
                    *custom_conflicts,
                }
                model_names = {str(item["model_name"]) for item in evidence}
                source_reference = _question_reference(question_ids)
                rationale = _rationale(len(question_ids))
                pair_operation_id = (
                    f"{operation_id}:prerequisite:{source_key}:{target_key}"
                )
                try:
                    record = self.repository.create_suggestion(
                        relation,
                        source_kind="model",
                        rationale=rationale,
                        source_reference=source_reference,
                        source_operation_id=pair_operation_id,
                        model_name=" + ".join(sorted(model_names)),
                        model_version="combined-evidence-role-v1",
                        prompt_version="combined-v3",
                        confidence=confidence,
                        conflict_codes=tuple(sorted(conflict_codes)),
                    )
                except KnowledgeRelationDuplicate as exc:
                    if exc.status is RelationStatus.CONFIRMED:
                        outcomes.append({
                            "source_key": source_key,
                            "target_key": target_key,
                            "outcome": "reused_confirmed",
                            "relation_id": exc.relation_id,
                        })
                        continue
                    record = self.repository.get_relation(exc.relation_id)

                if record.status is RelationStatus.CONFIRMED:
                    outcomes.append({
                        "source_key": source_key,
                        "target_key": target_key,
                        "outcome": "reused_confirmed",
                        "relation_id": record.relation_id,
                    })
                    continue

                if record.status is RelationStatus.SUGGESTED:
                    question_ids.update(_question_ids(record.source_reference))
                    model_names.update(_model_names(record.model_name))
                    confidence = max(confidence, float(record.confidence or 0.0))
                    conflict_codes.update(record.conflict_codes)
                    merged_reference = _question_reference(question_ids)
                    merged_rationale = _rationale(len(question_ids))
                    if (
                        merged_reference != record.source_reference
                        or merged_rationale != record.rationale
                        or confidence != record.confidence
                        or tuple(sorted(conflict_codes)) != tuple(sorted(record.conflict_codes))
                        or " + ".join(sorted(model_names)) != record.model_name
                    ):
                        record = self.repository.accumulate_suggestion(
                            record.relation_id,
                            expected_revision=record.revision,
                            source_reference=merged_reference,
                            rationale=merged_rationale,
                            model_name=" + ".join(sorted(model_names)),
                            confidence=confidence,
                            conflict_codes=tuple(sorted(conflict_codes)),
                        )

                automatic = (
                    record.status is RelationStatus.SUGGESTED
                    and not conflict_codes
                    and confidence >= self.policy.automatic_confidence
                    and len(question_ids) >= self.policy.minimum_independent_evidence
                )
                outcome = "exception_queued"
                if automatic:
                    record = self.repository.transition(
                        record.relation_id,
                        expected_revision=record.revision,
                        to_status=RelationStatus.CONFIRMED,
                        actor_kind="system",
                        actor_ref="system:evidence-relation-policy-v1",
                        reason=(
                            "自动治理：高置信、至少两道不同题重复支持、映射唯一且图约束无冲突"
                        ),
                    )
                    outcome = "auto_confirmed"
                    active = (*active, record.as_contract())
                elif record.status is not RelationStatus.SUGGESTED:
                    outcome = "reused_decided"
                outcomes.append({
                    "source_key": source_key,
                    "target_key": target_key,
                    "outcome": outcome,
                    "relation_id": record.relation_id,
                    "confidence": confidence,
                    "evidence_count": len(question_ids),
                    "conflict_codes": sorted(conflict_codes),
                    "question_ids": sorted(question_ids),
                })
            except Exception as exc:  # noqa: BLE001
                outcomes.append({
                    "source_key": source_key,
                    "target_key": target_key,
                    "outcome": "failed",
                    "error_type": type(exc).__name__,
                    "question_ids": sorted({
                        int(item["question_id"])
                        for item in evidence
                        if item["question_id"]
                    }),
                })

        return {
            "candidate_count": len(grouped),
            "auto_confirmed_count": sum(
                item["outcome"] == "auto_confirmed" for item in outcomes
            ),
            "exception_count": sum(
                item["outcome"] == "exception_queued" for item in outcomes
            ),
            "reused_count": sum(
                item["outcome"] in {"reused_confirmed", "reused_decided"}
                for item in outcomes
            ),
            "failed_count": sum(item["outcome"] == "failed" for item in outcomes),
            "failed_question_ids": sorted({
                int(question_id)
                for item in outcomes
                if item["outcome"] == "failed"
                for question_id in item.get("question_ids", [])
            }),
            "skipped_ambiguous_count": skipped_ambiguous,
            "skipped_low_confidence_count": skipped_low_confidence,
            "outcomes": outcomes,
        }


def _question_ids(source_reference: str | None) -> set[int]:
    prefix = "questions:"
    text = str(source_reference or "")
    if not text.startswith(prefix):
        return set()
    result: set[int] = set()
    for value in text[len(prefix):].split(","):
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id > 0:
            result.add(question_id)
    return result


def _question_reference(question_ids: set[int]) -> str:
    return "questions:" + ",".join(map(str, sorted(question_ids)))


def _model_names(value: str | None) -> set[str]:
    return {
        item.strip()
        for item in str(value or "").split("+")
        if item.strip()
    }


def _rationale(question_count: int) -> str:
    return (
        "解题证据把目标知识标为直接考查，并把另一知识标为辅助/前置；"
        f"目前由 {question_count} 道不同题支持。"
    )


def _text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            str(item or "").strip().casefold()
            for item in value
            if str(item or "").strip()
        )
    )


def _confidence(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if 0.0 <= result <= 1.0 else 0.0


__all__ = ["EvidenceRelationGovernanceService", "EvidenceRelationPolicy"]
