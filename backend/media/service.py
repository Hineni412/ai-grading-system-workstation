from __future__ import annotations

import hashlib
import json
import os
import threading
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageDraw, UnidentifiedImageError

from answer_region_geometry import attach_region_source_image_sizes, scaled_region_bbox
from backend.file_access import (
    ControlledFileError,
    ResolvedFile,
    resolve_controlled_file,
)
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from question_id_contract import question_id_coordinates


IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})
_CROP_RENDER_VERSION = "review-crop-v1"
_DEFAULT_CROP_CACHE_BYTES = 256 * 1024 * 1024
_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_LOCKS: dict[Path, threading.RLock] = {}
_MAINTAINED_CACHE_DIRS: set[Path] = set()


class ReviewMediaNotFound(LookupError):
    """Raised when IDs do not identify one owned review media resource."""


class ReviewMediaUnreadable(ValueError):
    """Raised when an allowlisted image cannot be decoded."""


class ReviewMediaService:
    def __init__(
        self,
        db: GradingRepositoryAccess,
        *,
        data_root: Path,
        exams_dir: Path,
        templates_dir: Path,
        annotated_dir: Path,
        crop_cache_dir: Path | None = None,
        max_crop_cache_bytes: int = _DEFAULT_CROP_CACHE_BYTES,
        enable_crop_cache: bool = True,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.data_root = Path(data_root)
        self.exams_dir = Path(exams_dir)
        self.templates_dir = Path(templates_dir)
        self.annotated_dir = Path(annotated_dir)
        self.crop_cache_dir = Path(
            crop_cache_dir or self.data_root / "cache" / "review_crops"
        )
        if int(max_crop_cache_bytes) <= 0:
            raise ValueError("max_crop_cache_bytes must be positive")
        self.max_crop_cache_bytes = int(max_crop_cache_bytes)
        self.enable_crop_cache = bool(enable_crop_cache)
        self._crop_cache_lock = _cache_lock(self.crop_cache_dir)

    def resolve_result_page(
        self,
        session_id: int,
        result_id: int,
        page: str,
        variant: str,
    ) -> ResolvedFile:
        normalized_page = str(page or "").strip().lower()
        normalized_variant = str(variant or "").strip().lower()
        if normalized_page not in {"front", "back"}:
            raise ReviewMediaNotFound("Review media resource was not found.")

        if normalized_variant == "original":
            context = self.db.results.get_result_context(int(result_id))
            if context is None or int(context["session_id"]) != int(session_id):
                raise ReviewMediaNotFound("Review media resource was not found.")
            path_value = context[f"{normalized_page}_image"]
            root = self.exams_dir
        elif normalized_variant == "annotated":
            context = self.db.results.get_result_context(int(result_id))
            annotated = self.db.reviews.get_annotated_result(int(result_id))
            if (
                context is None
                or int(context["session_id"]) != int(session_id)
                or annotated is None
                or int(annotated["session_id"]) != int(session_id)
            ):
                raise ReviewMediaNotFound("Review media resource was not found.")
            if not annotated.get("annotated_front_path") or not annotated.get("annotated_back_path"):
                from manual_review_service import ManualReviewService

                try:
                    ManualReviewService(self.db, self.annotated_dir).ensure_result_annotation(int(result_id))
                except Exception as exc:
                    raise ReviewMediaUnreadable("Annotation could not be refreshed; retry the image.") from exc
                annotated = self.db.reviews.get_annotated_result(int(result_id))
                if not annotated:
                    raise ReviewMediaNotFound("Review media resource was not found.")
            path_value = annotated[f"annotated_{normalized_page}_path"]
            root = self.annotated_dir
        else:
            raise ReviewMediaNotFound("Review media resource was not found.")

        return resolve_controlled_file(
            path_value,
            root=root,
            data_root=self.data_root,
            allowed_suffixes=IMAGE_SUFFIXES,
        )

    def render_detail_crop(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> bytes:
        context = self.db.reviews.get_review_media_context(
            int(session_id),
            int(result_id),
            int(detail_id),
        )
        if context is None:
            raise ReviewMediaNotFound("Review media resource was not found.")

        region = self._find_region(
            int(session_id),
            str(context.get("question_id") or ""),
        )
        if region is None:
            raise ReviewMediaNotFound("Review media resource was not found.")

        page = "back" if str(region.get("page") or "").lower() == "back" else "front"
        source = resolve_controlled_file(
            context[f"{page}_image"],
            root=self.exams_dir,
            data_root=self.data_root,
            allowed_suffixes=IMAGE_SUFFIXES,
        )
        try:
            source_bytes = source.path.read_bytes()
        except OSError as exc:
            raise ReviewMediaUnreadable(
                "Review media resource could not be decoded."
            ) from exc
        return self._cached_crop(source_bytes, region, page)

    def render_preflight_crop(
        self,
        session_id: int,
        question_id: str,
        *,
        source_region_id: int | None = None,
        front_source: Path,
        back_source: Path | None,
    ) -> bytes:
        """Render one teacher-grading crop before an AI result exists."""

        region = self._find_region(
            int(session_id),
            str(question_id or "").strip(),
            source_region_id=source_region_id,
        )
        if region is None:
            raise ReviewMediaNotFound("Review media resource was not found.")
        page = (
            "back"
            if str(region.get("page") or "").lower() == "back"
            else "front"
        )
        source = back_source if page == "back" else front_source
        if source is None:
            raise ReviewMediaNotFound("Review media resource was not found.")
        try:
            source_bytes = Path(source).read_bytes()
        except OSError as exc:
            raise ReviewMediaUnreadable(
                "Review media resource could not be decoded."
            ) from exc
        return self._cached_crop(source_bytes, region, page)

    def _cached_crop(
        self, source_bytes: bytes, region: dict[str, Any], page: str,
    ) -> bytes:
        if not self.enable_crop_cache:
            return _render_crop_jpeg(source_bytes, region)
        cache_key = _crop_cache_key(source_bytes, region, page)
        cache_path = self.crop_cache_dir / f"{cache_key}.jpg"
        with self._crop_cache_lock:
            self._prepare_crop_cache()
            cached = _read_valid_jpeg(cache_path)
            if cached is not None:
                return cached
            rendered = _render_crop_jpeg(source_bytes, region)
            self._publish_crop_cache(cache_path, rendered)
            return rendered

    def clear_detail_crop_cache(self) -> int:
        """Remove only rebuildable review crop derivatives."""
        with self._crop_cache_lock:
            if not self.crop_cache_dir.exists():
                return 0
            removed = 0
            for path in self.crop_cache_dir.iterdir():
                if path.is_file() and (
                    path.suffix.lower() == ".jpg" or path.name.endswith(".tmp")
                ):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    removed += 1
            return removed

    def _prepare_crop_cache(self) -> None:
        """Repair bounded derivative-cache state once per process start."""
        key = self.crop_cache_dir.resolve()
        with _CACHE_LOCKS_GUARD:
            if key in _MAINTAINED_CACHE_DIRS:
                return
        if self.crop_cache_dir.exists():
            try:
                candidates = tuple(self.crop_cache_dir.iterdir())
            except OSError:
                candidates = ()
            for path in candidates:
                if path.is_file() and path.name.endswith(".tmp"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
            _trim_cache(
                self.crop_cache_dir,
                max_bytes=self.max_crop_cache_bytes,
                keep=None,
            )
        with _CACHE_LOCKS_GUARD:
            _MAINTAINED_CACHE_DIRS.add(key)

    def _publish_crop_cache(self, cache_path: Path, payload: bytes) -> None:
        if len(payload) > self.max_crop_cache_bytes:
            return
        temp_path = cache_path.with_name(
            f".{cache_path.stem}.{uuid4().hex}.tmp"
        )
        try:
            self.crop_cache_dir.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(payload)
            os.replace(temp_path, cache_path)
        except OSError:
            return
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        _trim_cache(
            self.crop_cache_dir,
            max_bytes=self.max_crop_cache_bytes,
            keep=cache_path,
        )

    def _find_region(
        self,
        session_id: int,
        question_id: str,
        *,
        source_region_id: int | None = None,
    ) -> dict[str, Any] | None:
        regions = self._regions_with_template_sizes(session_id)

        if source_region_id is not None:
            try:
                requested_region_id = int(source_region_id)
            except (TypeError, ValueError):
                return None
            if requested_region_id <= 0:
                return None
            for region in regions:
                if int(region.get("id") or 0) != requested_region_id:
                    continue
                if _region_matches_question(region, question_id):
                    return region
                if _is_unique_child_for_parent(
                    regions,
                    region,
                    question_id,
                ):
                    return region
            return None

        requested_identity = question_id_coordinates(question_id)
        if requested_identity is not None:
            for region in regions:
                if question_id_coordinates(
                    _region_question_id(region)
                ) == requested_identity:
                    return region

            requested_parent, requested_part = requested_identity
            if requested_part is not None:
                for region in regions:
                    if question_id_coordinates(
                        _region_question_id(region)
                    ) == (requested_parent, None):
                        return region
            else:
                child_regions = [
                    region
                    for region in regions
                    if (
                        (identity := question_id_coordinates(
                            _region_question_id(region)
                        ))
                        is not None
                        and identity[0] == requested_parent
                        and identity[1] is not None
                    )
                ]
                if len(child_regions) == 1:
                    return child_regions[0]
            return None

        normalized_question_id = str(question_id or "").strip()
        exact_values = {
            normalized_question_id,
            (
                normalized_question_id[1:]
                if normalized_question_id.upper().startswith("Q")
                else f"Q{normalized_question_id}"
            ),
        }
        for region in regions:
            if _region_question_id(region) in exact_values:
                return region
        return None

    def _regions_with_template_sizes(self, session_id: int) -> list[dict[str, Any]]:
        regions = [dict(region) for region in self.db.templates.list_answer_regions(session_id)]
        template = self.db.templates.get_session_template(session_id)
        if not template:
            return regions

        page_sizes: dict[str, tuple[int, int]] = {}
        for page in ("front", "back"):
            try:
                template_file = resolve_controlled_file(
                    template.get(f"{page}_template_path"),
                    root=self.templates_dir,
                    data_root=self.data_root,
                    allowed_suffixes=IMAGE_SUFFIXES,
                )
                with Image.open(template_file.path) as image:
                    page_sizes[page] = (int(image.width), int(image.height))
            except (ControlledFileError, OSError, UnidentifiedImageError):
                continue
        return attach_region_source_image_sizes(regions, page_sizes)


def _region_question_id(region: dict[str, Any]) -> str:
    return str(
        region.get("mapped_question_id")
        or region.get("detected_question_id")
        or ""
    ).strip()


def _region_matches_question(
    region: dict[str, Any],
    question_id: str,
) -> bool:
    requested = question_id_coordinates(question_id)
    mapped = question_id_coordinates(_region_question_id(region))
    if requested is not None and mapped is not None:
        if requested == mapped:
            return True
        requested_parent, requested_part = requested
        mapped_parent, mapped_part = mapped
        return (
            requested_parent == mapped_parent
            and requested_part is not None
            and mapped_part is None
        )

    normalized_question_id = str(question_id or "").strip()
    exact_values = {
        normalized_question_id,
        (
            normalized_question_id[1:]
            if normalized_question_id.upper().startswith("Q")
            else f"Q{normalized_question_id}"
        ),
    }
    return _region_question_id(region) in exact_values


def _is_unique_child_for_parent(
    regions: list[dict[str, Any]],
    selected_region: dict[str, Any],
    question_id: str,
) -> bool:
    requested = question_id_coordinates(question_id)
    selected = question_id_coordinates(
        _region_question_id(selected_region)
    )
    if (
        requested is None
        or selected is None
        or requested[1] is not None
        or selected[0] != requested[0]
        or selected[1] is None
    ):
        return False
    child_regions = [
        region
        for region in regions
        if (
            (identity := question_id_coordinates(
                _region_question_id(region)
            ))
            is not None
            and identity[0] == requested[0]
            and identity[1] is not None
        )
    ]
    return (
        len(child_regions) == 1
        and int(child_regions[0].get("id") or 0)
        == int(selected_region.get("id") or 0)
    )


def _cache_lock(cache_dir: Path) -> threading.RLock:
    key = Path(cache_dir).resolve()
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CACHE_LOCKS[key] = lock
        return lock


def _crop_cache_key(
    source_bytes: bytes,
    region: dict[str, Any],
    page: str,
) -> str:
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    geometry = json.dumps(
        {
            "page": page,
            "region": region,
            "render_version": _CROP_RENDER_VERSION,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(
        source_digest.encode("ascii") + b"\0" + geometry.encode("utf-8")
    ).hexdigest()


def _read_valid_jpeg(path: Path) -> bytes | None:
    try:
        payload = path.read_bytes()
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG":
                return None
            image.verify()
        return payload
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return None


def _render_crop_jpeg(
    source_bytes: bytes,
    region: dict[str, Any],
) -> bytes:
    try:
        with Image.open(BytesIO(source_bytes)) as opened:
            image = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ReviewMediaUnreadable(
            "Review media resource could not be decoded."
        ) from exc

    try:
        x, y, region_right, region_bottom = scaled_region_bbox(
            region,
            image.width,
            image.height,
        )
        pad = max(18, min(image.width, image.height) // 70)
        left = max(0, x - pad)
        top = max(0, y - pad)
        right = min(image.width, region_right + pad)
        bottom = min(image.height, region_bottom + pad)
        crop = image.crop((left, top, right, bottom))
        try:
            draw = ImageDraw.Draw(crop)
            line_width = max(3, crop.width // 220)
            draw.rectangle(
                [
                    x - left,
                    y - top,
                    min(crop.width - 1, region_right - left),
                    min(crop.height - 1, region_bottom - top),
                ],
                outline=(220, 38, 38),
                width=line_width,
            )
            output = BytesIO()
            crop.save(output, format="JPEG", quality=90)
            return output.getvalue()
        finally:
            crop.close()
    finally:
        image.close()


def _trim_cache(cache_dir: Path, *, max_bytes: int, keep: Path | None) -> None:
    entries: list[tuple[int, Path]] = []
    total = 0
    try:
        candidates = tuple(cache_dir.glob("*.jpg"))
    except OSError:
        return
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        total += int(stat.st_size)
        entries.append((int(stat.st_mtime_ns), path))
    if total <= max_bytes:
        return
    for _modified, path in sorted(entries):
        if path == keep:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        total -= int(size)
        if total <= max_bytes:
            break
