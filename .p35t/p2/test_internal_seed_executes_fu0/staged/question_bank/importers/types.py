from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class ExtractedDocument:
    source_file: str
    page_range: str
    text: str
    needs_ocr: bool = False
    has_images: bool = False
    needs_image_review: bool = False
    image_paths: list[str] = field(default_factory=list)
    rich_paragraphs: list[dict[str, object]] = field(default_factory=list)
