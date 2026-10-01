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
from question_bank.mastery.model import (
    MasteryModel, MasteryParameters, build_exam_observations, lineage, week_of,
)

_CHINA_TIMEZONE = timezone(timedelta(hours=8))
CURRENT_MASTERY_PARAMETERS = MasteryParameters()


@dataclass(frozen=True, slots=True)
class TrainingEvidence:
    evidence_id: str
    stable_key: str
    occurred_at: datetime
    achieved_points: float
    total_points: float
    part_difficulty: float | None = None
    evidence_weight: float = 1.0
    difficulty_weight: float = 1.0
    item_key: tuple = ()
    activity: tuple = ()
    source_id: str = ""


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
    interval_low: float | None = None
    interval_high: float | None = None
    tier: str = "insufficient"
    observation_count: int = 0
    full_correct_count: int = 0
    recent_trend: str | None = None
    tier_counts: tuple[tuple[str, int], ...] = ()

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
            "interval_low": self.interval_low,
            "interval_high": self.interval_high,
            "tier": self.tier,
            "observation_count": self.observation_count,
            "full_correct_count": self.full_correct_count,
            "recent_trend": self.recent_trend,
            "tier_counts": dict(self.tier_counts),
        }


class CurrentMasteryCalculator:
    """Compute the sole current mastery formula for current core identities."""

    def __init__(
        self,
        db_path: Path,
        resolver: CurrentKnowledgeResolver,
        *,
        parameters: MasteryParameters | None = CURRENT_MASTERY_PARAMETERS,
        clock: Callable[[], datetime] | None = None,
        data_root: Path | None = None,
        semester_mastery: Callable | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else self.db_path.parent.parent
        self.resolver = resolver
        self.parameters = parameters
        self.semester_mastery = semester_mastery
        self.clock = clock or (lambda: datetime.now(UTC))

    def calculate(
        self, profile: Mapping[str, Any], *,
        exclude_training_evidence_ids: frozenset[str] = frozenset(),
        allowed_student_ids: frozenset[str] | None = None,
    ) -> dict[tuple[str, str], CurrentMastery]:
        if self.parameters is None:
            raise ValueError("current mastery parameters are unavailable")
        as_of = self.clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("mastery clock must include a timezone")
        if self.semester_mastery is not None:
            all_values = self.semester_mastery(profile, exclude_training_evidence_ids=exclude_training_evidence_ids,
                                              as_of=as_of, parameters=self.parameters)
            selected = allowed_student_ids if allowed_student_ids is not None else frozenset(str(s["student_id"]) for s in profile.get("students", []))
            return {identity: item for identity, item in all_values.items() if identity[0] in selected}
        observations = self.model_observations(profile, exclude_training_evidence_ids=exclude_training_evidence_ids)
        if not observations:
            return {}
        # Never fit a page's selected students: the supplied snapshot determines
        # the population; allowed_student_ids only filters the result.
        parent = {r.source_key: r.target_key for r in self.resolver.relations if r.relation_type == "parent"}
        from question_bank.recommendation.target_matching import target_index
        kinds = target_index(self.resolver)
        model = MasteryModel(parent, self.parameters, dynamic_nodes={key for key, item in kinds.items() if item.get("kind") in {"topic", "skill"}}).fit(observations)
        counts = defaultdict(list)
        direct = defaultdict(list)
        for observation in observations:
            nodes = set()
            for key in observation["links"]:
                direct[observation["student"], key].append(observation)
                nodes.update(lineage(key, parent))
            for key in nodes:
                counts[observation["student"], key].append(observation)
        # Include prior estimates for this semester's unobserved nodes, without
        # expanding each student to every textbook volume in the catalogue.
        roots = {lineage(key, parent)[-1] for o in observations for key in o["links"]}
        volume_id = (profile.get("exam_scope") or {}).get("curriculum_volume_id")
        if volume_id:
            from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
            for volume in load_curriculum_catalog()["volumes"]:
                if volume["id"] == volume_id:
                    roots = {str(c["knowledge_id"]) for c in volume["chapters"]}
        active_keys = [node.stable_key for node in self.resolver.nodes if lineage(node.stable_key, parent)[-1] in roots]
        for student in {o["student"] for o in observations}:
            for key in active_keys:
                counts.setdefault((student, key), [])
        result = {}
        parameter_version = self.parameters.version
        for (student, key), records in sorted(counts.items()):
            if allowed_student_ids is not None and student not in allowed_student_ids:
                continue
            node = self.resolver.node(key)
            if node is None:
                continue
            activities = defaultdict(list)
            for o in sorted(records, key=lambda o: (o["occurred_at"], str(o["activity"]))):
                activities[o["activity"]].append(o["y"] == 1)
            perfect = [all(v) for v in activities.values()]
            trend = None
            if perfect and not perfect[-1] and any(perfect[:-1]):
                trend = "最近一次出错"
            elif perfect and perfect[-1] and not all(perfect):
                streak = next((i for i, v in enumerate(reversed(perfect)) if not v), len(perfect))
                if streak >= 2:
                    trend = f"最近 {streak} 次全对 ↑"
            exam_activities = {(o["activity"], o.get("qkey", o["item"])) for o in records if o["source"] == "exam"}
            training_activities = {(o["activity"], o["item"][:2]) for o in records if o["source"] == "training"}
            calculated = model.result(student, key, week_of(as_of))
            if not records:
                calculated["tier"] = "insufficient"
            result[student, key] = CurrentMastery(stable_key=key, display_name=node.display_name,
                status="available", evidence_count=len(records), effective_weight=float(len(records)),
                parameter_version=parameter_version, exam_evidence_count=len(exam_activities),
                training_evidence_count=len(training_activities), direct_evidence_count=len(direct.get((student, key), [])),
                precise_training_evidence_count=sum(o["source"] == "training" for o in direct.get((student, key), [])),
                observation_count=len(records), full_correct_count=sum(o["y"] == 1 for o in records),
                recent_trend=trend, **calculated)
        return result

    def model_observations(self, profile, *, exclude_training_evidence_ids=frozenset()):
        """Build the same exam/training observations for fitting and validation."""
        supplied = profile.get("_mastery_observations")
        observations = list(supplied if supplied is not None else self.exam_observations(profile))
        exam_scope = profile.get("exam_scope") or {}
        training = self.training_observations(exclude_evidence_ids=exclude_training_evidence_ids,
            allowed_student_ids=None,
            semester_session_ids=frozenset(int(session) for session in (profile.get("_mastery_session_times") or {})),
            curriculum_volume_id=(str(exam_scope.get("curriculum_volume_id") or "") if exam_scope.get("mode") == "semester" else None))
        grouped = {}
        for (student, key), records in training.items():
            for record in records:
                item = record.item_key or ("training", record.evidence_id)
                activity = record.activity or ("training", record.evidence_id)
                identity = student, item, activity
                observation = grouped.setdefault(identity, dict(student=student, item=item, activity=activity,
                    source="training", occurred_at=record.occurred_at, week=week_of(record.occurred_at),
                    d=record.part_difficulty or 5.5, y=record.achieved_points/record.total_points, links={}))
                observation["links"][key] = observation["links"].get(key, 0.) + record.evidence_weight
        for observation in grouped.values():
            total = sum(observation["links"].values())
            observation["links"] = {key: value/total for key, value in observation["links"].items()}
        observations.extend(grouped.values())
        return observations

    def exam_observations(self, profile):
        rows = {}
        for student in profile.get("students", []):
            for point in student.get("weak_points", []):
                if point.get("hierarchy_kind") == "parent_summary":
                    continue
                key = point.get("knowledge_key") or point.get("knowledge_point")
                for ref in point.get("source_question_refs", []):
                    if ref.get("source_kind") == "training":
                        continue
                    identity = str(student["student_id"]), int(ref.get("session_id") or 0), str(ref.get("question_id") or "")
                    row = rows.setdefault(identity, {**ref, "student_id": identity[0], "question_tags": {"knowledge_point": []}, "point_observations": []})
                    if key not in row["question_tags"]["knowledge_point"]:
                        row["question_tags"]["knowledge_point"].append(key)
                    assessment = ref.get("assessment") or {}
                    for observation in assessment.get("point_observations") or []:
                        if observation not in row["point_observations"]:
                            row["point_observations"].append(observation)
                    if assessment.get("granularity") in {"part", "step"} and not assessment.get("point_observations"):
                        row.setdefault("target_contributions", {})[key] = (ref.get("score_awarded"), ref.get("full_score"))
        return build_exam_observations(rows.values(), self.resolver, _session_times(profile.get("_mastery_session_times")))

    def training_observations(
        self,
        *,
        exclude_evidence_ids: frozenset[str],
        allowed_student_ids: frozenset[str] | None,
        curriculum_volume_id: str | None = None,
        semester_session_ids: frozenset[int] = frozenset(),
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
                sessions_by_draft: dict[str, set[int]] = {}
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
                        sessions_by_draft[draft_id] = {int(session) for session in frozen_scope.get("session_ids", [])}
                    if curriculum_volume_id and (volumes_by_draft[draft_id] == curriculum_volume_id or
                            (not volumes_by_draft[draft_id] and sessions_by_draft[draft_id].intersection(semester_session_ids))):
                        scoped_rows.append(row)
                rows = scoped_rows
            if not rows:
                return {}
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
            connection_criteria = {}
            for row in rows:
                criterion = connection.execute("SELECT criteria_json,criteria_hash FROM training_criterion_versions WHERE version_id=?", (row["criterion_version_id"],)).fetchone()
                if criterion and criterion["criteria_hash"] == row["criterion_hash"]:
                    connection_criteria[str(row["evidence_id"])] = json.loads(criterion["criteria_json"])
                else:
                    # Older frozen papers can retain their criterion even when
                    # the live criterion catalogue no longer has that version.
                    source = source_by_id.get(str(row["evidence_id"]), {})
                    item = connection.execute(
                        "SELECT criterion_version_id,criterion_hash,criterion_snapshot_json FROM personalized_paper_items WHERE paper_instance_id=? AND task_item_code=?",
                        (source.get("paper_instance_id"), row["task_item_code"]),
                    ).fetchone()
                    if item and item["criterion_version_id"] == row["criterion_version_id"] and item["criterion_hash"] == row["criterion_hash"]:
                        snapshot = json.loads(item["criterion_snapshot_json"])
                        if snapshot.get("version_id") == row["criterion_version_id"] and snapshot.get("criteria_hash") == row["criterion_hash"]:
                            connection_criteria[str(row["evidence_id"])] = snapshot.get("criteria", {})
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
                            occurred_at=_parse_datetime(row["occurred_at"]),
                            achieved_points=observation["achieved"], total_points=1,
                            part_difficulty=observation["difficulty"], evidence_weight=observation["weight"],
                            item_key=("training", source_by_id[str(row["evidence_id"])].get("bank_question_id"), str(row["criterion_version_id"]), observation["point_id"]),
                            activity=("training", str(row["submission_id"]), int(row["submission_revision"])), source_id=str(row["evidence_id"]),
                        ))
                continue
            frozen = connection_criteria.get(str(row["evidence_id"]), {})
            states = {str(p["point_id"]): p["state"] for p in json.loads(row["final_points_json"])}
            points = frozen.get("points", [])
            for point in points:
                pid = str(point["point_id"])
                if states.get(pid) not in {"met", "not_met"}:
                    continue
                if states[pid] == "not_met" and any(states.get(dep) != "met" for dep in point.get("depends_on", [])):
                    continue
                for target in self.resolver.resolve(row["stable_key"]):
                    atom = f"{row['evidence_id']}:point:{pid}"
                    identity = student_id, target.stable_key, atom
                    if identity in seen:
                        continue
                    seen.add(identity)
                    result[student_id, target.stable_key].append(TrainingEvidence(
                        evidence_id=atom, stable_key=target.stable_key,
                        occurred_at=_parse_datetime(row["occurred_at"]),
                        achieved_points=int(states[pid] == "met"), total_points=1,
                        item_key=("training", source_by_id[str(row["evidence_id"])].get("bank_question_id"), str(row["criterion_version_id"]), pid),
                        activity=("training", str(row["submission_id"]), int(row["submission_revision"])), source_id=str(row["evidence_id"])))
        return result


def aggregate_current_mastery(values):
    grouped = defaultdict(list)
    for (_student, key), item in values.items():
        if item.value is not None and item.observation_count > 0:
            grouped[key].append(item)
    result = {}
    for key, items in grouped.items():
        exemplar = items[0]
        tiers = {name: sum(i.tier == name for i in items) for name in ("stable", "unsteady", "weak", "insufficient")}
        # A group has a distribution of individual tiers, not a confidence
        # claim inferred from its mean probability.
        tier = max(("weak", "unsteady", "stable", "insufficient"), key=lambda t: tiers[t])
        result[key] = CurrentMastery(stable_key=key, display_name=exemplar.display_name, status="available",
            value=sum(i.value for i in items)/len(items), evidence_count=sum(i.evidence_count for i in items),
            effective_weight=sum(i.effective_weight for i in items), parameter_version=exemplar.parameter_version,
            contributing_student_count=len(items), exam_evidence_count=sum(i.exam_evidence_count for i in items),
            training_evidence_count=sum(i.training_evidence_count for i in items),
            direct_evidence_count=sum(i.direct_evidence_count for i in items),
            observation_count=sum(i.observation_count for i in items), full_correct_count=sum(i.full_correct_count for i in items),
            interval_low=sum(i.interval_low for i in items if i.interval_low is not None)/len(items),
            interval_high=sum(i.interval_high for i in items if i.interval_high is not None)/len(items),
            tier=tier, tier_counts=tuple(tiers.items()))
    return result


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
