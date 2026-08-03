from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.mastery.v2 import (
    EvidenceStatus,
    ExamEvidence,
    MasteryV2Parameters,
    TrainingEvidence,
    compute_mastery_v2,
)


_CHINA_TIMEZONE = timezone(timedelta(hours=8))
CURRENT_MASTERY_PARAMETERS = MasteryV2Parameters()


@dataclass(frozen=True, slots=True)
class CurrentMastery:
    stable_key: str
    display_name: str
    status: str
    value: float | None
    evidence_count: int
    parameter_version: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "value": self.value,
            "evidence_count": self.evidence_count,
            "parameter_version": self.parameter_version,
            "reason": self.reason,
        }


class CurrentMasteryCalculator:
    """Compute the sole current mastery formula for current core identities."""

    def __init__(
        self,
        db_path: Path,
        resolver: CurrentKnowledgeResolver,
        *,
        parameters: MasteryV2Parameters | None = CURRENT_MASTERY_PARAMETERS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.resolver = resolver
        self.parameters = parameters
        self.clock = clock or (lambda: datetime.now(UTC))

    def calculate(
        self,
        profile: Mapping[str, Any],
        *,
        exclude_training_evidence_ids: frozenset[str] = frozenset(),
    ) -> dict[tuple[str, str], CurrentMastery]:
        if self.parameters is None:
            raise ValueError("current mastery parameters are unavailable")
        exam = self._exam_evidence(profile)
        training = self._training_evidence(
            exclude_evidence_ids=exclude_training_evidence_ids
        )
        identities = set(exam) | set(training)
        as_of = self.clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("mastery clock must include a timezone")
        latest = [
            item.occurred_at
            for values in (*exam.values(), *training.values())
            for item in values
            if item.occurred_at is not None
        ]
        if latest:
            as_of = max(as_of.astimezone(UTC), *latest)
        result: dict[tuple[str, str], CurrentMastery] = {}
        for student_id, stable_key in sorted(identities):
            node = self.resolver.node(stable_key)
            if node is None:
                continue
            calculated = compute_mastery_v2(
                stable_key=stable_key,
                as_of=as_of,
                exam_evidence=tuple(exam.get((student_id, stable_key), ())),
                training_evidence=tuple(training.get((student_id, stable_key), ())),
                parameters=self.parameters,
            )
            result[(student_id, stable_key)] = CurrentMastery(
                stable_key=stable_key,
                display_name=node.display_name,
                status=calculated.status.value,
                value=calculated.value,
                evidence_count=calculated.direct_evidence_count,
                parameter_version=calculated.parameter_version,
                reason=(
                    None
                    if calculated.value is not None
                    else "current_mastery_evidence_missing"
                ),
            )
        return result

    def _exam_evidence(
        self,
        profile: Mapping[str, Any],
    ) -> dict[tuple[str, str], list[ExamEvidence]]:
        session_times = _session_times(profile.get("_mastery_session_times"))
        result: dict[tuple[str, str], list[ExamEvidence]] = defaultdict(list)
        seen: set[tuple[str, str, str]] = set()
        students = profile.get("students")
        if not isinstance(students, list):
            return result
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
                resolved = self.resolver.resolve(
                    weak_point.get("knowledge_point")
                    or weak_point.get("knowledge_key")
                )
                references = weak_point.get("source_question_refs")
                if not isinstance(references, list):
                    continue
                for target in resolved:
                    for reference in references:
                        if not isinstance(reference, Mapping):
                            continue
                        evidence = _exam_evidence(
                            reference,
                            student_id=student_id,
                            stable_key=target.stable_key,
                            session_times=session_times,
                        )
                        identity = (
                            student_id,
                            target.stable_key,
                            evidence.evidence_id,
                        )
                        if identity in seen:
                            continue
                        seen.add(identity)
                        result[(student_id, target.stable_key)].append(evidence)
        return result

    def _training_evidence(
        self,
        *,
        exclude_evidence_ids: frozenset[str],
    ) -> dict[tuple[str, str], list[TrainingEvidence]]:
        if not self.db_path.is_file():
            raise sqlite3.OperationalError("question bank database is missing")
        uri = self.db_path.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT evidence_id, student_id, stable_key, occurred_at,
                       achieved_points, total_points, difficulty_weight,
                       evidence_weight
                FROM training_evidence_records
                WHERE status = 'active'
                ORDER BY student_id, stable_key, occurred_at, evidence_id
                """
            ).fetchall()
        finally:
            connection.close()
        result: dict[tuple[str, str], list[TrainingEvidence]] = defaultdict(list)
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            if str(row["evidence_id"]) in exclude_evidence_ids:
                continue
            student_id = str(row["student_id"])
            for target in self.resolver.resolve(row["stable_key"]):
                identity = (student_id, target.stable_key, str(row["evidence_id"]))
                if identity in seen:
                    continue
                seen.add(identity)
                result[(student_id, target.stable_key)].append(
                    TrainingEvidence(
                        evidence_id=str(row["evidence_id"]),
                        stable_key=target.stable_key,
                        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
                        achieved_points=int(row["achieved_points"]),
                        total_points=int(row["total_points"]),
                        difficulty_weight=float(row["difficulty_weight"]),
                        evidence_weight=float(row["evidence_weight"]),
                    )
                )
        return result


def aggregate_current_mastery(
    values: Mapping[tuple[str, str], CurrentMastery],
) -> dict[str, CurrentMastery]:
    grouped: dict[str, list[CurrentMastery]] = defaultdict(list)
    for (_student_id, stable_key), item in values.items():
        grouped[stable_key].append(item)
    result: dict[str, CurrentMastery] = {}
    for stable_key, items in grouped.items():
        available = [item for item in items if item.value is not None]
        exemplar = items[0]
        if not available:
            result[stable_key] = exemplar
            continue
        total_weight = sum(max(item.evidence_count, 1) for item in available)
        value = sum(
            float(item.value) * max(item.evidence_count, 1)
            for item in available
            if item.value is not None
        ) / total_weight
        result[stable_key] = CurrentMastery(
            stable_key=stable_key,
            display_name=exemplar.display_name,
            status="available",
            value=round(value, 6),
            evidence_count=sum(item.evidence_count for item in available),
            parameter_version=exemplar.parameter_version,
        )
    return result


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
        if occurred_at is not None
        and full_score is not None
        and full_score > 0.0
        and score_awarded is not None
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
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CHINA_TIMEZONE)
    return parsed.astimezone(UTC)


def _optional_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = [
    "CURRENT_MASTERY_PARAMETERS",
    "CurrentMastery",
    "CurrentMasteryCalculator",
    "aggregate_current_mastery",
]
