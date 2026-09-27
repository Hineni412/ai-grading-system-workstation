from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.training_assessment.contracts import (
    EvidenceSyncCommand,
    TrainingEvidenceSink,
    TrainingPaperOutcome,
    stable_hash,
)
from question_bank.database.schema import connect
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.mastery.current import CurrentMasteryCalculator
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)


class TrainingEvidenceError(RuntimeError):
    pass


class EvidenceSyncConflict(TrainingEvidenceError):
    pass


class EvidenceReviewConflict(TrainingEvidenceError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__(
            "training evidence review revision is stale"
        )


class EvidenceSourceInvalid(TrainingEvidenceError):
    pass


class SQLiteTrainingEvidenceSink:
    """Idempotent evidence projection Adapter."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def deliver(self, payload: Mapping[str, Any]) -> None:
        evidence_id = str(payload["evidence_id"])
        payload_hash = str(payload["payload_hash"])
        source_revision = int(payload["source_review_revision"])
        target_status = (
            "active"
            if str(payload["action"]) == "publish"
            else "withdrawn"
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT source_review_revision, payload_hash, status
                FROM training_evidence_records
                WHERE evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
            if existing is not None:
                current_revision = int(existing["source_review_revision"])
                if current_revision > source_revision:
                    connection.commit()
                    return
                if current_revision == source_revision:
                    if (
                        str(existing["payload_hash"]) == payload_hash
                        and str(existing["status"]) == target_status
                    ):
                        connection.commit()
                        return
                    if str(existing["status"]) == "withdrawn":
                        connection.commit()
                        return
                    if target_status == "withdrawn":
                        pass
                    else:
                        raise EvidenceSyncConflict(
                            "同一证据修订包含不同内容"
                        )
            values = _record_values(payload, target_status)
            connection.execute(
                """
                INSERT INTO training_evidence_records (
                    evidence_id, submission_id, submission_revision,
                    task_item_code, source_review_revision, status,
                    student_id, stable_key, occurred_at, achieved_points,
                    total_points, coverage_ratio, difficulty_weight,
                    evidence_weight, expected_minutes,
                    criterion_version_id, criterion_hash, payload_hash,
                    final_points_json, teacher_corrections_json,
                    source_json, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, datetime('now','localtime')
                )
                ON CONFLICT(evidence_id) DO UPDATE SET
                    source_review_revision = excluded.source_review_revision,
                    status = excluded.status,
                    student_id = excluded.student_id,
                    stable_key = excluded.stable_key,
                    occurred_at = excluded.occurred_at,
                    achieved_points = excluded.achieved_points,
                    total_points = excluded.total_points,
                    coverage_ratio = excluded.coverage_ratio,
                    difficulty_weight = excluded.difficulty_weight,
                    evidence_weight = excluded.evidence_weight,
                    expected_minutes = excluded.expected_minutes,
                    criterion_version_id = excluded.criterion_version_id,
                    criterion_hash = excluded.criterion_hash,
                    payload_hash = excluded.payload_hash,
                    final_points_json = excluded.final_points_json,
                    teacher_corrections_json =
                        excluded.teacher_corrections_json,
                    source_json = excluded.source_json,
                    updated_at = excluded.updated_at
                """,
                values,
            )


class TrainingEvidencePublisher:
    """Deep implementation for evidence, feedback and next-round drafts."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        outcome_loader: Callable[
            [str, int], TrainingPaperOutcome | None
        ],
        sink: TrainingEvidenceSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.outcome_loader = outcome_loader
        self.sink = sink or SQLiteTrainingEvidenceSink(self.db_path)
        self.clock = clock or (lambda: datetime.now().astimezone())
        try:
            self.current_knowledge = (
                CurrentKnowledgeResolver.from_active_database(self.db_path)
            )
        except CurrentKnowledgeUnavailable as exc:
            raise TrainingEvidenceError(
                "current knowledge standard is unavailable"
            ) from exc

    def sync(
        self,
        submission_id: str,
        submission_revision: int,
        command: EvidenceSyncCommand,
    ) -> dict[str, Any]:
        outcome = self._require_outcome(
            submission_id, submission_revision
        )
        if outcome.review_revision != command.expected_review_revision:
            raise EvidenceReviewConflict(
                command.expected_review_revision,
                outcome.review_revision,
            )
        if outcome.status == "running":
            raise EvidenceSourceInvalid(
                "判定仍在运行，不能发布训练证据"
            )
        fingerprint = stable_hash(
            {
                "kind": "training-evidence-sync-v1",
                "submission_id": submission_id,
                "submission_revision": submission_revision,
                "command": asdict(command),
            }
        )
        existing = self._event(command.operation_token)
        if existing is not None:
            if str(existing["operation_fingerprint"]) != fingerprint:
                raise EvidenceSyncConflict(
                    "操作标识已被另一项证据操作使用"
                )
            replayed = self.replay()
            if replayed["examined_count"] == 0:
                feedback = self.get_feedback(
                    submission_id, submission_revision
                )
                if feedback is not None:
                    return feedback
            return self._refresh(
                submission_id,
                submission_revision,
                actor_ref=command.actor_ref,
            )

        context = self._context(submission_id, submission_revision)
        payloads = (
            self._publish_payloads(outcome, context)
            if command.action == "publish"
            else self._withdraw_payloads(
                outcome,
                context,
            )
        )
        now = self._now().isoformat()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = connection.execute(
                """
                SELECT operation_fingerprint
                FROM training_evidence_sync_events
                WHERE operation_token = ?
                """,
                (command.operation_token,),
            ).fetchone()
            if repeated is not None:
                if str(repeated["operation_fingerprint"]) != fingerprint:
                    raise EvidenceSyncConflict(
                        "操作标识已被另一项证据操作使用"
                    )
                connection.commit()
            else:
                run = connection.execute(
                    """
                    SELECT review_revision
                    FROM training_assessment_runs
                    WHERE submission_id = ?
                      AND submission_revision = ?
                    """,
                    (submission_id, submission_revision),
                ).fetchone()
                if run is None:
                    raise EvidenceSourceInvalid("训练判定不存在")
                current_revision = int(run["review_revision"])
                if current_revision != command.expected_review_revision:
                    raise EvidenceReviewConflict(
                        command.expected_review_revision,
                        current_revision,
                    )
                for payload in payloads:
                    self._insert_outbox(
                        connection,
                        payload,
                        actor_ref=command.actor_ref,
                        reason=command.reason,
                        now=now,
                    )
                event_id = stable_hash(
                    {
                        "kind": "training-evidence-sync-event-v1",
                        "operation_token": command.operation_token,
                    }
                )
                connection.execute(
                    """
                    INSERT INTO training_evidence_sync_events (
                        event_id, operation_token, operation_fingerprint,
                        submission_id, submission_revision,
                        source_review_revision, action, actor_ref, reason,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        command.operation_token,
                        fingerprint,
                        submission_id,
                        submission_revision,
                        outcome.review_revision,
                        command.action,
                        command.actor_ref,
                        command.reason,
                        now,
                    ),
                )
                connection.commit()
        self.replay()
        return self._refresh(
            submission_id,
            submission_revision,
            actor_ref=command.actor_ref,
        )

    def replay(self, max_items: int | None = None) -> dict[str, Any]:
        if max_items is not None and int(max_items) < 1:
            raise ValueError("max_items must be positive")
        sql = """
            SELECT * FROM training_evidence_outbox
            WHERE status IN ('pending', 'delivering', 'failed')
            ORDER BY created_at, outbox_id
        """
        params: tuple[Any, ...] = ()
        if max_items is not None:
            sql += " LIMIT ?"
            params = (int(max_items),)
        with connect(self.db_path) as connection:
            rows = connection.execute(sql, params).fetchall()
        delivered = 0
        failed = 0
        affected: set[tuple[str, int]] = set()
        for row in rows:
            affected.add(
                (
                    str(row["submission_id"]),
                    int(row["submission_revision"]),
                )
            )
            with connect(self.db_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE training_evidence_outbox
                    SET status = 'delivering',
                        attempt_count = attempt_count + 1,
                        error_code = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE outbox_id = ?
                    """,
                    (row["outbox_id"],),
                )
            try:
                payload = json.loads(str(row["payload_json"]))
                self.sink.deliver(payload)
            except Exception:
                failed += 1
                with connect(self.db_path) as connection:
                    connection.execute(
                        """
                        UPDATE training_evidence_outbox
                        SET status = 'failed',
                            error_code = 'evidence_delivery_failed',
                            updated_at = datetime('now','localtime')
                        WHERE outbox_id = ?
                        """,
                        (row["outbox_id"],),
                    )
                continue
            delivered += 1
            with connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE training_evidence_outbox
                    SET status = 'delivered', error_code = NULL,
                        delivered_at = COALESCE(
                            delivered_at, datetime('now','localtime')
                        ),
                        updated_at = datetime('now','localtime')
                    WHERE outbox_id = ?
                    """,
                    (row["outbox_id"],),
                )
        feedbacks = []
        for submission_id, revision in sorted(affected):
            try:
                feedbacks.append(
                    self._refresh(
                        submission_id,
                        revision,
                        actor_ref=self._latest_actor(
                            submission_id, revision
                        ),
                    )
                )
            except TrainingEvidenceError:
                continue
        return {
            "examined_count": len(rows),
            "delivered_count": delivered,
            "failed_count": failed,
            "feedbacks": feedbacks,
        }

    def get_feedback(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT feedback_json
                FROM training_feedback_snapshots
                WHERE submission_id = ? AND submission_revision = ?
                """,
                (submission_id, int(submission_revision)),
            ).fetchone()
        return (
            None
            if row is None
            else json.loads(str(row["feedback_json"]))
        )

    def _publish_payloads(
        self,
        outcome: TrainingPaperOutcome,
        context: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        metadata = {
            str(item["task_item_code"]): item
            for item in context["items"]
        }
        payloads: list[dict[str, Any]] = []
        occurred_at = _evidence_time(
            context.get("assessment_finished_at"),
            self._now(),
        )
        for question in outcome.questions:
            task_item_code = str(question["task_item_code"])
            item = metadata.get(task_item_code)
            if item is None:
                raise EvidenceSourceInvalid(
                    "冻结题目来源不完整"
                )
            review_points = tuple(question["review_points"])
            states = tuple(point.get("state") for point in review_points)
            if (
                question["review_status"] != "completed"
                or not states
                or any(state not in {"met", "not_met"} for state in states)
            ):
                continue
            resolved_targets = self.current_knowledge.resolve(
                item["matched_key"]
            )
            if not resolved_targets:
                continue
            total_points = len(review_points)
            achieved_points = sum(state == "met" for state in states)
            final_points = [
                {
                    "point_id": str(point["point_id"]),
                    "state": str(point["state"]),
                    "evidence": str(point.get("evidence") or ""),
                    "teacher_locked": bool(point["teacher_locked"]),
                }
                for point in review_points
            ]
            teacher_corrections = [
                {
                    "point_id": str(point["point_id"]),
                    "state": str(point["state"]),
                    "reason": str(point.get("teacher_reason") or ""),
                    "actor_ref": str(point.get("actor_ref") or ""),
                    "lock_revision": int(point["lock_revision"]),
                }
                for point in review_points
                if point["teacher_locked"]
            ]
            for target in resolved_targets:
                payload = {
                    "schema_version": "training-evidence-v1",
                    "action": "publish",
                    "evidence_id": _evidence_id(
                        outcome.submission_id,
                        outcome.submission_revision,
                        task_item_code,
                        target.stable_key,
                    ),
                    "submission_id": outcome.submission_id,
                    "submission_revision": outcome.submission_revision,
                    "task_item_code": task_item_code,
                    "source_review_revision": outcome.review_revision,
                    "student_id": str(context["student_id"]),
                    "stable_key": target.stable_key,
                    "occurred_at": occurred_at,
                    "achieved_points": achieved_points,
                    "total_points": total_points,
                    "coverage_ratio": round(
                        achieved_points / total_points, 6
                    ),
                    "difficulty_weight": _difficulty_weight(
                        item["difficulty"]
                    ),
                    "evidence_weight": 1.0,
                    "expected_minutes": _optional_minutes(
                        item["estimated_minutes"]
                    ),
                    "criterion_version_id": str(
                        item["criterion_version_id"]
                    ),
                    "criterion_hash": str(item["criterion_hash"]),
                    "final_points": final_points,
                    "teacher_corrections": teacher_corrections,
                    "source": {
                        "assessment_run_id": outcome.run_id,
                        "paper_instance_id": str(
                            context["paper_instance_id"]
                        ),
                        "draft_id": str(context["draft_id"]),
                        "draft_result_version": str(
                            context["draft_result_version"]
                        ),
                        "bank_question_id": item["bank_question_id"],
                        "matched_name": target.display_name,
                        "source_paper": str(item["source_paper"]),
                        "criterion_version_id": str(
                            item["criterion_version_id"]
                        ),
                    },
                }
                payload["payload_hash"] = stable_hash(payload)
                payloads.append(payload)
        published_identities = {
            (
                str(payload["task_item_code"]),
                str(payload["stable_key"]),
            )
            for payload in payloads
        }
        with connect(self.db_path) as connection:
            stale_rows = connection.execute(
                """
                SELECT * FROM training_evidence_records
                WHERE submission_id = ? AND submission_revision = ?
                  AND status = 'active'
                ORDER BY task_item_code
                """,
                (outcome.submission_id, outcome.submission_revision),
            ).fetchall()
        for row in stale_rows:
            if (
                str(row["task_item_code"]),
                str(row["stable_key"]),
            ) not in published_identities:
                payloads.append(
                    self._withdraw_payload(
                        row,
                        outcome=outcome,
                        context=context,
                    )
                )
        return tuple(payloads)

    def _withdraw_payloads(
        self,
        outcome: TrainingPaperOutcome,
        context: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM training_evidence_records
                WHERE submission_id = ? AND submission_revision = ?
                  AND status = 'active'
                ORDER BY task_item_code
                """,
                (outcome.submission_id, outcome.submission_revision),
            ).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            payloads.append(
                self._withdraw_payload(
                    row,
                    outcome=outcome,
                    context=context,
                )
            )
        return tuple(payloads)

    @staticmethod
    def _withdraw_payload(
        row: Mapping[str, Any],
        *,
        outcome: TrainingPaperOutcome,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "training-evidence-v1",
            "action": "withdraw",
            "evidence_id": str(row["evidence_id"]),
            "submission_id": outcome.submission_id,
            "submission_revision": outcome.submission_revision,
            "task_item_code": str(row["task_item_code"]),
            "source_review_revision": outcome.review_revision,
            "student_id": str(row["student_id"]),
            "stable_key": str(row["stable_key"]),
            "occurred_at": None,
            "achieved_points": None,
            "total_points": None,
            "coverage_ratio": None,
            "difficulty_weight": None,
            "evidence_weight": None,
            "expected_minutes": None,
            "criterion_version_id": str(
                row["criterion_version_id"]
            ),
            "criterion_hash": str(row["criterion_hash"]),
            "final_points": json.loads(
                str(row["final_points_json"])
            ),
            "teacher_corrections": json.loads(
                str(row["teacher_corrections_json"])
            ),
            "source": {
                **json.loads(str(row["source_json"])),
                "withdrawn_from_review_revision": int(
                    row["source_review_revision"]
                ),
                "paper_instance_id": str(context["paper_instance_id"]),
            },
        }
        payload["payload_hash"] = stable_hash(payload)
        return payload

    @staticmethod
    def _insert_outbox(
        connection: Any,
        payload: Mapping[str, Any],
        *,
        actor_ref: str,
        reason: str,
        now: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT payload_hash
            FROM training_evidence_outbox
            WHERE evidence_id = ? AND source_review_revision = ?
              AND action = ?
            """,
            (
                payload["evidence_id"],
                payload["source_review_revision"],
                payload["action"],
            ),
        ).fetchone()
        if existing is not None:
            if str(existing["payload_hash"]) != str(
                payload["payload_hash"]
            ):
                raise EvidenceSyncConflict(
                    "同一证据修订包含不同 outbox 内容"
                )
            return
        outbox_id = stable_hash(
            {
                "kind": "training-evidence-outbox-v1",
                "evidence_id": payload["evidence_id"],
                "source_review_revision": payload[
                    "source_review_revision"
                ],
                "action": payload["action"],
            }
        )
        connection.execute(
            """
            INSERT INTO training_evidence_outbox (
                outbox_id, evidence_id, submission_id,
                submission_revision, task_item_code,
                source_review_revision, action, payload_hash,
                payload_json, status, actor_ref, reason,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                outbox_id,
                payload["evidence_id"],
                payload["submission_id"],
                payload["submission_revision"],
                payload["task_item_code"],
                payload["source_review_revision"],
                payload["action"],
                payload["payload_hash"],
                _json(payload),
                actor_ref,
                reason,
                now,
                now,
            ),
        )

    def _refresh(
        self,
        submission_id: str,
        submission_revision: int,
        *,
        actor_ref: str,
    ) -> dict[str, Any]:
        outcome = self._require_outcome(
            submission_id, submission_revision
        )
        context = self._context(submission_id, submission_revision)
        with connect(self.db_path) as connection:
            evidence_rows = connection.execute(
                """
                SELECT * FROM training_evidence_records
                WHERE submission_id = ? AND submission_revision = ?
                ORDER BY task_item_code
                """,
                (submission_id, submission_revision),
            ).fetchall()
            pending = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM training_evidence_outbox
                    WHERE submission_id = ? AND submission_revision = ?
                      AND status <> 'delivered'
                    """,
                    (submission_id, submission_revision),
                ).fetchone()["total"]
            )
            latest_event = connection.execute(
                """
                SELECT action
                FROM training_evidence_sync_events
                WHERE submission_id = ? AND submission_revision = ?
                ORDER BY source_review_revision DESC,
                         CASE action
                           WHEN 'withdraw' THEN 1
                           ELSE 0
                         END DESC,
                         created_at DESC,
                         event_id DESC
                LIMIT 1
                """,
                (submission_id, submission_revision),
            ).fetchone()
            timeline_rows = connection.execute(
                """
                SELECT action, actor_ref, reason,
                       source_review_revision, created_at
                FROM training_evidence_sync_events
                WHERE submission_id = ? AND submission_revision = ?
                ORDER BY rowid
                """,
                (submission_id, submission_revision),
            ).fetchall()
        active_by_task = {
            str(row["task_item_code"]): row
            for row in evidence_rows
            if str(row["status"]) == "active"
        }
        item_by_task = {
            str(item["task_item_code"]): item
            for item in context["items"]
        }
        questions: list[dict[str, Any]] = []
        publishable_count = 0
        for question in outcome.questions:
            task_item_code = str(question["task_item_code"])
            item = item_by_task[task_item_code]
            states = tuple(
                point.get("state")
                for point in question["review_points"]
            )
            publishable = (
                question["review_status"] == "completed"
                and bool(states)
                and all(
                    state in {"met", "not_met"} for state in states
                )
            )
            if publishable:
                publishable_count += 1
            record = active_by_task.get(task_item_code)
            questions.append(
                {
                    "task_item_code": task_item_code,
                    "item_order": int(question["item_order"]),
                    "knowledge": {
                        "stable_key": str(item["matched_key"]),
                        "display_name": str(item["matched_name"]),
                    },
                    "coverage": {
                        "met_count": int(question["met_count"]),
                        "total_count": int(question["total_count"]),
                        "ratio": (
                            None
                            if not publishable
                            else round(
                                int(question["met_count"])
                                / int(question["total_count"]),
                                6,
                            )
                        ),
                    },
                    "difficulty": item["difficulty"],
                    "expected_minutes": item["estimated_minutes"],
                    "source_paper": str(item["source_paper"]),
                    "review_status": str(question["review_status"]),
                    "publication_status": (
                        "published"
                        if record is not None
                        else (
                            "ready"
                            if publishable
                            else "not_publishable"
                        )
                    ),
                    "publication_reason": _publication_reason(
                        question,
                        published=record is not None,
                    ),
                    "points": [
                        dict(point)
                        for point in question["review_points"]
                    ],
                }
            )
        active_count = len(active_by_task)
        latest_action = (
            None if latest_event is None else str(latest_event["action"])
        )
        if pending:
            status = "publication_pending"
        elif latest_action == "withdraw" and active_count == 0:
            status = "withdrawn"
        elif (
            publishable_count == len(outcome.questions)
            and active_count == publishable_count
        ):
            status = "complete"
        else:
            status = "partial"
        mastery_changes = self._mastery_changes(
            context,
            submission_id=submission_id,
            submission_revision=submission_revision,
        )
        evidence_version = self._evidence_version(
            submission_id, submission_revision
        )
        next_round = self._next_round(
            context,
            evidence_version=evidence_version,
            actor_ref=actor_ref,
            mastery_changes=mastery_changes,
            publication_pending=pending > 0,
        )
        feedback_id = stable_hash(
            {
                "kind": "training-feedback-v1",
                "submission_id": submission_id,
                "submission_revision": submission_revision,
            }
        )
        feedback = {
            "schema_version": "training-feedback-v1",
            "feedback_id": feedback_id,
            "submission_id": submission_id,
            "submission_revision": submission_revision,
            "source_review_revision": outcome.review_revision,
            "status": status,
            "student": {
                "student_id": str(context["student_id"]),
                "student_code": str(context["student_code"]),
                "student_name": str(context["student_name"]),
                "class_id": str(context["class_id"]),
            },
            "summary": {
                "published_question_count": active_count,
                "ready_question_count": publishable_count,
                "total_question_count": len(outcome.questions),
                "pending_outbox_count": pending,
                "message": _feedback_message(
                    status,
                    active_count=active_count,
                    total_count=len(outcome.questions),
                ),
            },
            "questions": questions,
            "mastery_changes": mastery_changes,
            "next_round": next_round,
            "timeline": [
                {
                    "action": str(row["action"]),
                    "actor_ref": str(row["actor_ref"]),
                    "reason": str(row["reason"]),
                    "source_review_revision": int(
                        row["source_review_revision"]
                    ),
                    "created_at": str(row["created_at"]),
                }
                for row in timeline_rows
            ],
            "safety": {
                "is_exam_score": False,
                "changes_exam_score": False,
                "auto_paper_created": False,
                "auto_printed": False,
            },
            "evidence_version": evidence_version,
        }
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO training_feedback_snapshots (
                    feedback_id, submission_id, submission_revision,
                    source_review_revision, evidence_version, status,
                    feedback_json, next_draft_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(submission_id, submission_revision)
                DO UPDATE SET
                    source_review_revision =
                        excluded.source_review_revision,
                    evidence_version = excluded.evidence_version,
                    status = excluded.status,
                    feedback_json = excluded.feedback_json,
                    next_draft_id = excluded.next_draft_id,
                    updated_at = excluded.updated_at
                """,
                (
                    feedback_id,
                    submission_id,
                    submission_revision,
                    outcome.review_revision,
                    evidence_version,
                    status,
                    _json(feedback),
                    next_round.get("draft_id"),
                ),
            )
        return feedback

    def _mastery_changes(
        self,
        context: Mapping[str, Any],
        *,
        submission_id: str,
        submission_revision: int,
    ) -> list[dict[str, Any]]:
        diagnosis = context.get("diagnosis")
        profile = diagnosis if isinstance(diagnosis, Mapping) else {}
        student_id = str(context["student_id"])
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT evidence_id
                FROM training_evidence_records
                WHERE student_id = ? AND status = 'active'
                  AND submission_id = ? AND submission_revision = ?
                ORDER BY evidence_id
                """,
                (student_id, submission_id, submission_revision),
            ).fetchall()
        current_ids = frozenset(str(row["evidence_id"]) for row in rows)
        calculator = CurrentMasteryCalculator(
            self.db_path,
            self.current_knowledge,
            clock=self.clock,
            data_root=self.data_root,
        )
        after_all = calculator.calculate(profile)
        before_all = calculator.calculate(
            profile,
            exclude_training_evidence_ids=current_ids,
        )
        after = {
            key: value
            for (owner, key), value in after_all.items()
            if owner == student_id
        }
        before = {
            key: value
            for (owner, key), value in before_all.items()
            if owner == student_id
        }
        context_keys = {
            resolved.stable_key
            for item in context["items"]
            for resolved in self.current_knowledge.resolve(item["matched_key"])
        }
        keys = sorted(context_keys | set(after) | set(before))
        changes = []
        for stable_key in keys:
            node = self.current_knowledge.node(stable_key)
            if node is None:
                continue
            before_item = before.get(stable_key)
            after_item = after.get(stable_key)
            before_value = None if before_item is None else before_item.value
            after_value = None if after_item is None else after_item.value
            delta = (
                None
                if before_value is None or after_value is None
                else round(after_value - before_value, 6)
            )
            changes.append(
                {
                    "stable_key": stable_key,
                    "display_name": node.display_name,
                    "mastery_before": (
                        _missing_current_mastery()
                        if before_item is None
                        else before_item.to_dict()
                    ),
                    "mastery_after": (
                        _missing_current_mastery()
                        if after_item is None
                        else after_item.to_dict()
                    ),
                    "mastery_delta": delta,
                    "reason": _current_mastery_reason(
                        delta=delta,
                        current_evidence_count=len(current_ids),
                    ),
                }
            )
        return changes

    def _next_round(
        self,
        context: Mapping[str, Any],
        *,
        evidence_version: str,
        actor_ref: str,
        mastery_changes: Sequence[Mapping[str, Any]],
        publication_pending: bool,
    ) -> dict[str, Any]:
        if publication_pending:
            return {
                "status": "waiting_for_evidence",
                "draft_id": None,
                "message": "证据仍在等待重放，尚未生成下一轮草稿。",
                "changes": [],
            }
        diagnosis = context.get("diagnosis")
        config_payload = context.get("recommendation_config")
        if not isinstance(diagnosis, Mapping) or not isinstance(
            config_payload, Mapping
        ):
            return {
                "status": "source_unavailable",
                "draft_id": None,
                "message": "原推荐来源不完整，证据已保留；请教师重新选择范围生成草稿。",
                "changes": [],
            }
        student_id = str(context["student_id"])
        students = diagnosis.get("students")
        selected_students = [
            deepcopy(item)
            for item in (students if isinstance(students, list) else [])
            if isinstance(item, Mapping)
            and str(item.get("student_id") or "") == student_id
        ]
        if not selected_students:
            return {
                "status": "source_unavailable",
                "draft_id": None,
                "message": "原推荐中找不到该学生，证据已保留；请教师重新选择范围。",
                "changes": [],
            }
        next_diagnosis = {
            **dict(diagnosis),
            "students": selected_students,
        }
        # The paper keeps its original diagnosis. Only the next draft receives
        # the mastery just calculated from the published training evidence.
        current_by_key = {
            str(item["stable_key"]): item["mastery_after"]
            for item in mastery_changes
        }
        for point in selected_students[0].get("weak_points", []):
            targets = self.current_knowledge.resolve(
                point.get("knowledge_key") or point.get("knowledge_point")
            )
            if len(targets) != 1:
                continue
            current = current_by_key.get(targets[0].stable_key)
            if current is None:
                continue
            point.update(
                mastery=current.get("value"),
                evidence_count=int(current.get("evidence_count") or 0),
                effective_weight=float(current.get("effective_weight") or 0),
            )
        try:
            config = PersonalizedRecommendationConfig(
                **{
                    field: config_payload[field]
                    for field in (
                        "question_count",
                        "expected_minutes",
                        "difficulty_min",
                        "difficulty_max",
                        "target_keys",
                        "scope_keys",
                        "curriculum_volume_id",
                        "training_intent",
                        "teaching_progress_chapter_id",
                        "exclude_current_exam_originals",
                    )
                    if field in config_payload
                }
            )
            token = stable_hash(
                {
                    "kind": "training-next-round-draft-v2-current-mastery",
                    "submission_id": context["submission_id"],
                    "submission_revision": context[
                        "submission_revision"
                    ],
                    "student_id": student_id,
                    "evidence_version": evidence_version,
                    "diagnosis": next_diagnosis,
                }
            )[:32]
            draft = PersonalizedRecommendationModule(
                db_path=self.db_path,
                data_root=self.data_root,
                clock=self.clock,
            ).create(
                request_token=token,
                diagnosis=next_diagnosis,
                config=config,
                actor_ref=actor_ref,
            )
        except Exception:
            return {
                "status": "source_unavailable",
                "draft_id": None,
                "message": "下一轮候选来源已变化，证据和掌握度已保留；请教师重新生成草稿。",
                "changes": [],
            }
        student = next(
            (
                item
                for item in draft["students"]
                if item["student_id"] == student_id
            ),
            {"items": []},
        )
        current_question_ids = {
            int(item["bank_question_id"])
            for item in context["items"]
            if item["bank_question_id"] is not None
        }
        next_question_ids = {
            int(item["question_id"]) for item in student["items"]
        }
        changes = [
            {
                "type": "mastery",
                "stable_key": item["stable_key"],
                "mastery_delta": item["mastery_delta"],
                "reason": item["reason"],
            }
            for item in mastery_changes
        ]
        changes.extend(
            [
                {
                    "type": "question_removed",
                    "question_id": value,
                    "reason": "本轮已使用，下一轮不重复推荐。",
                }
                for value in sorted(
                    current_question_ids - next_question_ids
                )
            ]
        )
        changes.extend(
            [
                {
                    "type": "question_added",
                    "question_id": value,
                    "reason": "依据刷新后的掌握口径和候选约束加入。",
                }
                for value in sorted(
                    next_question_ids - current_question_ids
                )
            ]
        )
        return {
            "status": "draft",
            "draft_id": str(draft["draft_id"]),
            "revision": int(draft["revision"]),
            "result_version": str(draft["result_version"]),
            "message": "已沿用原训练范围和册别限制，为该学生生成个性化补练草稿；教师确认后才能形成正式训练卷。",
            "changes": changes,
            "student": student,
        }

    def _context(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT s.submission_id, s.revision AS submission_revision,
                       s.student_id, r.finished_at,
                       p.paper_instance_id, p.draft_id,
                       p.draft_result_version,
                       p.student_code_snapshot, p.student_name_snapshot,
                       p.class_id_snapshot, d.request_json
                FROM training_submissions s
                JOIN training_assessment_runs r
                  ON r.submission_id = s.submission_id
                 AND r.submission_revision = ?
                JOIN personalized_paper_instances p
                  ON p.paper_instance_id = s.paper_instance_id
                JOIN personalized_recommendation_drafts d
                  ON d.draft_id = p.draft_id
                WHERE s.submission_id = ?
                """,
                (submission_revision, submission_id),
            ).fetchone()
            item_rows = connection.execute(
                """
                SELECT i.task_item_code, i.item_order,
                       i.bank_question_id, i.criterion_version_id,
                       i.criterion_hash,
                       i.recommendation_snapshot_json
                FROM training_submissions s
                JOIN personalized_paper_items i
                  ON i.paper_instance_id = s.paper_instance_id
                WHERE s.submission_id = ?
                ORDER BY i.item_order
                """,
                (submission_id,),
            ).fetchall()
        if row is None:
            raise EvidenceSourceInvalid(
                "训练证据来源不存在"
            )
        request = _mapping_json(row["request_json"], "原推荐请求")
        items = []
        for item_row in item_rows:
            recommendation = _mapping_json(
                item_row["recommendation_snapshot_json"],
                "冻结推荐快照",
            )
            items.append(
                {
                    "task_item_code": str(item_row["task_item_code"]),
                    "item_order": int(item_row["item_order"]),
                    "bank_question_id": (
                        None
                        if item_row["bank_question_id"] is None
                        else int(item_row["bank_question_id"])
                    ),
                    "criterion_version_id": str(
                        item_row["criterion_version_id"]
                    ),
                    "criterion_hash": str(item_row["criterion_hash"]),
                    "matched_key": recommendation.get("matched_key"),
                    "matched_name": recommendation.get(
                        "matched_name"
                    )
                    or recommendation.get("matched_key"),
                    "difficulty": recommendation.get("difficulty"),
                    "estimated_minutes": recommendation.get(
                        "estimated_minutes"
                    ),
                    "source_paper": recommendation.get("source_paper")
                    or "",
                }
            )
        return {
            "submission_id": submission_id,
            "submission_revision": submission_revision,
            "student_id": str(row["student_id"]),
            "student_code": str(row["student_code_snapshot"] or ""),
            "student_name": str(row["student_name_snapshot"] or ""),
            "class_id": str(row["class_id_snapshot"] or ""),
            "assessment_finished_at": row["finished_at"],
            "paper_instance_id": str(row["paper_instance_id"]),
            "draft_id": str(row["draft_id"]),
            "draft_result_version": str(row["draft_result_version"]),
            "diagnosis": request.get("diagnosis"),
            "recommendation_config": request.get("config"),
            "items": items,
        }

    def _event(self, operation_token: str) -> Mapping[str, Any] | None:
        with connect(self.db_path) as connection:
            return connection.execute(
                """
                SELECT * FROM training_evidence_sync_events
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()

    def _evidence_version(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> str:
        with connect(self.db_path) as connection:
            records = [
                {
                    "evidence_id": str(row["evidence_id"]),
                    "status": str(row["status"]),
                    "payload_hash": str(row["payload_hash"]),
                    "source_review_revision": int(
                        row["source_review_revision"]
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT evidence_id, status, payload_hash,
                           source_review_revision
                    FROM training_evidence_records
                    WHERE submission_id = ? AND submission_revision = ?
                    ORDER BY evidence_id
                    """,
                    (submission_id, submission_revision),
                ).fetchall()
            ]
            outbox = [
                {
                    "outbox_id": str(row["outbox_id"]),
                    "status": str(row["status"]),
                    "payload_hash": str(row["payload_hash"]),
                }
                for row in connection.execute(
                    """
                    SELECT outbox_id, status, payload_hash
                    FROM training_evidence_outbox
                    WHERE submission_id = ? AND submission_revision = ?
                    ORDER BY outbox_id
                    """,
                    (submission_id, submission_revision),
                ).fetchall()
            ]
        return stable_hash(
            {
                "schema_version": "training-evidence-set-v1",
                "records": records,
                "outbox": outbox,
            }
        )

    def _latest_actor(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> str:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT actor_ref
                FROM training_evidence_sync_events
                WHERE submission_id = ? AND submission_revision = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (submission_id, submission_revision),
            ).fetchone()
        return "system-replay" if row is None else str(row["actor_ref"])

    def _require_outcome(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> TrainingPaperOutcome:
        outcome = self.outcome_loader(
            submission_id, submission_revision
        )
        if outcome is None:
            raise EvidenceSourceInvalid("训练判定不存在")
        return outcome

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("evidence clock must include a timezone")
        return value


def _record_values(
    payload: Mapping[str, Any],
    status: str,
) -> tuple[Any, ...]:
    return (
        payload["evidence_id"],
        payload["submission_id"],
        payload["submission_revision"],
        payload["task_item_code"],
        payload["source_review_revision"],
        status,
        payload["student_id"],
        payload["stable_key"],
        payload["occurred_at"],
        payload["achieved_points"],
        payload["total_points"],
        payload["coverage_ratio"],
        payload["difficulty_weight"],
        payload["evidence_weight"],
        payload["expected_minutes"],
        payload["criterion_version_id"],
        payload["criterion_hash"],
        payload["payload_hash"],
        _json(payload["final_points"]),
        _json(payload["teacher_corrections"]),
        _json(payload["source"]),
    )


def _evidence_id(
    submission_id: str,
    submission_revision: int,
    task_item_code: str,
    stable_key: str,
) -> str:
    return stable_hash(
        {
            "kind": "training-evidence-question-v1",
            "submission_id": submission_id,
            "submission_revision": submission_revision,
            "task_item_code": task_item_code,
            "stable_key": stable_key,
        }
    )


def _stable_key(value: object) -> str:
    key = str(value or "").strip().casefold()
    if not (
        (key.startswith("kp_") and len(key) > 3)
        or (key.startswith("sk_") and len(key) > 3)
        or (
            key.startswith("ki_")
            and len(key) == 35
            and all(char in "0123456789abcdef" for char in key[3:])
        )
    ):
        raise EvidenceSourceInvalid(
            "冻结推荐缺少受治理的稳定知识身份"
        )
    return key


def _difficulty_weight(value: object) -> float:
    try:
        difficulty = float(value)
    except (TypeError, ValueError):
        raise EvidenceSourceInvalid(
            "冻结推荐缺少有效难度"
        ) from None
    if not 1.0 <= difficulty <= 10.0:
        raise EvidenceSourceInvalid(
            "冻结推荐难度超出范围"
        )
    return round(difficulty / 5.0, 6)


def _optional_minutes(value: object) -> int | None:
    # Current recommendations have no time estimate; legacy snapshots may.
    if value is None:
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise EvidenceSourceInvalid(
            "冻结推荐缺少预计时间"
        ) from None
    if minutes < 1 or minutes > 180:
        raise EvidenceSourceInvalid(
            "冻结推荐预计时间超出范围"
        )
    return minutes


def _evidence_time(value: object, fallback: datetime) -> str:
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback.tzinfo)
    else:
        parsed = fallback
    return parsed.astimezone(UTC).isoformat()


def _mapping_json(value: object, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        raise EvidenceSourceInvalid(f"{label}无法读取") from None
    if not isinstance(payload, Mapping):
        raise EvidenceSourceInvalid(f"{label}不是对象")
    return dict(payload)


def _publication_reason(
    question: Mapping[str, Any],
    *,
    published: bool,
) -> str:
    if published:
        return "已按题发布训练覆盖证据。"
    if question["review_status"] != "completed":
        return (
            "判定点尚未全部复核，不发布、不补零，"
            "也不作为未达成。"
        )
    states = {
        point.get("state") for point in question["review_points"]
    }
    if states.intersection({"uncertain", "unreadable"}):
        return "仍含不确定或无法辨认项，不作为未达成发布。"
    return "该题等待证据投递。"


def _feedback_message(
    status: str,
    *,
    active_count: int,
    total_count: int,
) -> str:
    if status == "publication_pending":
        return "证据投递尚未完成；可安全重放，不会重复记录。"
    if status == "withdrawn":
        return "本次已发布证据已撤回，历史和原因仍保留。"
    if status == "complete":
        return f"本次 {total_count} 道题均已形成逐题训练证据。"
    return (
        f"已发布 {active_count}/{total_count} 道题；其余题保留真实待复核状态。"
    )


def _current_mastery_reason(
    *,
    delta: float | None,
    current_evidence_count: int,
) -> str:
    if current_evidence_count == 0:
        return "本次没有可计入当前知识标准的确定训练证据。"
    if delta is None:
        return "本次逐题训练证据使当前掌握度从缺失状态获得结果。"
    direction = "提高" if delta > 0 else "降低" if delta < 0 else "保持"
    return (
        f"当前掌握度按每题覆盖比例和题目权重重算，结果{direction}；"
        "未跨题累加判定点。"
    )


def _missing_current_mastery() -> dict[str, object]:
    return {
        "status": "missing",
        "value": None,
        "evidence_count": 0,
        "parameter_version": None,
        "reason": "current_mastery_evidence_missing",
    }


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
