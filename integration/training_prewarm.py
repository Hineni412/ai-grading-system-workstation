"""Background refresher for the diagnosis/overview/graph read caches.

A single daemon thread polls the commit generations of both databases.  When
they changed (and once at startup) — and only while no job is queued or
running — it recomputes the most recently requested keys plus the startup
batch (latest semester volume: all students + each class, diagnosis and
overview) through the same request context and helpers the endpoints use, so
the entries it writes are identical to request-produced ones.  One INFO line
per batch; no student data is logged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
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


def record_request(
    kind: str,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    params: Mapping[str, Any],
) -> None:
    """Remember a (scope, exam_scope) request for background refresh."""

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
        entry["kinds"][str(kind)] = dict(params)
        entry["touched"] = time.monotonic()
        _RECENT.move_to_end(key)
        while len(_RECENT) > _RECENT_LIMIT:
            _RECENT.popitem(last=False)


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
    with _RECENT_LOCK:
        _RECENT.clear()


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
    ) -> None:
        self._paths = paths
        self._job_manager = job_manager
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_generations: tuple[int, int] | None = None
        self._startup_done = False
        self._seen_day: str | None = None
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

        generations = (
            commit_generation(Path(self._paths.db_path)),
            commit_generation(Path(self._paths.qb_db_path)),
        )
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        if self._startup_done and generations == self._seen_generations and day == self._seen_day:
            return False
        if self._jobs_active():
            return False
        tasks = self._refresh_plan()
        if not tasks:
            self._seen_generations = generations
            self._startup_done = True
            self._seen_day = day
            return False
        started = time.monotonic()
        completed = 0
        interrupted = False
        for task in tasks:
            if self._stop.is_set() or self._jobs_active():
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

    def _refresh_plan(self) -> list[Callable[[], None]]:
        tasks: list[Callable[[], None]] = []
        seen: set[tuple[str, str, str, str]] = set()
        if not self._startup_done:
            tasks.extend(self._startup_tasks(seen))
        for entry in recent_requests():
            scope = dict(entry["scope"])
            exam_scope = dict(entry["exam_scope"])
            for kind, params in entry["kinds"].items():
                tasks.extend(
                    self._tasks_for(kind, scope, exam_scope, params, seen)
                )
        return tasks

    def _tasks_for(
        self,
        kind: str,
        scope: dict[str, Any],
        exam_scope: dict[str, Any],
        params: Mapping[str, Any],
        seen: set[tuple[str, str, str, str]],
    ) -> list[Callable[[], None]]:
        identity = (
            kind,
            json.dumps(scope, sort_keys=True, default=str),
            json.dumps(exam_scope, sort_keys=True, default=str),
            json.dumps(dict(params), sort_keys=True, default=str),
        )
        tasks: list[Callable[[], None]] = []
        if ("diagnosis", identity[1], identity[2], "") not in seen:
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
            if identity not in seen:
                seen.add(identity)
                tasks.append(
                    lambda s=scope, e=exam_scope, p=dict(params): self._compute(
                        "assistant", s, e, p
                    )
                )
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
        for scope in self._startup_scopes():
            for kind, params in (
                ("diagnosis", {}),
                ("overview", {"volume_id": volume_id}),
            ):
                tasks.extend(self._tasks_for(kind, dict(scope), dict(exam_scope), params, seen))
        for class_name in self._class_names():
            # The assistant panel's first search sends this exact default body.
            tasks.extend(
                self._tasks_for(
                    "assistant",
                    {"mode": "class", "class_ids": [class_name],
                     "use_historical_fallback": False},
                    {"mode": "semester", "session_ids": [],
                     "curriculum_volume_id": volume_id},
                    {
                        "class_id": class_name,
                        "curriculum_volume_id": volume_id,
                        "chapter_id": "",
                        "target_keys": None,
                        "question_type": "",
                        "difficulty_min": 1,
                        "difficulty_max": 10,
                        "exclude_exam_originals": True,
                        "exclude_recent": True,
                    },
                    seen,
                )
            )
        return tasks

    def _startup_scopes(self) -> list[dict[str, Any]]:
        scopes = [
            self._normalized_scope({"mode": "all", "use_historical_fallback": False})
        ]
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
            else:
                service.build_profiles(scope=scope, exam_scope=exam_scope)

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
        target_keys = params.get("target_keys")
        compute_assistant_candidates(
            diagnosis_service=service,
            read_service=QuestionBankReadService(
                paths.qb_db_path, data_root=paths.data_root
            ),
            recommendations=PersonalizedRecommendationModule(
                db_path=paths.qb_db_path, data_root=paths.data_root
            ),
            workspace=AssemblyWorkspaceService(paths.data_root),
            class_id=str(params.get("class_id") or ""),
            curriculum_volume_id=str(params.get("curriculum_volume_id") or ""),
            chapter_id=str(params.get("chapter_id") or ""),
            teaching_progress_chapter_id=str(params.get("teaching_progress_chapter_id") or ""),
            target_keys=None if target_keys is None else list(target_keys),
            question_type=str(params.get("question_type") or ""),
            difficulty_min=int(params.get("difficulty_min") or 1),
            difficulty_max=int(params.get("difficulty_max") or 10),
            exclude_exam_originals=bool(
                params.get("exclude_exam_originals", True)
            ),
            exclude_recent=bool(params.get("exclude_recent", True)),
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
    "prewarm_enabled",
    "recent_requests",
    "record_request",
    "start_prewarm",
]
