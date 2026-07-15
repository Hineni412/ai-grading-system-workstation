from __future__ import annotations

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
from typing import Any, Literal
from unittest.mock import patch
from urllib.parse import unquote

from PIL import Image

from backend.config_workspace.atomic import remove_exact_files, write_json_atomic
from backend.config_workspace.locks import session_config_lock


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_DOCX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_PDF_PAGES = 500
PUBLIC_PREVIEW_CHARACTERS = 500

_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUESTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:.+?\]\]", re.IGNORECASE)
_IMAGE_PATH_MARKER = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]*>")
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
_DOCX_PARSE_LOCK = threading.Lock()


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
            "questions": [asdict(question) for question in self.questions],
        }


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
    ) -> None:
        self.upload_config_dir = Path(upload_config_dir)
        self.max_upload_bytes = int(max_upload_bytes)
        self.max_docx_member_bytes = int(max_docx_member_bytes)
        self.max_docx_expanded_bytes = int(max_docx_expanded_bytes)
        self.max_pdf_pages = int(max_pdf_pages)
        if min(
            self.max_upload_bytes,
            self.max_docx_member_bytes,
            self.max_docx_expanded_bytes,
            self.max_pdf_pages,
        ) <= 0:
            raise ValueError("config source limits must be positive")

    async def stage_and_parse(
        self,
        *,
        session_id: int,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> ConfigSourceRecord:
        clean_session_id = _positive_session_id(session_id)
        safe_filename, suffix = _safe_filename(filename)
        source_id = uuid.uuid4().hex
        source_dir = self._source_dir(clean_session_id, source_id)
        self._prepare_source_dir(source_dir)
        temporary_path = source_dir / f".source.{uuid.uuid4().hex}.tmp"
        source_path = source_dir / f"source{suffix}"
        created_files: list[Path] = []
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with temporary_path.open("xb") as stream:
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
                os.fsync(stream.fileno())
            if size_bytes <= 0:
                raise ConfigSourceInvalidError()
            _validate_magic(temporary_path, suffix)
            os.replace(temporary_path, source_path)
            created_files.append(source_path)

            if suffix == ".docx":
                blocks, document_text, asset_files, whole_page_files, new_files = (
                    self._parse_docx(source_path, source_dir)
                )
            else:
                blocks, document_text, asset_files, whole_page_files, new_files = (
                    self._parse_pdf(source_path, source_dir)
                )
            created_files.extend(new_files)
            questions = _question_previews(blocks, asset_files)
            source_sha256 = digest.hexdigest()
            source_revision = hashlib.sha256(
                f"{clean_session_id}:{source_id}:{source_sha256}".encode("ascii")
            ).hexdigest()
            manifest_path = source_dir / "manifest.json"
            owned_names = [path.name for path in created_files]
            owned_names.append(manifest_path.name)
            if len(owned_names) != len(set(owned_names)):
                raise ConfigSourceInvalidError()
            manifest = {
                "version": 1,
                "session_id": clean_session_id,
                "source_id": source_id,
                "source_revision": source_revision,
                "safe_filename": safe_filename,
                "suffix": suffix,
                "size_bytes": size_bytes,
                "sha256": source_sha256,
                "parse_state": "ready",
                "source_file": source_path.name,
                "owned_files": owned_names,
                "questions": [asdict(question) for question in questions],
                "private_blocks": blocks,
                "private_document_text": document_text,
                "asset_files": asset_files,
                "whole_page_files": whole_page_files,
            }
            json.dumps(manifest, ensure_ascii=False)
            with session_config_lock(self.upload_config_dir, clean_session_id):
                write_json_atomic(manifest_path, manifest)
                if manifest_path.exists():
                    created_files.append(manifest_path)
                record = self._record_from_manifest(manifest_path, manifest)
                write_json_atomic(
                    self._active_path(clean_session_id),
                    {
                        "source_id": source_id,
                        "source_revision": source_revision,
                    },
                )
            return record
        except ConfigSourceError:
            generated = (
                [path for path in source_dir.rglob("*") if path.is_file()]
                if source_dir.exists()
                else []
            )
            remove_exact_files(
                [temporary_path, *reversed(created_files), *generated]
            )
            raise
        except Exception:
            if source_dir.exists():
                generated = [path for path in source_dir.rglob("*") if path.is_file()]
            else:
                generated = []
            remove_exact_files([temporary_path, *reversed(created_files), *generated])
            raise ConfigSourceInvalidError() from None

    def load(
        self,
        *,
        session_id: int,
        source_id: str,
        require_active: bool = True,
    ) -> ConfigSourceRecord:
        clean_session_id = _positive_session_id(session_id)
        clean_source_id = str(source_id or "").strip()
        if not _SOURCE_ID.fullmatch(clean_source_id):
            raise ConfigSourceNotFoundError()
        source_dir = self._source_dir(clean_session_id, clean_source_id)
        manifest_path = source_dir / "manifest.json"
        if not manifest_path.is_file() or _is_reparse(manifest_path):
            raise ConfigSourceNotFoundError()
        try:
            self._assert_controlled_directory(source_dir)
            manifest = _read_json_object(manifest_path)
            record = self._record_from_manifest(manifest_path, manifest)
        except ConfigSourceError:
            raise
        except Exception:
            raise ConfigSourceInvalidError() from None
        if require_active:
            try:
                active = _read_json_object(self._active_path(clean_session_id))
            except Exception:
                raise ConfigSourceChangedError() from None
            if (
                active.get("source_id") != record.source_id
                or active.get("source_revision") != record.source_revision
            ):
                raise ConfigSourceChangedError()
        return record

    def read_asset(
        self,
        *,
        session_id: int,
        source_id: str,
        question_id: str,
        asset_kind: str,
    ) -> tuple[bytes, str]:
        kind = str(asset_kind or "").strip()
        clean_question_id = str(question_id or "").strip()
        if kind not in {"question", "answer"} or not _QUESTION_ID.fullmatch(
            clean_question_id
        ):
            raise ConfigAssetNotFoundError()
        record = self.load(session_id=session_id, source_id=source_id)
        manifest = _read_json_object(record.manifest_path)
        asset_files = manifest.get("asset_files")
        entry = asset_files.get(clean_question_id) if isinstance(asset_files, dict) else None
        filename = entry.get(kind) if isinstance(entry, dict) else None
        if not isinstance(filename, str) or not filename:
            raise ConfigAssetNotFoundError()
        try:
            path = _owned_path(record.manifest_path.parent, filename)
            content = path.read_bytes()
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
        removed: list[str] = []
        with session_config_lock(self.upload_config_dir, clean_session_id):
            try:
                active = _read_json_object(self._active_path(clean_session_id))
                active_source_id = str(active.get("source_id") or "")
            except Exception:
                active_source_id = ""
            session_dir = self._session_dir(clean_session_id)
            if not session_dir.is_dir() or _is_reparse(session_dir):
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
                    manifest = _read_json_object(manifest_path)
                    if manifest.get("source_id") != source_id:
                        continue
                    owned_names = manifest.get("owned_files")
                    if not isinstance(owned_names, list):
                        continue
                    owned_paths = [_owned_path(source_dir, name) for name in owned_names]
                except Exception:
                    continue
                ordered = [path for path in owned_paths if path != manifest_path]
                ordered.append(manifest_path)
                remove_exact_files(ordered)
                removed.append(source_id)
        return tuple(removed)

    def _parse_docx(
        self,
        source_path: Path,
        source_dir: Path,
    ) -> tuple[
        list[dict[str, Any]],
        str,
        dict[str, dict[str, str | None]],
        list[str],
        list[Path],
    ]:
        _validate_docx_archive(
            source_path,
            max_member_bytes=self.max_docx_member_bytes,
            max_expanded_bytes=self.max_docx_expanded_bytes,
        )
        file_bytes = source_path.read_bytes()
        parser_root = source_dir / "p"
        parser_root.mkdir(parents=True, exist_ok=True)
        parser_io_root = _extended_length_path(parser_root)
        parser_files: list[Path] = []
        try:
            import session_manager
            from question_bank.importers import docx_importer

            with _DOCX_PARSE_LOCK:
                with patch.object(
                    session_manager,
                    "_resolve_upload_config_dir",
                    lambda: str(parser_io_root),
                ), patch.object(
                    docx_importer,
                    "project_data_root",
                    lambda: parser_io_root,
                ):
                    document_text = session_manager.extract_docx_text(file_bytes)
                    blocks = session_manager.preview_question_blocks_from_docx_bytes(
                        file_bytes,
                        fallback_doc_text=document_text,
                    )
            parser_files = [path for path in parser_io_root.rglob("*") if path.is_file()]
            private_blocks, asset_files, created_files = _copy_docx_assets(
                blocks,
                source_dir=source_dir,
                parser_root=parser_io_root,
            )
            return private_blocks, document_text, asset_files, [], created_files
        finally:
            if parser_io_root.exists():
                parser_files = [path for path in parser_io_root.rglob("*") if path.is_file()]
            remove_exact_files(parser_files)

    def _parse_pdf(
        self,
        source_path: Path,
        source_dir: Path,
    ) -> tuple[
        list[dict[str, Any]],
        str,
        dict[str, dict[str, str | None]],
        list[str],
        list[Path],
    ]:
        try:
            import fitz

            document = fitz.open(source_path)
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
            extract_pdf_text,
        )
        from session_manager import preview_question_blocks_from_docx_text

        file_bytes = source_path.read_bytes()
        document_text = extract_pdf_text(file_bytes)
        blocks = preview_question_blocks_from_docx_text(document_text)
        raw_assets = extract_pdf_question_images(file_bytes, blocks) if blocks else {}
        raw_pages = extract_pdf_images(file_bytes)
        created_files: list[Path] = []
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
                _write_bytes_atomic(output, content)
                created_files.append(output)
                entry[kind] = output.name
            if entry["question"] or entry["answer"]:
                asset_files[question_id] = entry
        whole_page_files: list[str] = []
        for index, content in enumerate(raw_pages, start=1):
            if not isinstance(content, bytes) or not content:
                raise ConfigSourceInvalidError()
            suffix, _media_type = _image_type(content)
            output = source_dir / f"whole-page-{index:04d}{suffix}"
            _write_bytes_atomic(output, content)
            created_files.append(output)
            whole_page_files.append(output.name)
        return blocks, document_text, asset_files, whole_page_files, created_files

    def _record_from_manifest(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> ConfigSourceRecord:
        session_id = int(manifest.get("session_id"))
        source_id = str(manifest.get("source_id") or "")
        source_revision = str(manifest.get("source_revision") or "")
        safe_filename = str(manifest.get("safe_filename") or "")
        suffix = str(manifest.get("suffix") or "")
        sha256 = str(manifest.get("sha256") or "")
        size_bytes = int(manifest.get("size_bytes"))
        if (
            manifest.get("version") != 1
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
        if not isinstance(owned_names, list) or "manifest.json" not in owned_names:
            raise ConfigSourceInvalidError()
        owned = {_owned_path(manifest_path.parent, name).name for name in owned_names}
        source_file = str(manifest.get("source_file") or "")
        if source_file not in owned:
            raise ConfigSourceInvalidError()
        source_path = _owned_path(manifest_path.parent, source_file)
        if not source_path.is_file():
            raise ConfigSourceInvalidError()

        raw_questions = manifest.get("questions")
        if not isinstance(raw_questions, list):
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
        private_images: dict[str, dict[str, str | None]] = {}
        for question_id, raw_entry in asset_files.items():
            if not _QUESTION_ID.fullmatch(str(question_id)) or not isinstance(raw_entry, dict):
                raise ConfigSourceInvalidError()
            encoded: dict[str, str | None] = {"question": None, "answer": None}
            for kind in ("question", "answer"):
                filename = raw_entry.get(kind)
                if filename is None:
                    continue
                if not isinstance(filename, str) or filename not in owned:
                    raise ConfigSourceInvalidError()
                content = _owned_path(manifest_path.parent, filename).read_bytes()
                _image_type(content)
                encoded[kind] = base64.b64encode(content).decode("ascii")
            if encoded["question"] or encoded["answer"]:
                private_images[str(question_id)] = encoded

        whole_page_files = manifest.get("whole_page_files")
        if not isinstance(whole_page_files, list):
            raise ConfigSourceInvalidError()
        whole_pages: list[bytes] = []
        for filename in whole_page_files:
            if not isinstance(filename, str) or filename not in owned:
                raise ConfigSourceInvalidError()
            content = _owned_path(manifest_path.parent, filename).read_bytes()
            _image_type(content)
            whole_pages.append(content)
        return ConfigSourceRecord(
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
            safe_filename=safe_filename,
            suffix=suffix,
            size_bytes=size_bytes,
            sha256=sha256,
            questions=questions,
            manifest_path=manifest_path,
            private_source_path=source_path,
            private_blocks=tuple(copy.deepcopy(private_blocks)),
            private_document_text=document_text,
            private_question_images=private_images,
            private_whole_page_images=tuple(whole_pages),
        )

    def _prepare_source_dir(self, source_dir: Path) -> None:
        config_sources = source_dir.parent.parent
        session_dir = source_dir.parent
        for directory in (config_sources, session_dir, source_dir):
            if directory.exists() and (_is_reparse(directory) or not directory.is_dir()):
                raise ConfigSourceInvalidError()
            directory.mkdir(parents=True, exist_ok=True)
            self._assert_controlled_directory(directory)

    def _assert_controlled_directory(self, directory: Path) -> None:
        if _is_reparse(directory) or not directory.is_dir():
            raise ConfigSourceInvalidError()

    def _session_dir(self, session_id: int) -> Path:
        return self.upload_config_dir / "config_sources" / f"session-{int(session_id)}"

    def _source_dir(self, session_id: int, source_id: str) -> Path:
        return self._session_dir(session_id) / source_id

    def _active_path(self, session_id: int) -> Path:
        return self._session_dir(session_id) / "active.json"


def _positive_session_id(value: int) -> int:
    try:
        clean = int(value)
    except (TypeError, ValueError):
        raise ConfigSourceInvalidError() from None
    if clean <= 0:
        raise ConfigSourceInvalidError()
    return clean


def _safe_filename(filename: str) -> tuple[str, Literal[".docx", ".pdf"]]:
    raw = str(filename or "").strip().replace("\\", "/")
    safe = raw.rsplit("/", 1)[-1].strip()
    if not safe or safe in {".", ".."} or len(safe) > 255 or "\x00" in safe:
        raise ConfigSourceInvalidError()
    suffix = Path(safe).suffix.casefold()
    if suffix not in {".docx", ".pdf"}:
        raise ConfigSourceTypeUnsupportedError()
    return safe, suffix


def _validate_magic(path: Path, suffix: str) -> None:
    with path.open("rb") as stream:
        prefix = stream.read(1024)
    if suffix == ".docx" and not prefix.startswith(b"PK\x03\x04"):
        raise ConfigSourceTypeUnsupportedError()
    if suffix == ".pdf" and b"%PDF-" not in prefix:
        raise ConfigSourceTypeUnsupportedError()


def _validate_docx_archive(
    path: Path,
    *,
    max_member_bytes: int,
    max_expanded_bytes: int,
) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
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


def _copy_docx_assets(
    blocks: Sequence[dict[str, Any]],
    *,
    source_dir: Path,
    parser_root: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, str | None]],
    list[Path],
]:
    private_blocks = copy.deepcopy(list(blocks))
    asset_files: dict[str, dict[str, str | None]] = {}
    created_files: list[Path] = []
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
                    content = path.read_bytes()
                    suffix, _media_type = _image_type(content)
                except Exception:
                    continue
                output = source_dir / (
                    f"asset-{question_id}-{kind}-{copied_index + 1}{suffix}"
                )
                _write_bytes_atomic(output, content)
                created_files.append(output)
                replacements[raw_path] = str(output)
                copied_index += 1
                if entry[kind] is None:
                    entry[kind] = output.name
            if copied_index == 0:
                entry[kind] = None
        if entry["question"] or entry["answer"]:
            asset_files[question_id] = entry
        _replace_block_paths(block, replacements)
    return private_blocks, asset_files, created_files


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
        questions.append(
            ConfigQuestionPreview(
                question_id=question_id,
                question_type=str(block.get("question_type") or "comprehensive"),
                question_preview=_public_preview(question_value),
                answer_preview=_public_preview(answer_value),
                answer_present=bool(
                    str(block.get("answer_text") or "").strip()
                    or str(block.get("canonical_answer") or "").strip()
                    or assets.get("answer")
                ),
                needs_review=bool(block.get("needs_review")),
                local_answer_trusted=bool(block.get("local_answer_trusted")),
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


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigSourceInvalidError()
    return payload


def _owned_path(source_dir: Path, name: Any) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise ConfigSourceInvalidError()
    path = source_dir / name
    if _is_reparse(path):
        raise ConfigSourceInvalidError()
    if path.resolve(strict=False).parent != source_dir.resolve(strict=False):
        raise ConfigSourceInvalidError()
    return path


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
