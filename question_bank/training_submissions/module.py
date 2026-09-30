from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Literal

import cv2
import fitz
import numpy as np
from PIL import Image, UnidentifiedImageError

from question_bank.database.schema import connect, initialize_database
from question_bank.personalized_papers import (
    PaperInvalid,
    PersonalizedPaperModule,
)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_PAGES = 100
MAX_PAGE_PIXELS = 25_000_000
SUPPORTED_UPLOADS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
PageAction = Literal["match", "replace", "dismiss"]

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_LOCK_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


class TrainingSubmissionError(RuntimeError):
    pass


class SubmissionNotFound(TrainingSubmissionError):
    pass


class SubmissionRequestConflict(TrainingSubmissionError):
    pass


class SubmissionRevisionConflict(TrainingSubmissionError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__(
            f"submission revision conflict: expected {expected_revision}, "
            f"current {current_revision}"
        )


class InvalidSubmissionUpload(TrainingSubmissionError):
    pass


@dataclass(frozen=True, slots=True)
class CreateScanBatchCommand:
    operation_token: str
    paper_instance_ids: tuple[str, ...]
    actor_ref: str

    def __post_init__(self) -> None:
        ids = tuple(dict.fromkeys(_identifier(value) for value in self.paper_instance_ids))
        if not ids or len(ids) > 200:
            raise ValueError("paper_instance_ids must contain 1 to 200 items")
        object.__setattr__(self, "operation_token", _token(self.operation_token))
        object.__setattr__(self, "paper_instance_ids", ids)
        object.__setattr__(self, "actor_ref", _text(self.actor_ref, "actor_ref", 100))


@dataclass(frozen=True, slots=True)
class IngestUploadCommand:
    operation_token: str
    expected_revision: int
    filename: str
    media_type: str
    content_sha256: str
    actor_ref: str

    def __post_init__(self) -> None:
        filename = Path(str(self.filename or "")).name
        media_type = str(self.media_type or "").strip().casefold()
        digest = str(self.content_sha256 or "").strip().casefold()
        suffix = Path(filename).suffix.casefold()
        if media_type not in SUPPORTED_UPLOADS:
            raise ValueError("unsupported scan media type")
        expected_suffix = SUPPORTED_UPLOADS[media_type]
        if (
            not filename
            or len(filename) > 180
            or (expected_suffix == ".jpg" and suffix not in {".jpg", ".jpeg"})
            or (expected_suffix != ".jpg" and suffix != expected_suffix)
        ):
            raise ValueError("scan filename does not match media type")
        if not _HASH.fullmatch(digest):
            raise ValueError("content_sha256 is invalid")
        revision = int(self.expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "operation_token", _token(self.operation_token))
        object.__setattr__(self, "expected_revision", revision)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "actor_ref", _text(self.actor_ref, "actor_ref", 100))


@dataclass(frozen=True, slots=True)
class ResolvePageCommand:
    operation_token: str
    expected_revision: int
    scan_page_id: str
    action: PageAction
    actor_ref: str
    paper_instance_id: str | None = None
    page_number: int | None = None

    def __post_init__(self) -> None:
        action = str(self.action)
        if action not in {"match", "replace", "dismiss"}:
            raise ValueError("page action is invalid")
        paper_id = (
            _identifier(self.paper_instance_id)
            if self.paper_instance_id is not None
            else None
        )
        page_number = int(self.page_number) if self.page_number is not None else None
        if action in {"match", "replace"} and (
            paper_id is None or page_number is None or page_number < 1
        ):
            raise ValueError("matching requires a paper and page number")
        revision = int(self.expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "operation_token", _token(self.operation_token))
        object.__setattr__(self, "expected_revision", revision)
        object.__setattr__(self, "scan_page_id", _identifier(self.scan_page_id))
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "paper_instance_id", paper_id)
        object.__setattr__(self, "page_number", page_number)
        object.__setattr__(self, "actor_ref", _text(self.actor_ref, "actor_ref", 100))


@dataclass(frozen=True, slots=True)
class CancelSubmissionCommand:
    operation_token: str
    expected_revision: int
    actor_ref: str
    reason: str

    def __post_init__(self) -> None:
        revision = int(self.expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "operation_token", _token(self.operation_token))
        object.__setattr__(self, "expected_revision", revision)
        object.__setattr__(self, "actor_ref", _text(self.actor_ref, "actor_ref", 100))
        object.__setattr__(self, "reason", _text(self.reason, "reason", 500))


class TrainingSubmissionModule:
    """Own scan ingestion, identity grouping and manual recovery as one module."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.artifact_root = (
            self.data_root / "question_bank" / "training_submissions"
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.paper_module = PersonalizedPaperModule(
            db_path=self.db_path,
            data_root=self.data_root,
            clock=self.clock,
        )

    def list_batches(self, paper_batch_id: str) -> list[dict[str, Any]]:
        clean_id = _identifier(paper_batch_id)
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT b.batch_id, b.paper_batch_id, b.status,
                       b.created_at, b.updated_at,
                       COUNT(s.submission_id) AS submission_count
                FROM training_scan_batches b
                LEFT JOIN training_submissions s ON s.batch_id = b.batch_id
                WHERE b.paper_batch_id = ?
                GROUP BY b.batch_id
                ORDER BY b.created_at DESC, b.rowid DESC
                """,
                (clean_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_batch(self, command: CreateScanBatchCommand) -> dict[str, Any]:
        initialize_database(self.db_path)
        fingerprint = _payload_hash(asdict(command))
        repeated = self._event_batch(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        instances = [self.paper_module.get(value) for value in command.paper_instance_ids]
        if any(item["status"] != "frozen" for item in instances):
            raise InvalidSubmissionUpload("all expected papers must be frozen")
        paper_batches = {str(item["paper_batch_id"]) for item in instances}
        if len(paper_batches) != 1:
            raise InvalidSubmissionUpload("expected papers must belong to one batch")
        batch_id = _payload_hash(
            {
                "kind": "training-scan-batch",
                "operation_token": command.operation_token,
                "paper_instance_ids": command.paper_instance_ids,
            }
        )
        now = self._now()
        with _batch_lock(batch_id), connect(self.db_path) as connection:
            repeated = self._event_batch(
                command.operation_token,
                fingerprint,
                connection=connection,
            )
            if repeated is not None:
                return repeated
            connection.execute(
                """
                INSERT INTO training_scan_batches (
                    batch_id, operation_token, operation_fingerprint,
                    paper_batch_id, status, revision, created_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'manual_review', 1, ?, ?, ?)
                """,
                (
                    batch_id,
                    command.operation_token,
                    fingerprint,
                    paper_batches.pop(),
                    command.actor_ref,
                    now,
                    now,
                ),
            )
            for instance in instances:
                submission_id = _payload_hash(
                    {
                        "kind": "training-submission",
                        "batch_id": batch_id,
                        "paper_instance_id": instance["paper_instance_id"],
                    }
                )
                connection.execute(
                    """
                    INSERT INTO training_submissions (
                        submission_id, batch_id, paper_instance_id, student_id,
                        status, revision, expected_total_pages,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'manual_review', 1, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        batch_id,
                        instance["paper_instance_id"],
                        instance["student_id"],
                        len(instance["pages"]),
                        now,
                        now,
                    ),
                )
            self._insert_event(
                connection,
                batch_id=batch_id,
                operation_token=command.operation_token,
                fingerprint=fingerprint,
                event_type="batch_created",
                actor_ref=command.actor_ref,
                expected_revision=0,
                resulting_revision=1,
                details={"paper_instance_ids": list(command.paper_instance_ids)},
            )
        return self.get_batch(batch_id)

    def ingest(
        self,
        batch_id: str,
        command: IngestUploadCommand,
        source: BinaryIO,
    ) -> dict[str, Any]:
        clean_batch_id = _identifier(batch_id)
        initialize_database(self.db_path)
        content = _read_limited(source, MAX_UPLOAD_BYTES)
        digest = hashlib.sha256(content).hexdigest()
        if digest != command.content_sha256:
            raise InvalidSubmissionUpload("scan content hash does not match")
        if not _has_file_signature(content, command.media_type):
            raise InvalidSubmissionUpload("scan file signature is invalid")
        fingerprint = _payload_hash(
            {
                "batch_id": clean_batch_id,
                **asdict(command),
            }
        )
        repeated = self._event_batch(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        with _batch_lock(clean_batch_id):
            batch = self._batch_row(clean_batch_id)
            self._require_revision(batch, command.expected_revision)
            with connect(self.db_path) as connection:
                duplicate = connection.execute(
                    """
                    SELECT upload_id FROM training_submission_uploads
                    WHERE batch_id = ? AND content_sha256 = ?
                    """,
                    (clean_batch_id, digest),
                ).fetchone()
            if duplicate is not None:
                with connect(self.db_path) as connection:
                    current = self._batch_row(
                        clean_batch_id,
                        connection=connection,
                    )
                    self._require_revision(
                        current,
                        command.expected_revision,
                    )
                    self._insert_event(
                        connection,
                        batch_id=clean_batch_id,
                        operation_token=command.operation_token,
                        fingerprint=fingerprint,
                        event_type="upload_ingested",
                        actor_ref=command.actor_ref,
                        expected_revision=command.expected_revision,
                        resulting_revision=int(current["revision"]),
                        details={
                            "upload_id": str(duplicate["upload_id"]),
                            "content_sha256": digest,
                            "page_count": 0,
                            "duplicate_upload": True,
                        },
                    )
                return self.get_batch(clean_batch_id, duplicate_upload=True)

            upload_id = _payload_hash(
                {
                    "kind": "training-submission-upload",
                    "batch_id": clean_batch_id,
                    "operation_token": command.operation_token,
                    "content_sha256": digest,
                }
            )
            analyzed: list[dict[str, Any]] = []
            for index, image in enumerate(
                _decode_upload(content, command.media_type),
                start=1,
            ):
                if index > MAX_UPLOAD_PAGES:
                    raise InvalidSubmissionUpload(
                        "scan page count is invalid"
                    )
                analyzed.append(
                    self._analyze_page(
                        image,
                        batch_id=clean_batch_id,
                        upload_id=upload_id,
                        upload_page_number=index,
                    )
                )
            if not analyzed:
                raise InvalidSubmissionUpload("scan page count is invalid")
            self._publish_page_images(clean_batch_id, analyzed)
            now = self._now()
            with connect(self.db_path) as connection:
                current = self._batch_row(clean_batch_id, connection=connection)
                self._require_revision(current, command.expected_revision)
                expected = {
                    str(row["paper_instance_id"]): row
                    for row in connection.execute(
                        """
                        SELECT * FROM training_submissions WHERE batch_id = ?
                        """,
                        (clean_batch_id,),
                    ).fetchall()
                }
                connection.execute(
                    """
                    INSERT INTO training_submission_uploads (
                        upload_id, batch_id, operation_token,
                        operation_fingerprint, filename, media_type,
                        content_sha256, byte_size, page_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        upload_id,
                        clean_batch_id,
                        command.operation_token,
                        fingerprint,
                        command.filename,
                        command.media_type,
                        digest,
                        len(content),
                        len(analyzed),
                        now,
                    ),
                )
                for page in analyzed:
                    self._insert_analyzed_page(
                        connection,
                        page,
                        batch_id=clean_batch_id,
                        upload_id=upload_id,
                        expected=expected,
                        now=now,
                    )
                new_revision = int(current["revision"]) + 1
                connection.execute(
                    """
                    UPDATE training_scan_batches
                    SET revision = ?, updated_at = ?
                    WHERE batch_id = ?
                    """,
                    (new_revision, now, clean_batch_id),
                )
                self._recompute(connection, clean_batch_id, now=now)
                self._insert_event(
                    connection,
                    batch_id=clean_batch_id,
                    operation_token=command.operation_token,
                    fingerprint=fingerprint,
                    event_type="upload_ingested",
                    actor_ref=command.actor_ref,
                    expected_revision=command.expected_revision,
                    resulting_revision=new_revision,
                    details={
                        "upload_id": upload_id,
                        "content_sha256": digest,
                        "page_count": len(analyzed),
                    },
                )
        return self.get_batch(clean_batch_id)

    def resolve_page(
        self,
        batch_id: str,
        command: ResolvePageCommand,
    ) -> dict[str, Any]:
        clean_batch_id = _identifier(batch_id)
        initialize_database(self.db_path)
        fingerprint = _payload_hash({"batch_id": clean_batch_id, **asdict(command)})
        repeated = self._event_batch(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        with _batch_lock(clean_batch_id), connect(self.db_path) as connection:
            batch = self._batch_row(clean_batch_id, connection=connection)
            self._require_revision(batch, command.expected_revision)
            page = connection.execute(
                """
                SELECT * FROM training_submission_pages
                WHERE batch_id = ? AND scan_page_id = ?
                """,
                (clean_batch_id, command.scan_page_id),
            ).fetchone()
            if page is None:
                raise SubmissionNotFound(command.scan_page_id)
            now = self._now()
            submission_id: str | None = None
            if command.action == "dismiss":
                connection.execute(
                    """
                    UPDATE training_submission_pages
                    SET state = 'dismissed', issue_code = NULL,
                        submission_id = NULL, paper_instance_id = NULL,
                        claimed_page_number = NULL, claimed_total_pages = NULL,
                        updated_at = ?
                    WHERE scan_page_id = ?
                    """,
                    (now, command.scan_page_id),
                )
            else:
                target = connection.execute(
                    """
                    SELECT * FROM training_submissions
                    WHERE batch_id = ? AND paper_instance_id = ?
                    """,
                    (clean_batch_id, command.paper_instance_id),
                ).fetchone()
                if target is None or str(target["status"]) == "cancelled":
                    raise SubmissionNotFound(str(command.paper_instance_id))
                if int(command.page_number or 0) > int(target["expected_total_pages"]):
                    raise InvalidSubmissionUpload("target page number is invalid")
                active = connection.execute(
                    """
                    SELECT scan_page_id FROM training_submission_pages
                    WHERE submission_id = ? AND claimed_page_number = ?
                      AND state = 'assigned'
                    """,
                    (target["submission_id"], command.page_number),
                ).fetchall()
                if active and command.action != "replace":
                    raise SubmissionRequestConflict(
                        "target page is already assigned; explicit replace is required"
                    )
                if command.action == "replace":
                    connection.execute(
                        """
                        UPDATE training_submission_pages
                        SET state = 'replaced', updated_at = ?
                        WHERE submission_id = ? AND claimed_page_number = ?
                          AND state = 'assigned'
                        """,
                        (now, target["submission_id"], command.page_number),
                    )
                submission_revision = int(target["revision"]) + 1
                submission_id = str(target["submission_id"])
                connection.execute(
                    """
                    UPDATE training_submission_pages
                    SET state = 'assigned', issue_code = NULL,
                        submission_id = ?, paper_instance_id = ?,
                        claimed_page_number = ?, claimed_total_pages = ?,
                        assignment_revision = ?, updated_at = ?
                    WHERE scan_page_id = ?
                    """,
                    (
                        submission_id,
                        command.paper_instance_id,
                        command.page_number,
                        target["expected_total_pages"],
                        submission_revision,
                        now,
                        command.scan_page_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE training_submissions
                    SET revision = ?, updated_at = ?
                    WHERE submission_id = ?
                    """,
                    (submission_revision, now, submission_id),
                )
            new_revision = int(batch["revision"]) + 1
            connection.execute(
                """
                UPDATE training_scan_batches SET revision = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (new_revision, now, clean_batch_id),
            )
            self._recompute(connection, clean_batch_id, now=now)
            self._insert_event(
                connection,
                batch_id=clean_batch_id,
                submission_id=submission_id,
                operation_token=command.operation_token,
                fingerprint=fingerprint,
                event_type={
                    "match": "page_matched",
                    "replace": "page_replaced",
                    "dismiss": "page_dismissed",
                }[command.action],
                actor_ref=command.actor_ref,
                expected_revision=command.expected_revision,
                resulting_revision=new_revision,
                details={
                    "scan_page_id": command.scan_page_id,
                    "paper_instance_id": command.paper_instance_id,
                    "page_number": command.page_number,
                },
            )
        return self.get_batch(clean_batch_id)

    def finalize_submission(self, submission_id: str) -> dict[str, Any]:
        """Return a P4-14-safe snapshot only when every page is unambiguous."""
        clean_id = _identifier(submission_id)
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM training_submissions WHERE submission_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None:
                raise SubmissionNotFound(clean_id)
            if str(row["status"]) != "ready":
                raise InvalidSubmissionUpload("submission is not ready")
            pages = connection.execute(
                """
                SELECT scan_page_id, claimed_page_number, image_sha256
                FROM training_submission_pages
                WHERE submission_id = ? AND state = 'assigned'
                  AND issue_code IS NULL
                ORDER BY claimed_page_number
                """,
                (clean_id,),
            ).fetchall()
        if len(pages) != int(row["expected_total_pages"]):
            raise InvalidSubmissionUpload("submission page set is incomplete")
        return {
            "submission_id": clean_id,
            "submission_revision": int(row["revision"]),
            "paper_instance_id": str(row["paper_instance_id"]),
            "pages": [
                {
                    "scan_page_id": str(page["scan_page_id"]),
                    "page_number": int(page["claimed_page_number"]),
                    "image_sha256": str(page["image_sha256"]),
                }
                for page in pages
            ],
        }

    def cancel_submission(
        self,
        submission_id: str,
        command: CancelSubmissionCommand,
    ) -> dict[str, Any]:
        clean_id = _identifier(submission_id)
        initialize_database(self.db_path)
        with connect(self.db_path) as lookup:
            submission = lookup.execute(
                "SELECT batch_id FROM training_submissions WHERE submission_id = ?",
                (clean_id,),
            ).fetchone()
        if submission is None:
            raise SubmissionNotFound(clean_id)
        batch_id = str(submission["batch_id"])
        fingerprint = _payload_hash(
            {"submission_id": clean_id, **asdict(command)}
        )
        repeated = self._event_batch(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        with _batch_lock(batch_id), connect(self.db_path) as connection:
            batch = self._batch_row(batch_id, connection=connection)
            self._require_revision(batch, command.expected_revision)
            row = connection.execute(
                "SELECT * FROM training_submissions WHERE submission_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None:
                raise SubmissionNotFound(clean_id)
            now = self._now()
            submission_revision = int(row["revision"]) + 1
            connection.execute(
                """
                UPDATE training_submissions
                SET status = 'cancelled', revision = ?, updated_at = ?
                WHERE submission_id = ?
                """,
                (submission_revision, now, clean_id),
            )
            new_revision = int(batch["revision"]) + 1
            connection.execute(
                """
                UPDATE training_scan_batches SET revision = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (new_revision, now, batch_id),
            )
            self._recompute(connection, batch_id, now=now)
            self._insert_event(
                connection,
                batch_id=batch_id,
                submission_id=clean_id,
                operation_token=command.operation_token,
                fingerprint=fingerprint,
                event_type="submission_cancelled",
                actor_ref=command.actor_ref,
                expected_revision=command.expected_revision,
                resulting_revision=new_revision,
                details={"reason": command.reason},
            )
        return self.get_batch(batch_id)

    def get_batch(
        self,
        batch_id: str,
        *,
        duplicate_upload: bool = False,
    ) -> dict[str, Any]:
        clean_id = _identifier(batch_id)
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            batch = self._batch_row(clean_id, connection=connection)
            submissions = connection.execute(
                """
                SELECT s.*, p.student_code_snapshot, p.student_name_snapshot,
                       p.class_id_snapshot, p.series_version
                FROM training_submissions s
                JOIN personalized_paper_instances p
                  ON p.paper_instance_id = s.paper_instance_id
                WHERE s.batch_id = ?
                ORDER BY COALESCE(p.student_name_snapshot, p.student_code_snapshot,
                                  s.student_id), p.series_version
                """,
                (clean_id,),
            ).fetchall()
            pages = connection.execute(
                """
                SELECT scan_page_id, upload_id, upload_page_number,
                       submission_id, paper_instance_id, claimed_page_number,
                       claimed_total_pages, image_sha256, image_fingerprint,
                       width_pixels, height_pixels, blur_score,
                       brightness_score, contrast_score, rotation_degrees,
                       issue_code, state, assignment_revision, created_at
                FROM training_submission_pages
                WHERE batch_id = ?
                ORDER BY created_at, upload_id, upload_page_number
                """,
                (clean_id,),
            ).fetchall()
            events = connection.execute(
                """
                SELECT event_type, submission_id, expected_revision,
                       resulting_revision, details_json, created_at
                FROM training_submission_events
                WHERE batch_id = ? ORDER BY event_id
                """,
                (clean_id,),
            ).fetchall()
        page_payload = [self._public_page(page, clean_id) for page in pages]
        submission_payload: list[dict[str, Any]] = []
        for row in submissions:
            assigned = [
                page
                for page in page_payload
                if page["submission_id"] == row["submission_id"]
                and page["state"] == "assigned"
            ]
            present = {
                int(page["page_number"])
                for page in assigned
                if page["page_number"] is not None
            }
            missing = [
                number
                for number in range(1, int(row["expected_total_pages"]) + 1)
                if number not in present
            ]
            issues = sorted(
                {
                    str(page["issue_code"])
                    for page in page_payload
                    if page["submission_id"] == row["submission_id"]
                    and page["issue_code"]
                    and page["state"] not in {"replaced", "dismissed"}
                }
            )
            submission_payload.append(
                {
                    "submission_id": str(row["submission_id"]),
                    "paper_instance_id": str(row["paper_instance_id"]),
                    "student_id": str(row["student_id"]),
                    "student_code": row["student_code_snapshot"],
                    "student_name": row["student_name_snapshot"],
                    "class_id": row["class_id_snapshot"],
                    "series_version": int(row["series_version"]),
                    "status": str(row["status"]),
                    "revision": int(row["revision"]),
                    "expected_total_pages": int(row["expected_total_pages"]),
                    "missing_pages": missing,
                    "issue_codes": issues,
                    "assessment_started": row["assessment_started_at"] is not None,
                }
            )
        return {
            "batch_id": clean_id,
            "paper_batch_id": str(batch["paper_batch_id"]),
            "status": str(batch["status"]),
            "revision": int(batch["revision"]),
            "duplicate_upload": bool(duplicate_upload),
            "submissions": submission_payload,
            "pages": page_payload,
            "candidates": [
                {
                    "paper_instance_id": item["paper_instance_id"],
                    "student_id": item["student_id"],
                    "student_code": item["student_code"],
                    "student_name": item["student_name"],
                    "series_version": item["series_version"],
                    "total_pages": item["expected_total_pages"],
                }
                for item in submission_payload
                if item["status"] != "cancelled"
            ],
            "history": [
                {
                    "event_type": str(event["event_type"]),
                    "submission_id": event["submission_id"],
                    "expected_revision": int(event["expected_revision"]),
                    "resulting_revision": int(event["resulting_revision"]),
                    "details": json.loads(str(event["details_json"])),
                    "created_at": str(event["created_at"]),
                }
                for event in events
            ],
            "created_at": str(batch["created_at"]),
            "updated_at": str(batch["updated_at"]),
        }

    def page_artifact_path(
        self,
        scan_page_id: str,
        *,
        batch_id: str | None = None,
    ) -> Path:
        clean_id = _identifier(scan_page_id)
        clean_batch_id = _identifier(batch_id) if batch_id is not None else None
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT batch_id, image_path FROM training_submission_pages
                WHERE scan_page_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None or (
            clean_batch_id is not None
            and str(row["batch_id"]) != clean_batch_id
        ):
            raise SubmissionNotFound(clean_id)
        relative = Path(str(row["image_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise SubmissionNotFound(clean_id)
        root = self.artifact_root.resolve()
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SubmissionNotFound(clean_id) from exc
        if not path.is_file():
            raise SubmissionNotFound(clean_id)
        return path

    def _analyze_page(
        self,
        image: np.ndarray,
        *,
        batch_id: str,
        upload_id: str,
        upload_page_number: int,
    ) -> dict[str, Any]:
        oriented, identity, rotation = _read_page_identity(image)
        gray = cv2.cvtColor(oriented, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        contrast = float(gray.std())
        height, width = gray.shape
        issue: str | None = None
        verified: Mapping[str, Any] | None = None
        if identity:
            try:
                verified = self.paper_module.verify_page_identity(identity)
            except (PaperInvalid, ValueError):
                issue = "invalid_identity"
        else:
            issue = "identity_unreadable"
        ratio = width / max(height, 1)
        if issue is None and not 0.62 <= ratio <= 0.80:
            issue = "severe_crop"
        elif issue in {None, "identity_unreadable"} and (
            blur < 8.0 or contrast < 4.0
        ):
            issue = "image_blurry"
        success, encoded = cv2.imencode(".png", oriented)
        if not success:
            raise InvalidSubmissionUpload("scan page could not be normalized")
        content = bytes(encoded)
        image_sha = hashlib.sha256(content).hexdigest()
        scan_page_id = _payload_hash(
            {
                "kind": "training-scan-page",
                "batch_id": batch_id,
                "upload_id": upload_id,
                "upload_page_number": upload_page_number,
                "image_sha256": image_sha,
            }
        )
        return {
            "scan_page_id": scan_page_id,
            "upload_page_number": upload_page_number,
            "identity": identity,
            "verified": dict(verified) if verified is not None else None,
            "image_bytes": content,
            "image_sha256": image_sha,
            "image_fingerprint": _difference_hash(gray),
            "width_pixels": width,
            "height_pixels": height,
            "blur_score": round(blur, 3),
            "brightness_score": round(brightness, 3),
            "contrast_score": round(contrast, 3),
            "rotation_degrees": rotation,
            "issue_code": issue,
        }

    def _publish_page_images(
        self,
        batch_id: str,
        pages: Sequence[Mapping[str, Any]],
    ) -> None:
        directory = self.artifact_root / batch_id[:20] / "pages"
        directory.mkdir(parents=True, exist_ok=True)
        for page in pages:
            short_page_id = str(page["scan_page_id"])[:32]
            target = directory / f"{short_page_id}.png"
            temporary = directory / f".{short_page_id}.tmp"
            try:
                with temporary.open("wb") as handle:
                    handle.write(bytes(page["image_bytes"]))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def _insert_analyzed_page(
        self,
        connection: Any,
        page: Mapping[str, Any],
        *,
        batch_id: str,
        upload_id: str,
        expected: Mapping[str, Any],
        now: str,
    ) -> None:
        verified = page["verified"]
        paper_instance_id = (
            str(verified["paper_instance_id"]) if verified is not None else None
        )
        submission = expected.get(paper_instance_id or "")
        issue = page["issue_code"]
        state = "unassigned"
        submission_id: str | None = None
        page_number: int | None = None
        total_pages: int | None = None
        assignment_revision: int | None = None
        if verified is not None:
            page_number = int(verified["page_number"])
            total_pages = int(verified["total_pages"])
            if submission is None:
                issue = "unexpected_paper"
            else:
                submission_id = str(submission["submission_id"])
                existing = connection.execute(
                    """
                    SELECT scan_page_id, image_sha256, image_fingerprint
                    FROM training_submission_pages
                    WHERE submission_id = ? AND claimed_page_number = ?
                      AND state = 'assigned'
                    """,
                    (submission_id, page_number),
                ).fetchone()
                if existing is None:
                    state = "assigned"
                    assignment_revision = int(submission["revision"])
                elif (
                    str(existing["image_sha256"]) == page["image_sha256"]
                    or str(existing["image_fingerprint"])
                    == page["image_fingerprint"]
                ):
                    state = "duplicate"
                    issue = "duplicate_page"
                else:
                    state = "conflict"
                    issue = "page_content_conflict"
        relative = (
            Path(batch_id[:20])
            / "pages"
            / f"{str(page['scan_page_id'])[:32]}.png"
        ).as_posix()
        connection.execute(
            """
            INSERT INTO training_submission_pages (
                scan_page_id, batch_id, upload_id, upload_page_number,
                submission_id, paper_instance_id, claimed_page_number,
                claimed_total_pages, page_identity, image_sha256,
                image_fingerprint, image_path, width_pixels, height_pixels,
                blur_score, brightness_score, contrast_score,
                rotation_degrees, issue_code, state, assignment_revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                page["scan_page_id"],
                batch_id,
                upload_id,
                page["upload_page_number"],
                submission_id,
                paper_instance_id,
                page_number,
                total_pages,
                page["identity"],
                page["image_sha256"],
                page["image_fingerprint"],
                relative,
                page["width_pixels"],
                page["height_pixels"],
                page["blur_score"],
                page["brightness_score"],
                page["contrast_score"],
                page["rotation_degrees"],
                issue,
                state,
                assignment_revision,
                now,
                now,
            ),
        )

    def _recompute(self, connection: Any, batch_id: str, *, now: str) -> None:
        submissions = connection.execute(
            "SELECT * FROM training_submissions WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        for submission in submissions:
            if str(submission["status"]) == "cancelled":
                continue
            pages = connection.execute(
                """
                SELECT claimed_page_number, issue_code, state
                FROM training_submission_pages WHERE submission_id = ?
                """,
                (submission["submission_id"],),
            ).fetchall()
            assigned = [
                page for page in pages if str(page["state"]) == "assigned"
            ]
            page_numbers = {
                int(page["claimed_page_number"])
                for page in assigned
                if page["claimed_page_number"] is not None
            }
            complete = page_numbers == set(
                range(1, int(submission["expected_total_pages"]) + 1)
            )
            unresolved = any(
                page["issue_code"] is not None
                and str(page["state"]) not in {"replaced", "dismissed"}
                for page in pages
            )
            status = "ready" if complete and not unresolved else "manual_review"
            connection.execute(
                """
                UPDATE training_submissions SET status = ?, updated_at = ?
                WHERE submission_id = ?
                """,
                (status, now, submission["submission_id"]),
            )
        remaining = connection.execute(
            """
            SELECT COUNT(*) AS total FROM training_submissions
            WHERE batch_id = ? AND status = 'manual_review'
            """,
            (batch_id,),
        ).fetchone()
        loose = connection.execute(
            """
            SELECT COUNT(*) AS total FROM training_submission_pages
            WHERE batch_id = ? AND state NOT IN ('assigned', 'replaced', 'dismissed')
            """,
            (batch_id,),
        ).fetchone()
        active = connection.execute(
            """
            SELECT COUNT(*) AS total FROM training_submissions
            WHERE batch_id = ? AND status <> 'cancelled'
            """,
            (batch_id,),
        ).fetchone()
        if int(active["total"]) == 0:
            status = "cancelled"
        elif int(remaining["total"]) == 0 and int(loose["total"]) == 0:
            status = "ready"
        else:
            status = "manual_review"
        connection.execute(
            """
            UPDATE training_scan_batches SET status = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (status, now, batch_id),
        )

    def _public_page(self, row: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
        return {
            "scan_page_id": str(row["scan_page_id"]),
            "upload_id": str(row["upload_id"]),
            "upload_page_number": int(row["upload_page_number"]),
            "submission_id": row["submission_id"],
            "paper_instance_id": row["paper_instance_id"],
            "page_number": (
                int(row["claimed_page_number"])
                if row["claimed_page_number"] is not None
                else None
            ),
            "total_pages": (
                int(row["claimed_total_pages"])
                if row["claimed_total_pages"] is not None
                else None
            ),
            "image_sha256": str(row["image_sha256"]),
            "image_fingerprint": str(row["image_fingerprint"]),
            "width_pixels": int(row["width_pixels"]),
            "height_pixels": int(row["height_pixels"]),
            "blur_score": float(row["blur_score"]),
            "brightness_score": float(row["brightness_score"]),
            "contrast_score": float(row["contrast_score"]),
            "rotation_degrees": int(row["rotation_degrees"]),
            "issue_code": row["issue_code"],
            "state": str(row["state"]),
            "assignment_revision": row["assignment_revision"],
            "preview_url": (
                f"/api/training/scan-batches/{batch_id}/pages/"
                f"{row['scan_page_id']}/preview"
            ),
            "created_at": str(row["created_at"]),
        }

    def _batch_row(
        self,
        batch_id: str,
        *,
        connection: Any | None = None,
    ) -> Any:
        if connection is None:
            with connect(self.db_path) as own:
                row = own.execute(
                    "SELECT * FROM training_scan_batches WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM training_scan_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise SubmissionNotFound(batch_id)
        return row

    def _event_batch(
        self,
        operation_token: str,
        fingerprint: str,
        *,
        connection: Any | None = None,
    ) -> dict[str, Any] | None:
        def lookup(conn: Any) -> Any:
            return conn.execute(
                """
                SELECT batch_id, operation_fingerprint
                FROM training_submission_events WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()

        if connection is None:
            with connect(self.db_path) as own:
                event = lookup(own)
        else:
            event = lookup(connection)
        if event is None:
            return None
        if str(event["operation_fingerprint"]) != fingerprint:
            raise SubmissionRequestConflict("operation token was reused")
        return self.get_batch(str(event["batch_id"]))

    @staticmethod
    def _require_revision(row: Mapping[str, Any], expected: int) -> None:
        current = int(row["revision"])
        if current != int(expected):
            raise SubmissionRevisionConflict(expected, current)

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        batch_id: str,
        operation_token: str,
        fingerprint: str,
        event_type: str,
        actor_ref: str,
        expected_revision: int,
        resulting_revision: int,
        details: Mapping[str, Any],
        submission_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO training_submission_events (
                batch_id, submission_id, operation_token,
                operation_fingerprint, event_type, actor_ref,
                expected_revision, resulting_revision, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                submission_id,
                operation_token,
                fingerprint,
                event_type,
                actor_ref,
                expected_revision,
                resulting_revision,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat()


def _decode_upload(content: bytes, media_type: str) -> Iterator[np.ndarray]:
    try:
        if media_type == "application/pdf":
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.page_count <= 0 or document.page_count > MAX_UPLOAD_PAGES:
                    raise InvalidSubmissionUpload("scan PDF page count is invalid")
                for page in document:
                    width = float(page.rect.width) * 2.75
                    height = float(page.rect.height) * 2.75
                    if (
                        width <= 0
                        or height <= 0
                        or width * height > MAX_PAGE_PIXELS
                    ):
                        raise InvalidSubmissionUpload(
                            "scan PDF page dimensions are invalid"
                        )
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(2.75, 2.75),
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                        pixmap.height,
                        pixmap.width,
                        3,
                    )
                    yield cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        else:
            try:
                with Image.open(BytesIO(content)) as header:
                    width, height = header.size
            except (OSError, UnidentifiedImageError) as exc:
                raise InvalidSubmissionUpload(
                    "scan image is unreadable"
                ) from exc
            if (
                width <= 0
                or height <= 0
                or width * height > MAX_PAGE_PIXELS
            ):
                raise InvalidSubmissionUpload(
                    "scan image dimensions are invalid"
                )
            image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise InvalidSubmissionUpload("scan image is unreadable")
            yield image
    except InvalidSubmissionUpload:
        raise
    except Exception as exc:
        raise InvalidSubmissionUpload("scan file is unreadable") from exc


def _read_page_identity(image: np.ndarray) -> tuple[np.ndarray, str | None, int]:
    detector = cv2.QRCodeDetector()
    best: tuple[float, np.ndarray, str, int] | None = None
    original_height, original_width = image.shape[:2]
    original_is_portrait = (
        0.62 <= original_width / max(original_height, 1) <= 0.80
    )
    for turns, degrees in ((0, 0), (1, 90), (2, 180), (3, 270)):
        candidate = np.ascontiguousarray(np.rot90(image, turns))
        height, width = candidate.shape[:2]
        regions = (
            (candidate[int(height * 0.84) :, int(width * 0.78) :], 3.0),
            (candidate[int(height * 0.75) :, int(width * 0.68) :], 2.5),
            (candidate, 0.0),
        )
        for region, position_score in regions:
            if region.size == 0:
                continue
            attempts = [
                region,
                cv2.resize(
                    region,
                    None,
                    fx=1.5,
                    fy=1.5,
                    interpolation=cv2.INTER_NEAREST,
                ),
                cv2.resize(
                    region,
                    None,
                    fx=1.75,
                    fy=1.75,
                    interpolation=cv2.INTER_CUBIC,
                ),
            ]
            if position_score >= 2.5:
                gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
                adaptive = cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    51,
                    1,
                )
                attempts.append(
                    cv2.resize(
                        adaptive,
                        None,
                        fx=1.75,
                        fy=1.75,
                        interpolation=cv2.INTER_NEAREST,
                    )
                )
            value = ""
            points = None
            for attempt in attempts:
                try:
                    value, points, _ = detector.detectAndDecode(attempt)
                except cv2.error:
                    value, points = "", None
                if value:
                    break
            if not value:
                continue
            portrait = 0.62 <= width / max(height, 1) <= 0.80
            if portrait and position_score >= 2.5:
                return candidate, str(value), degrees
            score = position_score + (
                10.0 if portrait else 0.0
            )
            if points is not None:
                center = np.asarray(points).reshape(-1, 2).mean(axis=0)
                region_height, region_width = region.shape[:2]
                score += float(
                    center[0] / max(region_width, 1)
                    + center[1] / max(region_height, 1)
                )
            if best is None or score > best[0]:
                best = (score, candidate, str(value), degrees)
            break
    if best is None:
        return image, None, 0
    best_height, best_width = best[1].shape[:2]
    best_is_portrait = (
        0.62 <= best_width / max(best_height, 1) <= 0.80
    )
    if original_is_portrait and not best_is_portrait:
        return image, best[2], 0
    return best[1], best[2], best[3]


def _difference_hash(gray: np.ndarray) -> str:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _read_limited(source: BinaryIO, limit: int) -> bytes:
    content = source.read(limit + 1)
    if not content or len(content) > limit:
        raise InvalidSubmissionUpload("scan upload is empty or too large")
    return content


def _has_file_signature(content: bytes, media_type: str) -> bool:
    if media_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def _payload_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identifier(value: object) -> str:
    clean = str(value or "").strip().casefold()
    if not _HASH.fullmatch(clean):
        raise ValueError("identifier is invalid")
    return clean


def _token(value: object) -> str:
    clean = str(value or "").strip().casefold()
    if not _TOKEN.fullmatch(clean):
        raise ValueError("operation_token is invalid")
    return clean


def _text(value: object, field: str, maximum: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{field} is invalid")
    return clean


def _batch_lock(batch_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        return _LOCKS.setdefault(batch_id, threading.Lock())
