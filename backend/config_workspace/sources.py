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
import tempfile
import threading
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, ContextManager, Literal
from urllib.parse import unquote

from PIL import Image

from backend.config_workspace.formula_preview import (
    paragraph_xml_index,
    preview_html_for_text,
)
from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from backend.document_parsing import (
    extract_docx_text,  # noqa: F401  (kept: tests monkeypatch this seam)
    extract_pdf_text,  # noqa: F401  (kept: tests monkeypatch this seam)
    parse_docx_question_blocks,
    parse_plain_question_blocks,
)
from backend.document_parsing.question_blocks import (
    _strip_leading_question_number,
    has_explicit_choice_options,
    has_visible_fill_blank_mark,
    has_visible_stem_fill_blank_mark,
    has_visible_subparts,
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
_HTML_TAG = re.compile(r"</?(?:p|span|div|table|tbody|thead|tr|td|th|b|strong|i|em|img|sup|sub|u|br)\b[^>]*>", re.IGNORECASE)
_QUESTION_SECTION_HEADING = re.compile(
    r"(?:^|\n)\s*(?:[一二三四五六七八九十]+|\d+)\s*[、.．]\s*"
    r"(?:选择|填空|解答|计算|证明|作图)题[^\n]*",
    re.MULTILINE,
)
_HTML_QUESTION_SECTION_HEADING = re.compile(
    r"<p\b[^>]*>\s*(?:<[^>]+>\s*)*(?:[一二三四五六七八九十]+|\d+)\s*"
    r"[、.．]\s*(?:选择|填空|解答|计算|证明|作图)题.*",
    re.IGNORECASE | re.DOTALL,
)
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
_DISPLAY_QUESTION_TYPES = _ALLOWED_QUESTION_TYPES | {"single_choice", "multi_choice"}
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


class ConfigSourceParseError(ConfigSourceInvalidError):
    """A known, actionable parsing failure with a fixed public explanation."""

    def __init__(self, reason: Literal["repeated_numbering", "no_questions", "missing_crops"]) -> None:
        ConfigSourceError.__init__(self, {
            "repeated_numbering": "试卷各分节重复使用大题编号，无法可靠对应答案。请将整卷大题改为连续编号后重新上传。",
            "no_questions": "未识别到可用题目。请检查原卷清晰度及本地 PDF 识别是否可用，再重新上传。",
            "missing_crops": "部分题目缺少完整题干裁图，无法用于分析。请核对原卷题号和版面。",
        }[reason])


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
    question_type_review_required: bool
    question_type_review_reason: str
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
    private_question_images: dict[str, dict[str, str | list[str] | None]]
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
                source_revision=self.source_revision,
                source_suffix=self.suffix,
                source_docx=self.private_source_bytes,
            ),
            "ambiguous_assets": _public_ambiguous_assets(
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
            ),
            "assets": _public_source_assets(
                self.questions,
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
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
    stored_questions: tuple[ConfigQuestionPreview, ...] | None = None

    def public_snapshot(
        self,
        *,
        source_bytes: bytes | None = None,
    ) -> dict[str, Any]:
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
                source_revision=self.source_revision,
                source_suffix=self.suffix,
                source_docx=source_bytes,
            ),
            "ambiguous_assets": _public_ambiguous_assets(
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
            ),
            "assets": _public_source_assets(
                self.questions,
                self.private_blocks,
                session_id=self.session_id,
                source_id=self.source_id,
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
    excluded: bool
    question_type: Literal[
        "choice",
        "fill_blank",
        "calculation",
        "proof",
        "comprehensive",
    ] | None = None
    answer_confirmed: bool = False
    answer_override: str | None = None
    bank_match: Literal["same", "different", "reanalyze"] | None = None
    bank_question_id: int | None = None


@dataclass(frozen=True, slots=True)
class AmbiguousAssetDecision:
    candidate_id: str
    action: Literal["bind", "ignore"]
    question_id: str | None = None
    asset_kind: Literal["question", "answer"] | None = None


@dataclass(frozen=True, slots=True)
class PreparedGenerationInput:
    confirmed_blocks: tuple[dict[str, Any], ...]
    document_text: str
    question_images: dict[str, dict[str, str | list[str] | None]]
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
        self._metadata_cache: OrderedDict[Path, tuple[dict[str, Any], _ConfigSourceMetadata]] = OrderedDict()
        self._metadata_cache_lock = threading.Lock()
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
            self._enforce_asset_budget(
                source_dir, asset_files, whole_page_files, blocks=blocks
            )
            for block in blocks:
                _strip_embedded_question_section_heading(block)
                _align_question_type_with_visible_blank(block)
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
            if len((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > self.max_manifest_bytes:
                # Preview acceleration must not reduce the accepted document
                # size. Large sources can use the existing read-time fallback.
                for block in blocks:
                    block.pop("_word_paragraphs", None)
                source_revision = _canonical_source_revision(manifest)
                manifest["source_revision"] = source_revision
            if len((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > self.max_manifest_bytes:
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
            from question_bank.parsers.type_detector import RepeatedQuestionNumberError
            try:
                if suffix == ".docx":
                    result = self._parse_docx(source_path, source_dir, registry)
                else:
                    result = self._parse_pdf(source_path, source_dir, registry)
            except RepeatedQuestionNumberError:
                raise ConfigSourceParseError("repeated_numbering") from None
            if not result[0]:
                raise ConfigSourceParseError("no_questions")
            return result

    def _enforce_asset_budget(
        self,
        source_dir: Path,
        asset_files: dict[str, dict[str, str | None]],
        whole_page_files: Sequence[str],
        *,
        blocks: Sequence[dict[str, Any]] = (),
    ) -> None:
        names = [
            filename
            for entry in asset_files.values()
            for filename in entry.values()
            if isinstance(filename, str) and filename
        ] + list(whole_page_files) + [
            item["filename"] for item in _ambiguous_assets_from_blocks(blocks)
        ]
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
        metadata = self._load_metadata(
            session_id=session_id,
            source_id=source_id,
            require_active=require_active,
        )
        return metadata.public_snapshot(
            source_bytes=self._snapshot_source_bytes(metadata),
        )

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
        return metadata.public_snapshot(
            source_bytes=self._snapshot_source_bytes(metadata),
        )

    def _snapshot_source_bytes(
        self,
        metadata: _ConfigSourceMetadata,
    ) -> bytes | None:
        if metadata.suffix != ".docx":
            return None
        try:
            return self._files.read_bytes(metadata.source_path)
        except SecureFilesystemError:
            return None

    def load_active_record(self, *, session_id: int) -> ConfigSourceRecord:
        clean_session_id = _positive_session_id(session_id)
        source_id, _source_revision = self._read_active(clean_session_id)
        return self.load(
            session_id=clean_session_id,
            source_id=source_id,
            require_active=True,
        )

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
            with self._metadata_cache_lock:
                cached = self._metadata_cache.get(manifest_path)
                metadata = cached[1] if cached is not None and cached[0] == manifest else None
            if metadata is None:
                metadata = self._metadata_from_manifest(manifest_path, manifest)
                with self._metadata_cache_lock:
                    self._metadata_cache[manifest_path] = (copy.deepcopy(manifest), metadata)
                    self._metadata_cache.move_to_end(manifest_path)
                    while len(self._metadata_cache) > 8:
                        self._metadata_cache.popitem(last=False)
            else:
                # Cached parsing never bypasses the existing controlled-path
                # and missing-file checks; actual file bodies are checked when read.
                for name in metadata.owned_names:
                    if not self._owned_path(source_dir, name).is_file():
                        raise ConfigSourceInvalidError()
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

    def read_ambiguous_asset(
        self,
        *,
        session_id: int,
        source_id: str,
        candidate_id: str,
    ) -> tuple[bytes, str]:
        clean_candidate_id = str(candidate_id or "").strip()
        if not re.fullmatch(r"A[1-9]\d{0,3}", clean_candidate_id):
            raise ConfigAssetNotFoundError()
        metadata = self._load_metadata(
            session_id=session_id,
            source_id=source_id,
            require_active=True,
        )
        candidate = next(
            (
                item
                for item in _ambiguous_assets_from_blocks(metadata.private_blocks)
                if item["candidate_id"] == clean_candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ConfigAssetNotFoundError()
        filename = str(candidate["filename"])
        try:
            path = self._owned_path(metadata.manifest_path.parent, filename)
            content = self._files.read_bytes(path)
            self._validate_inventory_content(metadata, filename, content)
            inventory_entry = metadata.file_inventory.get(filename)
            if (
                not isinstance(inventory_entry, dict)
                or inventory_entry.get("role") != "ambiguous_asset"
                or inventory_entry.get("candidate_id") != clean_candidate_id
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
        asset_decisions: Sequence[AmbiguousAssetDecision] = (),
    ) -> PreparedGenerationInput:
        known = {question.question_id for question in record.questions}
        by_id: dict[str, QuestionDecision] = {}
        for decision in decisions:
            question_id = str(decision.question_id or "").strip()
            if question_id not in known:
                raise ValueError("unknown question decision")
            if question_id in by_id:
                raise ValueError("duplicate question decision")
            if (
                decision.question_type is not None
                and decision.question_type not in _ALLOWED_QUESTION_TYPES
            ):
                raise ValueError("unsupported question type")
            if decision.bank_match is not None and decision.bank_match not in {
                "same",
                "different",
                "reanalyze",
            }:
                raise ValueError("unsupported bank match decision")
            if decision.bank_match == "same" and (
                not isinstance(decision.bank_question_id, int)
                or isinstance(decision.bank_question_id, bool)
                or decision.bank_question_id <= 0
            ):
                raise ValueError("bank decision requires a bank question")
            by_id[question_id] = decision

        ambiguous_candidates = {
            item["candidate_id"]: item
            for item in _ambiguous_assets_from_blocks(record.private_blocks)
        }
        automatic_assets = _automatic_asset_bindings(record)
        candidates = {**automatic_assets, **ambiguous_candidates}
        asset_by_id: dict[str, AmbiguousAssetDecision] = {}
        for decision in asset_decisions:
            candidate_id = str(decision.candidate_id or "").strip()
            candidate = candidates.get(candidate_id)
            if candidate is None or candidate_id in asset_by_id:
                raise ValueError("invalid ambiguous asset decision")
            if decision.action == "ignore":
                if decision.question_id is not None or decision.asset_kind is not None:
                    raise ValueError("ignored asset cannot have a binding target")
            elif decision.action == "bind":
                if (
                    decision.question_id not in known
                    or decision.asset_kind not in {"question", "answer"}
                ):
                    raise ValueError("asset target is invalid")
            else:
                raise ValueError("ambiguous asset action is invalid")
            asset_by_id[candidate_id] = decision

        unresolved_question_ids = {
            str(candidate[key])
            for candidate_id, candidate in ambiguous_candidates.items()
            if candidate_id not in asset_by_id
            for key in ("previous_question_id", "next_question_id")
        }
        question_images: dict[str, dict[str, str | list[str] | None]] = {
            question_id: {"question": None, "answer": None}
            for question_id in known
        }

        def append_image(question_id: str, asset_kind: str, encoded: str) -> None:
            target = question_images.setdefault(
                question_id, {"question": None, "answer": None}
            )
            existing = target.get(asset_kind)
            if existing is None:
                target[asset_kind] = encoded
            elif isinstance(existing, str):
                target[asset_kind] = [existing, encoded]
            elif isinstance(existing, list) and all(
                isinstance(item, str) and item for item in existing
            ):
                if len(existing) >= 32:
                    raise ValueError("asset target contains too many images")
                existing.append(encoded)
            else:
                raise ConfigSourceInvalidError()

        for asset_id, automatic in automatic_assets.items():
            decision = asset_by_id.get(asset_id)
            if decision is not None and decision.action == "ignore":
                continue
            target_question_id = (
                str(decision.question_id)
                if decision is not None
                else str(automatic["question_id"])
            )
            target_kind = (
                str(decision.asset_kind)
                if decision is not None
                else str(automatic["asset_kind"])
            )
            append_image(target_question_id, target_kind, str(automatic["encoded"]))

        current_metadata: _ConfigSourceMetadata | None = None
        if any(
            candidate_id in ambiguous_candidates and decision.action == "bind"
            for candidate_id, decision in asset_by_id.items()
        ):
            current_metadata = self._load_metadata(
                session_id=record.session_id,
                source_id=record.source_id,
                require_active=True,
            )
            if current_metadata.source_revision != record.source_revision:
                raise ConfigSourceChangedError()
        for candidate_id, decision in asset_by_id.items():
            if candidate_id not in ambiguous_candidates or decision.action != "bind":
                continue
            assert current_metadata is not None
            candidate = candidates[candidate_id]
            filename = str(candidate["filename"])
            content = self._files.read_bytes(
                self._owned_path(record.manifest_path.parent, filename)
            )
            self._validate_inventory_content(current_metadata, filename, content)
            inventory_entry = current_metadata.file_inventory.get(filename)
            if (
                not isinstance(inventory_entry, dict)
                or inventory_entry.get("role") != "ambiguous_asset"
                or inventory_entry.get("candidate_id") != candidate_id
            ):
                raise ConfigSourceInvalidError()
            _image_type(content)
            encoded = base64.b64encode(content).decode("ascii")
            assert decision.asset_kind is not None
            append_image(str(decision.question_id), decision.asset_kind, encoded)

        confirmed: list[dict[str, Any]] = []
        included_ids: set[str] = set()
        for private_block in record.private_blocks:
            block = copy.deepcopy(private_block)
            block.pop("_word_paragraphs", None)
            question_id = str(block.get("question_id") or "").strip()
            decision = by_id.get(question_id)
            if decision is not None and decision.excluded:
                continue
            if question_id in unresolved_question_ids:
                continue
            _strip_embedded_question_section_heading(block)
            if decision is not None and decision.question_type is not None:
                block["question_type"] = decision.question_type
                block["question_type_confirmed"] = True
                block.pop("response_form_fact", None)
            else:
                # Teacher confirmation remains distinct from a visible answer-form
                # fact.  A single printed blank is still not permission for the
                # model to invent process subquestions.
                _align_question_type_with_visible_blank(block)
                block["question_type_confirmed"] = False
                if _visible_response_form_fact(block) == "single_blank":
                    block["response_form_fact"] = "single_blank"
                else:
                    block.pop("response_form_fact", None)
            if decision is not None and decision.answer_confirmed:
                if decision.answer_override is not None:
                    block["answer_text"] = decision.answer_override
                block["answer_confirmed"] = True
            confirmed.append(block)
            included_ids.add(question_id)
        return PreparedGenerationInput(
            confirmed_blocks=tuple(confirmed),
            document_text=record.private_document_text,
            question_images={
                question_id: copy.deepcopy(images)
                for question_id, images in question_images.items()
                if question_id in included_ids
                and any(images.get(kind) is not None for kind in ("question", "answer"))
            },
            whole_page_images=record.private_whole_page_images,
        )

    def resolve_asset_decision_overrides(
        self,
        record: ConfigSourceRecord,
        asset_decisions: Sequence[AmbiguousAssetDecision],
    ) -> tuple[dict[str, Any], ...]:
        """Translate teacher asset decisions into import-time image overrides.

        The question-bank sync re-parses the original document, so decisions
        are keyed by image content (sha256) instead of by parse-specific ids.
        Validation mirrors apply_teacher_decisions.
        """
        known = {question.question_id for question in record.questions}
        ambiguous_candidates = {
            item["candidate_id"]: item
            for item in _ambiguous_assets_from_blocks(record.private_blocks)
        }
        automatic_assets = _automatic_asset_bindings(record)
        candidates = {**automatic_assets, **ambiguous_candidates}
        seen: set[str] = set()
        overrides: list[dict[str, Any]] = []
        for decision in asset_decisions:
            candidate_id = str(decision.candidate_id or "").strip()
            candidate = candidates.get(candidate_id)
            if candidate is None or candidate_id in seen:
                raise ValueError("invalid ambiguous asset decision")
            seen.add(candidate_id)
            action = str(decision.action or "").strip()
            if action == "ignore":
                if decision.question_id is not None or decision.asset_kind is not None:
                    raise ValueError("ignored asset cannot have a binding target")
            elif action == "bind":
                if (
                    decision.question_id not in known
                    or decision.asset_kind not in {"question", "answer"}
                ):
                    raise ValueError("asset target is invalid")
            else:
                raise ValueError("ambiguous asset action is invalid")
            if candidate_id in automatic_assets:
                content = base64.b64decode(str(candidate["encoded"]), validate=True)
            else:
                content = self._files.read_bytes(
                    self._owned_path(
                        record.manifest_path.parent,
                        str(candidate["filename"]),
                    )
                )
            digest = hashlib.sha256(content).hexdigest()
            if action == "ignore":
                overrides.append(
                    {
                        "sha256": digest,
                        "action": "ignore",
                        "question_number": None,
                        "asset_kind": None,
                    }
                )
            else:
                overrides.append(
                    {
                        "sha256": digest,
                        "action": "bind",
                        "question_number": str(decision.question_id).removeprefix("Q"),
                        "asset_kind": str(decision.asset_kind),
                    }
                )
        return tuple(overrides)

    def prepare_generation_input(
        self,
        record: ConfigSourceRecord,
        decisions: Sequence[QuestionDecision],
        generation_mode: str,
        asset_decisions: Sequence[AmbiguousAssetDecision] = (),
    ) -> PreparedGenerationInput:
        mode = str(generation_mode or "").strip()
        if mode == "per_question":
            mode = "batched"
        if mode != "batched":
            raise ValueError("unsupported config generation mode")
        prepared = self.apply_teacher_decisions(record, decisions, asset_decisions)
        if not prepared.confirmed_blocks:
            raise ValueError("at least one included question is required")
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
            document_text_parts: list[str] = []
            blocks = parse_docx_question_blocks(
                file_bytes,
                temporary_root=parser_io_root,
                asset_root=parser_io_root / "assets",
                register_created_file=parser_registry.register,
                write_created_file=write_parser_asset,
                document_text_out=document_text_parts,
                preserve_preview_xml=True,
            )
            document_text = "\n".join(document_text_parts)
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

        from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline
        from question_bank.importers.batch_importer import _extract_paper
        from backend.document_parsing.rubric_auto_cropper import (
            extract_pdf_images,
            extract_pdf_question_images,
        )

        # Reuse the bank's local PDF extraction, including layout coordinates.
        # Temporary OCR assets never become persistent source references.
        with tempfile.TemporaryDirectory(prefix="config-pdf-") as workdir:
            extracted = _extract_paper(
                source_path, asset_root=Path(workdir) / "images",
                document_pipeline=QuestionDocumentPipeline(Path(workdir) / "ocr"),
                operation_id="config-source",
            )
            document_text = extracted.text
            layout = extracted.pdf_layout
        blocks = parse_plain_question_blocks(document_text)
        for block in blocks:
            if isinstance(block, dict):
                block["semantic_source"] = "images"
        crop_options = {"layout_pages": layout["pages"]} if layout.get("pages") else {}
        question_pdf = layout.get("question_pdf", file_bytes)
        if layout.get("answer_pdf"):
            raw_assets = extract_pdf_question_images(question_pdf, blocks, include_answer=False, **crop_options)
            answer_assets = extract_pdf_question_images(layout["answer_pdf"], blocks, include_answer=False, **crop_options)
            for qid, values in raw_assets.items():
                values["answer"] = answer_assets.get(qid, {}).get("question")
        else:
            raw_assets = extract_pdf_question_images(file_bytes, blocks, **crop_options) if blocks else {}
        if any(not raw_assets.get(str(block.get("question_id")), {}).get("question") for block in blocks):
            raise ConfigSourceParseError("missing_crops")
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
        if normalized_questions != questions and not _matches_legacy_question_previews(
            questions,
            normalized_questions,
            private_blocks,
        ):
            stored_ids = [question.question_id for question in questions]
            recomputed_ids = [
                question.question_id for question in normalized_questions
            ]
            if stored_ids != recomputed_ids:
                raise ConfigSourceInvalidError()
        # Stored previews may predate current preview rules; the manifest and
        # its revision are still validated against the stored values below,
        # while the loaded record serves the recomputed ones.
        current_questions = normalized_questions

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
            questions=current_questions,
            manifest_path=manifest_path,
            source_path=source_path,
            owned_names=owned,
            file_inventory=file_inventory,
            private_blocks=tuple(copy.deepcopy(private_blocks)),
            private_document_text=document_text,
            asset_files=normalized_assets,
            whole_page_files=tuple(normalized_whole_pages),
            stored_questions=questions,
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
        private_images: dict[str, dict[str, str | list[str] | None]] = {}
        for question_id in metadata.asset_files:
            encoded: dict[str, str | list[str] | None] = {
                "question": None,
                "answer": None,
            }
            for kind in ("question", "answer"):
                values: list[str] = []
                for filename in _question_asset_filenames(
                    metadata,
                    question_id=question_id,
                    asset_kind=kind,
                ):
                    content_path = self._owned_path(
                        metadata.manifest_path.parent,
                        filename,
                    )
                    content = self._files.read_bytes(content_path)
                    self._validate_inventory_content(metadata, filename, content)
                    _image_type(content)
                    values.append(base64.b64encode(content).decode("ascii"))
                if len(values) == 1:
                    encoded[kind] = values[0]
                elif values:
                    encoded[kind] = values
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
        "questions": [asdict(question) for question in (metadata.stored_questions if metadata.stored_questions is not None else metadata.questions)],
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
        for candidate in block.get("_ambiguous_assets") or []:
            if not isinstance(candidate, dict):
                raise ConfigSourceInvalidError()
            candidate_id = str(candidate.get("candidate_id") or "")
            filename = str(candidate.get("filename") or "")
            if not re.fullmatch(r"A[1-9]\d{0,3}", candidate_id):
                raise ConfigSourceInvalidError()
            register(
                filename,
                {"role": "ambiguous_asset", "candidate_id": candidate_id},
            )
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
    known_question_ids = {
        str(block.get("question_id") or "").strip()
        for block in private_blocks
        if isinstance(block, dict)
    }
    ambiguous_index = 0
    for block_index, block in enumerate(private_blocks, start=1):
        question_id = str(block.get("question_id") or "").strip()
        if not _QUESTION_ID.fullmatch(question_id):
            raise ConfigSourceInvalidError()
        entry: dict[str, str | None] = {"question": None, "answer": None}
        replacements: dict[str, str] = {}
        raw_candidates = block.get("_ambiguous_assets")
        copied_candidates: list[dict[str, str]] = []
        if raw_candidates is not None:
            if not isinstance(raw_candidates, list):
                raise ConfigSourceInvalidError()
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, dict):
                    raise ConfigSourceInvalidError()
                raw_path = str(raw_candidate.get("path") or "").strip()
                previous_id = str(raw_candidate.get("previous_question_id") or "").strip()
                next_id = str(raw_candidate.get("next_question_id") or "").strip()
                source_section = str(raw_candidate.get("source_section") or "").strip()
                if (
                    not raw_path
                    or not _QUESTION_ID.fullmatch(previous_id)
                    or not _QUESTION_ID.fullmatch(next_id)
                    or previous_id == next_id
                    or previous_id not in known_question_ids
                    or next_id not in known_question_ids
                    or source_section not in {"question", "answer"}
                ):
                    raise ConfigSourceInvalidError()
                path = Path(raw_path)
                try:
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(parser_resolved) or _is_reparse(path):
                        raise ConfigSourceInvalidError()
                    content = filesystem.read_bytes(path)
                    suffix, _media_type = _image_type(content)
                except ConfigSourceError:
                    raise
                except Exception:
                    raise ConfigSourceInvalidError() from None
                if ambiguous_index >= 5_000:
                    raise ConfigSourceInvalidError()
                ambiguous_index += 1
                candidate_id = f"A{ambiguous_index}"
                output = source_dir / f"ambiguous-{ambiguous_index}{suffix}"
                _write_bytes_atomic(
                    output,
                    content,
                    registry=registry,
                    filesystem=filesystem,
                )
                copied_candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "filename": output.name,
                        "previous_question_id": previous_id,
                        "next_question_id": next_id,
                        "source_section": source_section,
                    }
                )
            block["_ambiguous_assets"] = copied_candidates
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
                    block.setdefault("parse_warnings", []).append("有一张配图未能生成预览，请核对原卷图片格式。")
                    block["needs_review"] = True
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


def _ambiguous_assets_from_blocks(
    blocks: Sequence[dict[str, Any]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    known_question_ids = {
        str(block.get("question_id") or "").strip()
        for block in blocks
        if isinstance(block, dict)
    }
    for block in blocks:
        raw_candidates = block.get("_ambiguous_assets")
        if raw_candidates is None:
            continue
        if not isinstance(raw_candidates, list):
            raise ConfigSourceInvalidError()
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                raise ConfigSourceInvalidError()
            candidate = {
                "candidate_id": str(raw.get("candidate_id") or ""),
                "filename": str(raw.get("filename") or ""),
                "previous_question_id": str(raw.get("previous_question_id") or ""),
                "next_question_id": str(raw.get("next_question_id") or ""),
                "source_section": str(raw.get("source_section") or ""),
            }
            if (
                not re.fullmatch(r"A[1-9]\d{0,3}", candidate["candidate_id"])
                or candidate["candidate_id"] in seen
                or Path(candidate["filename"]).name != candidate["filename"]
                or not _QUESTION_ID.fullmatch(candidate["previous_question_id"])
                or not _QUESTION_ID.fullmatch(candidate["next_question_id"])
                or candidate["previous_question_id"] == candidate["next_question_id"]
                or candidate["previous_question_id"] not in known_question_ids
                or candidate["next_question_id"] not in known_question_ids
                or candidate["source_section"] not in {"question", "answer"}
            ):
                raise ConfigSourceInvalidError()
            seen.add(candidate["candidate_id"])
            result.append(candidate)
    return result


def _public_ambiguous_assets(
    blocks: Sequence[dict[str, Any]],
    *,
    session_id: int,
    source_id: str,
) -> list[dict[str, str]]:
    return [
        {
            "candidate_id": item["candidate_id"],
            "previous_question_id": item["previous_question_id"],
            "next_question_id": item["next_question_id"],
            "source_section": item["source_section"],
            "asset_url": (
                f"/api/sessions/{session_id}/config/sources/{source_id}"
                f"/ambiguous-assets/{item['candidate_id']}"
            ),
        }
        for item in _ambiguous_assets_from_blocks(blocks)
    ]


def _public_source_assets(
    questions: Sequence[ConfigQuestionPreview],
    blocks: Sequence[dict[str, Any]],
    *,
    session_id: int,
    source_id: str,
) -> list[dict[str, Any]]:
    blocks_by_id = {
        str(block.get("question_id") or "").strip(): block
        for block in blocks
        if isinstance(block, dict)
    }
    assets: list[dict[str, Any]] = []
    automatic_index = 0
    for question in questions:
        block = blocks_by_id.get(question.question_id)
        for asset_kind in ("question", "answer"):
            has_legacy_asset = (
                question.has_question_asset
                if asset_kind == "question"
                else question.has_answer_asset
            )
            urls = _config_asset_urls(
                block,
                session_id=session_id,
                source_id=source_id,
                question_id=question.question_id,
                asset_kind=asset_kind,
                has_legacy_asset=has_legacy_asset,
            )
            for url in urls:
                automatic_index += 1
                assets.append(
                    {
                        "asset_id": f"P{automatic_index}",
                        "asset_url": url,
                        "assignment_state": "automatic",
                        "question_id": question.question_id,
                        "asset_kind": asset_kind,
                        "candidate_question_ids": [],
                    }
                )
    for item in _public_ambiguous_assets(
        blocks,
        session_id=session_id,
        source_id=source_id,
    ):
        assets.append(
            {
                "asset_id": item["candidate_id"],
                "asset_url": item["asset_url"],
                "assignment_state": "uncertain",
                "question_id": None,
                "asset_kind": item["source_section"],
                "candidate_question_ids": [
                    item["previous_question_id"],
                    item["next_question_id"],
                ],
            }
        )
    return assets


def _automatic_asset_bindings(
    record: ConfigSourceRecord,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    asset_index = 0
    for question in record.questions:
        entry = record.private_question_images.get(question.question_id) or {}
        for asset_kind in ("question", "answer"):
            raw = entry.get(asset_kind)
            values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
            for encoded in values:
                if not isinstance(encoded, str) or not encoded:
                    raise ConfigSourceInvalidError()
                asset_index += 1
                result[f"P{asset_index}"] = {
                    "question_id": question.question_id,
                    "asset_kind": asset_kind,
                    "encoded": encoded,
                }
    return result


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
    source_revision: str = "",
    source_suffix: Literal[".docx", ".pdf"],
    source_docx: bytes | None = None,
) -> list[dict[str, Any]]:
    formula_index: Mapping[str, str] | None = None
    if source_suffix == ".docx" and source_docx:
        try:
            formula_index = paragraph_xml_index(
                session_id,
                source_id,
                source_revision,
                source_docx,
                paragraphs=next(
                    (block["_word_paragraphs"] for block in private_blocks
                     if isinstance(block.get("_word_paragraphs"), list)),
                    None,
                ),
            )
        except Exception:
            formula_index = None
    blocks_by_id = {
        str(block.get("question_id") or "").strip(): block
        for block in private_blocks
        if str(block.get("question_id") or "").strip()
    }
    result: list[dict[str, Any]] = []
    for question in questions:
        payload = asdict(question)
        original_block = blocks_by_id.get(question.question_id)
        block = copy.deepcopy(original_block) if isinstance(original_block, dict) else None
        if block is not None:
            _strip_embedded_question_section_heading(block)
            _align_question_type_with_visible_blank(block)
            payload["question_type"] = str(
                block.get("question_type") or question.question_type
            )
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
                    "needs_review": bool(question.needs_review or not question.has_question_asset),
                    "local_answer_trusted": False,
                }
            )
        elif block is not None:
            question_value = (
                block.get("question_html")
                or block.get("question_text")
                or block.get("text")
                or ""
            )
            review_reason = _question_type_review_reason(
                str(block.get("question_type") or question.question_type),
                question_value,
            )
            payload.update(
                {
                    "question_preview": _public_preview(_strip_leading_question_number(
                        question.question_id.removeprefix("Q"), str(question_value),
                    )),
                    "answer_preview": _answer_summary(block),
                    "question_type_review_required": bool(review_reason),
                    "question_type_review_reason": review_reason,
                }
            )
        payload["question_type_basis"] = _question_type_basis(
            str(payload.get("question_type") or question.question_type),
            (
                ""
                if image_semantic_source or block is None
                else (
                    block.get("question_html")
                    or block.get("question_text")
                    or block.get("text")
                    or question.question_preview
                )
            ),
        )
        payload["parse_warnings"] = [str(value)[:200] for value in (block or {}).get("parse_warnings", [])][:20]
        payload["rich_content"] = _config_rich_content(
            block,
            session_id=session_id,
            source_id=source_id,
            question_id=question.question_id,
            has_question_asset=question.has_question_asset,
            has_answer_asset=question.has_answer_asset,
            force_image_semantics=image_semantic_source,
            formula_index=formula_index,
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
    formula_index: Mapping[str, str] | None = None,
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
            _strip_leading_question_number(
                question_id.removeprefix("Q"),
                block.get("question_html")
                or block.get("question_text")
                or block.get("text")
                or ""
            ),
            formula_index=formula_index,
        )
        answer_blocks = _deduplicate_config_rich_blocks(
            _project_config_rich_blocks(
                block.get("answer_html")
                or block.get("answer_text")
                or block.get("canonical_answer")
                or "",
                formula_index=formula_index,
            ),
            _project_config_rich_blocks(
                block.get("analysis_html")
                or block.get("analysis")
                or "",
                formula_index=formula_index,
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
            "html": "",
            "asset_indexes": list(range(len(asset_urls))),
            "asset_urls": list(asset_urls),
        }
    )
    return result


def _project_config_rich_blocks(
    value: Any,
    *,
    formula_index: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
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
                "html": preview_html_for_text(formula_index, chunk),
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
        type_review_reason = _question_type_review_reason(
            str(block.get("question_type") or "comprehensive"),
            question_value,
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
                answer_preview=("" if image_semantic_source else _answer_summary(block)),
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
                needs_review=bool(block.get("needs_review")) or bool(type_review_reason) or (image_semantic_source and not assets.get("question")),
                question_type_review_required=bool(type_review_reason),
                question_type_review_reason=type_review_reason,
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
    text = re.sub(r"<(b|strong|span|i|em|u)\b[^>]*>(.*?)</\1>", r"\2", text, flags=re.IGNORECASE | re.DOTALL)
    text = " ".join(text.split())
    return text[:PUBLIC_PREVIEW_CHARACTERS]


def _answer_summary(block: dict[str, Any]) -> str:
    canonical = _public_preview(block.get("canonical_answer") or "")
    answer = _public_preview(
        block.get("answer_html") or block.get("answer_text") or ""
    )
    lead = canonical or answer
    if canonical and not canonical.startswith("答案"):
        lead = f"答案 {canonical}"
    return lead[:220]


def _matches_legacy_question_previews(
    stored: Sequence[ConfigQuestionPreview],
    normalized: Sequence[ConfigQuestionPreview],
    blocks: Sequence[dict[str, Any]],
) -> bool:
    """Accept version-2 manifests written before answer previews became summaries."""
    if len(stored) != len(normalized) or len(stored) != len(blocks):
        return False
    legacy: list[ConfigQuestionPreview] = []
    for question, block in zip(normalized, blocks, strict=True):
        answer_value = (
            block.get("answer_html")
            or block.get("answer_text")
            or block.get("canonical_answer")
            or ""
        )
        legacy.append(
            replace(question, answer_preview=_public_preview(answer_value))
        )
    return tuple(stored) == tuple(legacy)


def _align_question_type_with_visible_blank(block: dict[str, Any]) -> None:
    """If an unconfirmed stem has one fill-in slot, prefer fill_blank."""
    if block.get("question_type_confirmed") is True:
        return
    question_value = (
        block.get("question_html")
        or block.get("question_text")
        or block.get("text")
        or ""
    )
    current_type = str(block.get("question_type") or "").strip()
    reason = _question_type_review_reason(current_type, question_value)
    if reason.startswith("题面只有一个明确填空位置"):
        block["question_type"] = "fill_blank"
        return
    if (
        current_type == "fill_blank"
        and has_visible_subparts(str(question_value or ""))
    ):
        # A labeled multi-task stem was only looking like a fill-in because a
        # table cell or one blank sat inside a larger worked question.
        block["question_type"] = "comprehensive"


def _question_type_review_reason(question_type: str, question_value: Any) -> str:
    raw = str(question_value or "")
    plain = html.unescape(_HTML_TAG.sub(" ", raw)).replace("\r", "")
    if _QUESTION_SECTION_HEADING.search(plain):
        return "检测到下一部分标题可能粘在本题末尾，请确认题型。"
    normalized_type = str(question_type or "").strip()
    looks_like_choice = (
        normalized_type in {"choice", "single_choice", "multi_choice"}
        or "选择" in normalized_type
        or has_explicit_choice_options(raw)
        or has_explicit_choice_options(plain)
    )
    labeled_subparts = has_visible_subparts(raw) or has_visible_subparts(plain)
    if (
        normalized_type != "fill_blank"
        and has_visible_stem_fill_blank_mark(raw)
        and not labeled_subparts
        and not looks_like_choice
    ):
        return "题面只有一个明确填空位置，但当前题型不是填空题。"
    if (
        normalized_type == "comprehensive"
        and labeled_subparts
        and has_visible_fill_blank_mark(raw)
    ):
        return "题面既有多个小问，也有填空位置。本地按综合解答题处理，请确认题型。"
    if (
        normalized_type in {"choice", "fill_blank"}
        and labeled_subparts
    ):
        return "题面包含多个小问，但当前题型是选择题或填空题，请确认题型。"
    return ""


def _question_type_basis(question_type: str, question_value: Any) -> str:
    normalized = str(question_type or "").strip()
    if normalized not in _DISPLAY_QUESTION_TYPES:
        return ""
    raw = str(question_value or "")
    plain = html.unescape(_HTML_TAG.sub(" ", raw)).replace("\r", "")
    parts: list[str] = []
    if has_explicit_choice_options(raw) or has_explicit_choice_options(plain):
        parts.append("题面有选项")
    if has_visible_stem_fill_blank_mark(raw) or (
        (has_visible_subparts(raw) or has_visible_subparts(plain))
        and has_visible_fill_blank_mark(raw)
    ):
        parts.append("题面有填空位置")
    if has_visible_subparts(raw) or has_visible_subparts(plain):
        parts.append("题面有多个小问")
    if parts:
        return " · ".join(parts)
    fallback = {
        "choice": "按选择题处理",
        "single_choice": "按选择题处理",
        "multi_choice": "按选择题处理",
        "fill_blank": "按填空题处理",
        "calculation": "按计算题处理",
        "proof": "按证明题处理",
        "comprehensive": "按综合解答题处理",
    }
    return fallback[normalized]


def _visible_response_form_fact(block: dict[str, Any]) -> str:
    question_type = str(block.get("question_type") or "").strip()
    raw = str(
        block.get("question_html")
        or block.get("question_text")
        or block.get("text")
        or ""
    )
    plain = html.unescape(_HTML_TAG.sub(" ", raw))
    if (
        question_type == "fill_blank"
        and has_visible_stem_fill_blank_mark(raw)
        and not has_visible_subparts(raw)
        and not has_visible_subparts(plain)
    ):
        return "single_blank"
    return ""


def _strip_embedded_question_section_heading(block: dict[str, Any]) -> None:
    for field in ("question_text", "text"):
        value = str(block.get(field) or "")
        match = _QUESTION_SECTION_HEADING.search(value.replace("\r", ""))
        if match:
            block[field] = value[: match.start()].rstrip()
    html_value = str(block.get("question_html") or "")
    match = None
    for paragraph in re.finditer(
        r"<p\b[^>]*>.*?</p>",
        html_value,
        re.IGNORECASE | re.DOTALL,
    ):
        plain_paragraph = html.unescape(
            _HTML_TAG.sub("", paragraph.group(0))
        ).replace("\r", "").strip()
        if _QUESTION_SECTION_HEADING.fullmatch(plain_paragraph):
            match = paragraph
            break
    if match is None:
        match = _HTML_QUESTION_SECTION_HEADING.search(html_value)
    if match is None:
        # DOCX rich text is sometimes stored as newline-delimited plain text in
        # question_html.  Treat that representation exactly like question_text.
        match = _QUESTION_SECTION_HEADING.search(html_value.replace("\r", ""))
    if match:
        block["question_html"] = html_value[: match.start()].rstrip()


def _question_from_dict(value: Any) -> ConfigQuestionPreview:
    if not isinstance(value, dict):
        raise ConfigSourceInvalidError()
    try:
        type_review_reason = str(value.get("question_type_review_reason") or "")
        if not type_review_reason:
            type_review_reason = _question_type_review_reason(
                str(value["question_type"]),
                value["question_preview"],
            )
        question = ConfigQuestionPreview(
            question_id=str(value["question_id"]),
            question_type=str(value["question_type"]),
            question_preview=str(value["question_preview"]),
            answer_preview=str(value["answer_preview"]),
            answer_present=value["answer_present"] is True,
            needs_review=value["needs_review"] is True,
            question_type_review_required=(
                value.get("question_type_review_required") is True
                or bool(type_review_reason)
            ),
            question_type_review_reason=type_review_reason,
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
