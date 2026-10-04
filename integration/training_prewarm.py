"""Background refresher for the diagnosis/overview/graph read caches.

A single daemon thread polls the commit generations of both databases.  When
they changed (and once at startup) — and only while no job is queued or
running — it recomputes the most recently requested keys plus the startup
batch (latest semester volume: all students + each class, diagnosis,
overview, default graph and first-chapter grouping) through the same request context and helpers the endpoints use, so
the entries it writes are identical to request-produced ones.  One INFO line
per batch; no student data is logged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from integration.data_generation import commit_generation

_LOG = logging.getLogger(__name__)

_RECENT_LIMIT = 8
_RECENT_TTL_SECONDS = 2 * 3600.0
_POLL_SECONDS = 15.0
# A paused job can sit for days while consuming nothing; only queued/running
# jobs actually compete with the refresher.
_ACTIVE_JOB_STATUSES = ("queued", "running")

_RECENT_LOCK = threading.Lock()
_RECENT: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_RECENT_GENERATION = 0
_RECENT_TARGET_LIMIT = 60
_RECENTS_FILE_NAME = "recent_requests.json"
_RECENTS_MAX_AGE = timedelta(days=14)

# Foreground read requests counted via get_request_read_context. The worker
# yields while one is active and briefly afterwards so a refresh batch never
# competes with the page the teacher is looking at.
_FOREGROUND_LOCK = threading.Lock()
_FOREGROUND_ACTIVE = 0
_FOREGROUND_LAST_FINISH = 0.0
_FOREGROUND_QUIET_SECONDS = 2.0


@contextmanager
def foreground_request() -> Iterator[None]:
    """Count one in-flight foreground read for the prewarm yield."""

    global _FOREGROUND_ACTIVE, _FOREGROUND_LAST_FINISH
    with _FOREGROUND_LOCK:
        _FOREGROUND_ACTIVE += 1
    try:
        yield
    finally:
        with _FOREGROUND_LOCK:
            _FOREGROUND_ACTIVE -= 1
            _FOREGROUND_LAST_FINISH = time.monotonic()


def record_request(
    kind: str,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    params: Mapping[str, Any],
) -> None:
    """Remember a (scope, exam_scope) request for background refresh."""

    global _RECENT_GENERATION
    key = (
        json.dumps(dict(scope), sort_keys=True, default=str),
        json.dumps(dict(exam_scope), sort_keys=True, default=str),
    )
    with _RECENT_LOCK:
        entry = _RECENT.get(key)
        if entry is None:
            entry = {
                "scope": dict(scope),
                "exam_scope": dict(exam_scope),
                "kinds": {},
                "touched": time.monotonic(),
            }
            _RECENT[key] = entry
        if entry['kinds'].get(str(kind)) != dict(params):
            _RECENT_GENERATION += 1
        entry["kinds"][str(kind)] = dict(params)
        entry["touched"] = time.monotonic()
        _RECENT.move_to_end(key)
        while len(_RECENT) > _RECENT_LIMIT:
            _RECENT.popitem(last=False)


def record_target(
    kind: str,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    target_keys: Any,
) -> None:
    """Record a target selection on the matching recent entry.

    Targets are replayed by the prewarm as separate tasks. Unlike
    record_request this does not bump the recent generation: one background
    plan per filter set still holds however many skills the panel prefetches.
    """

    key = (
        json.dumps(dict(scope), sort_keys=True, default=str),
        json.dumps(dict(exam_scope), sort_keys=True, default=str),
    )
    recorded = None if target_keys is None else tuple(target_keys)
    with _RECENT_LOCK:
        entry = _RECENT.get(key)
        if entry is None:
            entry = {
                "scope": dict(scope),
                "exam_scope": dict(exam_scope),
                "kinds": {},
                "targets": {},
                "touched": time.monotonic(),
            }
            _RECENT[key] = entry
        items = entry.setdefault("targets", {}).setdefault(str(kind), [])
        if recorded not in items:
            items.append(recorded)
            while len(items) > _RECENT_TARGET_LIMIT:
                items.pop(0)
        entry["touched"] = time.monotonic()
        _RECENT.move_to_end(key)
        while len(_RECENT) > _RECENT_LIMIT:
            _RECENT.popitem(last=False)


def _recent_row(entry: Mapping[str, Any], saved_at: str) -> dict[str, Any]:
    targets = {
        str(kind): [None if item is None else list(item) for item in items]
        for kind, items in (entry.get("targets") or {}).items()
    }
    return {
        "scope": dict(entry.get("scope") or {}),
        "exam_scope": dict(entry.get("exam_scope") or {}),
        "kinds": dict(entry.get("kinds") or {}),
        "targets": targets,
        "saved_at": entry.get("saved_at") or saved_at,
    }


def recent_requests() -> list[dict[str, Any]]:
    """Snapshot of the LRU for tests and the worker."""

    now = time.monotonic()
    with _RECENT_LOCK:
        stale = [
            key for key, entry in _RECENT.items()
            if now - entry["touched"] > _RECENT_TTL_SECONDS
        ]
        for key in stale:
            _RECENT.pop(key, None)
        return [dict(entry) for entry in _RECENT.values()]


def clear_recent_requests() -> None:
    global _RECENT_GENERATION
    with _RECENT_LOCK:
        _RECENT.clear()
        _RECENT_GENERATION += 1


def _recent_generation() -> int:
    with _RECENT_LOCK:
        return _RECENT_GENERATION


def prewarm_enabled() -> bool:
    """Default ON in the app, OFF under pytest; env var overrides."""

    value = os.getenv("AI_GRADING_PREWARM", "").strip().casefold()
    if value in {"0", "false", "off", "no"}:
        return False
    if value in {"1", "true", "on", "yes"}:
        return True
    return "pytest" not in sys.modules


class TrainingPrewarmWorker:
    def __init__(
        self,
        paths: Any,
        job_manager: Any,
        *,
        poll_seconds: float = _POLL_SECONDS,
        foreground_quiet_seconds: float = _FOREGROUND_QUIET_SECONDS,
    ) -> None:
        self._paths = paths
        self._job_manager = job_manager
        self._poll_seconds = poll_seconds
        self._foreground_quiet_seconds = foreground_quiet_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_generations: tuple[int, int] | None = None
        self._startup_done = False
        self._seen_day: str | None = None
        self._seen_group_day: str | None = None
        self._seen_recent_generation: int | None = None
        self._recents_loaded = False
        self._persisted_recents: str | None = None
        self.batches: list[tuple[int, float]] = []

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="training-prewarm",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        if self._stop.wait(2.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                _LOG.exception("training prewarm tick failed")
            if self._stop.wait(self._poll_seconds):
                return

    def tick(self) -> bool:
        """One refresh pass; returns True when a batch ran."""

        if not self._recents_loaded:
            self._recents_loaded = True
            self._load_recents()
        self._persist_recents()
        generations = (
            commit_generation(Path(self._paths.db_path)),
            commit_generation(Path(self._paths.qb_db_path)),
        )
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        group_day = datetime.now(timezone.utc).date().isoformat()
        recent_generation = _recent_generation()
        if (self._startup_done and generations == self._seen_generations and day == self._seen_day
                and group_day == self._seen_group_day
                and recent_generation == self._seen_recent_generation):
            return False
        if self._jobs_active():
            return False
        tasks = self._refresh_plan()
        if not tasks:
            self._seen_generations = generations
            self._startup_done = True
            self._seen_day = day
            self._seen_group_day = group_day
            self._seen_recent_generation = recent_generation
            return False
        started = time.monotonic()
        completed = 0
        interrupted = False
        for task in tasks:
            if self._stop.is_set() or self._jobs_active() or not self._wait_for_idle():
                interrupted = True
                break
            try:
                task()
                completed += 1
            except Exception:
                _LOG.debug("training prewarm key failed", exc_info=True)
        elapsed = time.monotonic() - started
        _LOG.info(
            "training prewarm refreshed %d item(s) in %.1fs",
            completed,
            elapsed,
        )
        self.batches.append((completed, elapsed))
        if not interrupted:
            self._seen_generations = generations
            self._startup_done = True
            self._seen_day = day
            self._seen_group_day = group_day
            self._seen_recent_generation = recent_generation
        return True

    def _jobs_active(self) -> bool:
        manager = self._job_manager
        if manager is None:
            return False
        try:
            if getattr(manager, "is_shutdown", False):
                return True
            records, _total = manager.list(statuses=_ACTIVE_JOB_STATUSES)
            return bool(records)
        except Exception:
            return True

    def _wait_for_idle(self) -> bool:
        """Wait while a foreground read is active or just finished.

        Returns False only when the worker is stopping, so the caller treats
        it as an interruption. Waiting itself does not interrupt the batch.
        """

        while not self._stop.is_set():
            with _FOREGROUND_LOCK:
                busy = _FOREGROUND_ACTIVE > 0
                quiet_for = time.monotonic() - _FOREGROUND_LAST_FINISH
            if not busy and quiet_for >= self._foreground_quiet_seconds:
                return True
            if self._stop.wait(0.2):
                return False
        return False

    def _recents_file(self) -> Path:
        data_root = getattr(self._paths, "data_root", None)
        if data_root is None:
            data_root = Path(self._paths.qb_db_path).parent.parent
        return Path(data_root) / "reports" / ".training_diagnosis" / _RECENTS_FILE_NAME

    def _load_recents(self) -> None:
        """Restore the persisted recent list, dropping entries older than the
        retention window."""

        try:
            payload = json.loads(self._recents_file().read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return
            cutoff = datetime.now(timezone.utc) - _RECENTS_MAX_AGE
            loaded: list[dict[str, Any]] = []
            for raw in payload.get("entries") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    saved_at = datetime.fromisoformat(str(raw.get("saved_at") or ""))
                except (TypeError, ValueError):
                    continue
                if saved_at.tzinfo is None:
                    saved_at = saved_at.replace(tzinfo=timezone.utc)
                if saved_at < cutoff:
                    continue
                targets = {
                    str(kind): [
                        None if item is None else tuple(item)
                        for item in items
                    ]
                    for kind, items in (raw.get("targets") or {}).items()
                    if isinstance(items, list)
                }
                loaded.append({
                    "scope": dict(raw.get("scope") or {}),
                    "exam_scope": dict(raw.get("exam_scope") or {}),
                    "kinds": dict(raw.get("kinds") or {}),
                    "targets": targets,
                    "saved_at": raw.get("saved_at"),
                    "touched": time.monotonic(),
                })
            with _RECENT_LOCK:
                for entry in loaded:
                    key = (
                        json.dumps(entry["scope"], sort_keys=True, default=str),
                        json.dumps(entry["exam_scope"], sort_keys=True, default=str),
                    )
                    if key not in _RECENT:
                        _RECENT[key] = entry
                    while len(_RECENT) > _RECENT_LIMIT:
                        _RECENT.popitem(last=False)
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            pass

    def _persist_recents(self) -> None:
        """Save the recent list once per content change, not per request."""

        try:
            now = datetime.now(timezone.utc).isoformat()
            with _RECENT_LOCK:
                snapshot = list(_RECENT.values())
                text = json.dumps(
                    {"version": 1,
                     "entries": [_recent_row(entry, now) for entry in snapshot]},
                    ensure_ascii=False, sort_keys=True, default=str)
            if text == self._persisted_recents:
                return
            path = self._recents_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, mode="w", encoding="utf-8",
                    delete=False, suffix=".tmp",
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(text)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            with _RECENT_LOCK:
                for entry in snapshot:
                    entry.setdefault("saved_at", now)
            self._persisted_recents = text
        except (OSError, TypeError, ValueError):
            pass

    def _refresh_plan(self) -> list[Callable[[], None]]:
        tasks: list[Callable[[], None]] = []
        seen: set[tuple[str, str, str, str]] = set()
        entries = recent_requests()
        if not self._startup_done:
            # What the teacher most recently opened comes before the generic
            # startup defaults; afterwards keep the recorded order.
            entries = list(reversed(entries))
        for entry in entries:
            scope = dict(entry["scope"])
            exam_scope = dict(entry["exam_scope"])
            targets = entry.get("targets") or {}
            for kind, params in entry["kinds"].items():
                tasks.extend(
                    self._tasks_for(kind, scope, exam_scope, params, seen,
                                    targets=targets.get(str(kind)))
                )
        if not self._startup_done:
            tasks.extend(self._startup_tasks(seen))
        return tasks

    def _tasks_for(
        self,
        kind: str,
        scope: dict[str, Any],
        exam_scope: dict[str, Any],
        params: Mapping[str, Any],
        seen: set[tuple[str, str, str, str]],
        targets: list | None = None,
    ) -> list[Callable[[], None]]:
        identity = (
            kind,
            json.dumps(scope, sort_keys=True, default=str),
            json.dumps(exam_scope, sort_keys=True, default=str),
            json.dumps(dict(params), sort_keys=True, default=str),
        )
        tasks: list[Callable[[], None]] = []
        # Exam evidence does not consume tag profiles; every other kind does.
        if kind != "assembly_exam" and ("diagnosis", identity[1], identity[2], "") not in seen:
            seen.add(("diagnosis", identity[1], identity[2], ""))
            tasks.append(lambda s=scope, e=exam_scope: self._compute("diagnosis", s, e, {}))
        if kind == "overview":
            volume_id = str(params.get("volume_id") or "")
            if volume_id and identity not in seen:
                seen.add(identity)
                tasks.append(
                    lambda s=scope, e=exam_scope, v=volume_id: self._compute(
                        "overview", s, e, {"volume_id": v}
                    )
                )
        elif kind == "graph":
            if identity not in seen:
                seen.add(identity)
                tasks.append(
                    lambda s=scope, e=exam_scope, p=dict(params): self._compute(
                        "graph", s, e, p
                    )
                )
        elif kind == "assistant":
            # One evidence task for the scope, then one task per recorded
            # target so the foreground-yield check runs between them.
            class_ids = [str(item) for item in (params.get("class_ids") or [])]
            if not class_ids and params.get("class_id"):
                class_ids = [str(params["class_id"])]
            evidence_params = {
                "class_ids": class_ids,
                "curriculum_volume_id": str(params.get("curriculum_volume_id") or ""),
            }
            evidence_identity = (
                "assembly_exam",
                identity[1],
                identity[2],
                json.dumps(evidence_params, sort_keys=True, default=str),
            )
            if evidence_identity not in seen:
                seen.add(evidence_identity)
                tasks.append(lambda p=dict(evidence_params): self._compute(
                    "assembly_exam", {}, {}, p))
            for target in targets or []:
                target_params = {**dict(params), "target_keys": target}
                target_identity = (
                    kind,
                    identity[1],
                    identity[2],
                    json.dumps(target_params, sort_keys=True, default=str),
                )
                if target_identity in seen:
                    continue
                seen.add(target_identity)
                tasks.append(
                    lambda s=scope, e=exam_scope, p=target_params: self._compute(
                        "assistant", s, e, p
                    )
                )
        elif kind == 'assembly_exam':
            if identity not in seen:
                seen.add(identity)
                tasks.append(lambda s=scope, e=exam_scope, p=dict(params):
                    self._compute('assembly_exam', s, e, p))
        elif kind == 'grouped_diagnosis':
            if identity not in seen:
                seen.add(identity)
                tasks.append(lambda s=scope, e=exam_scope, p=dict(params):
                    self._compute('grouped_diagnosis', s, e, p))
        return tasks

    def _startup_tasks(
        self,
        seen: set[tuple[str, str, str, str]],
    ) -> list[Callable[[], None]]:
        volume_id = self._latest_volume_id()
        if not volume_id:
            return []
        tasks: list[Callable[[], None]] = []
        exam_scope = self._normalized_exam_scope(volume_id)
        from backend.api.schemas.training import TrainingGroupingRequest
        from question_bank.taxonomy.curriculum_catalog import curriculum_volume
        volume = curriculum_volume(volume_id=volume_id)
        grouping = None
        if volume and volume['chapters']:
            grouping = TrainingGroupingRequest(scope_keys=[volume['chapters'][0]['knowledge_id']],
                curriculum_volume_id=volume_id).model_dump()
        for scope in self._startup_scopes():
            defaults = [
                ("diagnosis", {}),
                ("overview", {"volume_id": volume_id}),
                ("graph", {"knowledge_keys": [], "prerequisite_depth": 1}),
            ]
            if grouping is not None:
                defaults.append(('grouped_diagnosis', {'grouping': grouping}))
            for kind, params in defaults:
                tasks.extend(self._tasks_for(kind, dict(scope), dict(exam_scope), params, seen))
        for class_name in self._class_names():
            # The class-assembly panel reads this evidence before any search.
            tasks.extend(
                self._tasks_for(
                    "assembly_exam",
                    {"mode": "class", "class_ids": [class_name],
                     "use_historical_fallback": False},
                    dict(exam_scope),
                    {
                        "class_ids": [class_name],
                        "curriculum_volume_id": volume_id,
                    },
                    seen,
                )
            )
        return tasks

    def _startup_scopes(self) -> list[dict[str, Any]]:
        scopes = []
        for class_name in self._class_names():
            scopes.append(
                self._normalized_scope(
                    {
                        "mode": "class",
                        "class_ids": [class_name],
                        "class_id": class_name,
                        "use_historical_fallback": False,
                    }
                )
            )
        # Keep the default all-student page projections newest in the bounded
        # local cache when there are more class projections than it can hold.
        scopes.append(self._normalized_scope({"mode": "all", "use_historical_fallback": False}))
        return scopes

    @staticmethod
    def _normalized_scope(scope: dict[str, Any]) -> dict[str, Any]:
        from backend.api.schemas.training import TrainingScopeRequest

        return TrainingScopeRequest(**scope).model_dump(exclude_none=True)

    @staticmethod
    def _normalized_exam_scope(volume_id: str) -> dict[str, Any]:
        from backend.api.schemas.training import TrainingExamScopeRequest

        return TrainingExamScopeRequest(
            mode="semester", curriculum_volume_id=volume_id
        ).model_dump(exclude_none=True)

    def _latest_volume_id(self) -> str | None:
        try:
            connection = sqlite3.connect(
                f"{Path(self._paths.db_path).resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            try:
                connection.execute("PRAGMA query_only = ON")
                row = connection.execute(
                    """
                    SELECT curriculum_volume_id
                    FROM grading_sessions
                    WHERE is_deleted = 0
                      AND TRIM(COALESCE(curriculum_volume_id, '')) <> ''
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return None
        return str(row[0]).strip() if row else None

    def _class_names(self) -> list[str]:
        try:
            connection = sqlite3.connect(
                f"{Path(self._paths.db_path).resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            try:
                connection.execute("PRAGMA query_only = ON")
                rows = connection.execute(
                    """
                    SELECT DISTINCT class_name
                    FROM students
                    WHERE TRIM(COALESCE(class_name, '')) <> ''
                    ORDER BY class_name
                    """
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return []
        return [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]

    def _compute(
        self,
        kind: str,
        scope: dict[str, Any],
        exam_scope: dict[str, Any],
        params: Mapping[str, Any],
    ) -> None:
        from backend.api.read_connections import request_read_context
        from integration.mastery_overview import overview_payload

        with request_read_context(self._paths) as ctx:
            service = ctx.diagnosis_service
            # Only the existing idle background refresher writes derived local
            # profiles. Foreground recommendation requests remain read-only.
            service.persist_snapshots = True
            if kind == "overview":
                overview_payload(
                    service,
                    scope=scope,
                    exam_scope=exam_scope,
                    volume_id=str(params.get("volume_id") or ""),
                )
            elif kind == "graph":
                self._compute_graph(service, scope, exam_scope, params)
            elif kind == "assistant":
                self._compute_assistant(service, params)
            elif kind == 'assembly_exam':
                from backend.api.routers.assembly import _cached_exam_questions
                _cached_exam_questions(
                    service,
                    class_ids=[str(item) for item in params.get("class_ids") or []],
                    volume_id=str(params.get("curriculum_volume_id") or ""),
                )
            elif kind == 'grouped_diagnosis':
                from backend.api.routers.training import _grouped_diagnosis_response_bytes
                from backend.api.schemas.training import TrainingGroupingRequest
                from question_bank.recommendation.personalized import PersonalizedRecommendationModule
                module = PersonalizedRecommendationModule(db_path=self._paths.qb_db_path,
                    data_root=self._paths.data_root, semester_mastery=service.semester_mastery)
                _grouped_diagnosis_response_bytes(service, module, scope=scope, exam_scope=exam_scope,
                    grouping=TrainingGroupingRequest.model_validate(params['grouping']))
            else:
                from backend.api.routers.training import _diagnosis_response_bytes
                _diagnosis_response_bytes(service, scope=scope, exam_scope=exam_scope)

    def _compute_graph(
        self,
        service: Any,
        scope: dict[str, Any],
        exam_scope: dict[str, Any],
        params: Mapping[str, Any],
    ) -> None:
        from backend.api.routers.graph import (
            _cached_current_graph_service,
            compute_graph_query_payload,
        )
        from question_bank.relations.query_service import CurrentGraphQuery

        graph_service = _cached_current_graph_service(Path(self._paths.qb_db_path))
        query = CurrentGraphQuery(
            knowledge_keys=tuple(params.get("knowledge_keys") or ()),
            prerequisite_depth=int(params.get("prerequisite_depth") or 0),
        )
        compute_graph_query_payload(
            service,
            graph_service,
            scope=scope,
            exam_scope=exam_scope,
            query=query,
        )

    def _compute_assistant(
        self,
        service: Any,
        params: Mapping[str, Any],
    ) -> None:
        from backend.api.routers.assembly import compute_assistant_candidates
        from question_bank.recommendation.personalized import (
            PersonalizedRecommendationModule,
        )
        from question_bank.services.assembly_workspace_service import (
            AssemblyWorkspaceService,
        )
        from question_bank.services.question_read_service import (
            QuestionBankReadService,
        )

        paths = self._paths
        data_root = getattr(paths, "data_root", None) or Path(paths.qb_db_path).parent.parent
        target_keys = params.get("target_keys")
        # Same dependencies and parameters as the endpoint so the prewarm
        # fills the very cache keys a foreground request will look up.
        compute_assistant_candidates(
            diagnosis_service=service,
            read_service=QuestionBankReadService(
                paths.qb_db_path, data_root=data_root
            ),
            recommendations=PersonalizedRecommendationModule(
                db_path=paths.qb_db_path, data_root=data_root,
                semester_mastery=service.semester_mastery,
            ),
            workspace=AssemblyWorkspaceService(data_root),
            class_id=str(params.get("class_id") or ""),
            class_ids=[str(item) for item in params.get("class_ids") or []] or None,
            session_ids=[int(item) for item in params.get("session_ids") or []] or None,
            curriculum_volume_id=str(params.get("curriculum_volume_id") or ""),
            chapter_id=str(params.get("chapter_id") or ""),
            teaching_progress_chapter_id=str(params.get("teaching_progress_chapter_id") or ""),
            target_keys=None if target_keys is None else list(target_keys),
            question_type=str(params.get("question_type") or ""),
            difficulty_min=float(params.get("difficulty_min") or 1),
            difficulty_max=float(params.get("difficulty_max") or 10),
            exclude_exam_originals=bool(
                params.get("exclude_exam_originals", True)
            ),
            exclude_recent=bool(params.get("exclude_recent", True)),
            recent_activity_count=int(params.get("recent_activity_count", 3)),
            purpose=str(params.get("purpose") or "training"),
            record=False,
        )


def start_prewarm(paths: Any, job_manager: Any) -> TrainingPrewarmWorker | None:
    if not prewarm_enabled():
        return None
    # The app installs no root logging config, so without a handler this
    # module's INFO batch line would never reach the console.
    if not _LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("INFO:     %(message)s"))
        _LOG.addHandler(handler)
    _LOG.setLevel(logging.INFO)
    worker = TrainingPrewarmWorker(paths, job_manager)
    worker.start()
    return worker


__all__ = [
    "TrainingPrewarmWorker",
    "clear_recent_requests",
    "foreground_request",
    "prewarm_enabled",
    "recent_requests",
    "record_request",
    "record_target",
    "start_prewarm",
]
