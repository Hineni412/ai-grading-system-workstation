from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.database.schema import connect
from question_bank.mastery.current import (
    CURRENT_MASTERY_PARAMETERS,
    CurrentMasteryCalculator,
)
from question_bank.training_criteria import (
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
    usable_training_criterion,
)


ENGINE_VERSION = "personalized-recommendation-v1"
TIME_ESTIMATE_VERSION = "question-type-minutes-v1"
RECENT_WINDOW_DAYS = 90
Stage = Literal["direct", "prerequisite", "transfer"]
Action = Literal["lock", "unlock", "exclude", "replace"]


class PersonalizedRecommendationError(RuntimeError):
    pass


class RecommendationRequestConflict(PersonalizedRecommendationError):
    pass


class RecommendationDraftNotFound(PersonalizedRecommendationError):
    pass


class RecommendationRevisionConflict(PersonalizedRecommendationError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__("recommendation draft revision is stale")


class RecommendationSourceChanged(PersonalizedRecommendationError):
    pass


class RecommendationEditInvalid(PersonalizedRecommendationError):
    pass


@dataclass(frozen=True, slots=True)
class PersonalizedRecommendationConfig:
    paper_mode: Literal["individual", "shared"] = "individual"
    question_count: int = 10
    expected_minutes: int = 45
    difficulty_min: int = 1
    difficulty_max: int = 10
    direct_ratio: float = 0.6
    prerequisite_ratio: float = 0.3
    transfer_ratio: float = 0.1
    target_keys: tuple[str, ...] = ()
    scope_keys: tuple[str, ...] = ()
    exclude_current_exam_originals: bool = True

    def __post_init__(self) -> None:
        if self.paper_mode not in {"individual", "shared"}:
            raise ValueError("paper_mode is invalid")
        if not 8 <= int(self.question_count) <= 12:
            raise ValueError("question_count must be between 8 and 12")
        if not 10 <= int(self.expected_minutes) <= 180:
            raise ValueError("expected_minutes must be between 10 and 180")
        if (
            not 1 <= int(self.difficulty_min) <= 10
            or not 1 <= int(self.difficulty_max) <= 10
            or int(self.difficulty_min) > int(self.difficulty_max)
        ):
            raise ValueError("difficulty range is invalid")
        ratios = (
            float(self.direct_ratio),
            float(self.prerequisite_ratio),
            float(self.transfer_ratio),
        )
        if any(not math.isfinite(value) or value < 0.0 for value in ratios):
            raise ValueError("stage ratios must be nonnegative")
        if abs(sum(ratios) - 1.0) > 1e-9:
            raise ValueError("stage ratios must sum to one")
        normalized_targets = _identity_keys(self.target_keys, field="target_keys")
        normalized_scope = _identity_keys(self.scope_keys, field="scope_keys")
        object.__setattr__(self, "question_count", int(self.question_count))
        object.__setattr__(
            self, "expected_minutes", int(self.expected_minutes)
        )
        object.__setattr__(self, "difficulty_min", int(self.difficulty_min))
        object.__setattr__(self, "difficulty_max", int(self.difficulty_max))
        object.__setattr__(self, "target_keys", normalized_targets)
        object.__setattr__(self, "scope_keys", normalized_scope)
        if (
            self.paper_mode == "shared"
            and not normalized_targets
            and not normalized_scope
        ):
            raise ValueError(
                "shared paper mode requires teacher-selected targets"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "target_keys": list(self.target_keys),
            "scope_keys": list(self.scope_keys),
            "stage_ratios": {
                "direct": self.direct_ratio,
                "prerequisite": self.prerequisite_ratio,
                "transfer": self.transfer_ratio,
            },
            "time_estimate_version": TIME_ESTIMATE_VERSION,
            "recent_window_days": RECENT_WINDOW_DAYS,
        }


@dataclass(frozen=True, slots=True)
class RecommendationEditCommand:
    request_token: str
    expected_revision: int
    action: Action
    student_id: str
    item_id: str
    actor_ref: str
    reason: str
    replacement_question_id: int | None = None

    def __post_init__(self) -> None:
        _request_token(self.request_token)
        if int(self.expected_revision) < 1:
            raise ValueError("expected_revision must be positive")
        if self.action not in {"lock", "unlock", "exclude", "replace"}:
            raise ValueError("action is invalid")
        _required_text(self.student_id, "student_id")
        _required_text(self.item_id, "item_id")
        _required_text(self.actor_ref, "actor_ref")
        _required_text(self.reason, "reason")
        if (
            self.replacement_question_id is not None
            and int(self.replacement_question_id) <= 0
        ):
            raise ValueError("replacement_question_id must be positive")


class PersonalizedRecommendationModule:
    """Own deterministic recommendation, persistence and teacher adjustments."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.clock = clock or (lambda: datetime.now(UTC))
        try:
            self.current_knowledge = (
                CurrentKnowledgeResolver.from_active_database(self.db_path)
            )
        except CurrentKnowledgeUnavailable as exc:
            raise PersonalizedRecommendationError(
                "current knowledge standard is unavailable"
            ) from exc

    def create(
        self,
        *,
        request_token: str,
        diagnosis: Mapping[str, Any],
        config: PersonalizedRecommendationConfig,
        actor_ref: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        actor = _required_text(actor_ref, "actor_ref")
        normalized_diagnosis = _normalize_diagnosis(diagnosis)
        request = {
            "diagnosis": normalized_diagnosis,
            "config": config.to_dict(),
        }
        input_fingerprint = _hash_payload(request)
        existing = self._by_request_token(token)
        if existing is not None:
            existing_fingerprint = str(existing.pop("_input_fingerprint"))
            if existing_fingerprint != input_fingerprint:
                raise RecommendationRequestConflict(
                    "recommendation request token was reused"
                )
            return existing

        candidates, relations, base_source_version = self._source_snapshot()
        mastery = self._mastery_snapshot(normalized_diagnosis)
        recent = self._recent_question_ids(
            tuple(
                str(item["student_id"])
                for item in normalized_diagnosis["students"]
            )
        )
        excluded = (
            self._current_exam_question_ids(normalized_diagnosis)
            if config.exclude_current_exam_originals
            else set()
        )
        source_version = _context_source_version(
            base_source_version,
            as_of=_day_clock(self.clock()),
            recent=recent,
            excluded_question_ids=excluded,
        )
        draft = self._build_draft(
            diagnosis=normalized_diagnosis,
            config=config,
            candidates=candidates,
            relations=relations,
            mastery=mastery,
            recent=recent,
            excluded_question_ids=excluded,
        )
        result_version = _hash_payload(
            {"source_version": source_version, "draft": draft}
        )
        draft_id = _hash_payload(
            {
                "request_token": token,
                "input_fingerprint": input_fingerprint,
            }
        )
        stored = {
            "draft_id": draft_id,
            "status": "draft",
            "revision": 1,
            "result_version": result_version,
            "engine_version": ENGINE_VERSION,
            "source_version": source_version,
            **draft,
            "history": [
                {
                    "action": "created",
                    "actor_ref": actor,
                    "reason": "教师生成个性化推荐草稿",
                    "revision": 1,
                }
            ],
        }
        encoded_request = _json(request)
        encoded_draft = _json(stored)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_fingerprint, draft_json
                FROM personalized_recommendation_drafts
                WHERE request_token = ?
                """,
                (token,),
            ).fetchone()
            if row is not None:
                if str(row["input_fingerprint"]) != input_fingerprint:
                    raise RecommendationRequestConflict(
                        "recommendation request token was reused"
                    )
                return json.loads(str(row["draft_json"]))
            connection.execute(
                """
                INSERT INTO personalized_recommendation_drafts (
                    draft_id, request_token, input_fingerprint,
                    result_version, engine_version, source_version,
                    request_json, draft_json, status, revision, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?)
                """,
                (
                    draft_id,
                    token,
                    input_fingerprint,
                    result_version,
                    ENGINE_VERSION,
                    source_version,
                    encoded_request,
                    encoded_draft,
                    actor,
                ),
            )
            connection.execute(
                """
                INSERT INTO personalized_recommendation_events (
                    draft_id, request_token, command_hash, action,
                    actor_ref, reason, expected_revision,
                    resulting_revision, before_json, after_json,
                    resulting_draft_json
                ) VALUES (?, ?, ?, 'created', ?, ?, 0, 1, '{}', ?, ?)
                """,
                (
                    draft_id,
                    token,
                    _hash_payload(request),
                    actor,
                    "教师生成个性化推荐草稿",
                    encoded_draft,
                    encoded_draft,
                ),
            )
        return stored

    def resolve_target_names(
        self, target_names: Sequence[str]
    ) -> tuple[str, ...]:
        names = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in target_names
                if str(value or "").strip()
            )
        )
        if not names:
            return ()
        result: list[str] = []
        for name in names:
            matches = self.current_knowledge.resolve(name)
            if not matches:
                raise ValueError(
                    f"training target is not uniquely governed: {name}"
                )
            for match in matches:
                if match.stable_key not in result:
                    result.append(match.stable_key)
        return tuple(result)

    def get(self, draft_id: str) -> dict[str, Any]:
        clean_id = _draft_id(draft_id)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT draft_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None:
            raise RecommendationDraftNotFound(clean_id)
        return json.loads(str(row["draft_json"]))

    def ensure_current(self, draft_id: str) -> dict[str, Any]:
        """Return a draft only when every recommendation source is unchanged."""

        clean_id = _draft_id(draft_id)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT request_json, source_version, draft_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None:
            raise RecommendationDraftNotFound(clean_id)
        request = json.loads(str(row["request_json"]))
        _candidates, current_source = self._current_source_for_request(
            request,
            draft_id=clean_id,
        )
        if current_source != str(row["source_version"]):
            raise RecommendationSourceChanged(
                "recommendation sources changed"
            )
        return json.loads(str(row["draft_json"]))

    def edit(
        self,
        draft_id: str,
        command: RecommendationEditCommand,
    ) -> dict[str, Any]:
        clean_id = _draft_id(draft_id)
        command_payload = {
            **asdict(command),
            "replacement_question_id": command.replacement_question_id,
        }
        command_hash = _hash_payload(command_payload)
        with connect(self.db_path) as connection:
            repeated = connection.execute(
                """
                SELECT command_hash, resulting_draft_json
                FROM personalized_recommendation_events
                WHERE draft_id = ? AND request_token = ?
                """,
                (clean_id, command.request_token),
            ).fetchone()
        if repeated is not None:
            if str(repeated["command_hash"]) != command_hash:
                raise RecommendationRequestConflict(
                    "recommendation edit token was reused"
                )
            return json.loads(str(repeated["resulting_draft_json"]))

        with connect(self.db_path) as connection:
            source_row = connection.execute(
                """
                SELECT request_json
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if source_row is None:
            raise RecommendationDraftNotFound(clean_id)
        source_request = json.loads(str(source_row["request_json"]))
        if (
            source_request.get("config", {}).get("paper_mode") == "shared"
        ):
            raise RecommendationEditInvalid(
                "shared paper questions cannot be edited per student"
            )
        source_candidates, current_source = (
            self._current_source_for_request(
                source_request,
                draft_id=clean_id,
            )
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = connection.execute(
                """
                SELECT command_hash, resulting_draft_json
                FROM personalized_recommendation_events
                WHERE draft_id = ? AND request_token = ?
                """,
                (clean_id, command.request_token),
            ).fetchone()
            if repeated is not None:
                if str(repeated["command_hash"]) != command_hash:
                    raise RecommendationRequestConflict(
                        "recommendation edit token was reused"
                    )
                return json.loads(str(repeated["resulting_draft_json"]))

            row = connection.execute(
                """
                SELECT *
                FROM personalized_recommendation_drafts
                WHERE draft_id = ?
                """,
                (clean_id,),
            ).fetchone()
            if row is None:
                raise RecommendationDraftNotFound(clean_id)
            current_revision = int(row["revision"])
            if int(command.expected_revision) != current_revision:
                raise RecommendationRevisionConflict(
                    command.expected_revision,
                    current_revision,
                )
            if str(row["status"]) != "draft":
                raise RecommendationEditInvalid(
                    "reviewed recommendation cannot be changed"
                )
            if current_source != str(row["source_version"]):
                raise RecommendationSourceChanged(
                    "recommendation sources changed"
                )

            request = json.loads(str(row["request_json"]))
            draft = json.loads(str(row["draft_json"]))
            before, after = self._apply_edit(
                draft,
                draft_id=clean_id,
                request=request,
                command=command,
                candidates=source_candidates,
            )
            next_revision = current_revision + 1
            draft["revision"] = next_revision
            draft["history"].append(
                {
                    "action": _past_action(command.action),
                    "actor_ref": command.actor_ref.strip(),
                    "reason": command.reason.strip(),
                    "student_id": command.student_id.strip(),
                    "item_id": command.item_id.strip(),
                    "revision": next_revision,
                    "before_question_id": before.get("question_id"),
                    "after_question_id": after.get("question_id"),
                }
            )
            result_version = _hash_payload(
                {
                    key: value
                    for key, value in draft.items()
                    if key not in {"history", "revision", "result_version"}
                }
            )
            draft["result_version"] = result_version
            encoded = _json(draft)
            cursor = connection.execute(
                """
                UPDATE personalized_recommendation_drafts
                SET draft_json = ?,
                    result_version = ?,
                    revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE draft_id = ? AND revision = ?
                """,
                (
                    encoded,
                    result_version,
                    next_revision,
                    clean_id,
                    current_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RecommendationRevisionConflict(
                    command.expected_revision,
                    current_revision + 1,
                )
            connection.execute(
                """
                INSERT INTO personalized_recommendation_events (
                    draft_id, request_token, command_hash, action,
                    actor_ref, reason, expected_revision,
                    resulting_revision, before_json, after_json,
                    resulting_draft_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_id,
                    command.request_token,
                    command_hash,
                    _past_action(command.action),
                    command.actor_ref.strip(),
                    command.reason.strip(),
                    current_revision,
                    next_revision,
                    _json(before),
                    _json(after),
                    encoded,
                ),
            )
        return draft

    def _current_source_for_request(
        self,
        request: Mapping[str, Any],
        *,
        draft_id: str | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], str]:
        diagnosis = request["diagnosis"]
        config = request["config"]
        student_ids = tuple(
            str(item["student_id"])
            for item in diagnosis["students"]
        )
        candidates, _relations, base_source_version = self._source_snapshot()
        recent = self._recent_question_ids(
            student_ids,
            exclude_draft_id=draft_id,
        )
        excluded = (
            self._current_exam_question_ids(diagnosis)
            if bool(config.get("exclude_current_exam_originals", True))
            else set()
        )
        return (
            candidates,
            _context_source_version(
                base_source_version,
                as_of=_day_clock(self.clock()),
                recent=recent,
                excluded_question_ids=excluded,
            ),
        )

    def _build_draft(
        self,
        *,
        diagnosis: dict[str, Any],
        config: PersonalizedRecommendationConfig,
        candidates: tuple[dict[str, Any], ...],
        relations: tuple[dict[str, Any], ...],
        mastery: dict[tuple[str, str], dict[str, Any]],
        recent: dict[str, set[int]],
        excluded_question_ids: set[int],
    ) -> dict[str, Any]:
        stage_counts = _stage_counts(config)
        relation_index = _relation_index(relations)
        students: list[dict[str, Any]] = []
        shared_selected: dict[Stage, list[dict[str, Any]]] = {}
        shared_shortages: dict[Stage, tuple[int, str]] = {}
        shared_recent = set().union(*recent.values()) if recent else set()
        scope_leaves = _scope_leaves(
            config.scope_keys,
            diagnosis=diagnosis,
            relations=relations,
        )
        student_ids = tuple(
            str(item["student_id"]) for item in diagnosis["students"]
        )
        shared_explicit = config.target_keys
        if (
            config.paper_mode == "shared"
            and not shared_explicit
            and scope_leaves
        ):
            shared_explicit = _ranked_group_leaf_keys(
                student_ids=student_ids,
                leaves=scope_leaves,
                mastery=mastery,
                limit=config.question_count,
            )
        for profile in diagnosis["students"]:
            student_id = str(profile["student_id"])
            personalized = (
                config.paper_mode == "individual" and bool(config.scope_keys)
            )
            explicit = (
                _ranked_leaf_keys(
                    student_id=student_id,
                    leaves=scope_leaves,
                    mastery=mastery,
                    limit=config.question_count,
                )
                if personalized
                else shared_explicit
            )
            targets = _student_targets(
                profile,
                explicit=explicit,
                mastery=mastery,
                allow_implicit=not personalized and not shared_explicit,
            )
            maintenance = not targets
            fill_conservative = maintenance and not personalized
            stage_targets = _stage_targets(targets, relation_index)
            used: set[int] = set()
            elapsed = 0
            items: list[dict[str, Any]] = []
            shortages: list[dict[str, Any]] = []
            warnings: list[str] = []
            skip_candidate_search = maintenance and not fill_conservative
            if maintenance and fill_conservative:
                warnings.append(
                    "当前范围没有可确认的薄弱证据，以下内容是保守复习，不代表系统判断出新的薄弱点。"
                )
            elif maintenance:
                warnings.append(
                    "当前范围内没有该生可确认的掌握证据，未编造薄弱点。"
                )

            for stage in ("direct", "prerequisite", "transfer"):
                requested = stage_counts[stage]
                eligible: list[dict[str, Any]] = []
                if skip_candidate_search:
                    selected: list[dict[str, Any]] = []
                elif config.paper_mode == "shared" and stage in shared_selected:
                    selected = shared_selected[stage]
                    for candidate in selected:
                        used.add(int(candidate["question_id"]))
                        elapsed += int(candidate["estimated_minutes"])
                else:
                    eligible = self._eligible_candidates(
                        candidates,
                        stage=stage,
                        target_keys=stage_targets[stage],
                        maintenance=fill_conservative,
                        used=used,
                        recent=(
                            shared_recent
                            if config.paper_mode == "shared"
                            else recent.get(student_id, set())
                        ),
                        excluded=excluded_question_ids,
                        config=config,
                    )
                    selected = []
                    for candidate in eligible:
                        minutes = int(candidate["estimated_minutes"])
                        if elapsed + minutes > config.expected_minutes:
                            continue
                        selected.append(candidate)
                        used.add(int(candidate["question_id"]))
                        elapsed += minutes
                        if len(selected) >= requested:
                            break
                    if config.paper_mode == "shared":
                        shared_selected[stage] = selected
                for slot, candidate in enumerate(selected, start=1):
                    matched_key = _matched_key(
                        candidate["stable_keys"],
                        stage_targets[stage],
                    )
                    target = _target_for_match(
                        matched_key,
                        targets,
                        stage=stage,
                        relation_index=relation_index,
                    )
                    items.append(
                        _draft_item(
                            candidate,
                            stage=stage,
                            slot=slot,
                            student_id=student_id,
                            target=target,
                            matched_key=matched_key,
                            maintenance=maintenance,
                        )
                    )
                missing = requested - len(selected)
                if missing and not skip_candidate_search:
                    if config.paper_mode == "shared" and stage in shared_shortages:
                        missing, code = shared_shortages[stage]
                    else:
                        code = (
                            "time_limit_reached"
                            if any(
                                elapsed + int(item["estimated_minutes"])
                                > config.expected_minutes
                                for item in eligible
                                if int(item["question_id"]) not in used
                            )
                            else "approved_candidate_shortage"
                        )
                        if config.paper_mode == "shared":
                            shared_shortages[stage] = (missing, code)
                    shortages.append(
                        {
                            "stage": stage,
                            "requested_count": requested,
                            "selected_count": len(selected),
                            "missing_count": missing,
                            "reason_code": code,
                        }
                    )
                    warnings.append(_shortage_message(stage, missing, code))
            for order, item in enumerate(items, start=1):
                item["item_order"] = order
            students.append(
                {
                    "student_id": student_id,
                    "student_code": str(profile.get("student_code") or ""),
                    "student_name": str(profile.get("student_name") or ""),
                    "class_id": str(profile.get("class_id") or ""),
                    "selection_mode": (
                        "maintenance_fallback"
                        if maintenance
                        else "mastery_targeted"
                    ),
                    "targets": targets,
                    "items": items,
                    "shortages": shortages,
                    "warnings": list(dict.fromkeys(warnings)),
                    "estimated_minutes": elapsed,
                }
            )
        return {
            "config": config.to_dict(),
            "students": students,
            "warnings": list(
                dict.fromkeys(
                    warning
                    for student in students
                    for warning in student["warnings"]
                )
            ),
        }

    def _eligible_candidates(
        self,
        candidates: Sequence[dict[str, Any]],
        *,
        stage: Stage,
        target_keys: tuple[str, ...],
        maintenance: bool,
        used: set[int],
        recent: set[int],
        excluded: set[int],
        config: PersonalizedRecommendationConfig,
    ) -> list[dict[str, Any]]:
        result = []
        for candidate in candidates:
            question_id = int(candidate["question_id"])
            difficulty = candidate["difficulty"]
            if (
                question_id in used
                or question_id in recent
                or question_id in excluded
                or difficulty is None
                or not config.difficulty_min
                <= int(difficulty)
                <= config.difficulty_max
            ):
                continue
            if not maintenance and not set(candidate["stable_keys"]).intersection(
                target_keys
            ):
                continue
            result.append(candidate)
        center = (config.difficulty_min + config.difficulty_max) / 2
        result.sort(
            key=lambda item: (
                (
                    0
                    if maintenance
                    else _target_rank(item["stable_keys"], target_keys)
                ),
                (
                    0.0
                    if maintenance
                    else abs(float(item["difficulty"]) - center)
                ),
                0 if maintenance else int(item["estimated_minutes"]),
                int(item["question_id"]),
            )
        )
        return result

    def _source_snapshot(
        self,
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        str,
    ]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT q.id, q.question_number, q.question_text,
                       q.question_type, q.difficulty, q.updated_at,
                       p.title AS paper_title
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.id
                """
            ).fetchall()
            knowledge_rows = connection.execute(
                """
                SELECT qt.question_id, qt.tag_value
                FROM question_tags qt
                WHERE qt.tag_type IN (
                    'knowledge_point', 'canonical_knowledge_id'
                )
                  AND TRIM(COALESCE(qt.tag_value, '')) <> ''
                ORDER BY qt.question_id, qt.id
                """
            ).fetchall()
        stable_by_question: dict[int, list[dict[str, str]]] = {}
        for row in knowledge_rows:
            bucket = stable_by_question.setdefault(int(row["question_id"]), [])
            for resolved in self.current_knowledge.resolve(row["tag_value"]):
                item = {
                    "stable_key": resolved.stable_key,
                    "display_name": resolved.display_name,
                }
                if item not in bucket:
                    bucket.append(item)
        loader = QuestionAnalysisInputLoader(
            db_path=self.db_path,
            data_root=self.data_root,
        )
        criteria = TrainingCriterionModule(self.db_path)
        candidates: list[dict[str, Any]] = []
        for row in rows:
            question_id = int(row["id"])
            identities = stable_by_question.get(question_id, [])
            if not identities:
                continue
            try:
                question = loader.load((question_id,))[0]
                workspace = criteria.read(question)
            except (KeyError, OSError, ValueError):
                continue
            usable = usable_training_criterion(workspace)
            if usable is None:
                continue
            difficulty = _difficulty(row["difficulty"])
            criterion = usable.get("criteria")
            points = (
                criterion.get("points")
                if isinstance(criterion, Mapping)
                else None
            )
            candidates.append(
                {
                    "question_id": question_id,
                    "question_number": str(
                        row["question_number"] or question_id
                    ),
                    "question_type": str(row["question_type"] or ""),
                    "question_text": str(row["question_text"] or ""),
                    "source_paper": str(row["paper_title"] or ""),
                    "difficulty": difficulty,
                    "estimated_minutes": _estimated_minutes(
                        str(row["question_type"] or ""),
                        str(row["question_text"] or ""),
                    ),
                    "stable_keys": [
                        item["stable_key"] for item in identities
                    ],
                    "stable_names": {
                        item["stable_key"]: item["display_name"]
                        for item in identities
                    },
                    "criterion_version_id": str(usable["version_id"]),
                    "criterion_point_count": (
                        len(points) if isinstance(points, list) else 0
                    ),
                    "question_revision": str(row["updated_at"] or ""),
                }
            )
        relations = tuple(
            {
                "relation_id": relation.relation_key,
                "relation_key": relation.relation_key,
                "source_key": relation.source_key,
                "target_key": relation.target_key,
                "relation_type": relation.relation_type,
                "rationale": relation.rationale,
                "basis_kind": relation.basis_kind,
                "strength": relation.strength,
                "evidence_source_ids": list(
                    relation.evidence_source_ids
                ),
                "source_locator": relation.source_locator,
                "revision": 1,
            }
            for relation in self.current_knowledge.relations
        )
        normalized_candidates = tuple(
            sorted(candidates, key=lambda item: int(item["question_id"]))
        )
        source_version = _hash_payload(
            {
                "engine_version": ENGINE_VERSION,
                "time_estimate_version": TIME_ESTIMATE_VERSION,
                "candidates": normalized_candidates,
                "relations": relations,
                "current_mastery": self._current_mastery_version(),
            }
        )
        return normalized_candidates, relations, source_version

    def _mastery_snapshot(
        self,
        diagnosis: dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        calculated = CurrentMasteryCalculator(
            self.db_path,
            self.current_knowledge,
            clock=self.clock,
        ).calculate(diagnosis)
        snapshot = {
            identity: {
                "stable_key": item.stable_key,
                "display_name": item.display_name,
                "mode": "current",
                "status": item.status,
                "value": item.value,
                "evidence_count": item.evidence_count,
                "parameter_version": item.parameter_version,
                "source_question_refs": [],
                "explanations": (
                    [] if item.reason is None else [item.reason]
                ),
            }
            for identity, item in calculated.items()
        }
        _apply_diagnosis_mastery(
            snapshot,
            diagnosis,
            resolver=self.current_knowledge,
        )
        return snapshot

    def _current_mastery_version(self) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            evidence_rows = [
                (
                    str(row["evidence_id"]),
                    str(row["payload_hash"]),
                    str(row["status"]),
                    int(row["source_review_revision"]),
                )
                for row in connection.execute(
                    """
                    SELECT evidence_id, payload_hash, status,
                           source_review_revision
                    FROM training_evidence_records
                    ORDER BY evidence_id
                    """
                ).fetchall()
            ]
        return {
            "formula": CURRENT_MASTERY_PARAMETERS.formula_version,
            "parameter_version": CURRENT_MASTERY_PARAMETERS.version,
            "graph_release_id": self.current_knowledge.release_id,
            "training_evidence_version": _hash_payload(evidence_rows),
        }

    def _recent_question_ids(
        self,
        student_ids: tuple[str, ...],
        *,
        exclude_draft_id: str | None = None,
    ) -> dict[str, set[int]]:
        if not student_ids:
            return {}
        cutoff = (
            _day_clock(self.clock()) - timedelta(days=RECENT_WINDOW_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in student_ids)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT students.student_id, items.bank_question_id
                FROM variant_students students
                JOIN training_variants variants
                  ON variants.id = students.variant_id
                JOIN training_tasks tasks
                  ON tasks.id = variants.task_id
                JOIN training_task_items items
                  ON items.variant_id = variants.id
                WHERE students.student_id IN ({placeholders})
                  AND items.bank_question_id IS NOT NULL
                  AND tasks.status IN ('ready', 'exporting', 'completed')
                  AND tasks.created_at >= ?
                """,
                (*student_ids, cutoff),
            ).fetchall()
            draft_filter = (
                "AND instances.draft_id <> ?"
                if exclude_draft_id is not None
                else ""
            )
            personalized_parameters: tuple[object, ...] = (
                *student_ids,
                *(
                    (exclude_draft_id,)
                    if exclude_draft_id is not None
                    else ()
                ),
                cutoff,
            )
            personalized_rows = connection.execute(
                f"""
                SELECT instances.student_id, items.bank_question_id
                FROM personalized_paper_instances instances
                JOIN personalized_paper_items items
                  ON items.paper_instance_id =
                     instances.paper_instance_id
                WHERE instances.student_id IN ({placeholders})
                  AND items.bank_question_id IS NOT NULL
                  AND instances.status = 'frozen'
                  {draft_filter}
                  AND instances.created_at >= ?
                """,
                personalized_parameters,
            ).fetchall()
        result: dict[str, set[int]] = {}
        for row in (*rows, *personalized_rows):
            result.setdefault(str(row["student_id"]), set()).add(
                int(row["bank_question_id"])
            )
        return result

    def _current_exam_question_ids(
        self, diagnosis: Mapping[str, Any]
    ) -> set[int]:
        exam_scope = diagnosis.get("exam_scope")
        if not isinstance(exam_scope, Mapping):
            return set()
        session_ids = tuple(
            str(value)
            for value in exam_scope.get("session_ids", ())
            if str(value).strip()
        )
        if not session_ids:
            return set()
        placeholders = ",".join("?" for _ in session_ids)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT bank_question_id
                FROM grading_question_links
                WHERE grading_session_id IN ({placeholders})
                  AND status = 'confirmed'
                ORDER BY bank_question_id
                """,
                session_ids,
            ).fetchall()
        return {int(row["bank_question_id"]) for row in rows}

    def _by_request_token(self, token: str) -> dict[str, Any] | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT input_fingerprint, draft_json
                FROM personalized_recommendation_drafts
                WHERE request_token = ?
                """,
                (token,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(str(row["draft_json"]))
        result["_input_fingerprint"] = str(row["input_fingerprint"])
        return result

    def _apply_edit(
        self,
        draft: dict[str, Any],
        *,
        draft_id: str,
        request: dict[str, Any],
        command: RecommendationEditCommand,
        candidates: Sequence[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        student = next(
            (
                item
                for item in draft["students"]
                if item["student_id"] == command.student_id.strip()
            ),
            None,
        )
        if student is None:
            raise RecommendationEditInvalid("student is not in the draft")
        item = next(
            (
                value
                for value in student["items"]
                if value["item_id"] == command.item_id.strip()
            ),
            None,
        )
        if item is None:
            raise RecommendationEditInvalid("item is not in the draft")
        before = deepcopy(item)
        if command.action == "lock":
            item["locked"] = True
        elif command.action == "unlock":
            item["locked"] = False
        elif command.action == "exclude":
            if item["locked"]:
                raise RecommendationEditInvalid(
                    "locked item must be unlocked before exclusion"
                )
            student["items"].remove(item)
            student["estimated_minutes"] = max(
                0,
                int(student["estimated_minutes"])
                - int(item["estimated_minutes"]),
            )
            _add_edit_shortage(student, str(item["stage"]))
            after = {
                "item_id": item["item_id"],
                "question_id": None,
                "excluded": True,
            }
            return before, after
        else:
            if item["locked"]:
                raise RecommendationEditInvalid(
                    "locked item must be unlocked before replacement"
                )
            config = PersonalizedRecommendationConfig(
                **_config_constructor(request["config"])
            )
            used = {
                int(value["question_id"])
                for value in student["items"]
                if value is not item
            }
            eligible = self._eligible_candidates(
                candidates,
                stage=item["stage"],
                target_keys=(str(item["matched_key"]),),
                maintenance=student["selection_mode"]
                == "maintenance_fallback",
                used=used,
                recent=self._recent_question_ids(
                    (str(student["student_id"]),),
                    exclude_draft_id=draft_id,
                ).get(str(student["student_id"]), set()),
                excluded=(
                    self._current_exam_question_ids(request["diagnosis"])
                    if config.exclude_current_exam_originals
                    else set()
                ),
                config=config,
            )
            remaining_minutes = (
                int(student["estimated_minutes"])
                - int(item["estimated_minutes"])
            )
            eligible = [
                candidate
                for candidate in eligible
                if remaining_minutes
                + int(candidate["estimated_minutes"])
                <= config.expected_minutes
            ]
            if command.replacement_question_id is not None:
                eligible = [
                    candidate
                    for candidate in eligible
                    if int(candidate["question_id"])
                    == int(command.replacement_question_id)
                ]
            eligible = [
                candidate
                for candidate in eligible
                if int(candidate["question_id"]) != int(item["question_id"])
            ]
            if not eligible:
                raise RecommendationEditInvalid(
                    "no approved replacement is available"
                )
            replacement = _draft_item(
                eligible[0],
                stage=item["stage"],
                slot=int(item["slot"]),
                student_id=student["student_id"],
                target=item["target"],
                matched_key=_matched_key(
                    eligible[0]["stable_keys"],
                    (str(item["matched_key"]),),
                ),
                maintenance=student["selection_mode"]
                == "maintenance_fallback",
            )
            replacement["item_id"] = item["item_id"]
            replacement["item_order"] = item["item_order"]
            replacement["replacement_history"] = [
                *item.get("replacement_history", []),
                {
                    "question_id": item["question_id"],
                    "reason": command.reason.strip(),
                    "actor_ref": command.actor_ref.strip(),
                    "revision": int(draft["revision"]) + 1,
                },
            ]
            student["items"][student["items"].index(item)] = replacement
            student["estimated_minutes"] = (
                remaining_minutes
                + int(replacement["estimated_minutes"])
            )
            item = replacement
        after = deepcopy(item)
        return before, after


def _normalize_diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    students = value.get("students")
    if not isinstance(students, list) or not students:
        raise ValueError("diagnosis must contain students")
    normalized_students: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in students:
        if not isinstance(raw, Mapping):
            raise ValueError("diagnosis student is invalid")
        student_id = _required_text(raw.get("student_id"), "student_id")
        if student_id in seen:
            raise ValueError("diagnosis contains duplicate students")
        seen.add(student_id)
        weak_points = raw.get("weak_points")
        normalized_students.append(
            {
                "student_id": student_id,
                "student_code": str(raw.get("student_code") or "").strip(),
                "student_name": str(raw.get("student_name") or "").strip(),
                "class_id": str(raw.get("class_id") or "").strip(),
                "weak_points": [
                    dict(item)
                    for item in (
                        weak_points if isinstance(weak_points, list) else []
                    )
                    if isinstance(item, Mapping)
                ],
            }
        )
    exam_scope = value.get("exam_scope")
    return {
        "students": sorted(
            normalized_students, key=lambda item: item["student_id"]
        ),
        "exam_scope": (
            {
                "mode": str(exam_scope.get("mode") or ""),
                "session_ids": sorted(
                    {
                        int(item)
                        for item in exam_scope.get("session_ids", [])
                        if int(item) > 0
                    }
                ),
            }
            if isinstance(exam_scope, Mapping)
            else {"mode": "", "session_ids": []}
        ),
        "_mastery_session_times": dict(
            value.get("_mastery_session_times")
            if isinstance(value.get("_mastery_session_times"), Mapping)
            else {}
        ),
        "knowledge_catalog": [
            dict(item)
            for item in (
                value.get("knowledge_catalog")
                if isinstance(value.get("knowledge_catalog"), list)
                else []
            )
            if isinstance(item, Mapping) and str(item.get("knowledge_key") or "").strip()
        ],
    }


def _student_targets(
    profile: Mapping[str, Any],
    *,
    explicit: tuple[str, ...],
    mastery: Mapping[tuple[str, str], dict[str, Any]],
    allow_implicit: bool = True,
) -> list[dict[str, Any]]:
    student_id = str(profile["student_id"])
    by_key = {
        key: value
        for (owner, key), value in mastery.items()
        if owner == student_id
    }
    if explicit:
        keys = explicit
    elif allow_implicit:
        keys = tuple(
            key
            for key, value in sorted(
                by_key.items(),
                key=lambda item: (
                    item[1]["value"] is None,
                    item[1]["value"]
                    if item[1]["value"] is not None
                    else 2.0,
                    item[0],
                ),
            )
        )
    else:
        keys = ()
    targets = []
    for key in keys:
        evidence = by_key.get(key)
        targets.append(
            evidence
            or {
                "stable_key": key,
                "display_name": key,
                "mode": "selected",
                "status": "missing",
                "value": None,
                "evidence_count": 0,
                "parameter_version": None,
                "source_question_refs": [],
                "explanations": [
                    "教师明确选择了该目标，但当前范围没有可计入的掌握证据。"
                ],
            }
        )
    return targets[:50]


def _relation_index(
    relations: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        source = str(relation["source_key"])
        target = str(relation["target_key"])
        relation_type = str(relation["relation_type"])
        item = dict(relation)
        result.setdefault(source, []).append(item)
        if relation_type == "related":
            result.setdefault(target, []).append(item)
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (
                    item["relation_type"],
                    item["source_key"],
                    item["target_key"],
                    item["relation_id"],
                ),
            )
        )
        for key, values in result.items()
    }


def _stage_targets(
    targets: Sequence[Mapping[str, Any]],
    relations: Mapping[str, tuple[dict[str, Any], ...]],
) -> dict[Stage, tuple[str, ...]]:
    direct = tuple(str(item["stable_key"]) for item in targets)
    prerequisite: list[str] = []
    transfer: list[str] = []
    for target in direct:
        for relation in relations.get(target, ()):
            kind = relation["relation_type"]
            if kind == "prerequisite" and relation["source_key"] == target:
                prerequisite.append(str(relation["target_key"]))
            elif kind == "related":
                transfer.append(
                    str(
                        relation["target_key"]
                        if relation["source_key"] == target
                        else relation["source_key"]
                    )
                )
    return {
        "direct": tuple(dict.fromkeys(direct)),
        "prerequisite": tuple(dict.fromkeys(prerequisite)),
        "transfer": tuple(dict.fromkeys(transfer)),
    }


def _target_for_match(
    matched_key: str,
    targets: Sequence[dict[str, Any]],
    *,
    stage: Stage,
    relation_index: Mapping[str, tuple[dict[str, Any], ...]],
) -> dict[str, Any]:
    if stage == "direct":
        return next(
            (
                dict(item)
                for item in targets
                if item["stable_key"] == matched_key
            ),
            {"stable_key": matched_key, "status": "missing"},
        )
    for target in targets:
        for relation in relation_index.get(target["stable_key"], ()):
            if stage == "prerequisite":
                if (
                    relation["relation_type"] == "prerequisite"
                    and relation["target_key"] == matched_key
                ):
                    return {
                        **dict(target),
                        "relation": _relation_evidence(relation),
                    }
            elif relation["relation_type"] == "related" and matched_key in {
                relation["source_key"],
                relation["target_key"],
            }:
                return {
                    **dict(target),
                    "relation": _relation_evidence(relation),
                }
    return {"stable_key": matched_key, "status": "missing"}


def _draft_item(
    candidate: Mapping[str, Any],
    *,
    stage: Stage,
    slot: int,
    student_id: str,
    target: Mapping[str, Any],
    matched_key: str,
    maintenance: bool,
) -> dict[str, Any]:
    item_id = _hash_payload(
        {
            "student_id": student_id,
            "stage": stage,
            "slot": slot,
        }
    )[:20]
    relation = target.get("relation")
    if maintenance:
        reason = "当前证据不足，安排一题可练判定点的保守复习题。"
    elif stage == "direct":
        reason = (
            f"直接巩固 {target.get('display_name') or matched_key}；"
            f"当前掌握口径为 {target.get('mode', 'unknown')}。"
        )
    elif stage == "prerequisite":
        reason = (
            f"补强已确认的先修知识 {candidate['stable_names'].get(matched_key, matched_key)}。"
        )
    else:
        reason = (
            f"练习与目标已确认相关的迁移知识 {candidate['stable_names'].get(matched_key, matched_key)}。"
        )
    return {
        "item_id": item_id,
        "item_order": 0,
        "slot": slot,
        "question_id": int(candidate["question_id"]),
        "question_number": str(candidate["question_number"]),
        "stage": stage,
        "target": {
            key: deepcopy(value)
            for key, value in target.items()
            if key != "relation"
        },
        "matched_key": matched_key,
        "matched_name": candidate["stable_names"].get(
            matched_key, matched_key
        ),
        "relation": deepcopy(relation),
        "criterion_version_id": candidate["criterion_version_id"],
        "criterion_point_count": candidate["criterion_point_count"],
        "difficulty": candidate["difficulty"],
        "estimated_minutes": candidate["estimated_minutes"],
        "source_paper": candidate["source_paper"],
        "reason": reason,
        "locked": False,
        "replacement_history": [],
    }


def _stage_counts(
    config: PersonalizedRecommendationConfig,
) -> dict[Stage, int]:
    ratios = {
        "direct": config.direct_ratio,
        "prerequisite": config.prerequisite_ratio,
        "transfer": config.transfer_ratio,
    }
    raw = {
        stage: config.question_count * ratio
        for stage, ratio in ratios.items()
    }
    result = {stage: int(math.floor(value)) for stage, value in raw.items()}
    remainder = config.question_count - sum(result.values())
    for stage in sorted(
        ratios,
        key=lambda item: (
            -(raw[item] - result[item]),
            ("direct", "prerequisite", "transfer").index(item),
        ),
    )[:remainder]:
        result[stage] += 1
    return result  # type: ignore[return-value]


def _estimated_minutes(question_type: str, text: str) -> int:
    value = f"{question_type} {text}".casefold()
    if any(token in value for token in ("证明", "proof")):
        return 10
    if any(token in value for token in ("作图", "画图", "construction")):
        return 8
    if any(token in value for token in ("计算", "解答", "calculation")):
        return 6
    if any(token in value for token in ("填空", "fill")):
        return 3
    return 2


def _difficulty(value: object) -> int | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 1 <= parsed <= 10:
        return None
    return int(round(parsed))


def _rate(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return min(max(parsed, 0.0), 1.0)


def _target_rank(
    stable_keys: Sequence[str], target_keys: Sequence[str]
) -> int:
    positions = {
        value: index for index, value in enumerate(target_keys)
    }
    return min(
        (positions[value] for value in stable_keys if value in positions),
        default=len(positions),
    )


def _matched_key(
    stable_keys: Sequence[str], target_keys: Sequence[str]
) -> str:
    positions = {value: index for index, value in enumerate(target_keys)}
    matched = sorted(
        (value for value in stable_keys if value in positions),
        key=lambda value: (positions[value], value),
    )
    return matched[0] if matched else str(stable_keys[0])


def _relation_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "relation_id": str(value["relation_id"]),
        "relation_type": str(value["relation_type"]),
        "source_key": str(value["source_key"]),
        "target_key": str(value["target_key"]),
        "rationale": str(value["rationale"]),
        "revision": int(value["revision"]),
    }


def _shortage_message(stage: str, missing: int, code: str) -> str:
    labels = {
        "direct": "直接巩固",
        "prerequisite": "先修补强",
        "transfer": "迁移应用",
    }
    cause = (
        "预计时长已达到教师设置"
        if code == "time_limit_reached"
        else "没有更多同时满足稳定知识、难度、近期去重和可练判定点的题目"
    )
    return f"{labels[stage]}少配 {missing} 题：{cause}。"


def _add_edit_shortage(student: dict[str, Any], stage: str) -> None:
    selected_count = sum(
        1 for item in student["items"] if item["stage"] == stage
    )
    shortage = next(
        (
            item
            for item in student["shortages"]
            if item["stage"] == stage
        ),
        None,
    )
    if shortage is None:
        student["shortages"].append(
            {
                "stage": stage,
                "requested_count": selected_count + 1,
                "selected_count": selected_count,
                "missing_count": 1,
                "reason_code": "teacher_excluded",
            }
        )
    else:
        shortage["selected_count"] = max(
            0, int(shortage["selected_count"]) - 1
        )
        shortage["missing_count"] += 1
        shortage["reason_code"] = "teacher_excluded"
    student["warnings"].append("教师排除了一道题，草稿保留空缺，不自动模糊补题。")
    student["warnings"] = list(dict.fromkeys(student["warnings"]))


def _config_constructor(value: Mapping[str, Any]) -> dict[str, Any]:
    ratios = value.get("stage_ratios")
    return {
        "paper_mode": value.get("paper_mode", "individual"),
        "question_count": value["question_count"],
        "expected_minutes": value["expected_minutes"],
        "difficulty_min": value["difficulty_min"],
        "difficulty_max": value["difficulty_max"],
        "direct_ratio": (
            ratios["direct"]
            if isinstance(ratios, Mapping)
            else value["direct_ratio"]
        ),
        "prerequisite_ratio": (
            ratios["prerequisite"]
            if isinstance(ratios, Mapping)
            else value["prerequisite_ratio"]
        ),
        "transfer_ratio": (
            ratios["transfer"]
            if isinstance(ratios, Mapping)
            else value["transfer_ratio"]
        ),
        "target_keys": tuple(value.get("target_keys") or ()),
        "scope_keys": tuple(value.get("scope_keys") or ()),
        "exclude_current_exam_originals": bool(
            value.get("exclude_current_exam_originals", True)
        ),
    }


def _past_action(action: Action) -> str:
    return {
        "lock": "locked",
        "unlock": "unlocked",
        "exclude": "excluded",
        "replace": "replaced",
    }[action]


def _context_source_version(
    base_source_version: str,
    *,
    as_of: datetime,
    recent: Mapping[str, set[int]],
    excluded_question_ids: set[int],
) -> str:
    return _hash_payload(
        {
            "base_source_version": base_source_version,
            "as_of_day": as_of.date().isoformat(),
            "recent_question_ids": {
                student_id: sorted(question_ids)
                for student_id, question_ids in sorted(recent.items())
            },
            "excluded_question_ids": sorted(excluded_question_ids),
        }
    )


def _identity_keys(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(
            str(value or "").strip().casefold()
            for value in values
            if str(value or "").strip()
        )
    )
    if len(normalized) > 50:
        raise ValueError(f"{field} contains too many values")
    if any(
        not (value.startswith("kp_") or value.startswith("ki_"))
        for value in normalized
    ):
        raise ValueError(f"{field} must use governed stable identities")
    return normalized


def _scope_leaves(
    scope_keys: Sequence[str],
    *,
    diagnosis: Mapping[str, Any],
    relations: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    if not scope_keys:
        return ()
    children: dict[str, list[str]] = {}

    def _add_child(parent: str, child: str) -> None:
        if not parent or not child or parent == child:
            return
        bucket = children.setdefault(parent, [])
        if child not in bucket:
            bucket.append(child)

    for relation in relations:
        if str(relation.get("relation_type") or "") != "parent":
            continue
        _add_child(str(relation.get("target_key") or ""), str(relation.get("source_key") or ""))
    catalog = diagnosis.get("knowledge_catalog")
    if isinstance(catalog, list):
        for item in catalog:
            if not isinstance(item, Mapping):
                continue
            _add_child(
                str(item.get("parent_knowledge_key") or "").strip().casefold(),
                str(item.get("knowledge_key") or "").strip().casefold(),
            )
    leaves: list[str] = []
    seen: set[str] = set()

    def walk(node: str, trail: frozenset[str]) -> None:
        if not node or node in trail:
            return
        kids = children.get(node, ())
        if not kids:
            if node not in seen:
                seen.add(node)
                leaves.append(node)
            return
        next_trail = trail | {node}
        for kid in kids:
            walk(kid, next_trail)

    for key in scope_keys:
        walk(str(key), frozenset())
    return tuple(leaves)


def _apply_diagnosis_mastery(
    snapshot: dict[tuple[str, str], dict[str, Any]],
    diagnosis: Mapping[str, Any],
    *,
    resolver: CurrentKnowledgeResolver,
) -> None:
    """Prefer mastery already shown on the diagnosis page over a second pass."""

    students = diagnosis.get("students")
    if not isinstance(students, list):
        return
    for profile in students:
        if not isinstance(profile, Mapping):
            continue
        student_id = str(profile.get("student_id") or "").strip()
        if not student_id:
            continue
        weak_points = profile.get("weak_points")
        if not isinstance(weak_points, list):
            continue
        for item in weak_points:
            if not isinstance(item, Mapping):
                continue
            raw_value = item.get("mastery")
            if raw_value is None:
                continue
            try:
                value = float(raw_value)
                count = int(item.get("evidence_count") or 0)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            raw_key = str(
                item.get("knowledge_key") or item.get("knowledge_point") or ""
            ).strip()
            if not raw_key:
                continue
            resolved = resolver.resolve(raw_key)
            keys = [match.stable_key for match in resolved]
            if not keys and raw_key.casefold().startswith(("kp_", "ki_")):
                keys = [raw_key.casefold()]
            display = str(item.get("knowledge_point") or "").strip()
            for key in keys:
                node = resolver.node(key)
                existing = snapshot.get((student_id, key), {})
                snapshot[(student_id, key)] = {
                    "stable_key": key,
                    "display_name": (
                        node.display_name if node is not None else display or key
                    ),
                    "mode": "current",
                    "status": "available",
                    "value": value,
                    "evidence_count": count,
                    "parameter_version": str(
                        existing.get("parameter_version") or ""
                    ),
                    "source_question_refs": [],
                    "explanations": [],
                }


def _ranked_leaf_keys(
    *,
    student_id: str,
    leaves: Sequence[str],
    mastery: Mapping[tuple[str, str], Mapping[str, Any]],
    limit: int,
) -> tuple[str, ...]:
    scored: list[tuple[float, str]] = []
    for key in leaves:
        evidence = mastery.get((student_id, key))
        if not isinstance(evidence, Mapping):
            continue
        value = evidence.get("value")
        count = int(evidence.get("evidence_count") or 0)
        if value is None or count <= 0:
            continue
        scored.append((float(value), key))
    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(key for _, key in scored[: max(0, int(limit))])


def _ranked_group_leaf_keys(
    *,
    student_ids: Sequence[str],
    leaves: Sequence[str],
    mastery: Mapping[tuple[str, str], Mapping[str, Any]],
    limit: int,
) -> tuple[str, ...]:
    scored: list[tuple[float, str]] = []
    for key in leaves:
        values: list[float] = []
        for student_id in student_ids:
            evidence = mastery.get((student_id, key))
            if (
                isinstance(evidence, Mapping)
                and evidence.get("value") is not None
                and int(evidence.get("evidence_count") or 0) > 0
            ):
                values.append(float(evidence["value"]))
        if not values:
            continue
        scored.append((sum(values) / len(values), key))
    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(key for _, key in scored[: max(0, int(limit))])


def _day_clock(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clock must include timezone")
    current = value.astimezone(UTC)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if len(token) != 32 or any(
        character not in "0123456789abcdef" for character in token
    ):
        raise ValueError("request_token must be 32 hexadecimal characters")
    return token


def _draft_id(value: object) -> str:
    draft_id = str(value or "").strip().casefold()
    if len(draft_id) != 64 or any(
        character not in "0123456789abcdef" for character in draft_id
    ):
        raise ValueError("draft_id must be a 64-character hash")
    return draft_id


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be blank")
    return text


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "ENGINE_VERSION",
    "PersonalizedRecommendationConfig",
    "PersonalizedRecommendationError",
    "PersonalizedRecommendationModule",
    "RecommendationDraftNotFound",
    "RecommendationEditCommand",
    "RecommendationEditInvalid",
    "RecommendationRequestConflict",
    "RecommendationRevisionConflict",
    "RecommendationSourceChanged",
]
