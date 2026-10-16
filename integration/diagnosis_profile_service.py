from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from dataclasses import fields
from question_bank.mastery.model import week_of
import os
import pickle
import sqlite3
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any, TypedDict

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.grading_database import open_grading_repositories
from integration.data_generation import (
    commit_generation,
    database_change_stamp,
    data_versions,
)
from integration.evidence_scope import EvidenceScopeResolver
from integration.persistent_entries import persistent_entry_store
from integration.question_tag_projection_service import (
    QuestionTagProjection,
    QuestionTagProjectionService,
)
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.mastery.current import (
    CurrentMasteryCalculator,
    CURRENT_MASTERY_PARAMETERS,
    aggregate_current_mastery,
)
from question_bank.question_types import (
    chapter_target_kind, chapter_target_kinds, is_training_target, is_type_key, target_kind_for_key,
)

GENERIC_ERROR_REASONS = {
    "未作答",
    "未选择正确答案",
    "答案不等价",
    "答案不正确",
}

_TAG_PROFILE_CACHE_LOCK = threading.RLock()
_TAG_PROFILE_CACHE_LIMIT = 12
_LOCAL_PROFILE_MAX_ENTRIES = 240
_LOCAL_PROFILE_MAX_BYTES = 512 * 1024 * 1024
_SESSION_ERROR_MAX_ENTRIES = 64
_SESSION_ERROR_MAX_BYTES = 256 * 1024 * 1024
# Cached payloads are stored as pickle bytes: rebuilding a hit with
# pickle.loads is far cheaper than deepcopy on large profiles, and bytes are
# inherently isolated from caller mutation in both directions.
_TAG_PROFILE_CACHE: dict[
    tuple[str, ...],
    tuple[bytes, bytes],
] = {}


class _ProfileFlight:
    """Single-flight slot for an in-progress profile computation."""

    __slots__ = ("event", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.error: BaseException | None = None


_TAG_PROFILE_FLIGHTS: dict[tuple[str, ...], _ProfileFlight] = {}


def _claim_or_wait_tag_profile(
    cache_key: tuple[str, ...],
) -> tuple[bytes, bytes] | None:
    """Return the cached entry, or None when this caller owns the compute.

    Concurrent callers for the same key wait on the in-progress flight
    instead of duplicating the computation.
    """

    while True:
        with _TAG_PROFILE_CACHE_LOCK:
            cached = _TAG_PROFILE_CACHE.get(cache_key)
            if cached is not None:
                return cached
            flight = _TAG_PROFILE_FLIGHTS.get(cache_key)
            if flight is None:
                flight = _ProfileFlight()
                _TAG_PROFILE_FLIGHTS[cache_key] = flight
                return None
        flight.event.wait()
        if flight.error is not None:
            raise flight.error


def _release_tag_profile_flight(
    cache_key: tuple[str, ...],
    error: BaseException | None = None,
) -> None:
    with _TAG_PROFILE_CACHE_LOCK:
        flight = _TAG_PROFILE_FLIGHTS.pop(cache_key, None)
        if flight is not None:
            flight.error = error
            flight.event.set()


def _normalized_profile_scope(
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized = dict(scope)
    classes = _text_list(scope.get("class_ids"))
    legacy_class = str(scope.get("class_id") or scope.get("class_name") or "").strip()
    if not classes and legacy_class:
        classes = [legacy_class]
    if classes:
        normalized["class_ids"] = classes
        normalized.pop("class_id", None)
        normalized.pop("class_name", None)
    if exam_scope.get("mode") == "semester":
        normalized["use_historical_fallback"] = False
    return normalized


def _profile_semantics() -> tuple:
    return ("exam-source-scores-v4", str(week_of(datetime.now(UTC))))


class DiagnosisSnapshot(TypedDict, total=False):
    """Teacher-visible diagnosis snapshot returned by build_profiles/build_tag_profiles."""

    scope: dict[str, Any]
    exam_scope: dict[str, Any]
    students: list[dict[str, Any]]
    group_weak_points: list[dict[str, Any]]
    knowledge_catalog: list[dict[str, Any]]
    knowledge_associations: list[dict[str, Any]]
    coverage: dict[str, Any]
    confirmed_concept_ids: list[Any]
    suggested_terms: list[Any]
    unmapped_terms: list[Any]
    warnings: list[str]
    diagnosis_identity: str


class DiagnosisProfileService:
    def __init__(
        self,
        grading_db_path: str | Path,
        question_bank_db_path: str | Path,
        *,
        grading_db: GradingRepositoryAccess | None = None,
        question_bank_connection: sqlite3.Connection | None = None,
        cache_identity: tuple[str, ...] | None = None,
        data_root: Path | None = None,
        persist_snapshots: bool = False,
    ) -> None:
        self.grading_db_path = Path(grading_db_path)
        self.db = (
            as_grading_repositories(grading_db)
            if grading_db is not None
            else open_grading_repositories(Path(grading_db_path))
        )
        self.question_bank_db_path = Path(question_bank_db_path)
        self.question_bank_connection = question_bank_connection
        self.data_root = Path(data_root) if data_root is not None else self.question_bank_db_path.parent.parent
        self.cache_identity = cache_identity
        self.persist_snapshots = persist_snapshots
        self.latest_aggregated_mastery: dict[str, Any] = {}

    def build_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> DiagnosisSnapshot:
        return self.build_tag_profiles(scope=scope, exam_scope=exam_scope)

    def build_summary_profiles(self, *, scope, exam_scope) -> DiagnosisSnapshot:
        """Internal overview/graph input; detailed evidence still uses build_profiles."""
        return self.build_tag_profiles(scope=scope, exam_scope=exam_scope,
                                       include_source_details=False)

    @staticmethod
    def _summary_tag_profile_snapshot(result: DiagnosisSnapshot) -> DiagnosisSnapshot:
        """Strip the per-question source details a summary view never shows.

        The summary variant shares the detailed snapshot's evidence scan and
        mastery merge; only the fields guarded by ``include_source_details``
        in ``_compute_tag_profiles`` differ, and each maps to a fixed value.
        """
        summary = dict(result)
        summary.pop("_type_class_question_totals", None)
        summary["knowledge_associations"] = []
        students = []
        for student in result.get("students") or ():
            students.append({
                **student,
                "weak_points": [{
                    **point,
                    "error_categories": [],
                    "error_patterns": [],
                    "source_question_refs": [
                        {key: ({} if key == "assessment" else value)
                         for key, value in reference.items() if key != "causes"}
                        for reference in point.get("source_question_refs") or ()
                    ],
                } for point in student.get("weak_points") or ()],
            })
        summary["students"] = students
        return summary

    def tag_profile_cache_key(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
        live: bool = False,
    ) -> tuple[str, ...]:
        """Exact key under which build_tag_profiles caches this request."""

        scope = _normalized_profile_scope(scope, exam_scope)
        source_paths = (self.grading_db_path, self.question_bank_db_path)
        if live and self.cache_identity and len(self.cache_identity) == 6:
            source_paths = (Path(self.cache_identity[0]), Path(self.cache_identity[3]))
        source_identity = (None if live else self.cache_identity) or (
            *_path_generation(source_paths[0]),
            *_path_generation(source_paths[1], _QB_VERSION_DOMAINS),
        )
        source_identity = (*source_identity,
            *self._cause_changes(exam_scope),
            json.dumps(_profile_semantics(), default=str))
        return (
            "\u0000".join(source_identity),
            "tag-profile",
            str(self.data_root),
            str(week_of(datetime.now(UTC))),
            json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(exam_scope, ensure_ascii=False, sort_keys=True, default=str),
        )

    def mastery_exam_scope(self, exam_scope):
        if exam_scope.get("mode") == "semester":
            return {"mode": "semester", "curriculum_volume_id": exam_scope.get("curriculum_volume_id")}
        sessions = [s for s in self.db.sessions.list_grading_sessions() if not s.get("is_deleted")]
        selected = set(int(v) for v in exam_scope.get("session_ids", []))
        linked = [s for s in sessions if (not selected or int(s["id"]) in selected) and s.get("curriculum_volume_id")]
        if linked:
            latest = max(linked, key=lambda s: (str(s.get("created_at") or ""), int(s["id"])))
            return {"mode": "semester", "curriculum_volume_id": latest["curriculum_volume_id"]}
        return dict(exam_scope)

    def semester_observations(self, exam_scope, *, supplied=None):
        from question_bank.mastery.model import build_exam_observations
        exam_scope = self.mastery_exam_scope(exam_scope)
        key = (*self.tag_profile_cache_key(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=exam_scope), "exam-observations")
        cached = _claim_or_wait_tag_profile(key)
        if cached is not None:
            return pickle.loads(cached[0])
        error = None
        try:
            if supplied is not None:
                observations = supplied
            else:
                resolved = EvidenceScopeResolver(self.db).resolve(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=exam_scope)
                ids = [int(s["id"]) for s in resolved.sessions]
                rows = self._projected_tag_evidence(student_ids=[str(s["id"]) for s in resolved.students],
                    session_ids=ids, projection_by_session=self._tag_projections(ids))
                resolver = CurrentKnowledgeResolver.from_active_database(self.question_bank_db_path)
                observations = build_exam_observations(rows, resolver, self.mastery_session_times(exam_scope=exam_scope))
            entry = (pickle.dumps(observations, pickle.HIGHEST_PROTOCOL), b"")
            with _TAG_PROFILE_CACHE_LOCK:
                _TAG_PROFILE_CACHE[key] = entry
                while len(_TAG_PROFILE_CACHE) > _TAG_PROFILE_CACHE_LIMIT:
                    _TAG_PROFILE_CACHE.pop(next(iter(_TAG_PROFILE_CACHE)))
            return observations
        except BaseException as exc:
            error = exc
            raise
        finally:
            _release_tag_profile_flight(key, error)

    def semester_mastery(self, profile, *, exclude_training_evidence_ids=frozenset(), as_of=None, parameters=None, evidence_until=None, include_unobserved=True):
        from datetime import UTC, datetime
        from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS
        from question_bank.mastery.model import week_of
        parameters = parameters or CURRENT_MASTERY_PARAMETERS
        as_of = as_of or datetime.now(UTC)
        exam_scope = self.mastery_exam_scope(profile.get("exam_scope") or {})
        key = (*self.tag_profile_cache_key(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=exam_scope),
            "mastery", str(week_of(as_of)), ",".join(sorted(exclude_training_evidence_ids)),
            tuple((item.name, getattr(parameters, item.name)) for item in fields(parameters)
                  if "version" not in item.name),
            evidence_until.isoformat() if evidence_until is not None else "")
        if not include_unobserved:
            key = (*key, "observed-nodes")
        cached = _claim_or_wait_tag_profile(key)
        if cached is not None:
            if getattr(self, "persist_snapshots", False) and not exclude_training_evidence_ids and evidence_until is None:
                self._save_local_profile(key, cached)
            return pickle.loads(cached[0])
        error = None
        try:
            local_reader = getattr(self, "_read_local_profile", None)
            local = local_reader(key) if callable(local_reader) and evidence_until is None else None
            if local is not None:
                with _TAG_PROFILE_CACHE_LOCK:
                    _TAG_PROFILE_CACHE[key] = local
                return pickle.loads(local[0])
            source_scope = profile.get("exam_scope") or {}
            same_scope = exam_scope == source_scope or (
                source_scope.get("mode") == "semester"
                and exam_scope == self.mastery_exam_scope(source_scope)
            )
            supplied = profile.get("_mastery_observations") if profile.get("_mastery_population") and same_scope else None
            observations = self.semester_observations(exam_scope, supplied=supplied)
            resolver = CurrentKnowledgeResolver.from_active_database(self.question_bank_db_path)
            calculation_profile = {"exam_scope": exam_scope, "_mastery_observations": observations,
                                   "_mastery_session_times": self.mastery_session_times(exam_scope=exam_scope)}
            # The surrounding cache already shares this computation and isolates
            # its result. A second identical cache only serializes it twice.
            values = CurrentMasteryCalculator(
                self.question_bank_db_path, resolver, parameters=parameters,
                clock=lambda: as_of, data_root=self.data_root,
            ).calculate(calculation_profile,
                        exclude_training_evidence_ids=exclude_training_evidence_ids,
                        evidence_until=evidence_until, include_unobserved=include_unobserved)
            entry = (pickle.dumps(values, pickle.HIGHEST_PROTOCOL), pickle.dumps({}, pickle.HIGHEST_PROTOCOL))
            with _TAG_PROFILE_CACHE_LOCK:
                _TAG_PROFILE_CACHE[key] = entry
                while len(_TAG_PROFILE_CACHE) > _TAG_PROFILE_CACHE_LIMIT:
                    _TAG_PROFILE_CACHE.pop(next(iter(_TAG_PROFILE_CACHE)))
            if getattr(self, "persist_snapshots", False) and not exclude_training_evidence_ids and evidence_until is None:
                self._save_local_profile(key, entry)
            return values
        except BaseException as exc:
            error = exc
            raise
        finally:
            _release_tag_profile_flight(key, error)

    def build_tag_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
        include_source_details: bool = True,
    ) -> DiagnosisSnapshot:
        cache_key = self.tag_profile_cache_key(scope=scope, exam_scope=exam_scope)
        full_cache_key = cache_key
        if not include_source_details:
            cache_key = (*cache_key, 'summary-without-source-details-v1')
        cached = _claim_or_wait_tag_profile(cache_key)
        if cached is not None:
            if self.persist_snapshots:
                self._save_local_profile(cache_key, cached)
            self.latest_aggregated_mastery = pickle.loads(cached[1])
            result = pickle.loads(cached[0])
            if getattr(self, "persist_snapshots", False):
                try:
                    self.semester_mastery(result)
                except (CurrentKnowledgeUnavailable, OSError, sqlite3.Error, TypeError, ValueError):
                    pass  # Optional prewarm preserves the cached evidence-only fallback.
            return result
        error: BaseException | None = None
        try:
            local = self._read_local_profile(cache_key)
            if local is None:
                if include_source_details:
                    result, aggregated = self._compute_tag_profiles(
                        scope=_normalized_profile_scope(scope, exam_scope),
                        exam_scope=exam_scope,
                    )
                else:
                    # Reuse an existing detailed snapshot when available;
                    # otherwise compute without source details, because a
                    # summary read must not load the private class baseline.
                    with _TAG_PROFILE_CACHE_LOCK:
                        full_entry = _TAG_PROFILE_CACHE.get(full_cache_key)
                    if full_entry is None:
                        full_entry = self._read_local_profile(full_cache_key)
                    if full_entry is not None:
                        result = self._summary_tag_profile_snapshot(pickle.loads(full_entry[0]))
                        aggregated = dict(pickle.loads(full_entry[1]))
                    else:
                        result, aggregated = self._compute_tag_profiles(
                            scope=_normalized_profile_scope(scope, exam_scope),
                            exam_scope=exam_scope, include_source_details=False,
                        )
            else:
                result, aggregated = pickle.loads(local[0]), pickle.loads(local[1])
        except BaseException as exc:
            error = exc
            raise
        else:
            entry = (
                pickle.dumps(result, pickle.HIGHEST_PROTOCOL),
                pickle.dumps(aggregated, pickle.HIGHEST_PROTOCOL),
            )
            with _TAG_PROFILE_CACHE_LOCK:
                _TAG_PROFILE_CACHE[cache_key] = entry
                while len(_TAG_PROFILE_CACHE) > _TAG_PROFILE_CACHE_LIMIT:
                    _TAG_PROFILE_CACHE.pop(next(iter(_TAG_PROFILE_CACHE)))
            if self.persist_snapshots:
                self._save_local_profile(cache_key, entry)
            self.latest_aggregated_mastery = dict(aggregated)
            return result
        finally:
            _release_tag_profile_flight(cache_key, error)

    def _cause_changes(self, exam_scope: Mapping[str, Any]) -> tuple[str, ...]:
        directory = self.data_root / "reports" / ".class_analysis"
        sessions = sorted(set(_safe_int(value) for value in exam_scope.get("session_ids", ())
                              if _safe_int(value) > 0))
        if not sessions:
            return _dir_generation(directory)
        return (str(directory), json.dumps([
            _session_error_file_state(directory / f"{sid}.json") for sid in sessions]))

    def _local_profile_path(self) -> Path:
        return self.data_root / "reports" / ".training_diagnosis" / "entries"

    def _local_profile_store(self):
        # Caps are read on each call so tests can narrow them.
        return persistent_entry_store(
            self._local_profile_path(),
            max_entries=_LOCAL_PROFILE_MAX_ENTRIES,
            max_bytes=_LOCAL_PROFILE_MAX_BYTES,
            compact=True,
        )

    def maintain_local_profiles(self, *, can_continue=lambda: True, compact=True,
                                dry_run=False) -> dict:
        """Maintain only free-to-rebuild diagnosis caches, using read rules.

        Last-display responses deliberately survive input changes so a page
        can show its previous result while refreshing. They are not pruned.
        """
        asset_states = []

        def profile_current(key, signature):
            if (not isinstance(key, tuple) or len(key) < 5
                    or key[0] != "tag-profile"):
                return False
            if len(key) > 5 and key[5] in ("overview-payload-v2", "overview-payload-v3"):
                return False
            try:
                if len(key) > 6 and key[5] == "api-diagnosis-json" and key[6] is not None:
                    grouping = key[6]
                    if (not isinstance(grouping, tuple) or len(grouping) != 4
                            or grouping[0] != "grouping-public-v2"
                            or grouping[2] != str(datetime.now(UTC).date())):
                        return False
                    if not asset_states:
                        from question_bank.services.question_read_service import _skill_asset_manifest
                        manifest = _skill_asset_manifest(self.data_root)
                        asset_states.append(tuple(sorted(manifest.items())) if manifest is not None else None)
                    if asset_states[0] is None:
                        return None
                    if grouping[3] != asset_states[0]:
                        return False
                scope, exam_scope = json.loads(key[3]), json.loads(key[4])
                if not isinstance(scope, dict) or not isinstance(exam_scope, dict):
                    return False
                base = self.tag_profile_cache_key(scope=scope, exam_scope=exam_scope)
                if key[:5] != base[1:]:
                    return False
                expected = self._local_profile_signature((base[0], *key))
                return None if expected is None else signature == expected
            except (ValueError, TypeError, OSError, sqlite3.Error):
                return None

        def session_current(key, signature):
            if (not isinstance(key, tuple) or len(key) != 2
                    or key[0] != "session-error-records"):
                return False
            try:
                session = self.db.sessions.get_grading_session(int(key[1]))
                if not session or session.get("is_deleted"):
                    return False
                expected = self._session_error_change_stamp(int(key[1]))
                return None if expected is None else signature == expected
            except (ValueError, TypeError, OSError, sqlite3.Error):
                return None

        result = {"removed_files": 0, "rewritten_files": 0,
                  "freed_bytes": 0, "completed": True}
        for name, count, size, predicate in (
            ("entries", _LOCAL_PROFILE_MAX_ENTRIES, _LOCAL_PROFILE_MAX_BYTES, profile_current),
            ("sessions", _SESSION_ERROR_MAX_ENTRIES, _SESSION_ERROR_MAX_BYTES, session_current),
        ):
            store = persistent_entry_store(
                self.data_root / "reports" / ".training_diagnosis" / name,
                max_entries=count, max_bytes=size, compact=compact,
            )
            outcome = store.maintain(predicate, can_continue=can_continue, dry_run=dry_run)
            for field in ("removed_files", "rewritten_files", "freed_bytes"):
                result[field] += outcome[field]
            if not outcome["completed"]:
                result["completed"] = False
                break
        return result

    def _local_profile_entry_path(self, cache_key: tuple[str, ...]) -> Path:
        return self._local_profile_store().entry_path(cache_key[1:])

    def _local_profile_signature(self, cache_key: tuple[str, ...]) -> tuple | None:
        try:
            # Every persisted result starts with the complete tag-profile key;
            # its namespace and view parameters follow that shared prefix.
            if len(cache_key) < 6 or cache_key[1] != "tag-profile" or not isinstance(cache_key[5], str):
                return None
            exam_scope = json.loads(cache_key[5])
            if not isinstance(exam_scope, dict):
                return None
            origins = self.cache_identity[:6] if self.cache_identity else ()
            paths = ((Path(origins[0]), Path(origins[3])) if len(origins) == 6
                     else (self.grading_db_path, self.question_bank_db_path))
            live = (*_path_generation(paths[0]), *_path_generation(paths[1], _QB_VERSION_DOMAINS))
            if origins and tuple(origins) != tuple(live):
                return None
            causes = self._cause_changes(exam_scope)
            expected = "\u0000".join((*live, *causes,
                                     json.dumps(_profile_semantics(), default=str)))
            if cache_key[0] != expected:
                return None
            changes = tuple(_path_change_signature(path) for path in paths)
            if live != (*_path_generation(paths[0]), *_path_generation(paths[1], _QB_VERSION_DOMAINS)):
                return None
            return (changes, causes, _profile_semantics())
        except (OSError, sqlite3.Error, AttributeError, ValueError, IndexError):
            return None

    def _read_local_profile(self, cache_key: tuple[str, ...]) -> tuple[bytes, bytes] | None:
        try:
            changes = self._local_profile_signature(cache_key)
            if changes is None:
                return None
            entry = self._local_profile_store().get(cache_key[1:], changes)
            if (not isinstance(entry, tuple) or len(entry) != 2
                    or not all(isinstance(part, bytes) for part in entry)):
                return None
            # Bad/incompatible local bytes must fall back before entering the
            # shared memory cache; no official model is required for rebuilding.
            if not all(isinstance(pickle.loads(part), dict) for part in entry):
                return None
            return entry
        except (OSError, pickle.PickleError, EOFError, KeyError, TypeError, ValueError,
                AttributeError, ImportError):
            return None

    def _save_local_profile(self, cache_key: tuple[str, ...], entry: tuple[bytes, bytes]) -> None:
        signature = self._local_profile_signature(cache_key)
        if signature is None:
            return
        self._local_profile_store().put(cache_key[1:], signature, entry)

    def read_persistent_result(self, cache_key: tuple) -> tuple[bytes, bytes] | None:
        """Read a durable derived result; cache_key[0] is the source identity
        produced by tag_profile_cache_key."""
        return self._read_local_profile(cache_key)

    def save_persistent_result(self, cache_key: tuple, entry: tuple[bytes, bytes]) -> None:
        """Persist a derived result regardless of persist_snapshots."""
        self._save_local_profile(cache_key, entry)

    def _compute_tag_profiles(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
        include_source_details: bool = True,
    ) -> tuple[DiagnosisSnapshot, dict[str, Any]]:
        resolved = EvidenceScopeResolver(self.db).resolve(
            scope=scope,
            exam_scope=exam_scope,
        )
        warnings = list(resolved.warnings)
        sessions = list(resolved.sessions)
        profile_volume = str(exam_scope.get("curriculum_volume_id") or "").strip()
        if not profile_volume:
            session_volumes = {str(item.get("curriculum_volume_id") or "").strip() for item in sessions}
            if len(session_volumes) == 1:
                profile_volume = next(iter(session_volumes))
        students = list(resolved.students)
        session_ids = [int(item["id"]) for item in sessions]
        student_ids = [str(item["id"]) for item in students]
        historical_by_student = {
            student_id: set(resolved.historical_session_ids_by_student.get(student_id, ()))
            for student_id in student_ids
        }
        evidence_session_ids = list(dict.fromkeys([
            *session_ids,
            *(
                session_id
                for values in historical_by_student.values()
                for session_id in values
            ),
        ]))
        projection_by_session = self._tag_projections(evidence_session_ids)
        evidence_rows = self._projected_tag_evidence(
            student_ids=student_ids,
            session_ids=evidence_session_ids,
            projection_by_session=projection_by_session,
        )
        cause_index = self._error_cause_index(evidence_session_ids) if include_source_details else {}
        selected_session_ids = set(session_ids)
        evidence_rows = [
            row for row in evidence_rows
            if int(row.get("session_id") or 0) in selected_session_ids
            or int(row.get("session_id") or 0)
            in historical_by_student.get(str(row.get("student_id") or ""), set())
        ]

        grouped_by_student: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in evidence_rows:
            student_id = str(row.get("student_id") or "")
            awarded = max(_number(row.get("score_awarded")), 0.0)
            full_score = max(_number(row.get("full_score")), 0.0)
            score_for_rate = min(awarded, full_score) if full_score > 0 else awarded
            tags = row.get("question_tags") if isinstance(row.get("question_tags"), Mapping) else {}
            contributions = row.get("target_contributions")
            has_contributions = isinstance(contributions, Mapping)
            targets: Iterable[tuple[str, tuple[float, float]]] = (
                (
                    (str(key), (float(value[0]), float(value[1])))
                    for key, value in contributions.items()
                )
                if has_contributions
                else (
                    (str(point or ""), (score_for_rate, full_score))
                    for point in tags.get("knowledge_point", [])
                )
            )
            for knowledge_point, (point_score, point_full) in targets:
                point = str(knowledge_point or "").strip()
                if not point:
                    continue
                item = grouped_by_student[student_id].setdefault(
                    point,
                    {
                        "knowledge_key": f"knowledge_point:{point}",
                        "knowledge_point": point,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "deduction_count": 0,
                        "source_question_refs": [],
                        "actionable_reasons": [],
                        "tag_context": defaultdict(list),
                        "primary_errors": Counter(),
                        "secondary_errors": Counter(),
                        "cause_categories": set(),
                        "cause_patterns": set(),
                    },
                )
                item["score_sum"] += point_score
                item["full_score_sum"] += point_full
                if point_full > 0 and point_score < point_full - 1e-6:
                    item["deduction_count"] += 1
                reference = {
                    "session_id": int(row.get("session_id") or 0),
                    "session_name": str(row.get("session_name") or ""),
                    "question_id": str(row.get("question_id") or ""),
                    "bank_question_id": int(row.get("bank_question_id") or 0),
                    "score_awarded": point_score if has_contributions else awarded,
                    "full_score": point_full if has_contributions else full_score,
                    "question_score_awarded": awarded,
                    "question_full_score": full_score,
                    "assessment": dict(row.get("assessment") or {}) if include_source_details else {},
                    "deduction_reason": str(row.get("deduction_reason") or "").strip(),
                    "error_summary": str(row.get("error_summary") or "").strip(),
                    "secondary_errors": [dict(error) for error in row.get("secondary_errors") or []
                                         if isinstance(error, Mapping)],
                    "score_rate": (round(point_score / point_full, 4) if point_full > 0 else None)
                    if has_contributions else (round(awarded / full_score, 4) if full_score > 0 else None),
                    "source_kind": (
                        "current_exam"
                        if int(row.get("session_id") or 0) in selected_session_ids
                        else "historical_exam"
                    ),
                }
                if row.get("combined_group_id"):
                    reference["combined_group_id"] = row["combined_group_id"]
                # 新错因体系：同场次同学同题的物化记录按证据挂到来源引用上，
                # 只引用当前答卷仍有效的实际错因，不把题库预测当作学生表现。
                cause_rows = cause_index.get(
                    (
                        int(row.get("session_id") or 0),
                        _safe_int(row.get("student_id")),
                        str(row.get("question_id") or ""),
                    ),
                    (),
                )
                if cause_rows:
                    reference["causes"] = [dict(cause) for cause in cause_rows]
                    for cause in cause_rows:
                        if cause.get("category"):
                            item["cause_categories"].add(str(cause["category"]))
                        if cause.get("pattern"):
                            item["cause_patterns"].add(str(cause["pattern"]))
                observations = row.get("point_observations")
                if include_source_details and isinstance(observations, list):
                    reference["assessment"]["point_observations"] = [
                        dict(observation) for observation in observations
                        if observation["stable_key"] == point
                    ]
                if reference not in item["source_question_refs"]:
                    item["source_question_refs"].append(reference)
                reasons = _actionable_reasons(
                    ";".join(
                        value
                        for value in (
                            str(row.get("deduction_reason") or "").strip(),
                            str(row.get("error_summary") or "").strip(),
                        )
                        if value
                    )
                )
                item["actionable_reasons"] = _unique(
                    [*item["actionable_reasons"], *reasons]
                )
                for tag_type in ("sub_skill", "method", "ability", "model", "prerequisite"):
                    for tag_value in tags.get(tag_type, []):
                        text = str(tag_value or "").strip()
                        if text and text not in item["tag_context"][tag_type]:
                            item["tag_context"][tag_type].append(text)
                primary_summary = str(row.get("error_summary") or "").strip()
                if primary_summary:
                    item["primary_errors"][primary_summary] += 1
                for secondary in row.get("secondary_errors") or []:
                    if not isinstance(secondary, Mapping):
                        continue
                    secondary_summary = str(secondary.get("summary") or "").strip()
                    if secondary_summary:
                        item["secondary_errors"][secondary_summary] += 1

        try:
            hierarchy_resolver: CurrentKnowledgeResolver | None = (
                CurrentKnowledgeResolver.from_active_database(
                    self.question_bank_db_path
                )
            )
        except (
            CurrentKnowledgeUnavailable,
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ):
            hierarchy_resolver = None

        student_profiles: list[dict[str, Any]] = []
        for student in students:
            student_id = str(student["id"])
            weak_points: list[dict[str, Any]] = []
            for raw_item in grouped_by_student.get(student_id, {}).values():
                item = dict(raw_item)
                score_sum = float(item.pop("score_sum"))
                full_score_sum = float(item.pop("full_score_sum"))
                tag_context = {
                    tag_type: list(values)
                    for tag_type, values in item.pop("tag_context").items()
                    if values
                }
                primary_errors = dict(sorted(item.pop("primary_errors").items()))
                secondary_errors = dict(sorted(item.pop("secondary_errors").items()))
                cause_categories = sorted(item.pop("cause_categories"))
                cause_patterns = sorted(item.pop("cause_patterns"))
                references = sorted(
                    item["source_question_refs"],
                    key=lambda ref: (ref["session_id"], ref["question_id"]),
                )
                weak_points.append(
                    {
                        **item,
                        "mastery": round(score_sum / full_score_sum, 4)
                        if full_score_sum > 0
                        else 1.0,
                        "score_sum": round(score_sum, 4),
                        "full_score_sum": round(full_score_sum, 4),
                        "evidence_count": len(references),
                        "exam_count": len({ref["session_id"] for ref in references}),
                        "source_question_refs": references,
                        "tag_context": tag_context,
                        "error_counts": {
                            "primary": primary_errors,
                            "secondary": secondary_errors,
                        },
                        # 新错因体系输出：7 类大类 + 具体错法名；旧 error_types
                        # 词表由 mastery_adapter 继续按兼容输入换算。
                        "error_categories": cause_categories,
                        "error_patterns": cause_patterns,
                    }
                )
            self._apply_governed_hierarchy(
                weak_points,
                resolver=hierarchy_resolver,
            )
            weak_points = self._dedupe_governed_points(weak_points)
            weak_points.sort(key=lambda item: (
                item.get("parent_knowledge_point") or item["knowledge_point"],
                item["knowledge_point"],
            ))
            student_profiles.append(
                {
                    "student_id": student_id,
                    "student_code": str(student.get("student_code") or ""),
                    "student_name": str(student.get("name") or ""),
                    "class_id": str(student.get("class_name") or ""),
                    **resolved.score_profiles.get(student_id, {}),
                    "weak_points": weak_points,
                }
            )

        group_weak_points: list[dict[str, Any]] = []
        aggregated_mastery: dict[str, Any] = {}
        knowledge_catalog: list[dict[str, Any]] = []
        knowledge_associations: list[dict[str, Any]] = []
        try:
            resolver = hierarchy_resolver
            if resolver is None:
                raise CurrentKnowledgeUnavailable(
                    "current knowledge resolver is unavailable"
                )
            mastery_profile = {
                "scope": {"student_ids": student_ids},
                "exam_scope": dict(exam_scope),
                "students": student_profiles,
                "_mastery_session_times": self.mastery_session_times(
                    exam_scope=exam_scope
                ),
            }
            from question_bank.mastery.model import build_exam_observations
            mastery_profile["_mastery_population"] = (set(student_ids) == {str(s["id"]) for s in self.db.students.list_students()})
            mastery_profile["_mastery_observations"] = build_exam_observations(evidence_rows, resolver, mastery_profile["_mastery_session_times"])
            selected_students = set(student_ids)
            per_student_mastery = {identity: item for identity, item in self.semester_mastery(mastery_profile).items() if identity[0] in selected_students}
            self._merge_current_mastery(
                student_profiles,
                per_student_mastery,
                resolver=resolver,
            )
            aggregated_mastery = aggregate_current_mastery(per_student_mastery)
            group_weak_points = self._group_mastery_payload(
                student_profiles,
                aggregated_mastery,
                resolver=resolver,
            )
            parent_by_child = {
                relation.source_key: relation.target_key
                for relation in resolver.relations
                if relation.relation_type == "parent"
            }
            node_by_key = {node.stable_key: node for node in resolver.nodes}
            knowledge_catalog = [
                {
                    "knowledge_key": node.stable_key,
                    "knowledge_point": node.display_name,
                    "definition": node.definition,
                    "parent_knowledge_key": parent_by_child.get(node.stable_key),
                    "parent_knowledge_point": (
                        node_by_key[parent_by_child[node.stable_key]].display_name
                        if node.stable_key in parent_by_child else None
                    ),
                }
                for node in resolver.nodes
            ]
            from question_bank.recommendation.target_matching import (
                knowledge_skill_associations,
                load_question_facets,
                target_index,
            )
            facets_index = target_index(resolver)
            for item in knowledge_catalog:
                item["node_kind"] = facets_index.get(item["knowledge_key"], {}).get("kind", "topic")
                item["target_kind"] = target_kind_for_key(item["knowledge_key"], resolver, profile_volume)
            if include_source_details and "skill" in chapter_target_kinds(
                    resolver, profile_volume
                ).values():
                # Skill-knowledge edges only feed skill-rule chapters; type and
                # knowledge volumes drop them through is_training_target anyway.
                knowledge_associations = knowledge_skill_associations(
                    load_question_facets(self.question_bank_db_path, resolver,
                                         include_practice_observations=False)
                )
        except (
            CurrentKnowledgeUnavailable,
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ):
            warnings.append("当前掌握度参数或训练证据暂不可用，已保留考试证据诊断。")

        coverage_missing: dict[str, str] = {}
        covered_items = 0
        total_items = 0
        for session_id, projection in projection_by_session.items():
            if session_id not in set(session_ids):
                continue
            covered_items += projection.covered_items
            total_items += projection.total_items
            coverage_missing.update(projection.missing_items)
        if coverage_missing:
            warnings.append(
                f"知识图谱仅覆盖 {covered_items}/{total_items} 个评分题；缺失题目已列出。"
            )
        if not any(item["weak_points"] for item in student_profiles):
            warnings.append("所选范围内没有已关联且带知识点标签的诊断证据。")

        normalized_scope = resolved.normalized_scope(scope)
        result = {
            "scope": normalized_scope,
            "exam_scope": {
                "mode": str(exam_scope.get("mode") or "current"),
                **({"curriculum_volume_id": str(exam_scope.get("curriculum_volume_id") or "")}
                   if exam_scope.get("mode") == "semester" else {}),
                "session_ids": session_ids,
                "sessions": [
                    {
                        "session_id": int(item["id"]),
                        "session_name": str(item.get("session_name") or ""),
                    }
                    for item in sessions
                ],
            },
            "students": student_profiles,
            "group_weak_points": group_weak_points,
            "knowledge_catalog": knowledge_catalog,
            "knowledge_associations": knowledge_associations,
            "coverage": {
                "covered_items": covered_items,
                "total_items": total_items,
                "missing_items": coverage_missing,
            },
            "confirmed_concept_ids": [],
            "suggested_terms": [],
            "unmapped_terms": [],
            "warnings": _unique(warnings),
            "diagnosis_identity": "question_tag",
            # An unconverted volume keeps its existing skill presentation.
            "target_kind": chapter_target_kind(hierarchy_resolver, profile_volume),
            "chapter_target_kinds": chapter_target_kinds(hierarchy_resolver, profile_volume),
            # Internal read-only contract. The API excludes root underscore
            # fields; source point text is not added to public references.
            "_exam_source_metadata": {
                str(session_id): {item.item_ref: dict(item.source_practice_metadata)
                    for item in projection.items if item.source_practice_metadata}
                for session_id, projection in projection_by_session.items()
            },
        }
        for student in student_profiles:
            for point in student["weak_points"]:
                point["target_kind"] = target_kind_for_key(point["knowledge_key"], hierarchy_resolver, profile_volume)
        for point in group_weak_points:
            point["target_kind"] = target_kind_for_key(point["knowledge_key"], hierarchy_resolver, profile_volume)
        if include_source_details and result["target_kind"] in {"type", "mixed"}:
            result["_type_class_question_totals"] = self._type_class_question_totals(
                student_profiles, evidence_rows,
            )
        return result, dict(aggregated_mastery)

    def _type_class_question_totals(
        self, profiles: list[dict[str, Any]], evidence_rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, dict[str, dict[str, float | int]]]]:
        """Final-score totals for each target student's complete class.

        Only the exam items actually referenced by the selected type targets
        are included. Reuse selected students' rows; read missing classmates
        through the existing grading repository, without expanding targets.
        This measures scored exam items, not precise mastery observations.
        """
        needed: dict[str, set[tuple[int, str]]] = defaultdict(set)
        for student in profiles:
            class_id = str(student.get("class_id") or "")
            if not class_id.strip():
                continue
            for point in student.get("weak_points") or ():
                if not is_type_key(point.get("knowledge_key")):
                    continue
                for ref in point.get("source_question_refs") or ():
                    if not isinstance(ref, Mapping) or ref.get("source_kind") == "training":
                        continue
                    session_id = _safe_int(ref.get("session_id"))
                    question_id = str(ref.get("question_id") or "")
                    if session_id > 0 and question_id:
                        needed[class_id].add((session_id, question_id))
        if not needed:
            return {}
        selected = {str(student["student_id"]) for student in profiles}
        extra_students = tuple(str(student["id"]) for student in self.db.students.list_students()
            if str(student.get("class_name") or "") in needed and str(student["id"]) not in selected)
        rows = evidence_rows
        if extra_students:
            rows = [*rows, *self.db.results.get_active_assessment_evidence(
                student_ids=extra_students,
                session_ids=tuple(sorted({session for pairs in needed.values() for session, _ in pairs})),
            )]
        totals: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {}
        seen: set[tuple[str, int, str]] = set()
        for row in rows:
            class_id = str(row.get("class_name") or "")
            session_id, question_id = _safe_int(row.get("session_id")), str(row.get("question_id") or "")
            if (session_id, question_id) not in needed.get(class_id, ()):
                continue
            state = row.get("assessment_state") or {}
            if row.get("teacher_final_revision") is None and (
                state.get("need_review") is True or state.get("answer_discarded_by_smudge") is True
            ):
                continue
            full_value = row.get("teacher_final_max_score")
            if full_value is None:
                full_value = row.get("full_score")
            try:
                score, full = float(row.get("score_awarded")), float(full_value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score) or not math.isfinite(full) or full <= 0:
                continue
            identity = (str(row.get("student_id")), session_id, question_id)
            if identity in seen:
                continue
            seen.add(identity)
            bucket = totals.setdefault(class_id, {}).setdefault(str(session_id), {}).setdefault(
                question_id, {"score_sum": 0.0, "full_score_sum": 0.0, "evidence_student_count": 0},
            )
            bucket["score_sum"] += min(max(score, 0.0), full)
            bucket["full_score_sum"] += full
            bucket["evidence_student_count"] += 1
        return totals

    def graded_activities(self, student_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Read actual participation independently of tag coverage or score loss."""
        ids = tuple(student_ids)
        if not ids:
            return []
        sessions = {str(item["id"]): item for item in self.db.sessions.list_grading_sessions()
                    if not item.get("is_deleted")}
        activities: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.db.results.get_active_assessment_evidence(student_ids=ids):
            if row.get("score_awarded") is None:
                continue
            sid, session = str(row["student_id"]), str(row["session_id"])
            if session not in sessions:
                continue
            activities.setdefault((sid, session), {
                "student_id": sid, "activity_id": "exam:" + session,
                "session_id": session, "occurred_at": str(sessions[session].get("created_at") or ""),
            })
        return sorted(activities.values(), key=lambda item: (item["student_id"], item["occurred_at"], item["activity_id"]))

    def _merge_current_mastery(
        self,
        student_profiles: list[dict[str, Any]],
        mastery: Mapping[tuple[str, str], Any],
        *,
        resolver: CurrentKnowledgeResolver,
    ) -> None:
        parent_by_child = {
            relation.source_key: relation.target_key
            for relation in resolver.relations
            if relation.relation_type == "parent"
        }
        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for child, parent in parent_by_child.items():
            children_by_parent[parent].append(child)
        node_by_key = {node.stable_key: node for node in resolver.nodes}
        layouts = {}
        for stable_key, node in node_by_key.items():
            parent = node_by_key.get(parent_by_child.get(stable_key))
            layouts[stable_key] = (
                node.display_name,
                parent.stable_key if parent else None,
                parent.display_name if parent else None,
                'parent_summary' if stable_key in children_by_parent else 'child' if parent else 'root',
                tuple(sorted(children_by_parent.get(stable_key, ()))),
            )
        mastery_by_student = defaultdict(list)
        for (student_id, stable_key), current in mastery.items():
            mastery_by_student[student_id].append((stable_key, current))
        for student in student_profiles:
            student_id = str(student["student_id"])
            points = {
                str(item["knowledge_key"]): item
                for item in student.get("weak_points") or []
            }
            for point in points.values():
                point.update(mastery=None, evidence_count=0, effective_weight=0.0,
                             direct_evidence_count=0, child_evidence_count=0)
            for stable_key, current in mastery_by_student.get(student_id, ()):
                layout = layouts.get(stable_key)
                if layout is None:
                    continue
                name, parent_key, parent_name, hierarchy_kind, children = layout
                point = points.get(stable_key)
                if point is None:
                    point = {
                        "knowledge_key": stable_key,
                        "knowledge_point": name,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "deduction_count": 0,
                        "exam_count": 0,
                        "source_question_refs": [],
                        "actionable_reasons": [],
                        "tag_context": {},
                        "error_counts": {"primary": {}, "secondary": {}},
                        "parent_knowledge_key": parent_key,
                        "parent_knowledge_point": parent_name,
                    }
                    student["weak_points"].append(point)
                    points[stable_key] = point
                point.update({
                    "interval_low": current.interval_low,
                    "interval_high": current.interval_high,
                    "tier": current.tier,
                    "observation_count": current.observation_count,
                    "full_correct_count": current.full_correct_count,
                    "recent_trend": current.recent_trend,
                    "parameter_version": current.parameter_version,
                    "tier_counts": dict(current.tier_counts),
                    "logit_mean": current.logit_mean,
                    "logit_sd": current.logit_sd,
                    "difficulty_slope": current.difficulty_slope,
                    "mastery": float(current.value) if current.value is not None else None,
                    "evidence_count": int(current.evidence_count),
                    "effective_weight": float(current.effective_weight),
                    "hierarchy_kind": hierarchy_kind,
                    "child_knowledge_keys": list(children),
                    "direct_evidence_count": int(current.direct_evidence_count),
                    "precise_training_evidence_count": int(current.precise_training_evidence_count),
                    "child_evidence_count": max(
                        int(current.evidence_count) - int(current.direct_evidence_count),
                        0,
                    ),
                })
            student["weak_points"].sort(key=lambda item: (
                item.get("parent_knowledge_point") or item["knowledge_point"],
                item.get("hierarchy_kind") != "parent_summary",
                item["knowledge_point"],
            ))

    def _group_mastery_payload(
        self,
        student_profiles: list[dict[str, Any]],
        mastery: Mapping[str, Any],
        *,
        resolver: CurrentKnowledgeResolver,
    ) -> list[dict[str, Any]]:
        exemplars = {
            str(point["knowledge_key"]): point
            for student in student_profiles
            for point in student.get("weak_points") or []
        }
        node_by_key = {node.stable_key: node for node in resolver.nodes}
        result: list[dict[str, Any]] = []
        for stable_key, current in mastery.items():
            if current.value is None:
                continue
            exemplar = exemplars.get(stable_key, {})
            metadata = current.to_dict()
            result.append({
                "knowledge_key": stable_key,
                "knowledge_point": str(
                    exemplar.get("knowledge_point")
                    or node_by_key[stable_key].display_name
                ),
                **{field: metadata[field] for field in ("interval_low", "interval_high", "tier", "observation_count", "full_correct_count", "recent_trend", "parameter_version", "tier_counts")},
                "mastery": float(current.value),
                "score_sum": 0.0,
                "full_score_sum": 0.0,
                "deduction_count": 0,
                "evidence_count": int(current.evidence_count),
                "effective_weight": float(current.effective_weight),
                "exam_count": 0,
                "source_question_refs": [],
                "actionable_reasons": [],
                "tag_context": {},
                "error_counts": {"primary": {}, "secondary": {}},
                "hierarchy_kind": exemplar.get("hierarchy_kind", "root"),
                "parent_knowledge_key": exemplar.get("parent_knowledge_key"),
                "parent_knowledge_point": exemplar.get("parent_knowledge_point"),
                "child_knowledge_keys": list(exemplar.get("child_knowledge_keys") or []),
                "direct_evidence_count": int(current.direct_evidence_count),
                "child_evidence_count": max(
                    int(current.evidence_count) - int(current.direct_evidence_count),
                    0,
                ),
            })
        result.sort(key=lambda item: (item["mastery"], item["knowledge_point"]))
        return result

    def _apply_governed_hierarchy(
        self,
        weak_points: list[dict[str, Any]],
        *,
        resolver: CurrentKnowledgeResolver | None,
    ) -> None:
        if resolver is None:
            for item in weak_points:
                item.update({
                    "hierarchy_kind": "root",
                    "parent_knowledge_key": None,
                    "parent_knowledge_point": None,
                })
            return
        parent_by_child = {
            relation.source_key: relation.target_key
            for relation in resolver.relations
            if relation.relation_type == "parent"
        }
        node_by_key = {node.stable_key: node for node in resolver.nodes}
        for item in weak_points:
            targets = resolver.resolve(
                item.get("knowledge_point") or item.get("knowledge_key")
            )
            target = targets[0] if targets else None
            if target is None:
                item.update({
                    "hierarchy_kind": "root",
                    "parent_knowledge_key": None,
                    "parent_knowledge_point": None,
                })
                continue
            item["knowledge_key"] = target.stable_key
            item["knowledge_point"] = target.display_name
            parent_key = parent_by_child.get(target.stable_key)
            parent = node_by_key.get(parent_key) if parent_key else None
            item.update({
                "hierarchy_kind": "child" if parent is not None else "root",
                "parent_knowledge_key": parent.stable_key if parent else None,
                "parent_knowledge_point": parent.display_name if parent else None,
            })

    def _dedupe_governed_points(
        self,
        weak_points: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in weak_points:
            key = str(item["knowledge_key"])
            target = grouped.setdefault(key, {
                **item,
                "source_question_refs": [],
                "actionable_reasons": [],
                "tag_context": {},
                "error_counts": {"primary": {}, "secondary": {}},
            })
            references = {
                (int(reference["session_id"]), str(reference["question_id"])): reference
                for reference in target["source_question_refs"]
            }
            for reference in item.get("source_question_refs") or []:
                references.setdefault(
                    (int(reference["session_id"]), str(reference["question_id"])),
                    reference,
                )
            target["source_question_refs"] = sorted(
                references.values(),
                key=lambda reference: (
                    int(reference["session_id"]), str(reference["question_id"])
                ),
            )
            target["actionable_reasons"] = _unique([
                *target["actionable_reasons"],
                *(item.get("actionable_reasons") or []),
            ])
            for tag_type, values in (item.get("tag_context") or {}).items():
                target["tag_context"][tag_type] = _unique([
                    *target["tag_context"].get(tag_type, []), *values,
                ])
            for level in ("primary", "secondary"):
                counts = target["error_counts"][level]
                for label, count in (item.get("error_counts") or {}).get(level, {}).items():
                    counts[label] = max(int(counts.get(label, 0)), int(count))
        result: list[dict[str, Any]] = []
        for item in grouped.values():
            references = item["source_question_refs"]
            score_sum = sum(float(reference.get("score_awarded") or 0) for reference in references)
            full_score_sum = sum(float(reference.get("full_score") or 0) for reference in references)
            item.update({
                "mastery": round(score_sum / full_score_sum, 4) if full_score_sum > 0 else 1.0,
                "score_sum": round(score_sum, 4),
                "full_score_sum": round(full_score_sum, 4),
                "evidence_count": len(references),
                "exam_count": len({int(reference["session_id"]) for reference in references}),
            })
            result.append(item)
        return result

    def mastery_session_times(
        self,
        *,
        exam_scope: Mapping[str, Any],
    ) -> dict[str, str]:
        """Expose evidence time only to the internal mastery adapter."""

        # The profile can contain explicitly labelled historical fallback evidence.
        # Return timestamps for all active sessions; only references already present
        # in the resolved profile are consumed by the mastery calculator.
        sessions = [
            item
            for item in self.db.sessions.list_grading_sessions()
            if not item.get("is_deleted")
        ]
        return {
            str(int(item["id"])): str(item.get("created_at") or "")
            for item in sessions
        }

    def tag_evidence(
        self,
        *,
        knowledge_point: str,
        student_ids: list[str] | tuple[str, ...] = (),
        session_ids: list[int] | tuple[int, ...] | None = None,
    ) -> list[dict[str, Any]]:
        target = str(knowledge_point or "").strip()
        if not target:
            raise ValueError("knowledge_point is required")
        selected_sessions = _int_list(session_ids)
        if session_ids is None:
            selected_sessions = [
                int(item["id"])
                for item in self.db.sessions.list_grading_sessions()
                if not item.get("is_deleted")
            ]
        if not selected_sessions:
            return []
        rows = self._projected_tag_evidence(
            student_ids=_text_list(student_ids),
            session_ids=selected_sessions,
            projection_by_session=self._tag_projections(selected_sessions),
        )
        return [
            row
            for row in rows
            if target in row.get("question_tags", {}).get("knowledge_point", [])
        ]

    def assembly_recent_question_ids(self, recommendations, context: Mapping[str, Any], rules: Mapping[str, Any]) -> set[int]:
        """Use the same roster and graded-activity contract as recommendations."""
        if not context.get('class_ids') or not rules.get('recent_activity_count', 3):
            return set()
        # The scan below walks every graded activity plus three question-bank
        # domains; draft saves call it on each GET/PUT, so memoize by the data
        # versions of both databases and return copies callers may mutate.
        grading_identity = (
            self.cache_identity[:3]
            if self.cache_identity and len(self.cache_identity) == 6
            else _path_generation(self.grading_db_path)
        )
        key = (
            tuple(grading_identity),
            _path_generation(Path(recommendations.db_path), _ASSEMBLY_QB_VERSION_DOMAINS),
            str(Path(recommendations.data_root).resolve())
                if getattr(recommendations, 'data_root', None) else "",
            tuple(sorted(str(c) for c in context['class_ids'])),
            int(rules.get('recent_activity_count', 3)),
            str(rules.get('purpose', 'handout')),
        )
        with _ASSEMBLY_RECENT_LOCK:
            cached = _ASSEMBLY_RECENT_CACHE.get(key)
            if cached is not None:
                _ASSEMBLY_RECENT_CACHE.move_to_end(key)
                return set(cached)
        students = [{'student_id': str(s['id'])} for s in self.db.students.list_students()
                    if s.get('class_name') in context['class_ids']]
        result = frozenset(recommendations.current_exam_question_ids({'students': students},
            graded_activities=self.graded_activities(s['student_id'] for s in students),
            recent_activity_count=rules.get('recent_activity_count', 3), purpose=rules.get('purpose', 'handout')))
        with _ASSEMBLY_RECENT_LOCK:
            _ASSEMBLY_RECENT_CACHE[key] = result
            _ASSEMBLY_RECENT_CACHE.move_to_end(key)
            while len(_ASSEMBLY_RECENT_CACHE) > _ASSEMBLY_RECENT_CACHE_LIMIT:
                _ASSEMBLY_RECENT_CACHE.popitem(last=False)
        return set(result)

    def assembly_exam_questions(self, *, class_ids: list[str], volume_id: str) -> dict[str, Any]:
        """Read scored exam items through the existing evidence boundary; no persistence."""
        from backend.config_generation.contract import iter_effective_rubric_item_refs
        from backend.scan_grading.grading_completeness import combined_scoring_groups
        from question_bank.current_knowledge import CurrentKnowledgeResolver
        classes = set(class_ids)
        students = {str(s["id"]): s for s in self.db.students.list_students()
                    if s.get("class_name") in classes}
        sessions = [s for s in self.db.sessions.list_grading_sessions()
                    if not s.get("is_deleted") and s.get("curriculum_volume_id") == volume_id]
        if not students or not sessions:
            try:
                resolver = CurrentKnowledgeResolver.from_active_database(self.question_bank_db_path)
            except (CurrentKnowledgeUnavailable, OSError, sqlite3.Error, TypeError, ValueError):
                resolver = None
            return {"student_count": len(students), "exams": [],
                    "target_kind": chapter_target_kind(resolver, volume_id),
                    "chapter_target_kinds": chapter_target_kinds(resolver, volume_id)}
        ids = [int(s["id"]) for s in sessions]
        projections = self._tag_projections(ids)
        causes = self._error_cause_index(ids)
        cause_availability = self._assembly_cause_availability_index(ids)
        rows = self.db.results.get_active_assessment_rows(student_ids=[int(s) for s in students], session_ids=ids)
        score_maps = {sid: self.db.results._load_rubric_maps_for_session(sid)['score'] for sid in ids}
        by_item: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            if row.get("score_awarded") is not None:
                row = {**row, 'full_score': row.get('teacher_final_max_score') if row.get('teacher_final_max_score') is not None else score_maps[int(row['session_id'])].get(str(row['question_id']), 0)}
                if float(row['full_score'] or 0) <= 0:
                    continue
                by_item.setdefault((int(row["session_id"]), str(row["question_id"])), {})[str(row["student_id"])] = row
        resolver = CurrentKnowledgeResolver.from_active_database(self.question_bank_db_path)
        from question_bank.recommendation.target_matching import target_index
        from question_bank.taxonomy.curriculum_catalog import curriculum_volume
        anchors = target_index(resolver)
        volume = curriculum_volume(volume_id=volume_id)
        # Questions can be linked to skills anchored in another textbook
        # volume; shortlist targets only exist inside this one. An unknown
        # volume keeps the previous everything-eligible behaviour.
        volume_sections = (
            None if volume is None else {
                str(section["knowledge_id"])
                for chapter in volume["chapters"]
                for section in chapter["sections"]
            }
        )
        bank_ids = sorted({p.bank_question_id for projection in projections.values() for p in projection.items
                           if p.bank_question_id and p.bank_question_id > 0})
        from question_bank.services.question_read_service import _read_connection
        from question_bank.services.standard_difficulty import difficulty_level
        bank_difficulty: dict[int, int | None] = {}
        if bank_ids:
            connection_scope = (nullcontext(self.question_bank_connection)
                                if self.question_bank_connection is not None
                                else _read_connection(self.question_bank_db_path))
            with connection_scope as connection:
                for offset in range(0, len(bank_ids), 500):
                    batch = bank_ids[offset:offset + 500]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""SELECT q.id, q.difficulty
                            FROM questions q LEFT JOIN papers p ON p.id = q.paper_id
                            WHERE q.id IN ({placeholders})
                              AND COALESCE(q.is_deleted, 0) = 0
                              AND COALESCE(p.import_status, '') <> 'deleted'""",
                        batch,
                    ).fetchall()
                    bank_difficulty.update({int(row["id"]): difficulty_level(row["difficulty"]) for row in rows})
        categories = ("概念理解", "计算与化简", "审题与条件", "方法与思路", "过程与依据", "书写与规范", "未作答")
        exams = []
        for session in sorted(sessions, key=lambda s: (str(s.get("created_at") or ""), int(s["id"])), reverse=True):
            sid = int(session["id"])
            projection = {p.item_ref: p for p in projections[sid].items}
            questions = []
            participants: set[str] = set()
            totals: dict[str, float] = {}
            sid_rubric = self.db.results._load_session_rubric(sid)
            sid_rubric_doc = (
                sid_rubric.get("rubric")
                if isinstance(sid_rubric, dict) and isinstance(sid_rubric.get("rubric"), dict)
                else sid_rubric
            )
            member_group = {
                member: group
                for group in combined_scoring_groups(sid_rubric_doc)
                for member in group["question_ids"]
            }
            for qid, _parent, question, item in iter_effective_rubric_item_refs(sid_rubric):
                scored = by_item.get((sid, qid), {})
                if qid in member_group:
                    # 合并给分组成员不展示单独得分率（只用极端学生会高估）；
                    # 成员明细仍计入本场总分。
                    for student, row in scored.items():
                        participants.add(student)
                        maximum = float(row.get("teacher_final_max_score") or row.get("full_score") or 0)
                        awarded = min(max(float(row["score_awarded"]), 0), maximum)
                        totals[student] = totals.get(student, 0.0) + awarded
                    continue
                if not scored:
                    continue
                participants.update(scored)
                per_class = []
                rate_sum = 0.0
                lost: set[str] = set()
                classified: set[str] = set()
                availability = cause_availability.get((sid, qid), {})
                cause_status = availability.get("status", "missing")
                members = {c: set() for c in categories}
                for name in sorted(classes):
                    values = [(student, row) for student, row in scored.items() if students[student].get("class_name") == name]
                    full = sum(float(r.get("teacher_final_max_score") or r.get("full_score") or 0) for _, r in values)
                    score = class_rate_sum = 0.0
                    for student, row in values:
                        maximum = float(row.get("teacher_final_max_score") or row.get("full_score") or 0)
                        awarded = min(max(float(row["score_awarded"]), 0), maximum)
                        score += awarded
                        class_rate_sum += awarded / maximum if maximum > 0 else 0
                        totals[student] = totals.get(student, 0) + awarded
                        if awarded < maximum:
                            lost.add(student)
                        records = causes.get((sid, int(student), qid))
                        for record in records or []:
                            if record.get("category") in members:
                                members[record["category"]].add(student)
                                if awarded < maximum:
                                    classified.add(student)
                    if values:
                        per_class.append({"class_id": name, "student_count": len(values), "class_rate": round(class_rate_sum / len(values), 4)})
                    rate_sum += class_rate_sum
                unclassified_reasons = Counter(
                    availability.get("reasons_by_student", {}).get(int(student), "missing")
                    for student in lost - classified
                )
                projected = projection.get(qid)
                keys = list(projected.tags.get("knowledge_point", ())) if projected else []
                keys = [key for key in keys if is_training_target(key, resolver, volume_id)]
                questions.append({"key": f"{sid}:{qid}", "session_id": sid, "question_id": qid,
                    "question_type": {"choice": "选择题", "fill_blank": "填空题", "multi_choice": "多选题"}.get(question.get("question_type"), "解答题"),
                    "full_score": max(float(r.get("full_score") or 0) for r in scored.values()),
                    "class_rate": round(rate_sum / len(scored), 4),
                    "class_rates": per_class, "student_count": len(scored),
                    "cause_category_counts": None if lost and not classified else [{"category": c, "count": len(members[c])} for c in categories],
                    "cause_unclassified_count": len(lost - classified),
                    "cause_availability": {
                        "status": cause_status,
                        "unclassified_by_reason": [
                            {"reason": reason, "count": unclassified_reasons[reason]}
                            for reason in ("review", "carry_forward", "positive_only", "missing")
                            if unclassified_reasons[reason]
                        ],
                    },
                    "bank_question_id": projected.bank_question_id if projected else None,
                    "difficulty": bank_difficulty.get(projected.bank_question_id) if projected else None,
                    "skill_keys": keys, "skills": [{"key": key,
                        "label": resolver.node(key).display_name if resolver.node(key) else key,
                        "target_kind": target_kind_for_key(key, resolver, volume_id),
                        "in_volume": volume_sections is None or anchors.get(key, {}).get("section") in volume_sections}
                        for key in keys],
                    "question_text": str(question.get("question_text") or item.get("question_text") or "")})
            # 只存组总分的学生：合并给分计入本场总分与参与人数。
            for student_id, entries in (
                self.db.results.get_session_combined_scores(sid).items()
            ):
                student_key = str(student_id)
                participants.add(student_key)
                for entry in entries.values():
                    totals[student_key] = (
                        totals.get(student_key, 0.0) + float(entry["score"])
                    )
            if participants:
                exams.append({"session_id": sid, "title": session.get("session_name") or session.get("exam_name") or session.get("name") or session.get("title") or f"考试 {sid}",
                    "date": str(session.get("created_at") or ""), "class_ids": sorted({students[s].get("class_name") for s in participants}),
                    "student_count": len(participants), "average_score": round(sum(totals.values()) / len(participants), 2), "questions": questions})
        return {"student_count": len(students), "exams": exams,
                "target_kind": chapter_target_kind(resolver, volume_id),
                    "chapter_target_kinds": chapter_target_kinds(resolver, volume_id)}

    def _assembly_cause_availability_index(
        self,
        session_ids: Iterable[int],
    ) -> dict[tuple[int, str], dict[str, Any]]:
        """按已保存归属展示组卷错因状态与待核对等非大类原因。"""
        from backend.class_analysis import (
            ClassAnalysisStateStore, _saved_evidence_members, _step_units,
            assemble_cause_data,
        )
        from backend.report_results import resolve_result_state

        store = ClassAnalysisStateStore(self.data_root / "reports")
        index: dict[tuple[int, str], dict[str, Any]] = {}
        for session_id in session_ids:
            sid = int(session_id)
            state = store.load(sid) or {}
            saved_questions = (state.get("cause_analysis") or {}).get("questions") or {}
            if not saved_questions:
                continue
            data = assemble_cause_data(self.db, sid, data_root=self.data_root)
            for qid, saved in saved_questions.items():
                if not isinstance(saved, dict) or not isinstance(saved.get("result"), dict):
                    continue
                status = resolve_result_state(saved, "causes")["status"]
                reasons: dict[int, str] = {}
                index[(sid, str(qid))] = {"status": status, "reasons_by_student": reasons}
                source = saved.get("input")
                if not isinstance(source, dict) or not isinstance(source.get("evidence"), list):
                    continue
                members_by_evidence = _saved_evidence_members(data, source)
                units = _step_units(source)
                result = saved["result"]
                members: dict[str, set[str]] = {
                    "review": set(), "carry_forward": set(), "positive_only": set(),
                }
                for group in result.get("groups") or []:
                    kind = group.get("kind")
                    if kind not in ("review", "carry_forward"):
                        continue
                    refs = set(group.get("evidence_ids") or [])
                    refs.update(ref for variant in group.get("manifestations") or []
                                for ref in variant.get("evidence_ids") or [])
                    members[kind].update(units[ref][0] if ref in units else ref for ref in refs)
                for field, reason in (("uncertain_ids", "review"), ("positive_ids", "positive_only")):
                    members[reason].update(
                        units[ref][0] if ref in units else ref for ref in result.get(field) or []
                    )
                for reason in ("positive_only", "carry_forward", "review"):
                    for ref in members[reason]:
                        for student_id in members_by_evidence.get(ref, ()):
                            reasons[int(student_id)] = reason
        return index

    def _error_cause_index(
        self,
        session_ids: Iterable[int],
    ) -> dict[tuple[int, int, str], list[dict[str, Any]]]:
        """{(session_id, student_id, question_id): [{category, pattern, kind}]}。

        与报告共用已存记录读取；已有归类继续参与统计。
        """
        reports_dir = self.data_root / "reports"
        if not reports_dir.is_dir():
            return {}
        index: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
        for session_id in session_ids:
            students = self._session_error_records(
                int(session_id), reports_dir,
            )
            for student_id, questions in students.items():
                for question_id, rows in questions.items():
                    key = (int(session_id), student_id, str(question_id))
                    seen = index.setdefault(key, [])
                    for row in rows:
                        signature = (row.get("kind"), row.get("category"), row.get("pattern"))
                        existing = next(
                            (item for item in seen
                             if (item.get("kind"), item.get("category"), item.get("pattern")) == signature),
                            None,
                        )
                        if existing is None:
                            existing = {name: row.get(name) for name in
                                        ("kind", "category", "pattern", "pattern_status", "step_id")}
                            existing["step_ids"] = (
                                [row["step_id"]] if row.get("step_id") else [])
                            seen.append(existing)
                        elif row.get("step_id") and row["step_id"] not in existing["step_ids"]:
                            existing["step_ids"].append(row["step_id"])
        return index

    def _session_error_records(
        self,
        session_id: int,
        reports_dir: Path,
    ) -> dict:
        """按场次复用已存归类；相关数据库提交或保存文件修改后刷新。"""
        changes = self._session_error_change_stamp(session_id)
        if changes is None:
            return self._read_session_error_records(session_id, reports_dir)
        memo_key = (str(self.grading_db_path), str(self.data_root), session_id)
        with _SESSION_ERROR_LOCK:
            cached = _SESSION_ERROR_MEMO.get(memo_key)
            if cached is not None and cached[0] == changes:
                _SESSION_ERROR_MEMO.move_to_end(memo_key)
                return cached[1]
        store = persistent_entry_store(
            self.data_root / "reports" / ".training_diagnosis" / "sessions",
            max_entries=_SESSION_ERROR_MAX_ENTRIES,
            max_bytes=_SESSION_ERROR_MAX_BYTES,
            compact=True,
        )
        pkey = ("session-error-records", session_id)
        records = store.get(pkey, changes)
        if not isinstance(records, dict):
            records = self._read_session_error_records(session_id, reports_dir)
            store.put(pkey, changes, records)
        with _SESSION_ERROR_LOCK:
            _SESSION_ERROR_MEMO[memo_key] = (changes, records)
            _SESSION_ERROR_MEMO.move_to_end(memo_key)
            while len(_SESSION_ERROR_MEMO) > _SESSION_ERROR_MAX_ENTRIES:
                _SESSION_ERROR_MEMO.popitem(last=False)
        return records

    def _read_session_error_records(self, session_id: int, reports_dir: Path) -> dict:
        from backend import class_analysis
        try:
            state = class_analysis.ClassAnalysisStateStore(reports_dir).load(session_id)
            envelopes = (state or {}).get("error_records") or {}
            cause = (state or {}).get("cause_analysis") or {}
            questions = (cause.get("questions") or {}) if isinstance(cause, dict) else None
            question_ids = (set(envelopes) | set(questions)
                            if isinstance(envelopes, dict) and isinstance(questions, dict) else ())
            if question_ids and not state.get("read_error") and all(
                isinstance(envelopes.get(question_id), dict)
                and isinstance(envelopes[question_id].get("records"), list)
                for question_id in question_ids
            ):
                from backend.session_analysis import assemble_session_analysis
                data = assemble_session_analysis(self.db, session_id, data_root=self.data_root,
                    page_only=True, include_answer_evidence=False)
                sources = []
                prepared = class_analysis._index_saved_error_records(state, data, sources)
                out = {}
                for student in data.students:
                    mapped = class_analysis.student_error_map(state, student, sources, data, _prepared=prepared)
                    if mapped:
                        out[int(student.student_id)] = mapped
                class_analysis._enrich_error_record_skills(data, out, self.db)
                return out
        except Exception:
            pass
        return class_analysis.session_error_records(
            self.db, session_id, reports_dir, data_root=self.data_root,
        )

    def _session_error_change_stamp(self, session_id: int) -> tuple | None:
        try:
            from path_manager import resolve_stored_file_path
            origins = self.cache_identity or (
                *_path_generation(self.grading_db_path),
                *_path_generation(self.question_bank_db_path, _QB_VERSION_DOMAINS),
            )
            session = self.db.sessions.get_grading_session(int(session_id)) or {}
            files = []
            for field in ("rubric_path", "answer_key_path"):
                try:
                    resolved = resolve_stored_file_path(session.get(field), data_root=self.data_root)
                except (OSError, RuntimeError):
                    resolved = None
                files.append(_session_error_file_state(resolved))
            state = self.data_root / "reports" / ".class_analysis" / f"{int(session_id)}.json"
            return (tuple(origins), tuple(files), _session_error_file_state(state))
        except (OSError, sqlite3.Error, TypeError, ValueError, AttributeError, KeyError):
            return None

    def _tag_projections(
        self,
        session_ids: Iterable[int],
    ) -> dict[int, QuestionTagProjection]:
        service = QuestionTagProjectionService(
            self.question_bank_db_path,
            external_connection=self.question_bank_connection,
            data_root=self.data_root,
        )
        projections: dict[int, QuestionTagProjection] = {}
        for session_id in session_ids:
            rubric = self.db.results._load_session_rubric(int(session_id))
            projections[int(session_id)] = service.project_session(
                grading_session_id=int(session_id),
                rubric=rubric,
            )
        return projections

    def _projected_tag_evidence(
        self,
        *,
        student_ids: Iterable[str],
        session_ids: Iterable[int],
        projection_by_session: Mapping[int, QuestionTagProjection],
        include_diagnostics: bool = True,
    ) -> list[dict[str, Any]]:
        # The repository's empty filter means all exams. Here the resolved
        # range is explicit, and training evidence is merged separately.
        session_ids = tuple(session_ids)
        if not session_ids:
            return []
        projected_by_item = {
            (session_id, item.item_ref): item
            for session_id, projection in projection_by_session.items()
            for item in projection.items
        }
        extra = {} if include_diagnostics else {"include_diagnostics": False}
        rows = self.db.results.get_active_assessment_evidence(
            student_ids=tuple(student_ids),
            session_ids=session_ids,
            **extra,
        )
        from backend.scan_grading.grading_completeness import combined_scoring_groups

        combined_members: dict[int, dict[str, str]] = {}
        result: list[dict[str, Any]] = []
        for row in rows:
            key = (
                int(row.get("session_id") or 0),
                str(row.get("question_id") or ""),
            )
            projected = projected_by_item.get(key)
            if projected is None or not projected.is_graph_eligible:
                continue
            enriched = dict(row) if include_diagnostics else row
            sid = int(row.get("session_id") or 0)
            members = combined_members.get(sid)
            if members is None:
                rubric_doc = self.db.results._load_session_rubric(sid)
                rubric = (
                    rubric_doc.get("rubric")
                    if isinstance(rubric_doc, dict)
                    and isinstance(rubric_doc.get("rubric"), dict)
                    else rubric_doc
                )
                members = {
                    str(member): str(group["group_id"])
                    for group in combined_scoring_groups(
                        rubric if isinstance(rubric, dict) else {}
                    )
                    for member in group["question_ids"]
                }
                combined_members[sid] = members
            group_id = members.get(str(row.get("question_id") or ""))
            if group_id:
                enriched["combined_group_id"] = group_id
            enriched["bank_question_id"] = projected.bank_question_id
            enriched["source_practice_metadata"] = projected.source_practice_metadata
            from question_bank.solution_evidence.part_assessments import (
                exam_assessment_state,
            )
            enriched["assessment"] = exam_assessment_state(projected.assessment, row.get("assessment_state") or {},
                teacher_final=row.get("teacher_final_revision") is not None,
                teacher_score=float(row["score_awarded"]) if row.get("teacher_final_revision") is not None else None)
            teacher_records = _teacher_step_records(projected, row)
            if row.get("teacher_final_revision") is None or teacher_records is not None:
                records = teacher_records if teacher_records is not None else (row.get("assessment_state") or {}).get("step_assessments")
                observations = _step_point_observations(projected, records)
                contributions = _step_target_contributions(projected, records, observations=observations)
                if observations is not None:
                    enriched["point_observations"] = observations
                    enriched["target_contributions"] = contributions
                elif contributions:
                    enriched["target_contributions"] = contributions
                if teacher_records is not None:
                    enriched["assessment"].update(
                        granularity="step", reason="teacher_step_review", eligible=True,
                    )
                elif (not observations and not contributions and not projected.is_single_result
                      and enriched["assessment"].get("granularity") == "part"):
                    # A total for several independent points cannot locate
                    # which point failed. Keep its overall mastery input.
                    enriched["assessment"].update(
                        granularity="whole_question", reason="part_total_without_step_attribution",
                        evidence_weight=1.0 / max(len(projected.tags.get("knowledge_point", ())), 1),
                    )
            elif projected.is_single_result and enriched["assessment"].get("granularity") == "part":
                # The teacher's final score already judges the sole result;
                # superseded AI steps remain unused, even when they disagree.
                enriched["assessment"].update(
                    reason="teacher_final_single_result", evidence_weight=1.0,
                )
            else:
                # A teacher's final total does not supply new per-step facts.
                # Do not reintroduce the superseded AI step scores.
                enriched["assessment"].update(
                    granularity="whole_question", reason="teacher_final_without_step_attribution",
                    evidence_weight=1.0 / max(len(projected.tags.get("knowledge_point", ())), 1),
                )
            if row.get("teacher_final_max_score") is not None:
                enriched["full_score"] = row["teacher_final_max_score"]
            enriched["question_tags"] = {
                tag_type: list(values)
                for tag_type, values in projected.tags.items()
            } if include_diagnostics else projected.tags
            result.append(enriched)
        return sorted(
            result,
            key=lambda row: (
                int(row.get("session_id") or 0),
                int(row.get("student_id") or 0),
                str(row.get("question_id") or ""),
                int(row.get("result_id") or 0),
            ),
        )

    def _resolve_sessions(
        self,
        exam_scope: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        active = {
            int(item["id"]): item
            for item in self.db.sessions.list_grading_sessions()
            if not item.get("is_deleted")
        }
        mode = str(exam_scope.get("mode") or "current")
        requested = _int_list(exam_scope.get("session_ids"))
        if mode == "cross_exam":
            ids = sorted(active)
        elif mode == "manual":
            ids = [session_id for session_id in requested if session_id in active]
        elif mode == "current":
            if requested:
                ids = [requested[0]] if requested[0] in active else []
            else:
                ids = [max(active)] if active else []
        else:
            raise ValueError(f"unsupported exam scope mode: {mode}")
        missing = [session_id for session_id in requested if session_id not in active]
        if missing:
            warnings.append(f"已忽略不存在或已删除的考试：{', '.join(map(str, missing))}")
        if not ids:
            warnings.append("未选择可用考试。")
        return [active[session_id] for session_id in ids]

    def _resolve_students(
        self,
        scope: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        all_students = self.db.students.list_students()
        by_id = {str(item["id"]): item for item in all_students}
        mode = str(scope.get("mode") or "student")
        requested = _text_list(scope.get("student_ids"))
        if mode in {"student", "selected"}:
            selected = [by_id[student_id] for student_id in requested if student_id in by_id]
            missing = [student_id for student_id in requested if student_id not in by_id]
            if missing:
                warnings.append(f"已忽略不存在的学生：{', '.join(missing)}")
        elif mode == "class":
            class_id = str(scope.get("class_id") or scope.get("class_name") or "").strip()
            selected = [
                item
                for item in all_students
                if not class_id or str(item.get("class_name") or "") == class_id
            ]
            if requested:
                requested_set = set(requested)
                selected = [item for item in selected if str(item["id"]) in requested_set]
        else:
            raise ValueError(f"unsupported student scope mode: {mode}")
        if not selected:
            warnings.append("未选择可用学生。")
        return selected

    def _score_rates(
        self,
        students: list[dict[str, Any]],
        session_ids: list[int],
    ) -> dict[str, float | None]:
        student_id_by_code = {
            str(item.get("student_code") or ""): str(item["id"])
            for item in students
        }
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for session_id in session_ids:
            for result in self.db.results.get_session_results(session_id):
                student_id = student_id_by_code.get(str(result.get("student_code") or ""))
                if student_id is None:
                    continue
                totals[student_id][0] += _number(result.get("student_score"))
                totals[student_id][1] += _number(result.get("total_score"))
        fallback = {
            str(item["student_id"]): _number(item.get("avg_score_rate")) / 100
            for item in self.db.results.get_active_student_score_rates()
        }
        return {
            str(student["id"]): (
                round(totals[str(student["id"])][0] / totals[str(student["id"])][1], 4)
                if totals[str(student["id"])][1] > 0
                else fallback.get(str(student["id"]))
            )
            for student in students
        }

_GRADING_VERSION_DOMAINS = ("results",)
_QB_VERSION_DOMAINS = ("materials", "training_records")
# _recent_question_ids also reads training_ops (personalized_paper_items,
# training_submissions, training_assessment_runs), so the assembly cache keys
# on all three question-bank domains.
_ASSEMBLY_QB_VERSION_DOMAINS = ("materials", "training_ops", "training_records")
_ASSEMBLY_RECENT_CACHE: OrderedDict[tuple, frozenset] = OrderedDict()
_ASSEMBLY_RECENT_LOCK = threading.Lock()
_ASSEMBLY_RECENT_CACHE_LIMIT = 32


def _path_generation(path: Path, domains: tuple[str, ...] = _GRADING_VERSION_DOMAINS) -> tuple[str, ...]:
    """Cache identity for a database file: resolved path plus per-category
    data versions when triggers cover the schema, else file identity and the
    commit generation (``PRAGMA data_version`` monitor) so real commits
    invalidate while read-only opens and checkpoints do not."""

    source = Path(path).resolve(strict=False)
    versions = data_versions(source)
    if versions is not None:
        return (
            str(source),
            "dv",
            ";".join(
                f"{domain}:{int(versions.get(domain, 0))}" for domain in domains
            ),
        )
    try:
        stat = source.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
    except OSError:
        identity = "missing"
    return (
        str(source),
        f"identity:{identity}",
        f"commits:{commit_generation(source)}",
    )


def _path_change_signature(path: Path) -> object:
    """Persisted-entry change marker: every domain version when covered,
    else the file stamp it replaces."""

    versions = data_versions(Path(path).resolve(strict=False))
    if versions is not None:
        return ("dv", tuple(sorted(versions.items())))
    return database_change_stamp(path)


def _dir_generation(path: Path) -> tuple[str, ...]:
    """目录改动通知：文件名集合 + 最新修改时间，用于 .class_analysis 状态目录。"""
    source = Path(path).resolve(strict=False)
    try:
        entries = sorted(
            (item.name, item.stat().st_mtime_ns)
            for item in source.glob("*.json")
        )
    except OSError:
        return (str(source), "missing")
    if not entries:
        return (str(source), "empty")
    names = ",".join(name for name, _ in entries)
    newest = max(mtime for _, mtime in entries)
    return (str(source), f"{len(entries)}:{names}:{newest}")


def _session_error_file_state(path: Path | None) -> tuple:
    """(path, dev, ino, size, mtime_ns) identity for one evidence file."""
    if path is None:
        return ("none",)
    try:
        info = Path(path).stat()
        return (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except OSError:
        return (str(path), "missing")


_SESSION_ERROR_MEMO: OrderedDict[tuple, tuple] = OrderedDict()
_SESSION_ERROR_LOCK = threading.Lock()


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _actionable_reasons(value: object) -> list[str]:
    text = str(value or "").replace("；", ";")
    return [
        item
        for item in _unique(part.strip() for part in text.split(";"))
        if item and item not in GENERIC_ERROR_REASONS
    ]


def _int_list(value: object) -> list[int]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    result: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result


def _text_list(value: object) -> list[str]:
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    return _unique(str(item or "").strip() for item in values if str(item or "").strip())


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _teacher_step_records(projected: Any, row: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Use only the teacher steps attached to the current final-score revision."""
    review = (row.get("assessment_state") or {}).get("teacher_review")
    if not isinstance(review, Mapping) or row.get("teacher_final_revision") is None:
        return None
    if review.get("revision") != row.get("teacher_final_revision") or review.get("scan_batch_id") != row.get("teacher_final_scan_batch_id"):
        return None
    records = review.get("steps")
    expected = getattr(projected, "steps", ())
    if not isinstance(records, list) or not expected or len(records) != len(expected):
        return None
    if review.get("score_awarded") != row.get("score_awarded"):
        return None
    normalized = []
    for step in expected:
        matches = [record for record in records if isinstance(record, Mapping)
                   and record.get("step_id") == step.get("step_id")
                   and (not step.get("part_id") or record.get("part_id") == step.get("part_id"))]
        if len(matches) != 1:
            return None
        record = matches[0]
        awarded, maximum = _number(record.get("score_awarded")), _number(step.get("step_score"))
        if maximum <= 0 or not awarded.is_integer() or awarded < 0 or awarded > maximum:
            return None
        if record.get("max_score") != maximum or set(record.get("evidence_point_ids") or []) != set(step.get("evidence_point_ids") or []):
            return None
        normalized.append({**record, "achievement": "full" if awarded == maximum else "none"})
    if sum(_number(record.get("score_awarded")) for record in normalized) != row.get("score_awarded"):
        return None
    return normalized


def _step_point_observations(projected: Any, step_assessments: object) -> list[dict[str, Any]] | None:
    """Frozen point identities, dependencies and weights; no missing-as-failure."""
    points = getattr(projected, "evidence_points", None) or {}
    links = getattr(projected, "point_links", None) or {}
    if not points or not isinstance(step_assessments, list) or not step_assessments:
        return None
    states: dict[str, tuple[float, float]] = {}
    for step in getattr(projected, "steps", ()):
        matches = [entry for entry in step_assessments if isinstance(entry, Mapping)
                   and str(entry.get("step_id") or "") == str(step.get("step_id") or "")
                   and (not entry.get("part_id") or not step.get("part_id")
                        or str(entry["part_id"]) == str(step["part_id"]))]
        if len(matches) != 1:
            continue
        record = matches[0]
        ids = list(dict.fromkeys(pid for pid in step.get("evidence_point_ids", ()) if pid in points))
        if not ids:
            continue
        score = _number(step.get("step_score"))
        achievement = str(record.get("achievement") or "").lower()
        if achievement in {"full", "equivalent"}:
            value = 1.0
        elif achievement == "none":
            # 沿用前步错误结果而判 none、但本步方法正确的步骤按达成计入。
            value = 1.0 if record.get("carried_error_from") else 0.0
        elif achievement == "partial" and len(ids) == 1:
            value = 0.5
        elif achievement == "partial" and score > 0:
            value = min(max(_number(record.get("score_awarded")) / score, 0.0), 1.0)
        else:
            continue
        for pid in ids:
            states[pid] = (value, score / len(ids))
    observed: list[tuple[str, float, float, list[Mapping[str, Any]]]] = []
    for pid, (achieved, score) in states.items():
        if achieved < 1.0 and any(states.get(dep, (None,))[0] != 1.0
                                  for dep in points[pid].get("depends_on", ())):
            continue
        direct = [link for link in links.get(pid, ())
                  if link.get("role") == "direct" and link.get("resolution_status") == "resolved"
                  and link.get("stable_key") and _number(link.get("weight")) > 0]
        if direct:
            observed.append((pid, achieved, score, direct))
    return [{"point_id": pid, "stable_key": str(link["stable_key"]),
             "achieved": achieved, "weight": float(link["weight"]) / len(observed),
             "score_weight": score * float(link["weight"])}
            for pid, achieved, score, direct in observed for link in direct]


def _step_target_contributions(
    projected: Any,
    step_assessments: object,
    *, observations=...,
) -> dict[str, tuple[float, float]]:
    """Per-step achievement allocated to evidence-linked targets.

    Returns ``{stable_key: (score_sum, full_score_sum)}``; empty when the item
    has no step-level snapshot or no recorded step assessments.
    """
    if observations is ...:
        observations = _step_point_observations(projected, step_assessments)
    if observations is not None:
        totals: dict[str, list[float]] = {}
        for item in observations:
            bucket = totals.setdefault(item["stable_key"], [0.0, 0.0])
            bucket[0] += item["achieved"] * item["score_weight"]
            bucket[1] += item["score_weight"]
        return {key: (values[0], values[1]) for key, values in totals.items()}
    steps = getattr(projected, "steps", None) or ()
    step_targets = getattr(projected, "step_targets", None) or {}
    if not steps or not isinstance(step_assessments, list):
        return {}
    records = [
        entry for entry in step_assessments if isinstance(entry, Mapping)
    ]
    result: dict[str, list[float]] = {}
    for step in steps:
        step_id = str(step.get("step_id") or "")
        step_part = str(step.get("part_id") or "")
        record = None
        for entry in records:
            if str(entry.get("step_id") or "") != step_id:
                continue
            entry_part = str(entry.get("part_id") or "")
            if entry_part and step_part and entry_part != step_part:
                continue
            record = entry
            break
        step_score = _number(step.get("step_score"))
        if record is None or step_score <= 0:
            continue
        achievement = str(record.get("achievement") or "").lower()
        awarded = _number(record.get("score_awarded"))
        covered = step.get("evidence_point_ids") or []
        if achievement in {"full", "equivalent"}:
            achieved = 1.0
        elif achievement == "none":
            # 沿用前步错误结果而判 none、但本步方法正确的步骤按达成计入。
            achieved = 1.0 if record.get("carried_error_from") else 0.0
        elif achievement == "partial":
            achieved = 0.5 if len(covered) == 1 else min(max(awarded / step_score, 0.0), 1.0)
        else:
            achieved = min(max(awarded / step_score, 0.0), 1.0)
        for stable_key in step_targets.get(step_id, ()):
            bucket = result.setdefault(str(stable_key), [0.0, 0.0])
            bucket[0] += achieved * step_score
            bucket[1] += step_score
    return {key: (value[0], value[1]) for key, value in result.items()}


__all__ = ["DiagnosisProfileService"]
