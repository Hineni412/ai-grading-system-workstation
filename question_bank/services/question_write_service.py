from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import AsyncIterable, Iterable

from question_bank.database.schema import connect
from question_bank.models.question import ALLOWED_TAG_TYPES
from question_bank.models.tag_schema import MAX_TAG_LENGTH
from question_bank.services.question_revision import question_revision


@dataclass(frozen=True)
class ConfirmedQuestionTag:
    tag_type: str
    tag_value: str
    confidence: float | None = None


@dataclass(frozen=True)
class QuestionWriteResult:
    question_id: int
    revision: str
    deleted: bool
    tags: tuple[ConfirmedQuestionTag, ...]


class QuestionWriteNotFound(LookupError):
    pass


class QuestionWriteConflict(RuntimeError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("Question state changed")
        self.current_revision = current_revision


class QuestionImportUploadNotFound(LookupError):
    pass


class QuestionImportTypeNotSupported(ValueError):
    pass


class QuestionImportStorageForbidden(RuntimeError):
    pass


class QuestionImportTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class StagedImportUpload:
    upload_id: str
    filename: str
    suffix: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "suffix": self.suffix,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class QuestionImportRequest:
    request_id: str
    upload_id: str
    filename: str
    size: int
    sha256: str
    status: str = "pending"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "request_id": self.request_id,
            "upload_id": self.upload_id,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "status": self.status,
        }


class QuestionBankWriteService:
    def __init__(
        self,
        db_path: Path,
        *,
        data_root: Path,
        max_upload_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.max_upload_bytes = int(max_upload_bytes)
        self._request_publish_lock = threading.Lock()

    def get_revision(self, question_id: int) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            revision = question_revision(conn, int(question_id))
        finally:
            conn.close()
        if revision is None:
            raise QuestionWriteNotFound("Question not found")
        return revision

    def replace_tags(
        self,
        question_id: int,
        *,
        expected_revision: str,
        tags: Iterable[ConfirmedQuestionTag],
    ) -> QuestionWriteResult:
        normalized = _normalize_tags(tags)
        question_id = int(question_id)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = _load_question_state(conn, question_id)
            if state is None or bool(state["is_deleted"]):
                raise QuestionWriteNotFound("Question not found")
            current_revision = question_revision(conn, question_id)
            assert current_revision is not None
            if _is_exact_manual_tag_state(conn, question_id, normalized):
                return QuestionWriteResult(
                    question_id=question_id,
                    revision=current_revision,
                    deleted=False,
                    tags=normalized,
                )
            if str(expected_revision) != current_revision:
                raise QuestionWriteConflict(current_revision)

            conn.execute(
                "DELETE FROM question_tags WHERE question_id = ?",
                (question_id,),
            )
            conn.executemany(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, confidence, source, model_name
                ) VALUES (?, ?, ?, ?, 'manual', NULL)
                """,
                [
                    (question_id, tag.tag_type, tag.tag_value, tag.confidence)
                    for tag in normalized
                ],
            )
            _touch_question(conn, question_id)
            updated_revision = question_revision(conn, question_id)
            assert updated_revision is not None

        try:
            from question_bank.services.question_frequency_service import (
                QuestionFrequencyService,
            )

            QuestionFrequencyService(self.db_path).invalidate_frequency_cache_for_question(
                question_id
            )
        except Exception:
            pass
        return QuestionWriteResult(
            question_id=question_id,
            revision=updated_revision,
            deleted=False,
            tags=normalized,
        )

    def set_deleted(
        self,
        question_id: int,
        *,
        expected_revision: str,
        deleted: bool,
    ) -> QuestionWriteResult:
        question_id = int(question_id)
        desired = bool(deleted)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = _load_question_state(conn, question_id)
            if state is None:
                raise QuestionWriteNotFound("Question not found")
            current_revision = question_revision(conn, question_id)
            assert current_revision is not None
            tags = _load_current_tags(conn, question_id)
            if bool(state["is_deleted"]) == desired:
                return QuestionWriteResult(
                    question_id=question_id,
                    revision=current_revision,
                    deleted=desired,
                    tags=tags,
                )
            if str(expected_revision) != current_revision:
                raise QuestionWriteConflict(current_revision)
            conn.execute(
                """
                UPDATE questions
                SET is_deleted = ?,
                    deleted_at = CASE
                        WHEN ? = 1 THEN strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                        ELSE NULL
                    END,
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                WHERE id = ?
                """,
                (int(desired), int(desired), question_id),
            )
            updated_revision = question_revision(conn, question_id)
            assert updated_revision is not None
        return QuestionWriteResult(
            question_id=question_id,
            revision=updated_revision,
            deleted=desired,
            tags=tags,
        )

    def stage_upload(self, *, filename: str, content: bytes) -> StagedImportUpload:
        if not content:
            raise ValueError("Question import upload is empty")
        if len(content) > self.max_upload_bytes:
            raise QuestionImportTooLarge("Question import upload is too large")
        safe_filename = _safe_client_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        if suffix not in {".docx", ".pdf"}:
            raise QuestionImportTypeNotSupported(
                "Question import file type is not supported"
            )
        upload_id = uuid.uuid4().hex
        uploads_root = self._controlled_staging_path("uploads")
        uploads_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=uploads_root))
        destination = uploads_root / upload_id
        digest = hashlib.sha256(content).hexdigest()
        upload = StagedImportUpload(
            upload_id=upload_id,
            filename=safe_filename,
            suffix=suffix,
            size=len(content),
            sha256=digest,
        )
        try:
            (temporary / f"source{suffix}").write_bytes(content)
            (temporary / "upload.json").write_text(
                json.dumps(upload.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return upload

    async def stage_upload_stream(
        self,
        *,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> StagedImportUpload:
        safe_filename = _safe_client_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        if suffix not in {".docx", ".pdf"}:
            raise QuestionImportTypeNotSupported(
                "Question import file type is not supported"
            )
        upload_id = uuid.uuid4().hex
        uploads_root = self._controlled_staging_path("uploads")
        uploads_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=uploads_root))
        destination = uploads_root / upload_id
        digest = hashlib.sha256()
        size = 0
        try:
            with (temporary / f"source{suffix}").open("wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise QuestionImportTooLarge(
                            "Question import upload is too large"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise ValueError("Question import upload is empty")
            upload = StagedImportUpload(
                upload_id=upload_id,
                filename=safe_filename,
                suffix=suffix,
                size=size,
                sha256=digest.hexdigest(),
            )
            (temporary / "upload.json").write_text(
                json.dumps(upload.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return upload

    def create_import_request(self, *, upload_id: str) -> QuestionImportRequest:
        normalized_upload_id = str(upload_id).strip().casefold()
        if re.fullmatch(r"[0-9a-f]{32}", normalized_upload_id) is None:
            raise QuestionImportUploadNotFound("Question import upload not found")
        upload_root = self._controlled_staging_path("uploads", normalized_upload_id)
        manifest_path = upload_root / "upload.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_filename = str(manifest["filename"])
            safe_filename = _safe_client_filename(raw_filename)
            upload = StagedImportUpload(
                upload_id=str(manifest["upload_id"]),
                filename=safe_filename,
                suffix=str(manifest["suffix"]),
                size=int(manifest["size"]),
                sha256=str(manifest["sha256"]),
            )
            source_path = upload_root / f"source{upload.suffix}"
            content = source_path.read_bytes()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QuestionImportUploadNotFound(
                "Question import upload not found"
            ) from exc
        if (
            upload.upload_id != normalized_upload_id
            or upload.filename != raw_filename
            or upload.suffix not in {".docx", ".pdf"}
            or Path(upload.filename).suffix.casefold() != upload.suffix
            or len(content) != upload.size
            or hashlib.sha256(content).hexdigest() != upload.sha256
        ):
            raise QuestionImportUploadNotFound("Question import upload not found")

        request = QuestionImportRequest(
            request_id=hashlib.sha256(
                f"question-import:{upload.upload_id}".encode("ascii")
            ).hexdigest()[:32],
            upload_id=upload.upload_id,
            filename=upload.filename,
            size=upload.size,
            sha256=upload.sha256,
        )
        requests_root = self._controlled_staging_path("requests")
        requests_root.mkdir(parents=True, exist_ok=True)
        destination = requests_root / f"{request.request_id}.json"
        with self._request_publish_lock:
            if destination.exists():
                return _load_existing_import_request(destination, request)
            temporary = requests_root / f".{request.request_id}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(
                    json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(temporary, destination)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        return request

    def _controlled_staging_path(self, *parts: str) -> Path:
        self.data_root.mkdir(parents=True, exist_ok=True)
        canonical_root = self.data_root.resolve()
        target = self.data_root.joinpath(
            "question_bank",
            "import_staging",
            *parts,
        )
        try:
            target.resolve(strict=False).relative_to(canonical_root)
        except ValueError as exc:
            raise QuestionImportStorageForbidden(
                "Question import storage is outside the data root"
            ) from exc
        return target


def _normalize_tags(
    tags: Iterable[ConfirmedQuestionTag],
) -> tuple[ConfirmedQuestionTag, ...]:
    normalized: list[ConfirmedQuestionTag] = []
    seen: set[tuple[str, str]] = set()
    for item in tags:
        tag_type = str(item.tag_type).strip()
        tag_value = str(item.tag_value).strip()
        if tag_type not in ALLOWED_TAG_TYPES:
            raise ValueError("Unsupported question tag type")
        if not tag_value:
            raise ValueError("Question tag value is required")
        if len(tag_value) > MAX_TAG_LENGTH:
            raise ValueError("Question tag value is too long")
        key = (tag_type, tag_value)
        if key in seen:
            continue
        seen.add(key)
        confidence = item.confidence
        if confidence is not None:
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("Question tag confidence is out of range")
        normalized.append(ConfirmedQuestionTag(tag_type, tag_value, confidence))
    return tuple(normalized)


def _load_question_state(
    conn: sqlite3.Connection,
    question_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, is_deleted, updated_at FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()


def _is_exact_manual_tag_state(
    conn: sqlite3.Connection,
    question_id: int,
    target: tuple[ConfirmedQuestionTag, ...],
) -> bool:
    rows = conn.execute(
        """
        SELECT tag_type, tag_value, confidence, source, model_name
        FROM question_tags
        WHERE question_id = ?
        ORDER BY id
        """,
        (question_id,),
    ).fetchall()
    current = tuple(
        ConfirmedQuestionTag(
            str(row["tag_type"]),
            str(row["tag_value"]),
            float(row["confidence"]) if row["confidence"] is not None else None,
        )
        for row in rows
    )
    order_key = lambda tag: (
        tag.tag_type,
        tag.tag_value,
        -1.0 if tag.confidence is None else tag.confidence,
    )
    return sorted(current, key=order_key) == sorted(target, key=order_key) and all(
        row["source"] == "manual" and row["model_name"] is None for row in rows
    )


def _load_current_tags(
    conn: sqlite3.Connection,
    question_id: int,
) -> tuple[ConfirmedQuestionTag, ...]:
    rows = conn.execute(
        """
        SELECT tag_type, tag_value, confidence
        FROM question_tags
        WHERE question_id = ?
        ORDER BY id
        """,
        (question_id,),
    ).fetchall()
    return tuple(
        ConfirmedQuestionTag(
            str(row["tag_type"]),
            str(row["tag_value"]),
            float(row["confidence"]) if row["confidence"] is not None else None,
        )
        for row in rows
    )


def _touch_question(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute(
        """
        UPDATE questions
        SET updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
        WHERE id = ?
        """,
        (question_id,),
    )


def _safe_client_filename(filename: str) -> str:
    normalized = str(filename).replace("\\", "/")
    safe = normalized.rsplit("/", 1)[-1].strip()
    if not safe or safe in {".", ".."}:
        raise ValueError("Question import filename is required")
    return safe


def _load_existing_import_request(
    path: Path,
    expected: QuestionImportRequest,
) -> QuestionImportRequest:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuestionImportUploadNotFound(
            "Question import request is invalid"
        ) from exc
    if existing != expected.to_dict():
        raise QuestionImportUploadNotFound("Question import request is invalid")
    return expected
