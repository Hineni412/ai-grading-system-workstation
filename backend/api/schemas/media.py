from __future__ import annotations

from pydantic import BaseModel


class ReviewMediaLinksResponse(BaseModel):
    originals_available: bool = True
    crop_url: str | None = None
    original_front_url: str | None = None
    original_back_url: str | None = None
    annotated_front_url: str | None = None
    annotated_back_url: str | None = None
