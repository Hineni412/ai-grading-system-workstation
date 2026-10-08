from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any, Literal

from backend.llm.errors import classify_transport_error
from question_bank.models.tag_schema import TagAnalysis
from question_bank.question_types import is_type_key
from question_bank.services.ai_tagging_service import converge_tag_analysis
from question_bank.solution_evidence.contracts import (
    CoreResolution,
    FineTermResolver,
    QuestionSolutionEvidence,
    validate_evidence_fine_terms,
)
from question_bank.solution_evidence.convergence import converge_evidence_terms
from question_bank.solution_evidence.normalization import (
    normalize_model_solution_evidence,
)
from question_bank.solution_evidence.repository import (
    SolutionEvidenceRepository,
)
from question_bank.training_criteria.analysis import (
    AnalysisProjection,
    GatewayBatchResponse,
    GatewayResponseParseError,
    PlannedAnalysisBatch,
    ProjectionValidationError,
    QuestionAnalysisGateway,
    QuestionAnalysisInput,
    QuestionTypeSuggestion,
    TaxonomyProjectionReviewRequired,
    criteria_from_confirmed_rubric,
    grading_config_skeleton_from_solution_evidence,
    plan_analysis_batches,
    gateway_parallel_limit,
    solution_evidence_source_content_hash,
    stops_batch_scheduling,
    training_criteria_from_solution_evidence,
    training_criterion_source_reference,
    normalize_question_type_result,
    validate_question_type_labels,
)

_MAX_REJECTED_RESULT_CHARS = 50_000

LOGGER = logging.getLogger(__name__)


class UnmappedFineTermResolver:
    """Safe resolver for pre-insert analysis without a taxonomy database."""

    def resolve(self, fine_term_id: str) -> CoreResolution:
        return CoreResolution(status="unmapped", reason="deferred_governed_resolution")


@dataclass(frozen=True, slots=True)
class ConfigQuestionAnalysisSource:
    source_question_ref: str
    question: QuestionAnalysisInput

    def __post_init__(self) -> None:
        reference = str(self.source_question_ref or "").strip()
        if not reference:
            raise ValueError("source_question_ref must not be empty")
        object.__setattr__(self, "source_question_ref", reference)


@dataclass(frozen=True, slots=True)
class _DeferredAnalysisRequest:
    batch: PlannedAnalysisBatch
    request_id: str
    request_fingerprint: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeferredKnowledgeCandidate:
    """Minimal governed candidate snapshot needed to revalidate an adoption."""

    fine_term_id: str
    fine_term_name: str
    aliases: tuple[str, ...] = ()
    usage: str = ""

    def __post_init__(self) -> None:
        term_id = str(self.fine_term_id or "").strip()
        term_name = str(self.fine_term_name or "").strip()
        aliases = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in self.aliases
                if str(item or "").strip()
            )
        )
        if not term_id or not term_name:
            raise ValueError("deferred knowledge candidate is incomplete")
        object.__setattr__(self, "fine_term_id", term_id)
        object.__setattr__(self, "fine_term_name", term_name)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(
            self, "usage", str(self.usage or "").strip()
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.fine_term_id,
            "name": self.fine_term_name,
            "aliases": list(self.aliases),
        }
        if self.usage:
            result["usage"] = self.usage
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DeferredKnowledgeCandidate:
        keys = {str(key) for key in payload}
        if not {"id", "name", "aliases"} <= keys <= {
            "id",
            "name",
            "aliases",
            "usage",
        }:
            raise ValueError(
                "deferred knowledge candidate fields do not match the contract"
            )
        aliases = payload.get("aliases")
        if not isinstance(aliases, list) or not all(
            isinstance(item, str) for item in aliases
        ):
            raise ValueError("deferred knowledge candidate aliases are invalid")
        return cls(
            fine_term_id=str(payload.get("id") or ""),
            fine_term_name=str(payload.get("name") or ""),
            aliases=tuple(aliases),
            usage=str(payload.get("usage") or ""),
        )


@dataclass(frozen=True, slots=True)
class ConfirmedQuestionAdoptionLink:
    """Explicit capability proving a source question was linked after insertion."""

    source_question_ref: str
    bank_question_id: int
    confirmed_by: str

    def __post_init__(self) -> None:
        source_ref = str(self.source_question_ref or "").strip()
        actor = str(self.confirmed_by or "").strip()
        if not source_ref or not actor:
            raise ValueError("confirmed adoption link is incomplete")
        if isinstance(self.bank_question_id, bool) or int(self.bank_question_id) <= 0:
            raise ValueError("confirmed adoption question id must be positive")
        object.__setattr__(self, "source_question_ref", source_ref)
        object.__setattr__(self, "bank_question_id", int(self.bank_question_id))
        object.__setattr__(self, "confirmed_by", actor)


@dataclass(frozen=True, slots=True)
class DeferredCombinedAnalysisItem:
    source_question_ref: str
    analysis_question_id: int
    source_content_hash: str
    curriculum_volume_id: str
    taxonomy_contract_hash: str
    knowledge_candidates: tuple[DeferredKnowledgeCandidate, ...]
    tag_analysis: Mapping[str, Any]
    solution_evidence_payload: Mapping[str, Any]
    solution_evidence: QuestionSolutionEvidence
    taxonomy_audit: Mapping[str, Any]
    model_name: str
    operation_id: str
    reference_assessment: str = "insufficient"
    reference_assessment_reason: str = ""
    question_type_suggestion: Mapping[str, Any] | None = None
    # Exact-duplicate reuse: the source was matched to this canonical bank
    # question before any model call, so the item carries the canonical's
    # stored analysis. Adoption must not write it back onto the canonical.
    reused_from_question_id: int | None = None
    question_type_labels: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        reference = str(self.source_question_ref or "").strip()
        operation = str(self.operation_id or "").strip()
        model = str(self.model_name or "").strip()
        source_hash = _sha256_text(self.source_content_hash, "source_content_hash")
        volume_id = str(self.curriculum_volume_id or "").strip()
        contract_hash = _sha256_text(
            self.taxonomy_contract_hash,
            "taxonomy_contract_hash",
        )
        if not reference or not operation or not model or not volume_id:
            raise ValueError("deferred analysis identity fields must not be empty")
        if isinstance(self.analysis_question_id, bool) or int(
            self.analysis_question_id
        ) <= 0:
            raise ValueError("analysis_question_id must be positive")
        if self.solution_evidence.question_id != int(self.analysis_question_id):
            raise ValueError("deferred evidence question id does not match")
        if self.solution_evidence.source_content_hash != source_hash:
            raise ValueError("deferred evidence source hash does not match")
        candidates = tuple(self.knowledge_candidates)
        if not all(
            isinstance(item, DeferredKnowledgeCandidate) for item in candidates
        ) or len(
            {item.fine_term_id for item in candidates}
        ) != len(candidates):
            raise ValueError("deferred knowledge candidates are invalid or duplicated")
        validate_evidence_fine_terms(
            self.solution_evidence,
            _candidate_contract(candidates),
        )
        if self.question_type_labels is not None:
            labels = validate_question_type_labels(self.question_type_labels, _candidate_contract(candidates))
            primary = labels["primary_type_id"]
            for part in self.solution_evidence.parts:
                for point in part.evidence_points:
                    typed = [link for link in point.fine_term_links if is_type_key(link.fine_term_id)]
                    if [link.fine_term_id for link in typed] != ([primary] if primary else []) or any(link.role != "direct" for link in typed):
                        raise ValueError("deferred primary type links do not match whole-question labels")
            object.__setattr__(self, "question_type_labels", labels)
        audit = _normalize_taxonomy_audit(self.taxonomy_audit)
        assessment = str(self.reference_assessment or "").strip().casefold()
        if assessment not in {"consistent", "conflict", "insufficient"}:
            raise ValueError("reference assessment is invalid")
        object.__setattr__(self, "source_question_ref", reference)
        object.__setattr__(self, "analysis_question_id", int(self.analysis_question_id))
        object.__setattr__(self, "source_content_hash", source_hash)
        object.__setattr__(self, "curriculum_volume_id", volume_id)
        object.__setattr__(self, "taxonomy_contract_hash", contract_hash)
        object.__setattr__(self, "knowledge_candidates", candidates)
        object.__setattr__(self, "model_name", model)
        object.__setattr__(self, "operation_id", operation)
        object.__setattr__(self, "tag_analysis", dict(self.tag_analysis))
        object.__setattr__(self, "taxonomy_audit", audit)
        object.__setattr__(self, "reference_assessment", assessment)
        object.__setattr__(
            self,
            "reference_assessment_reason",
            str(self.reference_assessment_reason or "").strip()[:500],
        )
        object.__setattr__(
            self,
            "solution_evidence_payload",
            dict(self.solution_evidence_payload),
        )
        object.__setattr__(
            self,
            "question_type_suggestion",
            (
                dict(self.question_type_suggestion)
                if isinstance(self.question_type_suggestion, Mapping)
                else None
            ),
        )
        if self.reused_from_question_id is not None and (
            isinstance(self.reused_from_question_id, bool)
            or int(self.reused_from_question_id) <= 0
        ):
            raise ValueError("reused question id must be positive")
        object.__setattr__(
            self,
            "reused_from_question_id",
            (
                int(self.reused_from_question_id)
                if self.reused_from_question_id is not None
                else None
            ),
        )

    def grading_config_skeleton(self) -> dict[str, Any]:
        return grading_config_skeleton_from_solution_evidence(
            self.solution_evidence,
            question_ref=self.source_question_ref,
        )

    def evidence_payload_for_taxonomy_retry(
        self,
        *,
        question_id: int,
    ) -> dict[str, Any]:
        """Restore unresolved model links for a later local-only convergence."""

        payload = json.loads(
            json.dumps(dict(self.solution_evidence_payload), ensure_ascii=False)
        )
        payload["question_id"] = int(question_id)
        parts = payload.get("parts")
        if not isinstance(parts, list):
            return payload
        part_by_id = {
            str(part.get("part_id") or ""): part
            for part in parts
            if isinstance(part, dict)
        }
        for unresolved in self.taxonomy_audit.get("unresolved_links", []):
            if not isinstance(unresolved, Mapping):
                continue
            part = part_by_id.get(str(unresolved.get("part_id") or ""))
            points = part.get("evidence_points") if isinstance(part, dict) else None
            if not isinstance(points, list):
                continue
            point = next(
                (
                    item
                    for item in points
                    if isinstance(item, dict)
                    and str(item.get("evidence_point_id") or "")
                    == str(unresolved.get("evidence_point_id") or "")
                ),
                None,
            )
            if not isinstance(point, dict):
                continue
            role = str(unresolved.get("role") or "").strip().casefold()
            name = str(unresolved.get("submitted_name") or "").strip()
            term_id = str(unresolved.get("submitted_id") or "").strip()
            if not name or role not in {"direct", "supporting_prerequisite"}:
                continue
            links = point.get("fine_term_links")
            if not isinstance(links, list):
                point["fine_term_links"] = links = []
            signature = (term_id, name, role)
            if any(
                isinstance(item, Mapping)
                and (
                    str(item.get("fine_term_id") or "").strip(),
                    str(item.get("fine_term_name") or "").strip(),
                    str(item.get("role") or "").strip().casefold(),
                )
                == signature
                for item in links
            ):
                continue
            links.append(
                {
                    "fine_term_id": term_id,
                    "fine_term_name": name,
                    "role": role,
                }
            )
        return payload

    def bind_evidence(
        self,
        question: QuestionAnalysisInput,
        *,
        resolver: FineTermResolver,
    ) -> QuestionSolutionEvidence:
        self._validate_volume(question)
        actual_hash = solution_evidence_source_content_hash(question)
        if actual_hash != self.source_content_hash:
            raise ValueError("deferred solution evidence source content changed")
        payload = dict(self.solution_evidence_payload)
        payload["question_id"] = question.question_id
        evidence = QuestionSolutionEvidence.from_model_dict(
            payload,
            question_id=question.question_id,
            source_content_hash=actual_hash,
            resolver=resolver,
        )
        validate_evidence_fine_terms(
            evidence,
            _candidate_contract(self.knowledge_candidates),
        )
        return evidence

    def bind_linked_evidence(
        self,
        question: QuestionAnalysisInput,
        *,
        link: ConfirmedQuestionAdoptionLink,
        resolver: FineTermResolver,
    ) -> QuestionSolutionEvidence:
        """Rebind through an explicit link while retaining candidate validation."""

        if link.source_question_ref != self.source_question_ref:
            raise ValueError("confirmed adoption source reference does not match")
        if link.bank_question_id != question.question_id:
            raise ValueError("confirmed adoption question id does not match")
        self._validate_volume(question)
        payload = dict(self.solution_evidence_payload)
        payload["question_id"] = question.question_id
        evidence = QuestionSolutionEvidence.from_model_dict(
            payload,
            question_id=question.question_id,
            source_content_hash=solution_evidence_source_content_hash(question),
            resolver=resolver,
        )
        validate_evidence_fine_terms(
            evidence,
            _candidate_contract(self.knowledge_candidates),
        )
        return evidence

    def _validate_volume(self, question: QuestionAnalysisInput) -> None:
        actual = str(
            question.tagging_context.curriculum_volume_id or ""
        ).strip()
        if not actual or actual != self.curriculum_volume_id:
            raise ValueError("deferred adoption curriculum volume does not match")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "deferred-combined-analysis-item-v4",
            "source_question_ref": self.source_question_ref,
            "analysis_question_id": self.analysis_question_id,
            "source_content_hash": self.source_content_hash,
            "curriculum_volume_id": self.curriculum_volume_id,
            "taxonomy_contract_hash": self.taxonomy_contract_hash,
            "knowledge_candidates": [
                item.to_dict() for item in self.knowledge_candidates
            ],
            "tag_analysis": dict(self.tag_analysis),
            "solution_evidence_payload": dict(self.solution_evidence_payload),
            "taxonomy_audit": dict(self.taxonomy_audit),
            "reference_assessment": self.reference_assessment,
            "reference_assessment_reason": self.reference_assessment_reason,
            "model_name": self.model_name,
            "operation_id": self.operation_id,
        }
        if self.question_type_suggestion is not None:
            payload["schema_version"] = "deferred-combined-analysis-item-v6"
            payload["question_type_suggestion"] = dict(
                self.question_type_suggestion
            )
        if self.reused_from_question_id is not None:
            payload["schema_version"] = "deferred-combined-analysis-item-v7"
            payload["reused_from_question_id"] = int(
                self.reused_from_question_id
            )
        if self.question_type_labels is not None:
            payload["schema_version"] = "deferred-combined-analysis-item-v8"
            payload["question_type_labels"] = dict(self.question_type_labels)
        return {**payload, "content_hash": _hash_payload(payload)}

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
    ) -> DeferredCombinedAnalysisItem:
        version = str(payload.get("schema_version") or "")
        common_keys = {
            "schema_version",
            "source_question_ref",
            "analysis_question_id",
            "source_content_hash",
            "curriculum_volume_id",
            "taxonomy_contract_hash",
            "knowledge_candidates",
            "tag_analysis",
            "solution_evidence_payload",
            "model_name",
            "operation_id",
            "content_hash",
        }
        if version == "deferred-combined-analysis-item-v2":
            submitted_keys = {str(key) for key in payload}
            if submitted_keys not in {
                frozenset(common_keys),
                frozenset({
                    *common_keys,
                    "reference_assessment",
                    "reference_assessment_reason",
                }),
            }:
                raise ValueError("deferred analysis item fields do not match the contract")
        elif version == "deferred-combined-analysis-item-v3":
            _require_exact_keys(
                payload,
                {*common_keys, "taxonomy_audit"},
                "deferred analysis item",
            )
        elif version in {"deferred-combined-analysis-item-v4", "deferred-combined-analysis-item-v5"}:
            _require_exact_keys(
                payload,
                {
                    *common_keys,
                    "taxonomy_audit",
                    "reference_assessment",
                    "reference_assessment_reason",
                    *({"part_assessments"} if version.endswith("-v5") else set()),
                },
                "deferred analysis item",
            )
        elif version == "deferred-combined-analysis-item-v6":
            submitted_keys = {str(key) for key in payload}
            v6_base = {
                *common_keys,
                "taxonomy_audit",
                "reference_assessment",
                "reference_assessment_reason",
                "question_type_suggestion",
            }
            if submitted_keys != v6_base and submitted_keys != (
                v6_base | {"part_assessments"}
            ):
                raise ValueError(
                    "deferred analysis item fields do not match the contract"
                )
        elif version in {"deferred-combined-analysis-item-v7", "deferred-combined-analysis-item-v8"}:
            v7_base = {
                *common_keys,
                "taxonomy_audit",
                "reference_assessment",
                "reference_assessment_reason",
                *( {"reused_from_question_id"} if version.endswith("-v7") else {"question_type_labels"} ),
            }
            submitted_keys = {str(key) for key in payload}
            if not v7_base.issubset(submitted_keys) or not submitted_keys.issubset(
                v7_base | {"part_assessments", "question_type_suggestion", "reused_from_question_id"}
            ):
                raise ValueError(
                    "deferred analysis item fields do not match the contract"
                )
        else:
            raise ValueError("deferred analysis item version is invalid")
        submitted_hash = _sha256_text(payload.get("content_hash"), "content_hash")
        unhashed = {key: value for key, value in payload.items() if key != "content_hash"}
        if _hash_payload(unhashed) != submitted_hash:
            raise ValueError("deferred analysis item content hash does not match")
        raw_evidence = payload.get("solution_evidence_payload")
        raw_tag = payload.get("tag_analysis")
        raw_candidates = payload.get("knowledge_candidates")
        if not isinstance(raw_evidence, Mapping) or not isinstance(raw_tag, Mapping):
            raise ValueError("deferred analysis checkpoint item is invalid")
        if not isinstance(raw_candidates, list) or not all(
            isinstance(item, Mapping) for item in raw_candidates
        ):
            raise ValueError("deferred knowledge candidate snapshot is invalid")
        question_id = int(payload.get("analysis_question_id"))
        source_hash = str(payload.get("source_content_hash") or "")
        normalized_tag = TagAnalysis.from_dict(dict(raw_tag)).to_dict()
        if set(raw_tag) != set(normalized_tag):
            raise ValueError("deferred tag analysis contains unknown fields")
        evidence = QuestionSolutionEvidence.from_model_dict(
            raw_evidence,
            question_id=question_id,
            source_content_hash=source_hash,
            resolver=resolver,
        )
        taxonomy_audit = (
            _normalize_taxonomy_audit(payload.get("taxonomy_audit"))
            if version in {"deferred-combined-analysis-item-v3", "deferred-combined-analysis-item-v4", "deferred-combined-analysis-item-v5", "deferred-combined-analysis-item-v6", "deferred-combined-analysis-item-v7", "deferred-combined-analysis-item-v8"}
            else _legacy_taxonomy_audit(normalized_tag)
        )
        return cls(
            source_question_ref=str(payload.get("source_question_ref") or ""),
            analysis_question_id=question_id,
            source_content_hash=source_hash,
            curriculum_volume_id=str(payload.get("curriculum_volume_id") or ""),
            taxonomy_contract_hash=str(
                payload.get("taxonomy_contract_hash") or ""
            ),
            knowledge_candidates=tuple(
                DeferredKnowledgeCandidate.from_dict(item)
                for item in raw_candidates
                if isinstance(item, Mapping)
            ),
            tag_analysis=normalized_tag,
            solution_evidence_payload=dict(raw_evidence),
            solution_evidence=evidence,
            taxonomy_audit=taxonomy_audit,
            reference_assessment=(
                str(payload.get("reference_assessment") or "insufficient")
                if version in {"deferred-combined-analysis-item-v4", "deferred-combined-analysis-item-v5", "deferred-combined-analysis-item-v6", "deferred-combined-analysis-item-v7", "deferred-combined-analysis-item-v8"}
                else "insufficient"
            ),
            reference_assessment_reason=(
                str(payload.get("reference_assessment_reason") or "")
                if version in {"deferred-combined-analysis-item-v4", "deferred-combined-analysis-item-v5", "deferred-combined-analysis-item-v6", "deferred-combined-analysis-item-v7", "deferred-combined-analysis-item-v8"}
                else ""
            ),
            model_name=str(payload.get("model_name") or ""),
            question_type_suggestion=(
                dict(payload["question_type_suggestion"])
                if isinstance(payload.get("question_type_suggestion"), Mapping)
                else None
            ),
            question_type_labels=(dict(payload["question_type_labels"]) if version.endswith("-v8") else None),
            reused_from_question_id=(
                int(payload["reused_from_question_id"])
                if "reused_from_question_id" in payload
                else None
            ),
            operation_id=str(payload.get("operation_id") or ""),
        )

    @classmethod
    def from_checkpoint_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
    ) -> DeferredCombinedAnalysisItem:
        return cls.from_dict(payload, resolver=resolver)


def reused_analysis_item(
    *,
    source: ConfigQuestionAnalysisSource,
    bank_question_id: int,
    evidence: QuestionSolutionEvidence,
    evidence_payload: Mapping[str, Any],
    model_name: str,
    fine_term_links: Sequence[Mapping[str, Any]],
    operation_id: str,
) -> DeferredCombinedAnalysisItem:
    """Build a completed bundle item from a canonical bank question's analysis.

    The caller has already proven the source is an exact duplicate, so this
    factory only re-anchors the stored evidence payload and derives the
    governed candidate snapshot from the evidence's own links; the durable
    item still passes the normal contract validation.
    """
    source_hash = solution_evidence_source_content_hash(source.question)
    if evidence.question_id != int(source.question.question_id):
        raise ValueError("reused evidence question id does not match")
    if evidence.source_content_hash != source_hash:
        raise ValueError("reused evidence source hash does not match")
    candidates = tuple(
        DeferredKnowledgeCandidate(
            fine_term_id=str(link.get("fine_term_id") or ""),
            fine_term_name=str(link.get("fine_term_name") or ""),
        )
        for link in fine_term_links
        if isinstance(link, Mapping)
    )
    return DeferredCombinedAnalysisItem(
        source_question_ref=source.source_question_ref,
        analysis_question_id=int(source.question.question_id),
        source_content_hash=source_hash,
        curriculum_volume_id=str(
            source.question.tagging_context.curriculum_volume_id or ""
        ).strip(),
        taxonomy_contract_hash=_hash_payload(
            dict(source.question.taxonomy_contract)
        ),
        knowledge_candidates=candidates,
        tag_analysis=TagAnalysis.from_dict({}).to_dict(),
        solution_evidence_payload=dict(evidence_payload),
        solution_evidence=evidence,
        taxonomy_audit=_normalize_taxonomy_audit(
            {
                "schema_version": "deferred-taxonomy-audit-v1",
                "taxonomy_revision": 0,
                "status": "accepted",
                "tag_quality_status": "",
                "quality_notes": [],
                "retrieval_misses": [],
                "proposals": [],
                "secondary_matches": [],
                "unresolved_links": [],
            }
        ),
        reference_assessment="consistent",
        reference_assessment_reason="题库中已有完全相同题目的判定结果",
        model_name=model_name,
        operation_id=operation_id,
        reused_from_question_id=int(bank_question_id),
    )


@dataclass(frozen=True, slots=True)
class DeferredAnalysisFailure:
    source_question_ref: str
    analysis_question_id: int
    request_id: str
    batch_hash: str
    category: str
    validation_error: str = ""
    rejected_result: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.source_question_ref or "").strip():
            raise ValueError("failure source_question_ref must not be empty")
        if int(self.analysis_question_id) <= 0:
            raise ValueError("failure analysis_question_id must be positive")
        _sha256_text(self.request_id, "request_id")
        _sha256_text(self.batch_hash, "batch_hash")
        if not str(self.category or "").strip():
            raise ValueError("failure category must not be empty")
        validation_error = " ".join(
            str(self.validation_error or "").split()
        )[:1000]
        rejected_result = self.rejected_result
        if rejected_result is not None:
            if not isinstance(rejected_result, Mapping):
                raise ValueError("failure rejected_result must be an object")
            try:
                serialized = json.dumps(
                    rejected_result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                rejected_result = None
            else:
                rejected_result = (
                    json.loads(serialized)
                    if len(serialized) <= _MAX_REJECTED_RESULT_CHARS
                    else None
                )
        object.__setattr__(self, "validation_error", validation_error)
        object.__setattr__(self, "rejected_result", rejected_result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_question_ref": self.source_question_ref,
            "analysis_question_id": self.analysis_question_id,
            "request_id": self.request_id,
            "batch_hash": self.batch_hash,
            "category": self.category,
            "validation_error": self.validation_error,
            "rejected_result": (
                None
                if self.rejected_result is None
                else dict(self.rejected_result)
            ),
        }


@dataclass(frozen=True, slots=True)
class AnalysisRequestCheckpoint:
    request_id: str
    request_fingerprint: str
    batch_hash: str
    source_question_refs: tuple[str, ...]
    status: Literal[
        "running",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "outcome_unknown",
    ]
    model_name: str = ""
    raw_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _sha256_text(self.request_id, "request_id")
        _sha256_text(self.request_fingerprint, "request_fingerprint")
        _sha256_text(self.batch_hash, "batch_hash")
        if self.status not in {
            "running",
            "succeeded",
            "partial",
            "failed",
            "cancelled",
            "outcome_unknown",
        }:
            raise ValueError("request checkpoint status is invalid")
        if not self.source_question_refs:
            raise ValueError("request checkpoint sources must not be empty")
        if self.raw_payload is not None:
            object.__setattr__(self, "raw_payload", dict(self.raw_payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_fingerprint": self.request_fingerprint,
            "batch_hash": self.batch_hash,
            "source_question_refs": list(self.source_question_refs),
            "status": self.status,
            "model_name": self.model_name,
            "raw_payload": (
                None if self.raw_payload is None else dict(self.raw_payload)
            ),
        }


@dataclass(frozen=True, slots=True)
class DeferredCombinedAnalysisBundle:
    operation_id: str
    curriculum_volume_id: str
    items: tuple[DeferredCombinedAnalysisItem, ...]
    failures: tuple[DeferredAnalysisFailure, ...] = ()
    requests: tuple[AnalysisRequestCheckpoint, ...] = ()
    source_fingerprints: tuple[tuple[str, str], ...] = ()
    input_fingerprint: str = ""

    def __post_init__(self) -> None:
        operation = str(self.operation_id or "").strip()
        volume_id = str(self.curriculum_volume_id or "").strip()
        if not operation or not volume_id:
            raise ValueError("bundle operation and curriculum volume must not be empty")
        refs = [item.source_question_ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("source_question_ref is duplicated")
        fingerprint_refs: list[str] = []
        for reference, fingerprint in self.source_fingerprints:
            clean_reference = str(reference or "").strip()
            if not clean_reference:
                raise ValueError("source fingerprint reference must not be empty")
            _sha256_text(fingerprint, "source fingerprint")
            fingerprint_refs.append(clean_reference)
        if len(fingerprint_refs) != len(set(fingerprint_refs)):
            raise ValueError("source fingerprint reference is duplicated")
        allowed = set(fingerprint_refs)
        if any(item.source_question_ref not in allowed for item in self.items):
            raise ValueError("bundle item is outside the source fingerprint set")
        if any(item.source_question_ref not in allowed for item in self.failures):
            raise ValueError("bundle failure is outside the source fingerprint set")
        if self.input_fingerprint:
            _sha256_text(self.input_fingerprint, "input_fingerprint")
        object.__setattr__(self, "operation_id", operation)
        object.__setattr__(self, "curriculum_volume_id", volume_id)

    def filtered(
        self,
        source_refs: Sequence[str],
    ) -> DeferredCombinedAnalysisBundle:
        """Return the sub-bundle covering only the selected source refs."""
        wanted = {
            str(reference or "").strip()
            for reference in source_refs
            if str(reference or "").strip()
        }
        fingerprints = tuple(
            (reference, fingerprint)
            for reference, fingerprint in self.source_fingerprints
            if reference in wanted
        )
        if len(fingerprints) != len(wanted):
            raise ValueError("filtered analysis refs are outside the bundle scope")
        return DeferredCombinedAnalysisBundle(
            operation_id=self.operation_id,
            curriculum_volume_id=self.curriculum_volume_id,
            items=tuple(
                item
                for item in self.items
                if item.source_question_ref in wanted
            ),
            failures=tuple(
                item
                for item in self.failures
                if item.source_question_ref in wanted
            ),
            requests=tuple(
                request
                for request in self.requests
                if set(request.source_question_refs).issubset(wanted)
            ),
            source_fingerprints=fingerprints,
            input_fingerprint=_hash_payload(
                {
                    "contract": "combined-v3-memory",
                    "curriculum_volume_id": self.curriculum_volume_id,
                    "sources": fingerprints,
                }
            ),
        )

    @property
    def status(self) -> str:
        latest_request_status = {
            request.request_id: request.status for request in self.requests
        }
        if any(
            value in {"running", "outcome_unknown"}
            for value in latest_request_status.values()
        ):
            return "needs_resolution"
        incomplete = bool(self.failures or self.missing_source_refs)
        if incomplete and self.items:
            return "partial"
        if incomplete:
            return "failed"
        return "succeeded"

    @property
    def failed_source_refs(self) -> tuple[str, ...]:
        selected = {
            item.source_question_ref for item in self.failures
        } | set(self.missing_source_refs)
        selected.difference_update(self.uncertain_source_refs)
        return tuple(
            reference
            for reference, _fingerprint in self.source_fingerprints
            if reference in selected
        )

    @property
    def taxonomy_review_source_refs(self) -> tuple[str, ...]:
        selected = {
            item.source_question_ref
            for item in self.items
            if str(item.taxonomy_audit.get("status") or "")
            in {"needs_review", "unavailable", "legacy_unrecorded"}
        }
        return tuple(
            reference
            for reference, _fingerprint in self.source_fingerprints
            if reference in selected
        )

    @property
    def missing_source_refs(self) -> tuple[str, ...]:
        covered = {
            item.source_question_ref for item in self.items
        } | {
            item.source_question_ref for item in self.failures
        } | set(self.uncertain_source_refs)
        return tuple(
            reference
            for reference, _fingerprint in self.source_fingerprints
            if reference not in covered
        )

    @property
    def running_source_refs(self) -> tuple[str, ...]:
        return self._request_source_refs_with_statuses({"running"})

    @property
    def uncertain_source_refs(self) -> tuple[str, ...]:
        return self._request_source_refs_with_statuses(
            {"running", "outcome_unknown"}
        )

    def mark_interrupted_requests_unknown(self) -> DeferredCombinedAnalysisBundle:
        latest: dict[str, AnalysisRequestCheckpoint] = {}
        for request in self.requests:
            latest[request.request_id] = request
        interrupted = tuple(
            request for request in latest.values() if request.status == "running"
        )
        if not interrupted:
            return self
        terminal = tuple(
            AnalysisRequestCheckpoint(
                request_id=request.request_id,
                request_fingerprint=request.request_fingerprint,
                batch_hash=request.batch_hash,
                source_question_refs=request.source_question_refs,
                status="outcome_unknown",
                model_name=request.model_name,
            )
            for request in interrupted
        )
        return DeferredCombinedAnalysisBundle(
            operation_id=self.operation_id,
            curriculum_volume_id=self.curriculum_volume_id,
            items=self.items,
            failures=self.failures,
            requests=(*self.requests, *terminal),
            source_fingerprints=self.source_fingerprints,
            input_fingerprint=self.input_fingerprint,
        )

    def _request_source_refs_with_statuses(
        self,
        statuses: set[str],
    ) -> tuple[str, ...]:
        latest: dict[str, AnalysisRequestCheckpoint] = {}
        for request in self.requests:
            latest[request.request_id] = request
        selected = {
            reference
            for request in latest.values()
            if request.status in statuses
            for reference in request.source_question_refs
        }
        return tuple(
            reference
            for reference, _fingerprint in self.source_fingerprints
            if reference in selected
        )

    def get(self, source_question_ref: str) -> DeferredCombinedAnalysisItem:
        reference = str(source_question_ref or "").strip()
        for item in self.items:
            if item.source_question_ref == reference:
                return item
        raise KeyError(reference)

    def grading_config_skeletons(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.grading_config_skeleton() for item in self.items)

    def compose_generated_config(self, *, exam_title: str) -> dict[str, Any]:
        if self.failures or len(self.items) != len(self.source_fingerprints):
            raise ValueError(
                "deferred analysis must be complete before config composition"
            )
        payload = compose_generated_config_from_skeletons(
            self.grading_config_skeletons(),
            exam_title=exam_title,
        )
        meta = payload.setdefault("meta", {})
        meta["taxonomy_review_question_ids"] = list(
            self.taxonomy_review_source_refs
        )
        meta["taxonomy_review_count"] = len(self.taxonomy_review_source_refs)
        assessments = [
            {
                "question_id": item.source_question_ref,
                "assessment": item.reference_assessment,
                "reason": item.reference_assessment_reason,
            }
            for item in self.items
        ]
        meta["reference_assessments"] = assessments
        for item in assessments:
            if item["assessment"] == "conflict":
                meta.setdefault("warnings", []).append(
                    f"[来源提醒] {item['question_id']} 来源解析存在冲突，请教师核对"
                )
        return payload

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "deferred-combined-analysis-v4",
            "operation_id": self.operation_id,
            "curriculum_volume_id": self.curriculum_volume_id,
            "status": self.status,
            "input_fingerprint": self.input_fingerprint,
            "source_fingerprints": [list(item) for item in self.source_fingerprints],
            "items": [item.to_checkpoint_dict() for item in self.items],
            "failures": [item.to_dict() for item in self.failures],
            "requests": [item.to_dict() for item in self.requests],
        }
        return {**payload, "content_hash": _hash_payload(payload)}

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "operation_id",
                "curriculum_volume_id",
                "status",
                "input_fingerprint",
                "source_fingerprints",
                "items",
                "failures",
                "requests",
                "content_hash",
            },
            "deferred analysis bundle",
        )
        if payload.get("schema_version") not in {
            "deferred-combined-analysis-v2",
            "deferred-combined-analysis-v3",
            "deferred-combined-analysis-v4",
        }:
            raise ValueError("deferred analysis checkpoint version is invalid")
        submitted_hash = _sha256_text(payload.get("content_hash"), "content_hash")
        unhashed = {key: value for key, value in payload.items() if key != "content_hash"}
        if _hash_payload(unhashed) != submitted_hash:
            raise ValueError("deferred analysis bundle content hash does not match")
        effective_resolver = resolver or UnmappedFineTermResolver()
        raw_items = payload.get("items")
        raw_failures = payload.get("failures")
        raw_requests = payload.get("requests")
        if not all(isinstance(value, list) for value in (raw_items, raw_failures, raw_requests)):
            raise ValueError("deferred analysis checkpoint is invalid")
        if not all(isinstance(item, Mapping) for item in raw_items):
            raise ValueError("deferred analysis checkpoint item is invalid")
        if not all(isinstance(item, Mapping) for item in raw_failures):
            raise ValueError("deferred analysis failure is invalid")
        if not all(isinstance(item, Mapping) for item in raw_requests):
            raise ValueError("deferred analysis request is invalid")
        source_fingerprints_raw = payload.get("source_fingerprints")
        if not isinstance(source_fingerprints_raw, list):
            raise ValueError("deferred source fingerprints are invalid")
        if not all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(value, str) for value in item)
            for item in source_fingerprints_raw
        ):
            raise ValueError("deferred source fingerprint is invalid")
        bundle_version = str(payload.get("schema_version") or "")
        for item in raw_failures:
            if isinstance(item, Mapping):
                _require_exact_keys(
                    item,
                    {
                        "source_question_ref",
                        "analysis_question_id",
                        "request_id",
                        "batch_hash",
                        "category",
                        *(
                            {"validation_error", "rejected_result"}
                            if bundle_version == "deferred-combined-analysis-v4"
                            else set()
                        ),
                    },
                    "deferred failure",
                )
        for item in raw_requests:
            if isinstance(item, Mapping):
                _require_exact_keys(
                    item,
                    {
                        "request_id",
                        "request_fingerprint",
                        "batch_hash",
                        "source_question_refs",
                        "status",
                        "model_name",
                        "raw_payload",
                    },
                    "analysis request checkpoint",
                )
        bundle = cls(
            operation_id=str(payload.get("operation_id") or ""),
            curriculum_volume_id=str(payload.get("curriculum_volume_id") or ""),
            input_fingerprint=str(payload.get("input_fingerprint") or ""),
            source_fingerprints=tuple(
                (str(item[0]), str(item[1]))
                for item in source_fingerprints_raw
                if isinstance(item, list) and len(item) == 2
            ),
            items=tuple(
                DeferredCombinedAnalysisItem.from_dict(
                    item,
                    resolver=effective_resolver,
                )
                for item in raw_items
                if isinstance(item, Mapping)
            ),
            failures=tuple(
                DeferredAnalysisFailure(
                    source_question_ref=str(item.get("source_question_ref") or ""),
                    analysis_question_id=int(item.get("analysis_question_id")),
                    request_id=str(item.get("request_id") or ""),
                    batch_hash=str(item.get("batch_hash") or ""),
                    category=str(item.get("category") or "unknown"),
                    validation_error=str(
                        item.get("validation_error") or ""
                    ),
                    rejected_result=(
                        dict(item["rejected_result"])
                        if isinstance(item.get("rejected_result"), Mapping)
                        else None
                    ),
                )
                for item in raw_failures
                if isinstance(item, Mapping)
            ),
            requests=tuple(
                AnalysisRequestCheckpoint(
                    request_id=str(item.get("request_id") or ""),
                    request_fingerprint=str(item.get("request_fingerprint") or ""),
                    batch_hash=str(item.get("batch_hash") or ""),
                    source_question_refs=tuple(
                        str(value) for value in item.get("source_question_refs", [])
                    ),
                    status=str(item.get("status") or "failed"),  # type: ignore[arg-type]
                    model_name=str(item.get("model_name") or ""),
                    raw_payload=(
                        dict(item["raw_payload"])
                        if isinstance(item.get("raw_payload"), Mapping)
                        else None
                    ),
                )
                for item in raw_requests
                if isinstance(item, Mapping)
            ),
        )
        if str(payload.get("status") or "") != bundle.status:
            raise ValueError("deferred analysis bundle status does not match")
        if len(bundle.input_fingerprint) != 64:
            raise ValueError("deferred analysis input fingerprint is invalid")
        return bundle

    @classmethod
    def from_checkpoint_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        return cls.from_dict(payload, resolver=resolver)


class DeferredCombinedQuestionAnalysisModule:
    """Analyze config-source questions once, with no database writes."""

    def __init__(
        self,
        *,
        gateway: QuestionAnalysisGateway,
        resolver: FineTermResolver | None = None,
        taxonomy_governance: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.resolver = resolver or UnmappedFineTermResolver()
        self.taxonomy_governance = taxonomy_governance

    def analyze(
        self,
        *,
        operation_id: str,
        curriculum_volume_id: str,
        sources: Sequence[ConfigQuestionAnalysisSource],
        reused_items: Mapping[
            str, DeferredCombinedAnalysisItem | DeferredAnalysisFailure
        ] | None = None,
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        clean_operation, volume_id, normalized = _normalize_sources(
            operation_id,
            curriculum_volume_id,
            sources,
        )
        reused = dict(reused_items or {})
        if reused:
            available = {item.source_question_ref for item in normalized}
            invalid = sorted(
                reference
                for reference, item in reused.items()
                if reference not in available
                or item.source_question_ref != reference
                or (isinstance(item, DeferredCombinedAnalysisItem) and (
                    item.reused_from_question_id is None
                    or item.curriculum_volume_id != volume_id
                    or item.operation_id != clean_operation
                ))
                or (isinstance(item, DeferredAnalysisFailure) and item.category not in {"duplicate_analysis_missing", "duplicate_content_uncertain"})
            )
            if invalid:
                raise ValueError(
                    "reused analysis items must match parsed sources"
                )
        source_fingerprints = tuple(
            (
                item.source_question_ref,
                solution_evidence_source_content_hash(item.question),
            )
            for item in normalized
        )
        input_fingerprint = _hash_payload(
            {
                "contract": "combined-v3-memory",
                "curriculum_volume_id": volume_id,
                "sources": source_fingerprints,
            }
        )
        return self._run(
            operation_id=clean_operation,
            curriculum_volume_id=volume_id,
            selected_sources=tuple(
                item
                for item in normalized
                if item.source_question_ref not in reused
            ),
            source_fingerprints=source_fingerprints,
            input_fingerprint=input_fingerprint,
            base_items=tuple(
                reused[item.source_question_ref]
                for item in normalized
                if isinstance(reused.get(item.source_question_ref), DeferredCombinedAnalysisItem)
            ),
            base_failures=tuple(item for item in reused.values() if isinstance(item, DeferredAnalysisFailure)),
            base_requests=(),
            checkpoint=checkpoint,
        )

    def retry_failed(
        self,
        previous: DeferredCombinedAnalysisBundle,
        *,
        sources: Sequence[ConfigQuestionAnalysisSource],
        curriculum_volume_id: str,
        retry_source_refs: Sequence[str] | None = None,
        retry_uncertain: bool = False,
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        clean_operation, volume_id, normalized = _normalize_sources(
            previous.operation_id,
            curriculum_volume_id,
            sources,
        )
        if volume_id != previous.curriculum_volume_id:
            raise ValueError("deferred retry curriculum volume changed")
        current_fingerprints = tuple(
            (
                item.source_question_ref,
                solution_evidence_source_content_hash(item.question),
            )
            for item in normalized
        )
        current_input_fingerprint = _hash_payload(
            {
                "contract": "combined-v3-memory",
                "curriculum_volume_id": volume_id,
                "sources": current_fingerprints,
            }
        )
        if (
            current_input_fingerprint != previous.input_fingerprint
            or current_fingerprints != previous.source_fingerprints
        ):
            raise ValueError("deferred retry input changed")
        failed_refs = set(previous.failed_source_refs)
        uncertain_refs = set(previous.uncertain_source_refs)
        available_refs = uncertain_refs if retry_uncertain else failed_refs
        available_refs -= {failure.source_question_ref for failure in previous.failures
                           if failure.category in {"duplicate_analysis_missing", "duplicate_content_uncertain"}}
        selected_refs: set[str] | None = None
        if retry_source_refs is not None:
            requested = tuple(
                str(value or "").strip() for value in retry_source_refs
            )
            if (
                not requested
                or any(not value for value in requested)
                or len(set(requested)) != len(requested)
            ):
                raise ValueError(
                    "retry_source_refs must be a non-empty unique sequence"
                )
            selected_refs = set(requested)
            if retry_uncertain and selected_refs != uncertain_refs:
                raise ValueError(
                    "retry_source_refs must contain all uncertain sources"
                )
            if not retry_uncertain and not selected_refs.issubset(failed_refs):
                raise ValueError(
                    "retry_source_refs must be a subset of previous failures"
                )
        selected = tuple(
            item
            for item in normalized
            if item.source_question_ref in available_refs
            and (
                selected_refs is None
                or item.source_question_ref in selected_refs
            )
        )
        if not selected:
            return previous
        failure_by_ref = {
            item.source_question_ref: item
            for item in previous.failures
        }
        selected = tuple(
            _source_with_repair_context(
                item,
                failure_by_ref.get(item.source_question_ref),
            )
            for item in selected
        )
        pending_refs = {item.source_question_ref for item in selected}
        base_requests = previous.requests
        if retry_uncertain:
            latest: dict[str, AnalysisRequestCheckpoint] = {}
            for request in previous.requests:
                latest[request.request_id] = request
            closed_unknown = tuple(
                AnalysisRequestCheckpoint(
                    request_id=request.request_id,
                    request_fingerprint=request.request_fingerprint,
                    batch_hash=request.batch_hash,
                    source_question_refs=request.source_question_refs,
                    # Keep the durable checkpoint readable by older builds.
                    # The new request gets its own id, while this local
                    # unknown outcome is explicitly closed as failed only
                    # after the teacher authorizes another paid call.
                    status="failed",
                    model_name=request.model_name,
                )
                for request in latest.values()
                if request.status in {"running", "outcome_unknown"}
                and set(request.source_question_refs).issubset(pending_refs)
            )
            if not closed_unknown:
                raise ValueError("uncertain request checkpoint is unavailable")
            base_requests = (*previous.requests, *closed_unknown)
        return self._run(
            operation_id=clean_operation,
            curriculum_volume_id=volume_id,
            selected_sources=selected,
            source_fingerprints=current_fingerprints,
            input_fingerprint=current_input_fingerprint,
            base_items=previous.items,
            # Keep the previous rejected result until the newly authorized
            # request has a definite replacement. If the request becomes
            # uncertain, a later teacher-confirmed retry still has the exact
            # repair context after restart.
            base_failures=previous.failures,
            base_requests=base_requests,
            checkpoint=checkpoint,
        )

    def reanalyze_selected(
        self,
        previous: DeferredCombinedAnalysisBundle,
        *,
        sources: Sequence[ConfigQuestionAnalysisSource],
        curriculum_volume_id: str,
        source_refs: Sequence[str],
        validation_issues_by_ref: Mapping[
            str, Sequence[Mapping[str, Any]]
        ],
        repair_attempts_by_ref: Mapping[str, int] | None = None,
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        """Replace selected successful analyses after downstream quality fails.

        A combined analysis can satisfy its transport/shape contract and still
        produce a grading skeleton that fails the later scoring-quality gate.
        This explicit teacher-triggered seam preserves every other successful
        item while issuing a new physical request only for the selected refs.
        """

        clean_operation, volume_id, normalized = _normalize_sources(
            previous.operation_id,
            curriculum_volume_id,
            sources,
        )
        if volume_id != previous.curriculum_volume_id:
            raise ValueError("deferred retry curriculum volume changed")
        current_fingerprints = tuple(
            (
                item.source_question_ref,
                solution_evidence_source_content_hash(item.question),
            )
            for item in normalized
        )
        current_input_fingerprint = _hash_payload(
            {
                "contract": "combined-v3-memory",
                "curriculum_volume_id": volume_id,
                "sources": current_fingerprints,
            }
        )
        if (
            current_input_fingerprint != previous.input_fingerprint
            or current_fingerprints != previous.source_fingerprints
        ):
            raise ValueError("deferred retry input changed")
        requested = tuple(str(value or "").strip() for value in source_refs)
        if (
            not requested
            or any(not value for value in requested)
            or len(set(requested)) != len(requested)
        ):
            raise ValueError("source_refs must be a non-empty unique sequence")
        selected_refs = set(requested)
        successful_refs = {
            item.source_question_ref for item in previous.items
        }
        if not selected_refs.issubset(successful_refs):
            raise ValueError("source_refs must select successful analyses")
        selected = tuple(
            item
            for item in normalized
            if item.source_question_ref in selected_refs
        )
        if len(selected) != len(selected_refs):
            raise ValueError("selected analysis source is unavailable")
        issue_map = {
            str(reference): tuple(
                dict(issue)
                for issue in validation_issues_by_ref.get(reference, ())
                if isinstance(issue, Mapping)
            )
            for reference in selected_refs
        }
        if any(not issue_map.get(reference) for reference in selected_refs):
            raise ValueError("selected reanalysis requires exact validation issues")
        previous_items = {
            item.source_question_ref: item
            for item in previous.items
            if item.source_question_ref in selected_refs
        }
        repair_failures = tuple(
            _local_quality_repair_failure(
                previous_items[reference],
                issue_map[reference],
                attempt=max(
                    1,
                    int((repair_attempts_by_ref or {}).get(reference, 1)),
                ),
                operation_id=clean_operation,
            )
            for reference in requested
        )
        failure_by_ref = {
            item.source_question_ref: item for item in repair_failures
        }
        selected = tuple(
            _source_with_repair_context(
                item,
                failure_by_ref.get(item.source_question_ref),
            )
            for item in selected
        )
        return self._run(
            operation_id=clean_operation,
            curriculum_volume_id=volume_id,
            selected_sources=selected,
            source_fingerprints=current_fingerprints,
            input_fingerprint=current_input_fingerprint,
            base_items=tuple(
                item
                for item in previous.items
                if item.source_question_ref not in selected_refs
            ),
            base_failures=(*tuple(
                item
                for item in previous.failures
                if item.source_question_ref not in selected_refs
            ), *repair_failures),
            base_requests=previous.requests,
            checkpoint=checkpoint,
            projection="training_criteria",
            preserved_items_by_ref=previous_items,
        )

    def regenerate_selected(
        self,
        previous: DeferredCombinedAnalysisBundle,
        *,
        sources: Sequence[ConfigQuestionAnalysisSource],
        curriculum_volume_id: str,
        source_refs: Sequence[str],
        reused_items: Mapping[
            str, DeferredCombinedAnalysisItem | DeferredAnalysisFailure
        ]
        | None = None,
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        """Re-run fresh analysis requests for selected refs, preserving the rest.

        Unlike :meth:`reanalyze_selected` the selected sources are sent as
        new physical requests without a repair context, and refs missing
        from the previous bundle simply extend its scope. Every untouched
        item and failure is preserved verbatim.
        """

        clean_operation, volume_id, normalized = _normalize_sources(
            previous.operation_id,
            curriculum_volume_id,
            sources,
        )
        if volume_id != previous.curriculum_volume_id:
            raise ValueError("deferred retry curriculum volume changed")
        current_fingerprints = tuple(
            (
                item.source_question_ref,
                solution_evidence_source_content_hash(item.question),
            )
            for item in normalized
        )
        current_fingerprint_map = dict(current_fingerprints)
        if any(
            current_fingerprint_map.get(reference) != fingerprint
            for reference, fingerprint in previous.source_fingerprints
        ):
            raise ValueError("deferred retry input changed")
        requested = tuple(str(value or "").strip() for value in source_refs)
        if (
            not requested
            or any(not value for value in requested)
            or len(set(requested)) != len(requested)
        ):
            raise ValueError("source_refs must be a non-empty unique sequence")
        selected_refs = set(requested)
        if any(
            reference not in current_fingerprint_map
            for reference in selected_refs
        ):
            raise ValueError("selected analysis source is unavailable")
        reused = dict(reused_items or {})
        if reused:
            invalid = sorted(
                reference
                for reference, item in reused.items()
                if reference not in selected_refs
                or item.source_question_ref != reference
                or (
                    isinstance(item, DeferredCombinedAnalysisItem)
                    and (
                        item.reused_from_question_id is None
                        or item.curriculum_volume_id != volume_id
                        or item.operation_id != clean_operation
                    )
                )
                or (
                    isinstance(item, DeferredAnalysisFailure)
                    and item.category
                    not in {
                        "duplicate_analysis_missing",
                        "duplicate_content_uncertain",
                    }
                )
            )
            if invalid:
                raise ValueError(
                    "reused analysis items must match selected sources"
                )
        selected = tuple(
            item
            for item in normalized
            if item.source_question_ref in selected_refs
            and item.source_question_ref not in reused
        )
        scoped_refs = {
            reference for reference, _fingerprint in previous.source_fingerprints
        } | selected_refs
        scoped_fingerprints = tuple(
            pair
            for pair in current_fingerprints
            if pair[0] in scoped_refs
        )
        return self._run(
            operation_id=clean_operation,
            curriculum_volume_id=volume_id,
            selected_sources=selected,
            source_fingerprints=scoped_fingerprints,
            input_fingerprint=_hash_payload(
                {
                    "contract": "combined-v3-memory",
                    "curriculum_volume_id": volume_id,
                    "sources": scoped_fingerprints,
                }
            ),
            base_items=tuple(
                item
                for item in previous.items
                if item.source_question_ref not in selected_refs
            )
            + tuple(
                reused[item.source_question_ref]
                for item in normalized
                if item.source_question_ref in selected_refs
                and isinstance(
                    reused.get(item.source_question_ref),
                    DeferredCombinedAnalysisItem,
                )
            ),
            base_failures=tuple(
                item
                for item in previous.failures
                if item.source_question_ref not in selected_refs
            )
            + tuple(
                item
                for item in reused.values()
                if isinstance(item, DeferredAnalysisFailure)
            ),
            base_requests=previous.requests,
            checkpoint=checkpoint,
        )

    def resume_interrupted(
        self,
        checkpoint_bundle: DeferredCombinedAnalysisBundle,
        *,
        sources: Sequence[ConfigQuestionAnalysisSource],
        curriculum_volume_id: str,
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        _clean_operation, volume_id, normalized = _normalize_sources(
            checkpoint_bundle.operation_id,
            curriculum_volume_id,
            sources,
        )
        current_fingerprints = tuple(
            (
                item.source_question_ref,
                solution_evidence_source_content_hash(item.question),
            )
            for item in normalized
        )
        current_input_fingerprint = _hash_payload(
            {
                "contract": "combined-v3-memory",
                "curriculum_volume_id": volume_id,
                "sources": current_fingerprints,
            }
        )
        if (
            current_input_fingerprint != checkpoint_bundle.input_fingerprint
            or current_fingerprints != checkpoint_bundle.source_fingerprints
        ):
            raise ValueError("deferred retry input changed")
        recovered = checkpoint_bundle.mark_interrupted_requests_unknown()
        if recovered is not checkpoint_bundle and checkpoint is not None:
            checkpoint(recovered)
        return recovered

    def _run(
        self,
        *,
        operation_id: str,
        curriculum_volume_id: str,
        selected_sources: tuple[ConfigQuestionAnalysisSource, ...],
        source_fingerprints: tuple[tuple[str, str], ...],
        input_fingerprint: str,
        base_items: tuple[DeferredCombinedAnalysisItem, ...],
        base_failures: tuple[DeferredAnalysisFailure, ...],
        base_requests: tuple[AnalysisRequestCheckpoint, ...],
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None,
        projection: AnalysisProjection = "both",
        preserved_items_by_ref: Mapping[
            str, DeferredCombinedAnalysisItem
        ] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        result = list(base_items)
        failures = list(base_failures)
        requests = list(base_requests)
        order = {
            reference: index
            for index, (reference, _fingerprint) in enumerate(source_fingerprints)
        }

        def prior_failure(
            source_question_ref: str,
        ) -> DeferredAnalysisFailure | None:
            return next(
                (
                    item
                    for item in reversed(failures)
                    if item.source_question_ref == source_question_ref
                ),
                None,
            )

        def remove_failure(source_question_ref: str) -> None:
            failures[:] = [
                item
                for item in failures
                if item.source_question_ref != source_question_ref
            ]

        def replace_failure(
            item: DeferredAnalysisFailure,
            *,
            preserve_previous_context: bool = False,
        ) -> None:
            previous_failure = prior_failure(item.source_question_ref)
            remove_failure(item.source_question_ref)
            if (
                preserve_previous_context
                and item.rejected_result is None
                and previous_failure is not None
                and previous_failure.rejected_result is not None
            ):
                item = replace(
                    item,
                    validation_error=(
                        item.validation_error
                        or previous_failure.validation_error
                    ),
                    rejected_result=previous_failure.rejected_result,
                )
            failures.append(item)

        def snapshot() -> DeferredCombinedAnalysisBundle:
            bundle = DeferredCombinedAnalysisBundle(
                operation_id=operation_id,
                curriculum_volume_id=curriculum_volume_id,
                items=tuple(
                    sorted(result, key=lambda item: order[item.source_question_ref])
                ),
                failures=tuple(
                    sorted(
                        failures,
                        key=lambda item: order[item.source_question_ref],
                    )
                ),
                requests=tuple(requests),
                source_fingerprints=source_fingerprints,
                input_fingerprint=input_fingerprint,
            )
            if checkpoint is not None:
                checkpoint(bundle)
            return bundle

        by_question_id = {
            item.question.question_id: item for item in selected_sources
        }
        batches = plan_analysis_batches(
            tuple(item.question for item in selected_sources),
            projection=projection,
        )
        prior_attempts = len({item.request_id for item in requests})
        planned_requests: list[_DeferredAnalysisRequest] = []
        for batch_index, batch in enumerate(batches, start=1):
            request_id = _hash_payload(
                {
                    "operation_id": operation_id,
                    "batch_hash": batch.batch_hash,
                    "attempt": prior_attempts + batch_index,
                    "contract": "combined-v3-memory",
                }
            )
            source_refs = tuple(
                by_question_id[item].source_question_ref
                for item in batch.question_ids
            )
            request_fingerprint = _hash_payload(
                {
                    "operation_id": operation_id,
                    "batch_hash": batch.batch_hash,
                    "source_question_refs": source_refs,
                }
            )
            planned_requests.append(
                _DeferredAnalysisRequest(
                    batch=batch,
                    request_id=request_id,
                    request_fingerprint=request_fingerprint,
                    source_refs=source_refs,
                )
            )

        if not planned_requests:
            return snapshot()

        worker_count = min(
            len(planned_requests),
            gateway_parallel_limit(self.gateway),
        )
        executor = ThreadPoolExecutor(
            max_workers=max(1, worker_count),
            thread_name_prefix="combined-question-analysis",
        )
        future_map: dict[
            Future[GatewayBatchResponse],
            _DeferredAnalysisRequest,
        ] = {}
        next_request_index = 0
        stop_scheduling = False
        stop_category = "cancelled"

        def record_unscheduled_cancellations(category: str = "cancelled") -> None:
            recorded_refs = {
                item.source_question_ref for item in result
            } | {
                item.source_question_ref for item in failures
            }
            for planned in planned_requests[next_request_index:]:
                for question_id, source_ref in zip(
                    planned.batch.question_ids,
                    planned.source_refs,
                    strict=True,
                ):
                    if source_ref in recorded_refs:
                        continue
                    failures.append(
                        DeferredAnalysisFailure(
                            source_question_ref=source_ref,
                            analysis_question_id=question_id,
                            request_id=planned.request_id,
                            batch_hash=planned.batch.batch_hash,
                            category=category,
                        )
                    )
                    recorded_refs.add(source_ref)

        def submit_next() -> None:
            nonlocal next_request_index
            planned = planned_requests[next_request_index]
            next_request_index += 1
            requests.append(
                AnalysisRequestCheckpoint(
                    request_id=planned.request_id,
                    request_fingerprint=planned.request_fingerprint,
                    batch_hash=planned.batch.batch_hash,
                    source_question_refs=planned.source_refs,
                    status="running",
                )
            )
            # Persist the running request before a worker can send it. A crash
            # can therefore never make an outbound request look unsent.
            snapshot()
            future = executor.submit(
                self.gateway.analyze,
                planned.batch,
                projection=projection,
                operation_id=operation_id,
                request_id=planned.request_id,
            )
            future_map[future] = planned

        def fill_available_slots() -> None:
            current_window = min(
                worker_count,
                gateway_parallel_limit(self.gateway),
            )
            while (
                next_request_index < len(planned_requests)
                and len(future_map) < current_window
            ):
                submit_next()

        try:
            fill_available_slots()

            while future_map:
                future = next(as_completed(tuple(future_map)))
                planned = future_map.pop(future)
                batch = planned.batch
                request_id = planned.request_id
                request_fingerprint = planned.request_fingerprint
                source_refs = planned.source_refs
                response_received = False
                try:
                    response = future.result()
                    response_received = True
                    raw_items = _response_items(response, batch.question_ids)
                except Exception as exc:
                    response_received = response_received or isinstance(
                        exc,
                        GatewayResponseParseError,
                    )
                    category = _analysis_error_category(exc)
                    if _analysis_outcome_is_unknown(exc):
                        requests.append(
                            AnalysisRequestCheckpoint(
                                request_id=request_id,
                                request_fingerprint=request_fingerprint,
                                batch_hash=batch.batch_hash,
                                source_question_refs=source_refs,
                                status="outcome_unknown",
                            )
                        )
                        snapshot()
                        if stops_batch_scheduling(category):
                            stop_scheduling = True
                            stop_category = category
                    else:
                        for question_id in batch.question_ids:
                            source_ref = by_question_id[
                                question_id
                            ].source_question_ref
                            replace_failure(
                                DeferredAnalysisFailure(
                                    source_question_ref=source_ref,
                                    analysis_question_id=question_id,
                                    request_id=request_id,
                                    batch_hash=batch.batch_hash,
                                    category=category,
                                ),
                                preserve_previous_context=(
                                    not response_received
                                ),
                            )
                        requests.append(
                            AnalysisRequestCheckpoint(
                                request_id=request_id,
                                request_fingerprint=request_fingerprint,
                                batch_hash=batch.batch_hash,
                                source_question_refs=source_refs,
                                status=(
                                    "cancelled"
                                    if category == "cancelled"
                                    else "failed"
                                ),
                            )
                        )
                        snapshot()
                        if stops_batch_scheduling(category):
                            stop_scheduling = True
                            stop_category = category
                else:
                    parsed_count = 0
                    validated_raw_results: list[dict[str, Any]] = []
                    for question in batch.questions:
                        source = by_question_id[question.question_id]
                        validation_category = "combined_item_contract"
                        raw: object = None
                        try:
                            raw = raw_items[question.question_id]
                            preserved_item = (preserved_items_by_ref or {}).get(
                                source.source_question_ref
                            )
                            raw_tag = (
                                raw.get("tag_analysis")
                                if projection in {"both", "tag"}
                                else (
                                    {
                                        key: preserved_item.tag_analysis.get(key)
                                        for key in _MODEL_TAG_PAYLOAD_FIELDS
                                    }
                                    if preserved_item is not None
                                    else None
                                )
                            )
                            raw_evidence = raw.get("solution_evidence")
                            if not isinstance(raw_evidence, Mapping):
                                raise ValueError(
                                    "combined response solution_evidence is invalid"
                                )
                            validation_category = "question_type_labels"
                            raw = normalize_question_type_result(question, {**dict(raw), "tag_analysis": raw_tag})
                            raw_tag = raw["tag_analysis"]
                            raw_evidence = raw["solution_evidence"]
                            source_hash = solution_evidence_source_content_hash(
                                question
                            )
                            reference_assessment = str(
                                raw.get("reference_assessment") or "insufficient"
                            ).strip().casefold()
                            if reference_assessment not in {
                                "consistent", "conflict", "insufficient"
                            }:
                                raise ValueError("reference assessment is invalid")
                            reference_assessment_reason = str(
                                raw.get("reference_assessment_reason") or ""
                            ).strip()
                            validation_category = "solution_evidence_contract"
                            (
                                normalized_tag,
                                normalized_evidence,
                                evidence,
                                candidate_snapshot,
                                taxonomy_audit,
                                suggestion_audit,
                            ) = _govern_deferred_analysis_item(
                                raw_tag=raw_tag,
                                raw_evidence=raw_evidence,
                                raw_type_suggestion=raw.get(
                                    "question_type_suggestion"
                                ),
                                question=question,
                                source_question_ref=source.source_question_ref,
                                source_content_hash=source_hash,
                                resolver=self.resolver,
                                taxonomy_governance=self.taxonomy_governance,
                                model_name=response.model_name,
                                operation_id=operation_id,
                            )
                            if question.taxonomy_contract.get("question_type_mode") is True:
                                snapshot_by_id = {item.fine_term_id: item for item in candidate_snapshot}
                                for candidate in question.taxonomy_contract.get("candidates", {}).get("knowledge", []):
                                    if is_type_key(candidate.get("id")):
                                        snapshot_by_id[candidate["id"]] = DeferredKnowledgeCandidate(
                                            candidate["id"], candidate["name"], usage=candidate.get("usage", ""),
                                        )
                                candidate_snapshot = tuple(snapshot_by_id.values())

                        except Exception as exc:
                            if isinstance(exc, _DeferredAnalysisValidationError):
                                validation_category = exc.category
                            replace_failure(
                                DeferredAnalysisFailure(
                                    source_question_ref=source.source_question_ref,
                                    analysis_question_id=question.question_id,
                                    request_id=request_id,
                                    batch_hash=batch.batch_hash,
                                    category=validation_category,
                                    validation_error=_safe_validation_error(exc),
                                    rejected_result=(
                                        dict(raw)
                                        if isinstance(raw, Mapping)
                                        else None
                                    ),
                                )
                            )
                            continue
                        remove_failure(source.source_question_ref)
                        result.append(
                            DeferredCombinedAnalysisItem(
                                source_question_ref=source.source_question_ref,
                                analysis_question_id=question.question_id,
                                source_content_hash=source_hash,
                                curriculum_volume_id=curriculum_volume_id,
                                taxonomy_contract_hash=_hash_payload(
                                    dict(question.taxonomy_contract)
                                ),
                                knowledge_candidates=candidate_snapshot,
                                tag_analysis=normalized_tag,
                                solution_evidence_payload=normalized_evidence,
                                solution_evidence=evidence,
                                taxonomy_audit=taxonomy_audit,
                                reference_assessment=reference_assessment,
                                reference_assessment_reason=reference_assessment_reason,
                                question_type_suggestion=suggestion_audit,
                                question_type_labels=(raw["question_type_labels"] if question.taxonomy_contract.get("question_type_mode") is True else None),
                                model_name=response.model_name,
                                operation_id=operation_id,
                            )
                        )
                        validated_raw_results.append(
                            {
                                "question_id": question.question_id,
                                "tag_analysis": normalized_tag,
                                "solution_evidence": normalized_evidence,
                                "reference_assessment": reference_assessment,
                                "reference_assessment_reason": reference_assessment_reason,
                            }
                        )
                        parsed_count += 1
                    requests.append(
                        AnalysisRequestCheckpoint(
                            request_id=request_id,
                            request_fingerprint=request_fingerprint,
                            batch_hash=batch.batch_hash,
                            source_question_refs=source_refs,
                            status=(
                                "succeeded"
                                if parsed_count == len(batch.questions)
                                else "partial"
                            ),
                            model_name=response.model_name,
                            raw_payload={"results": validated_raw_results},
                        )
                    )
                    snapshot()

                if not stop_scheduling:
                    fill_available_slots()
            if stop_scheduling:
                record_unscheduled_cancellations(stop_category)
        except BaseException as exc:
            if _analysis_error_category(exc) == "cancelled":
                record_unscheduled_cancellations()
                try:
                    snapshot()
                except BaseException:
                    # The cancellation-aware checkpoint is expected to raise
                    # again after durably saving the augmented bundle.
                    pass
            for future in future_map:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        return snapshot()


@dataclass(frozen=True, slots=True)
class _DeferredEvidenceBinding:
    evidence: QuestionSolutionEvidence
    proposal_ids: tuple[str, ...] = ()
    review_required: bool = False
    retry_required: bool = False


class DeferredCombinedProjectionWriter:
    """Persist deferred tag/evidence projections after a stable DB id exists."""

    def __init__(
        self,
        *,
        tag_writer: Any,
        mapping_repository: FineTermResolver,
        evidence_repository: SolutionEvidenceRepository,
        taxonomy_governance: Any | None = None,
        criterion_module: Any | None = None,
        question_type_writer: Any | None = None,
    ) -> None:
        self.tag_writer = tag_writer
        self.mapping_repository = mapping_repository
        self.evidence_repository = evidence_repository
        self.taxonomy_governance = taxonomy_governance
        self.criterion_module = criterion_module
        self.question_type_writer = question_type_writer

    def _publish_criterion(
        self,
        evidence: QuestionSolutionEvidence,
        *,
        question: QuestionAnalysisInput,
        model_name: str,
        confirmed_rubric: Mapping[str, Any] | None = None,
        confirmed_answer: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.criterion_module is None:
            return {"status": "not_requested"}
        try:
            draft = (
                criteria_from_confirmed_rubric(
                    question=question,
                    rubric_question=confirmed_rubric,
                    answer_key=confirmed_answer,
                )
                if confirmed_rubric is not None
                else training_criteria_from_solution_evidence(
                    evidence,
                    question=question,
                )
            )
            source_kind = (
                "confirmed_rubric_adapter"
                if confirmed_rubric is not None
                else "combined_model"
            )
            workspace = self.criterion_module.propose(
                question=question,
                draft=draft,
                source_kind=source_kind,
                source_reference=training_criterion_source_reference(
                    question.question_id,
                    draft,
                ),
                actor_ref=(
                    "system:confirmed-rubric-adapter"
                    if confirmed_rubric is not None
                    else f"model:{str(model_name or 'combined-analysis')}"
                ),
                reason=(
                    "由已确认评分依据本地生成训练判定点"
                    if confirmed_rubric is not None
                    else "联合题目解析自动发布训练判定点"
                ),
            )
            current = (
                workspace.get("current_version")
                if isinstance(workspace, Mapping)
                else None
            )
            quality_status = (
                str(current.get("quality_status") or "")
                if isinstance(current, Mapping)
                else ""
            )
            return {
                "status": (
                    "succeeded" if quality_status == "passed" else "needs_review"
                ),
                "version_id": (
                    str(current.get("version_id") or "")
                    if isinstance(current, Mapping)
                    else ""
                ),
                "quality_codes": (
                    list(current.get("quality_codes") or [])
                    if isinstance(current, Mapping)
                    else []
                ),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "error_category": type(exc).__name__,
            }

    def write(
        self,
        item: DeferredCombinedAnalysisItem,
        *,
        question: QuestionAnalysisInput,
        source_question_ref: str,
    ) -> dict[str, Any]:
        if str(source_question_ref or "").strip() != item.source_question_ref:
            raise ValueError("deferred analysis source reference does not match")
        if item.question_type_labels is not None:
            item.bind_evidence(question, resolver=self.mapping_repository)
        tag_status = "failed"
        evidence_status = "failed"
        tag_error = ""
        evidence_error = ""
        evidence_version_id = ""
        criterion_audit: dict[str, Any] = {"status": "not_requested"}
        taxonomy_proposal_ids: tuple[str, ...] = ()
        taxonomy_review_required = False
        taxonomy_retry_required = False
        try:
            self.tag_writer.write(
                question,
                item.tag_analysis,
                model_name=item.model_name,
                operation_id=item.operation_id,
            )
        except TaxonomyProjectionReviewRequired:
            tag_status = "needs_taxonomy_review"
        except Exception:
            tag_error = "tag_validation"
        else:
            tag_status = "succeeded"
        # 分析阶段已采纳的题型建议随标签落库：未确认题型按模型真实题型订正，
        # 教师确认或冲突项在分析阶段已排除，不重复处理。
        suggestion = item.question_type_suggestion
        if (
            suggestion is not None
            and self.question_type_writer is not None
            and str(suggestion.get("action") or "") == "applied"
        ):
            try:
                self.question_type_writer.apply(
                    question,
                    QuestionTypeSuggestion(
                        question_type=str(
                            suggestion.get("suggested_type") or ""
                        ),
                        reason=str(suggestion.get("reason") or ""),
                        essay_subtype=(
                            str(suggestion.get("suggested_subtype") or "")
                            or None
                        ),
                    ),
                    model_name=item.model_name,
                    operation_id=item.operation_id,
                )
            except Exception:
                LOGGER.exception(
                    "deferred question-type suggestion apply failed: %s",
                    item.source_question_ref,
                )
        try:
            binding = self._bind_adoption_evidence(item, question=question)
            taxonomy_proposal_ids = binding.proposal_ids
            taxonomy_review_required = binding.review_required
            taxonomy_retry_required = binding.retry_required
            evidence_version_id = self.evidence_repository.save(
                binding.evidence,
                source_kind="combined_model",
                source_reference=(
                    f"deferred:{item.operation_id}:{item.source_question_ref}"
                ),
                created_by=f"model:{item.model_name or 'unknown'}",
            )
        except Exception:
            evidence_error = "evidence_validation"
        else:
            evidence_status = "succeeded"
            if item.question_type_labels is not None:
                self.tag_writer.write_question_type_labels(
                    question, item.question_type_labels, model_name=item.model_name,
                    operation_id=item.operation_id,
                )
            criterion_audit = self._publish_criterion(
                binding.evidence,
                question=question,
                model_name=item.model_name,
            )
        return {
            "source_question_ref": item.source_question_ref,
            "question_id": question.question_id,
            "tag_status": tag_status,
            "tag_error_category": tag_error,
            "evidence_status": evidence_status,
            "evidence_error_category": evidence_error,
            "criteria_status": criterion_audit.get("status", "not_requested"),
            "criteria_error_category": str(
                criterion_audit.get("error_category") or ""
            ),
            "criterion_version_id": str(
                criterion_audit.get("version_id") or ""
            ),
            "source_evidence_version_id": evidence_version_id,
            "taxonomy_proposal_ids": list(taxonomy_proposal_ids),
            "taxonomy_review_required": taxonomy_review_required,
            "taxonomy_retry_required": taxonomy_retry_required,
        }

    def adopt(
        self,
        item: DeferredCombinedAnalysisItem,
        *,
        question: QuestionAnalysisInput,
        source_question_ref: str,
    ) -> dict[str, Any]:
        """Named adoption seam used after source_ref is bound to a real id."""

        return self.write(
            item,
            question=question,
            source_question_ref=source_question_ref,
        )

    def adopt_linked(
        self,
        item: DeferredCombinedAnalysisItem,
        *,
        question: QuestionAnalysisInput,
        link: ConfirmedQuestionAdoptionLink,
        confirmed_rubric: Mapping[str, Any] | None = None,
        confirmed_answer: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Adopt after an explicit source-ref link, without weakening validation.

        Imported text or image normalization may change the portable content hash.
        This seam therefore trusts only the explicit link for identity and still
        validates every evidence term against the immutable candidate snapshot.
        """

        if link.source_question_ref != item.source_question_ref:
            raise ValueError("confirmed adoption source reference does not match")
        if link.bank_question_id != question.question_id:
            raise ValueError("confirmed adoption question id does not match")
        item._validate_volume(question)
        tag_status = "failed"
        evidence_status = "failed"
        tag_error = ""
        evidence_error = ""
        evidence_version_id = ""
        criterion_audit: dict[str, Any] = {"status": "not_requested"}
        taxonomy_proposal_ids: tuple[str, ...] = ()
        taxonomy_review_required = False
        taxonomy_retry_required = False
        try:
            self.tag_writer.write(
                question,
                item.tag_analysis,
                model_name=item.model_name,
                operation_id=item.operation_id,
            )
        except TaxonomyProjectionReviewRequired:
            tag_status = "needs_taxonomy_review"
        except Exception:
            tag_error = "tag_validation"
        else:
            tag_status = "succeeded"
        # 分析阶段已采纳的题型建议随标签落库：未确认题型按模型真实题型订正，
        # 教师确认或显式小问冲突的项在分析阶段已排除，不重复处理。
        suggestion = item.question_type_suggestion
        if (
            suggestion is not None
            and self.question_type_writer is not None
            and str(suggestion.get("action") or "") == "applied"
        ):
            try:
                self.question_type_writer.apply(
                    question,
                    QuestionTypeSuggestion(
                        question_type=str(
                            suggestion.get("suggested_type") or ""
                        ),
                        reason=str(suggestion.get("reason") or ""),
                        essay_subtype=(
                            str(suggestion.get("suggested_subtype") or "")
                            or None
                        ),
                    ),
                    model_name=item.model_name,
                    operation_id=item.operation_id,
                )
            except Exception:
                LOGGER.exception(
                    "deferred question-type suggestion apply failed: %s",
                    item.source_question_ref,
                )
        try:
            binding = self._bind_adoption_evidence(
                item,
                question=question,
                link=link,
            )
            taxonomy_proposal_ids = binding.proposal_ids
            taxonomy_review_required = binding.review_required
            taxonomy_retry_required = binding.retry_required
            evidence_version_id = self.evidence_repository.save(
                binding.evidence,
                source_kind="combined_model",
                source_reference=(
                    f"deferred-linked:{item.operation_id}:"
                    f"{item.source_question_ref}:"
                    f"{binding.evidence.content_hash}"
                ),
                created_by=(
                    f"model:{item.model_name or 'unknown'};"
                    f"linked-by:{link.confirmed_by}"
                ),
            )
        except Exception:
            evidence_error = "evidence_validation"
        else:
            evidence_status = "succeeded"
            if item.question_type_labels is not None:
                self.tag_writer.write_question_type_labels(
                    question, item.question_type_labels, model_name=item.model_name,
                    operation_id=item.operation_id,
                )
            criterion_audit = self._publish_criterion(
                binding.evidence,
                question=question,
                model_name=item.model_name,
                confirmed_rubric=confirmed_rubric,
                confirmed_answer=confirmed_answer,
            )
        return {
            "source_question_ref": item.source_question_ref,
            "question_id": question.question_id,
            "adoption_mode": "confirmed_link",
            "tag_status": tag_status,
            "tag_error_category": tag_error,
            "evidence_status": evidence_status,
            "evidence_error_category": evidence_error,
            "criteria_status": criterion_audit.get("status", "not_requested"),
            "criteria_error_category": str(
                criterion_audit.get("error_category") or ""
            ),
            "criterion_version_id": str(
                criterion_audit.get("version_id") or ""
            ),
            "source_evidence_version_id": evidence_version_id,
            "taxonomy_proposal_ids": list(taxonomy_proposal_ids),
            "taxonomy_review_required": taxonomy_review_required,
            "taxonomy_retry_required": taxonomy_retry_required,
        }

    def _bind_adoption_evidence(
        self,
        item: DeferredCombinedAnalysisItem,
        *,
        question: QuestionAnalysisInput,
        link: ConfirmedQuestionAdoptionLink | None = None,
    ) -> _DeferredEvidenceBinding:
        if item.question_type_labels is not None:
            validate_question_type_labels(item.question_type_labels, question.taxonomy_contract)
        if self.taxonomy_governance is None:
            if link is None:
                return _DeferredEvidenceBinding(
                    item.bind_evidence(
                        question,
                        resolver=self.mapping_repository,
                    )
                )
            return _DeferredEvidenceBinding(
                item.bind_linked_evidence(
                    question,
                    link=link,
                    resolver=self.mapping_repository,
                )
            )
        payload = item.evidence_payload_for_taxonomy_retry(
            question_id=question.question_id
        )
        convergence = converge_evidence_terms(
            payload,
            taxonomy_contract=question.taxonomy_contract,
            governance=self.taxonomy_governance,
            question_ref=str(question.question_id),
            model_name=item.model_name,
            operation_id=item.operation_id,
            persist_proposals=True,
        )
        evidence = QuestionSolutionEvidence.from_model_dict(
            convergence.payload,
            question_id=question.question_id,
            source_content_hash=solution_evidence_source_content_hash(question),
            resolver=self.mapping_repository,
        )
        validate_evidence_fine_terms(
            evidence,
            question.taxonomy_contract,
            additional_allowed_term_ids=convergence.canonical_term_ids,
        )
        missing_links = any(
            not point.fine_term_links
            for part in evidence.parts
            for point in part.evidence_points
        )
        unresolved_reasons = {
            str(item.get("reason_code") or "")
            for item in convergence.unresolved_links
        }
        return _DeferredEvidenceBinding(
            evidence=evidence,
            proposal_ids=tuple(
                dict.fromkeys(
                    str(item.get("proposal_id") or item.get("id") or "")
                    for item in convergence.proposals
                    if str(item.get("proposal_id") or item.get("id") or "")
                )
            ),
            review_required=bool(convergence.unresolved_links or missing_links),
            retry_required=bool(
                unresolved_reasons
                & {
                    "unknown_term",
                    "id_name_conflict",
                    "taxonomy_governance_unavailable",
                }
            ),
        )


def compose_generated_config_from_skeletons(
    skeletons: Sequence[Mapping[str, Any]],
    *,
    exam_title: str,
) -> dict[str, Any]:
    """Compose the current generated-config contract before score allocation.

    Zero score fields are compatibility sentinels required by the existing
    config normalizer. They do not represent a grading decision and must be
    replaced by the dedicated whole-paper score-allocation seam.
    """

    title = str(exam_title or "").strip()
    if not title:
        raise ValueError("exam_title must not be empty")
    normalized = tuple(skeletons)
    if not normalized:
        raise ValueError("at least one solution-evidence skeleton is required")

    rubric_questions: list[dict[str, Any]] = []
    answer_questions: list[dict[str, Any]] = []
    seen_question_ids: set[str] = set()
    for skeleton in normalized:
        _require_exact_keys(
            skeleton,
            {"rubric_question", "answer_key_question"},
            "grading config skeleton",
        )
        raw_rubric = skeleton.get("rubric_question")
        raw_answer = skeleton.get("answer_key_question")
        if not isinstance(raw_rubric, Mapping) or not isinstance(
            raw_answer,
            Mapping,
        ):
            raise ValueError("grading config skeleton questions are invalid")
        if (
            raw_rubric.get("schema_version")
            != "solution-evidence-rubric-skeleton-v1"
            or raw_answer.get("schema_version")
            != "solution-evidence-answer-key-v1"
        ):
            raise ValueError("grading config skeleton version is invalid")
        question_id = str(raw_rubric.get("question_id") or "").strip()
        if (
            not question_id
            or question_id != str(raw_answer.get("question_id") or "").strip()
            or question_id in seen_question_ids
        ):
            raise ValueError("grading config skeleton question ids are invalid")
        seen_question_ids.add(question_id)
        evidence_version_id = _sha256_text(
            raw_rubric.get("source_evidence_version_id"),
            "source_evidence_version_id",
        )
        if evidence_version_id != _sha256_text(
            raw_answer.get("source_evidence_version_id"),
            "source_evidence_version_id",
        ):
            raise ValueError("rubric and answer evidence versions do not match")
        if _sha256_text(
            raw_rubric.get("source_content_hash"),
            "source_content_hash",
        ) != _sha256_text(
            raw_answer.get("source_content_hash"),
            "source_content_hash",
        ):
            raise ValueError("rubric and answer source hashes do not match")

        raw_parts = raw_rubric.get("parts")
        raw_answer_parts = raw_answer.get("parts")
        if not isinstance(raw_parts, list) or not isinstance(
            raw_answer_parts,
            list,
        ) or not raw_parts or len(raw_parts) != len(raw_answer_parts):
            raise ValueError("grading config skeleton parts are incomplete")
        answer_parts_by_id = {
            str(item.get("part_id") or "").strip(): item
            for item in raw_answer_parts
            if isinstance(item, Mapping)
        }
        if len(answer_parts_by_id) != len(raw_answer_parts):
            raise ValueError("answer skeleton part ids are invalid")

        rubric_parts: list[dict[str, Any]] = []
        answer_parts: list[dict[str, Any]] = []
        response_modes: list[str] = []
        seen_part_ids: set[str] = set()
        for raw_part in raw_parts:
            if not isinstance(raw_part, Mapping):
                raise ValueError("rubric skeleton part is invalid")
            part_id = str(raw_part.get("part_id") or "").strip()
            answer_part = answer_parts_by_id.get(part_id)
            if (
                not part_id
                or part_id in seen_part_ids
                or not isinstance(answer_part, Mapping)
            ):
                raise ValueError("rubric and answer skeleton parts do not align")
            seen_part_ids.add(part_id)
            response_mode = str(raw_part.get("response_mode") or "").strip()
            if response_mode not in {
                "exact_objective",
                "short_answer_points",
                "process_required",
                "visual_construction",
            }:
                raise ValueError("rubric skeleton response mode is invalid")
            response_modes.append(response_mode)
            allow_alternatives = bool(
                raw_part.get("allow_alternative_methods")
            )
            raw_steps = raw_part.get("steps")
            if not isinstance(raw_steps, list) or not raw_steps:
                raise ValueError("rubric skeleton steps are incomplete")
            steps: list[dict[str, Any]] = []
            seen_step_ids: set[str] = set()
            for raw_step in raw_steps:
                if not isinstance(raw_step, Mapping):
                    raise ValueError("rubric skeleton step is invalid")
                step_id = str(raw_step.get("step_id") or "").strip()
                core_goal = str(raw_step.get("core_goal") or "").strip()
                required_elements = _text_list(
                    raw_step.get("required_elements")
                )
                if (
                    not step_id
                    or step_id in seen_step_ids
                    or not core_goal
                    or not required_elements
                ):
                    raise ValueError("rubric skeleton step is incomplete")
                seen_step_ids.add(step_id)
                steps.append(
                    {
                        "step_id": step_id,
                        "evidence_point_ids": _text_list(raw_step.get("evidence_point_ids")),
                        "step_score": 0,
                        **({"answer_kind": "conditions"} if raw_step.get("answer_kind") == "conditions" else {}),
                        "core_goal": core_goal,
                        "required_elements": required_elements,
                        "allow_alternative_methods": allow_alternatives,
                        "equivalent_rules": _text_list(
                            raw_step.get("equivalent_rules")
                        ),
                        "counterexamples": _text_list(
                            raw_step.get("counterexamples")
                        ),
                        "deduction_rules": _text_list(
                            raw_step.get("deduction_rules")
                        ),
                    }
                )
            visual_requirements = _text_list(
                raw_part.get("visual_requirements")
            )
            rubric_parts.append(
                {
                    "part_id": part_id,
                    "evidence_part_id": part_id,
                    "part_score": 0,
                    "response_mode": response_mode,
                    "require_final_answer": bool(
                        raw_part.get("require_final_answer")
                    ),
                    "allow_alternative_methods": allow_alternatives,
                    "deduction_policy": _text_list(
                        raw_part.get("deduction_policy")
                    ),
                    "proof_obligations": _text_list(
                        raw_part.get("proof_obligations")
                    ),
                    "visual_requirements": visual_requirements,
                    "presentation_rules": visual_requirements,
                    "steps": steps,
                }
            )
            answer_parts.append(
                {
                    "part_id": part_id,
                    **({"answer_kind": "conditions"} if len(steps) == 1
                        and steps[0].get("answer_kind") == "conditions" else {}),
                    "answer": str(answer_part.get("answer") or "").strip(),
                    "canonical_answer": str(
                        answer_part.get("canonical_answer") or ""
                    ).strip(),
                    "accepted_forms": _text_list(
                        answer_part.get("accepted_forms")
                    ),
                    "analysis": str(
                        answer_part.get("analysis") or ""
                    ).strip(),
                    "step_milestones": _milestone_texts(
                        answer_part.get("step_milestones")
                    ),
                    "proof_obligations": _text_list(
                        answer_part.get("proof_obligations")
                    ),
                    "visual_requirements": _text_list(
                        answer_part.get("visual_requirements")
                    ),
                }
            )

        canonical_answer = _whole_question_answer(answer_parts)
        question_type = _question_type_from_skeleton(
            response_modes,
            rubric_parts,
            canonical_answer,
        )
        rubric_questions.append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "question_type_confirmed": False,
                "max_score": 0,
                "grading_mode": (
                    "direct_answer"
                    if all(
                        mode in {"exact_objective", "short_answer_points"}
                        for mode in response_modes
                    )
                    else "deductive_obligation"
                ),
                "source_evidence_version_id": evidence_version_id,
                "parts": rubric_parts,
            }
        )
        answer_questions.append(
            {
                "question_id": question_id,
                "canonical_answer": canonical_answer,
                "accepted_forms": (
                    list(answer_parts[0]["accepted_forms"])
                    if len(answer_parts) == 1
                    else []
                ),
                "method_variants": [],
                "source_evidence_version_id": evidence_version_id,
                "parts": answer_parts,
            }
        )
    return {
        "rubric": {
            "exam_title": title,
            "total_score": 0,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {
            "warnings": [],
            "generation_mode": "solution_evidence_structure",
            "structure_source": "solution_evidence",
            "structure_generation_model_requests": 0,
            "score_allocation_pending": True,
        },
    }


def _response_items(
    response: GatewayBatchResponse,
    expected_question_ids: Sequence[int],
) -> dict[int, Mapping[str, Any]]:
    raw = response.payload.get("results")
    if not isinstance(raw, list):
        raise ValueError("combined response has no results")
    expected = set(int(value) for value in expected_question_ids)
    result: dict[int, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("combined response item is invalid")
        try:
            question_id = int(item.get("question_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("combined response question_id is invalid") from exc
        if question_id not in expected or question_id in result:
            raise ValueError("combined response question_id is unknown or duplicated")
        result[question_id] = item
    if set(result) != expected:
        raise ValueError("combined response is incomplete")
    return result


def _normalize_sources(
    operation_id: str,
    curriculum_volume_id: str,
    sources: Sequence[ConfigQuestionAnalysisSource],
) -> tuple[str, str, tuple[ConfigQuestionAnalysisSource, ...]]:
    clean_operation = str(operation_id or "").strip()
    volume_id = str(curriculum_volume_id or "").strip()
    if not clean_operation or not volume_id:
        raise ValueError(
            "operation_id and curriculum_volume_id must not be empty"
        )
    normalized = tuple(sources)
    if not normalized:
        raise ValueError("analysis sources must not be empty")
    refs = [item.source_question_ref for item in normalized]
    ids = [item.question.question_id for item in normalized]
    if len(refs) != len(set(refs)) or len(ids) != len(set(ids)):
        raise ValueError("analysis source references and ids must be unique")
    if any(
        str(item.question.tagging_context.curriculum_volume_id or "").strip()
        != volume_id
        for item in normalized
    ):
        raise ValueError("analysis source curriculum volume is missing or inconsistent")
    return clean_operation, volume_id, normalized


def _analysis_error_category(exc: BaseException) -> str:
    transport = classify_transport_error(exc)
    if transport is not None:
        return transport.value
    text = f"{type(exc).__name__} {exc}".casefold()
    if "cancel" in text:
        return "cancelled"
    if "timeout" in text:
        return "timeout"
    if any(
        marker in text
        for marker in ("connection", "broken pipe", "connection reset", "disconnect")
    ):
        return "connection"
    if "json" in text or "parse" in text:
        return "parse"
    if isinstance(exc, ValueError) and "combined response" in text:
        return "combined_response_contract"
    return "model"


def _analysis_outcome_is_unknown(exc: BaseException) -> bool:
    return _analysis_error_category(exc) in {
        "cancelled",
        "timeout",
        "connection",
    }



def _hash_payload(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_text(value: object, field_name: str) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if {str(key) for key in payload} != expected:
        raise ValueError(f"{label} fields do not match the contract")


_TAXONOMY_AUDIT_KEYS = {
    "schema_version",
    "taxonomy_revision",
    "status",
    "tag_quality_status",
    "quality_notes",
    "retrieval_misses",
    "proposals",
    "secondary_matches",
    "unresolved_links",
}


def _normalize_taxonomy_audit(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("deferred taxonomy audit is invalid")
    _require_exact_keys(value, _TAXONOMY_AUDIT_KEYS, "deferred taxonomy audit")
    if value.get("schema_version") != "deferred-taxonomy-audit-v1":
        raise ValueError("deferred taxonomy audit version is invalid")
    status = str(value.get("status") or "").strip().casefold()
    if status not in {
        "accepted",
        "needs_review",
        "unavailable",
        "legacy_unrecorded",
    }:
        raise ValueError("deferred taxonomy audit status is invalid")
    try:
        revision = max(0, int(value.get("taxonomy_revision") or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("deferred taxonomy revision is invalid") from exc
    quality_status = str(value.get("tag_quality_status") or "").strip()[:80]
    quality_notes = _audit_text_list(value.get("quality_notes"))
    return {
        "schema_version": "deferred-taxonomy-audit-v1",
        "taxonomy_revision": revision,
        "status": status,
        "tag_quality_status": quality_status,
        "quality_notes": quality_notes,
        "retrieval_misses": _audit_mapping_list(value.get("retrieval_misses")),
        "proposals": _audit_mapping_list(value.get("proposals")),
        "secondary_matches": _audit_mapping_list(value.get("secondary_matches")),
        "unresolved_links": _audit_mapping_list(value.get("unresolved_links")),
    }


def _legacy_taxonomy_audit(tag_analysis: Mapping[str, Any]) -> dict[str, Any]:
    try:
        revision = max(0, int(tag_analysis.get("taxonomy_revision") or 0))
    except (TypeError, ValueError):
        revision = 0
    return {
        "schema_version": "deferred-taxonomy-audit-v1",
        "taxonomy_revision": revision,
        "status": "legacy_unrecorded",
        "tag_quality_status": "legacy_unrecorded",
        "quality_notes": ["旧断点未保存独立标签治理审计。"],
        "retrieval_misses": [],
        "proposals": [],
        "secondary_matches": [],
        "unresolved_links": [],
    }


def _audit_text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("deferred taxonomy audit notes are invalid")
    return list(
        dict.fromkeys(
            str(item or "").strip()[:500]
            for item in value
            if str(item or "").strip()
        )
    )


def _audit_mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError("deferred taxonomy audit details are invalid")
    result: list[dict[str, Any]] = []
    for item in value:
        # A JSON round-trip rejects non-portable checkpoint values while retaining
        # the governed audit fields without coupling this layer to one registry.
        portable = json.loads(json.dumps(dict(item), ensure_ascii=False))
        if not isinstance(portable, dict):
            raise ValueError("deferred taxonomy audit detail is invalid")
        result.append(portable)
    return result


_MODEL_TAG_PAYLOAD_FIELDS = frozenset(
    {
        "method_tags",
        "thought_tags",
        "ability_tags",
        "math_model_tags",
        "special_type_tags",
        "difficulty",
        "predicted_error_patterns",
        "part_features",
        "taxonomy_revision",
        "proposed_tags",
        "reason",
        "confidence",
    }
)


def _validate_model_tag_payload(payload: Mapping[str, Any]) -> None:
    _require_exact_keys(
        payload,
        _MODEL_TAG_PAYLOAD_FIELDS,
        "combined tag analysis",
    )


class _DeferredAnalysisValidationError(ValueError):
    def __init__(self, category: str, detail: str = "") -> None:
        clean_detail = " ".join(str(detail or "").split())[:1000]
        super().__init__(clean_detail or category)
        self.category = str(category or "combined_item_contract")
        self.detail = clean_detail


def _safe_validation_error(exc: BaseException) -> str:
    if isinstance(exc, _DeferredAnalysisValidationError):
        return exc.detail
    return " ".join(str(exc or "").split())[:1000]


def _local_quality_repair_failure(
    item: DeferredCombinedAnalysisItem,
    issues: Sequence[Mapping[str, Any]],
    *,
    attempt: int,
    operation_id: str,
) -> DeferredAnalysisFailure:
    normalized_issues = [
        {
            key: str(issue.get(key) or "").strip()[:500]
            for key in (
                "code",
                "path",
                "expected",
                "actual",
                "message",
            )
        }
        for issue in issues
        if isinstance(issue, Mapping)
    ][:20]
    if not normalized_issues or any(
        not issue["code"] or not issue["path"] for issue in normalized_issues
    ):
        raise ValueError("local quality repair issues are incomplete")
    signature = _hash_payload(
        {
            "source_question_ref": item.source_question_ref,
            "issues": normalized_issues,
        }
    )
    ticket = {
        "attempt": max(1, min(3, int(attempt))),
        "failure_signature": signature,
        "validation_issues": normalized_issues,
        "immutable_fields": ["question_id", "tag_analysis"],
        "allowed_changes": ["solution_evidence.parts", "solution_evidence.auxiliary_rules"],
    }
    rejected_result = {
        "question_id": item.analysis_question_id,
        "tag_analysis": dict(item.tag_analysis),
        "solution_evidence": dict(item.solution_evidence_payload),
        "_repair_ticket": ticket,
    }
    return DeferredAnalysisFailure(
        source_question_ref=item.source_question_ref,
        analysis_question_id=item.analysis_question_id,
        request_id=_hash_payload(
            {
                "operation_id": operation_id,
                "source_question_ref": item.source_question_ref,
                "repair_ticket": signature,
            }
        ),
        batch_hash=_hash_payload(
            {
                "source_question_ref": item.source_question_ref,
                "source_content_hash": item.source_content_hash,
                "repair_ticket": signature,
            }
        ),
        category="local_validation",
        validation_error="；".join(
            issue["message"] or issue["code"] for issue in normalized_issues
        )[:1000],
        rejected_result=rejected_result,
    )


def _source_with_repair_context(
    source: ConfigQuestionAnalysisSource,
    failure: DeferredAnalysisFailure | None,
) -> ConfigQuestionAnalysisSource:
    if failure is None:
        return source
    validation_error = (
        failure.validation_error
        or f"上一轮未通过 {failure.category} 校验"
    )
    previous_result = (
        dict(failure.rejected_result)
        if isinstance(failure.rejected_result, Mapping)
        else {}
    )
    ticket = previous_result.pop("_repair_ticket", None)
    repair_context: dict[str, Any] = {
        "mode": "repair_previous_rejected_result",
        "validation_error": validation_error,
        "previous_result": previous_result,
    }
    if isinstance(ticket, Mapping):
        for field_name in (
            "attempt",
            "failure_signature",
            "validation_issues",
            "immutable_fields",
            "allowed_changes",
        ):
            if field_name in ticket:
                repair_context[field_name] = ticket[field_name]
    return replace(
        source,
        question=replace(
            source.question,
            repair_context=repair_context,
        ),
    )


def _govern_deferred_analysis_item(
    *,
    raw_tag: object,
    raw_evidence: Mapping[str, Any],
    raw_type_suggestion: object = None,
    question: QuestionAnalysisInput,
    source_question_ref: str,
    source_content_hash: str,
    resolver: FineTermResolver,
    taxonomy_governance: Any | None,
    model_name: str,
    operation_id: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    QuestionSolutionEvidence,
    tuple[DeferredKnowledgeCandidate, ...],
    dict[str, Any],
    dict[str, Any] | None,
]:
    """Validate scoring structure and audit taxonomy on independent axes."""

    suggestion: QuestionTypeSuggestion | None = None
    suggestion_audit: dict[str, Any] | None = None
    if raw_type_suggestion is not None:
        try:
            suggestion = QuestionTypeSuggestion.from_dict(raw_type_suggestion)
        except ProjectionValidationError:
            suggestion_audit = {"action": "invalid_ignored"}
        else:
            suggestion_audit = {
                "local_type": str(
                    question.tagging_context.question_type or ""
                ).strip(),
                "suggested_type": suggestion.question_type,
                "suggested_subtype": suggestion.essay_subtype,
                "reason": suggestion.reason,
                "model_name": str(model_name or ""),
            }
    effective_group = question.question_type_group
    effective_shape = question.objective_response_shape
    if suggestion is not None and suggestion_audit is not None:
        if suggestion.question_type_group == effective_group:
            suggestion_audit["action"] = "unchanged"
        elif question.question_type_confirmed:
            # 教师确认的题型是事实：建议只登记冲突，不改变评分结构。
            suggestion_audit["action"] = "conflict_only"
        else:
            # 未确认的本地题型只是预览提示；采纳模型的真实题型，
            # 让评分结构按真实题组组织（例如本地误判为填空的过程题）。
            effective_group = suggestion.question_type_group
            effective_shape = question.objective_response_shape_for(
                effective_group
            )
            suggestion_audit["action"] = "applied"
    evidence_normalization = normalize_model_solution_evidence(
        raw_evidence,
        question_id=question.question_id,
        question_type=effective_group,
        taxonomy_contract=question.taxonomy_contract,
        question_type_confirmed=question.question_type_confirmed,
        objective_response_shape=effective_shape,
        expected_answer=question.tagging_context.answer_text,
        expected_part_count=(
            len(question.explicit_part_labels)
            if question.explicit_part_labels
            else None
        ),
    )

    if taxonomy_governance is None:
        try:
            if not isinstance(raw_tag, Mapping):
                raise ValueError("combined response tag_analysis is invalid")
            _validate_model_tag_payload(raw_tag)
        except Exception as exc:
            raise _DeferredAnalysisValidationError(
                "tag_contract", str(exc)
            ) from exc
        try:
            normalized_tag = TagAnalysis.from_dict(dict(raw_tag)).to_dict()
        except Exception as exc:
            raise _DeferredAnalysisValidationError(
                "tag_normalization", str(exc)
            ) from exc
        normalized_evidence = evidence_normalization.payload
        try:
            evidence = QuestionSolutionEvidence.from_model_dict(
                normalized_evidence,
                question_id=question.question_id,
                source_content_hash=source_content_hash,
                resolver=resolver,
            )
        except Exception as exc:
            raise _DeferredAnalysisValidationError(
                "solution_evidence_contract", str(exc)
            ) from exc
        try:
            validate_evidence_fine_terms(evidence, question.taxonomy_contract)
        except Exception as exc:
            raise _DeferredAnalysisValidationError(
                "solution_evidence_terms", str(exc)
            ) from exc
        try:
            candidates = _minimal_candidate_snapshot(
                question.taxonomy_contract,
                evidence,
            )
        except Exception as exc:
            raise _DeferredAnalysisValidationError(
                "solution_evidence_candidates", str(exc)
            ) from exc
        return (
            normalized_tag,
            normalized_evidence,
            evidence,
            candidates,
            {
                "schema_version": "deferred-taxonomy-audit-v1",
                "taxonomy_revision": _taxonomy_contract_revision(
                    question.taxonomy_contract
                ),
                "status": (
                    "needs_review"
                    if evidence_normalization.requires_review
                    else "accepted"
                ),
                "tag_quality_status": "not_checked",
                "quality_notes": list(evidence_normalization.notes),
                "retrieval_misses": [],
                "proposals": [],
                "secondary_matches": [],
                "unresolved_links": [],
            },
            suggestion_audit,
        )

    tag_parse_failed = False
    tag_quality_status = "invalid"
    tag_quality_notes: list[str] = []
    tag_proposals: list[dict[str, Any]] = []
    tag_retrieval_misses: list[dict[str, Any]] = []
    try:
        if not isinstance(raw_tag, Mapping):
            raise ValueError("combined response tag_analysis is invalid")
        _validate_model_tag_payload(raw_tag)
        parsed_tag = TagAnalysis.from_dict(dict(raw_tag))
        checked_tag = converge_tag_analysis(
            parsed_tag,
            question.tagging_context,
            governance=taxonomy_governance,
            taxonomy_contract=question.taxonomy_contract,
            question_ref=source_question_ref,
            model_name=model_name,
        )
        normalized_tag_analysis = checked_tag.analysis or parsed_tag
        tag_quality_status = str(checked_tag.quality_status or "invalid")
        tag_quality_notes = [
            str(item or "").strip()
            for item in checked_tag.quality_notes
            if str(item or "").strip()
        ]
        tag_proposals = [
            dict(item)
            for item in checked_tag.proposals
            if isinstance(item, Mapping)
        ]
        tag_retrieval_misses = [
            dict(item)
            for item in checked_tag.retrieval_misses
            if isinstance(item, Mapping)
        ]
    except Exception:
        tag_parse_failed = True
        tag_quality_notes = ["标签字段未通过本地规范化，已保留评分依据并转待处理。"]
        normalized_tag_analysis = _safe_fallback_tag_analysis(raw_tag)

    structure_only = evidence_normalization.payload
    tag_quality_notes.extend(evidence_normalization.notes)
    governance_unavailable = False
    evidence_proposals: list[dict[str, Any]] = []
    evidence_retrieval_misses: list[dict[str, Any]] = []
    secondary_matches: list[dict[str, Any]] = []
    unresolved_links: list[dict[str, Any]] = []
    canonical_terms: Sequence[Mapping[str, Any]] = ()
    additional_allowed: Sequence[str] = ()
    taxonomy_revision = _taxonomy_contract_revision(question.taxonomy_contract)
    try:
        convergence = converge_evidence_terms(
            structure_only,
            taxonomy_contract=question.taxonomy_contract,
            governance=taxonomy_governance,
            question_ref=source_question_ref,
            model_name=model_name,
            operation_id=operation_id,
            persist_proposals=False,
        )
        normalized_evidence = dict(convergence.payload)
        additional_allowed = convergence.canonical_term_ids
        canonical_terms = convergence.canonical_terms
        evidence_proposals = [dict(item) for item in convergence.proposals]
        evidence_retrieval_misses = [
            dict(item) for item in convergence.retrieval_misses
        ]
        secondary_matches = [
            dict(item) for item in convergence.secondary_matches
        ]
        unresolved_links = [
            dict(item) for item in convergence.unresolved_links
        ]
        taxonomy_revision = int(
            getattr(convergence, "taxonomy_revision", taxonomy_revision)
            or taxonomy_revision
        )
    except Exception:
        governance_unavailable = True
        normalized_evidence, unresolved_links = _strip_unverified_evidence_links(
            structure_only,
            reason_code="taxonomy_governance_unavailable",
        )
        tag_quality_notes.append(
            "本地完整词表暂时不可用；评分依据已保留，标签等待本地重试。"
        )

    evidence = QuestionSolutionEvidence.from_model_dict(
        normalized_evidence,
        question_id=question.question_id,
        source_content_hash=source_content_hash,
        resolver=resolver,
    )
    validate_evidence_fine_terms(
        evidence,
        question.taxonomy_contract,
        additional_allowed_term_ids=additional_allowed,
    )
    candidates = _governed_candidate_snapshot(canonical_terms, evidence)
    expected_parts = {part.part_id for part in evidence.parts}
    feature_parts = [str(part.get("part_id") or "") for part in normalized_tag_analysis.part_features]
    if set(feature_parts) != expected_parts or len(feature_parts) != len(set(feature_parts)):
        tag_quality_status = "invalid"
        tag_quality_notes.append("逐小问难度特征与解题依据的小问不完整对应（缺问、错号或重复号）。")
    proposals = _merge_taxonomy_proposals(tag_proposals, evidence_proposals)
    tag_payload = normalized_tag_analysis.to_dict()
    tag_payload["proposed_tags"] = proposals
    normalized_tag = TagAnalysis.from_dict(tag_payload).to_dict()
    missing_term_links = any(
        not point.fine_term_links
        for part in evidence.parts
        for point in part.evidence_points
    )
    if missing_term_links:
        tag_quality_notes.append(
            "部分判分点暂未关联正式知识词；评分依据已保留，标签等待补充或归并。"
        )
    tag_requires_review = tag_quality_status != "complete"
    status = (
        "unavailable"
        if governance_unavailable
        else "needs_review"
        if (
            tag_parse_failed
            or tag_requires_review
            or proposals
            or unresolved_links
            or missing_term_links
            or evidence_normalization.requires_review
        )
        else "accepted"
    )
    audit = {
        "schema_version": "deferred-taxonomy-audit-v1",
        "taxonomy_revision": taxonomy_revision,
        "status": status,
        "tag_quality_status": tag_quality_status,
        "quality_notes": list(dict.fromkeys(tag_quality_notes)),
        "retrieval_misses": [
            *tag_retrieval_misses,
            *evidence_retrieval_misses,
        ],
        "proposals": proposals,
        "secondary_matches": secondary_matches,
        "unresolved_links": unresolved_links,
    }
    return (
        normalized_tag,
        normalized_evidence,
        evidence,
        candidates,
        _normalize_taxonomy_audit(audit),
        suggestion_audit,
    )


def _safe_fallback_tag_analysis(raw_tag: object) -> TagAnalysis:
    payload = dict(raw_tag) if isinstance(raw_tag, Mapping) else {}
    return TagAnalysis.from_dict(payload)


def _merge_taxonomy_proposals(
    *groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for raw in group:
            dimension = str(raw.get("dimension") or "knowledge").strip().casefold()
            name = str(
                raw.get("proposed_name") or raw.get("name") or ""
            ).strip()
            key = (dimension, name.casefold())
            if not name or key in seen:
                continue
            seen.add(key)
            item = dict(raw)
            item.setdefault("dimension", dimension)
            item.setdefault("proposed_name", name)
            result.append(item)
            if len(result) >= 2:
                return result
    return result


def _strip_unverified_evidence_links(
    payload: Mapping[str, Any],
    *,
    reason_code: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    unresolved: list[dict[str, Any]] = []
    parts = normalized.get("parts")
    if not isinstance(parts, list):
        return normalized, unresolved
    for part in parts:
        if not isinstance(part, dict):
            continue
        points = part.get("evidence_points")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            raw_links = point.get("fine_term_links")
            if not isinstance(raw_links, list):
                continue
            for raw in raw_links:
                if not isinstance(raw, Mapping):
                    continue
                unresolved.append(
                    {
                        "part_id": str(part.get("part_id") or ""),
                        "evidence_point_id": str(
                            point.get("evidence_point_id") or ""
                        ),
                        "role": str(raw.get("role") or ""),
                        "submitted_id": str(raw.get("fine_term_id") or ""),
                        "submitted_name": str(raw.get("fine_term_name") or ""),
                        "proposal_id": "",
                        "reason_code": reason_code,
                    }
                )
            point["fine_term_links"] = []
    return normalized, unresolved


def _taxonomy_contract_revision(contract: Mapping[str, Any]) -> int:
    try:
        return max(0, int(contract.get("taxonomy_revision") or 0))
    except (TypeError, ValueError):
        return 0


def _governed_candidate_snapshot(
    canonical_terms: Sequence[Mapping[str, Any]],
    evidence: QuestionSolutionEvidence,
) -> tuple[DeferredKnowledgeCandidate, ...]:
    by_id: dict[str, DeferredKnowledgeCandidate] = {}
    for raw in canonical_terms:
        term_id = str(raw.get("id") or raw.get("fine_term_id") or "").strip()
        term_name = str(
            raw.get("name") or raw.get("fine_term_name") or ""
        ).strip()
        raw_aliases = raw.get("aliases")
        aliases = (
            tuple(
                str(item or "").strip()
                for item in raw_aliases
                if str(item or "").strip()
            )
            if isinstance(raw_aliases, (list, tuple))
            else ()
        )
        if term_id and term_name:
            by_id[term_id] = DeferredKnowledgeCandidate(
                fine_term_id=term_id,
                fine_term_name=term_name,
                aliases=aliases,
                usage=str(raw.get("usage") or ""),
            )
    referenced_ids: list[str] = []
    for part in evidence.parts:
        for point in part.evidence_points:
            for link in point.fine_term_links:
                if link.fine_term_id not in referenced_ids:
                    referenced_ids.append(link.fine_term_id)
    if any(term_id not in by_id for term_id in referenced_ids):
        raise ValueError("governed solution evidence term snapshot is incomplete")
    snapshot = tuple(by_id[term_id] for term_id in referenced_ids)
    validate_evidence_fine_terms(evidence, _candidate_contract(snapshot))
    return snapshot


def _minimal_candidate_snapshot(
    taxonomy_contract: Mapping[str, Any],
    evidence: QuestionSolutionEvidence,
) -> tuple[DeferredKnowledgeCandidate, ...]:
    candidates = taxonomy_contract.get("candidates")
    knowledge = (
        candidates.get("knowledge")
        if isinstance(candidates, Mapping)
        else None
    )
    if not isinstance(knowledge, list):
        raise ValueError("question taxonomy contract has no knowledge candidates")
    by_id: dict[str, DeferredKnowledgeCandidate] = {}
    for raw in knowledge:
        if not isinstance(raw, Mapping):
            continue
        term_id = str(raw.get("id") or "").strip()
        term_name = str(raw.get("name") or "").strip()
        aliases_raw = raw.get("aliases")
        aliases = (
            tuple(str(item).strip() for item in aliases_raw if str(item).strip())
            if isinstance(aliases_raw, list)
            else ()
        )
        if not term_id or not term_name:
            continue
        usage = str(raw.get("usage") or "").strip()
        previous = by_id.get(term_id)
        if previous is None:
            by_id[term_id] = DeferredKnowledgeCandidate(
                fine_term_id=term_id,
                fine_term_name=term_name,
                aliases=aliases,
                usage=usage,
            )
            continue
        by_id[term_id] = DeferredKnowledgeCandidate(
            fine_term_id=term_id,
            fine_term_name=previous.fine_term_name,
            aliases=(
                *previous.aliases,
                *((term_name,) if term_name != previous.fine_term_name else ()),
                *aliases,
            ),
            usage=previous.usage or usage,
        )
    referenced_ids: list[str] = []
    for part in evidence.parts:
        for point in part.evidence_points:
            for link in point.fine_term_links:
                if link.fine_term_id not in referenced_ids:
                    referenced_ids.append(link.fine_term_id)
    snapshot = tuple(
        by_id[term_id]
        for term_id in referenced_ids
        if term_id in by_id
    )
    if len(snapshot) != len(referenced_ids):
        raise ValueError("solution evidence references a missing candidate")
    validate_evidence_fine_terms(evidence, _candidate_contract(snapshot))
    return snapshot


def _candidate_contract(
    candidates: Sequence[DeferredKnowledgeCandidate],
) -> dict[str, Any]:
    return {
        "candidates": {
            "knowledge": [item.to_dict() for item in candidates],
        }
    }


def _text_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            str(item or "").strip()
            for item in value
            if str(item or "").strip()
        )
    )


def _milestone_texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            target = str(item.get("target") or "").strip()
            observable = str(item.get("observable_evidence") or "").strip()
            text = "：".join(part for part in (target, observable) if part)
        else:
            text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _whole_question_answer(parts: Sequence[Mapping[str, Any]]) -> str:
    answers = [
        str(part.get("canonical_answer") or part.get("answer") or "").strip()
        for part in parts
    ]
    if len(answers) == 1:
        return answers[0]
    return "；".join(
        f"（{index}）{answer}"
        for index, answer in enumerate(answers, start=1)
        if answer
    )


def _question_type_from_skeleton(
    response_modes: Sequence[str],
    parts: Sequence[Mapping[str, Any]],
    canonical_answer: str,
) -> str:
    if len(parts) > 1:
        return "comprehensive"
    if any(mode == "visual_construction" for mode in response_modes):
        return "comprehensive"
    if any(
        _text_list(part.get("proof_obligations"))
        for part in parts
    ):
        return "proof"
    if any(mode == "process_required" for mode in response_modes):
        return "comprehensive"
    if all(mode == "exact_objective" for mode in response_modes) and re.fullmatch(
        r"[A-Da-d]",
        canonical_answer,
    ):
        return "choice"
    return "fill_blank"


__all__ = [
    "ConfirmedQuestionAdoptionLink",
    "ConfigQuestionAnalysisSource",
    "AnalysisRequestCheckpoint",
    "DeferredAnalysisFailure",
    "DeferredCombinedAnalysisBundle",
    "DeferredCombinedAnalysisItem",
    "DeferredCombinedProjectionWriter",
    "DeferredKnowledgeCandidate",
    "DeferredCombinedQuestionAnalysisModule",
    "UnmappedFineTermResolver",
    "compose_generated_config_from_skeletons",
]
