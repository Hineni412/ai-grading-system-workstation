from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import html
import io
import json
import os
import re
import stat
import threading
import uuid
import zipfile
from collections.abc import AsyncIterator, Collection, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, ContextManager, Literal
from urllib.parse import unquote

from PIL import Image

from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from backend.document_parsing import (
    extract_docx_text,
    extract_pdf_text,
    parse_docx_question_blocks,
    parse_plain_question_blocks,
)


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_IMAGE_PIXELS = 160_000_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
PUBLIC_PREVIEW_CHARACTERS = 500

_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUESTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]", re.IGNORECASE)
_IMAGE_PATH_MARKER = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]*>")
_RICH_TABLE_PATTERN = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_RICH_TABLE_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_RICH_TABLE_CELL_PATTERN = re.compile(
    r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>",
    re.IGNORECASE | re.DOTALL,
)
_RICH_INLINE_TOKEN_PATTERN = re.compile(
    r"<br\b[^>]*>|</?(?:sup|sub|u)>",
    re.IGNORECASE,
)
_ALLOWED_QUESTION_TYPES = {
    "choice",
    "fill_blank",
    "calculation",
    "proof",
    "comprehensive",
}
_IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "GIF": (".gif", "image/gif"),
    "WEBP": (".webp", "image/webp"),
    "BMP": (".bmp", "image/bmp"),
}
_PARSE_LOCKS_GUARD = threading.Lock()
_PARSE_LOCKS: dict[tuple[str, int], threading.Lock] = {}
_PROCESS_TOKEN = uuid.uuid4().hex
_ACTIVE_SUBMISSIONS_GUARD = threading.Lock()
_ACTIVE_SUBMISSIONS: set[tuple[str, int, str]] = set()


class ConfigSourceError(RuntimeError):
    pass


class ConfigSourceTooLargeError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source is too large")


class ConfigSourceTypeUnsupportedError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source type is unsupported")


class ConfigSourceInvalidError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source is invalid")


class ConfigSourceNotFoundError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source was not found")


class ConfigSourceChangedError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source has changed")


class ConfigSourceSubmissionConflictError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config source submission token was reused")


class ConfigAssetNotFoundError(ConfigSourceError):
    def __init__(self) -> None:
        super().__init__("config asset was not found")


@dataclass(frozen=True, slots=True)
class ConfigQuestionPreview:
    question_id: str
    question_type: str
    question_preview: str
    answer_preview: str
    answer_present: bool
    needs_review: bool
    local_answer_trusted: bool
    has_question_asset: bool
    has_answer_asset: bool


@dataclass(frozen=True, slots=True)
class ConfigSourceRecord:
    session_id: int
    source_id: str
    source_revision: str
    safe_filename: str
    suffix: Literal[".docx", ".pdf"]
    size_bytes: int
    sha256: str
    questions: tuple[ConfigQuestionPreview, ...]
    manifest_path: Path
    private_source_path: Path
    private_source_bytes: bytes
    private_blocks: tuple[dict[str, Any], ...]
    private_document_text: str
    private_question_images: dict[str, dict[str, str | None]]
    private_whole_page_images: tuple[bytes, ...]

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "safe_filename": self.safe_filename,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "sha256_prefix": self.sha256[:12],
            "parse_state": "ready",
            "questions": _public_questions_with_rich_content(
                self.questions,
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
                source_suffix=self.suffix,
            ),
        }


@dataclass(frozen=True, slots=True)
class _ConfigSourceMetadata:
    session_id: int
    source_id: str
    source_revision: str
    safe_filename: str
    suffix: Literal[".docx", ".pdf"]
    size_bytes: int
    sha256: str
    questions: tuple[ConfigQuestionPreview, ...]
    manifest_path: Path
    source_path: Path
    owned_names: frozenset[str]
    file_inventory: dict[str, dict[str, Any]]
    private_blocks: tuple[dict[str, Any], ...]
    private_document_text: str
    asset_files: dict[str, dict[str, str | None]]
    whole_page_files: tuple[str, ...]

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "safe_filename": self.safe_filename,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "sha256_prefix": self.sha256[:12],
            "parse_state": "ready",
            "questions": _public_questions_with_rich_content(
                self.questions,
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
                source_suffix=self.suffix,
            ),
        }


class _OwnedFileRegistry:
    def __init__(self, filesystem: SecureRootFilesystem) -> None:
        self._filesystem = filesystem
        self._cleanup_paths: list[Path] = []
        self._cleanup_keys: set[str] = set()
        self._owned_paths: list[Path] = []
        self._owned_keys: set[str] = set()

    def register(self, path: Path, *, manifest_owned: bool = False) -> None:
        clean_path = Path(path)
        key = os.path.normcase(str(clean_path.resolve(strict=False)))
        if key not in self._cleanup_keys:
            self._cleanup_keys.add(key)
            self._cleanup_paths.append(clean_path)
        if manifest_owned and key not in self._owned_keys:
            self._owned_keys.add(key)
            self._owned_paths.append(clean_path)

    @property
    def owned_paths(self) -> tuple[Path, ...]:
        return tuple(self._owned_paths)

    def cleanup(self) -> None:
        try:
            self._filesystem.unlink_many(reversed(self._cleanup_paths))
        except SecureFilesystemError:
            pass


class ConfigSourceActivationBusyError(ConfigSourceError):
    pass


@dataclass(frozen=True, slots=True)
class QuestionDecision:
    question_id: str
    question_type: Literal[
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    ]
    excluded: bool
    answer_confirmed: bool = False
    answer_override: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedGenerationInput:
    confirmed_blocks: tuple[dict[str, Any], ...]
    document_text: str
    question_images: dict[str, dict[str, str | None]]
    whole_page_images: tuple[bytes, ...]


def decode_upload_filename(value: str | None) -> str:
    try:
        decoded = unquote(str(value or ""), errors="strict").strip()
    except (UnicodeError, ValueError):
        raise ConfigSourceInvalidError() from None
    if not decoded or len(decoded) > 1024 or any(ord(char) < 32 for char in decoded):
        raise ConfigSourceInvalidError()
    return decoded


class ConfigSourceService:
    def __init__(
        self,
        upload_config_dir: Path,
        *,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        max_docx_member_bytes: int = MAX_DOCX_MEMBER_BYTES,
        max_docx_expanded_bytes: int = MAX_DOCX_EXPANDED_BYTES,
        max_pdf_pages: int = MAX_PDF_PAGES,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        max_image_pixels: int = MAX_IMAGE_PIXELS,
        max_total_image_bytes: int = MAX_TOTAL_IMAGE_BYTES,
        max_total_image_pixels: int = MAX_TOTAL_IMAGE_PIXELS,
        max_manifest_bytes: int = MAX_MANIFEST_BYTES,
    ) -> None:
        self._files = SecureRootFilesystem(Path(upload_config_dir))
        self.upload_config_dir = self._files.root
        self.max_upload_bytes = int(max_upload_bytes)
        self.max_docx_member_bytes = int(max_docx_member_bytes)
        self.max_docx_expanded_bytes = int(max_docx_expanded_bytes)
        self.max_pdf_pages = int(max_pdf_pages)
        self.max_image_bytes = int(max_image_bytes)
        self.max_image_pixels = int(max_image_pixels)
        self.max_total_image_bytes = int(max_total_image_bytes)
        self.max_total_image_pixels = int(max_total_image_pixels)
        self.max_manifest_bytes = int(max_manifest_bytes)
        if min(
            self.max_upload_bytes,
            self.max_docx_member_bytes,
            self.max_docx_expanded_bytes,
            self.max_pdf_pages,
            self.max_image_bytes,
            self.max_image_pixels,
            self.max_total_image_bytes,
            self.max_total_image_pixels,
            self.max_manifest_bytes,
        ) <= 0:
            raise ValueError("config source limits must be positive")

    async def stage_and_parse(
        self,
        *,
        session_id: int,
        filename: str,
        chunks: AsyncIterator[bytes],
        source_id: str | None = None,
        activation_guard: Callable[[], ContextManager[None]] | None = None,
    ) -> ConfigSourceRecord:
        clean_session_id = _positive_session_id(session_id)
        safe_filename, suffix = _safe_filename(filename)
        clean_source_id = str(source_id or uuid.uuid4().hex).strip()
        if not _SOURCE_ID.fullmatch(clean_source_id):
            raise ConfigSourceInvalidError()
        source_dir = self._source_dir(clean_session_id, clean_source_id)
        self._prepare_source_dir(source_dir)
        temporary_path = source_dir / f".source.{uuid.uuid4().hex}.tmp"
        source_path = source_dir / f"source{suffix}"
        registry = _OwnedFileRegistry(self._files)
        digest = hashlib.sha256()
        size_bytes = 0
        completed = False
        try:
            registry.register(temporary_path)
            with self._files.create_exclusive(temporary_path) as stream:
                async for raw_chunk in chunks:
                    chunk = bytes(raw_chunk or b"")
                    if not chunk:
                        continue
                    size_bytes += len(chunk)
                    if size_bytes > self.max_upload_bytes:
                        raise ConfigSourceTooLargeError()
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
            if size_bytes <= 0:
                raise ConfigSourceInvalidError()
            _validate_magic(self._files.read_bytes(temporary_path)[:1024], suffix)
            registry.register(source_path, manifest_owned=True)
            self._files.replace(temporary_path, source_path)

            parse_task = asyncio.create_task(
                asyncio.to_thread(
                    self._parse_staged_source,
                    clean_session_id,
                    suffix,
                    source_path,
                    source_dir,
                    registry,
                )
            )
            try:
                blocks, document_text, asset_files, whole_page_files = await asyncio.shield(parse_task)
            except asyncio.CancelledError:
                await parse_task
                raise
            self._enforce_asset_budget(source_dir, asset_files, whole_page_files)
            questions = _question_previews(blocks, asset_files)
            source_sha256 = digest.hexdigest()
            manifest_path = source_dir / "manifest.json"
            registry.register(manifest_path, manifest_owned=True)
            expected_roles = _expected_inventory_roles(
                source_dir=source_dir,
                source_file=source_path.name,
                private_blocks=blocks,
                asset_files=asset_files,
                whole_page_files=whole_page_files,
            )
            registered_names = [
                path.name
                for path in registry.owned_paths
                if path.name != "manifest.json"
            ]
            if (
                len(registered_names) != len(set(registered_names))
                or set(registered_names) != set(expected_roles)
            ):
                raise ConfigSourceInvalidError()
            file_inventory: dict[str, dict[str, Any]] = {}
            for owned_name in sorted(expected_roles):
                content = self._files.read_bytes(
                    self._owned_path(source_dir, owned_name)
                )
                file_inventory[owned_name] = {
                    **expected_roles[owned_name],
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            source_inventory = file_inventory.get(source_path.name)
            if source_inventory != {
                "role": "source",
                "size_bytes": size_bytes,
                "sha256": source_sha256,
            }:
                raise ConfigSourceInvalidError()
            owned_names = sorted({*expected_roles, "manifest.json"})
            manifest = {
                "version": 2,
                "session_id": clean_session_id,
                "source_id": clean_source_id,
                "source_revision": "",
                "safe_filename": safe_filename,
                "suffix": suffix,
                "size_bytes": size_bytes,
                "sha256": source_sha256,
                "parse_state": "ready",
                "source_file": source_path.name,
                "owned_files": owned_names,
                "file_inventory": file_inventory,
                "questions": [asdict(question) for question in questions],
                "private_blocks": blocks,
                "private_document_text": document_text,
                "asset_files": asset_files,
                "whole_page_files": whole_page_files,
            }
            source_revision = _canonical_source_revision(manifest)
            manifest["source_revision"] = source_revision
            if len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) > self.max_manifest_bytes:
                raise ConfigSourceInvalidError()
            with session_config_lock(self.upload_config_dir, clean_session_id):
                guard = activation_guard() if activation_guard is not None else None
                if guard is None:
                    record = self._activate_source_locked(
                        clean_session_id, source_dir, manifest_path, manifest,
                        clean_source_id, source_revision,
                    )
                else:
                    with guard:
                        record = self._activate_source_locked(
                            clean_session_id, source_dir, manifest_path, manifest,
                            clean_source_id, source_revision,
                        )
            completed = True
            return record
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceInvalidError() from None
        finally:
            if not completed:
                registry.cleanup()

    def _activate_source_locked(
        self,
        session_id: int,
        source_dir: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        source_id: str,
        source_revision: str,
    ) -> ConfigSourceRecord:
        self._assert_controlled_directory(source_dir)
        self._assert_controlled_path(manifest_path)
        active_path = self._active_path(session_id)
        self._assert_controlled_path(active_path)
        self._files.write_json_atomic(manifest_path, manifest)
        record = self._record_from_manifest(manifest_path, manifest)
        final_manifest = self._read_json_object(manifest_path)
        final_metadata = self._metadata_from_manifest(manifest_path, final_manifest)
        self._validate_generation_integrity(final_metadata)
        self._files.write_json_atomic(
            active_path,
            {"source_id": source_id, "source_revision": source_revision},
        )
        return record

    def begin_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        filename: str,
        content_length: int | None,
    ) -> str:
        clean_session_id = _positive_session_id(session_id)
        clean_token = _request_token(request_token)
        request_fingerprint = _submission_request_fingerprint(
            filename=filename,
            content_length=content_length,
        )
        marker_path = self._submission_path(clean_session_id, clean_token)
        self._prepare_source_dir(self._source_dir(clean_session_id, clean_token))
        with session_config_lock(self.upload_config_dir, clean_session_id):
            if marker_path.is_file():
                marker = self._read_json_object(marker_path)
                if marker.get("request_fingerprint") != request_fingerprint:
                    raise ConfigSourceSubmissionConflictError()
                state, _source = self._submission_result_locked(
                    session_id=clean_session_id,
                    request_token=clean_token,
                    marker_path=marker_path,
                    marker=marker,
                )
                return state
            self._files.write_json_atomic(
                marker_path,
                {
                    "status": "processing",
                    "source_id": clean_token,
                    "process_token": _PROCESS_TOKEN,
                    "request_fingerprint": request_fingerprint,
                },
            )
            self._set_submission_active(clean_session_id, clean_token, True)
        return "started"

    def finish_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        succeeded: bool,
    ) -> None:
        clean_session_id = _positive_session_id(session_id)
        clean_token = _request_token(request_token)
        marker_path = self._submission_path(clean_session_id, clean_token)
        try:
            with session_config_lock(self.upload_config_dir, clean_session_id):
                marker = (
                    self._read_json_object(marker_path)
                    if marker_path.is_file()
                    else {"source_id": clean_token}
                )
                source_state, _source = self._submission_source_result_locked(
                    session_id=clean_session_id,
                    request_token=clean_token,
                )
                final_state = source_state or ("succeeded" if succeeded else "failed")
                next_marker = {
                    "status": final_state,
                    "source_id": clean_token,
                }
                fingerprint = str(marker.get("request_fingerprint") or "")
                if _SHA256.fullmatch(fingerprint):
                    next_marker["request_fingerprint"] = fingerprint
                self._files.write_json_atomic(marker_path, next_marker)
        finally:
            self._set_submission_active(clean_session_id, clean_token, False)

    def submission_public(self, *, session_id: int, request_token: str) -> dict[str, Any]:
        clean_session_id = _positive_session_id(session_id)
        clean_token = _request_token(request_token)
        marker_path = self._submission_path(clean_session_id, clean_token)
        if not marker_path.is_file():
            raise ConfigSourceNotFoundError()
        with session_config_lock(self.upload_config_dir, clean_session_id):
            marker = self._read_json_object(marker_path)
            if self._submission_state(marker_path, marker=marker) == "abandoned":
                raise ConfigSourceNotFoundError()
            state, source = self._submission_result_locked(
                session_id=clean_session_id,
                request_token=clean_token,
                marker_path=marker_path,
                marker=marker,
            )
        return {"status": state, "source": source}

    def abandon_submission(self, *, session_id: int, request_token: str) -> None:
        """Atomically reserve an unseen token so a late upload cannot start."""
        clean_session_id = _positive_session_id(session_id)
        clean_token = _request_token(request_token)
        marker_path = self._submission_path(clean_session_id, clean_token)
        self._prepare_source_dir(self._source_dir(clean_session_id, clean_token))
        with session_config_lock(self.upload_config_dir, clean_session_id):
            if marker_path.is_file():
                marker = self._read_json_object(marker_path)
                if self._submission_state(marker_path, marker=marker) == "abandoned":
                    return
                raise ConfigSourceSubmissionConflictError()
            self._files.write_json_atomic(
                marker_path,
                {"status": "abandoned", "source_id": clean_token},
            )

    def _submission_result_locked(
        self,
        *,
        session_id: int,
        request_token: str,
        marker_path: Path,
        marker: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        self._submission_state(marker_path, marker=marker)
        source_state, source = self._submission_source_result_locked(
            session_id=session_id,
            request_token=request_token,
        )
        if source_state is not None:
            return source_state, source
        state = str(marker.get("status") or "")
        if state == "processing" and (
            str(marker.get("process_token") or "") != _PROCESS_TOKEN
            or not self._submission_is_active(session_id, request_token)
        ):
            state = "failed"
            next_marker = {
                "status": state,
                "source_id": request_token,
            }
            fingerprint = str(marker.get("request_fingerprint") or "")
            if _SHA256.fullmatch(fingerprint):
                next_marker["request_fingerprint"] = fingerprint
            try:
                self._files.write_json_atomic(marker_path, next_marker)
            except (OSError, SecureFilesystemError):
                pass
        elif state == "succeeded":
            state = "failed"
            next_marker = {
                "status": state,
                "source_id": request_token,
            }
            fingerprint = str(marker.get("request_fingerprint") or "")
            if _SHA256.fullmatch(fingerprint):
                next_marker["request_fingerprint"] = fingerprint
            try:
                self._files.write_json_atomic(marker_path, next_marker)
            except (OSError, SecureFilesystemError):
                pass
        return state, None

    def _submission_source_result_locked(
        self,
        *,
        session_id: int,
        request_token: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        try:
            source = self.load_public(
                session_id=session_id,
                source_id=request_token,
                require_active=True,
            )
            return "succeeded", source
        except ConfigSourceChangedError:
            try:
                self.load_public(
                    session_id=session_id,
                    source_id=request_token,
                    require_active=False,
                )
            except ConfigSourceError:
                try:
                    active_source_id, _active_revision = self._read_active(session_id)
                except ConfigSourceError:
                    return None, None
                if active_source_id != request_token:
                    return "replaced", None
                return None, None
            return "replaced", None
        except ConfigSourceError:
            return None, None

    def _submission_key(self, session_id: int, request_token: str) -> tuple[str, int, str]:
        return (
            os.path.normcase(str(self.upload_config_dir)),
            int(session_id),
            str(request_token),
        )

    def _set_submission_active(
        self,
        session_id: int,
        request_token: str,
        active: bool,
    ) -> None:
        key = self._submission_key(session_id, request_token)
        with _ACTIVE_SUBMISSIONS_GUARD:
            if active:
                _ACTIVE_SUBMISSIONS.add(key)
            else:
                _ACTIVE_SUBMISSIONS.discard(key)

    def _submission_is_active(self, session_id: int, request_token: str) -> bool:
        key = self._submission_key(session_id, request_token)
        with _ACTIVE_SUBMISSIONS_GUARD:
            return key in _ACTIVE_SUBMISSIONS

    def _submission_state(
        self,
        marker_path: Path,
        *,
        marker: dict[str, Any] | None = None,
    ) -> str:
        marker = marker if marker is not None else self._read_json_object(marker_path)
        state = str(marker.get("status") or "")
        source_id = str(marker.get("source_id") or "")
        if state not in {"processing", "succeeded", "failed", "replaced", "abandoned"} or not _SOURCE_ID.fullmatch(source_id):
            raise ConfigSourceInvalidError()
        return state

    def _parse_staged_source(
        self,
        session_id: int,
        suffix: str,
        source_path: Path,
        source_dir: Path,
        registry: _OwnedFileRegistry,
    ) -> tuple[list[dict[str, Any]], str, dict[str, dict[str, str | None]], list[str]]:
        key = (os.path.normcase(str(self.upload_config_dir)), int(session_id))
        with _PARSE_LOCKS_GUARD:
            parse_lock = _PARSE_LOCKS.setdefault(key, threading.Lock())
        with parse_lock:
            if suffix == ".docx":
                return self._parse_docx(source_path, source_dir, registry)
            return self._parse_pdf(source_path, source_dir, registry)

    def _enforce_asset_budget(
        self,
        source_dir: Path,
        asset_files: dict[str, dict[str, str | None]],
        whole_page_files: Sequence[str],
    ) -> None:
        names = [
            filename
            for entry in asset_files.values()
            for filename in entry.values()
            if isinstance(filename, str) and filename
        ] + list(whole_page_files)
        total_bytes = 0
        total_pixels = 0
        for name in dict.fromkeys(names):
            content = self._files.read_bytes(self._owned_path(source_dir, name))
            width, height = _image_dimensions(content)
            image_pixels = width * height
            if len(content) > self.max_image_bytes or image_pixels > self.max_image_pixels:
                raise ConfigSourceInvalidError()
            total_bytes += len(content)
            total_pixels += image_pixels
            if total_bytes > self.max_total_image_bytes or total_pixels > self.max_total_image_pixels:
                raise ConfigSourceInvalidError()

    def load(
        self,
        *,
        session_id: int,
        source_id: str,
        require_active: bool = True,
    ) -> ConfigSourceRecord:
        metadata = self._load_metadata(
            session_id=session_id,
            source_id=source_id,
            require_active=require_active,
        )
        return self._record_from_metadata(metadata)

    def load_for_generation(
        self,
        *,
        session_id: int,
        source_id: str,
        source_revision: str,
    ) -> ConfigSourceRecord:
        clean_revision = str(source_revision or "").strip()
        if not _SOURCE_REVISION.fullmatch(clean_revision):
            raise ConfigSourceChangedError()
        record = self.load(
            session_id=session_id,
            source_id=source_id,
            require_active=True,
        )
        if record.source_revision != clean_revision:
            raise ConfigSourceChangedError()
        return record

    def load_public(
        self,
        *,
        session_id: int,
        source_id: str,
        require_active: bool = True,
    ) -> dict[str, Any]:
        return self._load_metadata(
            session_id=session_id,
            source_id=source_id,
            require_active=require_active,
        ).public_snapshot()

    def load_active_public(self, *, session_id: int) -> dict[str, Any]:
        clean_session_id = _positive_session_id(session_id)
        try:
            source_id, source_revision = self._read_active(clean_session_id)
            metadata = self._load_metadata(
                session_id=clean_session_id,
                source_id=source_id,
                require_active=True,
            )
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceInvalidError() from None
        if metadata.source_revision != source_revision:
            raise ConfigSourceChangedError()
        return metadata.public_snapshot()

    def _load_metadata(
        self,
        *,
        session_id: int,
        source_id: str,
        require_active: bool,
    ) -> _ConfigSourceMetadata:
        clean_session_id = _positive_session_id(session_id)
        clean_source_id = str(source_id or "").strip()
        if not _SOURCE_ID.fullmatch(clean_source_id):
            raise ConfigSourceNotFoundError()
        source_dir = self._source_dir(clean_session_id, clean_source_id)
        manifest_path = source_dir / "manifest.json"
        try:
            self._assert_controlled_path(manifest_path)
            if not manifest_path.is_file():
                raise ConfigSourceNotFoundError()
            self._assert_controlled_directory(source_dir)
            manifest = self._read_json_object(manifest_path)
            metadata = self._metadata_from_manifest(manifest_path, manifest)
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceInvalidError() from None
        if require_active:
            try:
                active_source_id, active_revision = self._read_active(clean_session_id)
            except ConfigSourceError:
                raise ConfigSourceChangedError() from None
            if (
                active_source_id != metadata.source_id
                or active_revision != metadata.source_revision
            ):
                raise ConfigSourceChangedError()
        return metadata

    def read_asset(
        self,
        *,
        session_id: int,
        source_id: str,
        question_id: str,
        asset_kind: str,
        asset_index: int | None = None,
    ) -> tuple[bytes, str]:
        kind = str(asset_kind or "").strip()
        clean_question_id = str(question_id or "").strip()
        if kind not in {"question", "answer"} or not _QUESTION_ID.fullmatch(
            clean_question_id
        ):
            raise ConfigAssetNotFoundError()
        metadata = self._load_metadata(
            session_id=session_id,
            source_id=source_id,
            require_active=True,
        )
        entry = metadata.asset_files.get(clean_question_id)
        legacy_filename = entry.get(kind) if isinstance(entry, dict) else None
        if asset_index is None:
            filename = legacy_filename
        else:
            if (
                not isinstance(asset_index, int)
                or isinstance(asset_index, bool)
                or asset_index < 0
            ):
                raise ConfigAssetNotFoundError()
            try:
                filenames = _question_asset_filenames(
                    metadata,
                    question_id=clean_question_id,
                    asset_kind=kind,
                )
            except Exception:
                raise ConfigAssetNotFoundError() from None
            filename = (
                filenames[asset_index]
                if asset_index < len(filenames)
                else None
            )
        if not isinstance(filename, str) or not filename:
            raise ConfigAssetNotFoundError()
        try:
            path = self._owned_path(metadata.manifest_path.parent, filename)
            content = self._files.read_bytes(path)
            self._validate_inventory_content(metadata, filename, content)
            inventory_entry = metadata.file_inventory.get(filename)
            if (
                not isinstance(inventory_entry, dict)
                or inventory_entry.get("role") != "question_asset"
                or inventory_entry.get("question_id") != clean_question_id
                or inventory_entry.get("asset_kind") != kind
            ):
                raise ConfigSourceInvalidError()
            _suffix, media_type = _image_type(content)
        except Exception:
            raise ConfigAssetNotFoundError() from None
        return content, media_type

    def apply_teacher_decisions(
        self,
        record: ConfigSourceRecord,
        decisions: Sequence[QuestionDecision],
    ) -> PreparedGenerationInput:
        known = {question.question_id for question in record.questions}
        by_id: dict[str, QuestionDecision] = {}
        for decision in decisions:
            question_id = str(decision.question_id or "").strip()
            if question_id not in known:
                raise ValueError("unknown question decision")
            if question_id in by_id:
                raise ValueError("duplicate question decision")
            if decision.question_type not in _ALLOWED_QUESTION_TYPES:
                raise ValueError("unsupported question type")
            by_id[question_id] = decision

        confirmed: list[dict[str, Any]] = []
        included_ids: set[str] = set()
        for private_block in record.private_blocks:
            block = copy.deepcopy(private_block)
            question_id = str(block.get("question_id") or "").strip()
            decision = by_id.get(question_id)
            if decision is not None and decision.excluded:
                continue
            if decision is not None:
                block["question_type"] = decision.question_type
                block["question_type_confirmed"] = True
                if decision.answer_override is not None and not decision.answer_confirmed:
                    raise ValueError("answer override must be confirmed")
                if decision.answer_confirmed:
                    teacher_answer = str(
                        decision.answer_override
                        if decision.answer_override is not None
                        else (
                            block.get("canonical_answer")
                            or block.get("answer_text")
                            or ""
                        )
                    ).strip()
                    if not teacher_answer or len(teacher_answer) > 20_000:
                        raise ValueError("confirmed answer must be nonblank and bounded")
                    block["canonical_answer"] = teacher_answer
                    block["answer_text"] = teacher_answer
                    block["accepted_forms"] = [teacher_answer]
                    block["local_answer_trusted"] = True
                    block["needs_review"] = False
                    block["answer_confirmed_by_teacher"] = True
            else:
                block["question_type_confirmed"] = False
            confirmed.append(block)
            included_ids.add(question_id)
        return PreparedGenerationInput(
            confirmed_blocks=tuple(confirmed),
            document_text=record.private_document_text,
            question_images={
                question_id: copy.deepcopy(images)
                for question_id, images in record.private_question_images.items()
                if question_id in included_ids
            },
            whole_page_images=record.private_whole_page_images,
        )

    def prepare_generation_input(
        self,
        record: ConfigSourceRecord,
        decisions: Sequence[QuestionDecision],
        generation_mode: str,
    ) -> PreparedGenerationInput:
        mode = str(generation_mode or "").strip()
        if mode == "per_question":
            mode = "batched"
        if mode == "whole_document":
            if decisions:
                raise ValueError(
                    "whole-document generation does not accept per-question decisions"
                )
            if record.suffix == ".docx" and not record.private_document_text.strip():
                raise ValueError("whole-document DOCX source has no readable text")
            if record.suffix == ".pdf" and not record.private_whole_page_images:
                raise ValueError("whole-document PDF source has no readable pages")
            return PreparedGenerationInput(
                confirmed_blocks=(),
                document_text=record.private_document_text,
                question_images={},
                whole_page_images=record.private_whole_page_images,
            )
        if mode != "batched":
            raise ValueError("unsupported config generation mode")
        prepared = self.apply_teacher_decisions(record, decisions)
        if not prepared.confirmed_blocks:
            raise ValueError("at least one confirmed question is required")
        return prepared

    def cleanup_inactive(
        self,
        *,
        session_id: int,
        referenced_source_ids: Collection[str],
    ) -> tuple[str, ...]:
        clean_session_id = _positive_session_id(session_id)
        referenced = {
            str(item).strip()
            for item in referenced_source_ids
            if _SOURCE_ID.fullmatch(str(item).strip())
        }
        pending_source_ids: list[str] = []
        pending_paths: list[Path] = []
        with session_config_lock(self.upload_config_dir, clean_session_id):
            try:
                active_source_id, active_revision = self._read_active(clean_session_id)
                active_metadata = self._load_metadata(
                    session_id=clean_session_id,
                    source_id=active_source_id,
                    require_active=False,
                )
                if active_metadata.source_revision != active_revision:
                    return ()
                self._validate_generation_integrity(active_metadata)
            except ConfigSourceError:
                return ()
            session_dir = self._session_dir(clean_session_id)
            try:
                self._assert_controlled_directory(session_dir)
            except ConfigSourceError:
                return ()
            for source_dir in sorted(session_dir.iterdir(), key=lambda item: item.name):
                source_id = source_dir.name
                if (
                    not _SOURCE_ID.fullmatch(source_id)
                    or source_id == active_source_id
                    or source_id in referenced
                    or not source_dir.is_dir()
                    or _is_reparse(source_dir)
                ):
                    continue
                manifest_path = source_dir / "manifest.json"
                try:
                    self._assert_controlled_path(manifest_path)
                    manifest = self._read_json_object(manifest_path)
                    metadata = self._metadata_from_manifest(manifest_path, manifest)
                    if metadata.source_id != source_id:
                        continue
                    self._validate_generation_integrity(metadata)
                    owned_paths = [
                        self._owned_path(source_dir, name)
                        for name in sorted(metadata.owned_names)
                    ]
                except Exception:
                    continue
                pending_paths.extend(
                    path for path in owned_paths if path != manifest_path
                )
                pending_paths.append(manifest_path)
                pending_source_ids.append(source_id)
            try:
                self._files.unlink_many(pending_paths)
            except SecureFilesystemError:
                return ()
        return tuple(pending_source_ids)

    def _validate_generation_integrity(
        self,
        metadata: _ConfigSourceMetadata,
    ) -> None:
        try:
            manifest = _metadata_manifest(metadata)
            if _canonical_source_revision(manifest) != metadata.source_revision:
                raise ConfigSourceInvalidError()
            for filename, inventory_entry in sorted(metadata.file_inventory.items()):
                path = self._owned_path(metadata.manifest_path.parent, filename)
                content = self._files.read_bytes(path)
                self._validate_inventory_content(metadata, filename, content)
                if inventory_entry["role"] != "source":
                    _image_type(content)
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceInvalidError() from None

    def _validate_inventory_content(
        self,
        metadata: _ConfigSourceMetadata,
        filename: str,
        content: bytes,
    ) -> None:
        inventory_entry = metadata.file_inventory.get(filename)
        if (
            not isinstance(inventory_entry, dict)
            or len(content) != inventory_entry.get("size_bytes")
            or hashlib.sha256(content).hexdigest()
            != inventory_entry.get("sha256")
        ):
            raise ConfigSourceInvalidError()

    def _parse_docx(
        self,
        source_path: Path,
        source_dir: Path,
        registry: _OwnedFileRegistry,
    ) -> tuple[
        list[dict[str, Any]],
        str,
        dict[str, dict[str, str | None]],
        list[str],
    ]:
        file_bytes = self._files.read_bytes(source_path)
        _validate_docx_archive(
            file_bytes,
            max_member_bytes=self.max_docx_member_bytes,
            max_expanded_bytes=self.max_docx_expanded_bytes,
        )
        parser_root = source_dir / "p"
        self._files.ensure_directory(parser_root)
        parser_io_root = _extended_length_path(parser_root)
        parser_registry = _OwnedFileRegistry(self._files)

        def write_parser_asset(path: Path, content: bytes) -> None:
            parser_registry.register(path)
            self._files.ensure_directory(path.parent)
            self._files.atomic_write_bytes(path, content)

        try:
            document_text = extract_docx_text(file_bytes)
            blocks = parse_docx_question_blocks(
                file_bytes,
                fallback_doc_text=document_text,
                temporary_root=parser_io_root,
                asset_root=parser_io_root / "assets",
                register_created_file=parser_registry.register,
                write_created_file=write_parser_asset,
            )
            for block in blocks:
                if isinstance(block, dict):
                    block.setdefault("semantic_source", "text")
            private_blocks, asset_files = _copy_docx_assets(
                blocks,
                source_dir=source_dir,
                parser_root=parser_io_root,
                registry=registry,
                filesystem=self._files,
            )
            return private_blocks, document_text, asset_files, []
        finally:
            parser_registry.cleanup()

    def _parse_pdf(
        self,
        source_path: Path,
        source_dir: Path,
        registry: _OwnedFileRegistry,
    ) -> tuple[
        list[dict[str, Any]],
        str,
        dict[str, dict[str, str | None]],
        list[str],
    ]:
        try:
            import fitz

            file_bytes = self._files.read_bytes(source_path)
            document = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                page_count = len(document)
            finally:
                document.close()
        except Exception:
            raise ConfigSourceInvalidError() from None
        if page_count > self.max_pdf_pages:
            raise ConfigSourceTooLargeError()
        if page_count <= 0:
            raise ConfigSourceInvalidError()

        from rubric_auto_cropper import (
            extract_pdf_images,
            extract_pdf_question_images,
        )

        document_text = extract_pdf_text(file_bytes)
        blocks = parse_plain_question_blocks(document_text)
        for block in blocks:
            if isinstance(block, dict):
                block["semantic_source"] = "images"
        raw_assets = extract_pdf_question_images(file_bytes, blocks) if blocks else {}
        raw_pages = extract_pdf_images(file_bytes)
        asset_files: dict[str, dict[str, str | None]] = {}
        known_ids = {str(block.get("question_id") or "") for block in blocks}
        for question_id, values in raw_assets.items():
            if question_id not in known_ids or not isinstance(values, dict):
                continue
            entry: dict[str, str | None] = {"question": None, "answer": None}
            for kind in ("question", "answer"):
                content = values.get(kind)
                if not isinstance(content, bytes) or not content:
                    continue
                suffix, _media_type = _image_type(content)
                output = source_dir / f"asset-{question_id}-{kind}{suffix}"
                _write_bytes_atomic(
                    output,
                    content,
                    registry=registry,
                    filesystem=self._files,
                )
                entry[kind] = output.name
            if entry["question"] or entry["answer"]:
                asset_files[question_id] = entry
        whole_page_files: list[str] = []
        for index, content in enumerate(raw_pages, start=1):
            if not isinstance(content, bytes) or not content:
                raise ConfigSourceInvalidError()
            suffix, _media_type = _image_type(content)
            output = source_dir / f"whole-page-{index:04d}{suffix}"
            _write_bytes_atomic(
                output,
                content,
                registry=registry,
                filesystem=self._files,
            )
            whole_page_files.append(output.name)
        return blocks, document_text, asset_files, whole_page_files

    def _record_from_manifest(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> ConfigSourceRecord:
        return self._record_from_metadata(
            self._metadata_from_manifest(manifest_path, manifest)
        )

    def _metadata_from_manifest(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> _ConfigSourceMetadata:
        self._assert_controlled_path(manifest_path)
        expected_manifest_keys = {
            "version",
            "session_id",
            "source_id",
            "source_revision",
            "safe_filename",
            "suffix",
            "size_bytes",
            "sha256",
            "parse_state",
            "source_file",
            "owned_files",
            "file_inventory",
            "questions",
            "private_blocks",
            "private_document_text",
            "asset_files",
            "whole_page_files",
        }
        if set(manifest) != expected_manifest_keys:
            raise ConfigSourceInvalidError()
        session_id = int(manifest.get("session_id"))
        source_id = str(manifest.get("source_id") or "")
        source_revision = str(manifest.get("source_revision") or "")
        safe_filename = str(manifest.get("safe_filename") or "")
        suffix = str(manifest.get("suffix") or "")
        sha256 = str(manifest.get("sha256") or "")
        size_bytes = int(manifest.get("size_bytes"))
        if (
            manifest.get("version") != 2
            or manifest.get("parse_state") != "ready"
            or session_id <= 0
            or not _SOURCE_ID.fullmatch(source_id)
            or not _SOURCE_REVISION.fullmatch(source_revision)
            or not _SHA256.fullmatch(sha256)
            or suffix not in {".docx", ".pdf"}
            or size_bytes <= 0
            or _safe_filename(safe_filename) != (safe_filename, suffix)
            or manifest_path.parent.name != source_id
            or manifest_path.parent.parent.name != f"session-{session_id}"
        ):
            raise ConfigSourceInvalidError()
        owned_names = manifest.get("owned_files")
        if (
            not isinstance(owned_names, list)
            or "manifest.json" not in owned_names
            or len(owned_names) != len(set(owned_names))
            or owned_names != sorted(owned_names)
        ):
            raise ConfigSourceInvalidError()
        owned = frozenset(
            self._owned_path(manifest_path.parent, name).name for name in owned_names
        )
        source_file = str(manifest.get("source_file") or "")
        if source_file not in owned or source_file == "manifest.json":
            raise ConfigSourceInvalidError()
        source_path = self._owned_path(manifest_path.parent, source_file)
        if not source_path.is_file():
            raise ConfigSourceInvalidError()

        raw_questions = manifest.get("questions")
        if not isinstance(raw_questions, list) or len(raw_questions) > MAX_PDF_PAGES:
            raise ConfigSourceInvalidError()
        questions = tuple(_question_from_dict(item) for item in raw_questions)
        private_blocks = manifest.get("private_blocks")
        if not isinstance(private_blocks, list) or any(
            not isinstance(block, dict) for block in private_blocks
        ):
            raise ConfigSourceInvalidError()
        document_text = manifest.get("private_document_text")
        if not isinstance(document_text, str):
            raise ConfigSourceInvalidError()

        asset_files = manifest.get("asset_files")
        if not isinstance(asset_files, dict):
            raise ConfigSourceInvalidError()
        normalized_assets: dict[str, dict[str, str | None]] = {}
        for question_id, raw_entry in asset_files.items():
            if (
                not _QUESTION_ID.fullmatch(str(question_id))
                or not isinstance(raw_entry, dict)
                or set(raw_entry) != {"question", "answer"}
            ):
                raise ConfigSourceInvalidError()
            normalized: dict[str, str | None] = {"question": None, "answer": None}
            for kind in ("question", "answer"):
                filename = raw_entry.get(kind)
                if filename is None:
                    continue
                if not isinstance(filename, str) or filename not in owned:
                    raise ConfigSourceInvalidError()
                asset_path = self._owned_path(manifest_path.parent, filename)
                if not asset_path.is_file():
                    raise ConfigSourceInvalidError()
                normalized[kind] = filename
            if normalized["question"] or normalized["answer"]:
                normalized_assets[str(question_id)] = normalized

        whole_page_files = manifest.get("whole_page_files")
        if (
            not isinstance(whole_page_files, list)
            or len(whole_page_files) > self.max_pdf_pages
            or len(whole_page_files) != len(set(whole_page_files))
        ):
            raise ConfigSourceInvalidError()
        normalized_whole_pages: list[str] = []
        for filename in whole_page_files:
            if not isinstance(filename, str) or filename not in owned:
                raise ConfigSourceInvalidError()
            page_path = self._owned_path(manifest_path.parent, filename)
            if not page_path.is_file():
                raise ConfigSourceInvalidError()
            normalized_whole_pages.append(filename)
        block_question_ids = {
            str(block.get("question_id") or "").strip() for block in private_blocks
        }
        if not set(normalized_assets).issubset(block_question_ids):
            raise ConfigSourceInvalidError()
        normalized_questions = _question_previews(private_blocks, normalized_assets)
        if normalized_questions != questions:
            raise ConfigSourceInvalidError()

        expected_roles = _expected_inventory_roles(
            source_dir=manifest_path.parent,
            source_file=source_file,
            private_blocks=private_blocks,
            asset_files=normalized_assets,
            whole_page_files=normalized_whole_pages,
        )
        if owned != frozenset({*expected_roles, "manifest.json"}):
            raise ConfigSourceInvalidError()
        raw_inventory = manifest.get("file_inventory")
        if not isinstance(raw_inventory, dict) or set(raw_inventory) != set(expected_roles):
            raise ConfigSourceInvalidError()
        file_inventory: dict[str, dict[str, Any]] = {}
        for filename, expected_role in expected_roles.items():
            raw_entry = raw_inventory.get(filename)
            expected_keys = {*expected_role, "size_bytes", "sha256"}
            if not isinstance(raw_entry, dict) or set(raw_entry) != expected_keys:
                raise ConfigSourceInvalidError()
            size = raw_entry.get("size_bytes")
            file_sha256 = raw_entry.get("sha256")
            if (
                any(raw_entry.get(key) != value for key, value in expected_role.items())
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size <= 0
                or not isinstance(file_sha256, str)
                or not _SHA256.fullmatch(file_sha256)
            ):
                raise ConfigSourceInvalidError()
            file_inventory[filename] = {
                **expected_role,
                "size_bytes": size,
                "sha256": file_sha256,
            }
        source_inventory = file_inventory.get(source_file)
        if source_inventory != {
            "role": "source",
            "size_bytes": size_bytes,
            "sha256": sha256,
        }:
            raise ConfigSourceInvalidError()

        normalized_manifest = {
            "version": 2,
            "session_id": session_id,
            "source_id": source_id,
            "source_revision": source_revision,
            "safe_filename": safe_filename,
            "suffix": suffix,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "parse_state": "ready",
            "source_file": source_file,
            "owned_files": list(owned_names),
            "file_inventory": file_inventory,
            "questions": [asdict(question) for question in questions],
            "private_blocks": private_blocks,
            "private_document_text": document_text,
            "asset_files": normalized_assets,
            "whole_page_files": normalized_whole_pages,
        }
        if _canonical_source_revision(normalized_manifest) != source_revision:
            raise ConfigSourceInvalidError()
        return _ConfigSourceMetadata(
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
            safe_filename=safe_filename,
            suffix=suffix,
            size_bytes=size_bytes,
            sha256=sha256,
            questions=questions,
            manifest_path=manifest_path,
            source_path=source_path,
            owned_names=owned,
            file_inventory=file_inventory,
            private_blocks=tuple(copy.deepcopy(private_blocks)),
            private_document_text=document_text,
            asset_files=normalized_assets,
            whole_page_files=tuple(normalized_whole_pages),
        )

    def _record_from_metadata(
        self,
        metadata: _ConfigSourceMetadata,
    ) -> ConfigSourceRecord:
        source_content = self._files.read_bytes(metadata.source_path)
        self._validate_inventory_content(
            metadata,
            metadata.source_path.name,
            source_content,
        )
        private_images: dict[str, dict[str, str | None]] = {}
        for question_id, entry in metadata.asset_files.items():
            encoded: dict[str, str | None] = {"question": None, "answer": None}
            for kind in ("question", "answer"):
                filename = entry.get(kind)
                if filename is None:
                    continue
                content = self._owned_path(
                    metadata.manifest_path.parent,
                    filename,
                )
                content = self._files.read_bytes(content)
                self._validate_inventory_content(metadata, filename, content)
                _image_type(content)
                encoded[kind] = base64.b64encode(content).decode("ascii")
            private_images[question_id] = encoded
        whole_pages: list[bytes] = []
        for filename in metadata.whole_page_files:
            content = self._owned_path(
                metadata.manifest_path.parent,
                filename,
            )
            content = self._files.read_bytes(content)
            self._validate_inventory_content(metadata, filename, content)
            _image_type(content)
            whole_pages.append(content)
        private_blocks = copy.deepcopy(list(metadata.private_blocks))
        if metadata.suffix == ".pdf":
            for block in private_blocks:
                block["semantic_source"] = "images"
        else:
            for block in private_blocks:
                block.setdefault("semantic_source", "text")
        return ConfigSourceRecord(
            session_id=metadata.session_id,
            source_id=metadata.source_id,
            source_revision=metadata.source_revision,
            safe_filename=metadata.safe_filename,
            suffix=metadata.suffix,
            size_bytes=metadata.size_bytes,
            sha256=metadata.sha256,
            questions=metadata.questions,
            manifest_path=metadata.manifest_path,
            private_source_path=metadata.source_path,
            private_source_bytes=source_content,
            private_blocks=tuple(private_blocks),
            private_document_text=metadata.private_document_text,
            private_question_images=private_images,
            private_whole_page_images=tuple(whole_pages),
        )

    def _prepare_source_dir(self, source_dir: Path) -> None:
        config_sources = source_dir.parent.parent
        session_dir = source_dir.parent
        for directory in (config_sources, session_dir, source_dir):
            self._assert_controlled_path(directory)
            try:
                self._files.ensure_directory(directory)
            except SecureFilesystemError:
                raise ConfigSourceInvalidError() from None
            self._assert_controlled_directory(directory)

    def _assert_controlled_directory(self, directory: Path) -> None:
        self._assert_controlled_path(directory)
        if not directory.is_dir():
            raise ConfigSourceInvalidError()

    def _assert_controlled_path(self, path: Path) -> None:
        candidate = Path(path)
        try:
            relative = candidate.relative_to(self.upload_config_dir)
        except ValueError:
            raise ConfigSourceInvalidError() from None
        trusted_root = self.upload_config_dir.resolve(strict=False)
        current = self.upload_config_dir
        for part in relative.parts:
            current = current / part
            if _is_reparse(current):
                raise ConfigSourceInvalidError()
            try:
                resolved = current.resolve(strict=False)
            except OSError:
                raise ConfigSourceInvalidError() from None
            if not resolved.is_relative_to(trusted_root):
                raise ConfigSourceInvalidError()

    def _owned_path(self, source_dir: Path, name: Any) -> Path:
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in {".", ".."}
        ):
            raise ConfigSourceInvalidError()
        self._assert_controlled_directory(source_dir)
        path = source_dir / name
        self._assert_controlled_path(path)
        if path.resolve(strict=False).parent != source_dir.resolve(strict=False):
            raise ConfigSourceInvalidError()
        return path

    def _read_active(self, session_id: int) -> tuple[str, str]:
        active_path = self._active_path(session_id)
        self._assert_controlled_path(active_path)
        if not active_path.is_file():
            raise ConfigSourceChangedError()
        try:
            active = self._read_json_object(active_path)
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceChangedError() from None
        source_id = str(active.get("source_id") or "")
        source_revision = str(active.get("source_revision") or "")
        if (
            not _SOURCE_ID.fullmatch(source_id)
            or not _SOURCE_REVISION.fullmatch(source_revision)
        ):
            raise ConfigSourceChangedError()
        return source_id, source_revision

    def _read_json_object(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(
                self._files.read_text(
                    path,
                    encoding="utf-8",
                    max_bytes=self.max_manifest_bytes,
                )
            )
        except (SecureFilesystemError, json.JSONDecodeError, UnicodeError):
            raise ConfigSourceInvalidError() from None
        if not isinstance(payload, dict):
            raise ConfigSourceInvalidError()
        return payload

    def _session_dir(self, session_id: int) -> Path:
        return self.upload_config_dir / "config_sources" / f"session-{int(session_id)}"

    def _source_dir(self, session_id: int, source_id: str) -> Path:
        return self._session_dir(session_id) / source_id

    def _active_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "active.json"

    def _submission_path(self, session_id: int, request_token: str) -> Path:
        return self._session_dir(session_id) / f"submission-{request_token}.json"


def _positive_session_id(value: int) -> int:
    try:
        clean = int(value)
    except (TypeError, ValueError):
        raise ConfigSourceInvalidError() from None
    if clean <= 0:
        raise ConfigSourceInvalidError()
    return clean


def _request_token(value: str) -> str:
    clean = str(value or "").strip()
    if not _SOURCE_ID.fullmatch(clean):
        raise ConfigSourceInvalidError()
    return clean


def _submission_request_fingerprint(
    *,
    filename: str,
    content_length: int | None,
) -> str:
    safe_filename, suffix = _safe_filename(filename)
    if content_length is None:
        clean_length = None
    else:
        try:
            clean_length = int(content_length)
        except (TypeError, ValueError):
            raise ConfigSourceInvalidError() from None
        if clean_length < 0:
            raise ConfigSourceInvalidError()
    canonical = json.dumps(
        {
            "safe_filename": safe_filename,
            "suffix": suffix,
            "content_length": clean_length,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_filename(filename: str) -> tuple[str, Literal[".docx", ".pdf"]]:
    raw = str(filename or "").strip().replace("\\", "/")
    safe = raw.rsplit("/", 1)[-1].strip()
    if not safe or safe in {".", ".."} or len(safe) > 255 or "\x00" in safe:
        raise ConfigSourceInvalidError()
    suffix = Path(safe).suffix.casefold()
    if suffix not in {".docx", ".pdf"}:
        raise ConfigSourceTypeUnsupportedError()
    return safe, suffix


def _validate_magic(prefix: bytes, suffix: str) -> None:
    if suffix == ".docx" and not prefix.startswith(b"PK\x03\x04"):
        raise ConfigSourceTypeUnsupportedError()
    if suffix == ".pdf" and b"%PDF-" not in prefix:
        raise ConfigSourceTypeUnsupportedError()


def _validate_docx_archive(
    content: bytes,
    *,
    max_member_bytes: int,
    max_expanded_bytes: int,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ConfigSourceInvalidError()
            expanded = 0
            for member in members:
                if member.is_dir():
                    continue
                size = int(member.file_size)
                if size > max_member_bytes:
                    raise ConfigSourceTooLargeError()
                expanded += size
                if expanded > max_expanded_bytes:
                    raise ConfigSourceTooLargeError()
    except ConfigSourceError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise ConfigSourceInvalidError() from None


def _canonical_source_revision(manifest: dict[str, Any]) -> str:
    payload = {
        key: manifest[key]
        for key in (
            "version",
            "session_id",
            "source_id",
            "safe_filename",
            "suffix",
            "size_bytes",
            "sha256",
            "parse_state",
            "source_file",
            "owned_files",
            "file_inventory",
            "questions",
            "private_blocks",
            "private_document_text",
            "asset_files",
            "whole_page_files",
        )
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError):
        raise ConfigSourceInvalidError() from None
    return hashlib.sha256(canonical).hexdigest()


def _metadata_manifest(metadata: _ConfigSourceMetadata) -> dict[str, Any]:
    return {
        "version": 2,
        "session_id": metadata.session_id,
        "source_id": metadata.source_id,
        "source_revision": metadata.source_revision,
        "safe_filename": metadata.safe_filename,
        "suffix": metadata.suffix,
        "size_bytes": metadata.size_bytes,
        "sha256": metadata.sha256,
        "parse_state": "ready",
        "source_file": metadata.source_path.name,
        "owned_files": sorted(metadata.owned_names),
        "file_inventory": copy.deepcopy(metadata.file_inventory),
        "questions": [asdict(question) for question in metadata.questions],
        "private_blocks": copy.deepcopy(list(metadata.private_blocks)),
        "private_document_text": metadata.private_document_text,
        "asset_files": copy.deepcopy(metadata.asset_files),
        "whole_page_files": list(metadata.whole_page_files),
    }


def _expected_inventory_roles(
    *,
    source_dir: Path,
    source_file: str,
    private_blocks: Sequence[dict[str, Any]],
    asset_files: dict[str, dict[str, str | None]],
    whole_page_files: Sequence[str],
) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}

    def register(filename: str, role: dict[str, Any]) -> None:
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or filename == "manifest.json"
        ):
            raise ConfigSourceInvalidError()
        existing = roles.get(filename)
        if existing is not None and existing != role:
            raise ConfigSourceInvalidError()
        roles[filename] = role

    register(source_file, {"role": "source"})
    for block in private_blocks:
        question_id = str(block.get("question_id") or "").strip()
        if not _QUESTION_ID.fullmatch(question_id):
            raise ConfigSourceInvalidError()
        for kind in ("question", "answer"):
            role = {
                "role": "question_asset",
                "question_id": question_id,
                "asset_kind": kind,
            }
            for raw_path in _block_image_paths(block, kind):
                register(_semantic_owned_name(raw_path, source_dir), role)
    for question_id, entry in asset_files.items():
        if not _QUESTION_ID.fullmatch(str(question_id)):
            raise ConfigSourceInvalidError()
        for kind in ("question", "answer"):
            filename = entry.get(kind)
            if filename is not None:
                register(
                    filename,
                    {
                        "role": "question_asset",
                        "question_id": str(question_id),
                        "asset_kind": kind,
                    },
                )
    for page_index, filename in enumerate(whole_page_files, start=1):
        register(
            filename,
            {"role": "whole_page", "page_index": page_index},
        )
    return roles


def _semantic_owned_name(raw_path: str, source_dir: Path) -> str:
    try:
        clean = Path(os.path.abspath(str(raw_path)))
        clean_root = Path(os.path.abspath(str(source_dir)))
    except (OSError, TypeError, ValueError):
        raise ConfigSourceInvalidError() from None
    if os.path.normcase(str(clean.parent)) != os.path.normcase(str(clean_root)):
        raise ConfigSourceInvalidError()
    return clean.name


def _copy_docx_assets(
    blocks: Sequence[dict[str, Any]],
    *,
    source_dir: Path,
    parser_root: Path,
    registry: _OwnedFileRegistry,
    filesystem: SecureRootFilesystem,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, str | None]],
]:
    private_blocks = copy.deepcopy(list(blocks))
    asset_files: dict[str, dict[str, str | None]] = {}
    parser_resolved = parser_root.resolve(strict=False)
    for block_index, block in enumerate(private_blocks, start=1):
        question_id = str(block.get("question_id") or "").strip()
        if not _QUESTION_ID.fullmatch(question_id):
            raise ConfigSourceInvalidError()
        entry: dict[str, str | None] = {"question": None, "answer": None}
        replacements: dict[str, str] = {}
        by_kind = {
            "question": _block_image_paths(block, "question"),
            "answer": _block_image_paths(block, "answer"),
        }
        for kind, raw_paths in by_kind.items():
            copied_index = 0
            for raw_path in raw_paths:
                path = Path(raw_path)
                try:
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(parser_resolved) or _is_reparse(path):
                        continue
                    content = filesystem.read_bytes(path)
                    suffix, _media_type = _image_type(content)
                except Exception:
                    continue
                output = source_dir / (
                    f"asset-{question_id}-{kind}-{copied_index + 1}{suffix}"
                )
                _write_bytes_atomic(
                    output,
                    content,
                    registry=registry,
                    filesystem=filesystem,
                )
                replacements[raw_path] = str(output)
                copied_index += 1
                if entry[kind] is None:
                    entry[kind] = output.name
            if copied_index == 0:
                entry[kind] = None
        if entry["question"] or entry["answer"]:
            asset_files[question_id] = entry
        _replace_block_paths(block, replacements)
    return private_blocks, asset_files


def _block_image_paths(block: dict[str, Any], kind: str) -> list[str]:
    values: list[str] = []
    if kind == "question":
        raw_list = block.get("image_paths")
        if isinstance(raw_list, list):
            values.extend(str(item) for item in raw_list if str(item).strip())
        fields = ("question_html",)
    else:
        fields = ("answer_html", "analysis_html")
    for field in fields:
        text = str(block.get(field) or "")
        values.extend(match.group("path").strip() for match in _IMAGE_PATH_MARKER.finditer(text))
    return list(dict.fromkeys(value for value in values if value))


def _question_asset_filenames(
    metadata: _ConfigSourceMetadata,
    *,
    question_id: str,
    asset_kind: str,
) -> list[str]:
    block = next(
        (
            item
            for item in metadata.private_blocks
            if str(item.get("question_id") or "").strip() == question_id
        ),
        None,
    )
    filenames: list[str] = []
    if isinstance(block, dict):
        filenames.extend(
            _semantic_owned_name(raw_path, metadata.manifest_path.parent)
            for raw_path in _block_image_paths(block, asset_kind)
        )
    entry = metadata.asset_files.get(question_id)
    legacy_filename = entry.get(asset_kind) if isinstance(entry, dict) else None
    if isinstance(legacy_filename, str) and legacy_filename:
        filenames.append(legacy_filename)
    return list(dict.fromkeys(filenames))


def _replace_block_paths(block: dict[str, Any], replacements: dict[str, str]) -> None:
    raw_paths = block.get("image_paths")
    if isinstance(raw_paths, list):
        block["image_paths"] = [
            replacements[str(path)] for path in raw_paths if str(path) in replacements
        ]
    for field in ("question_html", "answer_html", "analysis_html"):
        if field not in block:
            continue
        text = str(block.get(field) or "")
        text = _IMAGE_PATH_MARKER.sub(
            lambda match: (
                f"[[IMAGE:{replacements[match.group('path').strip()]}]]"
                if match.group("path").strip() in replacements
                else ""
            ),
            text,
        )
        block[field] = text


def _public_questions_with_rich_content(
    questions: Sequence[ConfigQuestionPreview],
    private_blocks: Sequence[dict[str, Any]],
    *,
    session_id: int,
    source_id: str,
    source_suffix: Literal[".docx", ".pdf"],
) -> list[dict[str, Any]]:
    blocks_by_id = {
        str(block.get("question_id") or "").strip(): block
        for block in private_blocks
        if str(block.get("question_id") or "").strip()
    }
    result: list[dict[str, Any]] = []
    for question in questions:
        payload = asdict(question)
        block = blocks_by_id.get(question.question_id)
        image_semantic_source = source_suffix == ".pdf" or (
            isinstance(block, dict)
            and str(block.get("semantic_source") or "").strip() == "images"
        )
        if image_semantic_source:
            payload.update(
                {
                    "question_preview": "",
                    "answer_preview": "",
                    "answer_present": question.has_answer_asset,
                    "needs_review": True,
                    "local_answer_trusted": False,
                }
            )
        payload["rich_content"] = _config_rich_content(
            block,
            session_id=session_id,
            source_id=source_id,
            question_id=question.question_id,
            has_question_asset=question.has_question_asset,
            has_answer_asset=question.has_answer_asset,
            force_image_semantics=image_semantic_source,
        )
        result.append(payload)
    return result


def _config_rich_content(
    block: dict[str, Any] | None,
    *,
    session_id: int,
    source_id: str,
    question_id: str,
    has_question_asset: bool,
    has_answer_asset: bool,
    force_image_semantics: bool = False,
) -> dict[str, Any]:
    image_semantic_source = force_image_semantics or (
        isinstance(block, dict)
        and str(block.get("semantic_source") or "").strip() == "images"
    )
    if not isinstance(block, dict) or image_semantic_source:
        question_blocks: list[dict[str, Any]] = []
        answer_blocks: list[dict[str, Any]] = []
    else:
        question_blocks = _project_config_rich_blocks(
            block.get("question_html")
            or block.get("question_text")
            or block.get("text")
            or ""
        )
        answer_blocks = _deduplicate_config_rich_blocks(
            _project_config_rich_blocks(
                block.get("answer_html")
                or block.get("answer_text")
                or block.get("canonical_answer")
                or ""
            ),
            _project_config_rich_blocks(
                block.get("analysis_html")
                or block.get("analysis")
                or ""
            ),
        )
    question_blocks = _append_config_asset_block(
        question_blocks,
        _config_asset_urls(
            block,
            session_id=session_id,
            source_id=source_id,
            question_id=question_id,
            asset_kind="question",
            has_legacy_asset=has_question_asset,
        ),
    )
    answer_blocks = _append_config_asset_block(
        answer_blocks,
        _config_asset_urls(
            block,
            session_id=session_id,
            source_id=source_id,
            question_id=question_id,
            asset_kind="answer",
            has_legacy_asset=has_answer_asset,
        ),
    )
    return {
        "available": bool(question_blocks or answer_blocks),
        "question_block_count": len(question_blocks),
        "answer_block_count": len(answer_blocks),
        "question_blocks": question_blocks,
        "answer_blocks": answer_blocks,
    }


def _config_asset_urls(
    block: dict[str, Any] | None,
    *,
    session_id: int,
    source_id: str,
    question_id: str,
    asset_kind: Literal["question", "answer"],
    has_legacy_asset: bool,
) -> list[str]:
    marker_count = (
        len(_block_image_paths(block, asset_kind))
        if isinstance(block, dict)
        else 0
    )
    asset_count = marker_count or int(has_legacy_asset)
    base = (
        f"/api/sessions/{session_id}/config/sources/{source_id}"
        f"/questions/{question_id}/assets/{asset_kind}"
    )
    return [f"{base}/{asset_index}" for asset_index in range(asset_count)]


def _append_config_asset_block(
    blocks: list[dict[str, Any]],
    asset_urls: Sequence[str],
) -> list[dict[str, Any]]:
    if not asset_urls:
        return blocks
    result = list(blocks)
    result.append(
        {
            "kind": "paragraph",
            "text": "",
            "segments": [],
            "rows": [],
            "asset_indexes": list(range(len(asset_urls))),
            "asset_urls": list(asset_urls),
        }
    )
    return result


def _project_config_rich_blocks(value: Any) -> list[dict[str, Any]]:
    source = _IMAGE_PATH_MARKER.sub("", str(value or ""))
    if not source.strip():
        return []
    chunks: list[str] = []
    cursor = 0
    for match in _RICH_TABLE_PATTERN.finditer(source):
        chunks.extend(
            line
            for line in source[cursor : match.start()].splitlines()
            if line.strip()
        )
        chunks.append(match.group(0))
        cursor = match.end()
    chunks.extend(line for line in source[cursor:].splitlines() if line.strip())
    if not chunks and source.strip():
        chunks.append(source)

    result: list[dict[str, Any]] = []
    for raw_chunk in chunks:
        chunk = str(raw_chunk or "").strip()
        if not chunk:
            continue
        structured = _structured_config_rich_text(chunk)
        result.append(
            {
                "kind": structured["kind"],
                "text": chunk,
                "segments": structured["segments"],
                "rows": structured["rows"],
                "asset_indexes": [],
                "asset_urls": [],
            }
        )
    return result


def _deduplicate_config_rich_blocks(
    *groups: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for block in group:
            signature = json.dumps(
                {
                    "kind": block.get("kind"),
                    "text": block.get("text"),
                    "segments": block.get("segments"),
                    "rows": block.get("rows"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            result.append(block)
    return result


def _structured_config_rich_text(text: str) -> dict[str, Any]:
    normalized = str(text or "")
    if normalized.casefold().startswith("<table"):
        rows: list[dict[str, Any]] = []
        for row_match in _RICH_TABLE_ROW_PATTERN.finditer(normalized):
            cells = [
                {"segments": _config_rich_inline_segments(cell_match.group(1))}
                for cell_match in _RICH_TABLE_CELL_PATTERN.finditer(
                    row_match.group(1)
                )
            ]
            if cells:
                rows.append({"cells": cells})
        if rows:
            return {"kind": "table", "segments": [], "rows": rows}
    return {
        "kind": "paragraph",
        "segments": _config_rich_inline_segments(normalized),
        "rows": [],
    }


def _config_rich_inline_segments(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    superscript_depth = 0
    subscript_depth = 0
    underline_depth = 0

    def append_segment(value: str, *, line_break: bool = False) -> None:
        decoded = html.unescape(value)
        if not decoded and not line_break:
            return
        segment = {
            "text": decoded,
            "superscript": superscript_depth > 0,
            "subscript": subscript_depth > 0,
            "underline": underline_depth > 0,
            "line_break": line_break,
        }
        if (
            segments
            and not line_break
            and not segments[-1]["line_break"]
            and all(
                segments[-1][key] == segment[key]
                for key in ("superscript", "subscript", "underline")
            )
        ):
            segments[-1]["text"] += decoded
        else:
            segments.append(segment)

    cursor = 0
    for match in _RICH_INLINE_TOKEN_PATTERN.finditer(str(text or "")):
        append_segment(text[cursor : match.start()])
        token = match.group(0).casefold()
        if token.startswith("<br"):
            append_segment("", line_break=True)
        elif token == "<sup>":
            superscript_depth += 1
        elif token == "</sup>":
            superscript_depth = max(0, superscript_depth - 1)
        elif token == "<sub>":
            subscript_depth += 1
        elif token == "</sub>":
            subscript_depth = max(0, subscript_depth - 1)
        elif token == "<u>":
            underline_depth += 1
        elif token == "</u>":
            underline_depth = max(0, underline_depth - 1)
        cursor = match.end()
    append_segment(text[cursor:])
    return segments


def _question_previews(
    blocks: Sequence[dict[str, Any]],
    asset_files: dict[str, dict[str, str | None]],
) -> tuple[ConfigQuestionPreview, ...]:
    questions: list[ConfigQuestionPreview] = []
    seen: set[str] = set()
    for block in blocks:
        question_id = str(block.get("question_id") or "").strip()
        if not _QUESTION_ID.fullmatch(question_id) or question_id in seen:
            raise ConfigSourceInvalidError()
        seen.add(question_id)
        question_value = (
            block.get("question_html")
            or block.get("question_text")
            or block.get("text")
            or ""
        )
        answer_value = (
            block.get("answer_html")
            or block.get("answer_text")
            or block.get("canonical_answer")
            or ""
        )
        assets = asset_files.get(question_id, {})
        image_semantic_source = (
            str(block.get("semantic_source") or "").strip() == "images"
        )
        questions.append(
            ConfigQuestionPreview(
                question_id=question_id,
                question_type=str(block.get("question_type") or "comprehensive"),
                question_preview=(
                    "" if image_semantic_source else _public_preview(question_value)
                ),
                answer_preview=(
                    "" if image_semantic_source else _public_preview(answer_value)
                ),
                answer_present=(
                    bool(assets.get("answer"))
                    if image_semantic_source
                    else bool(
                        str(block.get("answer_text") or "").strip()
                        or str(block.get("canonical_answer") or "").strip()
                        or str(block.get("analysis") or "").strip()
                        or str(block.get("analysis_html") or "").strip()
                        or assets.get("answer")
                    )
                ),
                needs_review=bool(block.get("needs_review")) or image_semantic_source,
                local_answer_trusted=(
                    False
                    if image_semantic_source
                    else bool(block.get("local_answer_trusted"))
                ),
                has_question_asset=bool(assets.get("question")),
                has_answer_asset=bool(assets.get("answer")),
            )
        )
    return tuple(questions)


def _public_preview(value: Any) -> str:
    text = _IMAGE_MARKER.sub(" ", str(value or ""))
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    text = " ".join(text.split())
    return text[:PUBLIC_PREVIEW_CHARACTERS]


def _question_from_dict(value: Any) -> ConfigQuestionPreview:
    if not isinstance(value, dict):
        raise ConfigSourceInvalidError()
    try:
        question = ConfigQuestionPreview(
            question_id=str(value["question_id"]),
            question_type=str(value["question_type"]),
            question_preview=str(value["question_preview"]),
            answer_preview=str(value["answer_preview"]),
            answer_present=value["answer_present"] is True,
            needs_review=value["needs_review"] is True,
            local_answer_trusted=value["local_answer_trusted"] is True,
            has_question_asset=value["has_question_asset"] is True,
            has_answer_asset=value["has_answer_asset"] is True,
        )
    except (KeyError, TypeError):
        raise ConfigSourceInvalidError() from None
    if (
        not _QUESTION_ID.fullmatch(question.question_id)
        or len(question.question_preview) > PUBLIC_PREVIEW_CHARACTERS
        or len(question.answer_preview) > PUBLIC_PREVIEW_CHARACTERS
    ):
        raise ConfigSourceInvalidError()
    return question


def _write_bytes_atomic(
    path: Path,
    content: bytes,
    *,
    registry: _OwnedFileRegistry | None = None,
    filesystem: SecureRootFilesystem,
) -> None:
    if registry is not None:
        registry.register(path, manifest_owned=True)
    filesystem.atomic_write_bytes(path, content)


def _image_type(content: bytes) -> tuple[str, str]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
            image_format = str(image.format or "").upper()
    except Exception:
        raise ConfigSourceInvalidError() from None
    result = _IMAGE_FORMATS.get(image_format)
    if result is None:
        raise ConfigSourceInvalidError()
    return result


def _image_dimensions(content: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            image.verify()
    except Exception:
        raise ConfigSourceInvalidError() from None
    if width <= 0 or height <= 0:
        raise ConfigSourceInvalidError()
    return int(width), int(height)


def _is_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(flag and attributes & flag)


def _extended_length_path(path: Path) -> Path:
    resolved = str(path.resolve(strict=False))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return Path(resolved)
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved.lstrip("\\"))
    return Path("\\\\?\\" + resolved)


__all__ = [
    "ConfigAssetNotFoundError",
    "ConfigQuestionPreview",
    "ConfigSourceChangedError",
    "ConfigSourceSubmissionConflictError",
    "ConfigSourceInvalidError",
    "ConfigSourceNotFoundError",
    "ConfigSourceRecord",
    "ConfigSourceService",
    "ConfigSourceTooLargeError",
    "ConfigSourceTypeUnsupportedError",
    "PreparedGenerationInput",
    "QuestionDecision",
    "decode_upload_filename",
]
