from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import fitz
from PIL import Image

from answer_region_draft_service import AnswerRegionDraftService
from answer_region_session_lock import get_answer_region_session_lock
from db_manager import DBManager
from path_manager import resolve_stored_file_path
from template_analyzer import create_template_mapping_package


TemplatePageRole = Literal["front", "back"]


class TemplateUploadError(ValueError):
    pass


class TemplateUploadTooLargeError(TemplateUploadError):
    pass


class TemplateUploadSubmissionConflictError(TemplateUploadError):
    pass


@dataclass(frozen=True)
class TemplatePage:
    path: Path
    width: int
    height: int


@dataclass(frozen=True)
class TemplateUploadResult:
    session_id: int
    template_id: int
    template_fingerprint: str
    first_page_role: TemplatePageRole
    front: TemplatePage
    back: TemplatePage
    is_confirmed: bool
    regions_snapshot_pending: bool


class TemplateUploadService:
    def __init__(self, templates_dir: Path, *, max_upload_bytes: int = 100 * 1024 * 1024) -> None:
        self.templates_dir = Path(templates_dir)
        self.max_upload_bytes = int(max_upload_bytes)
        self._active_guard = threading.Lock()
        self._active_submissions: set[tuple[int, str]] = set()

    def upload(
        self,
        *,
        db: DBManager,
        session_id: int,
        pdf_bytes: bytes,
        first_page_role: TemplatePageRole,
    ) -> TemplateUploadResult:
        if first_page_role not in {"front", "back"}:
            raise TemplateUploadError("invalid first page role")
        if not pdf_bytes or len(pdf_bytes) > self.max_upload_bytes:
            if len(pdf_bytes) > self.max_upload_bytes:
                raise TemplateUploadTooLargeError("template upload is too large")
            raise TemplateUploadError("template upload is empty")

        session_dir = self.templates_dir / f"session_{int(session_id)}"
        session_dir.mkdir(parents=True, exist_ok=True)
        with get_answer_region_session_lock(session_dir):
            session = db.get_grading_session(int(session_id))
            if session is None:
                raise TemplateUploadError("grading session is missing")
            rubric = _load_json(
                resolve_stored_file_path(
                    session.get("rubric_path"),
                    search_roots=[session_dir, self.templates_dir, Path(db.db_path).parent],
                )
            )
            answer_key = _load_json(
                resolve_stored_file_path(
                    session.get("answer_key_path"),
                    search_roots=[session_dir, self.templates_dir, Path(db.db_path).parent],
                )
            )
            with TemporaryDirectory(prefix="template-upload-", dir=session_dir) as raw_temp_dir:
                temp_dir = Path(raw_temp_dir)
                source_temp = temp_dir / "template_source_full_class.pdf"
                front_temp = temp_dir / "template_front_from_pdf_page.jpg"
                back_temp = temp_dir / "template_back_from_pdf_page.jpg"
                manifest_temp = temp_dir / "template_upload_manifest.json"
                source_temp.write_bytes(pdf_bytes)
                document = self._open_pdf(pdf_bytes)
                try:
                    if document.page_count < 2:
                        raise TemplateUploadError("template PDF must contain at least two pages")
                    front_index = 0 if first_page_role == "front" else 1
                    back_index = 1 if first_page_role == "front" else 0
                    front_size = _render_page(document, front_index, front_temp)
                    back_size = _render_page(document, back_index, back_temp)
                finally:
                    document.close()

                source_path = session_dir / "template_source_full_class.pdf"
                front_path = session_dir / "template_front_from_pdf_page.jpg"
                back_path = session_dir / "template_back_from_pdf_page.jpg"
                fingerprint = AnswerRegionDraftService(session_dir).compute_template_fingerprint(
                    front_temp, back_temp
                )
                _write_manifest(
                    manifest_temp,
                    first_page_role=first_page_role,
                    template_fingerprint=fingerprint,
                    front_size=front_size,
                    back_size=back_size,
                )
                try:
                    mapping_package = create_template_mapping_package(
                        front_temp,
                        back_temp,
                        rubric=rubric,
                        answer_key=answer_key,
                        output_dir=temp_dir,
                    )
                except (TypeError, ValueError) as exc:
                    raise TemplateUploadError("saved scoring config cannot build a template") from exc
                mapping_sources = {
                    "ai_analysis_path": Path(mapping_package["paths"]["raw_path"]),
                    "template_config_path": Path(mapping_package["paths"]["config_path"]),
                    "regions_path": Path(mapping_package["paths"]["regions_path"]),
                }
                mapping_targets = {
                    key: session_dir / path.name for key, path in mapping_sources.items()
                }
                targets = [
                    (source_temp, source_path),
                    (front_temp, front_path),
                    (back_temp, back_path),
                    (manifest_temp, session_dir / "template_upload_manifest.json"),
                    *[
                        (mapping_sources[key], mapping_targets[key])
                        for key in ("ai_analysis_path", "template_config_path", "regions_path")
                    ],
                ]
                backups = _backup_targets(targets, temp_dir)
                try:
                    for staged, target in targets:
                        staged.replace(target)
                    template_id = db.activate_session_template(
                        int(session_id),
                        front_template_path=str(front_path),
                        back_template_path=str(back_path),
                        ai_analysis_path=str(mapping_targets["ai_analysis_path"]),
                        template_config_path=str(mapping_targets["template_config_path"]),
                        regions_path=str(mapping_targets["regions_path"]),
                    )
                    template = db.get_session_template(int(session_id))
                    if template is None:
                        raise RuntimeError("template activation failed")
                except BaseException:
                    _restore_targets(targets, backups)
                    raise
            return TemplateUploadResult(
                session_id=int(session_id),
                template_id=template_id,
                template_fingerprint=fingerprint,
                first_page_role=first_page_role,
                front=TemplatePage(front_path, *front_size),
                back=TemplatePage(back_path, *back_size),
                is_confirmed=bool(template.get("is_confirmed")),
                regions_snapshot_pending=bool(template.get("regions_snapshot_pending")),
            )

    def load_current(self, *, db: DBManager, session_id: int) -> TemplateUploadResult:
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        with get_answer_region_session_lock(session_dir):
            template = db.get_session_template(int(session_id))
            if template is None:
                raise FileNotFoundError("session template is missing")
            paths = {
                role: resolve_stored_file_path(
                    template.get(f"{role}_template_path"),
                    search_roots=[session_dir, self.templates_dir],
                )
                for role in ("front", "back")
            }
            if not all(path.is_file() for path in paths.values()):
                raise TemplateUploadError("session template file is missing")
            sizes: dict[str, tuple[int, int]] = {}
            for role, path in paths.items():
                with Image.open(path) as image:
                    sizes[role] = (int(image.width), int(image.height))
            fingerprint = AnswerRegionDraftService(session_dir).compute_template_fingerprint(
                paths["front"], paths["back"]
            )
            manifest = _read_json(session_dir / "template_upload_manifest.json") or {}
            manifest_fingerprint = str(manifest.get("template_fingerprint") or "")
            role = str(manifest.get("first_page_role") or "front")
            first_page_role: TemplatePageRole = (
                role if manifest_fingerprint == fingerprint and role in {"front", "back"} else "front"
            )
            return TemplateUploadResult(
                session_id=int(session_id),
                template_id=int(template["id"]),
                template_fingerprint=fingerprint,
                first_page_role=first_page_role,
                front=TemplatePage(paths["front"], *sizes["front"]),
                back=TemplatePage(paths["back"], *sizes["back"]),
                is_confirmed=bool(template.get("is_confirmed")),
                regions_snapshot_pending=bool(template.get("regions_snapshot_pending")),
            )

    def begin_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        filename: str,
        content_length: int | None,
        first_page_role: TemplatePageRole,
    ) -> str:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        fingerprint = _request_fingerprint(
            filename=filename,
            content_length=content_length,
            first_page_role=first_page_role,
        )
        marker_path = _submission_path(session_dir, token)
        with get_answer_region_session_lock(session_dir):
            marker = _read_json(marker_path)
            if marker is not None:
                if marker.get("status") == "abandoned":
                    return "abandoned"
                if marker.get("request_fingerprint") != fingerprint:
                    raise TemplateUploadSubmissionConflictError(
                        "template upload token was reused for another request"
                    )
                return str(marker.get("status") or "failed")
            session_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(
                marker_path,
                {"status": "processing", "request_fingerprint": fingerprint},
            )
            with self._active_guard:
                self._active_submissions.add((int(session_id), token))
            return "started"

    def abandon_submission(self, *, session_id: int, request_token: str) -> None:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        marker_path = _submission_path(session_dir, token)
        with get_answer_region_session_lock(session_dir):
            marker = _read_json(marker_path)
            with self._active_guard:
                active = (int(session_id), token) in self._active_submissions
            if active or (marker is not None and marker.get("status") != "processing"):
                raise TemplateUploadSubmissionConflictError(
                    "template upload submission already exists"
                )
            _write_json_atomic(
                marker_path,
                {
                    "status": "abandoned",
                    "request_fingerprint": (
                        marker.get("request_fingerprint") if marker is not None else None
                    ),
                },
            )

    def finish_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        succeeded: bool,
        template: dict[str, object] | None = None,
    ) -> None:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        marker_path = _submission_path(session_dir, token)
        try:
            with get_answer_region_session_lock(session_dir):
                marker = _read_json(marker_path)
                if marker is None:
                    raise TemplateUploadError("template upload submission is missing")
                next_marker = {
                    "status": "succeeded" if succeeded else "failed",
                    "request_fingerprint": marker.get("request_fingerprint"),
                }
                if succeeded and template is not None:
                    next_marker["template"] = template
                _write_json_atomic(marker_path, next_marker)
        finally:
            with self._active_guard:
                self._active_submissions.discard((int(session_id), token))

    def submission_public(self, *, session_id: int, request_token: str) -> dict[str, object]:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        with get_answer_region_session_lock(session_dir):
            marker = _read_json(_submission_path(session_dir, token))
        if marker is None:
            raise FileNotFoundError("template upload submission is missing")
        status = str(marker.get("status") or "failed")
        return {
            "status": status,
            "template": marker.get("template") if status == "succeeded" else None,
        }

    @staticmethod
    def _open_pdf(pdf_bytes: bytes) -> fitz.Document:
        try:
            return fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise TemplateUploadError("template upload is not a valid PDF") from exc


def _render_page(document: fitz.Document, page_index: int, output_path: Path) -> tuple[int, int]:
    page = document.load_page(page_index)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    image.save(output_path, format="JPEG", quality=90)
    return int(pixmap.width), int(pixmap.height)


def _write_manifest(
    path: Path,
    *,
    first_page_role: TemplatePageRole,
    template_fingerprint: str,
    front_size: tuple[int, int],
    back_size: tuple[int, int],
) -> None:
    payload = {
        "schema_version": 1,
        "first_page_role": first_page_role,
        "template_fingerprint": template_fingerprint,
        "image_sizes": {"front": list(front_size), "back": list(back_size)},
    }
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


_REQUEST_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def _request_token(value: str) -> str:
    token = str(value or "").strip().lower()
    if not _REQUEST_TOKEN.fullmatch(token):
        raise TemplateUploadError("invalid template upload request token")
    return token


def _request_fingerprint(
    *, filename: str, content_length: int | None, first_page_role: TemplatePageRole
) -> str:
    canonical = json.dumps(
        {
            "filename": str(filename),
            "content_length": content_length,
            "first_page_role": first_page_role,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _submission_path(session_dir: Path, request_token: str) -> Path:
    return session_dir / f"template-submission-{request_token}.json"


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateUploadError("saved scoring config is unavailable") from exc
    if not isinstance(value, dict):
        raise TemplateUploadError("saved scoring config is invalid")
    return value


def _backup_targets(
    targets: list[tuple[Path, Path]], temp_dir: Path
) -> dict[Path, Path | None]:
    backups: dict[Path, Path | None] = {}
    for index, (_staged, target) in enumerate(targets):
        if not target.is_file():
            backups[target] = None
            continue
        backup = temp_dir / f"previous-{index}.bak"
        shutil.copy2(target, backup)
        backups[target] = backup
    return backups


def _restore_targets(
    targets: list[tuple[Path, Path]], backups: dict[Path, Path | None]
) -> None:
    for _staged, target in targets:
        backup = backups[target]
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            backup.replace(target)
