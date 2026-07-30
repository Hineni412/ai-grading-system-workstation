from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import CancelledError as FutureCancelledError
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.training_assessment.adapters import AssessmentContextExceeded
from backend.training_assessment.contracts import (
    POINT_STATES,
    AssessmentGatewayResponse,
    AssessmentItem,
    AssessmentPage,
    AssessmentUsage,
    ModelPointResult,
    TrainingAssessmentGateway,
    TrainingAssessmentRequest,
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


class TrainingAssessmentModule:
    """Deep module for one-request, non-score training assessment."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        gateway: TrainingAssessmentGateway,
        clock: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.gateway = gateway
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
            request_id = stable_hash(
                {"run_id": run_id, "request_number": 1}
            )
            self._record_request_started(run_id, clean_id)
            try:
                response = self.gateway.assess(
                    request,
                    operation_id=run_id,
                    request_id=request_id,
                )
            except (asyncio.CancelledError, FutureCancelledError):
                self._finish_failure(run_id, "cancelled", "cancelled")
                return self._require_outcome(run_id)
            except AssessmentContextExceeded:
                self._finish_failure(run_id, "failed", "context_limit")
                return self._require_outcome(run_id)
            except TimeoutError:
                self._finish_failure(run_id, "failed", "timeout")
                return self._require_outcome(run_id)
            except (json.JSONDecodeError, TypeError, ValueError):
                self._finish_failure(run_id, "failed", "invalid_response")
                return self._require_outcome(run_id)
            except Exception:
                self._finish_failure(run_id, "failed", "model_failure")
                return self._require_outcome(run_id)
            try:
                self._store_candidates(run_id, request, response)
            except Exception:
                self._finish_failure(run_id, "failed", "storage_failure")
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
            if any(
                field in raw
                for field in (
                    "score",
                    "max_score",
                    "points_awarded",
                    "weight",
                    "point_value",
                )
            ):
                raise AssessmentInputInvalid(
                    "frozen training criterion contains a score field"
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
    ) -> None:
        now = self._now()
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE training_assessment_runs
                SET request_count = 1, started_at = ?, updated_at = ?
                WHERE run_id = ? AND status = 'running'
                  AND request_count = 0
                """,
                (now, now, run_id),
            )
            if cursor.rowcount != 1:
                raise TrainingAssessmentError(
                    "assessment request was already started"
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
        request: TrainingAssessmentRequest,
        response: AssessmentGatewayResponse,
    ) -> None:
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
                SELECT status, request_count
                FROM training_assessment_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if (
                run is None
                or str(run["status"]) != "running"
                or int(run["request_count"]) != 1
            ):
                raise TrainingAssessmentError(
                    "assessment run is not writable"
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
                SET status = ?, model_name = ?,
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
            connection.commit()

    def _finish_failure(
        self,
        run_id: str,
        status: str,
        error_code: str,
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
            connection.commit()

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

    def _outcome(self, run_id: str) -> TrainingPaperOutcome | None:
        with connect(self.db_path) as connection:
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
                       model_evidence
                FROM training_point_results
                WHERE run_id = ?
                ORDER BY task_item_code, point_id
                """,
                (run_id,),
            ).fetchall()
        point_payloads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for point in points:
            point_payloads[str(point["task_item_code"])].append(
                {
                    "point_id": str(point["point_id"]),
                    "state": str(point["candidate_state"]),
                    "evidence": str(point["model_evidence"]),
                }
            )
        question_payloads = tuple(
            {
                "task_item_code": str(question["task_item_code"]),
                "item_order": int(question["item_order"]),
                "status": str(question["status"]),
                "met_count": int(question["met_count"]),
                "not_met_count": int(question["not_met_count"]),
                "uncertain_count": int(question["uncertain_count"]),
                "unreadable_count": int(question["unreadable_count"]),
                "total_count": int(question["total_count"]),
                "issue_codes": tuple(
                    json.loads(str(question["issue_codes_json"]))
                ),
                "points": tuple(
                    point_payloads[str(question["task_item_code"])]
                ),
            }
            for question in questions
        )
        return TrainingPaperOutcome(
            run_id=str(run["run_id"]),
            submission_id=str(run["submission_id"]),
            submission_revision=int(run["submission_revision"]),
            status=str(run["status"]),
            request_count=int(run["request_count"]),
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


def _image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise AssessmentInputInvalid("submission page is not a PNG or JPEG")


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
