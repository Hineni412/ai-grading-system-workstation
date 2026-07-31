from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from question_bank.database.schema import connect
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
    find_confirmation_conflicts,
    normalize_stable_key,
)
from question_bank.relations.repository import (
    KnowledgeRelationDuplicate,
    KnowledgeRelationRepository,
)


class RelationSuggestionGateway(Protocol):
    model_name: str
    model_version: str

    def suggest_relations(
        self,
        batch: Sequence[Mapping[str, object]],
        *,
        operation_id: str,
        prompt_version: str,
    ) -> Mapping[str, object]:
        """Make exactly one physical request and return structured output."""


class RelationSuggestionError(RuntimeError):
    pass


class RelationSuggestionNotFound(RelationSuggestionError):
    pass


class RelationSuggestionOperationConflict(RelationSuggestionError):
    pass


class RelationSuggestionInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RelationPairCandidate:
    candidate_id: str
    source_key: str
    target_key: str
    allowed_types: tuple[RelationType, ...]
    evidence_summary: str = ""

    def __post_init__(self) -> None:
        candidate_id = _required_text(self.candidate_id, "candidate_id")
        source_key = normalize_stable_key(self.source_key)
        target_key = normalize_stable_key(self.target_key)
        if source_key == target_key:
            raise RelationSuggestionInvalid(
                "relation candidate cannot reference one identity twice"
            )
        allowed = tuple(dict.fromkeys(RelationType(value) for value in self.allowed_types))
        if not allowed:
            raise RelationSuggestionInvalid(
                "relation candidate requires at least one allowed type"
            )
        summary = " ".join(str(self.evidence_summary or "").split())[:600]
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "target_key", target_key)
        object.__setattr__(self, "allowed_types", allowed)
        object.__setattr__(self, "evidence_summary", summary)


@dataclass(frozen=True, slots=True)
class RelationEvaluation:
    precision: float
    coverage: float
    true_positive_count: int
    suggested_count: int
    positive_count: int
    error_counts: dict[str, int]
    passed: bool


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_RUNS: set[str] = set()
_TERMINAL_ITEM_STATUSES = frozenset(
    {"suggested", "not_suggested", "failed", "cancelled"}
)


class RelationSuggestionService:
    """Persist bounded model suggestions without ever confirming graph edges."""

    def __init__(
        self,
        db_path: Path,
        *,
        minimum_confidence: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db_path = Path(db_path)
        self.repository = KnowledgeRelationRepository(self.db_path)
        self.minimum_confidence = _confidence(minimum_confidence)
        self.clock = clock

    def create_run(
        self,
        candidates: Sequence[RelationPairCandidate],
        *,
        operation_id: str,
        prompt_version: str,
    ) -> dict[str, object]:
        clean_operation_id = _required_text(operation_id, "operation_id")
        clean_prompt_version = _required_text(
            prompt_version,
            "prompt_version",
        )
        normalized = tuple(
            candidate
            if isinstance(candidate, RelationPairCandidate)
            else RelationPairCandidate(**candidate)
            for candidate in candidates
        )
        if not normalized:
            raise RelationSuggestionInvalid(
                "at least one controlled relation pair is required"
            )
        candidate_ids = [candidate.candidate_id for candidate in normalized]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise RelationSuggestionInvalid("candidate_id values must be unique")

        active_identity_keys = {
            identity.stable_key
            for identity in self.repository.list_identities(status="active")
        }
        unknown_keys = sorted(
            {
                key
                for candidate in normalized
                for key in (candidate.source_key, candidate.target_key)
                if key not in active_identity_keys
            }
        )
        if unknown_keys:
            raise RelationSuggestionInvalid(
                "candidate scope contains an unknown or retired stable key"
            )

        fingerprint = _fingerprint(
            {
                "prompt_version": clean_prompt_version,
                "candidates": [
                    {
                        "candidate_id": item.candidate_id,
                        "source_key": item.source_key,
                        "target_key": item.target_key,
                        "allowed_types": [
                            relation_type.value
                            for relation_type in item.allowed_types
                        ],
                        "evidence_summary": item.evidence_summary,
                    }
                    for item in normalized
                ],
            }
        )
        with self._transaction() as connection:
            remembered = connection.execute(
                """
                SELECT run_id, operation_fingerprint
                FROM knowledge_relation_suggestion_runs
                WHERE operation_id = ?
                """,
                (clean_operation_id,),
            ).fetchone()
            if remembered is not None:
                if str(remembered["operation_fingerprint"]) != fingerprint:
                    raise RelationSuggestionOperationConflict(
                        "operation_id was reused with another candidate scope"
                    )
                return self._snapshot(connection, str(remembered["run_id"]))

            run_id = f"krr_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO knowledge_relation_suggestion_runs (
                    run_id,
                    operation_id,
                    operation_fingerprint,
                    prompt_version
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    clean_operation_id,
                    fingerprint,
                    clean_prompt_version,
                ),
            )
            connection.executemany(
                """
                INSERT INTO knowledge_relation_suggestion_items (
                    run_id,
                    candidate_id,
                    source_key,
                    target_key,
                    allowed_types_json,
                    evidence_summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        run_id,
                        item.candidate_id,
                        item.source_key,
                        item.target_key,
                        json.dumps(
                            [
                                relation_type.value
                                for relation_type in item.allowed_types
                            ],
                            separators=(",", ":"),
                        ),
                        item.evidence_summary,
                    )
                    for item in normalized
                ),
            )
            return self._snapshot(connection, run_id)

    def get_run(self, run_id: str) -> dict[str, object]:
        with connect(self.db_path) as connection:
            return self._snapshot(
                connection,
                _required_text(run_id, "run_id"),
            )

    def cancel_run(self, run_id: str) -> dict[str, object]:
        clean_run_id = _required_text(run_id, "run_id")
        with self._transaction() as connection:
            self._require_run(connection, clean_run_id)
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_runs
                SET cancellation_requested = 1,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (clean_run_id,),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_items
                SET status = 'cancelled',
                    outcome_code = 'cancelled',
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND status = 'pending'
                """,
                (clean_run_id,),
            )
            self._finalize(connection, clean_run_id)
            return self._snapshot(connection, clean_run_id)

    def recover_interrupted(self, run_id: str) -> dict[str, object]:
        clean_run_id = _required_text(run_id, "run_id")
        with self._transaction() as connection:
            self._require_run(connection, clean_run_id)
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_items
                SET status = 'failed',
                    outcome_code = 'interrupted',
                    error_category = 'interrupted',
                    error_message = '应用退出后需要教师显式重试。',
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND status = 'running'
                """,
                (clean_run_id,),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_requests
                SET status = 'failed',
                    error_category = 'interrupted',
                    finished_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND status = 'started'
                """,
                (clean_run_id,),
            )
            self._finalize(connection, clean_run_id)
            return self._snapshot(connection, clean_run_id)

    def retry_failed(
        self,
        run_id: str,
        gateway: RelationSuggestionGateway,
        *,
        batch_size: int = 8,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        clean_run_id = _required_text(run_id, "run_id")
        with self._transaction() as connection:
            self._require_run(connection, clean_run_id)
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_items
                SET status = 'pending',
                    outcome_code = NULL,
                    error_category = NULL,
                    error_message = NULL,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND status IN ('failed', 'cancelled')
                """,
                (clean_run_id,),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_runs
                SET status = 'queued',
                    cancellation_requested = 0,
                    last_error_category = NULL,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (clean_run_id,),
            )
        return self.process_run(
            clean_run_id,
            gateway,
            batch_size=batch_size,
            cancel_requested=cancel_requested,
        )

    def process_run(
        self,
        run_id: str,
        gateway: RelationSuggestionGateway,
        *,
        batch_size: int = 8,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        clean_run_id = _required_text(run_id, "run_id")
        size = int(batch_size)
        if size < 1 or size > 16:
            raise ValueError("batch_size must be between 1 and 16")
        model_name = _required_text(gateway.model_name, "model_name")
        model_version = _required_text(
            gateway.model_version,
            "model_version",
        )

        with _ACTIVE_LOCK:
            if clean_run_id in _ACTIVE_RUNS:
                return self.get_run(clean_run_id)
            _ACTIVE_RUNS.add(clean_run_id)
        try:
            while True:
                if _safe_cancel(cancel_requested):
                    return self.cancel_run(clean_run_id)
                claimed = self._claim_batch(
                    clean_run_id,
                    batch_size=size,
                    model_name=model_name,
                    model_version=model_version,
                )
                if claimed is None:
                    break
                request_id, prompt_version, batch = claimed
                started = self.clock()
                try:
                    raw_response = gateway.suggest_relations(
                        [self._gateway_item(item) for item in batch],
                        operation_id=request_id,
                        prompt_version=prompt_version,
                    )
                    parsed, usage = self._validate_response(batch, raw_response)
                    self._finish_batch_success(
                        clean_run_id,
                        request_id=request_id,
                        prompt_version=prompt_version,
                        model_name=model_name,
                        model_version=model_version,
                        batch=batch,
                        parsed=parsed,
                        usage=usage,
                        latency_ms=_elapsed_ms(started, self.clock()),
                    )
                except Exception:
                    self._finish_batch_failure(
                        clean_run_id,
                        request_id=request_id,
                        batch=batch,
                        category="model_response",
                        latency_ms=_elapsed_ms(started, self.clock()),
                    )
            with self._transaction() as connection:
                self._finalize(connection, clean_run_id)
                return self._snapshot(connection, clean_run_id)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_RUNS.discard(clean_run_id)

    def request_records(
        self,
        run_id: str,
    ) -> tuple[dict[str, object], ...]:
        clean_run_id = _required_text(run_id, "run_id")
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_relation_suggestion_requests
                WHERE run_id = ?
                ORDER BY batch_sequence, attempt
                """,
                (clean_run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _claim_batch(
        self,
        run_id: str,
        *,
        batch_size: int,
        model_name: str,
        model_version: str,
    ) -> tuple[str, str, tuple[dict[str, object], ...]] | None:
        with self._transaction() as connection:
            run = self._require_run(connection, run_id)
            if bool(run["cancellation_requested"]):
                self._finalize(connection, run_id)
                return None
            rows = connection.execute(
                """
                SELECT *
                FROM knowledge_relation_suggestion_items
                WHERE run_id = ?
                  AND status = 'pending'
                ORDER BY id
                LIMIT ?
                """,
                (run_id, batch_size),
            ).fetchall()
            if not rows:
                return None
            item_ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in item_ids)
            connection.execute(
                f"""
                UPDATE knowledge_relation_suggestion_items
                SET status = 'running',
                    attempts = attempts + 1,
                    updated_at = datetime('now','localtime')
                WHERE id IN ({placeholders})
                  AND status = 'pending'
                """,
                tuple(item_ids),
            )
            batch_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(batch_sequence), 0) + 1
                    FROM knowledge_relation_suggestion_requests
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()[0]
            )
            attempt = max(int(row["attempts"]) for row in rows) + 1
            request_id = f"krrq_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO knowledge_relation_suggestion_requests (
                    request_id,
                    run_id,
                    batch_sequence,
                    attempt,
                    status,
                    model_name,
                    model_version,
                    prompt_version,
                    candidate_count
                ) VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?)
                """,
                (
                    request_id,
                    run_id,
                    batch_sequence,
                    attempt,
                    model_name,
                    model_version,
                    str(run["prompt_version"]),
                    len(rows),
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_runs
                SET status = 'running',
                    request_count = request_count + 1,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (run_id,),
            )
            return (
                request_id,
                str(run["prompt_version"]),
                tuple(dict(row) for row in rows),
            )

    @staticmethod
    def _gateway_item(item: Mapping[str, object]) -> dict[str, object]:
        return {
            "candidate_id": str(item["candidate_id"]),
            "source_key": str(item["source_key"]),
            "target_key": str(item["target_key"]),
            "allowed_relation_types": json.loads(
                str(item["allowed_types_json"])
            ),
            "aggregate_evidence": str(item["evidence_summary"]),
        }

    def _validate_response(
        self,
        batch: Sequence[Mapping[str, object]],
        response: object,
    ) -> tuple[dict[str, dict[str, object]], dict[str, int]]:
        if not isinstance(response, Mapping):
            raise RelationSuggestionInvalid(
                "relation suggestion response must be an object"
            )
        raw_items = response.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(
            raw_items,
            (str, bytes),
        ):
            raise RelationSuggestionInvalid(
                "relation suggestion items must be a list"
            )
        expected = {str(item["candidate_id"]): item for item in batch}
        parsed: dict[str, dict[str, object]] = {}
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise RelationSuggestionInvalid(
                    "relation suggestion item must be an object"
                )
            candidate_id = _required_text(
                raw.get("candidate_id"),
                "candidate_id",
            )
            if candidate_id not in expected or candidate_id in parsed:
                raise RelationSuggestionInvalid(
                    "relation suggestion candidate_id is unknown or repeated"
                )
            decision = _required_text(raw.get("decision"), "decision")
            if decision not in {"suggest", "none"}:
                raise RelationSuggestionInvalid(
                    "relation suggestion decision is invalid"
                )
            reason = _required_text(raw.get("reason"), "reason")[:500]
            if decision == "none":
                parsed[candidate_id] = {
                    "decision": "none",
                    "reason": reason,
                    "confidence": _confidence(raw.get("confidence", 0.0)),
                }
                continue
            relation_type = RelationType(raw.get("relation_type"))
            allowed = {
                RelationType(value)
                for value in json.loads(
                    str(expected[candidate_id]["allowed_types_json"])
                )
            }
            if relation_type not in allowed:
                raise RelationSuggestionInvalid(
                    "relation type is outside the controlled candidate scope"
                )
            proposed = KnowledgeRelation(
                source_key=_required_text(raw.get("source_key"), "source_key"),
                target_key=_required_text(raw.get("target_key"), "target_key"),
                relation_type=relation_type,
            )
            scoped = KnowledgeRelation(
                source_key=str(expected[candidate_id]["source_key"]),
                target_key=str(expected[candidate_id]["target_key"]),
                relation_type=relation_type,
            )
            if (
                proposed.source_key != scoped.source_key
                or proposed.target_key != scoped.target_key
            ):
                raise RelationSuggestionInvalid(
                    "relation output contains an unknown stable key"
                )
            parsed[candidate_id] = {
                "decision": "suggest",
                "relation": proposed,
                "reason": reason,
                "confidence": _confidence(raw.get("confidence")),
            }
        if set(parsed) != set(expected):
            raise RelationSuggestionInvalid(
                "relation suggestion response omitted a candidate"
            )
        usage_raw = response.get("usage")
        usage = {
            "input_tokens": _nonnegative_int(
                usage_raw.get("input_tokens", 0)
                if isinstance(usage_raw, Mapping)
                else 0
            ),
            "output_tokens": _nonnegative_int(
                usage_raw.get("output_tokens", 0)
                if isinstance(usage_raw, Mapping)
                else 0
            ),
        }
        return parsed, usage

    def _finish_batch_success(
        self,
        run_id: str,
        *,
        request_id: str,
        prompt_version: str,
        model_name: str,
        model_version: str,
        batch: Sequence[Mapping[str, object]],
        parsed: Mapping[str, Mapping[str, object]],
        usage: Mapping[str, int],
        latency_ms: int,
    ) -> None:
        active_contracts = tuple(
            KnowledgeRelation(
                source_key=item.source_key,
                target_key=item.target_key,
                relation_type=item.relation_type,
                status=RelationStatus.CONFIRMED,
            )
            for item in self.repository.list_active_relations()
        )
        outcomes: dict[str, dict[str, object]] = {}
        for item in batch:
            candidate_id = str(item["candidate_id"])
            prediction = parsed[candidate_id]
            if prediction["decision"] == "none":
                outcomes[candidate_id] = {
                    "status": "not_suggested",
                    "outcome_code": "model_none",
                }
                continue
            confidence = float(prediction["confidence"])
            if confidence < self.minimum_confidence:
                outcomes[candidate_id] = {
                    "status": "not_suggested",
                    "outcome_code": "low_confidence",
                }
                continue
            relation = prediction["relation"]
            assert isinstance(relation, KnowledgeRelation)
            conflicts = find_confirmation_conflicts(
                relation,
                active_contracts,
            )
            try:
                saved = self.repository.create_suggestion(
                    relation,
                    source_kind="model",
                    rationale=str(prediction["reason"]),
                    source_operation_id=request_id,
                    model_name=model_name,
                    model_version=model_version,
                    prompt_version=prompt_version,
                    confidence=confidence,
                    conflict_codes=tuple(
                        conflict.value for conflict in conflicts
                    ),
                )
            except KnowledgeRelationDuplicate as exc:
                outcomes[candidate_id] = {
                    "status": "not_suggested",
                    "outcome_code": "duplicate",
                    "relation_id": exc.relation_id,
                }
            else:
                outcomes[candidate_id] = {
                    "status": "suggested",
                    "outcome_code": (
                        "conflict_flagged" if conflicts else "accepted"
                    ),
                    "relation_id": saved.relation_id,
                }

        with self._transaction() as connection:
            for candidate_id, outcome in outcomes.items():
                connection.execute(
                    """
                    UPDATE knowledge_relation_suggestion_items
                    SET status = ?,
                        outcome_code = ?,
                        relation_id = ?,
                        error_category = NULL,
                        error_message = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE run_id = ?
                      AND candidate_id = ?
                      AND status = 'running'
                    """,
                    (
                        outcome["status"],
                        outcome["outcome_code"],
                        outcome.get("relation_id"),
                        run_id,
                        candidate_id,
                    ),
                )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_requests
                SET status = 'succeeded',
                    input_tokens = ?,
                    output_tokens = ?,
                    latency_ms = ?,
                    finished_at = datetime('now','localtime')
                WHERE request_id = ?
                  AND status = 'started'
                """,
                (
                    int(usage["input_tokens"]),
                    int(usage["output_tokens"]),
                    latency_ms,
                    request_id,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_runs
                SET input_tokens = input_tokens + ?,
                    output_tokens = output_tokens + ?,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (
                    int(usage["input_tokens"]),
                    int(usage["output_tokens"]),
                    run_id,
                ),
            )

    def _finish_batch_failure(
        self,
        run_id: str,
        *,
        request_id: str,
        batch: Sequence[Mapping[str, object]],
        category: str,
        latency_ms: int,
    ) -> None:
        candidate_ids = [str(item["candidate_id"]) for item in batch]
        placeholders = ",".join("?" for _ in candidate_ids)
        with self._transaction() as connection:
            connection.execute(
                f"""
                UPDATE knowledge_relation_suggestion_items
                SET status = 'failed',
                    outcome_code = ?,
                    error_category = ?,
                    error_message = '模型建议未形成可用结构，可由教师显式重试。',
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND candidate_id IN ({placeholders})
                  AND status = 'running'
                """,
                (
                    category,
                    category,
                    run_id,
                    *candidate_ids,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_requests
                SET status = 'failed',
                    latency_ms = ?,
                    error_category = ?,
                    finished_at = datetime('now','localtime')
                WHERE request_id = ?
                  AND status = 'started'
                """,
                (latency_ms, category, request_id),
            )
            connection.execute(
                """
                UPDATE knowledge_relation_suggestion_runs
                SET last_error_category = ?,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (category, run_id),
            )

    @staticmethod
    def _require_run(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM knowledge_relation_suggestion_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise RelationSuggestionNotFound(run_id)
        return row

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> dict[str, object]:
        run = self._require_run(connection, run_id)
        rows = connection.execute(
            """
            SELECT
                candidate_id,
                source_key,
                target_key,
                allowed_types_json,
                status,
                attempts,
                outcome_code,
                relation_id,
                error_category,
                error_message
            FROM knowledge_relation_suggestion_items
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        items = [
            {
                "candidate_id": str(row["candidate_id"]),
                "source_key": str(row["source_key"]),
                "target_key": str(row["target_key"]),
                "allowed_relation_types": json.loads(
                    str(row["allowed_types_json"])
                ),
                "status": str(row["status"]),
                "attempts": int(row["attempts"]),
                "outcome_code": row["outcome_code"],
                "relation_id": row["relation_id"],
                "error": (
                    None
                    if row["error_category"] is None
                    else {
                        "category": str(row["error_category"]),
                        "message": str(row["error_message"] or ""),
                    }
                ),
            }
            for row in rows
        ]
        statuses = [str(item["status"]) for item in items]
        return {
            "run_id": str(run["run_id"]),
            "operation_id": str(run["operation_id"]),
            "status": str(run["status"]),
            "prompt_version": str(run["prompt_version"]),
            "request_count": int(run["request_count"]),
            "usage": {
                "input_tokens": int(run["input_tokens"]),
                "output_tokens": int(run["output_tokens"]),
            },
            "last_error_category": run["last_error_category"],
            "progress": {
                "total": len(items),
                "completed": sum(
                    status in {"suggested", "not_suggested"}
                    for status in statuses
                ),
                "failed": sum(status == "failed" for status in statuses),
                "cancelled": sum(
                    status == "cancelled" for status in statuses
                ),
                "pending": sum(
                    status in {"pending", "running"} for status in statuses
                ),
            },
            "retryable": any(
                status in {"failed", "cancelled"} for status in statuses
            ),
            "items": items,
        }

    def _finalize(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> None:
        run = self._require_run(connection, run_id)
        statuses = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT status
                FROM knowledge_relation_suggestion_items
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            )
        ]
        if any(status in {"pending", "running"} for status in statuses):
            status = "running"
        elif any(status == "cancelled" for status in statuses):
            status = "cancelled"
        elif statuses and all(item == "failed" for item in statuses):
            status = "failed"
        elif any(item == "failed" for item in statuses):
            status = "partial"
        else:
            status = "completed"
        connection.execute(
            """
            UPDATE knowledge_relation_suggestion_runs
            SET status = ?,
                updated_at = datetime('now','localtime')
            WHERE run_id = ?
            """,
            (status, run_id),
        )

    def _transaction(self):
        return _transaction(self.db_path)


def evaluate_relation_predictions(
    gold_samples: Sequence[Mapping[str, object]],
    predictions: Mapping[str, Mapping[str, object]],
    *,
    minimum_precision: float = 0.9,
    minimum_coverage: float = 0.7,
) -> RelationEvaluation:
    positive = {
        str(sample["id"]): sample
        for sample in gold_samples
        if str(sample.get("kind")) == "positive"
        and str(sample.get("expected")) == "allowed"
    }
    true_positives = 0
    suggested_count = 0
    errors: dict[str, int] = {}
    for sample in gold_samples:
        sample_id = str(sample["id"])
        prediction = predictions.get(sample_id, {})
        decision = str(prediction.get("decision") or "none")
        if decision != "suggest":
            if sample_id in positive:
                _increment(errors, "false_negative")
            continue
        suggested_count += 1
        if sample_id not in positive:
            _increment(errors, "false_positive")
            continue
        if str(prediction.get("relation_type") or "") != str(
            sample.get("relation_type") or ""
        ):
            _increment(errors, "wrong_type")
            continue
        true_positives += 1
    precision = (
        true_positives / suggested_count if suggested_count else 0.0
    )
    coverage = true_positives / len(positive) if positive else 1.0
    return RelationEvaluation(
        precision=precision,
        coverage=coverage,
        true_positive_count=true_positives,
        suggested_count=suggested_count,
        positive_count=len(positive),
        error_counts=errors,
        passed=(
            precision >= float(minimum_precision)
            and coverage >= float(minimum_coverage)
        ),
    )


class _transaction:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._context = None
        self._connection = None

    def __enter__(self) -> sqlite3.Connection:
        self._context = connect(self.db_path)
        self._connection = self._context.__enter__()
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self._context is not None
        return bool(self._context.__exit__(exc_type, exc, traceback))


def _fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RelationSuggestionInvalid(f"{field_name} must be nonblank")
    return text


def _confidence(value: object) -> float:
    if isinstance(value, bool):
        raise RelationSuggestionInvalid(
            "confidence must be between zero and one"
        )
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise RelationSuggestionInvalid(
            "confidence must be between zero and one"
        ) from exc
    if not 0.0 <= normalized <= 1.0:
        raise RelationSuggestionInvalid(
            "confidence must be between zero and one"
        )
    return normalized


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, normalized)


def _safe_cancel(callback: Callable[[], bool] | None) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def _elapsed_ms(started: float, finished: float) -> int:
    return max(0, int(round((finished - started) * 1000)))


def _increment(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1


__all__ = [
    "RelationEvaluation",
    "RelationPairCandidate",
    "RelationSuggestionError",
    "RelationSuggestionGateway",
    "RelationSuggestionInvalid",
    "RelationSuggestionNotFound",
    "RelationSuggestionOperationConflict",
    "RelationSuggestionService",
    "evaluate_relation_predictions",
]
