"""Teacher-reviewed skill candidates for evidence points without a skill link.

判定点 whose effective links never reach a ``sk_`` node are "skill gaps".
This service projects those gaps, runs one AI grouping call per chapter
batch on explicit teacher request, and stores the suggestions for review.
Approving ``link_existing`` writes links immediately; approving
``new_skill`` only records an approved-but-unpublished skill; approving
``keep_section`` dismisses the gap from the preview.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from question_bank.atomic_files import replace_with_retry
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_skill_index import (
    short_node_name,
    skill_anchor_ids,
)
from question_bank.services.skill_gaps import (
    gap_items,
    teacher_protected_versions,
)
from question_bank.taxonomy.curriculum_catalog import curriculum_volume

_STATE_LOCK = threading.RLock()
_ACTIVE_RUNS: set[str] = set()
_TERMINAL_STATUSES = frozenset(
    {"completed", "partial", "failed", "cancelled", "stale"}
)
_MAX_READY_PER_RUN = 600
_MAX_BATCH_GAPS = 60
_MAX_RUNS = 20
_REQUEST_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_DECISIONS = frozenset({"link_existing", "new_skill", "keep_section"})
_EDIT_KINDS = frozenset(
    {"link_existing", "new_skill", "merge_into_approved", "keep_section"}
)


class SkillCandidateGateway(Protocol):
    def suggest_skill_candidates(
        self, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Return one grouping response per physical request."""


class SkillCandidateNotFound(LookupError):
    """The requested run, suggestion, or approved skill does not exist."""


class SkillCandidateRequestConflict(RuntimeError):
    """A request token was reused with a different command."""


class SkillCandidateRevisionConflict(RuntimeError):
    """Candidate state changed between preview and the requested action."""


class SkillCandidateBusy(RuntimeError):
    """Another candidate run is still active."""


class SkillCandidateStale(RuntimeError):
    """The knowledge release changed since the suggestion was created."""


class SkillCandidateInvalid(ValueError):
    """The requested candidate action is invalid."""


class SkillCandidateService:
    """Persist resumable, teacher-review-only skill candidate runs."""

    def __init__(
        self,
        *,
        state_path: Path,
        db_path: Path,
        read_service: QuestionBankReadService,
    ) -> None:
        self.state_path = Path(state_path)
        self.db_path = Path(db_path)
        self.read_service = read_service

    # ---------- read-side projection ----------

    def _snapshot(self, volume_id: str) -> dict[str, Any]:
        return self.read_service._skill_snapshot(volume_id=volume_id)

    def _volume(self, volume_id: str) -> dict[str, Any]:
        volume = curriculum_volume(volume_id=volume_id)
        if volume is None:
            raise SkillCandidateInvalid("请选择有效的教学学期")
        return volume

    def _gap_items(
        self,
        volume_id: str,
        snapshot: Mapping[str, Any],
        volume: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            protected = teacher_protected_versions(conn)
        return gap_items(snapshot, volume_id, protected, volume)

    def _classify(
        self,
        state: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        snapshot: Mapping[str, Any],
        volume_id: str,
    ) -> list[dict[str, Any]]:
        pending: set[str] = set()
        for suggestion in state["suggestions"].values():
            if (
                isinstance(suggestion, Mapping)
                and suggestion.get("status") == "pending"
                and suggestion.get("curriculum_volume_id") == volume_id
            ):
                pending.update(
                    str(ref["gap_key"])
                    for ref in suggestion.get("gap_refs", [])
                    if isinstance(ref, Mapping)
                )
        approved: set[str] = set()
        published = set(snapshot["nodes"])
        for skill in state["approved_skills"].values():
            if not isinstance(skill, Mapping):
                continue
            if (
                skill.get("curriculum_volume_id") != volume_id
                or str(skill.get("skill_id") or "") in published
            ):
                continue
            approved.update(
                str(ref["gap_key"])
                for ref in skill.get("gap_refs", [])
                if isinstance(ref, Mapping)
            )
        dismissed = set(state["dismissed"])
        classified: list[dict[str, Any]] = []
        for item in items:
            gap_key = str(item["gap_key"])
            if gap_key in pending:
                status = "pending_review"
            elif gap_key in approved:
                status = "approved_new"
            elif gap_key in dismissed:
                status = "dismissed"
            elif not str(item["section_key"] or "") and not str(
                item["chapter_key"] or ""
            ):
                status = "unlocated"
            else:
                status = "ready"
            classified.append({**dict(item), "status": status})
        return classified

    @staticmethod
    def _fingerprint(
        volume_id: str, release: object, items: Sequence[Mapping[str, Any]]
    ) -> str:
        payload = {
            "curriculum_volume_id": volume_id,
            "graph_release_id": str(release or ""),
            "gap_keys": sorted(str(item["gap_key"]) for item in items),
        }
        return _sha256(payload)

    @staticmethod
    def _plan_batches(
        ready: Sequence[Mapping[str, Any]],
        volume: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        chapter_order = {
            str(chapter["knowledge_id"]): index
            for index, chapter in enumerate(volume["chapters"])
        }
        section_order = {
            str(section["knowledge_id"]): index
            for chapter in volume["chapters"]
            for index, section in enumerate(chapter["sections"])
        }
        by_chapter: dict[str, list[Mapping[str, Any]]] = {}
        for item in ready:
            by_chapter.setdefault(str(item["chapter_key"]), []).append(item)
        batches: list[dict[str, Any]] = []
        for chapter_key in sorted(
            by_chapter, key=lambda key: chapter_order.get(key, 9999)
        ):
            group = sorted(
                by_chapter[chapter_key],
                key=lambda item: (
                    section_order.get(str(item["section_key"]), 9999),
                    int(item["question_id"]),
                    int(item["number"]),
                ),
            )
            if len(group) <= _MAX_BATCH_GAPS:
                batches.append(
                    {"chapter_key": chapter_key, "gap_keys": [i["gap_key"] for i in group]}
                )
                continue
            by_section: dict[str, list[Mapping[str, Any]]] = {}
            for item in group:
                by_section.setdefault(str(item["section_key"]), []).append(item)
            for section_key in sorted(by_section, key=section_order.get):
                items = by_section[section_key]
                for offset in range(0, len(items), _MAX_BATCH_GAPS):
                    chunk = items[offset : offset + _MAX_BATCH_GAPS]
                    batches.append(
                        {
                            "chapter_key": chapter_key,
                            "gap_keys": [i["gap_key"] for i in chunk],
                        }
                    )
        return batches

    # ---------- preview + summary ----------

    def gap_preview(self, curriculum_volume_id: str) -> dict[str, Any]:
        volume = self._volume(curriculum_volume_id)
        snapshot = self._snapshot(curriculum_volume_id)
        items = self._gap_items(curriculum_volume_id, snapshot, volume)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            classified = self._classify(
                state, items, snapshot, curriculum_volume_id
            )
        fingerprint = self._fingerprint(
            curriculum_volume_id, snapshot["release"], items
        )
        ready = [item for item in classified if item["status"] == "ready"]
        ready.sort(
            key=lambda item: (
                str(item["chapter_key"]),
                str(item["section_key"]),
                int(item["question_id"]),
                int(item["number"]),
            )
        )
        batches = self._plan_batches(ready, volume)
        chapter_labels = {
            str(chapter["knowledge_id"]): str(
                chapter.get("label") or chapter["display_name"]
            )
            for chapter in volume["chapters"]
        }
        question_meta = self._question_meta(
            [int(item["question_id"]) for item in ready]
        )
        counts = {
            "total": len(classified),
            "ready": len(ready),
            "pending_review": sum(
                i["status"] == "pending_review" for i in classified
            ),
            "approved_new": sum(
                i["status"] == "approved_new" for i in classified
            ),
            "dismissed": sum(
                i["status"] == "dismissed" for i in classified
            ),
            "unlocated": sum(
                i["status"] == "unlocated" for i in classified
            ),
        }
        return {
            "curriculum_volume_id": curriculum_volume_id,
            "graph_release_id": snapshot["release"],
            "fingerprint": fingerprint,
            "counts": counts,
            "max_per_run": _MAX_READY_PER_RUN,
            "planned_requests": len(batches),
            "batches": [
                {
                    "chapter_key": batch["chapter_key"],
                    "chapter_label": chapter_labels.get(
                        batch["chapter_key"], ""
                    ),
                    "gap_count": len(batch["gap_keys"]),
                }
                for batch in batches
            ],
            "items": [
                {
                    "gap_key": item["gap_key"],
                    "question_id": item["question_id"],
                    "question_number": question_meta.get(
                        item["question_id"], {}
                    ).get("question_number", ""),
                    "paper_title": question_meta.get(
                        item["question_id"], {}
                    ).get("paper_title", ""),
                    "chapter_key": item["chapter_key"],
                    "section_key": item["section_key"],
                    "target": item["target"],
                }
                for item in ready[:_MAX_READY_PER_RUN]
            ],
            "model_calls": 0,
        }

    def _question_meta(
        self, question_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        ids = sorted({int(qid) for qid in question_ids if int(qid) > 0})
        if not ids:
            return {}
        try:
            loaded = self.read_service.get_questions(ids)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "optional question metadata unavailable (%s)",
                type(exc).__name__,
            )
            return {}
        meta: dict[int, dict[str, str]] = {}
        for raw in loaded:
            if not isinstance(raw, Mapping):
                continue
            try:
                qid = int(raw["id"])
            except (KeyError, TypeError, ValueError):
                continue
            meta[qid] = {
                "question_number": str(raw.get("question_number") or ""),
                "paper_title": str(raw.get("paper_title") or ""),
                "paper_id": int(raw.get("paper_id") or 0),
                "question_type": str(raw.get("question_type") or ""),
                "question_text": str(raw.get("question_text") or "")[:300],
            }
        return meta

    def _active_run(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        active = [
            run
            for run in state["runs"].values()
            if isinstance(run, dict)
            and run.get("status") in {"queued", "running", "cancelling"}
        ]
        if not active:
            return None
        active.sort(key=lambda run: str(run.get("created_at") or ""))
        run = active[-1]
        return {"run_id": str(run["run_id"]), "status": str(run["status"])}

    def summary(self, curriculum_volume_id: str) -> dict[str, Any]:
        volume = self._volume(curriculum_volume_id)
        snapshot = self._snapshot(curriculum_volume_id)
        items = self._gap_items(curriculum_volume_id, snapshot, volume)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            classified = self._classify(
                state, items, snapshot, curriculum_volume_id
            )
            pending_suggestions = sum(
                1
                for suggestion in state["suggestions"].values()
                if isinstance(suggestion, Mapping)
                and suggestion.get("status") == "pending"
                and suggestion.get("curriculum_volume_id")
                == curriculum_volume_id
            )
            published = set(snapshot["nodes"])
            approved_unpublished = sum(
                1
                for skill in state["approved_skills"].values()
                if isinstance(skill, Mapping)
                and skill.get("curriculum_volume_id") == curriculum_volume_id
                and str(skill.get("skill_id") or "") not in published
            )
            active_run = self._active_run(state)
            revision = int(state["revision"])
        counts = {
            "total": len(classified),
            "ready": sum(i["status"] == "ready" for i in classified),
            "pending_review": sum(
                i["status"] == "pending_review" for i in classified
            ),
            "approved_new": sum(
                i["status"] == "approved_new" for i in classified
            ),
            "dismissed": sum(
                i["status"] == "dismissed" for i in classified
            ),
            "unlocated": sum(
                i["status"] == "unlocated" for i in classified
            ),
        }
        gap_question_count = len(
            {
                int(i["question_id"])
                for i in classified
                if i["status"] in {"ready", "pending_review"}
            }
        )
        return {
            "curriculum_volume_id": curriculum_volume_id,
            "graph_release_id": snapshot["release"],
            "counts": counts,
            "gap_question_count": gap_question_count,
            "pending_suggestion_count": pending_suggestions,
            "approved_unpublished_skill_count": approved_unpublished,
            "active_run": active_run,
            "revision": revision,
            "model_calls": 0,
        }

    # ---------- run lifecycle ----------

    def create_run(
        self,
        *,
        curriculum_volume_id: str,
        fingerprint: str,
        request_token: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        volume = self._volume(curriculum_volume_id)
        command = {
            "curriculum_volume_id": curriculum_volume_id,
            "fingerprint": str(fingerprint or ""),
        }
        command_fingerprint = _sha256(command)
        snapshot = self._snapshot(curriculum_volume_id)
        items = self._gap_items(curriculum_volume_id, snapshot, volume)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            remembered = state["requests"].get(token)
            if remembered is not None:
                if (
                    remembered.get("kind") != "run"
                    or remembered.get("fingerprint") != command_fingerprint
                ):
                    raise SkillCandidateRequestConflict(
                        "Request token was already used for another command"
                    )
                return self._public_run(
                    self._require_run(state, remembered.get("run_id"))
                )
            if self._active_run(state) is not None:
                raise SkillCandidateBusy(
                    "Another skill candidate run is still active"
                )
            classified = self._classify(
                state, items, snapshot, curriculum_volume_id
            )
            current_fingerprint = self._fingerprint(
                curriculum_volume_id, snapshot["release"], items
            )
            if current_fingerprint != str(fingerprint or "").strip():
                raise SkillCandidateRevisionConflict(
                    "Skill gaps changed; refresh the preview and retry"
                )
            ready = [
                item for item in classified if item["status"] == "ready"
            ]
            if not ready:
                raise SkillCandidateInvalid(
                    "当前没有可整理成技能候选的判定点"
                )
            if len(ready) > _MAX_READY_PER_RUN:
                raise SkillCandidateInvalid(
                    f"一次整理最多 {_MAX_READY_PER_RUN} 个判定点"
                )
            planned = self._plan_batches(ready, volume)
            now = _now()
            run_id = uuid.uuid4().hex
            run = {
                "run_id": run_id,
                "curriculum_volume_id": curriculum_volume_id,
                "graph_release_id": str(snapshot["release"] or ""),
                "status": "queued",
                "stale": False,
                "cancellation_requested": False,
                "created_at": now,
                "updated_at": now,
                "batches": [
                    {
                        "batch_id": f"{index + 1}",
                        "chapter_key": batch["chapter_key"],
                        "gap_keys": list(batch["gap_keys"]),
                        "status": "pending",
                        "attempts": 0,
                        "error": None,
                        "uncovered_count": 0,
                    }
                    for index, batch in enumerate(planned)
                ],
            }
            state["runs"][run_id] = run
            state["requests"][token] = {
                "kind": "run",
                "fingerprint": command_fingerprint,
                "run_id": run_id,
            }
            self._trim_runs(state)
            self._write_state_unlocked(state)
            return self._public_run(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            return self._public_run(self._require_run(state, run_id))

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run["status"] in {"completed", "partial", "stale"}:
                return self._public_run(run)
            run["cancellation_requested"] = True
            for batch in run["batches"]:
                if batch["status"] == "pending":
                    batch["status"] = "cancelled"
            run["status"] = (
                "cancelling"
                if any(b["status"] == "running" for b in run["batches"])
                else "cancelled"
            )
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def recover_interrupted(
        self, run_id: str, *, cancelled: bool = False
    ) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run["status"] not in {"queued", "running", "cancelling"}:
                return self._public_run(run)
            for batch in run["batches"]:
                if batch["status"] not in {"pending", "running"}:
                    continue
                if cancelled:
                    batch["status"] = "cancelled"
                    batch["error"] = None
                else:
                    batch["status"] = "failed"
                    batch["error"] = {
                        "category": "interrupted",
                        "message": "应用在处理期间退出，可重新整理未完成章节。",
                    }
            run["cancellation_requested"] = bool(cancelled)
            statuses = [b["status"] for b in run["batches"]]
            if cancelled:
                run["status"] = "cancelled"
            elif "completed" in statuses:
                run["status"] = "partial"
            else:
                run["status"] = "failed"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def mark_interrupted(
        self, run_id: str, *, cancelled: bool = False
    ) -> dict[str, Any]:
        return self.recover_interrupted(run_id, cancelled=cancelled)

    # ---------- model-backed processing ----------

    def retry_failed(
        self,
        run_id: str,
        gateway: SkillCandidateGateway,
        *,
        concurrency: int = 3,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Re-send failed batches only; always a new explicit action."""
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run.get("stale"):
                return self._public_run(run)
            reset = 0
            for batch in run["batches"]:
                if batch["status"] == "failed":
                    batch["status"] = "pending"
                    batch["error"] = None
                    reset += 1
            if reset <= 0:
                return self._public_run(run)
            run["cancellation_requested"] = False
            run["status"] = "queued"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
        return self.process_run(
            run_id,
            gateway,
            concurrency=concurrency,
            progress_callback=progress_callback,
            cancel_requested=cancel_requested,
        )

    def process_run(
        self,
        run_id: str,
        gateway: SkillCandidateGateway,
        *,
        concurrency: int = 3,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        workers = max(1, int(concurrency))
        normalized = str(run_id or "").strip()
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, normalized)
            if normalized in _ACTIVE_RUNS:
                return self._public_run(run)
            if run["status"] in _TERMINAL_STATUSES:
                return self._public_run(run)
            if not self._release_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return self._public_run(run)
            for batch in run["batches"]:
                if batch["status"] == "running":
                    batch["status"] = "pending"
                    batch["error"] = None
            run["status"] = "running"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            _ACTIVE_RUNS.add(normalized)
        try:
            self._emit_progress(progress_callback, normalized)
            errors: list[BaseException] = []
            error_lock = threading.Lock()

            def worker() -> None:
                while True:
                    with error_lock:
                        if errors:
                            return
                    if _callback_requests_cancel(cancel_requested):
                        self.cancel_run(normalized)
                        self._emit_progress(progress_callback, normalized)
                        return
                    batch = self._claim_batch(normalized)
                    if batch is None:
                        return
                    try:
                        self._process_batch(normalized, batch, gateway)
                    except BaseException as exc:
                        with error_lock:
                            errors.append(exc)
                        return
                    self._emit_progress(progress_callback, normalized)

            if workers <= 1:
                worker()
            else:
                threads = [
                    threading.Thread(
                        target=worker,
                        name=f"skill-candidate-{normalized[:8]}-{index}",
                        daemon=True,
                    )
                    for index in range(workers)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            if errors:
                raise errors[0]
            result = self._finalize_run(normalized)
            _safe_progress_callback(progress_callback, result)
            return result
        finally:
            with _STATE_LOCK:
                _ACTIVE_RUNS.discard(normalized)

    def _release_is_current(self, run: Mapping[str, Any]) -> bool:
        try:
            snapshot = self._snapshot(str(run["curriculum_volume_id"]))
        except Exception:
            return False
        return str(snapshot["release"] or "") == str(
            run.get("graph_release_id") or ""
        )

    @staticmethod
    def _mark_stale(run: dict[str, Any]) -> None:
        run["stale"] = True
        run["status"] = "stale"
        for batch in run["batches"]:
            if batch["status"] in {"pending", "running"}:
                batch["status"] = "stale"
        run["updated_at"] = _now()

    def _claim_batch(
        self, run_id: str
    ) -> dict[str, Any] | None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._release_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return None
            if run.get("cancellation_requested"):
                for batch in run["batches"]:
                    if batch["status"] == "pending":
                        batch["status"] = "cancelled"
                run["updated_at"] = _now()
                self._write_state_unlocked(state)
                return None
            claimed = next(
                (
                    batch
                    for batch in run["batches"]
                    if batch["status"] == "pending"
                ),
                None,
            )
            if claimed is None:
                return None
            claimed["status"] = "running"
            claimed["attempts"] = int(claimed.get("attempts") or 0) + 1
            claimed["error"] = None
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return copy.deepcopy(claimed)

    def _process_batch(
        self,
        run_id: str,
        batch: Mapping[str, Any],
        gateway: SkillCandidateGateway,
    ) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = dict(self._require_run(state, run_id))
        volume_id = str(run["curriculum_volume_id"])
        volume = self._volume(volume_id)
        snapshot = self._snapshot(volume_id)
        current = {
            item["gap_key"]: item
            for item in self._gap_items(volume_id, snapshot, volume)
        }
        wanted = [
            current[key]
            for key in batch["gap_keys"]
            if key in current
        ]
        if not wanted:
            self._finish_batch(run_id, str(batch["batch_id"]), uncovered=0)
            return
        payload, alias_map, context = self._batch_payload(
            run, batch, volume, snapshot, wanted
        )
        try:
            response = gateway.suggest_skill_candidates(payload)
            groups = self._validated_groups(
                response, alias_map, context, wanted
            )
        except Exception:
            self._fail_batch(run_id, str(batch["batch_id"]))
            return
        suggestions = [
            self._new_suggestion(run, batch, group, current)
            for group in groups
        ]
        covered = {
            gap_key
            for group in groups
            for gap_key in group["gap_keys"]
        }
        uncovered = sum(
            1 for item in wanted if item["gap_key"] not in covered
        )
        self._finish_batch(
            run_id,
            str(batch["batch_id"]),
            uncovered=uncovered,
            suggestions=suggestions,
        )

    def _batch_payload(
        self,
        run: Mapping[str, Any],
        batch: Mapping[str, Any],
        volume: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        wanted: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        chapter_key = str(batch["chapter_key"])
        chapter = next(
            (
                item
                for item in volume["chapters"]
                if str(item["knowledge_id"]) == chapter_key
            ),
            {},
        )
        sections = [
            {
                "section_key": str(section["knowledge_id"]),
                "label": str(
                    section.get("label") or section["display_name"]
                ),
            }
            for section in chapter.get("sections", [])
        ]
        section_keys = {item["section_key"] for item in sections}
        chapter_skill_keys = {chapter_key, *section_keys}
        existing: list[dict[str, Any]] = []
        for key, node in snapshot["nodes"].items():
            if not str(key).startswith("sk_"):
                continue
            anchors = skill_anchor_ids(node, dict(volume))
            inside = [a for a in anchors if a in chapter_skill_keys]
            if not inside:
                continue
            anchor_section = next(
                (a for a in inside if a in section_keys), ""
            )
            existing.append(
                {
                    "skill_key": str(key),
                    "name": short_node_name(str(node["display_name"])),
                    "section_key": anchor_section,
                    "include": str(node.get("include_scope") or ""),
                    "exclude": str(node.get("exclude_scope") or ""),
                    "observable_evidence": str(
                        node.get("observable_evidence") or ""
                    ),
                }
            )
        existing.sort(key=lambda item: item["skill_key"])
        meta = self._question_meta(
            [int(item["question_id"]) for item in wanted]
        )
        alias_map: dict[str, str] = {}
        gap_points: list[dict[str, Any]] = []
        for index, item in enumerate(wanted):
            alias = f"g{index + 1}"
            alias_map[alias] = str(item["gap_key"])
            question = meta.get(int(item["question_id"]), {})
            gap_points.append(
                {
                    "gap_id": alias,
                    "section_key": str(item["section_key"]),
                    "question_type": question.get("question_type", ""),
                    "target": str(item["target"]),
                    "observable_evidence": str(
                        item["observable_evidence"]
                    ),
                    "question_excerpt": question.get(
                        "question_text", ""
                    ),
                }
            )
        payload = {
            "task": "Group the skill-gap evidence points of one chapter.",
            "chapter": {
                "chapter_key": chapter_key,
                "label": str(
                    chapter.get("label") or chapter.get("display_name") or ""
                ),
            },
            "sections": sections,
            "existing_skills": existing,
            "gap_points": gap_points,
        }
        context = {
            "existing_skill_keys": {
                item["skill_key"] for item in existing
            },
            "section_keys": section_keys,
        }
        return payload, alias_map, context

    @staticmethod
    def _validated_groups(
        response: object,
        alias_map: Mapping[str, str],
        context: Mapping[str, Any],
        wanted: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(response, Mapping):
            raise SkillCandidateInvalid("技能候选整理返回不是 JSON 对象")
        raw_groups = response.get("groups")
        if not isinstance(raw_groups, list):
            raise SkillCandidateInvalid("技能候选整理返回缺少 groups 列表")
        valid_keys = {str(item["gap_key"]) for item in wanted}
        seen: set[str] = set()
        groups: list[dict[str, Any]] = []
        for raw in raw_groups:
            if not isinstance(raw, Mapping):
                continue
            keys: list[str] = []
            for alias in raw.get("gap_ids") or []:
                gap_key = alias_map.get(str(alias))
                if (
                    gap_key is None
                    or gap_key not in valid_keys
                    or gap_key in seen
                    or gap_key in keys
                ):
                    continue
                keys.append(gap_key)
                seen.add(gap_key)
            if not keys:
                continue
            decision = str(raw.get("decision") or "").strip()
            if decision == "link_existing":
                skill_key = str(raw.get("skill_key") or "").strip()
                if skill_key not in context["existing_skill_keys"]:
                    continue
                groups.append(
                    {
                        "decision": "link_existing",
                        "skill_key": skill_key,
                        "new_skill": None,
                        "reason": str(raw.get("reason") or "").strip(),
                        "gap_keys": keys,
                    }
                )
            elif decision == "new_skill":
                new_skill = _clean_new_skill(
                    raw.get("new_skill"), context["section_keys"]
                )
                if new_skill is None:
                    continue
                groups.append(
                    {
                        "decision": "new_skill",
                        "skill_key": "",
                        "new_skill": new_skill,
                        "reason": str(raw.get("reason") or "").strip(),
                        "gap_keys": keys,
                    }
                )
            elif decision == "keep_section":
                groups.append(
                    {
                        "decision": "keep_section",
                        "skill_key": "",
                        "new_skill": None,
                        "reason": str(raw.get("reason") or "").strip(),
                        "gap_keys": keys,
                    }
                )
        return groups

    @staticmethod
    def _new_suggestion(
        run: Mapping[str, Any],
        batch: Mapping[str, Any],
        group: Mapping[str, Any],
        current: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        return {
            "suggestion_id": uuid.uuid4().hex,
            "run_id": str(run["run_id"]),
            "curriculum_volume_id": str(run["curriculum_volume_id"]),
            "graph_release_id": str(run["graph_release_id"]),
            "chapter_key": str(batch["chapter_key"]),
            "decision": str(group["decision"]),
            "skill_key": str(group["skill_key"]),
            "new_skill": (
                dict(group["new_skill"])
                if isinstance(group["new_skill"], Mapping)
                else None
            ),
            "reason": str(group["reason"]),
            "gap_refs": [
                {
                    "gap_key": gap_key,
                    "question_id": int(current[gap_key]["question_id"]),
                    "evidence_version_id": str(
                        current[gap_key]["evidence_version_id"]
                    ),
                    "point_id": str(current[gap_key]["point_id"]),
                    "part_id": str(current[gap_key]["part_id"]),
                    "target": str(current[gap_key]["target"]),
                    "section_key": str(current[gap_key]["section_key"]),
                }
                for gap_key in group["gap_keys"]
                if gap_key in current
            ],
            "status": "pending",
            "created_at": _now(),
            "reviewed_at": None,
            "result": None,
        }

    def _finish_batch(
        self,
        run_id: str,
        batch_id: str,
        *,
        uncovered: int,
        suggestions: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._release_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return
            batch = next(
                (
                    item
                    for item in run["batches"]
                    if str(item["batch_id"]) == batch_id
                ),
                None,
            )
            if batch is not None and batch["status"] == "running":
                batch["status"] = "completed"
                batch["uncovered_count"] = int(uncovered)
                batch["error"] = None
            for suggestion in suggestions:
                state["suggestions"][str(suggestion["suggestion_id"])] = dict(
                    suggestion
                )
            if run.get("cancellation_requested"):
                for item in run["batches"]:
                    if item["status"] == "pending":
                        item["status"] = "cancelled"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)

    def _fail_batch(
        self,
        run_id: str,
        batch_id: str,
        *,
        category: str = "model_gateway",
        message: str = "AI 整理暂时未返回，可重新整理该章节。",
    ) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._release_is_current(run):
                self._mark_stale(run)
            else:
                batch = next(
                    (
                        item
                        for item in run["batches"]
                        if str(item["batch_id"]) == batch_id
                    ),
                    None,
                )
                if batch is not None and batch["status"] == "running":
                    batch["status"] = "failed"
                    batch["error"] = {
                        "category": str(category),
                        "message": str(message),
                    }
                run["updated_at"] = _now()
            self._write_state_unlocked(state)

    def _finalize_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run.get("stale"):
                run["status"] = "stale"
            else:
                statuses = [b["status"] for b in run["batches"]]
                if run.get("cancellation_requested") or "cancelled" in statuses:
                    run["status"] = "cancelled"
                elif statuses and all(s == "completed" for s in statuses):
                    run["status"] = "completed"
                elif "completed" in statuses:
                    run["status"] = "partial"
                elif statuses and all(s == "failed" for s in statuses):
                    run["status"] = "failed"
                elif "failed" in statuses:
                    run["status"] = "partial"
                else:
                    run["status"] = "running"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def _emit_progress(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        run_id: str,
    ) -> None:
        if callback is None:
            return
        _safe_progress_callback(callback, self.get_run(run_id))

    # ---------- listing + review ----------

    def list_candidates(self, curriculum_volume_id: str) -> dict[str, Any]:
        self._volume(curriculum_volume_id)
        snapshot = self._snapshot(curriculum_volume_id)
        summary = self.summary(curriculum_volume_id)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            published = set(snapshot["nodes"])
            suggestions: list[dict[str, Any]] = []
            for raw in state["suggestions"].values():
                if (
                    not isinstance(raw, Mapping)
                    or raw.get("curriculum_volume_id") != curriculum_volume_id
                ):
                    continue
                item = copy.deepcopy(dict(raw))
                item["stale"] = bool(
                    item["status"] == "pending"
                    and str(item["graph_release_id"])
                    != str(snapshot["release"] or "")
                )
                suggestions.append(item)
            suggestions.sort(
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            approved: list[dict[str, Any]] = []
            for raw in state["approved_skills"].values():
                if (
                    not isinstance(raw, Mapping)
                    or raw.get("curriculum_volume_id") != curriculum_volume_id
                ):
                    continue
                approved.append(
                    {
                        **copy.deepcopy(dict(raw)),
                        "published": str(raw.get("skill_id") or "")
                        in published,
                    }
                )
            approved.sort(key=lambda item: str(item.get("approved_at") or ""))
            active_run = self._active_run(state)
            revision = int(state["revision"])
        question_ids = [
            ref.get("question_id")
            for item in suggestions
            for ref in item.get("gap_refs", [])
            if isinstance(ref, Mapping)
        ] + [
            ref.get("question_id")
            for item in approved
            for ref in item.get("gap_refs", [])
            if isinstance(ref, Mapping)
        ]
        meta = self._question_meta(
            [int(qid) for qid in question_ids if str(qid or "").isdigit()]
        )
        if meta:
            for item in (*suggestions, *approved):
                for ref in item.get("gap_refs", []):
                    if not isinstance(ref, dict):
                        continue
                    info = meta.get(int(ref.get("question_id") or 0))
                    if info:
                        ref.setdefault(
                            "question_number", info["question_number"]
                        )
                        ref.setdefault("paper_title", info["paper_title"])
                        if info["paper_id"]:
                            ref.setdefault("paper_id", info["paper_id"])
        return {
            "revision": revision,
            "graph_release_id": snapshot["release"],
            "summary": summary,
            "suggestions": suggestions,
            "approved_skills": approved,
            "active_run": active_run,
        }

    def review(
        self,
        suggestion_id: str,
        *,
        decision: str,
        expected_revision: int,
        request_token: str,
        edits: Mapping[str, Any] | None = None,
        gap_keys: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        sid = str(suggestion_id or "").strip()
        normalized = str(decision or "").strip()
        if normalized not in {"accept", "reject", "reopen"}:
            raise SkillCandidateInvalid("审核操作只能是接受、拒绝或撤回")
        command = {
            "suggestion_id": sid,
            "decision": normalized,
            "edits": _clean_edits_input(edits),
            "gap_keys": sorted(
                str(key) for key in (gap_keys or []) if str(key or "").strip()
            )
            if gap_keys is not None
            else None,
            "expected_revision": int(expected_revision),
        }
        fingerprint = _sha256(command)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            remembered = state["requests"].get(token)
            if remembered is not None:
                if (
                    remembered.get("kind") != "review"
                    or remembered.get("fingerprint") != fingerprint
                ):
                    raise SkillCandidateRequestConflict(
                        "Request token was already used for another command"
                    )
                return copy.deepcopy(remembered["result"])
            if int(state["revision"]) != int(expected_revision):
                raise SkillCandidateRevisionConflict(
                    "Skill candidate state changed; refresh and retry"
                )
            suggestion = state["suggestions"].get(sid)
            if not isinstance(suggestion, dict):
                raise SkillCandidateNotFound(
                    "Skill candidate suggestion does not exist"
                )
            result = self._apply_review_locked(
                state,
                suggestion,
                decision=normalized,
                edits=edits,
                gap_keys=gap_keys,
            )
            payload = {
                "suggestion": self._public_suggestion(suggestion),
                "result": result,
            }
            state["requests"][token] = {
                "kind": "review",
                "fingerprint": fingerprint,
                "result": copy.deepcopy(payload),
            }
            self._write_state_unlocked(state)
            return payload

    def _apply_review_locked(
        self,
        state: dict[str, Any],
        suggestion: dict[str, Any],
        *,
        decision: str,
        edits: Mapping[str, Any] | None,
        gap_keys: Sequence[str] | None,
    ) -> dict[str, Any]:
        sid = str(suggestion["suggestion_id"])
        volume_id = str(suggestion["curriculum_volume_id"])
        snapshot = self._snapshot(volume_id)
        release = str(snapshot["release"] or "")
        stale = str(suggestion["graph_release_id"]) != release
        if decision == "reopen":
            return self._reopen_locked(state, suggestion, snapshot)
        if suggestion["status"] != "pending":
            raise SkillCandidateInvalid("只有待审核的候选可以处理")
        if decision == "reject":
            suggestion["status"] = "rejected"
            suggestion["reviewed_at"] = _now()
            return {
                "rejected": [
                    str(ref["gap_key"])
                    for ref in suggestion["gap_refs"]
                ]
            }
        volume = self._volume(volume_id)
        kind, applied = self._review_kind(
            state, suggestion, edits, volume, snapshot
        )
        if stale and kind in {
            "link_existing",
            "new_skill",
            "merge_into_approved",
        }:
            raise SkillCandidateStale(
                "知识标准版本已更新，这条建议只能拒绝或保持只归小节"
            )
        refs = list(suggestion["gap_refs"])
        if gap_keys is not None:
            wanted = {str(key) for key in gap_keys if str(key or "").strip()}
            allowed = {str(ref["gap_key"]) for ref in refs}
            if not wanted or not wanted.issubset(allowed):
                raise SkillCandidateInvalid("所选判定点不属于这条候选")
            selected = [
                ref for ref in refs if str(ref["gap_key"]) in wanted
            ]
            suggestion["gap_refs"] = selected
        else:
            selected = refs
        if not selected:
            raise SkillCandidateInvalid("这条候选没有可处理的判定点")
        now = _now()
        suggestion["applied_decision"] = kind
        if kind == "link_existing":
            result = self._accept_links(
                suggestion, selected, str(applied["skill_key"]), snapshot
            )
        elif kind == "new_skill":
            skill_id = self._accept_new_skill(
                state,
                suggestion,
                selected,
                dict(applied["new_skill"]),
                snapshot,
                sid,
                now,
            )
            result = {"approved_skill_id": skill_id}
        elif kind == "merge_into_approved":
            skill_id = self._accept_merge(
                state,
                selected,
                str(applied["approved_skill_id"]),
                sid,
                snapshot,
            )
            result = {"approved_skill_id": skill_id}
        else:
            for ref in selected:
                state["dismissed"][str(ref["gap_key"])] = {
                    "suggestion_id": sid,
                    "dismissed_at": now,
                    "evidence_version_id": str(ref["evidence_version_id"]),
                }
            result = {
                "dismissed": [str(ref["gap_key"]) for ref in selected]
            }
        suggestion["status"] = "accepted"
        suggestion["reviewed_at"] = now
        suggestion["result"] = result
        return result

    def _review_kind(
        self,
        state: Mapping[str, Any],
        suggestion: Mapping[str, Any],
        edits: Mapping[str, Any] | None,
        volume: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        chapter_key = str(suggestion["chapter_key"])
        chapter = next(
            (
                item
                for item in volume["chapters"]
                if str(item["knowledge_id"]) == chapter_key
            ),
            {},
        )
        section_keys = {
            str(section["knowledge_id"])
            for section in chapter.get("sections", [])
        }
        anchored = self._chapter_skills(
            snapshot, volume, chapter_key, section_keys
        )
        if edits is None:
            kind = str(suggestion["decision"])
            if kind == "link_existing":
                skill_key = str(suggestion["skill_key"])
                if skill_key not in anchored:
                    raise SkillCandidateInvalid(
                        "建议链接的技能在当前版本中不可用"
                    )
                return kind, {"skill_key": skill_key}
            if kind == "new_skill":
                new_skill = _clean_new_skill(
                    suggestion.get("new_skill"), section_keys
                )
                if new_skill is None:
                    raise SkillCandidateInvalid("建议的新技能定义不完整")
                return kind, {"new_skill": new_skill}
            return "keep_section", {}
        kind = str(edits.get("kind") or "").strip()
        if kind not in _EDIT_KINDS:
            raise SkillCandidateInvalid("审核调整为无效的技能处理方式")
        if kind == "link_existing":
            skill_key = str(edits.get("skill_key") or "").strip()
            if skill_key not in anchored:
                raise SkillCandidateInvalid(
                    "所选技能不属于该章的当前技能"
                )
            return kind, {"skill_key": skill_key}
        if kind == "new_skill":
            new_skill = _clean_new_skill(edits, section_keys)
            if new_skill is None:
                raise SkillCandidateInvalid("新技能定义不完整")
            return kind, {"new_skill": new_skill}
        if kind == "merge_into_approved":
            skill_id = str(edits.get("approved_skill_id") or "").strip()
            skill = state["approved_skills"].get(skill_id)
            if (
                not isinstance(skill, dict)
                or str(skill.get("skill_id") or "") in snapshot["nodes"]
                or str(skill.get("curriculum_volume_id") or "")
                != str(suggestion["curriculum_volume_id"])
            ):
                raise SkillCandidateInvalid(
                    "只能并入本册尚未发布的已批准技能"
                )
            return kind, {"approved_skill_id": skill_id}
        return "keep_section", {}

    @staticmethod
    def _chapter_skills(
        snapshot: Mapping[str, Any],
        volume: Mapping[str, Any],
        chapter_key: str,
        section_keys: set[str],
    ) -> set[str]:
        wanted = {chapter_key, *section_keys}
        result: set[str] = set()
        for key, node in snapshot["nodes"].items():
            if str(key).startswith("sk_") and wanted & set(
                skill_anchor_ids(node, dict(volume))
            ):
                result.add(str(key))
        return result

    def _accept_links(
        self,
        suggestion: Mapping[str, Any],
        selected: Sequence[Mapping[str, Any]],
        skill_key: str,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Write one direct skill link per still-current gap point."""
        from question_bank.solution_evidence.knowledge_links import (
            link_points_to_skill,
        )

        volume_id = str(suggestion["curriculum_volume_id"])
        volume = self._volume(volume_id)
        current = {
            item["gap_key"]: item
            for item in self._gap_items(volume_id, snapshot, volume)
        }
        linked: list[str] = []
        skipped: list[dict[str, str]] = []
        by_question: dict[int, list[Mapping[str, Any]]] = {}
        for ref in selected:
            gap_key = str(ref["gap_key"])
            item = current.get(gap_key)
            if item is None:
                skipped.append(
                    {"gap_key": gap_key, "reason": "判定点状态已变化"}
                )
                continue
            by_question.setdefault(int(ref["question_id"]), []).append(ref)
        if by_question:
            conn = sqlite3.connect(self.db_path)
            try:
                for question_id, refs in by_question.items():
                    conn.execute("BEGIN IMMEDIATE")
                    link_points_to_skill(
                        conn,
                        db_path=self.db_path,
                        question_id=question_id,
                        evidence_version_id=str(
                            refs[0]["evidence_version_id"]
                        ),
                        graph_release_id=str(
                            suggestion["graph_release_id"]
                        ),
                        point_skills={
                            str(ref["point_id"]): skill_key
                            for ref in refs
                        },
                        part_ids={
                            str(ref["point_id"]): str(ref["part_id"])
                            for ref in refs
                        },
                        source_reference=(
                            f"skill_candidate:{suggestion['suggestion_id']}"
                        ),
                    )
                    conn.commit()
                    linked.extend(str(ref["gap_key"]) for ref in refs)
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        return {"linked": linked, "skipped": skipped}

    def _accept_new_skill(
        self,
        state: dict[str, Any],
        suggestion: Mapping[str, Any],
        selected: Sequence[Mapping[str, Any]],
        new_skill: dict[str, Any],
        snapshot: Mapping[str, Any],
        sid: str,
        now: str,
    ) -> str:
        skill_id = self._next_skill_id(
            state, snapshot, str(new_skill["section_key"])
        )
        state["approved_skills"][skill_id] = {
            "skill_id": skill_id,
            "curriculum_volume_id": str(
                suggestion["curriculum_volume_id"]
            ),
            "chapter_key": str(suggestion["chapter_key"]),
            "section_key": str(new_skill["section_key"]),
            "name": str(new_skill["name"]),
            "include": str(new_skill["include"]),
            "exclude": str(new_skill["exclude"]),
            "examples": list(new_skill["examples"]),
            "gap_refs": [dict(ref) for ref in selected],
            "approved_at": now,
            "source_suggestion_ids": [sid],
        }
        return skill_id

    def _accept_merge(
        self,
        state: dict[str, Any],
        selected: Sequence[Mapping[str, Any]],
        skill_id: str,
        sid: str,
        snapshot: Mapping[str, Any],
    ) -> str:
        skill = state["approved_skills"][skill_id]
        if str(skill.get("skill_id") or "") in snapshot["nodes"]:
            raise SkillCandidateStale("目标技能已经发布，不能并入")
        existing = {
            str(ref["gap_key"])
            for ref in skill["gap_refs"]
            if isinstance(ref, Mapping)
        }
        for ref in selected:
            if str(ref["gap_key"]) not in existing:
                skill["gap_refs"].append(dict(ref))
        if sid not in skill["source_suggestion_ids"]:
            skill["source_suggestion_ids"].append(sid)
        return skill_id

    def _next_skill_id(
        self,
        state: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        section_key: str,
    ) -> str:
        prefix = f"sk_{section_key.removeprefix('kp_')}_"
        best = 100
        for key in list(snapshot["nodes"]) + list(state["approved_skills"]):
            match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", str(key))
            if match:
                best = max(best, int(match.group(1)))
        return f"{prefix}{best + 1}"

    def _reopen_locked(
        self,
        state: dict[str, Any],
        suggestion: dict[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        if suggestion["status"] != "accepted":
            raise SkillCandidateInvalid("只有已接受的候选可以撤回")
        kind = str(
            suggestion.get("applied_decision") or suggestion["decision"]
        )
        if kind == "link_existing":
            raise SkillCandidateInvalid("已写入技能链接的候选不能撤回")
        sid = str(suggestion["suggestion_id"])
        if kind in {"new_skill", "merge_into_approved"}:
            result = suggestion.get("result") or {}
            skill_id = str(result.get("approved_skill_id") or "")
            skill = state["approved_skills"].get(skill_id)
            if isinstance(skill, dict):
                if str(skill.get("skill_id") or "") in snapshot["nodes"]:
                    raise SkillCandidateStale(
                        "技能已经发布，不能撤回批准"
                    )
                removed = {
                    str(ref["gap_key"]) for ref in suggestion["gap_refs"]
                }
                skill["gap_refs"] = [
                    ref
                    for ref in skill["gap_refs"]
                    if str(ref["gap_key"]) not in removed
                ]
                if sid in skill["source_suggestion_ids"]:
                    skill["source_suggestion_ids"].remove(sid)
                if not skill["gap_refs"]:
                    del state["approved_skills"][skill_id]
        else:
            for ref in suggestion["gap_refs"]:
                entry = state["dismissed"].get(str(ref["gap_key"]))
                if (
                    isinstance(entry, Mapping)
                    and entry.get("suggestion_id") == sid
                ):
                    del state["dismissed"][str(ref["gap_key"])]
        suggestion["status"] = "pending"
        suggestion["reviewed_at"] = None
        suggestion["result"] = None
        suggestion.pop("applied_decision", None)
        return {
            "reopened": [
                str(ref["gap_key"]) for ref in suggestion["gap_refs"]
            ]
        }

    def update_approved(
        self,
        skill_id: str,
        *,
        name: str,
        include: str,
        exclude: str,
        examples: Sequence[str],
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        key = str(skill_id or "").strip()
        command = {
            "skill_id": key,
            "name": str(name or ""),
            "include": str(include or ""),
            "exclude": str(exclude or ""),
            "examples": [str(item) for item in examples],
            "expected_revision": int(expected_revision),
        }
        fingerprint = _sha256(command)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            remembered = state["requests"].get(token)
            if remembered is not None:
                if (
                    remembered.get("kind") != "update_approved"
                    or remembered.get("fingerprint") != fingerprint
                ):
                    raise SkillCandidateRequestConflict(
                        "Request token was already used for another command"
                    )
                return copy.deepcopy(remembered["result"])
            if int(state["revision"]) != int(expected_revision):
                raise SkillCandidateRevisionConflict(
                    "Skill candidate state changed; refresh and retry"
                )
            skill = state["approved_skills"].get(key)
            if not isinstance(skill, dict):
                raise SkillCandidateNotFound(
                    "Approved skill does not exist"
                )
            volume_id = str(skill["curriculum_volume_id"])
            snapshot = self._snapshot(volume_id)
            if key in snapshot["nodes"]:
                raise SkillCandidateStale("技能已经发布，不能再修改")
            volume = self._volume(volume_id)
            cleaned = _clean_new_skill(
                {
                    "section_key": skill["section_key"],
                    "name": name,
                    "include": include,
                    "exclude": exclude,
                    "examples": list(examples),
                },
                {
                    str(section["knowledge_id"])
                    for chapter in volume["chapters"]
                    for section in chapter["sections"]
                },
            )
            if cleaned is None:
                raise SkillCandidateInvalid("技能定义不完整")
            skill["name"] = cleaned["name"]
            skill["include"] = cleaned["include"]
            skill["exclude"] = cleaned["exclude"]
            skill["examples"] = cleaned["examples"]
            payload = {
                "approved_skill": {
                    **copy.deepcopy(skill),
                    "published": False,
                }
            }
            state["requests"][token] = {
                "kind": "update_approved",
                "fingerprint": fingerprint,
                "result": copy.deepcopy(payload),
            }
            self._write_state_unlocked(state)
            return payload

    # ---------- shared state helpers ----------

    @staticmethod
    def _public_run(run: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(run))
        result.pop("cancellation_requested", None)
        statuses = [
            str(batch.get("status") or "")
            for batch in result.get("batches", [])
            if isinstance(batch, Mapping)
        ]
        result["progress"] = {
            "total": len(statuses),
            "processed": sum(
                status in {"completed", "failed", "cancelled", "stale"}
                for status in statuses
            ),
            "completed": sum(status == "completed" for status in statuses),
            "failed": sum(status == "failed" for status in statuses),
            "pending": sum(
                status in {"pending", "running"} for status in statuses
            ),
            "cancelled": sum(status == "cancelled" for status in statuses),
        }
        result["retryable"] = not bool(result.get("stale")) and any(
            status == "failed" for status in statuses
        )
        return result

    @staticmethod
    def _public_suggestion(
        suggestion: Mapping[str, Any]
    ) -> dict[str, Any]:
        item = copy.deepcopy(dict(suggestion))
        item.pop("applied_decision", None)
        return item

    @staticmethod
    def _require_run(
        state: Mapping[str, Any], run_id: object
    ) -> dict[str, Any]:
        normalized = str(run_id or "").strip()
        run = state["runs"].get(normalized)
        if not isinstance(run, dict):
            raise SkillCandidateNotFound(
                "Skill candidate run does not exist"
            )
        return run

    @staticmethod
    def _trim_runs(state: dict[str, Any]) -> None:
        while len(state["runs"]) > _MAX_RUNS:
            terminal = [
                run
                for run in state["runs"].values()
                if run.get("status") in _TERMINAL_STATUSES
            ]
            if not terminal:
                return
            oldest = min(
                terminal, key=lambda run: str(run.get("created_at") or "")
            )
            del state["runs"][str(oldest["run_id"])]

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _empty_state()
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("runs"), Mapping)
            or not isinstance(payload.get("requests"), Mapping)
            or not isinstance(payload.get("suggestions"), Mapping)
            or not isinstance(payload.get("approved_skills"), Mapping)
            or not isinstance(payload.get("dismissed"), Mapping)
        ):
            raise RuntimeError("Skill candidate state is invalid")
        return {
            "schema_version": 1,
            "revision": int(payload.get("revision") or 0),
            "runs": copy.deepcopy(dict(payload["runs"])),
            "requests": copy.deepcopy(dict(payload["requests"])),
            "suggestions": copy.deepcopy(dict(payload["suggestions"])),
            "approved_skills": copy.deepcopy(
                dict(payload["approved_skills"])
            ),
            "dismissed": copy.deepcopy(dict(payload["dismissed"])),
        }

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision") or 0) + 1
        _write_json_atomic(self.state_path, state)


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": 0,
        "runs": {},
        "requests": {},
        "suggestions": {},
        "approved_skills": {},
        "dismissed": {},
    }


def _clean_new_skill(
    raw: object, section_keys: set[str]
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    section_key = str(raw.get("section_key") or "").strip()
    name = " ".join(str(raw.get("name") or "").split())
    include = " ".join(str(raw.get("include") or "").split())
    exclude = " ".join(str(raw.get("exclude") or "").split())
    if section_key not in section_keys:
        return None
    if not 2 <= len(name) <= 30:
        return None
    if not include or not exclude:
        return None
    examples = [
        " ".join(str(item or "").split())
        for item in (raw.get("examples") or [])
        if " ".join(str(item or "").split())
    ]
    if not 1 <= len(examples):
        return None
    return {
        "section_key": section_key,
        "name": name,
        "include": include,
        "exclude": exclude,
        "examples": examples[:3],
    }


def _clean_edits_input(edits: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(edits, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, value in edits.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
        elif isinstance(value, (list, tuple)):
            result[str(key)] = [str(item) for item in value]
    return result


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if _REQUEST_TOKEN.fullmatch(token) is None:
        raise SkillCandidateInvalid(
            "request_token must contain 32 hexadecimal characters"
        )
    return token


def _sha256(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _callback_requests_cancel(
    callback: Callable[[], bool] | None,
) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional cancel check unavailable (%s)", type(exc).__name__
        )
        return False


def _safe_progress_callback(
    callback: Callable[[dict[str, Any]], None] | None,
    snapshot: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(copy.deepcopy(snapshot))
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional progress callback unavailable (%s)",
            type(exc).__name__,
        )


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "SkillCandidateBusy",
    "SkillCandidateGateway",
    "SkillCandidateInvalid",
    "SkillCandidateNotFound",
    "SkillCandidateRequestConflict",
    "SkillCandidateRevisionConflict",
    "SkillCandidateService",
    "SkillCandidateStale",
]
