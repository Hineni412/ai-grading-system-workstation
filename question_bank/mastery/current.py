from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Mapping
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
CURRENT_MASTERY_PARAMETERS = MasteryV2Parameters(formula_version="mastery-v2-formula-v2")


@dataclass(frozen=True, slots=True)
class CurrentMastery:
    stable_key: str
    display_name: str
    status: str
    value: float | None
    evidence_count: int
    effective_weight: float
    parameter_version: str
    reason: str | None = None
    contributing_student_count: int = 1
    exam_evidence_count: int = 0
    training_evidence_count: int = 0
    evidence_contributions: tuple[tuple[str, float, float], ...] = ()
    direct_evidence_count: int = 0
    precise_training_evidence_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "value": self.value,
            "evidence_count": self.evidence_count,
            "effective_weight": self.effective_weight,
            "parameter_version": self.parameter_version,
            "reason": self.reason,
            "contributing_student_count": self.contributing_student_count,
            "exam_evidence_count": self.exam_evidence_count,
            "training_evidence_count": self.training_evidence_count,
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
        data_root: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else self.db_path.parent.parent
        self.resolver = resolver
        self.parameters = parameters
        self.clock = clock or (lambda: datetime.now(UTC))

    def calculate(
        self,
        profile: Mapping[str, Any],
        *,
        exclude_training_evidence_ids: frozenset[str] = frozenset(),
        allowed_student_ids: frozenset[str] | None = None,
    ) -> dict[tuple[str, str], CurrentMastery]:
        if self.parameters is None:
            raise ValueError("current mastery parameters are unavailable")
        exam = self._exam_evidence(profile)
        exam_scope = profile.get("exam_scope") or {}
        training = self.training_observations(
            exclude_evidence_ids=exclude_training_evidence_ids,
            allowed_student_ids=allowed_student_ids,
            curriculum_volume_id=(str(exam_scope.get("curriculum_volume_id") or "")
                                  if exam_scope.get("mode") == "semester" else None),
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
                effective_weight=calculated.effective_sample_weight,
                parameter_version=calculated.parameter_version,
                reason=(
                    None
                    if calculated.value is not None
                    else "current_mastery_evidence_missing"
                ),
                exam_evidence_count=sum(
                    1 for item in calculated.contributions
                    if item.included and item.evidence_id.startswith("exam:")
                ),
                training_evidence_count=sum(
                    1 for item in calculated.contributions
                    if item.included and not item.evidence_id.startswith("exam:")
                ),
                evidence_contributions=tuple(
                    (
                        item.evidence_id,
                        float(item.effective_weight),
                        float(item.weighted_value),
                    )
                    for item in calculated.contributions
                    if item.included
                ),
                direct_evidence_count=calculated.direct_evidence_count,
                precise_training_evidence_count=len({
                    item.evidence_id.split(":target:")[0]
                    for item in calculated.contributions
                    if item.included and item.evidence_id.startswith("training:")
                    and ":target:" in item.evidence_id
                    and item.weighted_value < item.effective_weight
                }),
            )
        return self._with_parent_rollups(result)

    def _with_parent_rollups(
        self,
        direct: dict[tuple[str, str], CurrentMastery],
    ) -> dict[tuple[str, str], CurrentMastery]:
        """Roll every stable evidence identity into each governed parent once."""

        parent_by_child = {
            relation.source_key: relation.target_key
            for relation in self.resolver.relations
            if relation.relation_type == "parent"
        }
        node_by_key = {node.stable_key: node for node in self.resolver.nodes}
        buckets: dict[
            tuple[str, str], dict[str, tuple[float, float]]
        ] = defaultdict(dict)
        for (student_id, stable_key), item in direct.items():
            lineage: list[str] = [stable_key]
            seen = {stable_key}
            parent = parent_by_child.get(stable_key)
            while parent and parent not in seen:
                lineage.append(parent)
                seen.add(parent)
                parent = parent_by_child.get(parent)
            for target_key in lineage:
                bucket = buckets[(student_id, target_key)]
                for evidence_id, weight, weighted_value in item.evidence_contributions:
                    bucket.setdefault(evidence_id, (weight, weighted_value))

        result = dict(direct)
        for identity, evidence in buckets.items():
            student_id, stable_key = identity
            node = node_by_key.get(stable_key)
            if node is None or not evidence:
                continue
            effective_weight = sum(item[0] for item in evidence.values())
            if effective_weight <= 0:
                continue
            numerator = (
                self.parameters.prior_mean * self.parameters.prior_strength
                + sum(item[1] for item in evidence.values())
            )
            denominator = self.parameters.prior_strength + effective_weight
            result[(student_id, stable_key)] = CurrentMastery(
                stable_key=stable_key,
                display_name=node.display_name,
                status="available",
                value=round(min(1.0, max(0.0, numerator / denominator)), 6),
                evidence_count=len({key.split(":target:")[0] for key in evidence}),
                effective_weight=round(effective_weight, 6),
                parameter_version=self.parameters.version,
                exam_evidence_count=sum(
                    1 for evidence_id in {key.split(":target:")[0] for key in evidence}
                    if evidence_id.startswith("exam:")
                ),
                training_evidence_count=sum(
                    1 for evidence_id in {key.split(":target:")[0] for key in evidence}
                    if not evidence_id.startswith("exam:")
                ),
                evidence_contributions=tuple(
                    (evidence_id, values[0], values[1])
                    for evidence_id, values in sorted(evidence.items())
                ),
                direct_evidence_count=(
                    direct.get((student_id, stable_key)).evidence_count
                    if (student_id, stable_key) in direct else 0
                ),
                precise_training_evidence_count=(
                    direct[(student_id, stable_key)].precise_training_evidence_count
                    if (student_id, stable_key) in direct else 0
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

    def training_observations(
        self,
        *,
        exclude_evidence_ids: frozenset[str],
        allowed_student_ids: frozenset[str] | None,
        curriculum_volume_id: str | None = None,
    ) -> dict[tuple[str, str], list[TrainingEvidence]]:
        if not self.db_path.is_file():
            raise sqlite3.OperationalError("question bank database is missing")
        uri = self.db_path.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT *
                FROM training_evidence_records
                WHERE status = 'active'
                ORDER BY student_id, stable_key, occurred_at, evidence_id
                """
            ).fetchall()
            rows = [row for row in rows if str(row["evidence_id"]) not in exclude_evidence_ids
                    and (allowed_student_ids is None or str(row["student_id"]) in allowed_student_ids)]
            if curriculum_volume_id is not None:
                # Semester belongs to the frozen training request, not to the
                # day the teacher finally publishes the marking result.
                scoped_rows = []
                volumes_by_draft: dict[str, str] = {}
                for row in rows:
                    source = json.loads(row["source_json"])
                    draft_id = str(source.get("draft_id") or "")
                    if draft_id not in volumes_by_draft:
                        draft = connection.execute(
                            "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
                            (draft_id,),
                        ).fetchone() if draft_id else None
                        request = json.loads(draft["request_json"]) if draft else {}
                        frozen_scope = request.get("diagnosis", {}).get("exam_scope", {})
                        volumes_by_draft[draft_id] = str(
                            frozen_scope.get("curriculum_volume_id")
                            or request.get("config", {}).get("curriculum_volume_id") or "")
                    if curriculum_volume_id and volumes_by_draft[draft_id] == curriculum_volume_id:
                        scoped_rows.append(row)
                rows = scoped_rows
            from question_bank.solution_evidence.knowledge_links import load_point_links
            from question_bank.solution_evidence.part_assessments import (
                load_profiles,
                training_part_observations,
            )
            source_by_id = {str(row["evidence_id"]): json.loads(row["source_json"]) for row in rows
                            if "source_json" in row.keys()}
            profiles = load_profiles(self.db_path, sorted({int(source["bank_question_id"]) for source in source_by_id.values() if source.get("bank_question_id")}), connection=connection, data_root=self.data_root)
            point_links = load_point_links(
                self.db_path,
                [str(profile["evidence_version_id"]) for profile in profiles.values() if profile.get("evidence_version_id")],
                None,
                connection=connection,
            )
            refined = {}
            for row in rows:
                profile = profiles.get(int(source_by_id.get(str(row["evidence_id"]), {}).get("bank_question_id") or 0))
                if profile is None:
                    continue
                criterion = connection.execute("SELECT criteria_json,criteria_hash FROM training_criterion_versions WHERE version_id=? AND question_id=?", (row["criterion_version_id"], profile["question_id"])).fetchone()
                refined[str(row["evidence_id"])] = training_part_observations(profile, json.loads(criterion["criteria_json"]), json.loads(row["final_points_json"]), links=point_links.get(str(profile["evidence_version_id"]), {})) if criterion and criterion["criteria_hash"] == row["criterion_hash"] else []
        finally:
            connection.close()
        result: dict[tuple[str, str], list[TrainingEvidence]] = defaultdict(list)
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            if str(row["evidence_id"]) in exclude_evidence_ids:
                continue
            student_id = str(row["student_id"])
            if allowed_student_ids is not None and student_id not in allowed_student_ids:
                continue
            if str(row["evidence_id"]) in refined:
                for observation in refined[str(row["evidence_id"])] or []:
                    for target in self.resolver.resolve(observation["stable_key"]):
                        observation_id = f"training:{row['submission_id']}:{row['submission_revision']}:{row['task_item_code']}:{observation['part_id']}"
                        atom_id = f"{observation_id}:target:{observation['point_id']}:{target.stable_key}"
                        identity = (student_id, target.stable_key, atom_id)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        result[(student_id, target.stable_key)].append(TrainingEvidence(
                            evidence_id=atom_id, stable_key=target.stable_key,
                            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
                            achieved_points=observation["achieved"], total_points=1,
                            part_difficulty=observation["difficulty"], evidence_weight=observation["weight"],
                        ))
                continue
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
            result[stable_key] = CurrentMastery(
                stable_key=stable_key,
                display_name=exemplar.display_name,
                status=exemplar.status,
                value=None,
                evidence_count=0,
                effective_weight=0.0,
                parameter_version=exemplar.parameter_version,
                contributing_student_count=0,
                exam_evidence_count=0,
                training_evidence_count=0,
                evidence_contributions=(),
                direct_evidence_count=0,
            )
            continue
        total_weight = sum(max(item.effective_weight, 0.0) for item in available)
        if total_weight <= 0:
            total_weight = float(len(available))
        value = sum(
            float(item.value) * (
                max(item.effective_weight, 0.0)
                if any(candidate.effective_weight > 0 for candidate in available)
                else 1.0
            )
            for item in available
            if item.value is not None
        ) / total_weight
        result[stable_key] = CurrentMastery(
            stable_key=stable_key,
            display_name=exemplar.display_name,
            status="available",
            value=round(value, 6),
            evidence_count=sum(item.evidence_count for item in available),
            effective_weight=round(sum(item.effective_weight for item in available), 6),
            parameter_version=exemplar.parameter_version,
            contributing_student_count=len(available),
            exam_evidence_count=sum(
                item.exam_evidence_count for item in available
            ),
            training_evidence_count=sum(
                item.training_evidence_count for item in available
            ),
            evidence_contributions=(),
            direct_evidence_count=sum(item.direct_evidence_count for item in available),
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
    assessment = reference.get("assessment") or {}
    observations = assessment.get("point_observations")
    if isinstance(observations, list):
        weight = sum(float(item["weight"]) for item in observations)
        score_awarded = sum(float(item["achieved"]) * float(item["weight"]) for item in observations)
        full_score = weight
        assessment = {**assessment, "evidence_weight": weight}
    status = (
        EvidenceStatus.COMPLETED
        if occurred_at is not None
        and full_score is not None
        and full_score > 0.0
        and score_awarded is not None
        and assessment.get("eligible") is not False
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
            + (f":target:{stable_key}" if assessment.get("granularity") in {"part", "step"} else "")
        ),
        stable_key=stable_key,
        occurred_at=occurred_at,
        score_awarded=score_awarded,
        full_score=full_score,
        status=status,
        part_difficulty=_optional_number(assessment.get("part_difficulty")),
        evidence_weight=float(assessment.get("evidence_weight", 1.0)),
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
