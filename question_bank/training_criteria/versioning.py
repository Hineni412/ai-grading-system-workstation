from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from question_bank.database.schema import connect
from question_bank.training_criteria.analysis import (
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
)


CriterionVersionStatus = Literal[
    "proposed",
    "approved",
    "rejected",
    "superseded",
    "stale",
]
CriterionSourceKind = Literal[
    "combined_model",
    "confirmed_rubric_adapter",
    "teacher_manual",
    "backfill",
]
CriterionReviewAction = Literal["approve", "reject"]
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
ADVISORY_QUALITY_CODES = frozenset({"missing_actual_image"})
class CriterionVersionNotFound(LookupError):
    pass


class CriterionRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("criterion head changed")
        self.current_revision = int(current_revision)


class CriterionRequestConflict(RuntimeError):
    pass


class CriterionTransitionError(RuntimeError):
    pass


class CriterionQualityError(ValueError):
    def __init__(self, quality_codes: Sequence[str]) -> None:
        super().__init__("criterion quality gate did not pass")
        self.quality_codes = tuple(quality_codes)


class ApprovedCriterionMissing(LookupError):
    def __init__(self, question_ids: Sequence[int]) -> None:
        super().__init__("approved criterion version is missing")
        self.question_ids = tuple(int(value) for value in question_ids)


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    passed: bool
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CriterionReviewCommand:
    question_id: int
    version_id: str
    expected_revision: int
    action: CriterionReviewAction
    actor_ref: str
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.question_id, bool) or int(self.question_id) <= 0:
            raise ValueError("question_id must be positive")
        version_id = str(self.version_id or "").strip().casefold()
        if len(version_id) != 64:
            raise ValueError("version_id is invalid")
        if isinstance(self.expected_revision, bool) or int(
            self.expected_revision
        ) < 1:
            raise ValueError("expected_revision must be positive")
        action = str(self.action or "").strip().casefold()
        if action not in {"approve", "reject"}:
            raise ValueError("criterion review action is invalid")
        actor = _required_text(self.actor_ref, "actor_ref")
        reason = _required_text(self.reason, "reason")
        object.__setattr__(self, "question_id", int(self.question_id))
        object.__setattr__(self, "version_id", version_id)
        object.__setattr__(self, "expected_revision", int(self.expected_revision))
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "actor_ref", actor)
        object.__setattr__(self, "reason", reason)


def blocking_quality_codes(codes: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        code
        for code in dict.fromkeys(codes)
        if code not in ADVISORY_QUALITY_CODES
    )


def evaluate_criterion_quality(
    question: QuestionAnalysisInput,
    draft: TrainingCriteriaDraft,
) -> QualityGateResult:
    codes: list[str] = []
    if draft.question_id != question.question_id:
        codes.append("question_mismatch")
    if draft.source_content_hash != question.criterion_source_content_hash:
        codes.append("source_stale")
    if not draft.points:
        codes.append("no_points")
    point_ids = [point.point_id for point in draft.points]
    if len(point_ids) != len(set(point_ids)):
        codes.append("duplicate_point_id")
    signatures = [
        _compact(f"{point.target}|{point.observable_evidence}")
        for point in draft.points
    ]
    if len(signatures) != len(set(signatures)):
        codes.append("duplicate_obligation")
    if any(
        not point.target.strip() or not point.observable_evidence.strip()
        for point in draft.points
    ):
        codes.append("unobservable_point")
    known_ids = {point.point_id for point in draft.points}
    if any(
        item not in known_ids
        for point in draft.points
        for item in point.depends_on
    ):
        codes.append("unknown_dependency")
    if question.tagging_context.has_images and not question.has_required_images:
        codes.append("missing_actual_image")

    group = question.question_type_group
    response_shape = question.objective_response_shape
    if group in {"single_choice", "fill_blank"} and not str(
        question.tagging_context.answer_text or ""
    ).strip():
        codes.append("objective_answer_missing")
    if response_shape in {"single_choice", "single_blank"} and len(
        draft.points
    ) != 1:
        codes.append("objective_point_count")
    if response_shape == "multiple_blank":
        blank_count = _blank_count(question.tagging_context.question_text)
        if blank_count > 1 and len(draft.points) < blank_count:
            codes.append("multiple_blanks_collapsed")
    teacher_visible_values = [
        draft.rationale,
        *draft.auxiliary_rules,
        *(
            value
            for point in draft.points
            for value in (
                point.target,
                point.observable_evidence,
                *point.equivalent_rules,
                *point.counterexamples,
            )
        ),
    ]
    if any(_contains_teacher_visible_english(value) for value in teacher_visible_values):
        codes.append("teacher_visible_language_not_zh")
    unique_codes = tuple(dict.fromkeys(codes))
    return QualityGateResult(
        passed=not blocking_quality_codes(unique_codes),
        codes=unique_codes,
    )


class TrainingCriterionModule:
    """Deep module for immutable criterion versions and teacher decisions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def read(self, question: QuestionAnalysisInput) -> dict[str, Any]:
        self._sync_source(question)
        return self._workspace(question.question_id)

    def propose(
        self,
        *,
        question: QuestionAnalysisInput,
        draft: TrainingCriteriaDraft | Mapping[str, Any],
        source_kind: CriterionSourceKind,
        source_reference: str,
        actor_ref: str,
        reason: str,
        expected_revision: int | None = None,
        parent_version_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_draft(question, draft)
        source = _source_kind(source_kind)
        reference = _required_text(
            source_reference,
            "source_reference",
        )
        actor = _required_text(actor_ref, "actor_ref")
        clean_reason = _required_text(reason, "reason")
        quality = evaluate_criterion_quality(question, normalized)
        criteria_json = _canonical_json(normalized.to_dict())
        criteria_hash = _sha256(criteria_json)
        self._sync_source(question)

        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT version_id, source_content_hash, criteria_hash
                FROM training_criterion_versions
                WHERE question_id = ?
                  AND source_kind = ?
                  AND source_reference = ?
                """,
                (question.question_id, source, reference),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["source_content_hash"])
                    != normalized.source_content_hash
                    or str(existing["criteria_hash"]) != criteria_hash
                ):
                    raise CriterionRequestConflict(
                        "criterion source reference was reused"
                    )
                version_id = str(existing["version_id"])
                return self._workspace_after_commit(
                    connection,
                    question.question_id,
                    version_id,
                )

            head = _head(connection, question.question_id)
            current_revision = int(head["revision"]) if head else 0
            if (
                expected_revision is not None
                and int(expected_revision) != current_revision
            ):
                raise CriterionRevisionConflict(current_revision)
            current_id = (
                str(head["current_version_id"]) if head is not None else None
            )
            parent_id = (
                str(parent_version_id or "").strip().casefold()
                or current_id
            )
            if parent_id is not None:
                parent = connection.execute(
                    """
                    SELECT question_id
                    FROM training_criterion_versions
                    WHERE version_id = ?
                    """,
                    (parent_id,),
                ).fetchone()
                if (
                    parent is None
                    or int(parent["question_id"]) != question.question_id
                ):
                    raise CriterionVersionNotFound(parent_id)

            version_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM training_criterion_versions
                    WHERE question_id = ?
                    """,
                    (question.question_id,),
                ).fetchone()[0]
            )
            version_id = _hash_payload(
                {
                    "question_id": question.question_id,
                    "version_number": version_number,
                    "parent_version_id": parent_id,
                    "source_content_hash": normalized.source_content_hash,
                    "criteria_hash": criteria_hash,
                    "source_kind": source,
                    "source_reference": reference,
                }
            )
            next_revision = current_revision + 1
            if current_id is not None:
                current = connection.execute(
                    """
                    SELECT status
                    FROM training_criterion_versions
                    WHERE version_id = ?
                    """,
                    (current_id,),
                ).fetchone()
                if current is not None and str(current["status"]) == "proposed":
                    connection.execute(
                        """
                        UPDATE training_criterion_versions
                        SET status = 'superseded',
                            updated_at = datetime('now','localtime')
                        WHERE version_id = ? AND status = 'proposed'
                        """,
                        (current_id,),
                    )
                    _event(
                        connection,
                        question_id=question.question_id,
                        version_id=current_id,
                        event_type="superseded",
                        actor_ref=actor,
                        reason="replaced by a newer draft",
                        from_status="proposed",
                        to_status="superseded",
                        revision=next_revision,
                    )
            connection.execute(
                """
                INSERT INTO training_criterion_versions (
                    version_id,
                    question_id,
                    version_number,
                    parent_version_id,
                    source_content_hash,
                    schema_version,
                    status,
                    source_kind,
                    source_reference,
                    criteria_json,
                    criteria_hash,
                    quality_status,
                    quality_codes_json,
                    created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    question.question_id,
                    version_number,
                    parent_id,
                    normalized.source_content_hash,
                    normalized.schema_version,
                    source,
                    reference,
                    criteria_json,
                    criteria_hash,
                    "passed" if quality.passed else "failed",
                    _canonical_json(list(quality.codes)),
                    actor,
                ),
            )
            if head is None:
                connection.execute(
                    """
                    INSERT INTO training_criterion_heads (
                        question_id,
                        current_version_id,
                        approved_version_id,
                        current_source_hash,
                        revision
                    ) VALUES (?, ?, NULL, ?, 1)
                    """,
                    (
                        question.question_id,
                        version_id,
                        normalized.source_content_hash,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE training_criterion_heads
                    SET current_version_id = ?,
                        current_source_hash = ?,
                        revision = ?,
                        updated_at = datetime('now','localtime')
                    WHERE question_id = ?
                    """,
                    (
                        version_id,
                        normalized.source_content_hash,
                        next_revision,
                        question.question_id,
                    ),
                )
            _event(
                connection,
                question_id=question.question_id,
                version_id=version_id,
                event_type=(
                    "edited" if source == "teacher_manual" else "proposed"
                ),
                actor_ref=actor,
                reason=clean_reason,
                from_status=None,
                to_status="proposed",
                revision=next_revision,
            )
        return self._workspace(question.question_id)

    def edit(
        self,
        *,
        question: QuestionAnalysisInput,
        criteria: Mapping[str, Any],
        expected_revision: int,
        parent_version_id: str | None,
        request_token: str,
        actor_ref: str,
        reason: str,
    ) -> dict[str, Any]:
        token = _request_token(request_token)
        return self.propose(
            question=question,
            draft=criteria,
            source_kind="teacher_manual",
            source_reference=f"manual:{token}",
            actor_ref=actor_ref,
            reason=reason,
            expected_revision=expected_revision,
            parent_version_id=parent_version_id,
        )

    def review(
        self,
        command: CriterionReviewCommand,
        *,
        question: QuestionAnalysisInput,
    ) -> dict[str, Any]:
        if question.question_id != command.question_id:
            raise ValueError("criterion review belongs to another question")
        self._sync_source(question)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            head = _head(connection, command.question_id)
            if head is None:
                raise CriterionVersionNotFound(command.version_id)
            current_revision = int(head["revision"])
            if current_revision != command.expected_revision:
                raise CriterionRevisionConflict(current_revision)
            if str(head["current_version_id"]) != command.version_id:
                raise CriterionRevisionConflict(current_revision)
            version = connection.execute(
                """
                SELECT *
                FROM training_criterion_versions
                WHERE version_id = ? AND question_id = ?
                """,
                (command.version_id, command.question_id),
            ).fetchone()
            if version is None:
                raise CriterionVersionNotFound(command.version_id)
            if str(version["status"]) != "proposed":
                raise CriterionTransitionError(
                    "only the current proposed version can be reviewed"
                )
            next_revision = current_revision + 1
            if command.action == "approve":
                quality_codes = _json_strings(
                    version["quality_codes_json"]
                )
                blocked = blocking_quality_codes(quality_codes)
                if blocked:
                    raise CriterionQualityError(quality_codes)
                if str(version["source_content_hash"]) != str(
                    head["current_source_hash"]
                ):
                    raise CriterionTransitionError(
                        "stale criterion version cannot be approved"
                    )
                old_approved = head["approved_version_id"]
                if old_approved and str(old_approved) != command.version_id:
                    connection.execute(
                        """
                        UPDATE training_criterion_versions
                        SET status = 'superseded',
                            updated_at = datetime('now','localtime')
                        WHERE version_id = ? AND status = 'approved'
                        """,
                        (str(old_approved),),
                    )
                    _event(
                        connection,
                        question_id=command.question_id,
                        version_id=str(old_approved),
                        event_type="superseded",
                        actor_ref=command.actor_ref,
                        reason="a newer version was approved",
                        from_status="approved",
                        to_status="superseded",
                        revision=next_revision,
                    )
                target_status = "approved"
                connection.execute(
                    """
                    UPDATE training_criterion_heads
                    SET approved_version_id = ?,
                        revision = ?,
                        updated_at = datetime('now','localtime')
                    WHERE question_id = ?
                    """,
                    (
                        command.version_id,
                        next_revision,
                        command.question_id,
                    ),
                )
            else:
                target_status = "rejected"
                connection.execute(
                    """
                    UPDATE training_criterion_heads
                    SET revision = ?,
                        updated_at = datetime('now','localtime')
                    WHERE question_id = ?
                    """,
                    (next_revision, command.question_id),
                )
            connection.execute(
                """
                UPDATE training_criterion_versions
                SET status = ?,
                    decision_by = ?,
                    decision_note = ?,
                    decided_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE version_id = ?
                """,
                (
                    target_status,
                    command.actor_ref,
                    command.reason,
                    command.version_id,
                ),
            )
            _event(
                connection,
                question_id=command.question_id,
                version_id=command.version_id,
                event_type=target_status,
                actor_ref=command.actor_ref,
                reason=command.reason,
                from_status="proposed",
                to_status=target_status,
                revision=next_revision,
            )
        return self._workspace(command.question_id)

    def get_version(self, version_id: str) -> dict[str, Any]:
        clean = str(version_id or "").strip().casefold()
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM training_criterion_versions
                WHERE version_id = ?
                """,
                (clean,),
            ).fetchone()
        if row is None:
            raise CriterionVersionNotFound(clean)
        return _public_version(row)

    def freeze(
        self,
        questions: Sequence[QuestionAnalysisInput],
    ) -> tuple[dict[str, Any], ...]:
        versions: list[dict[str, Any]] = []
        missing: list[int] = []
        for question in questions:
            workspace = self.read(question)
            approved = workspace.get("approved_version")
            if not workspace["available"] or not isinstance(
                approved,
                Mapping,
            ):
                missing.append(question.question_id)
            else:
                versions.append(dict(approved))
        if missing:
            raise ApprovedCriterionMissing(missing)
        return tuple(versions)

    def create_backfill_run(
        self,
        *,
        question_ids: Sequence[int],
        request_token: str,
        mode: Literal["missing_only", "regenerate"] = "missing_only",
    ) -> tuple[dict[str, Any], bool]:
        ids = _question_ids(question_ids)
        token = _request_token(request_token)
        clean_mode = str(mode or "").strip().casefold()
        if clean_mode not in {"missing_only", "regenerate"}:
            raise ValueError("criterion backfill mode is invalid")
        fingerprint = _hash_payload(
            {"question_ids": ids, "mode": clean_mode}
        )
        run_id = _hash_payload(
            {
                "contract": "criterion-backfill-v1",
                "request_token": token,
                "input_fingerprint": fingerprint,
            }
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT run_id, input_fingerprint
                FROM training_criterion_backfill_runs
                WHERE request_token = ?
                """,
                (token,),
            ).fetchone()
            if existing is not None:
                if str(existing["input_fingerprint"]) != fingerprint:
                    raise CriterionRequestConflict(
                        "backfill request token was reused"
                    )
                return self._backfill_after_commit(
                    connection,
                    str(existing["run_id"]),
                ), False
            found = {
                int(row["id"])
                for row in connection.execute(
                    f"""
                    SELECT id
                    FROM questions
                    WHERE id IN ({','.join('?' for _ in ids)})
                      AND is_deleted = 0
                    """,
                    ids,
                ).fetchall()
            }
            if found != set(ids):
                raise CriterionVersionNotFound(
                    str(sorted(set(ids) - found))
                )
            connection.execute(
                """
                INSERT INTO training_criterion_backfill_runs (
                    run_id,
                    request_token,
                    input_fingerprint,
                    mode,
                    question_ids_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    token,
                    fingerprint,
                    clean_mode,
                    _canonical_json(ids),
                ),
            )
            connection.executemany(
                """
                INSERT INTO training_criterion_backfill_items (
                    run_id,
                    question_id
                ) VALUES (?, ?)
                """,
                ((run_id, question_id) for question_id in ids),
            )
        return self.get_backfill_run(run_id), True

    def get_backfill_run(self, run_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            return self._backfill(connection, run_id)

    def claim_backfill(self, run_id: str) -> tuple[int, ...]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status
                FROM training_criterion_backfill_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise CriterionVersionNotFound(run_id)
            if str(run["status"]) not in {"pending", "failed", "cancelled"}:
                raise CriterionTransitionError(
                    "backfill run cannot be started"
                )
            rows = connection.execute(
                """
                SELECT question_id
                FROM training_criterion_backfill_items
                WHERE run_id = ?
                  AND status IN ('pending', 'failed', 'cancelled')
                ORDER BY question_id
                """,
                (run_id,),
            ).fetchall()
            ids = tuple(int(row["question_id"]) for row in rows)
            connection.execute(
                """
                UPDATE training_criterion_backfill_runs
                SET status = 'running',
                    updated_at = datetime('now','localtime'),
                    finished_at = NULL
                WHERE run_id = ?
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE training_criterion_backfill_items
                SET status = 'running',
                    error_category = '',
                    attempt_count = attempt_count + 1,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ?
                  AND status IN ('pending', 'failed', 'cancelled')
                """,
                (run_id,),
            )
        return ids

    def finish_backfill_item(
        self,
        *,
        run_id: str,
        question_id: int,
        status: Literal[
            "succeeded",
            "failed",
            "cancelled",
            "skipped",
        ],
        version_id: str | None = None,
        error_category: str = "",
    ) -> None:
        with connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE training_criterion_backfill_items
                SET status = ?,
                    version_id = ?,
                    error_category = ?,
                    updated_at = datetime('now','localtime')
                WHERE run_id = ? AND question_id = ? AND status = 'running'
                """,
                (
                    status,
                    version_id,
                    str(error_category or ""),
                    run_id,
                    int(question_id),
                ),
            )
            if cursor.rowcount != 1:
                raise CriterionTransitionError(
                    "backfill item is no longer running"
                )

    def complete_backfill(self, run_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            statuses = [
                str(row["status"])
                for row in connection.execute(
                    """
                    SELECT status
                    FROM training_criterion_backfill_items
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
            ]
            status = _aggregate_backfill_status(statuses)
            connection.execute(
                """
                UPDATE training_criterion_backfill_runs
                SET status = ?,
                    updated_at = datetime('now','localtime'),
                    finished_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (status, run_id),
            )
        return self.get_backfill_run(run_id)

    def cancel_backfill(self, run_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE training_criterion_backfill_items
                SET status = 'cancelled',
                    error_category = 'cancelled',
                    updated_at = datetime('now','localtime')
                WHERE run_id = ? AND status IN ('pending', 'running')
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE training_criterion_backfill_runs
                SET status = 'cancelled',
                    updated_at = datetime('now','localtime'),
                    finished_at = datetime('now','localtime')
                WHERE run_id = ?
                """,
                (run_id,),
            )
        return self.get_backfill_run(run_id)

    def recover_interrupted_backfills(self) -> int:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            runs = connection.execute(
                """
                SELECT run_id
                FROM training_criterion_backfill_runs
                WHERE status = 'running'
                """
            ).fetchall()
            for row in runs:
                run_id = str(row["run_id"])
                connection.execute(
                    """
                    UPDATE training_criterion_backfill_items
                    SET status = 'failed',
                        error_category = 'interrupted',
                        updated_at = datetime('now','localtime')
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (run_id,),
                )
                connection.execute(
                    """
                    UPDATE training_criterion_backfill_runs
                    SET status = 'failed',
                        updated_at = datetime('now','localtime'),
                        finished_at = datetime('now','localtime')
                    WHERE run_id = ?
                    """,
                    (run_id,),
                )
        return len(runs)

    def recover_backfill(self, run_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status
                FROM training_criterion_backfill_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise CriterionVersionNotFound(run_id)
            if str(run["status"]) == "running":
                connection.execute(
                    """
                    UPDATE training_criterion_backfill_items
                    SET status = 'failed',
                        error_category = 'interrupted',
                        updated_at = datetime('now','localtime')
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (run_id,),
                )
                connection.execute(
                    """
                    UPDATE training_criterion_backfill_runs
                    SET status = 'failed',
                        updated_at = datetime('now','localtime'),
                        finished_at = datetime('now','localtime')
                    WHERE run_id = ?
                    """,
                    (run_id,),
                )
        return self.get_backfill_run(run_id)

    def _sync_source(self, question: QuestionAnalysisInput) -> None:
        source_hash = question.criterion_source_content_hash
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            head = _head(connection, question.question_id)
            if head is None or str(head["current_source_hash"]) == source_hash:
                return
            revision = int(head["revision"]) + 1
            version_ids = {
                str(value)
                for value in (
                    head["current_version_id"],
                    head["approved_version_id"],
                )
                if value
            }
            for version_id in version_ids:
                row = connection.execute(
                    """
                    SELECT status, source_content_hash
                    FROM training_criterion_versions
                    WHERE version_id = ?
                    """,
                    (version_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row["source_content_hash"]) == source_hash
                    or str(row["status"]) not in {"proposed", "approved"}
                ):
                    continue
                from_status = str(row["status"])
                connection.execute(
                    """
                    UPDATE training_criterion_versions
                    SET status = 'stale',
                        updated_at = datetime('now','localtime')
                    WHERE version_id = ?
                    """,
                    (version_id,),
                )
                _event(
                    connection,
                    question_id=question.question_id,
                    version_id=version_id,
                    event_type="stale",
                    actor_ref="system",
                    reason="question content changed",
                    from_status=from_status,
                    to_status="stale",
                    revision=revision,
                )
            connection.execute(
                """
                UPDATE training_criterion_heads
                SET approved_version_id = CASE
                        WHEN approved_version_id IN (
                            SELECT version_id
                            FROM training_criterion_versions
                            WHERE status = 'approved'
                              AND source_content_hash = ?
                        )
                        THEN approved_version_id
                        ELSE NULL
                    END,
                    current_source_hash = ?,
                    revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE question_id = ?
                """,
                (
                    source_hash,
                    source_hash,
                    revision,
                    question.question_id,
                ),
            )

    def _workspace(self, question_id: int) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            return self._workspace_query(connection, question_id)

    def _workspace_after_commit(
        self,
        connection,
        question_id: int,
        _version_id: str,
    ) -> dict[str, Any]:
        connection.commit()
        return self._workspace_query(connection, question_id)

    @staticmethod
    def _workspace_query(connection, question_id: int) -> dict[str, Any]:
        head = _head(connection, question_id)
        rows = connection.execute(
            """
            SELECT *
            FROM training_criterion_versions
            WHERE question_id = ?
            ORDER BY version_number DESC
            """,
            (int(question_id),),
        ).fetchall()
        versions = [_public_version(row) for row in rows]
        by_id = {item["version_id"]: item for item in versions}
        current = (
            None
            if head is None
            else by_id.get(str(head["current_version_id"]))
        )
        approved = (
            None
            if head is None or head["approved_version_id"] is None
            else by_id.get(str(head["approved_version_id"]))
        )
        available = bool(
            approved
            and approved["status"] == "approved"
            and head is not None
            and approved["source_content_hash"]
            == str(head["current_source_hash"])
        )
        return {
            "question_id": int(question_id),
            "state": (
                "available"
                if available
                else str(current["status"])
                if current is not None
                else "missing"
            ),
            "available": available,
            "revision": int(head["revision"]) if head is not None else 0,
            "current_source_hash": (
                str(head["current_source_hash"])
                if head is not None
                else ""
            ),
            "current_version": current,
            "approved_version": approved,
            "versions": versions,
        }

    @staticmethod
    def _backfill(connection, run_id: str) -> dict[str, Any]:
        clean = str(run_id or "").strip().casefold()
        run = connection.execute(
            """
            SELECT *
            FROM training_criterion_backfill_runs
            WHERE run_id = ?
            """,
            (clean,),
        ).fetchone()
        if run is None:
            raise CriterionVersionNotFound(clean)
        items = connection.execute(
            """
            SELECT *
            FROM training_criterion_backfill_items
            WHERE run_id = ?
            ORDER BY question_id
            """,
            (clean,),
        ).fetchall()
        return {
            "run_id": clean,
            "status": str(run["status"]),
            "mode": str(run["mode"]),
            "question_ids": json.loads(str(run["question_ids_json"])),
            "created_at": str(run["created_at"]),
            "updated_at": str(run["updated_at"]),
            "finished_at": run["finished_at"],
            "items": [
                {
                    "question_id": int(row["question_id"]),
                    "status": str(row["status"]),
                    "version_id": row["version_id"],
                    "error_category": str(
                        row["error_category"] or ""
                    ),
                    "attempt_count": int(row["attempt_count"]),
                }
                for row in items
            ],
        }

    def _backfill_after_commit(self, connection, run_id: str) -> dict[str, Any]:
        connection.commit()
        return self._backfill(connection, run_id)


def _normalize_draft(
    question: QuestionAnalysisInput,
    value: TrainingCriteriaDraft | Mapping[str, Any],
) -> TrainingCriteriaDraft:
    if isinstance(value, TrainingCriteriaDraft):
        draft = value
    else:
        draft = TrainingCriteriaDraft.from_model_dict(
            dict(value),
            question=question,
        )
    if draft.question_id != question.question_id:
        raise ValueError("criterion draft belongs to another question")
    if draft.source_content_hash != question.criterion_source_content_hash:
        if isinstance(value, TrainingCriteriaDraft):
            raise ValueError("criterion draft source is stale")
        draft = TrainingCriteriaDraft.from_model_dict(
            dict(value),
            question=question,
        )
    return draft


def _public_version(row) -> dict[str, Any]:
    return {
        "version_id": str(row["version_id"]),
        "question_id": int(row["question_id"]),
        "version_number": int(row["version_number"]),
        "parent_version_id": row["parent_version_id"],
        "source_content_hash": str(row["source_content_hash"]),
        "schema_version": str(row["schema_version"]),
        "status": str(row["status"]),
        "source_kind": str(row["source_kind"]),
        "source_reference": str(row["source_reference"]),
        "criteria": json.loads(str(row["criteria_json"])),
        "criteria_hash": str(row["criteria_hash"]),
        "quality_status": str(row["quality_status"]),
        "quality_codes": _json_strings(row["quality_codes_json"]),
        "created_by": str(row["created_by"]),
        "decision_by": row["decision_by"],
        "decision_note": row["decision_note"],
        "decided_at": row["decided_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _head(connection, question_id: int):
    return connection.execute(
        """
        SELECT *
        FROM training_criterion_heads
        WHERE question_id = ?
        """,
        (int(question_id),),
    ).fetchone()


def _event(
    connection,
    *,
    question_id: int,
    version_id: str,
    event_type: str,
    actor_ref: str,
    reason: str,
    from_status: str | None,
    to_status: str,
    revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO training_criterion_events (
            question_id,
            version_id,
            event_type,
            actor_ref,
            reason,
            from_status,
            to_status,
            resulting_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(question_id),
            version_id,
            event_type,
            actor_ref,
            reason,
            from_status,
            to_status,
            int(revision),
        ),
    )


def _aggregate_backfill_status(statuses: Sequence[str]) -> str:
    if statuses and all(value in {"succeeded", "skipped"} for value in statuses):
        return "succeeded"
    if any(value in {"succeeded", "skipped"} for value in statuses):
        return "partial"
    if statuses and all(value == "cancelled" for value in statuses):
        return "cancelled"
    return "failed"


def _source_kind(value: object) -> CriterionSourceKind:
    normalized = str(value or "").strip().casefold()
    if normalized not in {
        "combined_model",
        "confirmed_rubric_adapter",
        "teacher_manual",
        "backfill",
    }:
        raise ValueError("criterion source kind is invalid")
    return normalized  # type: ignore[return-value]


def _request_token(value: object) -> str:
    token = str(value or "").strip().casefold()
    if not _TOKEN.fullmatch(token):
        raise ValueError("request_token is invalid")
    return token


def _question_ids(values: Sequence[int]) -> tuple[int, ...]:
    result = tuple(dict.fromkeys(int(value) for value in values))
    if (
        not result
        or len(result) > 500
        or any(value <= 0 for value in result)
    ):
        raise ValueError(
            "question_ids must contain between 1 and 500 positive IDs"
        )
    return result


def _blank_count(value: object) -> int:
    text = str(value or "")
    named = re.findall(r"第[一二三四五六七八九十\d]+空", text)
    underscores = re.findall(r"_{2,}", text)
    empty_brackets = re.findall(r"[（(]\s*[）)]", text)
    return max(len(named), len(underscores) + len(empty_brackets), 1)


def _contains_teacher_visible_english(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    words = [
        word
        for word in re.findall(r"[A-Za-z]{2,}", text)
        if not (
            (word.isupper() and len(word) <= 3)
            or word.casefold() in {
                "sin",
                "cos",
                "tan",
                "log",
                "ln",
                "cm",
                "mm",
                "km",
            }
        )
    ]
    return bool(words)


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _json_strings(value: object) -> list[str]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item).strip()]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_payload(value: object) -> str:
    return _sha256(_canonical_json(value))


__all__ = [
    "ADVISORY_QUALITY_CODES",
    "ApprovedCriterionMissing",
    "CriterionQualityError",
    "CriterionRequestConflict",
    "CriterionReviewCommand",
    "CriterionRevisionConflict",
    "CriterionTransitionError",
    "CriterionVersionNotFound",
    "QualityGateResult",
    "TrainingCriterionModule",
    "blocking_quality_codes",
    "evaluate_criterion_quality",
]
