from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from question_bank.mastery.model import week_of
import os
import pickle
import sqlite3
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.grading_database import open_grading_repositories
from integration.data_generation import (
    _database_file_state,
    cached_content_revision,
    commit_generation,
    database_content_revision as _database_content_revision,
)
from integration.evidence_scope import EvidenceScopeResolver
from integration.persistent_entries import persistent_entry_store
from integration.result_cache import ResultCache
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
_LOCAL_SOURCE_REVISIONS = ResultCache(2)
_MASTERY_INPUT_RESULTS = ResultCache(2)
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


def _profile_calculation_state() -> tuple:
    root = Path(__file__).resolve().parent.parent
    files = {
        *root.joinpath('question_bank').rglob('*.py'),
        *root.joinpath('integration').glob('*.py'),
        *root.joinpath('backend/repositories').glob('*.py'),
        *root.joinpath('question_bank/taxonomy/catalogs').glob('*.json'),
        root / "backend/api/routers/graph.py",
        root / 'backend/api/routers/training.py', root / 'backend/api/schemas/training.py',
        root / 'backend/public_data.py', root / 'backend/class_analysis.py',
        root / 'backend/session_analysis.py', root / 'backend/repositories/db_manager.py',
    }
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in sorted(files))


# Bind code versions when this process imports the calculation. A still-running
# old server must not label its results with newly edited source file versions;
# a new process rejects its old cache. No repeated source-tree scan per request.
_PROFILE_CALCULATION_STATE = _profile_calculation_state()


def _profile_semantics() -> tuple:
    """Running calculation versions, separate from database commits.

    The persisted profile inputs only use the date through the Beijing week
    (`week_of` for observations and mastery `as_of`); a calendar date would
    invalidate every saved entry daily for no real input change."""
    from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS
    return (
        str(week_of(datetime.now(UTC))),
        CURRENT_MASTERY_PARAMETERS.version,
        _PROFILE_CALCULATION_STATE,
    )


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

    def tag_profile_cache_key(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """Exact key under which build_tag_profiles caches this request."""

        scope = _normalized_profile_scope(scope, exam_scope)
        source_identity = self.cache_identity or (
            *_path_generation(self.grading_db_path),
            *_path_generation(self.question_bank_db_path),
        )
        source_identity = (*source_identity,
            *_dir_generation(self.data_root / "reports" / ".class_analysis"),
            json.dumps(_profile_semantics(), default=str))
        return (
            "\u0000".join(source_identity),
            "tag-profile-part-v11-effective-results-frozen-source",
            str(self.data_root),
            CURRENT_MASTERY_PARAMETERS.version,
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
        key = (*self.tag_profile_cache_key(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=exam_scope), "mastery-v3-exam-observations")
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

    def semester_mastery(self, profile, *, exclude_training_evidence_ids=frozenset(), as_of=None, parameters=None):
        from datetime import UTC, datetime
        from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS
        from question_bank.mastery.model import week_of
        parameters = parameters or CURRENT_MASTERY_PARAMETERS
        as_of = as_of or datetime.now(UTC)
        exam_scope = self.mastery_exam_scope(profile.get("exam_scope") or {})
        key = (*self.tag_profile_cache_key(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=exam_scope),
            "mastery-v3-population-slope", parameters.version, str(week_of(as_of)), ",".join(sorted(exclude_training_evidence_ids)))
        cached = _claim_or_wait_tag_profile(key)
        if cached is not None:
            if getattr(self, "persist_snapshots", False) and not exclude_training_evidence_ids:
                self._save_local_profile(key, cached)
            return pickle.loads(cached[0])
        error = None
        try:
            local_reader = getattr(self, "_read_local_profile", None)
            local = local_reader(key) if callable(local_reader) else None
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
            # Grading descriptions invalidate the full diagnosis, but do not
            # require another fit when every model input is byte-identical.
            grading_origin = self.cache_identity[0] if self.cache_identity else str(self.grading_db_path)
            input_key = (grading_origin, str(self.data_root), *_path_generation(self.question_bank_db_path),
                         _database_file_state(self.question_bank_db_path), _profile_semantics(),
                         resolver.release_id, parameters.version, week_of(as_of),
                         tuple(sorted(exclude_training_evidence_ids)),
                         hashlib.sha256(pickle.dumps(calculation_profile, pickle.HIGHEST_PROTOCOL)).digest())
            values = _MASTERY_INPUT_RESULTS.get_or_compute(input_key, lambda:
                CurrentMasteryCalculator(self.question_bank_db_path, resolver, parameters=parameters,
                    clock=lambda: as_of, data_root=self.data_root).calculate(
                        calculation_profile, exclude_training_evidence_ids=exclude_training_evidence_ids))
            entry = (pickle.dumps(values, pickle.HIGHEST_PROTOCOL), pickle.dumps({}, pickle.HIGHEST_PROTOCOL))
            with _TAG_PROFILE_CACHE_LOCK:
                _TAG_PROFILE_CACHE[key] = entry
                while len(_TAG_PROFILE_CACHE) > _TAG_PROFILE_CACHE_LIMIT:
                    _TAG_PROFILE_CACHE.pop(next(iter(_TAG_PROFILE_CACHE)))
            if getattr(self, "persist_snapshots", False) and not exclude_training_evidence_ids:
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
                kwargs = {'include_source_details': False} if not include_source_details else {}
                result, aggregated = self._compute_tag_profiles(
                    scope=_normalized_profile_scope(scope, exam_scope),
                    exam_scope=exam_scope, **kwargs,
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

    def _local_profile_path(self) -> Path:
        return self.data_root / "reports" / ".training_diagnosis" / "entries"

    def _local_profile_store(self):
        # Caps are read on each call so tests can narrow them.
        return persistent_entry_store(
            self._local_profile_path(),
            max_entries=_LOCAL_PROFILE_MAX_ENTRIES,
            max_bytes=_LOCAL_PROFILE_MAX_BYTES,
        )

    def _local_profile_entry_path(self, cache_key: tuple[str, ...]) -> Path:
        return self._local_profile_store().entry_path(cache_key[1:])

    def _local_profile_signature(self, cache_key: tuple[str, ...]) -> tuple | None:
        try:
            origins = self.cache_identity[:6] if self.cache_identity else ()
            paths = ((Path(origins[0]), Path(origins[3])) if len(origins) == 6
                     else (self.grading_db_path, self.question_bank_db_path))
            live = (*_path_generation(paths[0]), *_path_generation(paths[1]))
            expected = "\u0000".join((*live,
                *_dir_generation(self.data_root / "reports" / ".class_analysis"),
                json.dumps(_profile_semantics(), default=str)))
            if cache_key[0] != expected:
                return None  # A writer moved beyond the captured read transaction.
            state = tuple(_database_file_state(path) for path in paths)
            revisions = _LOCAL_SOURCE_REVISIONS.get_or_compute((live, state), lambda: (
                _database_content_revision(self.grading_db_path),
                _database_content_revision(self.question_bank_db_path, self.question_bank_connection),
            ))
            if live != (*_path_generation(paths[0]), *_path_generation(paths[1])):
                return None
            return (1, revisions, _profile_semantics(),
                    _dir_generation(self.data_root / "reports" / ".class_analysis"))
        except (OSError, sqlite3.Error, AttributeError, ValueError):
            return None

    def _read_local_profile(self, cache_key: tuple[str, ...]) -> tuple[bytes, bytes] | None:
        def signature_for(stored: object) -> tuple | None:
            # Cheap rejects run before the full signature, which serializes
            # both databases; the stored shape itself must still be checked.
            if (not isinstance(stored, tuple) or len(stored) != 4
                    or stored[2] != _profile_semantics()
                    or stored[3] != _dir_generation(self.data_root / 'reports' / '.class_analysis')):
                return None
            return self._local_profile_signature(cache_key)
        try:
            entry = self._local_profile_store().get(cache_key[1:], signature_for)
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
            if include_source_details:
                knowledge_associations = knowledge_skill_associations(
                    load_question_facets(self.question_bank_db_path, resolver)
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
            # Internal read-only contract. The API excludes root underscore
            # fields; source point text is not added to public references.
            "_exam_source_metadata": {
                str(session_id): {item.item_ref: dict(item.source_practice_metadata)
                    for item in projection.items if item.source_practice_metadata}
                for session_id, projection in projection_by_session.items()
            },
        }
        return result, dict(aggregated_mastery)

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
                node = node_by_key.get(stable_key)
                if node is None:
                    continue
                parent_key = parent_by_child.get(stable_key)
                parent = node_by_key.get(parent_key) if parent_key else None
                is_parent_summary = stable_key in children_by_parent
                point = points.get(stable_key)
                if point is None:
                    point = {
                        "knowledge_key": stable_key,
                        "knowledge_point": node.display_name,
                        "score_sum": 0.0,
                        "full_score_sum": 0.0,
                        "deduction_count": 0,
                        "exam_count": 0,
                        "source_question_refs": [],
                        "actionable_reasons": [],
                        "tag_context": {},
                        "error_counts": {"primary": {}, "secondary": {}},
                        "parent_knowledge_key": parent.stable_key if parent else None,
                        "parent_knowledge_point": parent.display_name if parent else None,
                    }
                    student["weak_points"].append(point)
                    points[stable_key] = point
                metadata = current.to_dict()
                point.update({field: metadata[field] for field in ("interval_low", "interval_high", "tier", "observation_count", "full_correct_count", "recent_trend", "parameter_version", "tier_counts", "logit_mean", "logit_sd", "difficulty_slope")})
                point.update({
                    "mastery": float(current.value) if current.value is not None else None,
                    "evidence_count": int(current.evidence_count),
                    "effective_weight": float(current.effective_weight),
                    "hierarchy_kind": (
                        "parent_summary" if is_parent_summary
                        else "child" if parent else "root"
                    ),
                    "child_knowledge_keys": sorted(children_by_parent.get(stable_key, [])),
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
        students = [{'student_id': str(s['id'])} for s in self.db.students.list_students()
                    if s.get('class_name') in context['class_ids']]
        return recommendations.current_exam_question_ids({'students': students},
            graded_activities=self.graded_activities(s['student_id'] for s in students),
            recent_activity_count=rules.get('recent_activity_count', 3), purpose=rules.get('purpose', 'handout'))

    def assembly_exam_questions(self, *, class_ids: list[str], volume_id: str) -> dict[str, Any]:
        """Read scored exam items through the existing evidence boundary; no persistence."""
        from backend.config_generation.contract import iter_effective_rubric_item_refs
        from question_bank.current_knowledge import CurrentKnowledgeResolver
        classes = set(class_ids)
        students = {str(s["id"]): s for s in self.db.students.list_students()
                    if s.get("class_name") in classes}
        sessions = [s for s in self.db.sessions.list_grading_sessions()
                    if not s.get("is_deleted") and s.get("curriculum_volume_id") == volume_id]
        if not students or not sessions:
            return {"student_count": len(students), "exams": []}
        ids = [int(s["id"]) for s in sessions]
        projections = self._tag_projections(ids)
        causes = self._error_cause_index(ids)
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
        bank_ids = {p.bank_question_id for projection in projections.values() for p in projection.items if p.bank_question_id}
        from question_bank.services.question_read_service import QuestionBankReadService
        from question_bank.services.standard_difficulty import difficulty_level
        bank_difficulty = {int(q['id']): difficulty_level(q.get('difficulty')) for q in
            QuestionBankReadService(self.question_bank_db_path).get_questions(sorted(bank_ids))} if bank_ids else {}
        categories = ("概念理解", "计算与化简", "审题与条件", "方法与思路", "过程与依据", "书写与规范", "未作答")
        exams = []
        for session in sorted(sessions, key=lambda s: (str(s.get("created_at") or ""), int(s["id"])), reverse=True):
            sid = int(session["id"])
            projection = {p.item_ref: p for p in projections[sid].items}
            questions = []
            participants: set[str] = set()
            totals: dict[str, float] = {}
            for qid, _parent, question, item in iter_effective_rubric_item_refs(self.db.results._load_session_rubric(sid)):
                scored = by_item.get((sid, qid), {})
                if not scored:
                    continue
                participants.update(scored)
                per_class = []
                rate_sum = 0.0
                lost: set[str] = set()
                classified: set[str] = set()
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
                projected = projection.get(qid)
                keys = list(projected.tags.get("knowledge_point", ())) if projected else []
                keys = [key for key in keys if key.startswith("sk_")]
                questions.append({"key": f"{sid}:{qid}", "session_id": sid, "question_id": qid,
                    "question_type": {"choice": "选择题", "fill_blank": "填空题", "multi_choice": "多选题"}.get(question.get("question_type"), "解答题"),
                    "full_score": max(float(r.get("full_score") or 0) for r in scored.values()),
                    "class_rate": round(rate_sum / len(scored), 4),
                    "class_rates": per_class, "student_count": len(scored),
                    "cause_category_counts": None if lost and not classified else [{"category": c, "count": len(members[c])} for c in categories],
                    "cause_unclassified_count": len(lost - classified),
                    "bank_question_id": projected.bank_question_id if projected else None,
                    "difficulty": bank_difficulty.get(projected.bank_question_id) if projected else None,
                    "skill_keys": keys, "skills": [{"key": key,
                        "label": resolver.node(key).display_name if resolver.node(key) else key,
                        "in_volume": volume_sections is None or anchors.get(key, {}).get("section") in volume_sections}
                        for key in keys],
                    "question_text": str(question.get("question_text") or item.get("question_text") or "")})
            if participants:
                exams.append({"session_id": sid, "title": session.get("session_name") or session.get("exam_name") or session.get("name") or session.get("title") or f"考试 {sid}",
                    "date": str(session.get("created_at") or ""), "class_ids": sorted({students[s].get("class_name") for s in participants}),
                    "student_count": len(participants), "average_score": round(sum(totals.values()) / len(participants), 2), "questions": questions})
        return {"student_count": len(students), "exams": exams}

    def _error_cause_index(
        self,
        session_ids: Iterable[int],
    ) -> dict[tuple[int, int, str], list[dict[str, Any]]]:
        """{(session_id, student_id, question_id): [{category, pattern, kind}]}。

        与报告共用证据校验；改分、改答案后的旧记录不进入诊断与训练。
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
        """session_error_records 的按场次复用；指纹覆盖不了任一输入时直接计算。

        _prepare_error_sources 声明“不缓存学生证据，也不跨请求复用”；此处
        允许复用，因为下面的指纹覆盖该次读取的全部输入（评分、场次行、
        出勤、rubric/答案文件、状态文件、题库版本、上传来源与计算代码）。
        """
        from backend.class_analysis import session_error_records
        fingerprint = self._session_error_fingerprint(session_id)
        if fingerprint is None:
            return session_error_records(
                self.db, session_id, reports_dir, data_root=self.data_root,
            )
        memo_key = (str(self.grading_db_path), str(self.data_root), session_id)
        with _SESSION_ERROR_LOCK:
            cached = _SESSION_ERROR_MEMO.get(memo_key)
            if cached is not None and cached[0] == fingerprint:
                _SESSION_ERROR_MEMO.move_to_end(memo_key)
                return cached[1]
        store = persistent_entry_store(
            self.data_root / "reports" / ".training_diagnosis" / "sessions",
            max_entries=_SESSION_ERROR_MAX_ENTRIES,
            max_bytes=_SESSION_ERROR_MAX_BYTES,
        )
        pkey = ("session-error-records-v1", session_id, fingerprint)
        records = store.get(pkey, fingerprint)
        if not isinstance(records, dict):
            records = session_error_records(
                self.db, session_id, reports_dir, data_root=self.data_root,
            )
            store.put(pkey, fingerprint, records)
        with _SESSION_ERROR_LOCK:
            _SESSION_ERROR_MEMO[memo_key] = (fingerprint, records)
            _SESSION_ERROR_MEMO.move_to_end(memo_key)
            while len(_SESSION_ERROR_MEMO) > _SESSION_ERROR_MAX_ENTRIES:
                _SESSION_ERROR_MEMO.popitem(last=False)
        return records

    def _session_error_fingerprint(self, session_id: int) -> tuple | None:
        """Cover every input session_error_records reads for this session.

        Returns None when any input cannot be fingerprinted; the caller then
        computes without caching, preserving the previous behaviour.
        """
        try:
            from path_manager import resolve_stored_file_path
            # 行内容哈希必须与读取它的数据库代次配对，否则一次提交会把旧
            # 快照的哈希登记到新代次名下。
            rows_hash = self._session_rows_hash(session_id)
            session = self.db.sessions.get_grading_session(int(session_id)) or {}
            files = []
            for field in ("rubric_path", "answer_key_path"):
                try:
                    resolved = resolve_stored_file_path(
                        session.get(field), data_root=self.data_root,
                    )
                except (OSError, RuntimeError):
                    resolved = None
                files.append(_session_error_file_state(resolved))
            state_path = (
                self.data_root / "reports" / ".class_analysis"
                / f"{int(session_id)}.json"
            )
            bank_revision = cached_content_revision(self.question_bank_db_path)
            source_state = _session_source_state(
                self.data_root / "config" / "uploaded" / "config_sources"
                / f"session-{int(session_id)}"
            )
            return (
                rows_hash,
                tuple(files),
                _session_error_file_state(state_path),
                bank_revision,
                source_state,
                _PROFILE_CALCULATION_STATE,
                _SESSION_ERROR_CODE_STATE,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError,
                AttributeError, KeyError):
            return None

    def _session_rows_hash(self, session_id: int) -> str:
        """sha256 of this session's grading rows keyed by the state read.

        A snapshot captured by request_read_context is frozen, so the rows
        hash is safe to cache under the grading identity captured with it.
        A live connection can observe a commit between the row reads, so
        the hash is cached only while the generation holds still; on a move
        the computed hash is returned without caching.
        """
        from backend.report_exports import score_revision
        if self.cache_identity:
            generation = tuple(self.cache_identity[:3])
            stable = True
        else:
            # Monitor the real database path, not a request-scoped snapshot:
            # the monitor keeps a long-lived connection that would pin a
            # captured snapshot file open past request cleanup.
            source_path = Path(getattr(self.db, "db_path", self.grading_db_path))
            generation = _path_generation(source_path)
            stable = False
        key = (*generation, int(session_id))
        with _SESSION_ERROR_LOCK:
            hit = _SESSION_ERROR_ROW_HASHES.get(key)
            if hit is not None:
                _SESSION_ERROR_ROW_HASHES.move_to_end(key)
                return hit
        value = hashlib.sha256(json.dumps(
            (
                score_revision(self.db, int(session_id)),
                self.db.sessions.get_grading_session(int(session_id)) or {},
                self.db.sessions.get_session_attendance(int(session_id)),
            ),
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode()).hexdigest()
        if stable or generation == _path_generation(source_path):
            with _SESSION_ERROR_LOCK:
                _SESSION_ERROR_ROW_HASHES[key] = value
                _SESSION_ERROR_ROW_HASHES.move_to_end(key)
                while len(_SESSION_ERROR_ROW_HASHES) > _SESSION_ERROR_ROW_LIMIT:
                    _SESSION_ERROR_ROW_HASHES.popitem(last=False)
        return value

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
    ) -> list[dict[str, Any]]:
        projected_by_item = {
            (session_id, item.item_ref): item
            for session_id, projection in projection_by_session.items()
            for item in projection.items
        }
        rows = self.db.results.get_active_assessment_evidence(
            student_ids=tuple(student_ids),
            session_ids=tuple(session_ids),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            key = (
                int(row.get("session_id") or 0),
                str(row.get("question_id") or ""),
            )
            projected = projected_by_item.get(key)
            if projected is None or not projected.is_graph_eligible:
                continue
            enriched = dict(row)
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
                contributions = _step_target_contributions(projected, records)
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
            }
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

def _path_generation(path: Path) -> tuple[str, ...]:
    """Cache identity for a database file: resolved path, file identity, and
    the commit generation (``PRAGMA data_version`` monitor) so real commits
    invalidate while read-only opens and checkpoints do not."""

    source = Path(path).resolve(strict=False)
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


def _dir_generation(path: Path) -> tuple[str, ...]:
    """目录级缓存指纹：文件名集合 + 最新修改时间，用于 .class_analysis 状态目录。"""
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


def _session_source_state(directory: Path) -> tuple:
    """Recursive file stamp for one session's uploaded config-source tree."""
    try:
        if not directory.is_dir():
            return (str(directory), "missing")
        return (str(directory), tuple(sorted(
            (str(item.relative_to(directory)), info.st_size, info.st_mtime_ns)
            for item in directory.rglob("*")
            if item.is_file() and (info := item.stat())
        )))
    except OSError:
        return (str(directory), "unreadable")


def _session_error_code_state() -> tuple:
    """File stamps for the modules session_error_records depends on that
    _PROFILE_CALCULATION_STATE does not already cover."""
    root = Path(__file__).resolve().parent.parent
    files = [
        root / "backend" / "report_exports.py",
        root / "backend" / "error_causes.py",
        root / "backend" / "error_patterns.py",
        root / "backend" / "review" / "service.py",
        root / "backend" / "analytics" / "service.py",
        root / "backend" / "config_workspace" / "sources.py",
        root / "backend" / "document_parsing" / "question_blocks.py",
        root / "backend" / "repositories" / "results.py",
        root / "backend/scan_grading/solution_answer_guard.py",
        root / "backend/question_id_contract.py",
        root / "backend/scan_grading/grading_completeness.py",
        root / "path_manager.py",
    ]
    return tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in files
    )


_SESSION_ERROR_CODE_STATE = _session_error_code_state()
# 指纹通过才允许跨请求/重启复用；内存层与同进程共享条目库互为两级缓存。
_SESSION_ERROR_MEMO: OrderedDict[tuple, tuple] = OrderedDict()
_SESSION_ERROR_LOCK = threading.Lock()
# 评分修订、场次行与出勤只读阅卷库；同一提交代次内结果不变，按代次复用，
# 否则每次指纹都要重读全部行。代次变化（提交、文件替换、归档）即重读。
# 键为 (路径, 文件标识, 提交代次, 场次)；直接连接（无请求快照）只在两次
# 代次读取一致时才写入，避免把快照前的内容记到新代次名下。
_SESSION_ERROR_ROW_HASHES: OrderedDict[tuple, str] = OrderedDict()
_SESSION_ERROR_ROW_LIMIT = 128


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
) -> dict[str, tuple[float, float]]:
    """Per-step achievement allocated to evidence-linked targets.

    Returns ``{stable_key: (score_sum, full_score_sum)}``; empty when the item
    has no step-level snapshot or no recorded step assessments.
    """
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
