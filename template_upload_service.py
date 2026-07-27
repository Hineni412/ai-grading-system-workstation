from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO, Literal
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

import fitz
from PIL import Image

from answer_region_draft_service import AnswerRegionDraftService
from answer_region_session_lock import get_answer_region_session_lock
from backend.repositories.access import GradingRepositoryAccess
from path_manager import resolve_stored_file_path
from template_analyzer import create_template_mapping_package


TemplatePageRole = Literal["front", "back"]
LOGGER = logging.getLogger(__name__)
_FRONT_PAGE_FILENAME = "template_front_from_pdf_page.jpg"
_BACK_PAGE_FILENAME = "template_back_from_pdf_page.jpg"


class TemplateUploadError(ValueError):
    pass


class TemplateUploadTooLargeError(TemplateUploadError):
    pass


class TemplateUploadSubmissionConflictError(TemplateUploadError):
    pass


class TemplateUploadInProgressError(TemplateUploadError):
    pass


class TemplatePageAssignmentConflictError(TemplateUploadError):
    pass


class TemplatePageAssignmentDraftConflictError(TemplateUploadError):
    pass


class TemplatePageAssignmentUnsupportedError(TemplateUploadError):
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


@dataclass(frozen=True)
class TemplatePageAssignmentResult:
    changed: bool
    draft_sync_pending: bool
    template: TemplateUploadResult


@dataclass(frozen=True)
class _TemplatePageOrientation:
    first_page_role: TemplatePageRole
    reversed: bool


class TemplateUploadService:
    def __init__(self, templates_dir: Path, *, max_upload_bytes: int = 100 * 1024 * 1024) -> None:
        self.templates_dir = Path(templates_dir)
        self.max_upload_bytes = int(max_upload_bytes)
        self._active_guard = threading.Lock()
        self._active_leases: dict[tuple[int, str], BinaryIO] = {}

    def upload(
        self,
        *,
        db: GradingRepositoryAccess,
        session_id: int,
        pdf_bytes: bytes,
        first_page_role: TemplatePageRole,
        request_token: str | None = None,
    ) -> TemplateUploadResult:
        if first_page_role not in {"front", "back"}:
            raise TemplateUploadError("invalid first page role")
        if not pdf_bytes or len(pdf_bytes) > self.max_upload_bytes:
            if len(pdf_bytes) > self.max_upload_bytes:
                raise TemplateUploadTooLargeError("template upload is too large")
            raise TemplateUploadError("template upload is empty")
        activated_request_token = (
            _request_token(request_token) if request_token is not None else None
        )

        session_dir = self.templates_dir / f"session_{int(session_id)}"
        version_id = activated_request_token or f"legacy-{uuid4().hex}"
        version_path = session_dir / "template-versions" / version_id
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
            if version_path.exists():
                if activated_request_token is not None and _current_activation_token(
                    db, session_dir, int(session_id)
                ) == activated_request_token:
                    return self.load_current(db=db, session_id=session_id)
                shutil.rmtree(version_path)
            with TemporaryDirectory(prefix="template-upload-", dir=session_dir) as raw_temp_dir:
                temp_dir = Path(raw_temp_dir)
                package_dir = temp_dir / "package"
                package_dir.mkdir()
                source_temp = package_dir / "template_source_full_class.pdf"
                front_temp = package_dir / "template_front_from_pdf_page.jpg"
                back_temp = package_dir / "template_back_from_pdf_page.jpg"
                manifest_temp = package_dir / "template_upload_manifest.json"
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

                fingerprint = AnswerRegionDraftService(session_dir).compute_template_fingerprint(
                    front_temp, back_temp
                )
                _write_manifest(
                    manifest_temp,
                    first_page_role=first_page_role,
                    template_fingerprint=fingerprint,
                    front_size=front_size,
                    back_size=back_size,
                    request_token=activated_request_token,
                )
                try:
                    mapping_package = create_template_mapping_package(
                        front_temp,
                        back_temp,
                        rubric=rubric,
                        answer_key=answer_key,
                        output_dir=package_dir,
                    )
                except (TypeError, ValueError) as exc:
                    raise TemplateUploadError("saved scoring config cannot build a template") from exc
                mapping_sources = {
                    "ai_analysis_path": Path(mapping_package["paths"]["raw_path"]),
                    "template_config_path": Path(mapping_package["paths"]["config_path"]),
                    "regions_path": Path(mapping_package["paths"]["regions_path"]),
                }
                version_path.parent.mkdir(parents=True, exist_ok=True)
                package_dir.replace(version_path)
                source_path = version_path / source_temp.name
                front_path = version_path / front_temp.name
                back_path = version_path / back_temp.name
                mapping_targets = {
                    key: version_path / path.name for key, path in mapping_sources.items()
                }
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
                if activated_request_token is not None:
                    try:
                        _write_activation_receipt(
                            _activation_path(session_dir, activated_request_token),
                            request_token=activated_request_token,
                            first_page_role=first_page_role,
                            template_fingerprint=fingerprint,
                            front_size=front_size,
                            back_size=back_size,
                        )
                    except OSError:
                        # The database points only at a fully published immutable package.
                        # A later query or upload start can reconstruct this receipt.
                        pass
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

    def load_current(
        self,
        *,
        db: GradingRepositoryAccess,
        session_id: int,
    ) -> TemplateUploadResult:
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
            manifest = (
                _read_json(paths["front"].parent / "template_upload_manifest.json")
                or _read_json(session_dir / "template_upload_manifest.json")
                or {}
            )
            manifest_fingerprint = str(manifest.get("template_fingerprint") or "")
            role = str(manifest.get("first_page_role") or "front")
            orientation = _generated_page_orientation(
                paths=paths,
                manifest=manifest,
                draft_service=AnswerRegionDraftService(session_dir),
            )
            if orientation is not None:
                first_page_role = orientation.first_page_role
            else:
                first_page_role = (
                    role
                    if manifest_fingerprint == fingerprint and role in {"front", "back"}
                    else "front"
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

    def assign_first_page_role(
        self,
        *,
        db: GradingRepositoryAccess,
        session_id: int,
        first_page_role: TemplatePageRole,
        expected_template_fingerprint: str,
    ) -> TemplatePageAssignmentResult:
        if first_page_role not in {"front", "back"}:
            raise TemplatePageAssignmentConflictError("invalid first page role")
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
            draft_service = AnswerRegionDraftService(session_dir)
            manifest = (
                _read_json(paths["front"].parent / "template_upload_manifest.json")
                or _read_json(session_dir / "template_upload_manifest.json")
                or {}
            )
            orientation = _generated_page_orientation(
                paths=paths,
                manifest=manifest,
                draft_service=draft_service,
            )
            if orientation is None:
                raise TemplatePageAssignmentUnsupportedError(
                    "current template does not support page reassignment"
                )
            current_fingerprint = draft_service.compute_template_fingerprint(
                paths["front"], paths["back"]
            )
            reversed_fingerprint = draft_service.compute_template_fingerprint(
                paths["back"], paths["front"]
            )
            requested_is_current = first_page_role == orientation.first_page_role
            expected_matches = expected_template_fingerprint == current_fingerprint
            retry_matches = (
                requested_is_current
                and expected_template_fingerprint == reversed_fingerprint
            )
            if not expected_matches and not retry_matches:
                raise TemplatePageAssignmentConflictError(
                    "session template changed before page reassignment"
                )

            draft_result = draft_service.load(
                expected_template_fingerprint=current_fingerprint
            )
            if (
                draft_result.draft is not None
                and int(draft_result.draft.get("session_id", 0)) != int(session_id)
            ):
                raise TemplatePageAssignmentDraftConflictError(
                    "answer region draft belongs to a different session"
                )
            recoverable_draft = bool(
                requested_is_current
                and draft_result.status == "incompatible"
                and draft_result.draft is not None
                and draft_result.draft.get("template_fingerprint")
                == reversed_fingerprint
            )
            if draft_result.status in {"incompatible", "corrupt"} and not recoverable_draft:
                raise TemplatePageAssignmentDraftConflictError(
                    "answer region draft must be resolved before page reassignment"
                )

            if requested_is_current:
                draft_sync_pending = False
                if recoverable_draft and draft_result.draft is not None:
                    draft_sync_pending = not _save_swapped_draft(
                        draft_service,
                        session_id=int(session_id),
                        draft=draft_result.draft,
                        template_fingerprint=current_fingerprint,
                    )
                return TemplatePageAssignmentResult(
                    changed=False,
                    draft_sync_pending=draft_sync_pending,
                    template=self.load_current(db=db, session_id=session_id),
                )

            changed = db.swap_template_page_assignment(
                int(session_id),
                int(template["id"]),
                expected_front_path=str(template.get("front_template_path") or ""),
                expected_back_path=str(template.get("back_template_path") or ""),
            )
            if not changed:
                raise TemplatePageAssignmentConflictError(
                    "session template changed before page reassignment"
                )
            current = self.load_current(db=db, session_id=session_id)
            draft_sync_pending = False
            if draft_result.status == "compatible" and draft_result.draft is not None:
                draft_sync_pending = not _save_swapped_draft(
                    draft_service,
                    session_id=int(session_id),
                    draft=draft_result.draft,
                    template_fingerprint=current.template_fingerprint,
                )
            return TemplatePageAssignmentResult(
                changed=True,
                draft_sync_pending=draft_sync_pending,
                template=current,
            )

    def begin_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        filename: str,
        content_length: int | None,
        first_page_role: TemplatePageRole,
        content_sha256: str,
        db: GradingRepositoryAccess | None = None,
    ) -> str:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        fingerprint = _request_fingerprint(
            filename=filename,
            content_length=content_length,
            first_page_role=first_page_role,
            content_sha256=_content_sha256(content_sha256),
        )
        marker_path = _submission_path(session_dir, token)
        lease = _try_acquire_upload_lease(session_dir)
        if lease is None:
            raise TemplateUploadInProgressError(
                "another template upload is active for this session"
            )
        retain_lease = False
        try:
            with get_answer_region_session_lock(session_dir):
                current_token = (
                    _current_activation_token(db, session_dir, int(session_id))
                    if db is not None else None
                )
                if current_token is not None:
                    _recover_activated_submission(
                        session_dir, current_token, db, int(session_id)
                    )
                    recovered_marker = _read_json(_submission_path(session_dir, current_token))
                    if (current_token != token
                            and not _has_reliable_activation_receipt(session_dir, current_token)
                            and (recovered_marker is None
                                 or recovered_marker.get("status") != "succeeded")):
                        raise TemplateUploadInProgressError(
                            "current template activation history is not yet durable"
                        )
                marker = _read_json(marker_path)
                if (marker is not None
                        and marker.get("request_fingerprint") not in {None, fingerprint}):
                    raise TemplateUploadSubmissionConflictError(
                        "template upload token was reused for another request"
                    )
                if marker is not None and marker.get("status") != "processing":
                    if marker.get("status") == "abandoned":
                        return "abandoned"
                    return str(marker.get("status") or "failed")
                for processing_path in session_dir.glob("template-submission-*.json"):
                    processing_marker = _read_json(processing_path)
                    processing_token = _submission_token_from_path(processing_path)
                    if (processing_marker is None
                            or processing_marker.get("status") != "processing"
                            or processing_token is None):
                        continue
                    if processing_token == current_token:
                        _recover_activated_submission(
                            session_dir, processing_token, db, int(session_id)
                        )
                    elif _has_reliable_activation_receipt(session_dir, processing_token):
                        _write_json_atomic(processing_path, {
                            "status": "replaced",
                            "request_fingerprint": processing_marker.get("request_fingerprint"),
                        })
                    elif processing_token != token:
                        _write_json_atomic(processing_path, {
                            "status": "abandoned",
                            "request_fingerprint": processing_marker.get("request_fingerprint"),
                        })
                session_dir.mkdir(parents=True, exist_ok=True)
                _write_json_atomic(
                    marker_path,
                    {"status": "processing", "request_fingerprint": fingerprint},
                )
                with self._active_guard:
                    key = (int(session_id), token)
                    self._active_leases[key] = lease
                retain_lease = True
                return "started"
        finally:
            if not retain_lease:
                _release_upload_lease(lease)

    def abandon_submission(
        self,
        *,
        session_id: int,
        request_token: str,
        db: GradingRepositoryAccess | None = None,
    ) -> None:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        marker_path = _submission_path(session_dir, token)
        lease = _try_acquire_upload_lease(session_dir)
        if lease is None:
            raise TemplateUploadSubmissionConflictError(
                "template upload submission is still active"
            )
        try:
            with get_answer_region_session_lock(session_dir):
                marker = _read_json(marker_path)
                current_token = (
                    _current_activation_token(db, session_dir, int(session_id))
                    if db is not None else None
                )
                if current_token == token:
                    _recover_activated_submission(
                        session_dir, token, db, int(session_id)
                    )
                if (_has_reliable_activation_receipt(session_dir, token)
                        or current_token == token
                        or (marker is not None and marker.get("status") != "processing")):
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
        finally:
            _release_upload_lease(lease)

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
                key = (int(session_id), token)
                lease = self._active_leases.pop(key, None)
            if lease is not None:
                _release_upload_lease(lease)

    def submission_public(
        self,
        *,
        session_id: int,
        request_token: str,
        db: GradingRepositoryAccess | None = None,
    ) -> dict[str, object]:
        token = _request_token(request_token)
        session_dir = self.templates_dir / f"session_{int(session_id)}"
        with get_answer_region_session_lock(session_dir):
            marker_path = _submission_path(session_dir, token)
            marker = _read_json(marker_path)
            current_token = (
                _current_activation_token(db, session_dir, int(session_id))
                if db is not None else None
            )
            if current_token == token and db is not None:
                return _recover_activated_submission(
                    session_dir, token, db, int(session_id)
                )
            if marker is None:
                raise FileNotFoundError("template upload submission is missing")
            status = str(marker.get("status") or "failed")
            activated = _has_reliable_activation_receipt(session_dir, token)
            if activated and current_token != token:
                try:
                    _write_json_atomic(marker_path, {
                        "status": "replaced",
                        "request_fingerprint": marker.get("request_fingerprint"),
                    })
                except OSError:
                    pass
                return {"status": "replaced", "template": None}
            if status == "succeeded" and current_token is not None and current_token != token:
                try:
                    _write_json_atomic(marker_path, {
                        "status": "replaced",
                        "request_fingerprint": marker.get("request_fingerprint"),
                    })
                except OSError:
                    pass
                return {"status": "replaced", "template": None}
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


def _submission_template(result: TemplateUploadResult) -> dict[str, object]:
    return {
        "session_id": result.session_id,
        "template_id": result.template_id,
        "template_fingerprint": result.template_fingerprint,
        "first_page_role": result.first_page_role,
        "pages": {
            page: {
                "url": f"/api/sessions/{result.session_id}/template/pages/{page}",
                "width": getattr(result, page).width,
                "height": getattr(result, page).height,
            }
            for page in ("front", "back")
        },
        "is_confirmed": result.is_confirmed,
        "regions_snapshot_pending": result.regions_snapshot_pending,
    }


def _write_manifest(
    path: Path,
    *,
    first_page_role: TemplatePageRole,
    template_fingerprint: str,
    front_size: tuple[int, int],
    back_size: tuple[int, int],
    request_token: str | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "first_page_role": first_page_role,
        "template_fingerprint": template_fingerprint,
        "image_sizes": {"front": list(front_size), "back": list(back_size)},
        "request_token": request_token,
    }
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _generated_page_orientation(
    *,
    paths: dict[str, Path],
    manifest: dict[str, object],
    draft_service: AnswerRegionDraftService,
) -> _TemplatePageOrientation | None:
    front_path = paths["front"]
    back_path = paths["back"]
    if front_path.parent != back_path.parent:
        return None
    if (
        front_path.name == _FRONT_PAGE_FILENAME
        and back_path.name == _BACK_PAGE_FILENAME
    ):
        reversed_pages = False
        original_front = front_path
        original_back = back_path
    elif (
        front_path.name == _BACK_PAGE_FILENAME
        and back_path.name == _FRONT_PAGE_FILENAME
    ):
        reversed_pages = True
        original_front = back_path
        original_back = front_path
    else:
        return None
    role = str(manifest.get("first_page_role") or "")
    if role not in {"front", "back"}:
        return None
    manifest_fingerprint = str(manifest.get("template_fingerprint") or "")
    original_fingerprint = draft_service.compute_template_fingerprint(
        original_front,
        original_back,
    )
    if manifest_fingerprint != original_fingerprint:
        return None
    original_role: TemplatePageRole = "front" if role == "front" else "back"
    current_role = (
        _opposite_page_role(original_role) if reversed_pages else original_role
    )
    return _TemplatePageOrientation(
        first_page_role=current_role,
        reversed=reversed_pages,
    )


def _opposite_page_role(role: TemplatePageRole) -> TemplatePageRole:
    return "back" if role == "front" else "front"


def _save_swapped_draft(
    draft_service: AnswerRegionDraftService,
    *,
    session_id: int,
    draft: dict[str, object],
    template_fingerprint: str,
) -> bool:
    regions = []
    for raw_region in draft.get("regions", []):
        if not isinstance(raw_region, dict):
            continue
        region = dict(raw_region)
        if region.get("page") == "front":
            region["page"] = "back"
        elif region.get("page") == "back":
            region["page"] = "front"
        region["is_confirmed"] = False
        regions.append(region)
    try:
        draft_service.save(
            session_id=int(session_id),
            template_fingerprint=template_fingerprint,
            revision=int(draft.get("revision", 0)) + 1,
            regions=regions,
        )
    except Exception:
        LOGGER.exception(
            "template_page_assignment_draft_sync_failed session_id=%s",
            int(session_id),
        )
        return False
    return True


def _write_activation_receipt(
    path: Path,
    *,
    request_token: str,
    first_page_role: TemplatePageRole,
    template_fingerprint: str,
    front_size: tuple[int, int],
    back_size: tuple[int, int],
) -> None:
    _write_json_atomic(path, {
        "schema_version": 2,
        "request_token": request_token,
        "first_page_role": first_page_role,
        "template_fingerprint": template_fingerprint,
        "image_sizes": {"front": list(front_size), "back": list(back_size)},
    })


_REQUEST_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def _request_token(value: str) -> str:
    token = str(value or "").strip().lower()
    if not _REQUEST_TOKEN.fullmatch(token):
        raise TemplateUploadError("invalid template upload request token")
    return token


def _request_fingerprint(
    *,
    filename: str,
    content_length: int | None,
    first_page_role: TemplatePageRole,
    content_sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "filename": str(filename),
            "content_length": content_length,
            "first_page_role": first_page_role,
            "content_sha256": content_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise TemplateUploadError("invalid template upload content digest")
    return digest


def _submission_path(session_dir: Path, request_token: str) -> Path:
    return session_dir / f"template-submission-{request_token}.json"


def _activation_path(session_dir: Path, request_token: str) -> Path:
    return session_dir / f"template-activation-{request_token}.json"


def _has_reliable_activation_receipt(session_dir: Path, request_token: str) -> bool:
    receipt = _read_json(_activation_path(session_dir, request_token))
    return bool(
        receipt is not None
        and receipt.get("schema_version") == 2
        and receipt.get("request_token") == request_token
    )


def _current_activation_token(
    db: GradingRepositoryAccess,
    session_dir: Path,
    session_id: int,
) -> str | None:
    template = db.get_session_template(int(session_id))
    if template is None:
        return None
    try:
        front = resolve_stored_file_path(
            template.get("front_template_path"),
            search_roots=[session_dir, session_dir.parent],
        )
        back = resolve_stored_file_path(
            template.get("back_template_path"),
            search_roots=[session_dir, session_dir.parent],
        )
    except (FileNotFoundError, TypeError, ValueError):
        return None
    if (front.parent != back.parent
            or front.parent.parent.name != "template-versions"
            or not front.is_file()
            or not back.is_file()):
        return None
    token = front.parent.name
    if _REQUEST_TOKEN.fullmatch(token) is None:
        return None
    return token


def _recover_activated_submission(
    session_dir: Path,
    request_token: str,
    db: GradingRepositoryAccess,
    session_id: int,
) -> dict[str, object]:
    current = TemplateUploadService(session_dir.parent).load_current(
        db=db, session_id=int(session_id)
    )
    template = _submission_template(current)
    marker_path = _submission_path(session_dir, request_token)
    marker = _read_json(marker_path) or {}
    try:
        _write_activation_receipt(
            _activation_path(session_dir, request_token),
            request_token=request_token,
            first_page_role=current.first_page_role,
            template_fingerprint=current.template_fingerprint,
            front_size=(current.front.width, current.front.height),
            back_size=(current.back.width, current.back.height),
        )
    except OSError:
        pass
    try:
        _write_json_atomic(marker_path, {
            "status": "succeeded",
            "request_fingerprint": marker.get("request_fingerprint"),
            "template": template,
        })
    except OSError:
        pass
    return {"status": "succeeded", "template": template}


def _try_acquire_upload_lease(session_dir: Path) -> BinaryIO | None:
    session_dir.mkdir(parents=True, exist_ok=True)
    lease = (session_dir / ".template_upload.lock").open("a+b")
    try:
        lease.seek(0, os.SEEK_END)
        if lease.tell() == 0:
            lease.write(b"\0")
            lease.flush()
            os.fsync(lease.fileno())
        lease.seek(0)
        if os.name == "nt":
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lease
    except OSError:
        lease.close()
        return None


def _release_upload_lease(lease: BinaryIO) -> None:
    try:
        lease.seek(0)
        if os.name == "nt":
            msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
    finally:
        lease.close()


def _submission_token_from_path(path: Path) -> str | None:
    match = re.fullmatch(r"template-submission-([0-9a-f]{32})\.json", path.name)
    return match.group(1) if match is not None else None


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
