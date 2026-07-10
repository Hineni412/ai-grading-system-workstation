from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScanAnalyzeRequest(BaseModel):
    enhance_images: bool = True
    ocr_workers: int | None = Field(default=None, ge=1, le=32)
    exams_dir: str | None = None
    front_page_parity: Literal["odd", "even"] | None = None
