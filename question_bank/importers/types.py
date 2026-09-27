from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from question_bank.document_pipeline.contracts import DocumentSnapshot


@dataclass(frozen=True)
class ExtractedDocument:
    source_file: str
    page_range: str
    text: str
    needs_ocr: bool = False
    ocr_applied: bool = False
    has_images: bool = False
    needs_image_review: bool = False
    image_paths: list[str] = field(default_factory=list)
    rich_paragraphs: list[dict[str, object]] = field(default_factory=list)
    document_snapshot: "DocumentSnapshot | None" = None
    # 提取阶段推断出的题型覆盖（如彩色答案层），题号 → 题型。
    type_overrides: dict[str, str] = field(default_factory=dict)
    # 本次提取的定位信息；不持久化正文，供考试入口复用同一次本地识别。
    pdf_layout: dict = field(default_factory=dict)
