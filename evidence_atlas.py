from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from answer_region_geometry import scaled_region_bbox
from backend.domain_models import ExamPaperGroup
from scanner import STUDENT_NAME_REGION_ALIASES


@dataclass(frozen=True)
class EvidenceCrop:
    question_id: str
    page: str
    bbox: dict[str, int]
    image_path: Path
    source_image_path: Path


@dataclass(frozen=True)
class EvidenceAtlasResult:
    atlas_path: Path
    manifest_path: Path
    crops: list[EvidenceCrop]
    manifest: dict[str, Any]


class EvidenceAtlasBuilder:
    def __init__(
        self,
        output_root: Path,
        *,
        crop_padding: int = 8,
        max_atlas_width: int = 1400,
        jpeg_quality: int = 88,
    ) -> None:
        self.output_root = Path(output_root)
        self.crop_padding = max(0, int(crop_padding))
        self.max_atlas_width = max(320, int(max_atlas_width))
        self.jpeg_quality = max(40, min(95, int(jpeg_quality)))

    def build(
        self,
        *,
        session_id: int | str,
        paper_group: ExamPaperGroup,
        answer_regions: list[dict[str, Any]],
    ) -> EvidenceAtlasResult:
        regions = _usable_answer_regions(answer_regions)
        if not regions:
            raise ValueError("No confirmed answer regions are available for evidence atlas grading.")

        student_key = _safe_path_part(str(paper_group.student_id or paper_group.student_name or "unknown"))
        output_dir = self.output_root / f"session_{session_id}" / student_key
        crops_dir = output_dir / "crops"
        crops_dir.mkdir(parents=True, exist_ok=True)

        source_images = {
            "front": paper_group.enhanced_front_image or paper_group.front_image,
            "back": paper_group.enhanced_back_image or paper_group.back_image,
        }

        crops: list[EvidenceCrop] = []
        crop_images: list[Image.Image] = []
        try:
            for index, region in enumerate(regions, start=1):
                page = _region_page(region)
                source_path = Path(source_images[page])
                crop_image, bbox = self._crop_region(source_path, region)
                question_id = _region_question_id(region)
                crop_path = crops_dir / f"{index:03d}_{_safe_path_part(question_id)}.jpg"
                crop_image.save(crop_path, format="JPEG", quality=self.jpeg_quality)
                crop_images.append(crop_image)
                crops.append(
                    EvidenceCrop(
                        question_id=question_id,
                        page=page,
                        bbox=bbox,
                        image_path=crop_path,
                        source_image_path=source_path,
                    )
                )

            if not crops:
                raise ValueError("No valid answer region crop could be created.")

            atlas_path = output_dir / "atlas_001.jpg"
            self._save_atlas(crops, crop_images, atlas_path)
            manifest = self._build_manifest(session_id, paper_group, atlas_path, crops)
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return EvidenceAtlasResult(
                atlas_path=atlas_path,
                manifest_path=manifest_path,
                crops=crops,
                manifest=manifest,
            )
        finally:
            for image in crop_images:
                image.close()

    def _crop_region(self, source_path: Path, region: dict[str, Any]) -> tuple[Image.Image, dict[str, int]]:
        with Image.open(source_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            left, top, right, bottom = scaled_region_bbox(
                region,
                width,
                height,
                padding=self.crop_padding,
            )
            if right <= left or bottom <= top:
                raise ValueError(f"Invalid answer region bbox: {region}")
            crop = rgb.crop((left, top, right, bottom))
        return crop, {"x": left, "y": top, "w": right - left, "h": bottom - top}

    def _save_atlas(self, crops: list[EvidenceCrop], crop_images: list[Image.Image], atlas_path: Path) -> None:
        label_height = 34
        gap = 12
        margin = 18
        scaled: list[tuple[EvidenceCrop, Image.Image]] = []
        for crop, image in zip(crops, crop_images):
            max_crop_width = self.max_atlas_width - margin * 2
            if image.width > max_crop_width:
                ratio = max_crop_width / float(image.width)
                new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
                scaled.append((crop, image.resize(new_size, Image.Resampling.LANCZOS)))
            else:
                scaled.append((crop, image.copy()))

        atlas_width = max(image.width for _, image in scaled) + margin * 2
        atlas_height = margin + sum(label_height + image.height + gap for _, image in scaled) + margin
        atlas = Image.new("RGB", (atlas_width, atlas_height), "white")
        draw = ImageDraw.Draw(atlas)
        y = margin
        try:
            for idx, (crop, image) in enumerate(scaled, start=1):
                label = f"{idx:02d}. {crop.question_id} ({crop.page})"
                draw.rectangle((margin, y, atlas_width - margin, y + label_height - 4), fill=(242, 244, 247))
                draw.text((margin + 10, y + 9), label, fill=(20, 30, 40))
                y += label_height
                atlas.paste(image, (margin, y))
                y += image.height + gap
            atlas.save(atlas_path, format="JPEG", quality=self.jpeg_quality)
        finally:
            atlas.close()
            for _, image in scaled:
                image.close()

    def _build_manifest(
        self,
        session_id: int | str,
        paper_group: ExamPaperGroup,
        atlas_path: Path,
        crops: list[EvidenceCrop],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "session_id": session_id,
            "student_id": paper_group.student_id,
            "student_name": paper_group.student_name,
            "atlas_path": str(atlas_path),
            "items": [
                {
                    "question_id": crop.question_id,
                    "page": crop.page,
                    "bbox": crop.bbox,
                    "crop_path": str(crop.image_path),
                    "source_image_path": str(crop.source_image_path),
                }
                for crop in crops
            ],
        }


def _usable_answer_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for region in regions:
        qid = _region_question_id(region)
        if not qid or qid in STUDENT_NAME_REGION_ALIASES:
            continue
        if region.get("is_confirmed") is False:
            continue
        if _region_page(region) not in {"front", "back"}:
            continue
        result.append(region)
    return sorted(result, key=lambda item: (_page_sort_key(_region_page(item)), _region_order(item), _region_question_id(item)))


def _region_question_id(region: dict[str, Any]) -> str:
    return str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()


def _region_page(region: dict[str, Any]) -> str:
    page = str(region.get("page") or "front").strip().lower()
    return "back" if page == "back" else "front"


def _region_order(region: dict[str, Any]) -> int:
    return _int_region_value(region, "region_order", default=0)


def _int_region_value(region: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(round(float(region.get(key, default) or default)))
    except (TypeError, ValueError):
        return default


def _page_sort_key(page: str) -> int:
    return 0 if page == "front" else 1


def _safe_path_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return text.strip("._") or "unknown"
