from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence


FineTermRole = Literal["direct", "supporting_prerequisite"]
CoreResolutionStatus = Literal["resolved", "ambiguous", "unmapped"]
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{1,127}$")
_CORE_KEY = re.compile(r"^(?:kp|ki)_[a-z0-9_]+$")
_BANNED_SCORE_KEYS = frozenset(
    {
        "score",
        "max_score",
        "min_score",
        "step_score",
        "total_score",
        "points_awarded",
        "score_awarded",
        "full_score",
    }
)
class FineTermResolver(Protocol):
    def resolve(self, fine_term_id: str) -> "CoreResolution": ...


@dataclass(frozen=True, slots=True)
class CoreResolution:
    status: CoreResolutionStatus
    stable_keys: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().casefold()
        if status not in {"resolved", "ambiguous", "unmapped"}:
            raise ValueError("core resolution status is invalid")
        keys = _unique_text(self.stable_keys, casefold=True)
        if any(not _CORE_KEY.fullmatch(item) for item in keys):
            raise ValueError("core resolution contains an invalid stable key")
        if status in {"resolved", "ambiguous"} and not keys:
            raise ValueError("resolved core candidates must not be empty")
        if status == "unmapped" and keys:
            raise ValueError("unmapped resolution cannot contain stable keys")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stable_keys", keys)
        object.__setattr__(self, "reason", str(self.reason or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stable_keys": list(self.stable_keys),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FineTermLink:
    fine_term_id: str
    fine_term_name: str
    role: FineTermRole
    core_resolution: CoreResolution

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
    ) -> "FineTermLink":
        _require_exact_keys(
            payload,
            {"fine_term_id", "fine_term_name", "role"},
            "fine_term_link",
        )
        fine_term_id = _required_text(payload.get("fine_term_id"), "fine_term_id")
        name = _required_text(payload.get("fine_term_name"), "fine_term_name")
        role = str(payload.get("role") or "").strip().casefold()
        if role not in {"direct", "supporting_prerequisite"}:
            raise ValueError("fine term role is invalid")
        return cls(
            fine_term_id=fine_term_id,
            fine_term_name=name,
            role=role,  # type: ignore[arg-type]
            core_resolution=resolver.resolve(fine_term_id),
        )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fine_term_id",
            _required_text(self.fine_term_id, "fine_term_id"),
        )
        object.__setattr__(
            self,
            "fine_term_name",
            _required_text(self.fine_term_name, "fine_term_name"),
        )
        role = str(self.role or "").strip().casefold()
        if role not in {"direct", "supporting_prerequisite"}:
            raise ValueError("fine term role is invalid")
        object.__setattr__(self, "role", role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fine_term_id": self.fine_term_id,
            "fine_term_name": self.fine_term_name,
            "role": self.role,
            "core_resolution": self.core_resolution.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SolutionEvidencePoint:
    evidence_point_id: str
    step_index: int
    target: str
    justification: str
    answer_anchor: str
    observable_evidence: str
    depends_on: tuple[str, ...]
    fine_term_links: tuple[FineTermLink, ...]
    equivalent_rules: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
        schema_version: str,
        expected_step_index: int,
    ) -> "SolutionEvidencePoint":
        v2 = schema_version == "question-solution-evidence-v2"
        required_keys = {
            "evidence_point_id",
            "target",
            "observable_evidence",
            "fine_term_links",
            "equivalent_rules",
            "counterexamples",
        }
        if v2:
            required_keys.update(
                {"step_index", "justification", "answer_anchor", "depends_on"}
            )
        _require_exact_keys(
            payload,
            required_keys,
            "solution_evidence_point",
        )
        raw_links = payload.get("fine_term_links")
        if not isinstance(raw_links, list):
            raise ValueError("fine_term_links must be an array")
        links = tuple(
            FineTermLink.from_model_dict(item, resolver=resolver)
            for item in raw_links
            if isinstance(item, Mapping)
        )
        if len(links) != len(raw_links):
            raise ValueError("fine term link is invalid")
        raw_dependencies = payload.get("depends_on") if v2 else []
        if v2 and (
            not isinstance(raw_dependencies, list)
            or not all(isinstance(item, str) for item in raw_dependencies)
            or len(raw_dependencies) != len(set(raw_dependencies))
        ):
            raise ValueError("depends_on must be an array of unique identifiers")
        return cls(
            evidence_point_id=str(payload.get("evidence_point_id") or ""),
            step_index=(
                payload.get("step_index")
                if v2
                else expected_step_index
            ),  # type: ignore[arg-type]
            target=str(payload.get("target") or ""),
            justification=(
                str(payload.get("justification") or "")
                if v2
                else str(payload.get("observable_evidence") or "")
            ),
            answer_anchor=(
                str(payload.get("answer_anchor") or "")
                if v2
                else str(payload.get("observable_evidence") or "")
            ),
            observable_evidence=str(payload.get("observable_evidence") or ""),
            depends_on=(
                _unique_text(raw_dependencies, casefold=True)
                if v2
                else ()
            ),
            fine_term_links=links,
            equivalent_rules=_unique_text(payload.get("equivalent_rules")),
            counterexamples=_unique_text(payload.get("counterexamples")),
        )

    def __post_init__(self) -> None:
        point_id = str(self.evidence_point_id or "").strip().casefold()
        if not _IDENTIFIER.fullmatch(point_id):
            raise ValueError("evidence_point_id is invalid")
        if (
            isinstance(self.step_index, bool)
            or not isinstance(self.step_index, int)
            or self.step_index <= 0
        ):
            raise ValueError("step_index must be positive")
        target = _required_text(self.target, "target")
        justification = _required_text(self.justification, "justification")
        answer_anchor = _required_text(self.answer_anchor, "answer_anchor")
        evidence = _required_text(self.observable_evidence, "observable_evidence")
        depends_on = _unique_text(self.depends_on, casefold=True)
        if any(not _IDENTIFIER.fullmatch(item) for item in depends_on):
            raise ValueError("depends_on contains an invalid evidence_point_id")
        links = tuple(self.fine_term_links)
        signatures = [(item.fine_term_id, item.role) for item in links]
        if len(signatures) != len(set(signatures)):
            raise ValueError("fine term link is duplicated in an evidence point")
        object.__setattr__(self, "evidence_point_id", point_id)
        object.__setattr__(self, "step_index", int(self.step_index))
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "justification", justification)
        object.__setattr__(self, "answer_anchor", answer_anchor)
        object.__setattr__(self, "observable_evidence", evidence)
        object.__setattr__(self, "depends_on", depends_on)
        object.__setattr__(self, "fine_term_links", links)
        object.__setattr__(self, "equivalent_rules", _unique_text(self.equivalent_rules))
        object.__setattr__(self, "counterexamples", _unique_text(self.counterexamples))

    def to_dict(self, *, schema_version: str) -> dict[str, Any]:
        payload = {
            "evidence_point_id": self.evidence_point_id,
            "target": self.target,
            "observable_evidence": self.observable_evidence,
            "fine_term_links": [item.to_dict() for item in self.fine_term_links],
            "equivalent_rules": list(self.equivalent_rules),
            "counterexamples": list(self.counterexamples),
        }
        if schema_version == "question-solution-evidence-v2":
            payload = {
                "evidence_point_id": self.evidence_point_id,
                "step_index": self.step_index,
                "target": self.target,
                "justification": self.justification,
                "answer_anchor": self.answer_anchor,
                "observable_evidence": self.observable_evidence,
                "depends_on": list(self.depends_on),
                "fine_term_links": [item.to_dict() for item in self.fine_term_links],
                "equivalent_rules": list(self.equivalent_rules),
                "counterexamples": list(self.counterexamples),
            }
        return payload


@dataclass(frozen=True, slots=True)
class QuestionPart:
    part_id: str
    label: str
    response_mode: Literal[
        "exact_objective",
        "short_answer_points",
        "process_required",
        "visual_construction",
    ]
    canonical_answer: str
    accepted_forms: tuple[str, ...]
    full_answer: str
    proof_obligations: tuple[str, ...]
    visual_requirements: tuple[str, ...]
    deduction_policy: tuple[str, ...]
    allow_alternative_methods: bool
    evidence_points: tuple[SolutionEvidencePoint, ...]

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        resolver: FineTermResolver,
        schema_version: str,
    ) -> "QuestionPart":
        _require_exact_keys(
            payload,
            {
                "part_id",
                "label",
                "response_mode",
                "canonical_answer",
                "accepted_forms",
                "full_answer",
                "proof_obligations",
                "visual_requirements",
                "deduction_policy",
                "allow_alternative_methods",
                "evidence_points",
            },
            "question_part",
        )
        raw_points = payload.get("evidence_points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError("question part must contain evidence points")
        points = tuple(
            SolutionEvidencePoint.from_model_dict(
                item,
                resolver=resolver,
                schema_version=schema_version,
                expected_step_index=index,
            )
            for index, item in enumerate(raw_points, start=1)
            if isinstance(item, Mapping)
        )
        if len(points) != len(raw_points):
            raise ValueError("solution evidence point is invalid")
        result = cls(
            part_id=str(payload.get("part_id") or ""),
            label=str(payload.get("label") or ""),
            response_mode=str(payload.get("response_mode") or ""),  # type: ignore[arg-type]
            canonical_answer=str(payload.get("canonical_answer") or ""),
            accepted_forms=_unique_text(payload.get("accepted_forms")),
            full_answer=str(payload.get("full_answer") or ""),
            proof_obligations=_unique_text(payload.get("proof_obligations")),
            visual_requirements=_unique_text(payload.get("visual_requirements")),
            deduction_policy=_unique_text(payload.get("deduction_policy")),
            allow_alternative_methods=payload.get("allow_alternative_methods"),  # type: ignore[arg-type]
            evidence_points=points,
        )
        if schema_version == "question-solution-evidence-v2":
            _validate_v2_step_sequence(result)
            _validate_v2_answer_anchors(result)
        return result

    def __post_init__(self) -> None:
        part_id = str(self.part_id or "").strip().casefold()
        if not _IDENTIFIER.fullmatch(part_id):
            raise ValueError("part_id is invalid")
        response_mode = str(self.response_mode or "").strip().casefold()
        if response_mode not in {
            "exact_objective",
            "short_answer_points",
            "process_required",
            "visual_construction",
        }:
            raise ValueError("response_mode is invalid")
        canonical_answer = str(self.canonical_answer or "").strip()
        full_answer = str(self.full_answer or "").strip()
        if response_mode == "exact_objective" and not canonical_answer:
            raise ValueError("objective part requires canonical_answer")
        if response_mode != "exact_objective" and not full_answer:
            raise ValueError("process part requires full_answer")
        if not isinstance(self.allow_alternative_methods, bool):
            raise ValueError("allow_alternative_methods must be boolean")
        deduction_policy = _unique_text(self.deduction_policy)
        if not deduction_policy:
            raise ValueError("deduction_policy must not be empty")
        points = tuple(self.evidence_points)
        if not points:
            raise ValueError("question part must contain evidence points")
        point_ids = [item.evidence_point_id for item in points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("evidence_point_id is duplicated in a question part")
        object.__setattr__(self, "part_id", part_id)
        object.__setattr__(self, "label", str(self.label or "").strip())
        object.__setattr__(self, "response_mode", response_mode)
        object.__setattr__(self, "canonical_answer", canonical_answer)
        object.__setattr__(self, "accepted_forms", _unique_text(self.accepted_forms))
        object.__setattr__(self, "full_answer", full_answer)
        object.__setattr__(self, "proof_obligations", _unique_text(self.proof_obligations))
        object.__setattr__(self, "visual_requirements", _unique_text(self.visual_requirements))
        object.__setattr__(self, "deduction_policy", deduction_policy)
        object.__setattr__(self, "evidence_points", points)

    def to_dict(self, *, schema_version: str) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "label": self.label,
            "response_mode": self.response_mode,
            "canonical_answer": self.canonical_answer,
            "accepted_forms": list(self.accepted_forms),
            "full_answer": self.full_answer,
            "proof_obligations": list(self.proof_obligations),
            "visual_requirements": list(self.visual_requirements),
            "deduction_policy": list(self.deduction_policy),
            "allow_alternative_methods": self.allow_alternative_methods,
            "evidence_points": [
                item.to_dict(schema_version=schema_version)
                for item in self.evidence_points
            ],
        }


@dataclass(frozen=True, slots=True)
class QuestionSolutionEvidence:
    schema_version: Literal[
        "question-solution-evidence-v1",
        "question-solution-evidence-v2",
    ]
    question_id: int
    source_content_hash: str
    parts: tuple[QuestionPart, ...]
    auxiliary_rules: tuple[str, ...]
    rationale: str
    confidence: float
    content_hash: str = field(init=False)
    version_id: str = field(init=False)

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        question_id: int,
        source_content_hash: str,
        resolver: FineTermResolver,
    ) -> "QuestionSolutionEvidence":
        _reject_score_fields(payload)
        _require_exact_keys(
            payload,
            {
                "schema_version",
                "question_id",
                "parts",
                "auxiliary_rules",
                "rationale",
                "confidence",
            },
            "question_solution_evidence",
        )
        schema_version = str(payload.get("schema_version") or "")
        if schema_version not in {
            "question-solution-evidence-v1",
            "question-solution-evidence-v2",
        }:
            raise ValueError("solution evidence schema version is invalid")
        try:
            submitted_question_id = int(payload.get("question_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("solution evidence question_id is invalid") from exc
        if submitted_question_id != int(question_id):
            raise ValueError("solution evidence belongs to another question")
        raw_parts = payload.get("parts")
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("solution evidence must contain question parts")
        parts = tuple(
            QuestionPart.from_model_dict(
                item,
                resolver=resolver,
                schema_version=schema_version,
            )
            for item in raw_parts
            if isinstance(item, Mapping)
        )
        if len(parts) != len(raw_parts):
            raise ValueError("question part is invalid")
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("solution evidence confidence is invalid") from exc
        return cls(
            schema_version=schema_version,  # type: ignore[arg-type]
            question_id=int(question_id),
            source_content_hash=str(source_content_hash or ""),
            parts=parts,
            auxiliary_rules=_unique_text(payload.get("auxiliary_rules")),
            rationale=str(payload.get("rationale") or "").strip(),
            confidence=confidence,
        )

    def __post_init__(self) -> None:
        if self.schema_version not in {
            "question-solution-evidence-v1",
            "question-solution-evidence-v2",
        }:
            raise ValueError("solution evidence schema version is invalid")
        if isinstance(self.question_id, bool) or int(self.question_id) <= 0:
            raise ValueError("question_id must be positive")
        source_hash = str(self.source_content_hash or "").strip().casefold()
        if len(source_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_hash):
            raise ValueError("source_content_hash is invalid")
        parts = tuple(self.parts)
        if not parts:
            raise ValueError("solution evidence must contain question parts")
        part_ids = [item.part_id for item in parts]
        point_ids = [
            point.evidence_point_id
            for part in parts
            for point in part.evidence_points
        ]
        if len(part_ids) != len(set(part_ids)):
            raise ValueError("part_id is duplicated")
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("evidence_point_id must be unique within a question")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("solution evidence confidence is invalid")
        object.__setattr__(self, "question_id", int(self.question_id))
        object.__setattr__(self, "source_content_hash", source_hash)
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "auxiliary_rules", _unique_text(self.auxiliary_rules))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "confidence", confidence)
        semantic_payload = self._semantic_payload()
        content_hash = _hash_payload(semantic_payload)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "version_id",
            _hash_payload(
                {
                    "schema_version": self.schema_version,
                    "question_id": self.question_id,
                    "source_content_hash": source_hash,
                    "content_hash": content_hash,
                }
            ),
        )

    def whole_question_classification(self) -> dict[str, Any]:
        direct: list[dict[str, str]] = []
        supporting: list[dict[str, str]] = []
        direct_resolved_core: list[str] = []
        supporting_resolved_core: list[str] = []
        direct_ambiguous_core: list[str] = []
        supporting_ambiguous_core: list[str] = []
        direct_unmapped: list[str] = []
        supporting_unmapped: list[str] = []
        resolved_core: list[str] = []
        ambiguous_core: list[str] = []
        unmapped: list[str] = []
        seen_links: dict[str, set[tuple[str, str]]] = {
            "direct": set(),
            "supporting_prerequisite": set(),
        }
        for part in self.parts:
            for point in part.evidence_points:
                for link in point.fine_term_links:
                    signature = (link.fine_term_id, link.fine_term_name)
                    bucket = direct if link.role == "direct" else supporting
                    if signature not in seen_links[link.role]:
                        seen_links[link.role].add(signature)
                        bucket.append(
                            {
                                "fine_term_id": link.fine_term_id,
                                "fine_term_name": link.fine_term_name,
                            }
                        )
                    resolution = link.core_resolution
                    if resolution.status == "resolved":
                        role_bucket = (
                            direct_resolved_core
                            if link.role == "direct"
                            else supporting_resolved_core
                        )
                        _extend_unique(role_bucket, resolution.stable_keys)
                        _extend_unique(resolved_core, resolution.stable_keys)
                    elif resolution.status == "ambiguous":
                        role_bucket = (
                            direct_ambiguous_core
                            if link.role == "direct"
                            else supporting_ambiguous_core
                        )
                        _extend_unique(role_bucket, resolution.stable_keys)
                        _extend_unique(ambiguous_core, resolution.stable_keys)
                    else:
                        role_bucket = (
                            direct_unmapped
                            if link.role == "direct"
                            else supporting_unmapped
                        )
                        _extend_unique(role_bucket, (link.fine_term_id,))
                        _extend_unique(unmapped, (link.fine_term_id,))
        return {
            "direct_fine_terms": direct,
            "supporting_prerequisite_fine_terms": supporting,
            "direct_resolved_core_node_ids": direct_resolved_core,
            "supporting_resolved_core_node_ids": supporting_resolved_core,
            "direct_ambiguous_core_node_ids": direct_ambiguous_core,
            "supporting_ambiguous_core_node_ids": supporting_ambiguous_core,
            "direct_unmapped_fine_term_ids": direct_unmapped,
            "supporting_unmapped_fine_term_ids": supporting_unmapped,
            "resolved_core_node_ids": resolved_core,
            "ambiguous_core_node_ids": ambiguous_core,
            "unmapped_fine_term_ids": unmapped,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._semantic_payload(),
            "content_hash": self.content_hash,
            "version_id": self.version_id,
            "whole_question_classification": self.whole_question_classification(),
        }

    def _semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "source_content_hash": self.source_content_hash,
            "parts": [
                item.to_dict(schema_version=self.schema_version)
                for item in self.parts
            ],
            "auxiliary_rules": list(self.auxiliary_rules),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _validate_v2_step_sequence(part: QuestionPart) -> None:
    prior_ids: set[str] = set()
    for expected_index, point in enumerate(part.evidence_points, start=1):
        if point.step_index != expected_index:
            raise ValueError("step_index must be contiguous and match array order")
        if point.evidence_point_id in point.depends_on:
            raise ValueError(
                f"位置 {part.part_id}/{point.evidence_point_id}："
                "评分点不能依赖自身"
            )
        invalid_dependencies = tuple(
            dependency
            for dependency in point.depends_on
            if dependency not in prior_ids
        )
        if invalid_dependencies:
            raise ValueError(
                f"位置 {part.part_id}/{point.evidence_point_id}：depends_on "
                "只能引用同一小问内更早的评分点，发现 "
                + "、".join(invalid_dependencies)
            )
        prior_ids.add(point.evidence_point_id)


def _validate_v2_answer_anchors(part: QuestionPart) -> None:
    source = (
        part.canonical_answer
        if part.response_mode == "exact_objective"
        else part.full_answer
    )
    cursor = 0
    seen: set[str] = set()
    for point in part.evidence_points:
        anchor = point.answer_anchor.strip()
        if anchor in seen:
            raise ValueError("answer_anchor must be unique within a question part")
        position = source.find(anchor, cursor)
        if position < 0:
            raise ValueError(
                "answer_anchor must occur in its answer source in evidence point order"
            )
        seen.add(anchor)
        cursor = position + len(anchor)


def validate_evidence_fine_terms(
    evidence: QuestionSolutionEvidence,
    taxonomy_contract: Mapping[str, Any],
    *,
    additional_allowed_term_ids: Sequence[str] = (),
) -> None:
    """Require links to match the shortlist or governed local convergence."""

    links = tuple(
        link
        for part in evidence.parts
        for point in part.evidence_points
        for link in point.fine_term_links
    )
    if not links:
        return

    raw_candidates = taxonomy_contract.get("candidates")
    knowledge = (
        raw_candidates.get("knowledge")
        if isinstance(raw_candidates, Mapping)
        else None
    )
    candidates: dict[str, set[str]] = {}
    for raw in (knowledge if isinstance(knowledge, list) else []):
        if not isinstance(raw, Mapping):
            continue
        term_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not term_id or not name:
            continue
        names = {name}
        aliases = raw.get("aliases")
        if isinstance(aliases, list):
            names.update(str(item).strip() for item in aliases if str(item).strip())
        candidates.setdefault(term_id, set()).update(
            _normalize_term_name(item) for item in names
        )
    locally_converged = {
        str(item or "").strip() for item in additional_allowed_term_ids
    }
    for link in links:
        if link.fine_term_id in locally_converged:
            continue
        allowed_names = candidates.get(link.fine_term_id)
        if not allowed_names or _normalize_term_name(
            link.fine_term_name
        ) not in allowed_names:
            raise ValueError(
                "solution evidence fine term is outside the question contract"
            )


def _normalize_term_name(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "")).casefold()


def _unique_text(value: object, *, casefold: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = str(raw or "").strip()
        normalized = text.casefold() if casefold else text
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized if casefold else text)
    return tuple(result)


def _extend_unique(target: list[str], values: Sequence[str]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            seen.add(value)
            target.append(value)


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    submitted = {str(key) for key in payload}
    if submitted != expected:
        raise ValueError(f"{label} fields do not match the contract")


def _reject_score_fields(value: object, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold()
            if key in _BANNED_SCORE_KEYS:
                raise ValueError(f"score field is forbidden at {path}.{key}")
            _reject_score_fields(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_score_fields(child, path=f"{path}[{index}]")


def _hash_payload(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "CoreResolution",
    "CoreResolutionStatus",
    "FineTermLink",
    "FineTermResolver",
    "FineTermRole",
    "QuestionPart",
    "QuestionSolutionEvidence",
    "SolutionEvidencePoint",
    "validate_evidence_fine_terms",
]
