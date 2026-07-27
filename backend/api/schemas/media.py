from __future__ import annotations

from pydantic import BaseModel


class ReviewMediaLinksResponse(BaseModel):
    crop_url: str
    original_front_url: str
    original_back_url: str | None = None
    annotated_front_url: str | None = None
    annotated_back_url: str | None = None
