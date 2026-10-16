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
from question_bank.question_types import is_training_target, question_type_key, target_kind_for_key
from question_bank.mastery.model import (
    MasteryModel, MasteryParameters, build_exam_observations, lineage, week_of,
)

_CHINA_TIMEZONE = timezone(timedelta(hours=8))
CURRENT_MASTERY_PARAMETERS = MasteryParameters()
TREND_LAST_ERROR = "最近一次出错"
TREND_JUST_RIGHT = "最近做对了"
TREND_STREAK_TEMPLATE = "最近 {streak} 次全对 ↑"
_STREAK_PREFIX, _, _STREAK_SUFFIX = TREND_STREAK_TEMPLATE.partition("{streak}")


def recent_trend_improved(trend: str | None) -> bool:
    if not trend:
        return False
    if trend == TREND_JUST_RIGHT:
        return True
    return (trend.startswith(_STREAK_PREFIX) and trend.endswith(_STREAK_SUFFIX)
            and trend[len(_STREAK_PREFIX):-len(_STREAK_SUFFIX)].isdigit())


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
    display_name: str = ""


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
    logit_mean: float | None = None
    logit_sd: float | None = None
    difficulty_slope: float | None = None

    def __reduce__(self):
        # Positional construction beats the per-field dataclass pickle protocol;
        # this object is serialized tens of thousands of times per snapshot.
        return (CurrentMastery, (
            self.stable_key, self.display_name, self.status, self.value,
            self.evidence_count, self.effective_weight, self.parameter_version,
            self.reason, self.contributing_student_count, self.exam_evidence_count,
            self.training_evidence_count, self.evidence_contributions,
            self.direct_evidence_count, self.precise_training_evidence_count,
            self.interval_low, self.interval_high, self.tier,
            self.observation_count, self.full_correct_count, self.recent_trend,
            self.tier_counts, self.logit_mean, self.logit_sd, self.difficulty_slope,
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
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
            "logit_mean": self.logit_mean,
            "logit_sd": self.logit_sd,
            "difficulty_slope": self.difficulty_slope,
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
        evidence_until: datetime | None = None,
        include_unobserved: bool = True,
    ) -> dict[tuple[str, str], CurrentMastery]:
        if self.parameters is None:
            raise ValueError("current mastery parameters are unavailable")
        as_of = self.clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("mastery clock must include a timezone")
        if self.semester_mastery is not None:
            extra = {"evidence_until": evidence_until} if evidence_until is not None else {}
            if not include_unobserved:
                extra["include_unobserved"] = False
            all_values = self.semester_mastery(profile, exclude_training_evidence_ids=exclude_training_evidence_ids,
                                              as_of=as_of, parameters=self.parameters, **extra)
            selected = allowed_student_ids if allowed_student_ids is not None else frozenset(str(s["student_id"]) for s in profile.get("students", []))
            return {identity: item for identity, item in all_values.items() if identity[0] in selected}
        observations = self.model_observations(profile, exclude_training_evidence_ids=exclude_training_evidence_ids)
        if evidence_until is not None:
            observations = [o for o in observations
                            if not isinstance(o.get("occurred_at"), datetime)
                            or o["occurred_at"] <= evidence_until]
        if not observations:
            return {}
        # Never fit a page's selected students: the supplied snapshot determines
        # the population; allowed_student_ids only filters the result.
        parent = {r.source_key: r.target_key for r in self.resolver.relations if r.relation_type == "parent"}
        paths = {}

        def path_for(key):
            if key not in paths:
                paths[key] = lineage(key, parent)
            return paths[key]

        from question_bank.recommendation.target_matching import target_index
        kinds = target_index(self.resolver)
        model = MasteryModel(parent, self.parameters, dynamic_nodes={key for key, item in kinds.items() if item.get("kind") in {"topic", "skill"}}).fit(observations)
        counts = defaultdict(list)
        direct = defaultdict(list)
        for observation in observations:
            nodes = set()
            for key in observation["links"]:
                direct[observation["student"], key].append(observation)
                nodes.update(path_for(key))
            for key in nodes:
                counts[observation["student"], key].append(observation)
        # Include prior estimates for this semester's unobserved nodes, without
        # expanding each student to every textbook volume in the catalogue.
        if include_unobserved:
            roots = {path_for(key)[-1] for o in observations for key in o["links"]}
            volume_id = (profile.get("exam_scope") or {}).get("curriculum_volume_id")
            if volume_id:
                from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
                for volume in load_curriculum_catalog()["volumes"]:
                    if volume["id"] == volume_id:
                        roots = {str(c["knowledge_id"]) for c in volume["chapters"]}
            active_keys = [node.stable_key for node in self.resolver.nodes if path_for(node.stable_key)[-1] in roots]
            for student in {o["student"] for o in observations}:
                for key in active_keys:
                    counts.setdefault((student, key), [])
        result = {}
        parameter_version = self.parameters.version
        week = week_of(as_of)
        difficulty_slope = model.difficulty_slope
        for (student, key), records in sorted(counts.items()):
            if allowed_student_ids is not None and student not in allowed_student_ids:
                continue
            node = self.resolver.node(key)
            trend = None
            if records:
                activities = defaultdict(list)
                for o in sorted(records, key=lambda o: (o["occurred_at"], str(o["activity"]))):
                    activities[o["activity"]].append(o["y"] == 1)
                perfect = [all(v) for v in activities.values()]
                if perfect and not perfect[-1] and any(perfect[:-1]):
                    trend = TREND_LAST_ERROR
                elif perfect and perfect[-1] and not all(perfect):
                    streak = next((i for i, v in enumerate(reversed(perfect)) if not v), len(perfect))
                    trend = TREND_STREAK_TEMPLATE.format(streak=streak) if streak >= 2 else TREND_JUST_RIGHT
                exam_activities = {(o["activity"], o.get("qkey", o["item"])) for o in records if o["source"] == "exam"}
                training_activities = {(o["activity"], o["item"][:2]) for o in records if o["source"] == "training"}
            else:
                exam_activities = training_activities = ()
            direct_records = direct.get((student, key), ())
            logit_mean, logit_sd = model.mastery(student, key, week)
            calculated = model.result(student, key, week)
            if not records:
                calculated["tier"] = "insufficient"
            result[student, key] = CurrentMastery(stable_key=key, display_name=node.display_name if node is not None else next(
                    (o.get("display_names", {}).get(key) for o in records if o.get("display_names", {}).get(key)), key),
                status="available", evidence_count=len(records), effective_weight=float(len(records)),
                parameter_version=parameter_version, exam_evidence_count=len(exam_activities),
                logit_mean=logit_mean, logit_sd=logit_sd, difficulty_slope=difficulty_slope,
                training_evidence_count=len(training_activities), direct_evidence_count=len(direct_records),
                precise_training_evidence_count=sum(o["source"] == "training" for o in direct_records),
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
                if record.display_name:
                    observation.setdefault("display_names", {})[key] = record.display_name
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
            from question_bank.solution_evidence.knowledge_links import links_from_embedded
            from question_bank.solution_evidence.part_assessments import training_part_observations
            source_by_id = {str(row["evidence_id"]): json.loads(row["source_json"]) for row in rows
                            if "source_json" in row.keys()}
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection_criteria = {}
            refined = {}
            material_by_item = {}
            for row in rows:
                source = source_by_id.get(str(row["evidence_id"]), {})
                material_key = (source.get("paper_instance_id"), row["task_item_code"], row["criterion_version_id"])
                if material_key not in material_by_item:
                    material_by_item[material_key] = _saved_training_material(connection, row, source, tables)
                frozen_criteria, assessment = material_by_item[material_key]
                connection_criteria[str(row["evidence_id"])] = frozen_criteria
                embedded = frozen_criteria.get("solution_evidence") or {}
                observations = training_part_observations(
                    {"parts": assessment.get("parts", [])}, frozen_criteria,
                    json.loads(row["final_points_json"]), links=links_from_embedded(embedded),
                ) if frozen_criteria else None
                if observations is not None:
                    refined[str(row["evidence_id"])] = observations
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
                    for stable_key in _saved_training_target_keys(self.resolver, observation["stable_key"]):
                        observation_id = f"training:{row['submission_id']}:{row['submission_revision']}:{row['task_item_code']}:{observation['part_id']}"
                        atom_id = f"{observation_id}:target:{observation['point_id']}:{stable_key}"
                        identity = (student_id, stable_key, atom_id)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        result[(student_id, stable_key)].append(TrainingEvidence(
                            evidence_id=atom_id, stable_key=stable_key,
                            display_name=observation.get("display_name", ""),
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
                for stable_key in _saved_training_target_keys(self.resolver, str(row["stable_key"])):
                    atom = f"training:{row['submission_id']}:{row['submission_revision']}:{row['task_item_code']}:point:{pid}"
                    identity = student_id, stable_key, atom
                    if identity in seen:
                        continue
                    seen.add(identity)
                    result[student_id, stable_key].append(TrainingEvidence(
                        evidence_id=atom, stable_key=stable_key,
                        display_name=str(source_by_id.get(str(row["evidence_id"]), {}).get("matched_name") or ""),
                        occurred_at=_parse_datetime(row["occurred_at"]),
                        achieved_points=int(states[pid] == "met"), total_points=1,
                        item_key=("training", source_by_id[str(row["evidence_id"])].get("bank_question_id"), str(row["criterion_version_id"]), pid),
                        activity=("training", str(row["submission_id"]), int(row["submission_revision"])), source_id=str(row["evidence_id"])))
        return result


def _saved_training_target_keys(resolver, stable_key: str) -> tuple[str, ...]:
    targets = resolver.resolve(stable_key)
    return tuple(dict.fromkeys(target.stable_key for target in targets)) or (stable_key,)


def _saved_training_material(connection, row, source, tables):
    if source.get("paper_instance_id") and "personalized_paper_items" in tables:
        item = connection.execute(
            "SELECT * FROM personalized_paper_items WHERE paper_instance_id=? AND task_item_code=?",
            (source["paper_instance_id"], row["task_item_code"]),
        ).fetchone()
        if item and item["criterion_version_id"] == row["criterion_version_id"]:
            snapshot = json.loads(item["criterion_snapshot_json"])
            recommendation = json.loads(item["recommendation_snapshot_json"]) if "recommendation_snapshot_json" in item.keys() else {}
            return snapshot.get("criteria", {}), recommendation.get("part_assessment") or {}
    if "training_criterion_versions" in tables:
        criterion = connection.execute(
            "SELECT question_id,criteria_json FROM training_criterion_versions WHERE version_id=?",
            (row["criterion_version_id"],),
        ).fetchone()
        question_id = source.get("bank_question_id")
        if criterion and (not question_id or int(criterion["question_id"]) == int(question_id)):
            return json.loads(criterion["criteria_json"]), {}
    return {}, {}


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
    "recent_trend_improved",
]
