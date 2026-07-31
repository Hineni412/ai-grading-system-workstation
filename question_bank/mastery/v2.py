from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, TypeAlias


class EvidenceStatus(str, Enum):
    COMPLETED = "completed"
    PENDING_REVIEW = "pending_review"
    INCOMPLETE = "incomplete"
    MISSING = "missing"
    WITHDRAWN = "withdrawn"


class MasteryStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"


class EvidenceConflictError(ValueError):
    """Raised when one evidence identity is reused for different content."""


def _finite(value: object, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _bounded(
    value: object,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite(value, field)
    if number < minimum or number > maximum:
        raise ValueError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return number


def _positive(value: object, field: str, *, maximum: float = 10.0) -> float:
    number = _finite(value, field)
    if number <= 0.0 or number > maximum:
        raise ValueError(f"{field} must be positive and at most {maximum}")
    return number


def _identity(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be nonblank")
    if len(text) > 200:
        raise ValueError(f"{field} is too long")
    return text


def _stable_key(value: object) -> str:
    key = _identity(value, "stable_key").casefold()
    if re.fullmatch(r"(?:kp_[a-z0-9_]+|ki_[0-9a-f]{32})", key) is None:
        raise ValueError("stable_key must use a governed identity")
    return key


def _aware_datetime(
    value: datetime | None,
    field: str,
    *,
    required: bool,
) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _teacher_correction(
    *,
    status: EvidenceStatus,
    value: float | None,
    reason: str | None,
) -> tuple[float | None, str | None]:
    if value is None:
        if reason is not None:
            raise ValueError(
                "teacher_correction_reason requires teacher_correction"
            )
        return None, None
    if status is not EvidenceStatus.COMPLETED:
        raise ValueError(
            "teacher_correction is only allowed for completed evidence"
        )
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("teacher_correction_reason must be nonblank")
    return (
        _bounded(
            value,
            "teacher_correction",
            minimum=0.0,
            maximum=1.0,
        ),
        normalized_reason,
    )


@dataclass(frozen=True, slots=True)
class ExamEvidence:
    evidence_id: str
    stable_key: str
    occurred_at: datetime | None
    score_awarded: float | None
    full_score: float | None
    status: EvidenceStatus = EvidenceStatus.COMPLETED
    difficulty_weight: float = 1.0
    evidence_weight: float = 1.0
    teacher_correction: float | None = None
    teacher_correction_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _identity(self.evidence_id, "evidence_id"),
        )
        object.__setattr__(self, "stable_key", _stable_key(self.stable_key))
        status = EvidenceStatus(self.status)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "occurred_at",
            _aware_datetime(
                self.occurred_at,
                "occurred_at",
                required=status is EvidenceStatus.COMPLETED,
            ),
        )
        object.__setattr__(
            self,
            "difficulty_weight",
            _positive(self.difficulty_weight, "difficulty_weight"),
        )
        object.__setattr__(
            self,
            "evidence_weight",
            _positive(self.evidence_weight, "evidence_weight"),
        )
        correction, reason = _teacher_correction(
            status=status,
            value=self.teacher_correction,
            reason=self.teacher_correction_reason,
        )
        object.__setattr__(self, "teacher_correction", correction)
        object.__setattr__(self, "teacher_correction_reason", reason)
        if status is EvidenceStatus.COMPLETED:
            if self.score_awarded is None or self.full_score is None:
                raise ValueError(
                    "completed exam evidence requires score values"
                )
            full_score = _positive(
                self.full_score,
                "full_score",
                maximum=1_000_000.0,
            )
            score_awarded = _bounded(
                self.score_awarded,
                "score_awarded",
                minimum=0.0,
                maximum=full_score,
            )
            object.__setattr__(self, "full_score", full_score)
            object.__setattr__(self, "score_awarded", score_awarded)


@dataclass(frozen=True, slots=True)
class TrainingEvidence:
    evidence_id: str
    stable_key: str
    occurred_at: datetime | None
    achieved_points: int | None
    total_points: int | None
    status: EvidenceStatus = EvidenceStatus.COMPLETED
    difficulty_weight: float = 1.0
    evidence_weight: float = 1.0
    teacher_correction: float | None = None
    teacher_correction_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _identity(self.evidence_id, "evidence_id"),
        )
        object.__setattr__(self, "stable_key", _stable_key(self.stable_key))
        status = EvidenceStatus(self.status)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "occurred_at",
            _aware_datetime(
                self.occurred_at,
                "occurred_at",
                required=status is EvidenceStatus.COMPLETED,
            ),
        )
        object.__setattr__(
            self,
            "difficulty_weight",
            _positive(self.difficulty_weight, "difficulty_weight"),
        )
        object.__setattr__(
            self,
            "evidence_weight",
            _positive(self.evidence_weight, "evidence_weight"),
        )
        correction, reason = _teacher_correction(
            status=status,
            value=self.teacher_correction,
            reason=self.teacher_correction_reason,
        )
        object.__setattr__(self, "teacher_correction", correction)
        object.__setattr__(self, "teacher_correction_reason", reason)
        if status is EvidenceStatus.COMPLETED:
            if self.achieved_points is None or self.total_points is None:
                raise ValueError(
                    "completed training evidence requires point counts"
                )
            if isinstance(self.total_points, bool) or not isinstance(
                self.total_points, int
            ):
                raise ValueError("total_points must be an integer")
            if self.total_points <= 0:
                raise ValueError("total_points must be positive")
            if isinstance(self.achieved_points, bool) or not isinstance(
                self.achieved_points, int
            ):
                raise ValueError("achieved_points must be an integer")
            if (
                self.achieved_points < 0
                or self.achieved_points > self.total_points
            ):
                raise ValueError(
                    "achieved_points must be between zero and total_points"
                )


@dataclass(frozen=True, slots=True)
class PrerequisiteMastery:
    stable_key: str
    display_name: str
    status: MasteryStatus
    value: float | None
    evidence_count: int
    relation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stable_key",
            _stable_key(self.stable_key),
        )
        object.__setattr__(
            self,
            "display_name",
            _identity(self.display_name, "display_name"),
        )
        object.__setattr__(
            self,
            "relation_id",
            _identity(self.relation_id, "relation_id"),
        )
        status = MasteryStatus(self.status)
        object.__setattr__(self, "status", status)
        if (
            isinstance(self.evidence_count, bool)
            or not isinstance(self.evidence_count, int)
            or self.evidence_count < 0
        ):
            raise ValueError("evidence_count must be a nonnegative integer")
        if status is MasteryStatus.AVAILABLE:
            if self.value is None or self.evidence_count <= 0:
                raise ValueError(
                    "available prerequisite requires value and evidence"
                )
            object.__setattr__(
                self,
                "value",
                _bounded(
                    self.value,
                    "value",
                    minimum=0.0,
                    maximum=1.0,
                ),
            )
        elif self.value is not None:
            raise ValueError("missing prerequisite cannot have a value")


@dataclass(frozen=True, slots=True)
class MasteryV2Parameters:
    schema_version: Literal["mastery-v2-parameters-v1"] = (
        "mastery-v2-parameters-v1"
    )
    formula_version: Literal["mastery-v2-formula-v1"] = (
        "mastery-v2-formula-v1"
    )
    prior_mean: float = 0.65
    prior_strength: float = 2.0
    exam_source_weight: float = 1.0
    training_source_weight: float = 0.7
    exam_half_life_days: float = 180.0
    training_half_life_days: float = 90.0
    future_tolerance_seconds: int = 300
    output_precision: int = 6

    def __post_init__(self) -> None:
        if self.schema_version != "mastery-v2-parameters-v1":
            raise ValueError("unsupported parameter schema")
        if self.formula_version != "mastery-v2-formula-v1":
            raise ValueError("unsupported formula version")
        object.__setattr__(
            self,
            "prior_mean",
            _bounded(
                self.prior_mean,
                "prior_mean",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        prior_strength = _finite(self.prior_strength, "prior_strength")
        if prior_strength < 0.0 or prior_strength > 1_000.0:
            raise ValueError(
                "prior_strength must be between zero and 1000"
            )
        object.__setattr__(self, "prior_strength", prior_strength)
        for field in (
            "exam_source_weight",
            "training_source_weight",
            "exam_half_life_days",
            "training_half_life_days",
        ):
            object.__setattr__(
                self,
                field,
                _positive(
                    getattr(self, field),
                    field,
                    maximum=10_000.0,
                ),
            )
        if (
            isinstance(self.future_tolerance_seconds, bool)
            or not isinstance(self.future_tolerance_seconds, int)
            or self.future_tolerance_seconds < 0
            or self.future_tolerance_seconds > 86_400
        ):
            raise ValueError(
                "future_tolerance_seconds must be between zero and 86400"
            )
        if (
            isinstance(self.output_precision, bool)
            or not isinstance(self.output_precision, int)
            or self.output_precision < 4
            or self.output_precision > 12
        ):
            raise ValueError("output_precision must be between 4 and 12")

    @property
    def version(self) -> str:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MasteryEvidenceContribution:
    evidence_id: str
    source_kind: Literal["exam", "training"]
    status: EvidenceStatus
    included: bool
    exclusion_reason: str | None
    occurred_at: str | None
    raw_value: float | None
    effective_value: float | None
    teacher_corrected: bool
    difficulty_weight: float
    evidence_weight: float
    source_weight: float
    time_decay: float
    effective_weight: float
    weighted_value: float


@dataclass(frozen=True, slots=True)
class MasteryEvidenceLayer:
    source_kind: Literal["exam", "training"]
    included_count: int
    excluded_count: int
    effective_weight: float
    weighted_mean: float | None


@dataclass(frozen=True, slots=True)
class MasteryV2Result:
    schema_version: Literal["mastery-v2-result-v1"]
    stable_key: str
    status: MasteryStatus
    value: float | None
    as_of: str
    parameter_version: str
    direct_evidence_count: int
    effective_sample_weight: float
    prior_mean: float
    prior_strength: float
    contributions: tuple[MasteryEvidenceContribution, ...]
    layers: tuple[MasteryEvidenceLayer, ...]
    prerequisites: tuple[PrerequisiteMastery, ...]
    explanations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


MasteryEvidence: TypeAlias = ExamEvidence | TrainingEvidence


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _evidence_value(evidence: MasteryEvidence) -> float:
    if isinstance(evidence, ExamEvidence):
        return float(evidence.score_awarded) / float(evidence.full_score)
    return float(evidence.achieved_points) / float(evidence.total_points)


def _exclusion_reason(status: EvidenceStatus) -> str:
    return {
        EvidenceStatus.PENDING_REVIEW: "pending_review_not_counted",
        EvidenceStatus.INCOMPLETE: "incomplete_not_counted",
        EvidenceStatus.MISSING: "missing_not_counted",
        EvidenceStatus.WITHDRAWN: "withdrawn_not_counted",
        EvidenceStatus.COMPLETED: "",
    }[status]


def _source_settings(
    evidence: MasteryEvidence,
    parameters: MasteryV2Parameters,
) -> tuple[Literal["exam", "training"], float, float]:
    if isinstance(evidence, ExamEvidence):
        return (
            "exam",
            parameters.exam_source_weight,
            parameters.exam_half_life_days,
        )
    return (
        "training",
        parameters.training_source_weight,
        parameters.training_half_life_days,
    )


def _deduplicate(
    exam_evidence: tuple[ExamEvidence, ...],
    training_evidence: tuple[TrainingEvidence, ...],
) -> tuple[MasteryEvidence, ...]:
    unique: dict[str, MasteryEvidence] = {}
    for evidence in (*exam_evidence, *training_evidence):
        current = unique.get(evidence.evidence_id)
        if current is None:
            unique[evidence.evidence_id] = evidence
        elif current != evidence:
            raise EvidenceConflictError(
                f"conflicting evidence identity: {evidence.evidence_id}"
            )
    return tuple(
        unique[key]
        for key in sorted(unique)
    )


def _contribution(
    evidence: MasteryEvidence,
    *,
    as_of: datetime,
    parameters: MasteryV2Parameters,
) -> MasteryEvidenceContribution:
    source_kind, source_weight, half_life_days = _source_settings(
        evidence,
        parameters,
    )
    if evidence.status is not EvidenceStatus.COMPLETED:
        return MasteryEvidenceContribution(
            evidence_id=evidence.evidence_id,
            source_kind=source_kind,
            status=evidence.status,
            included=False,
            exclusion_reason=_exclusion_reason(evidence.status),
            occurred_at=(
                None
                if evidence.occurred_at is None
                else evidence.occurred_at.isoformat()
            ),
            raw_value=None,
            effective_value=None,
            teacher_corrected=False,
            difficulty_weight=evidence.difficulty_weight,
            evidence_weight=evidence.evidence_weight,
            source_weight=source_weight,
            time_decay=0.0,
            effective_weight=0.0,
            weighted_value=0.0,
        )
    occurred_at = evidence.occurred_at
    if occurred_at is None:
        raise ValueError("completed evidence requires occurred_at")
    future_seconds = (occurred_at - as_of).total_seconds()
    if future_seconds > parameters.future_tolerance_seconds:
        raise ValueError(
            f"evidence occurs after as_of: {evidence.evidence_id}"
        )
    age_days = max(
        0.0,
        (as_of - occurred_at).total_seconds() / 86_400.0,
    )
    decay = 0.5 ** (age_days / half_life_days)
    raw_value = _evidence_value(evidence)
    effective_value = (
        evidence.teacher_correction
        if evidence.teacher_correction is not None
        else raw_value
    )
    effective_weight = (
        source_weight
        * evidence.difficulty_weight
        * evidence.evidence_weight
        * decay
    )
    precision = parameters.output_precision + 4
    return MasteryEvidenceContribution(
        evidence_id=evidence.evidence_id,
        source_kind=source_kind,
        status=evidence.status,
        included=True,
        exclusion_reason=None,
        occurred_at=occurred_at.isoformat(),
        raw_value=round(raw_value, precision),
        effective_value=round(effective_value, precision),
        teacher_corrected=evidence.teacher_correction is not None,
        difficulty_weight=evidence.difficulty_weight,
        evidence_weight=evidence.evidence_weight,
        source_weight=source_weight,
        time_decay=round(decay, precision),
        effective_weight=round(effective_weight, precision),
        weighted_value=round(
            effective_value * effective_weight,
            precision,
        ),
    )


def _layer(
    source_kind: Literal["exam", "training"],
    contributions: tuple[MasteryEvidenceContribution, ...],
    precision: int,
) -> MasteryEvidenceLayer:
    source_items = tuple(
        item
        for item in contributions
        if item.source_kind == source_kind
    )
    included = tuple(item for item in source_items if item.included)
    total_weight = sum(item.effective_weight for item in included)
    weighted_sum = sum(item.weighted_value for item in included)
    return MasteryEvidenceLayer(
        source_kind=source_kind,
        included_count=len(included),
        excluded_count=len(source_items) - len(included),
        effective_weight=round(total_weight, precision),
        weighted_mean=(
            None
            if total_weight <= 0.0
            else round(weighted_sum / total_weight, precision)
        ),
    )


def _explanations(
    *,
    value: float | None,
    contributions: tuple[MasteryEvidenceContribution, ...],
    layers: tuple[MasteryEvidenceLayer, ...],
    parameters: MasteryV2Parameters,
    prerequisites: tuple[PrerequisiteMastery, ...],
) -> tuple[str, ...]:
    included = tuple(item for item in contributions if item.included)
    excluded_count = len(contributions) - len(included)
    if value is None:
        lines = ["当前没有可用于计算掌握度的直接证据。"]
    else:
        exam = next(item for item in layers if item.source_kind == "exam")
        training = next(
            item for item in layers if item.source_kind == "training"
        )
        lines = [
            (
                f"采用 {len(included)} 条直接证据：正式考试 "
                f"{exam.included_count} 条，个性化训练 "
                f"{training.included_count} 条。"
            ),
            (
                f"小样本按先验 {parameters.prior_mean:.0%}、强度 "
                f"{parameters.prior_strength:g} 进行收缩；结果为 "
                f"{value:.1%}。"
            ),
        ]
        strongest = sorted(
            included,
            key=lambda item: (
                -item.effective_weight,
                item.evidence_id,
            ),
        )[:3]
        if strongest:
            lines.append(
                "主要贡献证据："
                + "、".join(
                    (
                        f"{item.evidence_id}"
                        f"（{float(item.effective_value):.0%}，"
                        f"权重 {item.effective_weight:.3f}）"
                    )
                    for item in strongest
                )
                + "。"
            )
    if excluded_count:
        lines.append(
            f"{excluded_count} 条待复核、未完成、缺失或撤回证据未计入。"
        )
    if prerequisites:
        lines.append(
            f"另列出 {len(prerequisites)} 个先修知识点供解释，"
            "不计入本知识点掌握度。"
        )
    return tuple(lines)


def compute_mastery_v2(
    *,
    stable_key: str,
    as_of: datetime,
    exam_evidence: tuple[ExamEvidence, ...] = (),
    training_evidence: tuple[TrainingEvidence, ...] = (),
    prerequisites: tuple[PrerequisiteMastery, ...] = (),
    parameters: MasteryV2Parameters | None = None,
) -> MasteryV2Result:
    """Aggregate direct evidence without I/O or hidden mutable state."""

    normalized_as_of = _aware_datetime(as_of, "as_of", required=True)
    if normalized_as_of is None:
        raise ValueError("as_of is required")
    selected_parameters = parameters or MasteryV2Parameters()
    target_key = _stable_key(stable_key)
    unique_evidence = _deduplicate(exam_evidence, training_evidence)
    mismatched = tuple(
        evidence.evidence_id
        for evidence in unique_evidence
        if evidence.stable_key != target_key
    )
    if mismatched:
        raise ValueError(
            "evidence stable_key does not match target: "
            + ", ".join(mismatched)
        )
    contributions = tuple(
        _contribution(
            evidence,
            as_of=normalized_as_of,
            parameters=selected_parameters,
        )
        for evidence in unique_evidence
    )
    precision = selected_parameters.output_precision
    layers = (
        _layer("exam", contributions, precision),
        _layer("training", contributions, precision),
    )
    included = tuple(item for item in contributions if item.included)
    effective_weight = sum(item.effective_weight for item in included)
    if not included or effective_weight <= 0.0:
        status = MasteryStatus.MISSING
        value = None
    else:
        denominator = (
            selected_parameters.prior_strength + effective_weight
        )
        if denominator <= 0.0:
            raise ValueError("mastery denominator must be positive")
        numerator = (
            selected_parameters.prior_mean
            * selected_parameters.prior_strength
            + sum(item.weighted_value for item in included)
        )
        value = round(
            min(1.0, max(0.0, numerator / denominator)),
            precision,
        )
        status = MasteryStatus.AVAILABLE
    normalized_prerequisites = tuple(
        sorted(
            prerequisites,
            key=lambda item: (
                item.display_name,
                item.stable_key,
                item.relation_id,
            ),
        )
    )
    return MasteryV2Result(
        schema_version="mastery-v2-result-v1",
        stable_key=target_key,
        status=status,
        value=value,
        as_of=normalized_as_of.isoformat(),
        parameter_version=selected_parameters.version,
        direct_evidence_count=len(included),
        effective_sample_weight=round(effective_weight, precision),
        prior_mean=selected_parameters.prior_mean,
        prior_strength=selected_parameters.prior_strength,
        contributions=contributions,
        layers=layers,
        prerequisites=normalized_prerequisites,
        explanations=_explanations(
            value=value,
            contributions=contributions,
            layers=layers,
            parameters=selected_parameters,
            prerequisites=normalized_prerequisites,
        ),
    )


__all__ = [
    "EvidenceConflictError",
    "EvidenceStatus",
    "ExamEvidence",
    "MasteryEvidenceContribution",
    "MasteryEvidenceLayer",
    "MasteryStatus",
    "MasteryV2Parameters",
    "MasteryV2Result",
    "PrerequisiteMastery",
    "TrainingEvidence",
    "compute_mastery_v2",
]
