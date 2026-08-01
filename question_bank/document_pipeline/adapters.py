from __future__ import annotations

import io
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol

from PIL import Image, ImageOps

from .contracts import ImageTransform


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    polygon: tuple[tuple[float, float], ...]
    confidence: float
    engine_version: str

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if not text or len(text) > 20_000:
            raise ValueError("OCR line text is invalid")
        points = tuple((float(x), float(y)) for x, y in self.polygon)
        if len(points) < 4:
            raise ValueError("OCR line polygon must have at least four points")
        if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in points):
            raise ValueError("OCR line polygon must be normalized")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("OCR confidence must be between 0 and 1")
        version = str(self.engine_version or "").strip()
        if not version or len(version) > 120:
            raise ValueError("OCR engine version is invalid")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "polygon", points)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "engine_version", version)


@dataclass(frozen=True, slots=True)
class PreparedImage:
    png_bytes: bytes
    width: int
    height: int
    transform: ImageTransform

    def __post_init__(self) -> None:
        if not self.png_bytes:
            raise ValueError("prepared image is empty")
        if int(self.width) < 1 or int(self.height) < 1:
            raise ValueError("prepared image dimensions must be positive")
        object.__setattr__(self, "png_bytes", bytes(self.png_bytes))
        object.__setattr__(self, "width", int(self.width))
        object.__setattr__(self, "height", int(self.height))


class LocalOcrAdapter(Protocol):
    def recognize(self, png_bytes: bytes, *, page_number: int) -> tuple[OcrLine, ...]: ...


class ImagePreprocessor(Protocol):
    def prepare(self, image_bytes: bytes, *, media_type: str) -> PreparedImage: ...


class RapidOcrAdapter:
    """Local-only OCR adapter. Import and model initialization are lazy."""

    def __init__(self, engine: object | None = None) -> None:
        self._engine = engine

    def recognize(self, png_bytes: bytes, *, page_number: int) -> tuple[OcrLine, ...]:
        del page_number
        import numpy as np

        engine = self._engine
        if engine is None:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
            self._engine = engine
        with Image.open(io.BytesIO(png_bytes)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            image = np.asarray(rgb)[:, :, ::-1]
        raw = engine(image)  # type: ignore[operator]
        result = raw[0] if isinstance(raw, tuple) else raw
        if not isinstance(result, list):
            return ()
        try:
            version = f"rapidocr-onnxruntime/{metadata.version('rapidocr-onnxruntime')}"
        except metadata.PackageNotFoundError:
            version = "rapidocr-onnxruntime/unknown"
        lines: list[OcrLine] = []
        for item in result:
            if not isinstance(item, (tuple, list)) or len(item) < 3:
                continue
            text = str(item[1] or "").strip()
            try:
                confidence = float(item[2])
                raw_points = item[0]
                points = tuple(
                    (
                        max(0.0, min(1.0, float(point[0]) / width)),
                        max(0.0, min(1.0, float(point[1]) / height)),
                    )
                    for point in raw_points
                )
                if text:
                    lines.append(
                        OcrLine(
                            text=text,
                            polygon=points,
                            confidence=confidence,
                            engine_version=version,
                        )
                    )
            except (IndexError, TypeError, ValueError):
                continue
        return tuple(lines)


class SafeImagePreprocessor:
    """Applies EXIF orientation and exposes a perspective-correction seam.

    The first implementation deliberately does not guess a perspective warp.
    It records that a teacher must review the normalized page instead.
    """

    _ROTATIONS = {3: 180, 6: 90, 8: 270}

    def prepare(self, image_bytes: bytes, *, media_type: str) -> PreparedImage:
        if media_type not in {"image/jpeg", "image/png"}:
            raise ValueError("image media type is not supported")
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                orientation = int(source.getexif().get(274) or 1)
                rotation = self._ROTATIONS.get(orientation, 0)
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                width, height = normalized.size
                output = io.BytesIO()
                normalized.save(output, format="PNG")
        except (OSError, ValueError) as exc:
            raise ValueError("image source could not be decoded") from exc
        return PreparedImage(
            png_bytes=output.getvalue(),
            width=width,
            height=height,
            transform=ImageTransform(
                rotation_degrees=rotation,
                perspective_corners=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
                method="exif-orientation+manual-perspective-review",
                requires_review=True,
            ),
        )


__all__ = [
    "ImagePreprocessor",
    "LocalOcrAdapter",
    "OcrLine",
    "PreparedImage",
    "RapidOcrAdapter",
    "SafeImagePreprocessor",
]
