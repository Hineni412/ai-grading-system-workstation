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

from backend.answer_regions.answer_region_geometry import attach_region_source_image_sizes, scaled_region_bbox
from backend.media.annotation_renderer import _load_font
from backend.file_access import (
    ControlledFileError,
    ResolvedFile,
    resolve_controlled_file,
)
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.question_id_contract import question_id_coordinates
from backend.files.session_originals import require_original_pages

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
        crop_cache_dir: Path | None = None,
        max_crop_cache_bytes: int = _DEFAULT_CROP_CACHE_BYTES,
        enable_crop_cache: bool = True,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.data_root = Path(data_root)
        self.exams_dir = Path(exams_dir)
        self.templates_dir = Path(templates_dir)
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
    ) -> ResolvedFile:
        require_original_pages(self.data_root, session_id)
        normalized_page = str(page or "").strip().lower()
        if normalized_page not in {"front", "back"}:
            raise ReviewMediaNotFound("Review media resource was not found.")

        context = self.db.results.get_result_context(int(result_id))
        if context is None or int(context["session_id"]) != int(session_id):
            raise ReviewMediaNotFound("Review media resource was not found.")

        return resolve_controlled_file(
            context[f"{normalized_page}_image"],
            root=self.exams_dir,
            data_root=self.data_root,
            allowed_suffixes=IMAGE_SUFFIXES,
        )

    def render_detail_crop(
        self,
        session_id: int,
        result_id: int,
        detail_id: int,
    ) -> bytes:
        require_original_pages(self.data_root, session_id)
        context = self.db.reviews.get_review_media_context(
            int(session_id),
            int(result_id),
            int(detail_id),
        )
        if context is None:
            raise ReviewMediaNotFound("Review media resource was not found.")

        regions = self._find_regions(
            int(session_id),
            str(context.get("question_id") or ""),
        )
        if not regions:
            raise ReviewMediaNotFound("Review media resource was not found.")

        crops = []
        sources: dict[str, bytes] = {}
        for region in regions:
            page = "back" if str(region.get("page") or "").lower() == "back" else "front"
            if page not in sources:
                source = resolve_controlled_file(
                    context[f"{page}_image"],
                    root=self.exams_dir,
                    data_root=self.data_root,
                    allowed_suffixes=IMAGE_SUFFIXES,
                )
                try:
                    sources[page] = source.path.read_bytes()
                except OSError as exc:
                    raise ReviewMediaUnreadable(
                        "Review media resource could not be decoded."
                    ) from exc
            crops.append((page, self._cached_crop(sources[page], region, page)))
        return _combine_region_crops(crops)

    def render_preflight_crop(
        self,
        session_id: int,
        question_id: str,
        *,
        source_region_id: int | None = None,
        front_source: Path,
        back_source: Path | None,
    ) -> bytes:
        """Render all areas of one teacher-grading question before AI results."""
        require_original_pages(self.data_root, session_id)

        regions = self._find_regions(
            int(session_id),
            str(question_id or "").strip(),
            source_region_id=source_region_id,
        )
        if not regions:
            raise ReviewMediaNotFound("Review media resource was not found.")
        crops = []
        sources: dict[str, bytes] = {}
        for region in regions:
            page = "back" if str(region.get("page") or "").lower() == "back" else "front"
            if page not in sources:
                source = back_source if page == "back" else front_source
                if source is None:
                    raise ReviewMediaNotFound("Review media resource was not found.")
                try:
                    sources[page] = Path(source).read_bytes()
                except OSError as exc:
                    raise ReviewMediaUnreadable(
                        "Review media resource could not be decoded."
                    ) from exc
            crops.append((page, self._cached_crop(sources[page], region, page)))
        return _combine_region_crops(crops)

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

    def _find_regions(
        self,
        session_id: int,
        question_id: str,
        *,
        source_region_id: int | None = None,
    ) -> list[dict[str, Any]]:
        regions = sorted(
            self._regions_with_template_sizes(session_id),
            key=lambda region: (
                str(region.get("page") or "front").lower() == "back",
                int(region.get("region_order") or 0),
                float(region.get("y") or 0),
                float(region.get("x") or 0),
            ),
        )

        if source_region_id is not None:
            try:
                requested_region_id = int(source_region_id)
            except (TypeError, ValueError):
                return []
            if requested_region_id <= 0:
                return []
            selected = next(
                (region for region in regions if int(region.get("id") or 0) == requested_region_id),
                None,
            )
            if selected is None:
                return []
            if not (
                _region_matches_question(selected, question_id)
                or _is_unique_child_for_parent(regions, selected, question_id)
            ):
                return []
            selected_identity = question_id_coordinates(_region_question_id(selected))
            if selected_identity is not None:
                return [
                    region for region in regions
                    if question_id_coordinates(_region_question_id(region)) == selected_identity
                ]
            return [
                region for region in regions
                if _region_question_id(region) == _region_question_id(selected)
            ]

        requested_identity = question_id_coordinates(question_id)
        if requested_identity is not None:
            exact = [
                region for region in regions
                if question_id_coordinates(_region_question_id(region)) == requested_identity
            ]
            if exact:
                return exact

            requested_parent, requested_part = requested_identity
            if requested_part is not None:
                return [
                    region for region in regions
                    if question_id_coordinates(_region_question_id(region)) == (requested_parent, None)
                ]
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
                child_identities = {
                    question_id_coordinates(_region_question_id(region))
                    for region in child_regions
                }
                if len(child_identities) == 1:
                    return child_regions
            return []

        normalized_question_id = str(question_id or "").strip()
        exact_values = {
            normalized_question_id,
            (
                normalized_question_id[1:]
                if normalized_question_id.upper().startswith("Q")
                else f"Q{normalized_question_id}"
            ),
        }
        return [region for region in regions if _region_question_id(region) in exact_values]

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
        {question_id_coordinates(_region_question_id(region)) for region in child_regions} == {selected}
        and any(
            int(region.get("id") or 0) == int(selected_region.get("id") or 0)
            for region in child_regions
        )
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


def _combine_region_crops(crops: list[tuple[str, bytes]]) -> bytes:
    if len(crops) == 1:
        return crops[0][1]
    images: list[Image.Image] = []
    try:
        for _page, payload in crops:
            with Image.open(BytesIO(payload)) as source:
                images.append(source.convert("RGB"))
        width = max(image.width for image in images)
        font_size = max(18, min(32, width // 45))
        label_height = font_size + 18
        height = sum(image.height + label_height for image in images)
        with Image.new("RGB", (width, height), "white") as combined:
            draw = ImageDraw.Draw(combined)
            font = _load_font(font_size)
            top = 0
            for index, ((page, _payload), image) in enumerate(zip(crops, images), start=1):
                draw.rectangle((0, top, width, top + label_height), fill=(241, 245, 249))
                draw.text(
                    (10, top + 7),
                    f"作答区域 {index} · {'反面' if page == 'back' else '正面'}",
                    font=font,
                    fill=(51, 65, 85),
                )
                combined.paste(image, ((width - image.width) // 2, top + label_height))
                top += image.height + label_height
            output = BytesIO()
            combined.save(output, format="JPEG", quality=90)
            return output.getvalue()
    finally:
        for image in images:
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
