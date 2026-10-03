from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.training_assessment.adapters import AssessmentContextExceeded
from backend.training_assessment.contracts import (
    POINT_STATES,
    AssessmentActionCommand,
    AssessmentGatewayResponse,
    AssessmentItem,
    AssessmentPage,
    AssessmentUsage,
    EvidenceSyncCommand,
    ModelPointResult,
    ReviewPointCommand,
    TrainingAssessmentGateway,
    TrainingAssessmentRequest,
    TrainingEvidenceSink,
    TrainingPaperOutcome,
    stable_hash,
)
from question_bank.database.schema import connect, initialize_database

MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_SUBMISSION_BYTES = 80 * 1024 * 1024


class TrainingAssessmentError(RuntimeError):
    pass


class SubmissionAssessmentNotFound(TrainingAssessmentError):
    pass


class AssessmentRevisionConflict(TrainingAssessmentError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__(
            "assessment revision conflict: "
            f"expected {expected_revision}, current {current_revision}"
        )


class AssessmentInputInvalid(TrainingAssessmentError):
    pass


class AssessmentReviewConflict(TrainingAssessmentError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__(
            "assessment review conflict: "
            f"expected {expected_revision}, current {current_revision}"
        )


class AssessmentOperationConflict(TrainingAssessmentError):
    pass


class TrainingAssessmentModule:
    """Deep module for one-request, non-score training assessment."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        gateway: TrainingAssessmentGateway,
        evidence_sink: TrainingEvidenceSink | None = None,
        clock: Any | None = None,
        semester_mastery: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.gateway = gateway
        self.evidence_sink = evidence_sink
        self.semester_mastery = semester_mastery
        self.clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))
        self.artifact_root = (
            self.data_root / "question_bank" / "training_submissions"
        )

    def assess(
        self,
        submission_id: str,
        expected_revision: int,
    ) -> TrainingPaperOutcome:
        clean_id = _identifier(submission_id)
        clean_revision = int(expected_revision)
        if clean_revision < 1:
            raise ValueError("expected_revision must be positive")
        initialize_database(self.db_path)
        existing = self._outcome_by_submission(clean_id, clean_revision)
        if existing is not None and existing.status != "running":
            return existing
        lock = _submission_lock(clean_id)
        with lock:
            existing = self._outcome_by_submission(
                clean_id,
                clean_revision,
            )
            if existing is not None:
                return existing
            request = self._load_request(clean_id, clean_revision)
            run_id = stable_hash(
                {
                    "kind": "training-assessment-v1",
                    "submission_id": clean_id,
                    "submission_revision": clean_revision,
                }
            )
            reserved = self._reserve_run(run_id, request)
            if not reserved:
                repeated = self._outcome_by_submission(
                    clean_id,
                    clean_revision,
                )
                if repeated is None:
                    raise TrainingAssessmentError(
                        "assessment run reservation did not converge"
                    )
                return repeated
            return self._execute_attempt(run_id, run_id, request)

    def _execute_attempt(
        self,
        run_id: str,
        attempt_id: str,
        request: TrainingAssessmentRequest,
    ) -> TrainingPaperOutcome:
        request_id = stable_hash(
            {"run_id": run_id, "attempt_id": attempt_id}
        )
        try:
            self._record_request_started(
                run_id,
                request.submission_id,
                attempt_id,
            )
        except TrainingAssessmentError:
            outcome = self._require_outcome(run_id)
            if (
                outcome.status != "running"
                or outcome.control_state != "active"
            ):
                return outcome
            raise
        try:
            response = self.gateway.assess(
                request,
                operation_id=run_id,
                request_id=request_id,
            )
        except (asyncio.CancelledError, FutureCancelledError):
            self._finish_failure(
                run_id, "cancelled", "cancelled", attempt_id
            )
            return self._require_outcome(run_id)
        except AssessmentContextExceeded:
            self._finish_failure(
                run_id, "failed", "context_limit", attempt_id
            )
            return self._require_outcome(run_id)
        except TimeoutError:
            self._finish_failure(run_id, "failed", "timeout", attempt_id)
            return self._require_outcome(run_id)
        except (json.JSONDecodeError, TypeError, ValueError):
            self._finish_failure(
                run_id, "failed", "invalid_response", attempt_id
            )
            return self._require_outcome(run_id)
        except Exception:
            self._finish_failure(
                run_id, "failed", "model_failure", attempt_id
            )
            return self._require_outcome(run_id)
        try:
            stored = self._store_candidates(
                run_id,
                attempt_id,
                request,
                response,
            )
            if not stored:
                return self._require_outcome(run_id)
        except Exception:
            self._finish_failure(
                run_id, "failed", "storage_failure", attempt_id
            )
        return self._require_outcome(run_id)

    def get_outcome(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> TrainingPaperOutcome | None:
        initialize_database(self.db_path)
        return self._outcome_by_submission(
            _identifier(submission_id),
            _positive_revision(submission_revision),
        )

    def pending_summary(self) -> dict[str, Any]:
        """Read pending scans, reviews and publication without initializing or writing data."""
        groups: dict[str, dict[str, Any]] = {}
        with closing(sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN DEFERRED")
            drafts = connection.execute("""
                SELECT DISTINCT d.draft_id, d.created_at
                FROM personalized_recommendation_drafts d
                JOIN personalized_paper_instances p ON p.draft_id = d.draft_id
                JOIN training_submissions s ON s.paper_instance_id = p.paper_instance_id
                JOIN training_scan_batches b ON b.batch_id = s.batch_id
                WHERE b.status <> 'cancelled'
                ORDER BY d.created_at DESC, d.draft_id
            """).fetchall()
            for draft in drafts:
                draft_id = str(draft["draft_id"])
                groups[draft_id] = dict(draft_id=draft_id,
                    draft_name=f"{str(draft['created_at'])[:19].replace('T', ' ')} 训练卷",
                    scan_page_count=0, review_submission_count=0, publish_submission_count=0)
            for row in connection.execute("""
                SELECT p.draft_id, COUNT(*) AS total
                FROM training_submission_pages page
                JOIN training_scan_batches b ON b.batch_id = page.batch_id
                JOIN (SELECT DISTINCT s.batch_id, p.draft_id FROM training_submissions s
                      JOIN personalized_paper_instances p ON p.paper_instance_id = s.paper_instance_id) p
                  ON p.batch_id = b.batch_id
                LEFT JOIN training_submissions s ON s.submission_id = page.submission_id
                WHERE b.status <> 'cancelled' AND COALESCE(s.status, '') <> 'cancelled'
                  AND page.issue_code IS NOT NULL AND page.issue_code <> ''
                  AND page.state NOT IN ('replaced', 'dismissed')
                GROUP BY p.draft_id
            """):
                groups[str(row["draft_id"])]["scan_page_count"] = int(row["total"])
            runs = connection.execute("""
                SELECT p.draft_id, r.run_id, f.feedback_json
                FROM training_submissions s
                JOIN personalized_paper_instances p ON p.paper_instance_id = s.paper_instance_id
                JOIN training_scan_batches b ON b.batch_id = s.batch_id
                JOIN training_assessment_runs r
                  ON r.submission_id = s.submission_id AND r.submission_revision = s.revision
                LEFT JOIN training_feedback_snapshots f
                  ON f.submission_id = s.submission_id AND f.submission_revision = s.revision
                WHERE s.status = 'ready' AND b.status <> 'cancelled' AND r.status <> 'running'
            """).fetchall()
            for row in runs:
                group = groups.get(str(row["draft_id"]))
                outcome = self._outcome(str(row["run_id"]), connection=connection)
                if group is None or outcome is None or not outcome.questions:
                    continue
                if any(question["review_status"] != "completed" for question in outcome.questions):
                    group["review_submission_count"] += 1
                else:
                    feedback = json.loads(row["feedback_json"]) if row["feedback_json"] else {}
                    if not (feedback.get("status") == "complete"
                            and feedback.get("source_review_revision") == outcome.review_revision):
                        group["publish_submission_count"] += 1
        return {"items": [group for group in groups.values() if any(group[key] for key in
            ("scan_page_count", "review_submission_count", "publish_submission_count"))]}

    def sync_evidence(
        self,
        submission_id: str,
        submission_revision: int,
        command: EvidenceSyncCommand,
    ) -> dict[str, Any]:
        from backend.training_assessment.evidence import (
            TrainingEvidencePublisher,
        )

        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        initialize_database(self.db_path)
        return TrainingEvidencePublisher(
            db_path=self.db_path,
            data_root=self.data_root,
            outcome_loader=self.get_outcome,
            semester_mastery=self.semester_mastery,
            sink=self.evidence_sink,
            clock=lambda: _aware_clock(self.clock()),
        ).sync(clean_id, clean_revision, command)

    def replay_evidence_outbox(
        self,
        max_items: int | None = None,
    ) -> dict[str, Any]:
        from backend.training_assessment.evidence import (
            TrainingEvidencePublisher,
        )

        initialize_database(self.db_path)
        return TrainingEvidencePublisher(
            db_path=self.db_path,
            data_root=self.data_root,
            outcome_loader=self.get_outcome,
            semester_mastery=self.semester_mastery,
            sink=self.evidence_sink,
            clock=lambda: _aware_clock(self.clock()),
        ).replay(max_items)

    def get_feedback(
        self,
        submission_id: str,
        submission_revision: int,
    ) -> dict[str, Any] | None:
        from backend.training_assessment.evidence import (
            TrainingEvidencePublisher,
        )

        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        initialize_database(self.db_path)
        return TrainingEvidencePublisher(
            db_path=self.db_path,
            data_root=self.data_root,
            outcome_loader=self.get_outcome,
            semester_mastery=self.semester_mastery,
            sink=self.evidence_sink,
            clock=lambda: _aware_clock(self.clock()),
        ).get_feedback(clean_id, clean_revision)

    def review_point(
        self,
        submission_id: str,
        submission_revision: int,
        command: ReviewPointCommand,
    ) -> TrainingPaperOutcome:
        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        initialize_database(self.db_path)
        fingerprint = stable_hash(
            {
                "kind": "training-point-review-v1",
                "submission_id": clean_id,
                "submission_revision": clean_revision,
                "command": {
                    "expected_review_revision": (
                        command.expected_review_revision
                    ),
                    "task_item_code": command.task_item_code,
                    "point_id": command.point_id,
                    "final_state": command.final_state,
                    "teacher_evidence": command.teacher_evidence,
                    "teacher_reason": command.teacher_reason,
                    "actor_ref": command.actor_ref,
                },
            }
        )
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_event(
                connection,
                command.operation_token,
                fingerprint,
            )
            if repeated is not None:
                connection.commit()
                return self._require_outcome(repeated)
            self._reject_attempt_token(
                connection,
                command.operation_token,
            )
            run = self._reviewable_run(
                connection,
                clean_id,
                clean_revision,
            )
            current_review_revision = int(run["review_revision"])
            if current_review_revision != command.expected_review_revision:
                raise AssessmentReviewConflict(
                    command.expected_review_revision,
                    current_review_revision,
                )
            expected_point = self._expected_point(
                connection,
                clean_id,
                clean_revision,
                command.task_item_code,
                command.point_id,
            )
            next_revision = current_review_revision + 1
            lock_id = stable_hash(
                {
                    "submission_id": clean_id,
                    "submission_revision": clean_revision,
                    "task_item_code": command.task_item_code,
                    "point_id": command.point_id,
                }
            )
            connection.execute(
                """
                INSERT INTO training_point_locks (
                    lock_id, run_id, submission_id, submission_revision,
                    task_item_code, point_id, final_state,
                    teacher_evidence, teacher_reason, actor_ref,
                    lock_revision, expected_point_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    submission_id, submission_revision,
                    task_item_code, point_id
                ) DO UPDATE SET
                    final_state = excluded.final_state,
                    teacher_evidence = excluded.teacher_evidence,
                    teacher_reason = excluded.teacher_reason,
                    actor_ref = excluded.actor_ref,
                    lock_revision = excluded.lock_revision,
                    expected_point_json = excluded.expected_point_json,
                    updated_at = excluded.updated_at
                """,
                (
                    lock_id,
                    str(run["run_id"]),
                    clean_id,
                    clean_revision,
                    command.task_item_code,
                    command.point_id,
                    command.final_state,
                    command.teacher_evidence,
                    command.teacher_reason,
                    command.actor_ref,
                    next_revision,
                    _json(expected_point),
                    now,
                    now,
                ),
            )
            self._advance_review_revision(
                connection,
                str(run["run_id"]),
                current_review_revision,
                next_revision,
                now,
            )
            self._insert_event(
                connection,
                run_id=str(run["run_id"]),
                token=command.operation_token,
                fingerprint=fingerprint,
                event_type="review_point",
                actor_ref=command.actor_ref,
                from_revision=current_review_revision,
                to_revision=next_revision,
                detail={
                    "task_item_code": command.task_item_code,
                    "point_id": command.point_id,
                    "final_state": command.final_state,
                },
                now=now,
            )
            connection.commit()
            run_id = str(run["run_id"])
        return self._require_outcome(run_id)

    def pause(
        self,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        return self._change_control(
            submission_id, submission_revision, "pause", command
        )

    def resume(
        self,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        return self._change_control(
            submission_id, submission_revision, "resume", command
        )

    def cancel(
        self,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        return self._change_control(
            submission_id, submission_revision, "cancel", command
        )

    def recover(
        self,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        initialize_database(self.db_path)
        fingerprint = self._action_fingerprint(
            "recover", clean_id, clean_revision, command
        )
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_event(
                connection, command.operation_token, fingerprint
            )
            if repeated is not None:
                connection.commit()
                return self._require_outcome(repeated)
            self._reject_attempt_token(
                connection, command.operation_token
            )
            run = self._reviewable_run(
                connection, clean_id, clean_revision
            )
            current_review_revision = int(run["review_revision"])
            if current_review_revision != command.expected_review_revision:
                raise AssessmentReviewConflict(
                    command.expected_review_revision,
                    current_review_revision,
                )
            if str(run["status"]) != "running":
                raise AssessmentInputInvalid(
                    "只有仍显示运行中的判定才能执行退出恢复"
                )
            attempt = connection.execute(
                """
                SELECT * FROM training_assessment_attempts
                WHERE run_id = ?
                ORDER BY attempt_number DESC LIMIT 1
                """,
                (run["run_id"],),
            ).fetchone()
            if attempt is None:
                raise TrainingAssessmentError(
                    "assessment attempt ledger is missing"
                )
            error_code = (
                "interrupted_after_request"
                if int(attempt["request_count"]) == 1
                else "interrupted_before_request"
            )
            next_revision = current_review_revision + 1
            connection.execute(
                """
                UPDATE training_assessment_attempts
                SET status = 'failed', error_code = ?, finished_at = ?,
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (error_code, now, now, attempt["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE training_assessment_runs
                SET status = 'failed', control_state = 'active',
                    error_code = ?, finished_at = ?,
                    review_revision = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    error_code,
                    now,
                    next_revision,
                    now,
                    run["run_id"],
                ),
            )
            self._insert_event(
                connection,
                run_id=str(run["run_id"]),
                token=command.operation_token,
                fingerprint=fingerprint,
                event_type="recover",
                actor_ref=command.actor_ref,
                from_revision=current_review_revision,
                to_revision=next_revision,
                detail={"error_code": error_code, "reason": command.reason},
                now=now,
            )
            connection.commit()
            run_id = str(run["run_id"])
        return self._require_outcome(run_id)

    def retry_failed(
        self,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        initialize_database(self.db_path)
        fingerprint = self._action_fingerprint(
            "retry", clean_id, clean_revision, command
        )
        with connect(self.db_path) as connection:
            existing_attempt = connection.execute(
                """
                SELECT run_id, request_fingerprint
                FROM training_assessment_attempts
                WHERE operation_token = ?
                """,
                (command.operation_token,),
            ).fetchone()
        if existing_attempt is not None:
            if str(existing_attempt["request_fingerprint"]) != fingerprint:
                raise AssessmentOperationConflict(
                    "操作标识已被另一项判定操作使用"
                )
            return self._require_outcome(str(existing_attempt["run_id"]))
        request = self._load_request(clean_id, clean_revision)
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_attempt = connection.execute(
                """
                SELECT run_id, request_fingerprint
                FROM training_assessment_attempts
                WHERE operation_token = ?
                """,
                (command.operation_token,),
            ).fetchone()
            if existing_attempt is not None:
                if str(existing_attempt["request_fingerprint"]) != fingerprint:
                    raise AssessmentOperationConflict(
                        "操作标识已被另一项判定操作使用"
                    )
                connection.commit()
                return self._require_outcome(
                    str(existing_attempt["run_id"])
                )
            self._reject_event_token(
                connection, command.operation_token
            )
            run = self._reviewable_run(
                connection, clean_id, clean_revision
            )
            current_review_revision = int(run["review_revision"])
            if current_review_revision != command.expected_review_revision:
                raise AssessmentReviewConflict(
                    command.expected_review_revision,
                    current_review_revision,
                )
            if str(run["status"]) not in {"failed", "cancelled"}:
                raise AssessmentInputInvalid(
                    "只有失败或已取消且没有候选结果的判定才能重试"
                )
            result_count = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM training_question_results WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            if int(result_count["total"]) > 0:
                raise AssessmentInputInvalid(
                    "已有候选结果，请直接本地复核，不能重新调用模型"
                )
            attempt_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_number
                    FROM training_assessment_attempts WHERE run_id = ?
                    """,
                    (run["run_id"],),
                ).fetchone()["next_number"]
            )
            attempt_id = stable_hash(
                {
                    "run_id": str(run["run_id"]),
                    "operation_token": command.operation_token,
                }
            )
            connection.execute(
                """
                INSERT INTO training_assessment_attempts (
                    attempt_id, run_id, attempt_number, operation_token,
                    request_fingerprint, status, request_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (
                    attempt_id,
                    run["run_id"],
                    attempt_number,
                    command.operation_token,
                    fingerprint,
                    now,
                    now,
                ),
            )
            next_revision = current_review_revision + 1
            connection.execute(
                """
                UPDATE training_assessment_runs
                SET status = 'running', control_state = 'active',
                    error_code = NULL, finished_at = NULL,
                    review_revision = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (next_revision, now, run["run_id"]),
            )
            connection.commit()
            run_id = str(run["run_id"])
        return self._execute_attempt(run_id, attempt_id, request)

    def _change_control(
        self,
        submission_id: str,
        submission_revision: int,
        action: str,
        command: AssessmentActionCommand,
    ) -> TrainingPaperOutcome:
        clean_id = _identifier(submission_id)
        clean_revision = _positive_revision(submission_revision)
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("unsupported assessment control action")
        initialize_database(self.db_path)
        fingerprint = self._action_fingerprint(
            action, clean_id, clean_revision, command
        )
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_event(
                connection, command.operation_token, fingerprint
            )
            if repeated is not None:
                connection.commit()
                return self._require_outcome(repeated)
            self._reject_attempt_token(
                connection, command.operation_token
            )
            run = self._reviewable_run(
                connection, clean_id, clean_revision
            )
            current_review_revision = int(run["review_revision"])
            if current_review_revision != command.expected_review_revision:
                raise AssessmentReviewConflict(
                    command.expected_review_revision,
                    current_review_revision,
                )
            current_status = str(run["status"])
            current_control = str(run["control_state"])
            if action == "pause":
                if current_status != "running" or current_control != "active":
                    raise AssessmentInputInvalid(
                        "只有正在运行的判定可以暂停"
                    )
                latest_attempt = connection.execute(
                    """
                    SELECT request_count
                    FROM training_assessment_attempts
                    WHERE run_id = ?
                    ORDER BY attempt_number DESC LIMIT 1
                    """,
                    (run["run_id"],),
                ).fetchone()
                if (
                    latest_attempt is None
                    or int(latest_attempt["request_count"]) != 1
                ):
                    raise AssessmentInputInvalid(
                        "请求尚未发出，无需暂停"
                    )
                target_control = "paused"
                target_status = current_status
                error_code = run["error_code"]
                finished_at = run["finished_at"]
            elif action == "resume":
                if current_status != "running" or current_control != "paused":
                    raise AssessmentInputInvalid(
                        "只有已暂停且仍运行中的判定可以恢复"
                    )
                target_control = "active"
                target_status = current_status
                error_code = run["error_code"]
                finished_at = run["finished_at"]
            else:
                if current_status != "running":
                    raise AssessmentInputInvalid(
                        "只有正在运行的判定可以取消"
                    )
                target_control = "cancelled"
                target_status = "cancelled"
                error_code = "cancelled_by_teacher"
                finished_at = now
            next_revision = current_review_revision + 1
            connection.execute(
                """
                UPDATE training_assessment_runs
                SET status = ?, control_state = ?, error_code = ?,
                    finished_at = ?, review_revision = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    target_status,
                    target_control,
                    error_code,
                    finished_at,
                    next_revision,
                    now,
                    run["run_id"],
                ),
            )
            if action == "cancel":
                connection.execute(
                    """
                    UPDATE training_assessment_attempts
                    SET status = 'cancelled', error_code = ?,
                        finished_at = ?, updated_at = ?
                    WHERE run_id = ? AND status IN ('pending', 'running')
                    """,
                    (error_code, now, now, run["run_id"]),
                )
            self._insert_event(
                connection,
                run_id=str(run["run_id"]),
                token=command.operation_token,
                fingerprint=fingerprint,
                event_type=action,
                actor_ref=command.actor_ref,
                from_revision=current_review_revision,
                to_revision=next_revision,
                detail={"reason": command.reason},
                now=now,
            )
            connection.commit()
            run_id = str(run["run_id"])
        return self._require_outcome(run_id)

    def _load_request(
        self,
        submission_id: str,
        expected_revision: int,
    ) -> TrainingAssessmentRequest:
        with connect(self.db_path) as connection:
            submission = connection.execute(
                """
                SELECT s.*, p.status AS paper_status,
                       p.paper_instance_id AS frozen_paper_instance_id
                FROM training_submissions s
                JOIN personalized_paper_instances p
                  ON p.paper_instance_id = s.paper_instance_id
                WHERE s.submission_id = ?
                """,
                (submission_id,),
            ).fetchone()
            if submission is None:
                raise SubmissionAssessmentNotFound(submission_id)
            current_revision = int(submission["revision"])
            if current_revision != expected_revision:
                raise AssessmentRevisionConflict(
                    expected_revision,
                    current_revision,
                )
            if str(submission["status"]) != "ready":
                raise AssessmentInputInvalid(
                    "submission is not ready for assessment"
                )
            if str(submission["paper_status"]) != "frozen":
                raise AssessmentInputInvalid(
                    "personalized paper is not frozen"
                )
            page_rows = connection.execute(
                """
                SELECT scan_page_id, claimed_page_number, image_sha256,
                       image_path, state, issue_code
                FROM training_submission_pages
                WHERE submission_id = ?
                ORDER BY claimed_page_number
                """,
                (submission_id,),
            ).fetchall()
            item_rows = connection.execute(
                """
                SELECT item_order, task_item_code, criterion_version_id,
                       criterion_hash, question_snapshot_json,
                       criterion_snapshot_json
                FROM personalized_paper_items
                WHERE paper_instance_id = ?
                ORDER BY item_order
                """,
                (submission["paper_instance_id"],),
            ).fetchall()
        expected_pages = int(submission["expected_total_pages"])
        if len(page_rows) != expected_pages:
            raise AssessmentInputInvalid("submission page set is incomplete")
        page_numbers = [int(row["claimed_page_number"] or 0) for row in page_rows]
        if page_numbers != list(range(1, expected_pages + 1)):
            raise AssessmentInputInvalid("submission pages are not contiguous")
        if any(
            str(row["state"]) != "assigned" or row["issue_code"] is not None
            for row in page_rows
        ):
            raise AssessmentInputInvalid(
                "submission contains unresolved page issues"
            )
        pages = self._load_pages(page_rows)
        items = tuple(self._load_item(row) for row in item_rows)
        if not items:
            raise AssessmentInputInvalid(
                "personalized paper has no frozen items"
            )
        if sum(len(item.points) for item in items) < 1:
            raise AssessmentInputInvalid(
                "personalized paper has no frozen criterion points"
            )
        return TrainingAssessmentRequest(
            submission_id=submission_id,
            submission_revision=expected_revision,
            paper_instance_id=str(submission["paper_instance_id"]),
            items=items,
            pages=pages,
        )

    def _load_pages(self, rows: Sequence[Mapping[str, Any]]) -> tuple[AssessmentPage, ...]:
        root = self.artifact_root.resolve()
        pages: list[AssessmentPage] = []
        total_bytes = 0
        for row in rows:
            relative = Path(str(row["image_path"] or ""))
            if relative.is_absolute():
                raise AssessmentInputInvalid(
                    "submission page path is not controlled"
                )
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                raise AssessmentInputInvalid(
                    "submission page path escapes the controlled directory"
                ) from None
            if not path.is_file():
                raise AssessmentInputInvalid("submission page file is missing")
            content = path.read_bytes()
            if not content or len(content) > MAX_PAGE_BYTES:
                raise AssessmentInputInvalid(
                    "submission page file size is invalid"
                )
            total_bytes += len(content)
            if total_bytes > MAX_SUBMISSION_BYTES:
                raise AssessmentInputInvalid(
                    "submission page payload exceeds the assessment limit"
                )
            mime_type = _image_type(content)
            try:
                pages.append(
                    AssessmentPage(
                        page_number=int(row["claimed_page_number"]),
                        sha256=str(row["image_sha256"]),
                        mime_type=mime_type,
                        content=content,
                    )
                )
            except ValueError as exc:
                raise AssessmentInputInvalid(str(exc)) from None
        return tuple(pages)

    @staticmethod
    def _load_item(row: Mapping[str, Any]) -> AssessmentItem:
        try:
            question_snapshot = json.loads(
                str(row["question_snapshot_json"])
            )
            criterion_snapshot = json.loads(
                str(row["criterion_snapshot_json"])
            )
        except (TypeError, json.JSONDecodeError):
            raise AssessmentInputInvalid(
                "frozen item snapshot is invalid"
            ) from None
        if not isinstance(question_snapshot, Mapping) or not isinstance(
            criterion_snapshot,
            Mapping,
        ):
            raise AssessmentInputInvalid(
                "frozen item snapshot must be an object"
            )
        criteria = criterion_snapshot.get("criteria")
        if not isinstance(criteria, Mapping):
            raise AssessmentInputInvalid(
                "frozen criterion payload is missing"
            )
        _reject_nested_score_fields(criteria)
        raw_points = criteria.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise AssessmentInputInvalid(
                "frozen criterion points are missing"
            )
        points: list[Mapping[str, Any]] = []
        point_ids: set[str] = set()
        for raw in raw_points:
            if not isinstance(raw, Mapping):
                raise AssessmentInputInvalid(
                    "frozen criterion point is invalid"
                )
            point_id = str(raw.get("point_id") or "").strip()
            if not point_id or point_id in point_ids:
                raise AssessmentInputInvalid(
                    "frozen criterion point identity is invalid"
                )
            point_ids.add(point_id)
            points.append(_safe_model_mapping(raw))
        version_id = str(row["criterion_version_id"])
        criterion_hash = str(row["criterion_hash"])
        if criterion_snapshot.get("version_id") not in {None, version_id}:
            raise AssessmentInputInvalid(
                "frozen criterion version identity changed"
            )
        if criterion_snapshot.get("criteria_hash") not in {
            None,
            criterion_hash,
        }:
            raise AssessmentInputInvalid(
                "frozen criterion hash changed"
            )
        context = question_snapshot.get("tagging_context")
        clean_context = (
            _safe_model_mapping(context)
            if isinstance(context, Mapping)
            else {}
        )
        question: dict[str, Any] = {}
        answer: dict[str, Any] = {}
        for key, value in clean_context.items():
            lowered = key.casefold()
            if any(
                marker in lowered
                for marker in ("answer", "solution", "reference")
            ):
                answer[key] = value
            else:
                question[key] = value
        auxiliary = criteria.get("auxiliary_rules")
        auxiliary_rules = (
            tuple(
                str(item).strip()
                for item in auxiliary
                if str(item).strip()
            )
            if isinstance(auxiliary, list)
            else ()
        )
        return AssessmentItem(
            item_order=int(row["item_order"]),
            task_item_code=str(row["task_item_code"]),
            criterion_version_id=version_id,
            criterion_hash=criterion_hash,
            question=question,
            answer=answer,
            points=tuple(points),
            auxiliary_rules=auxiliary_rules,
        )

    def _reserve_run(
        self,
        run_id: str,
        request: TrainingAssessmentRequest,
    ) -> bool:
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT status, revision
                FROM training_submissions
                WHERE submission_id = ?
                """,
                (request.submission_id,),
            ).fetchone()
            if current is None:
                raise SubmissionAssessmentNotFound(request.submission_id)
            revision = int(current["revision"])
            if revision != request.submission_revision:
                raise AssessmentRevisionConflict(
                    request.submission_revision,
                    revision,
                )
            if str(current["status"]) != "ready":
                raise AssessmentInputInvalid(
                    "submission changed before assessment"
                )
            existing = connection.execute(
                """
                SELECT run_id FROM training_assessment_runs
                WHERE submission_id = ? AND submission_revision = ?
                """,
                (request.submission_id, request.submission_revision),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return False
            connection.execute(
                """
                INSERT INTO training_assessment_runs (
                    run_id, submission_id, submission_revision, status,
                    request_count, expected_question_count,
                    expected_point_count, issue_codes_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'running', 0, ?, ?, '[]', ?, ?)
                """,
                (
                    run_id,
                    request.submission_id,
                    request.submission_revision,
                    len(request.items),
                    request.expected_point_count,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO training_assessment_attempts (
                    attempt_id, run_id, attempt_number, operation_token,
                    request_fingerprint, status, request_count,
                    created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, 'pending', 0, ?, ?)
                """,
                (
                    run_id,
                    run_id,
                    run_id[:32],
                    run_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE training_submissions
                SET assessment_started_at = COALESCE(
                        assessment_started_at, ?
                    ),
                    updated_at = ?
                WHERE submission_id = ?
                """,
                (now, now, request.submission_id),
            )
            connection.commit()
        return True

    def _record_request_started(
        self,
        run_id: str,
        submission_id: str,
        attempt_id: str,
    ) -> None:
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE training_assessment_attempts
                SET request_count = 1, status = 'running',
                    started_at = ?, updated_at = ?
                WHERE attempt_id = ? AND run_id = ?
                  AND status = 'pending' AND request_count = 0
                """,
                (now, now, attempt_id, run_id),
            )
            if cursor.rowcount != 1:
                raise TrainingAssessmentError(
                    "assessment request was already started"
                )
            run_cursor = connection.execute(
                """
                UPDATE training_assessment_runs
                SET request_count = 1,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE run_id = ? AND status = 'running'
                  AND control_state = 'active'
                """,
                (now, now, run_id),
            )
            if run_cursor.rowcount != 1:
                raise TrainingAssessmentError(
                    "assessment run is not active"
                )
            connection.execute(
                """
                UPDATE training_submissions
                SET updated_at = ?
                WHERE submission_id = ?
                """,
                (now, submission_id),
            )
            connection.commit()

    def _store_candidates(
        self,
        run_id: str,
        attempt_id: str,
        request: TrainingAssessmentRequest,
        response: AssessmentGatewayResponse,
    ) -> bool:
        now = self._now()
        expected_by_task = {
            item.task_item_code: item for item in request.items
        }
        by_task: dict[str, list[ModelPointResult]] = defaultdict(list)
        global_issues: set[str] = set()
        for result in response.results:
            if result.task_item_code not in expected_by_task:
                global_issues.add("unknown_task_item")
                continue
            by_task[result.task_item_code].append(result)
        prepared: list[dict[str, Any]] = []
        for item in request.items:
            results = by_task[item.task_item_code]
            expected_ids = set(item.point_ids)
            result_counts = Counter(result.point_id for result in results)
            returned_ids = set(result_counts)
            issue_codes: set[str] = set()
            if expected_ids - returned_ids:
                issue_codes.add("missing_point")
            if any(count > 1 for count in result_counts.values()):
                issue_codes.add("duplicate_point")
            if returned_ids - expected_ids:
                issue_codes.add("unknown_point")
            for result in results:
                if result.state not in POINT_STATES:
                    issue_codes.add("invalid_state")
                if not result.evidence or len(result.evidence) > 500:
                    issue_codes.add("invalid_evidence")
            clean_results = (
                tuple(results) if not issue_codes else ()
            )
            state_counts = Counter(
                result.state for result in clean_results
            )
            prepared.append(
                {
                    "item": item,
                    "status": (
                        "candidate" if not issue_codes else "manual_review"
                    ),
                    "issues": tuple(sorted(issue_codes)),
                    "results": clean_results,
                    "counts": state_counts,
                }
            )
        run_status = (
            "succeeded"
            if not global_issues
            and all(item["status"] == "candidate" for item in prepared)
            else "manual_review"
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status, request_count, control_state
                FROM training_assessment_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if (
                run is None
                or str(run["status"]) != "running"
                or int(run["request_count"]) != 1
            ):
                connection.commit()
                return False
            attempt = connection.execute(
                """
                SELECT status, request_count
                FROM training_assessment_attempts
                WHERE attempt_id = ? AND run_id = ?
                """,
                (attempt_id, run_id),
            ).fetchone()
            if (
                attempt is None
                or str(attempt["status"]) != "running"
                or int(attempt["request_count"]) != 1
            ):
                connection.commit()
                return False
            connection.execute(
                "DELETE FROM training_point_results WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM training_question_results WHERE run_id = ?",
                (run_id,),
            )
            for prepared_item in prepared:
                item = prepared_item["item"]
                question_result_id = stable_hash(
                    {
                        "run_id": run_id,
                        "task_item_code": item.task_item_code,
                    }
                )
                counts = prepared_item["counts"]
                connection.execute(
                    """
                    INSERT INTO training_question_results (
                        question_result_id, run_id, submission_id,
                        submission_revision, task_item_code, item_order,
                        criterion_version_id, criterion_hash, status,
                        met_count, not_met_count, uncertain_count,
                        unreadable_count, total_count, issue_codes_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?)
                    """,
                    (
                        question_result_id,
                        run_id,
                        request.submission_id,
                        request.submission_revision,
                        item.task_item_code,
                        item.item_order,
                        item.criterion_version_id,
                        item.criterion_hash,
                        prepared_item["status"],
                        int(counts.get("met", 0)),
                        int(counts.get("not_met", 0)),
                        int(counts.get("uncertain", 0)),
                        int(counts.get("unreadable", 0)),
                        len(item.points),
                        _json(prepared_item["issues"]),
                        now,
                        now,
                    ),
                )
                expected_points = {
                    str(point["point_id"]): point for point in item.points
                }
                for result in prepared_item["results"]:
                    point_result_id = stable_hash(
                        {
                            "submission_id": request.submission_id,
                            "submission_revision": request.submission_revision,
                            "task_item_code": item.task_item_code,
                            "point_id": result.point_id,
                        }
                    )
                    connection.execute(
                        """
                        INSERT INTO training_point_results (
                            point_result_id, run_id, question_result_id,
                            submission_id, submission_revision,
                            task_item_code, point_id,
                            criterion_version_id, criterion_hash,
                            expected_point_json, candidate_state,
                            model_evidence, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            point_result_id,
                            run_id,
                            question_result_id,
                            request.submission_id,
                            request.submission_revision,
                            item.task_item_code,
                            result.point_id,
                            item.criterion_version_id,
                            item.criterion_hash,
                            _json(expected_points[result.point_id]),
                            result.state,
                            result.evidence,
                            now,
                            now,
                        ),
                    )
            connection.execute(
                """
                UPDATE training_assessment_runs
                SET status = ?, control_state = 'active', model_name = ?,
                    prompt_tokens = ?, completion_tokens = ?,
                    total_tokens = ?, latency_ms = ?,
                    issue_codes_json = ?, error_code = NULL,
                    finished_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    run_status,
                    response.model_name,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    response.usage.total_tokens,
                    response.latency_ms,
                    _json(sorted(global_issues)),
                    now,
                    now,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE training_assessment_attempts
                SET status = 'succeeded', error_code = NULL,
                    model_name = ?, prompt_tokens = ?,
                    completion_tokens = ?, total_tokens = ?,
                    latency_ms = ?, finished_at = ?, updated_at = ?
                WHERE attempt_id = ? AND status = 'running'
                """,
                (
                    response.model_name,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                    response.usage.total_tokens,
                    response.latency_ms,
                    now,
                    now,
                    attempt_id,
                ),
            )
            connection.commit()
        return True

    def _finish_failure(
        self,
        run_id: str,
        status: str,
        error_code: str,
        attempt_id: str,
    ) -> None:
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM training_point_results WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM training_question_results WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                """
                UPDATE training_assessment_runs
                SET status = ?, error_code = ?, finished_at = ?,
                    updated_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (status, error_code, now, now, run_id),
            )
            connection.execute(
                """
                UPDATE training_assessment_attempts
                SET status = ?, error_code = ?, finished_at = ?,
                    updated_at = ?
                WHERE attempt_id = ?
                  AND status IN ('pending', 'running')
                """,
                (status, error_code, now, now, attempt_id),
            )
            connection.commit()

    def _reviewable_run(
        self,
        connection: Any,
        submission_id: str,
        submission_revision: int,
    ) -> Mapping[str, Any]:
        submission = connection.execute(
            """
            SELECT revision FROM training_submissions
            WHERE submission_id = ?
            """,
            (submission_id,),
        ).fetchone()
        if submission is None:
            raise SubmissionAssessmentNotFound(submission_id)
        current_revision = int(submission["revision"])
        if current_revision != submission_revision:
            raise AssessmentRevisionConflict(
                submission_revision, current_revision
            )
        run = connection.execute(
            """
            SELECT * FROM training_assessment_runs
            WHERE submission_id = ? AND submission_revision = ?
            """,
            (submission_id, submission_revision),
        ).fetchone()
        if run is None:
            raise AssessmentInputInvalid(
                "该答卷版本还没有判定运行"
            )
        return run

    @staticmethod
    def _expected_point(
        connection: Any,
        submission_id: str,
        submission_revision: int,
        task_item_code: str,
        point_id: str,
    ) -> Mapping[str, Any]:
        row = connection.execute(
            """
            SELECT i.criterion_snapshot_json
            FROM training_submissions s
            JOIN personalized_paper_items i
              ON i.paper_instance_id = s.paper_instance_id
            WHERE s.submission_id = ? AND s.revision = ?
              AND i.task_item_code = ?
            """,
            (submission_id, submission_revision, task_item_code),
        ).fetchone()
        if row is None:
            raise AssessmentInputInvalid(
                "题目不属于该答卷的冻结版本"
            )
        try:
            payload = json.loads(str(row["criterion_snapshot_json"]))
            points = payload["criteria"]["points"]
        except (TypeError, KeyError, json.JSONDecodeError):
            raise AssessmentInputInvalid(
                "冻结判定点快照无法读取"
            ) from None
        for point in points:
            if (
                isinstance(point, Mapping)
                and str(point.get("point_id") or "").strip() == point_id
            ):
                return _safe_model_mapping(point)
        raise AssessmentInputInvalid(
            "判定点不属于该答卷的冻结版本"
        )

    @staticmethod
    def _advance_review_revision(
        connection: Any,
        run_id: str,
        current_revision: int,
        next_revision: int,
        now: str,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE training_assessment_runs
            SET review_revision = ?, updated_at = ?
            WHERE run_id = ? AND review_revision = ?
            """,
            (next_revision, now, run_id, current_revision),
        )
        if cursor.rowcount != 1:
            raise AssessmentReviewConflict(
                current_revision, current_revision + 1
            )

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        run_id: str,
        token: str,
        fingerprint: str,
        event_type: str,
        actor_ref: str,
        from_revision: int,
        to_revision: int,
        detail: Mapping[str, Any],
        now: str,
    ) -> None:
        event_id = stable_hash(
            {
                "kind": "training-assessment-event-v1",
                "operation_token": token,
            }
        )
        connection.execute(
            """
            INSERT INTO training_assessment_events (
                event_id, run_id, operation_token, request_fingerprint,
                event_type, actor_ref, from_review_revision,
                to_review_revision, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                token,
                fingerprint,
                event_type,
                actor_ref,
                from_revision,
                to_revision,
                _json(detail),
                now,
            ),
        )

    @staticmethod
    def _repeated_event(
        connection: Any,
        token: str,
        fingerprint: str,
    ) -> str | None:
        event = connection.execute(
            """
            SELECT run_id, request_fingerprint
            FROM training_assessment_events
            WHERE operation_token = ?
            """,
            (token,),
        ).fetchone()
        if event is None:
            return None
        if str(event["request_fingerprint"]) != fingerprint:
            raise AssessmentOperationConflict(
                "操作标识已被另一项判定操作使用"
            )
        return str(event["run_id"])

    @staticmethod
    def _reject_attempt_token(connection: Any, token: str) -> None:
        exists = connection.execute(
            """
            SELECT 1 FROM training_assessment_attempts
            WHERE operation_token = ?
            """,
            (token,),
        ).fetchone()
        if exists is not None:
            raise AssessmentOperationConflict(
                "操作标识已被模型请求使用"
            )

    @staticmethod
    def _reject_event_token(connection: Any, token: str) -> None:
        exists = connection.execute(
            """
            SELECT 1 FROM training_assessment_events
            WHERE operation_token = ?
            """,
            (token,),
        ).fetchone()
        if exists is not None:
            raise AssessmentOperationConflict(
                "操作标识已被另一项判定操作使用"
            )

    @staticmethod
    def _action_fingerprint(
        action: str,
        submission_id: str,
        submission_revision: int,
        command: AssessmentActionCommand,
    ) -> str:
        return stable_hash(
            {
                "kind": f"training-assessment-{action}-v1",
                "submission_id": submission_id,
                "submission_revision": submission_revision,
                "expected_review_revision": (
                    command.expected_review_revision
                ),
                "actor_ref": command.actor_ref,
                "reason": command.reason,
            }
        )

    def _outcome_by_submission(
        self,
        submission_id: str,
        revision: int,
    ) -> TrainingPaperOutcome | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT run_id FROM training_assessment_runs
                WHERE submission_id = ? AND submission_revision = ?
                """,
                (submission_id, revision),
            ).fetchone()
        return (
            None
            if row is None
            else self._outcome(str(row["run_id"]))
        )

    def _require_outcome(self, run_id: str) -> TrainingPaperOutcome:
        outcome = self._outcome(run_id)
        if outcome is None:
            raise TrainingAssessmentError("assessment outcome is missing")
        return outcome

    def _outcome(self, run_id: str, *, connection: Any | None = None) -> TrainingPaperOutcome | None:
        with connect(self.db_path, external_connection=connection) as connection:
            run = connection.execute(
                """
                SELECT * FROM training_assessment_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            questions = connection.execute(
                """
                SELECT * FROM training_question_results
                WHERE run_id = ? ORDER BY item_order
                """,
                (run_id,),
            ).fetchall()
            points = connection.execute(
                """
                SELECT task_item_code, point_id, candidate_state,
                       model_evidence, expected_point_json
                FROM training_point_results
                WHERE run_id = ?
                ORDER BY task_item_code, point_id
                """,
                (run_id,),
            ).fetchall()
            locks = connection.execute(
                """
                SELECT task_item_code, point_id, final_state,
                       teacher_evidence, teacher_reason, actor_ref,
                       lock_revision, expected_point_json
                FROM training_point_locks
                WHERE run_id = ?
                ORDER BY task_item_code, point_id
                """,
                (run_id,),
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT attempt_number, status, request_count, error_code,
                       model_name
                FROM training_assessment_attempts
                WHERE run_id = ? ORDER BY attempt_number
                """,
                (run_id,),
            ).fetchall()
            item_rows = connection.execute(
                """
                SELECT i.item_order, i.task_item_code,
                       i.criterion_snapshot_json
                FROM training_assessment_runs r
                JOIN training_submissions s
                  ON s.submission_id = r.submission_id
                JOIN personalized_paper_items i
                  ON i.paper_instance_id = s.paper_instance_id
                WHERE r.run_id = ?
                ORDER BY i.item_order
                """,
                (run_id,),
            ).fetchall()
        point_payloads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        candidate_by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
        for point in points:
            identity = (
                str(point["task_item_code"]),
                str(point["point_id"]),
            )
            candidate_by_identity[identity] = point
            point_payloads[str(point["task_item_code"])].append(
                {
                    "point_id": str(point["point_id"]),
                    "state": str(point["candidate_state"]),
                    "evidence": str(point["model_evidence"]),
                }
            )
        lock_by_identity = {
            (str(lock["task_item_code"]), str(lock["point_id"])): lock
            for lock in locks
        }
        question_by_task = {
            str(question["task_item_code"]): question
            for question in questions
        }
        question_payloads_list: list[dict[str, Any]] = []
        completed_questions = 0
        for item_row in item_rows:
            task_item_code = str(item_row["task_item_code"])
            expected_points = _criterion_points(
                str(item_row["criterion_snapshot_json"])
            )
            review_points: list[dict[str, Any]] = []
            effective_counts: Counter[str] = Counter()
            needs_review = False
            for expected_point in expected_points:
                point_id = str(expected_point["point_id"])
                identity = (task_item_code, point_id)
                candidate = candidate_by_identity.get(identity)
                lock = lock_by_identity.get(identity)
                candidate_state = (
                    None
                    if candidate is None
                    else str(candidate["candidate_state"])
                )
                teacher_locked = lock is not None
                effective_state = (
                    str(lock["final_state"])
                    if lock is not None
                    else candidate_state
                )
                if effective_state is not None:
                    effective_counts[effective_state] += 1
                if effective_state is None or (
                    not teacher_locked
                    and effective_state in {"uncertain", "unreadable"}
                ):
                    needs_review = True
                review_points.append(
                    {
                        "point_id": point_id,
                        "expected_point": expected_point,
                        "candidate_state": candidate_state,
                        "state": effective_state,
                        "evidence": (
                            str(lock["teacher_evidence"])
                            if lock is not None
                            else (
                                None
                                if candidate is None
                                else str(candidate["model_evidence"])
                            )
                        ),
                        "teacher_locked": teacher_locked,
                        "teacher_reason": (
                            None
                            if lock is None
                            else str(lock["teacher_reason"])
                        ),
                        "actor_ref": (
                            None
                            if lock is None
                            else str(lock["actor_ref"])
                        ),
                        "lock_revision": (
                            None
                            if lock is None
                            else int(lock["lock_revision"])
                        ),
                    }
                )
            stored_question = question_by_task.get(task_item_code)
            run_status = str(run["status"])
            if not needs_review and expected_points:
                review_status = "completed"
                completed_questions += 1
            elif run_status == "failed" and stored_question is None:
                review_status = "failed"
            else:
                review_status = "review_required"
            question_payloads_list.append(
                {
                    "task_item_code": task_item_code,
                    "item_order": int(item_row["item_order"]),
                    "status": (
                        str(stored_question["status"])
                        if stored_question is not None
                        else (
                            "failed"
                            if run_status == "failed"
                            else "pending"
                        )
                    ),
                    "review_status": review_status,
                    "met_count": int(effective_counts.get("met", 0)),
                    "not_met_count": int(
                        effective_counts.get("not_met", 0)
                    ),
                    "uncertain_count": int(
                        effective_counts.get("uncertain", 0)
                    ),
                    "unreadable_count": int(
                        effective_counts.get("unreadable", 0)
                    ),
                    "total_count": len(expected_points),
                    "issue_codes": (
                        ()
                        if stored_question is None
                        else tuple(
                            json.loads(
                                str(stored_question["issue_codes_json"])
                            )
                        )
                    ),
                    "points": tuple(point_payloads[task_item_code]),
                    "review_points": tuple(review_points),
                }
            )
        question_payloads = tuple(question_payloads_list)
        workflow_status, action_message = _workflow_summary(
            run_status=str(run["status"]),
            control_state=str(run["control_state"]),
            error_code=(
                None
                if run["error_code"] is None
                else str(run["error_code"])
            ),
            completed_questions=completed_questions,
            expected_questions=len(item_rows),
        )
        attempt_payloads = tuple(
            {
                "attempt_number": int(attempt["attempt_number"]),
                "status": str(attempt["status"]),
                "request_count": int(attempt["request_count"]),
                "error_code": (
                    None
                    if attempt["error_code"] is None
                    else str(attempt["error_code"])
                ),
                "model_name": (
                    None
                    if attempt["model_name"] is None
                    else str(attempt["model_name"])
                ),
            }
            for attempt in attempts
        )
        return TrainingPaperOutcome(
            run_id=str(run["run_id"]),
            submission_id=str(run["submission_id"]),
            submission_revision=int(run["submission_revision"]),
            status=str(run["status"]),
            request_count=sum(
                int(attempt["request_count"]) for attempt in attempts
            ),
            expected_question_count=int(run["expected_question_count"]),
            expected_point_count=int(run["expected_point_count"]),
            model_name=(
                None
                if run["model_name"] is None
                else str(run["model_name"])
            ),
            usage=AssessmentUsage(
                prompt_tokens=int(run["prompt_tokens"]),
                completion_tokens=int(run["completion_tokens"]),
                total_tokens=int(run["total_tokens"]),
            ),
            latency_ms=int(run["latency_ms"]),
            issue_codes=tuple(
                json.loads(str(run["issue_codes_json"]))
            ),
            error_code=(
                None
                if run["error_code"] is None
                else str(run["error_code"])
            ),
            questions=question_payloads,
            review_revision=int(run["review_revision"]),
            control_state=str(run["control_state"]),
            workflow_status=workflow_status,
            action_message=action_message,
            attempts=attempt_payloads,
        )

    def _now(self) -> str:
        value = self.clock()
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        return str(value)


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _submission_lock(submission_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(submission_id, threading.Lock())


def _identifier(value: object) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError("submission_id must be a 64-character hex digest")
    return clean


def _positive_revision(value: object) -> int:
    revision = int(value)
    if revision < 1:
        raise ValueError("submission_revision must be positive")
    return revision


def _aware_clock(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            parsed = datetime.now().astimezone()
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    return parsed


def _criterion_points(payload_json: str) -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(payload_json)
        raw_points = payload["criteria"]["points"]
    except (TypeError, KeyError, json.JSONDecodeError):
        raise AssessmentInputInvalid(
            "frozen criterion snapshot is invalid"
        ) from None
    if not isinstance(raw_points, list):
        raise AssessmentInputInvalid(
            "frozen criterion points are invalid"
        )
    points: list[dict[str, Any]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, Mapping):
            raise AssessmentInputInvalid(
                "frozen criterion point is invalid"
            )
        point = _safe_model_mapping(raw_point)
        if not str(point.get("point_id") or "").strip():
            raise AssessmentInputInvalid(
                "frozen criterion point identity is invalid"
            )
        points.append(point)
    return tuple(points)


def _workflow_summary(
    *,
    run_status: str,
    control_state: str,
    error_code: str | None,
    completed_questions: int,
    expected_questions: int,
) -> tuple[str, str]:
    if expected_questions > 0 and completed_questions == expected_questions:
        return (
            "completed",
            "全部题目已完成判定，可进入训练证据发布。",
        )
    if completed_questions > 0:
        if run_status == "cancelled":
            return (
                "partial_review",
                "判定已取消；已完成题和教师锁已保留，请复核其余题目。",
            )
        if run_status == "failed":
            return (
                "partial_review",
                "部分题目已完成；运行失败未覆盖这些明细，请复核其余题目。",
            )
        return (
            "partial_review",
            "部分题目已完成；请只复核仍标记待复核的判定点。",
        )
    if run_status == "running" and control_state == "paused":
        return (
            "paused",
            "判定已暂停；已发出的当前请求仍会安全收尾，可等待或取消。",
        )
    if run_status == "running":
        return (
            "running",
            "判定正在进行；请等待当前请求完成，不要重复提交。",
        )
    if run_status == "cancelled":
        return (
            "cancelled",
            "判定已取消；已有候选和教师锁已保留，未完成点没有按未达成处理。",
        )
    if run_status == "failed":
        messages = {
            "interrupted_after_request": (
                "应用在请求发出后中断，结果是否返回未知；系统不会自动再次"
                "调用模型，请教师确认后再显式重试。"
            ),
            "interrupted_before_request": (
                "应用在请求发出前中断，未产生模型调用；请教师显式重试。"
            ),
            "timeout": "模型请求超时且未自动重试；请检查网络后显式重试。",
            "context_limit": (
                "整卷内容超过模型上下文限制；请核对答卷页后再决定是否重试。"
            ),
            "invalid_response": (
                "模型返回格式无法核对，未保存候选；请教师显式重试。"
            ),
            "storage_failure": (
                "候选写入失败且未形成半份结果；请检查存储后显式重试。"
            ),
            "model_failure": (
                "模型调用失败且未自动重试；请检查服务后显式重试。"
            ),
        }
        return (
            "failed",
            messages.get(
                error_code,
                "判定失败且未追加请求；请查看原因后由教师显式重试。",
            ),
        )
    return (
        "review_required",
        "当前题目仍需教师复核；不确定、无法辨认和缺失点均未按错误处理。",
    )


def _image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise AssessmentInputInvalid("submission page is not a PNG or JPEG")


_FROZEN_SCORE_KEYS = frozenset(
    {
        "score",
        "max_score",
        "min_score",
        "step_score",
        "total_score",
        "points_awarded",
        "score_awarded",
        "full_score",
        "weight",
        "point_value",
    }
)


def _reject_nested_score_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if str(raw_key).strip().casefold() in _FROZEN_SCORE_KEYS:
                raise AssessmentInputInvalid(
                    "frozen training criterion contains a score field"
                )
            _reject_nested_score_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_nested_score_fields(child)


def _safe_model_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.casefold()
        if any(
            marker in lowered
            for marker in (
                "path",
                "image",
                "student_name",
                "student_id",
                "class_id",
            )
        ):
            continue
        if isinstance(raw_value, Mapping):
            result[key] = _safe_model_mapping(raw_value)
        elif isinstance(raw_value, list):
            result[key] = [
                _safe_model_mapping(item)
                if isinstance(item, Mapping)
                else item
                for item in raw_value
            ]
        elif raw_value is None or isinstance(
            raw_value,
            (str, int, float, bool),
        ):
            result[key] = raw_value
    return result


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
