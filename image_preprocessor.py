from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

ENHANCER_VERSION = "v6"


def enhance_image_file(source_path: Path, output_dir: Path, force: bool = False) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _enhanced_name(source_path)
    if not force and output_path.exists() and output_path.stat().st_mtime >= source_path.stat().st_mtime:
        return output_path

    with Image.open(source_path) as image:
        enhanced = enhance_for_ai(image)
        enhanced.save(output_path, format="JPEG", quality=96, subsampling=0, optimize=True)
    return output_path


def enhance_for_ai(image: Image.Image) -> Image.Image:
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    return _whiten_paper_only(rgb)


def _whiten_paper_only(image: Image.Image) -> Image.Image:
    import numpy as np

    arr = np.asarray(image).astype("float32")
    luma = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
    result = arr.copy()

    # Do not touch dark/mid-dark pixels: printed text, handwriting, diagrams,
    # pencil strokes and anti-aliased text edges stay exactly as scanned.
    paper_mask = luma >= 140
    white_mask = luma >= 198
    lift = ((luma - 140) / 58).clip(0, 1) ** 0.60
    add = ((255 - luma) * lift)[:, :, None]
    result[paper_mask] = np.minimum(255, result[paper_mask] + add[paper_mask])
    result[white_mask] = 255

    return Image.fromarray(result.clip(0, 255).astype("uint8"), mode="RGB")


def _enhanced_name(source_path: Path) -> str:
    digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{source_path.stem}_{digest}_{ENHANCER_VERSION}_enhanced.jpg"
