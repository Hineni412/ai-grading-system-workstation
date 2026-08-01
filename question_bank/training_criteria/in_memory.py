from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

from question_bank.models.tag_schema import TagAnalysis
from question_bank.solution_evidence.contracts import (
    CoreResolution,
    FineTermResolver,
    QuestionSolutionEvidence,
    validate_evidence_fine_terms,
)
from question_bank.solution_evidence.repository import (
    FineTermCoreMappingRepository,
    SolutionEvidenceRepository,
)
from question_bank.training_criteria.analysis import (
    GatewayBatchResponse,
    QuestionAnalysisGateway,
    QuestionAnalysisInput,
    grading_config_skeleton_from_solution_evidence,
    plan_analysis_batches,
    solution_evidence_source_content_hash,
)


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
class DeferredKnowledgeCandidate:
    """Minimal governed candidate snapshot needed to revalidate an adoption."""

    fine_term_id: str
    fine_term_name: str
    aliases: tuple[str, ...] = ()

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.fine_term_id,
            "name": self.fine_term_name,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DeferredKnowledgeCandidate":
        _require_exact_keys(
            payload,
            {"id", "name", "aliases"},
            "deferred knowledge candidate",
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
    model_name: str
    operation_id: str

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
        if not candidates or not all(
            isinstance(item, DeferredKnowledgeCandidate) for item in candidates
        ) or len(
            {item.fine_term_id for item in candidates}
        ) != len(candidates):
            raise ValueError("deferred knowledge candidates are empty or duplicated")
        validate_evidence_fine_terms(
            self.solution_evidence,
            _candidate_contract(candidates),
        )
        object.__setattr__(self, "source_question_ref", reference)
        object.__setattr__(self, "analysis_question_id", int(self.analysis_question_id))
        object.__setattr__(self, "source_content_hash", source_hash)
        object.__setattr__(self, "curriculum_volume_id", volume_id)
        object.__setattr__(self, "taxonomy_contract_hash", contract_hash)
        object.__setattr__(self, "knowledge_candidates", candidates)
        object.__setattr__(self, "model_name", model)
        object.__setattr__(self, "operation_id", operation)
        object.__setattr__(self, "tag_analysis", dict(self.tag_analysis))
        object.__setattr__(
            self,
            "solution_evidence_payload",
            dict(self.solution_evidence_payload),
        )

    def grading_config_skeleton(self) -> dict[str, Any]:
        return grading_config_skeleton_from_solution_evidence(
            self.solution_evidence,
            question_ref=self.source_question_ref,
        )

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
        validate_evidence_fine_terms(evidence, question.taxonomy_contract)
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
            "schema_version": "deferred-combined-analysis-item-v2",
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
            "model_name": self.model_name,
            "operation_id": self.operation_id,
        }
        return {**payload, "content_hash": _hash_payload(payload)}

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
    ) -> "DeferredCombinedAnalysisItem":
        _require_exact_keys(
            payload,
            {
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
            },
            "deferred analysis item",
        )
        if payload.get("schema_version") != "deferred-combined-analysis-item-v2":
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
            model_name=str(payload.get("model_name") or ""),
            operation_id=str(payload.get("operation_id") or ""),
        )

    @classmethod
    def from_checkpoint_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
    ) -> "DeferredCombinedAnalysisItem":
        return cls.from_dict(payload, resolver=resolver)


@dataclass(frozen=True, slots=True)
class DeferredAnalysisFailure:
    source_question_ref: str
    analysis_question_id: int
    request_id: str
    batch_hash: str
    category: str

    def __post_init__(self) -> None:
        if not str(self.source_question_ref or "").strip():
            raise ValueError("failure source_question_ref must not be empty")
        if int(self.analysis_question_id) <= 0:
            raise ValueError("failure analysis_question_id must be positive")
        _sha256_text(self.request_id, "request_id")
        _sha256_text(self.batch_hash, "batch_hash")
        if not str(self.category or "").strip():
            raise ValueError("failure category must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_question_ref": self.source_question_ref,
            "analysis_question_id": self.analysis_question_id,
            "request_id": self.request_id,
            "batch_hash": self.batch_hash,
            "category": self.category,
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
        if self.failures and self.items:
            return "partial"
        if self.failures:
            return "failed"
        return "succeeded"

    @property
    def failed_source_refs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(item.source_question_ref for item in self.failures)
        )

    @property
    def running_source_refs(self) -> tuple[str, ...]:
        return self._request_source_refs_with_statuses({"running"})

    @property
    def uncertain_source_refs(self) -> tuple[str, ...]:
        return self._request_source_refs_with_statuses(
            {"running", "outcome_unknown"}
        )

    def mark_interrupted_requests_unknown(self) -> "DeferredCombinedAnalysisBundle":
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
        return compose_generated_config_from_skeletons(
            self.grading_config_skeletons(),
            exam_title=exam_title,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "deferred-combined-analysis-v2",
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
    ) -> "DeferredCombinedAnalysisBundle":
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
        if payload.get("schema_version") != "deferred-combined-analysis-v2":
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
    ) -> "DeferredCombinedAnalysisBundle":
        return cls.from_dict(payload, resolver=resolver)


class InMemoryCombinedQuestionAnalysisModule:
    """Analyze config-source questions once, with no database writes."""

    def __init__(
        self,
        *,
        gateway: QuestionAnalysisGateway,
        resolver: FineTermResolver | None = None,
    ) -> None:
        self.gateway = gateway
        self.resolver = resolver or UnmappedFineTermResolver()

    def analyze(
        self,
        *,
        operation_id: str,
        curriculum_volume_id: str,
        sources: Sequence[ConfigQuestionAnalysisSource],
        checkpoint: Callable[[DeferredCombinedAnalysisBundle], None] | None = None,
    ) -> DeferredCombinedAnalysisBundle:
        clean_operation, volume_id, normalized = _normalize_sources(
            operation_id,
            curriculum_volume_id,
            sources,
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
            selected_sources=normalized,
            source_fingerprints=source_fingerprints,
            input_fingerprint=input_fingerprint,
            base_items=(),
            base_failures=(),
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
            if not selected_refs.issubset(failed_refs):
                raise ValueError(
                    "retry_source_refs must be a subset of previous failures"
                )
        selected = tuple(
            item
            for item in normalized
            if item.source_question_ref in failed_refs
            and (
                selected_refs is None
                or item.source_question_ref in selected_refs
            )
        )
        if not selected:
            return previous
        pending_refs = {item.source_question_ref for item in selected}
        return self._run(
            operation_id=clean_operation,
            curriculum_volume_id=volume_id,
            selected_sources=selected,
            source_fingerprints=current_fingerprints,
            input_fingerprint=current_input_fingerprint,
            base_items=previous.items,
            base_failures=tuple(
                item
                for item in previous.failures
                if item.source_question_ref not in pending_refs
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
    ) -> DeferredCombinedAnalysisBundle:
        by_question_id = {
            item.question.question_id: item for item in selected_sources
        }
        result = list(base_items)
        failures = list(base_failures)
        requests = list(base_requests)
        order = {
            reference: index
            for index, (reference, _fingerprint) in enumerate(source_fingerprints)
        }

        def snapshot() -> DeferredCombinedAnalysisBundle:
            bundle = DeferredCombinedAnalysisBundle(
                operation_id=operation_id,
                curriculum_volume_id=curriculum_volume_id,
                items=tuple(
                    sorted(result, key=lambda item: order[item.source_question_ref])
                ),
                failures=tuple(failures),
                requests=tuple(requests),
                source_fingerprints=source_fingerprints,
                input_fingerprint=input_fingerprint,
            )
            if checkpoint is not None:
                checkpoint(bundle)
            return bundle

        batches = plan_analysis_batches(
            tuple(item.question for item in selected_sources),
            projection="both",
        )
        for batch_index, batch in enumerate(batches, start=1):
            request_id = _hash_payload(
                {
                    "operation_id": operation_id,
                    "batch_hash": batch.batch_hash,
                    "attempt": len({item.request_id for item in requests}) + batch_index,
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
            requests.append(
                AnalysisRequestCheckpoint(
                    request_id=request_id,
                    request_fingerprint=request_fingerprint,
                    batch_hash=batch.batch_hash,
                    source_question_refs=source_refs,
                    status="running",
                )
            )
            snapshot()
            try:
                response = self.gateway.analyze(
                    batch,
                    projection="both",
                    operation_id=operation_id,
                    request_id=request_id,
                )
                raw_items = _response_items(response, batch.question_ids)
            except Exception as exc:
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
                    if category == "cancelled":
                        break
                    continue
                failures.extend(
                    DeferredAnalysisFailure(
                        source_question_ref=by_question_id[question_id].source_question_ref,
                        analysis_question_id=question_id,
                        request_id=request_id,
                        batch_hash=batch.batch_hash,
                        category=category,
                    )
                    for question_id in batch.question_ids
                )
                requests.append(
                    AnalysisRequestCheckpoint(
                        request_id=request_id,
                        request_fingerprint=request_fingerprint,
                        batch_hash=batch.batch_hash,
                        source_question_refs=source_refs,
                        status="cancelled" if category == "cancelled" else "failed",
                    )
                )
                snapshot()
                if category == "cancelled":
                    break
                continue
            parsed_count = 0
            validated_raw_results: list[dict[str, Any]] = []
            for question in batch.questions:
                source = by_question_id[question.question_id]
                try:
                    raw = raw_items[question.question_id]
                    raw_tag = raw.get("tag_analysis")
                    raw_evidence = raw.get("solution_evidence")
                    if not isinstance(raw_tag, Mapping):
                        raise ValueError("combined response tag_analysis is invalid")
                    if not isinstance(raw_evidence, Mapping):
                        raise ValueError("combined response solution_evidence is invalid")
                    _validate_model_tag_payload(raw_tag)
                    normalized_tag = TagAnalysis.from_dict(dict(raw_tag)).to_dict()
                    source_hash = solution_evidence_source_content_hash(question)
                    evidence = QuestionSolutionEvidence.from_model_dict(
                        raw_evidence,
                        question_id=question.question_id,
                        source_content_hash=source_hash,
                        resolver=self.resolver,
                    )
                    validate_evidence_fine_terms(
                        evidence,
                        question.taxonomy_contract,
                    )
                    candidate_snapshot = _minimal_candidate_snapshot(
                        question.taxonomy_contract,
                        evidence,
                    )
                except Exception:
                    failures.append(
                        DeferredAnalysisFailure(
                            source_question_ref=source.source_question_ref,
                            analysis_question_id=question.question_id,
                            request_id=request_id,
                            batch_hash=batch.batch_hash,
                            category="evidence_validation",
                        )
                    )
                    continue
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
                        solution_evidence_payload=dict(raw_evidence),
                        solution_evidence=evidence,
                        model_name=response.model_name,
                        operation_id=operation_id,
                    )
                )
                validated_raw_results.append(
                    {
                        "question_id": question.question_id,
                        "tag_analysis": dict(raw_tag),
                        "solution_evidence": dict(raw_evidence),
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
        return snapshot()


class DeferredCombinedProjectionWriter:
    """Persist deferred tag/evidence projections after a stable DB id exists."""

    def __init__(
        self,
        *,
        tag_writer: Any,
        mapping_repository: FineTermCoreMappingRepository,
        evidence_repository: SolutionEvidenceRepository,
    ) -> None:
        self.tag_writer = tag_writer
        self.mapping_repository = mapping_repository
        self.evidence_repository = evidence_repository

    def write(
        self,
        item: DeferredCombinedAnalysisItem,
        *,
        question: QuestionAnalysisInput,
        source_question_ref: str,
    ) -> dict[str, Any]:
        if str(source_question_ref or "").strip() != item.source_question_ref:
            raise ValueError("deferred analysis source reference does not match")
        tag_status = "failed"
        evidence_status = "failed"
        tag_error = ""
        evidence_error = ""
        evidence_version_id = ""
        try:
            self.tag_writer.write(
                question,
                item.tag_analysis,
                model_name=item.model_name,
                operation_id=item.operation_id,
            )
        except Exception:
            tag_error = "tag_validation"
        else:
            tag_status = "succeeded"
        try:
            evidence = item.bind_evidence(
                question,
                resolver=self.mapping_repository,
            )
            evidence_version_id = self.evidence_repository.save(
                evidence,
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
        return {
            "source_question_ref": item.source_question_ref,
            "question_id": question.question_id,
            "tag_status": tag_status,
            "tag_error_category": tag_error,
            "evidence_status": evidence_status,
            "evidence_error_category": evidence_error,
            "source_evidence_version_id": evidence_version_id,
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
        try:
            self.tag_writer.write(
                question,
                item.tag_analysis,
                model_name=item.model_name,
                operation_id=item.operation_id,
            )
        except Exception:
            tag_error = "tag_validation"
        else:
            tag_status = "succeeded"
        try:
            evidence = item.bind_linked_evidence(
                question,
                link=link,
                resolver=self.mapping_repository,
            )
            evidence_version_id = self.evidence_repository.save(
                evidence,
                source_kind="combined_model",
                source_reference=(
                    f"deferred-linked:{item.operation_id}:"
                    f"{item.source_question_ref}:{item.source_content_hash}"
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
        return {
            "source_question_ref": item.source_question_ref,
            "question_id": question.question_id,
            "adoption_mode": "confirmed_link",
            "tag_status": tag_status,
            "tag_error_category": tag_error,
            "evidence_status": evidence_status,
            "evidence_error_category": evidence_error,
            "source_evidence_version_id": evidence_version_id,
        }


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
                        "step_score": 0,
                        "core_goal": core_goal,
                        "required_elements": required_elements,
                        "allow_alternative_methods": allow_alternatives,
                        "equivalent_rules": _text_list(
                            raw_step.get("equivalent_rules")
                        ),
                        "counterexamples": _text_list(
                            raw_step.get("counterexamples")
                        ),
                    }
                )
            visual_requirements = _text_list(
                raw_part.get("visual_requirements")
            )
            rubric_parts.append(
                {
                    "part_id": part_id,
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


def _validate_model_tag_payload(payload: Mapping[str, Any]) -> None:
    _require_exact_keys(
        payload,
        {
            "knowledge_points",
            "method_tags",
            "ability_tags",
            "math_model_tags",
            "special_type_tags",
            "difficulty",
            "error_prone_points",
            "prerequisite_points",
            "textbook_chapters",
            "curriculum_sections",
            "suitable_student_level",
            "canonical_knowledge_id",
            "taxonomy_revision",
            "proposed_tags",
            "reason",
            "confidence",
        },
        "combined tag analysis",
    )


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
        previous = by_id.get(term_id)
        if previous is None:
            by_id[term_id] = DeferredKnowledgeCandidate(
                fine_term_id=term_id,
                fine_term_name=term_name,
                aliases=aliases,
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
    "InMemoryCombinedQuestionAnalysisModule",
    "UnmappedFineTermResolver",
    "compose_generated_config_from_skeletons",
]
