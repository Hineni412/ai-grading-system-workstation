from __future__ import annotations

import json
import logging
import os
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

from backend.answer_regions.answer_region_draft_service import AnswerRegionDraftService
from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock
from backend.repositories.access import GradingRepositoryAccess
from path_manager import resolve_stored_file_path
from backend.config_generation.template_analyzer import create_template_mapping_package

TemplatePageRole = Literal["front", "back"]
LOGGER = logging.getLogger(__name__)
_FRONT_PAGE_FILENAME = "template_front_from_pdf_page.jpg"
_BACK_PAGE_FILENAME = "template_back_from_pdf_page.jpg"


class TemplateUploadError(ValueError):
    pass


class TemplateUploadTooLargeError(TemplateUploadError):
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

    def upload(
        self,
        *,
        db: GradingRepositoryAccess,
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
        version_path = session_dir / "template-versions" / f"upload-{uuid4().hex}"
        lease = _try_acquire_upload_lease(session_dir)
        if lease is None:
            raise TemplateUploadInProgressError(
                "another template upload is active for this session"
            )
        try:
            return self._upload_locked(
                db=db,
                session_id=session_id,
                pdf_bytes=pdf_bytes,
                first_page_role=first_page_role,
                session_dir=session_dir,
                version_path=version_path,
            )
        finally:
            _release_upload_lease(lease)

    def _upload_locked(
        self,
        *,
        db: GradingRepositoryAccess,
        session_id: int,
        pdf_bytes: bytes,
        first_page_role: TemplatePageRole,
        session_dir: Path,
        version_path: Path,
    ) -> TemplateUploadResult:
        with get_answer_region_session_lock(session_dir):
            session = db.sessions.get_grading_session(int(session_id))
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
                package_dir = temp_dir / "package"
                package_dir.mkdir()
                front_temp = package_dir / "template_front_from_pdf_page.jpg"
                back_temp = package_dir / "template_back_from_pdf_page.jpg"
                manifest_temp = package_dir / "template_upload_manifest.json"
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

                # The identity hashes the stored template path strings of the
                # final version directory, not the image contents.
                front_path = version_path / front_temp.name
                back_path = version_path / back_temp.name
                fingerprint = AnswerRegionDraftService(session_dir).compute_template_fingerprint(
                    front_path, back_path
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
                mapping_targets = {
                    key: version_path / path.name for key, path in mapping_sources.items()
                }
                template_id = db.templates.activate_session_template(
                    int(session_id),
                    front_template_path=str(front_path),
                    back_template_path=str(back_path),
                    ai_analysis_path=str(mapping_targets["ai_analysis_path"]),
                    template_config_path=str(mapping_targets["template_config_path"]),
                    regions_path=str(mapping_targets["regions_path"]),
                )
                template = db.templates.get_session_template(int(session_id))
                if template is None:
                    raise RuntimeError("template activation failed")
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
            template = db.templates.get_session_template(int(session_id))
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
            draft_service = AnswerRegionDraftService(session_dir)
            stored_paths = {
                page: str(template.get(f"{page}_template_path") or "")
                for page in ("front", "back")
            }
            fingerprint = draft_service.compute_template_fingerprint(
                stored_paths["front"], stored_paths["back"]
            )
            manifest_path = paths["front"].parent / "template_upload_manifest.json"
            manifest = _read_json(manifest_path)
            if not manifest:
                manifest_path = session_dir / "template_upload_manifest.json"
                manifest = _read_json(manifest_path)
            if not manifest:
                manifest_path = None
                manifest = {}
            manifest_fingerprint = str(manifest.get("template_fingerprint") or "")
            role = str(manifest.get("first_page_role") or "front")
            orientation = _generated_page_orientation(
                paths=paths,
                manifest=manifest,
                manifest_path=manifest_path,
                stored_paths=stored_paths,
                draft_service=draft_service,
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
            template = db.templates.get_session_template(int(session_id))
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
            manifest_path = paths["front"].parent / "template_upload_manifest.json"
            manifest = _read_json(manifest_path)
            if not manifest:
                manifest_path = session_dir / "template_upload_manifest.json"
                manifest = _read_json(manifest_path)
            if not manifest:
                manifest_path = None
                manifest = {}
            stored_paths = {
                page: str(template.get(f"{page}_template_path") or "")
                for page in ("front", "back")
            }
            orientation = _generated_page_orientation(
                paths=paths,
                manifest=manifest,
                manifest_path=manifest_path,
                stored_paths=stored_paths,
                draft_service=draft_service,
            )
            if orientation is None:
                raise TemplatePageAssignmentUnsupportedError(
                    "current template does not support page reassignment"
                )
            current_fingerprint = draft_service.compute_template_fingerprint(
                stored_paths["front"], stored_paths["back"]
            )
            reversed_fingerprint = draft_service.compute_template_fingerprint(
                stored_paths["back"], stored_paths["front"]
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

            changed = db.templates.swap_template_page_assignment(
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


def _generated_page_orientation(
    *,
    paths: dict[str, Path],
    manifest: dict[str, object],
    manifest_path: Path | None,
    stored_paths: dict[str, str],
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
        original_order = (stored_paths["front"], stored_paths["back"])
    elif (
        front_path.name == _BACK_PAGE_FILENAME
        and back_path.name == _FRONT_PAGE_FILENAME
    ):
        reversed_pages = True
        original_order = (stored_paths["back"], stored_paths["front"])
    else:
        return None
    role = str(manifest.get("first_page_role") or "")
    if role not in {"front", "back"}:
        return None
    manifest_fingerprint = str(manifest.get("template_fingerprint") or "")
    fingerprint_matches = manifest_fingerprint == draft_service.compute_template_fingerprint(
        *original_order
    )
    if not fingerprint_matches:
        # Older manifests recorded a hash of the image contents, which is not
        # recomputed here. A manifest sitting next to the rendered page pair is
        # still trustworthy: each version directory is written once and is
        # never mutated afterwards, and a page swap only exchanges the two
        # stored database paths.
        manifest_is_sibling = (
            manifest_path is not None
            and Path(manifest_path).parent == front_path.parent
        )
        if not manifest_is_sibling:
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


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateUploadError("saved scoring config is unavailable") from exc
    if not isinstance(value, dict):
        raise TemplateUploadError("saved scoring config is invalid")
    return value
