from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Mapping

from question_bank.mastery.v2 import (
    EvidenceStatus,
    ExamEvidence,
    MasteryStatus,
    MasteryV2Parameters,
    MasteryV2Result,
    TrainingEvidence,
    compute_mastery_v2,
)
from question_bank.taxonomy.registry import canonicalize_knowledge_exact


DEFAULT_REVIEW_DELTA = 0.10
_CHINA_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class MasteryComparisonCase:
    student_id: str
    student_code: str
    student_name: str
    class_id: str
    stable_key: str
    display_name: str
    mastery_v1: float | None
    exam_evidence: tuple[ExamEvidence, ...] = ()
    training_evidence: tuple[TrainingEvidence, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "student_id",
            "stable_key",
            "display_name",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} must be nonblank")
        if self.mastery_v1 is not None:
            value = float(self.mastery_v1)
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise ValueError("mastery_v1 must be between zero and one")
            object.__setattr__(self, "mastery_v1", value)


@dataclass(frozen=True, slots=True)
class MasteryComparisonItem:
    item_hash: str
    student_id: str
    student_code: str
    student_name: str
    class_id: str
    stable_key: str
    display_name: str
    mastery_v1: float | None
    mastery_v2: MasteryV2Result
    signed_delta: float | None
    absolute_delta: float | None
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mastery_v2"] = self.mastery_v2.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class MasteryComparisonReport:
    schema_version: str
    evaluation_id: str
    as_of: str
    parameter_version: str
    review_delta: float
    items: tuple[MasteryComparisonItem, ...]
    required_review_count: int
    maximum_absolute_delta: float | None
    duration_ms: float
    items_per_second: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "as_of": self.as_of,
            "parameter_version": self.parameter_version,
            "review_delta": self.review_delta,
            "items": [item.to_dict() for item in self.items],
            "required_review_count": self.required_review_count,
            "maximum_absolute_delta": self.maximum_absolute_delta,
            "performance": {
                "duration_ms": self.duration_ms,
                "items_per_second": self.items_per_second,
            },
        }


def compare_mastery_v1_v2(
    cases: tuple[MasteryComparisonCase, ...],
    *,
    as_of: datetime,
    parameters: MasteryV2Parameters | None = None,
    review_delta: float = DEFAULT_REVIEW_DELTA,
) -> MasteryComparisonReport:
    if not cases:
        raise ValueError("comparison requires at least one case")
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    threshold = float(review_delta)
    if not math.isfinite(threshold) or threshold <= 0.0 or threshold > 1.0:
        raise ValueError("review_delta must be between zero and one")
    normalized_as_of = as_of.astimezone(UTC)
    selected_parameters = parameters or MasteryV2Parameters()
    started = perf_counter()
    items = tuple(
        _compare_case(
            case,
            as_of=normalized_as_of,
            parameters=selected_parameters,
            review_delta=threshold,
        )
        for case in cases
    )
    ranked = tuple(
        sorted(
            items,
            key=lambda item: (
                "availability_changed" not in item.reason_codes,
                -(item.absolute_delta or 0.0),
                item.student_code,
                item.student_id,
                item.display_name,
                item.stable_key,
            ),
        )
    )
    if len({item.item_hash for item in ranked}) != len(ranked):
        raise ValueError("comparison cases contain duplicate identities")
    if not any(item.requires_review for item in ranked):
        first = ranked[0]
        ranked = (
            MasteryComparisonItem(
                **{
                    **asdict(first),
                    "mastery_v2": first.mastery_v2,
                    "requires_review": True,
                    "reason_codes": (
                        *first.reason_codes,
                        "teacher_sample_required",
                    ),
                    "reasons": (
                        *first.reasons,
                        "当前没有超过门槛的差异，仍抽检差异榜首项。",
                    ),
                }
            ),
            *ranked[1:],
        )
    elapsed = max(perf_counter() - started, 0.0)
    evaluation_id = _evaluation_id(
        ranked,
        as_of=normalized_as_of,
        parameter_version=selected_parameters.version,
        review_delta=threshold,
    )
    comparable_deltas = tuple(
        item.absolute_delta
        for item in ranked
        if item.absolute_delta is not None
    )
    return MasteryComparisonReport(
        schema_version="mastery-v1-v2-comparison-v1",
        evaluation_id=evaluation_id,
        as_of=normalized_as_of.isoformat(),
        parameter_version=selected_parameters.version,
        review_delta=threshold,
        items=ranked,
        required_review_count=sum(item.requires_review for item in ranked),
        maximum_absolute_delta=(
            None
            if not comparable_deltas
            else round(max(comparable_deltas), 6)
        ),
        duration_ms=round(elapsed * 1000.0, 3),
        items_per_second=round(len(ranked) / max(elapsed, 1e-9), 3),
    )


def build_profile_comparison_cases(
    profile: Mapping[str, Any],
) -> tuple[MasteryComparisonCase, ...]:
    session_times = _session_times(profile.get("_mastery_session_times"))
    cases: list[MasteryComparisonCase] = []
    students = profile.get("students")
    if not isinstance(students, list):
        return ()
    for student in students:
        if not isinstance(student, Mapping):
            continue
        student_id = str(student.get("student_id") or "").strip()
        weak_points = student.get("weak_points")
        if not student_id or not isinstance(weak_points, list):
            continue
        for weak_point in weak_points:
            if not isinstance(weak_point, Mapping):
                continue
            display_name = str(
                weak_point.get("knowledge_point") or ""
            ).strip()
            canonical = canonicalize_knowledge_exact(display_name)
            if canonical is None:
                continue
            stable_key = canonical.canonical_id.casefold()
            references = weak_point.get("source_question_refs")
            exam_evidence = tuple(
                _exam_evidence(
                    reference,
                    student_id=student_id,
                    stable_key=stable_key,
                    session_times=session_times,
                )
                for reference in (
                    references if isinstance(references, list) else []
                )
                if isinstance(reference, Mapping)
            )
            v1_value = _optional_rate(weak_point.get("mastery"))
            cases.append(
                MasteryComparisonCase(
                    student_id=student_id,
                    student_code=str(
                        student.get("student_code") or ""
                    ).strip(),
                    student_name=str(
                        student.get("student_name") or ""
                    ).strip(),
                    class_id=str(student.get("class_id") or "").strip(),
                    stable_key=stable_key,
                    display_name=canonical.canonical_name,
                    mastery_v1=v1_value,
                    exam_evidence=exam_evidence,
                )
            )
    return tuple(
        sorted(
            cases,
            key=lambda item: (
                item.student_code,
                item.student_id,
                item.display_name,
                item.stable_key,
            ),
        )
    )


def _compare_case(
    case: MasteryComparisonCase,
    *,
    as_of: datetime,
    parameters: MasteryV2Parameters,
    review_delta: float,
) -> MasteryComparisonItem:
    v2 = compute_mastery_v2(
        stable_key=case.stable_key,
        as_of=as_of,
        exam_evidence=case.exam_evidence,
        training_evidence=case.training_evidence,
        parameters=parameters,
    )
    v2_value = v2.value if v2.status is MasteryStatus.AVAILABLE else None
    availability_changed = (case.mastery_v1 is None) != (v2_value is None)
    signed_delta = (
        None
        if case.mastery_v1 is None or v2_value is None
        else round(v2_value - case.mastery_v1, 6)
    )
    absolute_delta = (
        None if signed_delta is None else round(abs(signed_delta), 6)
    )
    codes, reasons = _difference_reasons(
        case,
        v2,
        availability_changed=availability_changed,
    )
    requires_review = (
        availability_changed
        or (
            absolute_delta is not None
            and absolute_delta > review_delta
        )
    )
    fingerprint = {
        "student_id": case.student_id,
        "stable_key": case.stable_key,
        "mastery_v1": case.mastery_v1,
        "mastery_v2": v2.to_dict(),
        "reason_codes": codes,
    }
    return MasteryComparisonItem(
        item_hash=_hash_payload(fingerprint),
        student_id=case.student_id,
        student_code=case.student_code,
        student_name=case.student_name,
        class_id=case.class_id,
        stable_key=case.stable_key,
        display_name=case.display_name,
        mastery_v1=case.mastery_v1,
        mastery_v2=v2,
        signed_delta=signed_delta,
        absolute_delta=absolute_delta,
        reason_codes=codes,
        reasons=reasons,
        requires_review=requires_review,
    )


def _difference_reasons(
    case: MasteryComparisonCase,
    result: MasteryV2Result,
    *,
    availability_changed: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pairs: list[tuple[str, str]] = []
    if availability_changed:
        pairs.append(
            (
                "availability_changed",
                "v1 与 v2 对“是否有足够证据”的判断不同。",
            )
        )
    if result.prior_strength > 0.0 and result.value is not None:
        pairs.append(
            (
                "small_sample_shrinkage",
                "v2 对小样本采用先验收缩，避免少量证据直接形成极端值。",
            )
        )
    included = tuple(item for item in result.contributions if item.included)
    if any(item.time_decay < 0.999999 for item in included):
        pairs.append(
            (
                "time_decay",
                "v2 会降低较早证据的影响。",
            )
        )
    if case.training_evidence:
        pairs.append(
            (
                "training_layer",
                "v2 将个性化训练作为独立证据层按题归一化后纳入。",
            )
        )
    if any(item.teacher_corrected for item in included):
        pairs.append(
            (
                "teacher_correction",
                "v2 使用了带理由的教师修正值，并保留原始值。",
            )
        )
    if any(not item.included for item in result.contributions):
        pairs.append(
            (
                "excluded_evidence",
                "待复核、未完成、缺失或撤回证据未被当作零分。",
            )
        )
    if not pairs:
        pairs.append(
            (
                "formula_weighting",
                "v2 按单题证据权重聚合，口径不同于 v1 的满分加权。",
            )
        )
    return (
        tuple(code for code, _reason in pairs),
        tuple(reason for _code, reason in pairs),
    )


def _exam_evidence(
    reference: Mapping[str, Any],
    *,
    student_id: str,
    stable_key: str,
    session_times: Mapping[int, datetime],
) -> ExamEvidence:
    session_id = int(reference.get("session_id") or 0)
    full_score = _optional_number(reference.get("full_score"))
    score_awarded = _optional_number(reference.get("score_awarded"))
    occurred_at = session_times.get(session_id)
    status = (
        EvidenceStatus.COMPLETED
        if (
            occurred_at is not None
            and full_score is not None
            and full_score > 0.0
            and score_awarded is not None
        )
        else EvidenceStatus.MISSING
    )
    if status is EvidenceStatus.COMPLETED:
        score_awarded = min(max(float(score_awarded), 0.0), float(full_score))
    else:
        score_awarded = None
        full_score = None
    return ExamEvidence(
        evidence_id=(
            f"exam:{session_id}:{student_id}:"
            f"{reference.get('question_id') or ''}:"
            f"{reference.get('bank_question_id') or 0}"
        ),
        stable_key=stable_key,
        occurred_at=occurred_at,
        score_awarded=score_awarded,
        full_score=full_score,
        status=status,
    )


def _session_times(value: object) -> dict[int, datetime]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[int, datetime] = {}
    for raw_key, raw_value in value.items():
        try:
            session_id = int(raw_key)
        except (TypeError, ValueError):
            continue
        parsed = _parse_datetime(raw_value)
        if parsed is not None:
            result[session_id] = parsed
    return result


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CHINA_TIMEZONE)
    return parsed.astimezone(UTC)


def _optional_rate(value: object) -> float | None:
    number = _optional_number(value)
    if number is None or number < 0.0 or number > 1.0:
        return None
    return number


def _optional_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _evaluation_id(
    items: tuple[MasteryComparisonItem, ...],
    *,
    as_of: datetime,
    parameter_version: str,
    review_delta: float,
) -> str:
    return _hash_payload(
        {
            "schema_version": "mastery-v1-v2-comparison-v1",
            "as_of": as_of.isoformat(),
            "parameter_version": parameter_version,
            "review_delta": review_delta,
            "items": [
                {
                    "item_hash": item.item_hash,
                    "requires_review": item.requires_review,
                }
                for item in items
            ],
        }
    )


def _hash_payload(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_REVIEW_DELTA",
    "MasteryComparisonCase",
    "MasteryComparisonItem",
    "MasteryComparisonReport",
    "build_profile_comparison_cases",
    "compare_mastery_v1_v2",
]
