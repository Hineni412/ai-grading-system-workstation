"""参考图（标准答案原图）预处理：缩小体积、统一为 JPEG，供整卷批改复用。

原则：默认不放大；最大 960×2048；JPEG 质量 82。原始图不改动，只产出压缩副本，
降低每次大图请求的上传负载与重复编码开销。
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image


@dataclass(frozen=True, slots=True)
class PreparedImageBytes:
    blob: bytes
    width: int
    height: int
    original_width: int
    original_height: int


def prepare_reference_image(
    blob: bytes,
    *,
    max_width: int = 960,
    max_height: int = 2048,
    quality: int = 82,
) -> PreparedImageBytes:
    with Image.open(BytesIO(blob)) as source:
        original_width, original_height = source.size
        rgba = source.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
        scale = min(
            1.0,
            max_width / original_width if original_width else 1.0,
            max_height / original_height if original_height else 1.0,
        )
        target = (
            max(1, round(original_width * scale)),
            max(1, round(original_height * scale)),
        )
        if target != rgb.size:
            rgb = rgb.resize(target, Image.Resampling.LANCZOS)
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=quality, optimize=True)
        return PreparedImageBytes(
            output.getvalue(),
            target[0],
            target[1],
            original_width,
            original_height,
        )
