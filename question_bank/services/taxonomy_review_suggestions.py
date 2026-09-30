from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from question_bank.taxonomy.curriculum_catalog import (
    curriculum_volume,
    eligible_curriculum_knowledge_nodes,
)
from question_bank.taxonomy.governance import TaxonomyGovernance

_STATE_LOCK = threading.RLock()
_ACTIVE_RUNS: set[str] = set()
_COMPOSITE_SEPARATOR = re.compile(r"[，,、；;\n]+")
_TERMINAL_STATUSES = frozenset(
    {"completed", "partial", "failed", "cancelled", "stale"}
)
_ALLOWED_RELATION_KINDS = frozenset(
    {
        "exact",
        "broader",
        "narrower",
        "related",
        "new_core_candidate",
        "wrong_dimension",
        "reject",
        "uncertain",
    }
)


class TaxonomyReviewSuggestionGateway(Protocol):
    def suggest_taxonomy_reviews(
        self,
        batch: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        """Return suggestions only; this protocol never approves a proposal."""


QuestionLoader = Callable[
    [Sequence[int]],
    Sequence[Mapping[str, Any]],
]


class TaxonomySuggestionNotFound(LookupError):
    """The requested suggestion run does not exist."""


class TaxonomySuggestionRequestConflict(RuntimeError):
    """A request token was reused with a different create command."""


class TaxonomySuggestionRevisionConflict(RuntimeError):
    """The proposal list changed before a suggestion run could be created."""


class TaxonomySuggestionInvalid(ValueError):
    """The requested proposal selection is invalid."""


class TaxonomySuggestionModelResponseError(ValueError):
    """The model replied, but its suggestion payload was unusable."""


class TaxonomySuggestionService:
    """Persist resumable, teacher-review-only taxonomy suggestions."""

    def __init__(
        self,
        *,
        state_path: Path,
        governance: TaxonomyGovernance,
        question_loader: QuestionLoader | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.governance = governance
        self.question_loader = question_loader

    def create_run(
        self,
        *,
        proposal_ids: Sequence[str],
        expected_revision: int,
        request_token: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        selected = _unique_strings(proposal_ids)
        if not selected:
            raise TaxonomySuggestionInvalid(
                "At least one pending proposal must be selected"
            )
        if len(selected) > 200:
            raise TaxonomySuggestionInvalid(
                "A suggestion run can contain at most 200 proposals"
            )
        command = {
            "proposal_ids": sorted(selected),
            "expected_revision": int(expected_revision),
        }
        fingerprint = _fingerprint(command)

        with _STATE_LOCK:
            state = self._read_state_unlocked()
            remembered = state["requests"].get(token)
            if remembered is not None:
                if remembered.get("fingerprint") != fingerprint:
                    raise TaxonomySuggestionRequestConflict(
                        "Request token was already used for another suggestion run"
                    )
                return self._public_run(
                    self._require_run(state, remembered.get("run_id"))
                )

            proposal_page = self.contextualize_proposal_page(
                self.governance.list_proposals(status="pending")
            )
            current_revision = int(proposal_page["revision"])
            if current_revision != int(expected_revision):
                raise TaxonomySuggestionRevisionConflict(
                    "Taxonomy revision changed; refresh pending proposals"
                )
            pending_by_id = {
                str(item["id"]): item
                for item in proposal_page["items"]
            }
            missing = [
                proposal_id
                for proposal_id in selected
                if proposal_id not in pending_by_id
            ]
            if missing:
                raise TaxonomySuggestionInvalid(
                    "Only current pending proposals can be suggested"
                )
            for existing in state["runs"].values():
                existing_ids = (
                    [
                        str(item.get("proposal_id") or "")
                        for item in existing.get("items", [])
                        if isinstance(item, Mapping)
                    ]
                    if isinstance(existing, dict)
                    else []
                )
                if (
                    isinstance(existing, dict)
                    and int(existing.get("taxonomy_revision", -1))
                    == current_revision
                    and len(existing_ids) == len(selected)
                    and set(existing_ids) == set(selected)
                ):
                    state["requests"][token] = {
                        "fingerprint": fingerprint,
                        "run_id": existing["run_id"],
                    }
                    self._write_state_unlocked(state)
                    return self._public_run(existing)

            now = _now()
            run_id = uuid.uuid4().hex
            run = {
                "run_id": run_id,
                "status": "queued",
                "taxonomy_revision": current_revision,
                "evidence_revision": int(
                    proposal_page.get("evidence_revision") or 0
                ),
                "graph_release_id": str(
                    self.governance.observation_snapshot().get(
                        "graph_release_id"
                    )
                    or ""
                ),
                "stale": False,
                "cancellation_requested": False,
                "created_at": now,
                "updated_at": now,
                "items": [
                    {
                        "proposal_id": proposal_id,
                        "dimension": pending_by_id[proposal_id]["dimension"],
                        "proposed_name": pending_by_id[proposal_id][
                            "proposed_name"
                        ],
                        "question_refs": _positive_ids(
                            pending_by_id[proposal_id].get(
                                "active_question_refs", []
                            )
                        ),
                        "taxonomy_revision": current_revision,
                        "evidence_revision": int(
                            proposal_page.get("evidence_revision") or 0
                        ),
                        "graph_release_id": str(
                            self.governance.observation_snapshot().get(
                                "graph_release_id"
                            )
                            or ""
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "suggestion": None,
                        "error": None,
                    }
                    for proposal_id in selected
                ],
            }
            state["runs"][run_id] = run
            state["requests"][token] = {
                "fingerprint": fingerprint,
                "run_id": run_id,
            }
            self._write_state_unlocked(state)
            return self._public_run(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            return self._public_run(self._require_run(state, run_id))

    def contextualize_proposal_page(
        self,
        page: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Derive current question refs without rewriting historical evidence."""

        result = copy.deepcopy(dict(page))
        items = [
            item
            for item in result.get("items", [])
            if isinstance(item, dict)
        ]
        all_refs = _positive_ids(
            question_id
            for item in items
            for question_id in item.get(
                "active_question_refs", item.get("question_refs", [])
            )
        )
        active_ids = self._active_question_ids(all_refs)
        actionable = 0
        visible_items: list[dict[str, Any]] = []
        for item in items:
            current_refs = _positive_ids(
                item.get("active_question_refs", [])
            )
            active_refs = [
                question_id
                for question_id in current_refs
                if question_id in active_ids
            ]
            if not active_refs:
                continue
            item["question_refs"] = active_refs
            item["active_question_refs"] = active_refs
            item["unavailable_question_ref_count"] = 0
            item["actionable"] = True
            actionable += 1
            visible_items.append(item)
        counts = dict(result.get("counts") or {})
        counts["pending"] = actionable
        counts["actionable"] = actionable
        counts.pop("historical_unavailable", None)
        result["items"] = visible_items
        result["counts"] = counts
        return result

    def proposal_summary(self) -> dict[str, Any]:
        """Count actionable proposals without constructing the review page."""

        snapshot = self.governance.pending_proposal_reference_summary()
        refs_by_proposal = snapshot["question_refs_by_proposal"]
        all_refs = _positive_ids(
            question_id
            for question_refs in refs_by_proposal.values()
            for question_id in question_refs
        )
        active_ids = self._active_question_ids(all_refs)
        actionable = sum(
            any(question_id in active_ids for question_id in question_refs)
            for question_refs in refs_by_proposal.values()
        )
        return {
            "revision": snapshot["revision"],
            "evidence_revision": snapshot["evidence_revision"],
            "items": [],
            "counts": {
                "pending": actionable,
                "actionable": actionable,
                "historical_unavailable": 0,
            },
        }

    def _active_question_ids(self, question_ids: Sequence[object]) -> set[int]:
        normalized = _positive_ids(question_ids)
        active_ids = set(normalized)
        if not normalized or self.question_loader is None:
            return active_ids
        try:
            loaded = self.question_loader(normalized)
        except Exception:
            return active_ids
        return {
            int(raw.get("id"))
            for raw in loaded
            if isinstance(raw, Mapping)
            and str(raw.get("id") or "").isdigit()
        }

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run["status"] in {"completed", "stale"}:
                return self._public_run(run)
            run["cancellation_requested"] = True
            for item in run["items"]:
                if item["status"] == "pending":
                    item["status"] = "cancelled"
            run["status"] = (
                "cancelling"
                if any(item["status"] == "running" for item in run["items"])
                else "cancelled"
            )
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def recover_interrupted(
        self,
        run_id: str,
        *,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        """Close an orphaned active run while preserving completed suggestions."""

        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run["status"] not in {"queued", "running", "cancelling"}:
                return self._public_run(run)
            for item in run["items"]:
                if item["status"] not in {"pending", "running"}:
                    continue
                if cancelled:
                    item["status"] = "cancelled"
                    item["error"] = None
                else:
                    item["status"] = "failed"
                    item["error"] = {
                        "category": "interrupted",
                        "message": "应用在处理期间退出，可继续未完成项目。",
                    }
            run["cancellation_requested"] = bool(cancelled)
            statuses = [item["status"] for item in run["items"]]
            if cancelled:
                run["status"] = "cancelled"
            elif "suggested" in statuses:
                run["status"] = "partial"
            else:
                run["status"] = "failed"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def mark_interrupted(
        self,
        run_id: str,
        *,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        return self.recover_interrupted(run_id, cancelled=cancelled)

    def retry_failed(
        self,
        run_id: str,
        gateway: TaxonomyReviewSuggestionGateway,
        *,
        batch_size: int = 8,
        concurrency: int = 1,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run.get("stale"):
                return self._public_run(run)
            reset_count = 0
            for item in run["items"]:
                if item["status"] in {"failed", "cancelled", "running"}:
                    item["status"] = "pending"
                    item["error"] = None
                    reset_count += 1
            if reset_count <= 0:
                return self._public_run(run)
            run["cancellation_requested"] = False
            run["status"] = "queued"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
        return self.process_run(
            run_id,
            gateway,
            batch_size=batch_size,
            concurrency=concurrency,
            progress_callback=progress_callback,
            cancel_requested=cancel_requested,
        )

    def process_run(
        self,
        run_id: str,
        gateway: TaxonomyReviewSuggestionGateway,
        *,
        batch_size: int = 8,
        concurrency: int = 1,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        size = int(batch_size)
        if size <= 0:
            raise ValueError("batch_size must be positive")
        workers = max(1, int(concurrency))
        normalized_run_id = str(run_id or "").strip()
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, normalized_run_id)
            if normalized_run_id in _ACTIVE_RUNS:
                return self._public_run(run)
            if run["status"] in {"completed", "cancelled", "stale"}:
                return self._public_run(run)
            if not self._revision_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return self._public_run(run)
            # A persisted running item has no live worker after process restart.
            for item in run["items"]:
                if item["status"] == "running":
                    item["status"] = "pending"
                    item["error"] = None
            run["status"] = "running"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            _ACTIVE_RUNS.add(normalized_run_id)

        try:
            self._apply_local_suggestions(normalized_run_id)
            self._emit_progress(progress_callback, normalized_run_id)
            self._run_batch_workers(
                normalized_run_id,
                gateway,
                batch_size=size,
                workers=workers,
                progress_callback=progress_callback,
                cancel_requested=cancel_requested,
            )
            result = self._finalize_run(normalized_run_id)
            _safe_progress_callback(progress_callback, result)
            return result
        finally:
            with _STATE_LOCK:
                _ACTIVE_RUNS.discard(normalized_run_id)

    def _run_batch_workers(
        self,
        run_id: str,
        gateway: TaxonomyReviewSuggestionGateway,
        *,
        batch_size: int,
        workers: int,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        """Process claimed batches with a bounded worker pool.

        Only the model gateway call overlaps between workers; every state
        read/write stays serialized under _STATE_LOCK.
        """
        errors: list[BaseException] = []
        error_lock = threading.Lock()

        def worker() -> None:
            while True:
                with error_lock:
                    if errors:
                        return
                if _callback_requests_cancel(cancel_requested):
                    self.cancel_run(run_id)
                    self._emit_progress(progress_callback, run_id)
                    return
                batch = self._claim_batch(run_id, batch_size)
                if not batch:
                    return
                try:
                    self._process_claimed_batch(run_id, batch, gateway)
                except BaseException as exc:
                    with error_lock:
                        errors.append(exc)
                    return
                self._emit_progress(progress_callback, run_id)

        if workers <= 1:
            worker()
        else:
            threads = [
                threading.Thread(
                    target=worker,
                    name=f"taxonomy-suggestion-{run_id[:8]}-{index}",
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

    def _process_claimed_batch(
        self,
        run_id: str,
        batch: Sequence[dict[str, Any]],
        gateway: TaxonomyReviewSuggestionGateway,
    ) -> None:
        prepared = [
            (item, self._gateway_item(item)) for item in batch
        ]
        without_context = [
            item["proposal_id"]
            for item, payload in prepared
            if not payload["question_summaries"]
        ]
        if without_context:
            self._finish_batch_failure(
                run_id,
                without_context,
                category="question_context",
                message=(
                    "当前题库中已没有可核对的关联题目；"
                    "恢复原试卷后可重试。"
                ),
            )
        eligible = [
            (item, payload)
            for item, payload in prepared
            if payload["question_summaries"]
        ]
        if not eligible:
            return
        eligible_batch = [item for item, _payload in eligible]
        payload = [payload for _item, payload in eligible]
        proposal_ids = [item["proposal_id"] for item in eligible_batch]
        try:
            response = gateway.suggest_taxonomy_reviews(payload)
            suggestions = self._validated_gateway_response(
                eligible_batch,
                response,
            )
        except TaxonomySuggestionModelResponseError:
            self._finish_batch_failure(
                run_id,
                proposal_ids,
                category="model_response",
                message=(
                    "AI 已返回内容，但格式无法读取，可单独重试。"
                ),
            )
        except Exception:
            self._finish_batch_failure(
                run_id,
                proposal_ids,
                category="model_gateway",
                message="AI 建议暂时未返回，可单独重试。",
            )
        else:
            self._finish_batch_success(
                run_id,
                suggestions,
                proposal_ids=proposal_ids,
            )

    def _apply_local_suggestions(self, run_id: str) -> None:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run.get("cancellation_requested"):
                return
            changed = False
            for item in run["items"]:
                if item["status"] != "pending":
                    continue
                suggestion = self._local_suggestion(item)
                if suggestion is None:
                    continue
                item["status"] = "suggested"
                item["suggestion"] = suggestion
                item["error"] = None
                changed = True
            if changed:
                run["updated_at"] = _now()
                self._write_state_unlocked(state)

    def _local_suggestion(
        self,
        item: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        dimension = str(item.get("dimension") or "")
        if dimension not in {"curriculum", "knowledge"}:
            return None
        parts = _unique_strings(
            _COMPOSITE_SEPARATOR.split(
                str(item.get("proposed_name") or "")
            )
        )
        if len(parts) < 2:
            return None
        terms = [
            self.governance.resolve_term(dimension, part)
            for part in parts
        ]
        if any(term is None for term in terms):
            return None
        target_ids = _unique_strings(
            term["id"] for term in terms if term is not None
        )
        if len(target_ids) < 2:
            return None
        if dimension == "knowledge":
            summaries = self._question_summaries(
                item.get("question_refs", [])
            )
            volume_ids = self._curriculum_volume_ids(summaries)
            if volume_ids:
                allowed_ids = {
                    str(node["id"])
                    for volume_id in volume_ids
                    for node in eligible_curriculum_knowledge_nodes(
                        volume_id
                    )
                }
                if not set(target_ids).issubset(allowed_ids):
                    return None
        return {
            "relation_kind": "related",
            "target_term_ids": target_ids,
            "reason": (
                "名称已在本地拆分，并精确匹配到多个现有知识点。"
                if dimension == "knowledge"
                else "名称可安全拆分并精确匹配到多个现有教材章节。"
            ),
            "confidence": 1.0,
            "source": "local_exact",
            "legacy_format": False,
            "evidence_question_ids": _positive_ids(
                item.get("question_refs", [])
            ),
            "taxonomy_revision": int(item.get("taxonomy_revision") or 0),
            "graph_release_id": str(item.get("graph_release_id") or ""),
        }

    def _claim_batch(
        self,
        run_id: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._revision_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return []
            if run.get("cancellation_requested"):
                for item in run["items"]:
                    if item["status"] == "pending":
                        item["status"] = "cancelled"
                run["updated_at"] = _now()
                self._write_state_unlocked(state)
                return []
            claimed = [
                item
                for item in run["items"]
                if item["status"] == "pending"
            ][:batch_size]
            for item in claimed:
                item["status"] = "running"
                item["attempts"] = int(item.get("attempts") or 0) + 1
                item["error"] = None
            if claimed:
                run["updated_at"] = _now()
                self._write_state_unlocked(state)
            return copy.deepcopy(claimed)

    def _finish_batch_failure(
        self,
        run_id: str,
        proposal_ids: Sequence[str],
        *,
        category: str,
        message: str,
    ) -> None:
        wanted = set(proposal_ids)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._revision_is_current(run):
                self._mark_stale(run)
            else:
                for item in run["items"]:
                    if (
                        item["proposal_id"] in wanted
                        and item["status"] == "running"
                    ):
                        item["status"] = "failed"
                        item["error"] = {
                            "category": str(category),
                            "message": str(message),
                        }
                run["updated_at"] = _now()
            self._write_state_unlocked(state)

    def _finish_batch_success(
        self,
        run_id: str,
        suggestions: Mapping[str, Mapping[str, Any]],
        *,
        proposal_ids: Sequence[str],
    ) -> None:
        wanted = set(proposal_ids)
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if not self._revision_is_current(run):
                self._mark_stale(run)
                self._write_state_unlocked(state)
                return
            for item in run["items"]:
                if (
                    item["status"] != "running"
                    or item["proposal_id"] not in wanted
                ):
                    continue
                suggestion = suggestions.get(item["proposal_id"])
                if suggestion is None:
                    item["status"] = "failed"
                    item["error"] = {
                        "category": "model_response",
                        "message": "AI 返回中缺少这个待审词，可单独重试。",
                    }
                    continue
                item["status"] = "suggested"
                item["suggestion"] = dict(suggestion)
                item["error"] = None
            if run.get("cancellation_requested"):
                for item in run["items"]:
                    if item["status"] == "pending":
                        item["status"] = "cancelled"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)

    def _finalize_run(self, run_id: str) -> dict[str, Any]:
        with _STATE_LOCK:
            state = self._read_state_unlocked()
            run = self._require_run(state, run_id)
            if run.get("stale"):
                run["status"] = "stale"
            else:
                statuses = [item["status"] for item in run["items"]]
                if run.get("cancellation_requested") or "cancelled" in statuses:
                    run["status"] = "cancelled"
                elif statuses and all(
                    status == "suggested" for status in statuses
                ):
                    run["status"] = "completed"
                elif "suggested" in statuses and "failed" in statuses:
                    run["status"] = "partial"
                elif statuses and all(status == "failed" for status in statuses):
                    run["status"] = "failed"
                elif "failed" in statuses:
                    run["status"] = "partial"
                else:
                    run["status"] = "running"
            run["updated_at"] = _now()
            self._write_state_unlocked(state)
            return self._public_run(run)

    def _gateway_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        proposal = self.governance.get_proposal(
            str(item["proposal_id"])
        ) or dict(item)
        question_summaries = self._question_summaries(
            item.get("question_refs", [])
        )
        curriculum_volume_ids = self._curriculum_volume_ids(question_summaries)
        return {
            "proposal_id": item["proposal_id"],
            "dimension": item["dimension"],
            "proposed_name": item["proposed_name"],
            "reason": str(proposal.get("reason") or "")[:300],
            "nearest_id": str(proposal.get("nearest_id") or ""),
            "candidates": self._candidate_terms(
                dimension=str(item["dimension"]),
                curriculum_volume_ids=curriculum_volume_ids,
            ),
            "curriculum_volume_ids": curriculum_volume_ids,
            "question_summaries": question_summaries,
            "taxonomy_revision": int(item.get("taxonomy_revision") or 0),
            "evidence_revision": int(item.get("evidence_revision") or 0),
            "graph_release_id": str(item.get("graph_release_id") or ""),
        }

    def _candidate_terms(
        self,
        *,
        dimension: str,
        curriculum_volume_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        if dimension == "knowledge" and curriculum_volume_ids:
            seen: set[str] = set()
            scoped: list[dict[str, Any]] = []
            for volume_id in curriculum_volume_ids:
                for node in eligible_curriculum_knowledge_nodes(volume_id):
                    node_id = str(node["id"])
                    if node_id in seen:
                        continue
                    seen.add(node_id)
                    scoped.append(
                        {
                            "id": node_id,
                            "name": str(node["name"]),
                            "label": str(node.get("label") or ""),
                            "level": int(node.get("level") or 0),
                            "parent_id": str(node.get("parent_id") or ""),
                            "volume_id": str(node.get("volume_id") or ""),
                        }
                    )
            if scoped:
                return scoped
        terms = self.governance.catalog()["dimensions"].get(dimension, [])
        return [
            {
                "id": str(term["id"]),
                "name": str(term["name"]),
            }
            for term in terms
        ]

    @staticmethod
    def _curriculum_volume_ids(
        question_summaries: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        volume_ids: list[str] = []
        for summary in question_summaries:
            volume = curriculum_volume(
                grade=summary.get("grade"),
                semester=summary.get("semester"),
                textbook_version=summary.get("textbook_version"),
            )
            if volume is not None and str(volume["id"]) not in volume_ids:
                volume_ids.append(str(volume["id"]))
        return volume_ids

    def _question_summaries(
        self,
        question_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        ids = _positive_ids(question_ids)
        if not ids or self.question_loader is None:
            return []
        try:
            loaded = self.question_loader(ids)
        except Exception:
            return []
        by_id: dict[int, Mapping[str, Any]] = {}
        for raw in loaded:
            if not isinstance(raw, Mapping):
                continue
            try:
                question_id = int(raw.get("id"))
            except (TypeError, ValueError):
                continue
            if question_id in ids:
                by_id[question_id] = raw
        summaries: list[dict[str, Any]] = []
        for question_id in ids:
            raw = by_id.get(question_id)
            if raw is None:
                continue
            summary = {
                "id": question_id,
                "question_number": _trim_text(
                    raw.get("question_number"), 40
                ),
                "question_type": _trim_text(
                    raw.get("question_type"), 40
                ),
                "question_text": _trim_text(
                    raw.get("question_text"), 600
                ),
                "answer_text": _trim_text(
                    raw.get("answer_text"), 400
                ),
            }
            for key, limit in (
                ("grade", 40),
                ("semester", 40),
                ("textbook_version", 80),
            ):
                value = _trim_text(raw.get(key), limit)
                if value:
                    summary[key] = value
            summaries.append(summary)
        return summaries

    def _validated_gateway_response(
        self,
        batch: Sequence[Mapping[str, Any]],
        response: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(response, Sequence) or isinstance(
            response, (str, bytes)
        ):
            raise TaxonomySuggestionModelResponseError(
                "AI suggestion response must be a list"
            )
        payload_by_id = {
            str(item["proposal_id"]): self._gateway_item(item)
            for item in batch
        }
        suggestions: dict[str, dict[str, Any]] = {}
        for raw in response:
            if not isinstance(raw, Mapping):
                continue
            proposal_id = str(raw.get("proposal_id") or "").strip()
            payload = payload_by_id.get(proposal_id)
            if payload is None or proposal_id in suggestions:
                continue
            legacy_format = not bool(str(raw.get("relation_kind") or "").strip())
            relation_kind = str(
                raw.get("relation_kind")
                or ({
                    "merge": "exact",
                    "map_many": "related",
                    "approve": "new_core_candidate",
                }.get(
                    str(raw.get("decision") or "").strip(),
                    raw.get("decision"),
                ))
                or ""
            ).strip()
            if relation_kind not in _ALLOWED_RELATION_KINDS:
                continue
            allowed_targets = {
                candidate["id"] for candidate in payload["candidates"]
            }
            target_ids = _unique_strings(
                raw.get("target_term_ids", [])
                if isinstance(raw.get("target_term_ids"), Sequence)
                and not isinstance(raw.get("target_term_ids"), (str, bytes))
                else []
            )
            invalid_target_ids = [
                target_id
                for target_id in target_ids
                if target_id not in allowed_targets
            ]
            target_ids = [
                target_id
                for target_id in target_ids
                if target_id in allowed_targets
            ]
            if relation_kind == "exact" and len(target_ids) != 1:
                relation_kind = "uncertain"
                target_ids = []
            if relation_kind in {
                "new_core_candidate", "reject", "uncertain", "wrong_dimension"
            }:
                target_ids = []
            try:
                confidence = float(raw.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            reason_source = raw.get("reason")
            if invalid_target_ids:
                reason_source = "模型返回了本试卷册别范围外的目标，已忽略，请教师核对。"
            reason = _short_chinese_reason(reason_source, relation_kind)
            suggestions[proposal_id] = {
                "relation_kind": relation_kind,
                "target_term_ids": target_ids,
                "reason": reason,
                "confidence": max(0.0, min(1.0, confidence)),
                "source": "ai",
                "legacy_format": legacy_format,
                "evidence_question_ids": [
                    int(item["id"])
                    for item in payload["question_summaries"]
                ],
                "taxonomy_revision": int(payload["taxonomy_revision"]),
                "graph_release_id": str(payload["graph_release_id"]),
            }
        return suggestions

    def _revision_is_current(self, run: Mapping[str, Any]) -> bool:
        current = self.governance.observation_snapshot()
        return (
            int(current["taxonomy_revision"]) == int(run["taxonomy_revision"])
            and int(current["evidence_revision"])
            == int(run.get("evidence_revision") or 0)
            and str(current.get("graph_release_id") or "")
            == str(run.get("graph_release_id") or "")
        )

    def _emit_progress(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        run_id: str,
    ) -> None:
        if callback is None:
            return
        _safe_progress_callback(callback, self.get_run(run_id))

    @staticmethod
    def _mark_stale(run: dict[str, Any]) -> None:
        run["stale"] = True
        run["status"] = "stale"
        for item in run["items"]:
            if item["status"] in {"pending", "running"}:
                item["status"] = "stale"
        run["updated_at"] = _now()

    @staticmethod
    def _public_run(run: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(run))
        result.pop("cancellation_requested", None)
        statuses = [
            str(item.get("status") or "")
            for item in result.get("items", [])
            if isinstance(item, Mapping)
        ]
        result["progress"] = {
            "total": len(statuses),
            "processed": sum(
                status in {"suggested", "failed", "cancelled", "stale"}
                for status in statuses
            ),
            "completed": sum(status == "suggested" for status in statuses),
            "failed": sum(status == "failed" for status in statuses),
            "pending": sum(
                status in {"pending", "running"} for status in statuses
            ),
            "cancelled": sum(status == "cancelled" for status in statuses),
        }
        result["retryable"] = (
            not bool(result.get("stale"))
            and any(
                status in {"failed", "cancelled"} for status in statuses
            )
        )
        return result

    @staticmethod
    def _require_run(
        state: Mapping[str, Any],
        run_id: object,
    ) -> dict[str, Any]:
        normalized = str(run_id or "").strip()
        run = state["runs"].get(normalized)
        if not isinstance(run, dict):
            raise TaxonomySuggestionNotFound(
                "Taxonomy suggestion run does not exist"
            )
        return run

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "schema_version": 1,
                "runs": {},
                "requests": {},
            }
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("runs"), Mapping)
            or not isinstance(payload.get("requests"), Mapping)
        ):
            raise RuntimeError("Taxonomy suggestion state is invalid")
        return {
            "schema_version": 1,
            "runs": copy.deepcopy(dict(payload["runs"])),
            "requests": copy.deepcopy(dict(payload["requests"])),
        }

    def _write_state_unlocked(self, state: Mapping[str, Any]) -> None:
        _write_json_atomic(self.state_path, state)


def _positive_ids(values: Sequence[object]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in result:
            result.append(item)
    return result


def _unique_strings(values: Sequence[object] | Any) -> list[str]:
    if isinstance(values, (str, bytes)):
        values = [values]
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _trim_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _short_chinese_reason(value: object, relation_kind: str) -> str:
    defaults = {
        "exact": "与现有词严格同义，可核对目标词后归并。",
        "broader": "候选范围比现有词更宽，需要教师确认是否保留。",
        "narrower": "候选是现有词下的细分概念，需要教师确认是否单列。",
        "related": "只找到相关词，不能安全自动归并。",
        "new_core_candidate": "未找到可复用的现有词，建议核对是否新增。",
        "wrong_dimension": "候选可能放错标签类别，需要教师调整。",
        "reject": "候选不适合作为规范标签，建议拒绝。",
        "uncertain": "现有证据不足，无法给出可靠归并结论。",
    }
    reason = " ".join(str(value or "").split())
    if not re.search(r"[\u3400-\u9fff]", reason):
        return defaults.get(relation_kind, defaults["uncertain"])
    first_sentence = re.split(r"(?<=[。！？!?；;])", reason, maxsplit=1)[0].strip()
    if len(first_sentence) <= 48:
        return first_sentence
    return f"{first_sentence[:47]}…"


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError(
            "request_token must contain 32 hexadecimal characters"
        )
    return token


def _fingerprint(payload: Mapping[str, Any]) -> str:
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
    except Exception:
        return False


def _safe_progress_callback(
    callback: Callable[[dict[str, Any]], None] | None,
    snapshot: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(copy.deepcopy(snapshot))
    except Exception:
        return


def _replace_with_retry(source: Path, destination: Path) -> None:
    # Windows file scanners can hold a freshly written temporary file open
    # briefly; retry the atomic rename a few times before giving up.
    for attempt in range(12):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.4))


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
        _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
