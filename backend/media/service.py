from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, UnidentifiedImageError

from answer_region_geometry import attach_region_source_image_sizes, scaled_region_bbox
from backend.file_access import (
    ControlledFileError,
    ResolvedFile,
    resolve_controlled_file,
)
from db_manager import DBManager


IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})


class ReviewMediaNotFound(LookupError):
    """Raised when IDs do not identify one owned review media resource."""


class ReviewMediaUnreadable(ValueError):
    """Raised when an allowlisted image cannot be decoded."""


class ReviewMediaService:
    def __init__(
        self,
        db: DBManager,
        *,
        data_root: Path,
        exams_dir: Path,
        templates_dir: Path,
        annotated_dir: Path,
    ) -> None:
        self.db = db
        self.data_root = Path(data_root)
        self.exams_dir = Path(exams_dir)
        self.templates_dir = Path(templates_dir)
        self.annotated_dir = Path(annotated_dir)

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
            context = self.db.get_result_context(int(result_id))
            if context is None or int(context["session_id"]) != int(session_id):
                raise ReviewMediaNotFound("Review media resource was not found.")
            path_value = context[f"{normalized_page}_image"]
            root = self.exams_dir
        elif normalized_variant == "annotated":
            context = self.db.get_result_context(int(result_id))
            annotated = self.db.get_annotated_result(int(result_id))
            if (
                context is None
                or int(context["session_id"]) != int(session_id)
                or annotated is None
                or int(annotated["session_id"]) != int(session_id)
            ):
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
        context = self.db.get_review_media_context(
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
            with Image.open(source.path) as opened:
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

    def _find_region(
        self,
        session_id: int,
        question_id: str,
    ) -> dict[str, Any] | None:
        regions = self._regions_with_template_sizes(session_id)
        exact_values = {question_id, f"Q{question_id}"}
        for region in regions:
            if _region_question_id(region) in exact_values:
                return region

        match = re.match(r"^(?:Q)?(\d+)", question_id)
        if match:
            base_number = match.group(1)
            fallback_values = {base_number, f"Q{base_number}"}
            for region in regions:
                if _region_question_id(region) in fallback_values:
                    return region
        return None

    def _regions_with_template_sizes(self, session_id: int) -> list[dict[str, Any]]:
        regions = [dict(region) for region in self.db.list_answer_regions(session_id)]
        template = self.db.get_session_template(session_id)
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
    )
