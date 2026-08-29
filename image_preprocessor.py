from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

ENHANCER_VERSION = "v6"
_ENHANCER_COMMENT = f"ai-grading-enhancer:{ENHANCER_VERSION}".encode("ascii")
_PNG_TEXT_KEY = "ai-grading-enhancer"


def is_standard_pdf_page(path: Path) -> bool:
    page_dir = Path(path).parent
    if page_dir.parent.name != "_pdf_pages":
        return False
    return (page_dir / "source_manifest.json").is_file()


def enhance_image_file(
    source_path: Path,
    output_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Replace the scan with a single working image.

    ``output_dir`` is only used to find leftover ``_enhanced`` copies.
    """
    source_path = Path(source_path)
    if is_standard_pdf_page(source_path):
        _delete_legacy_enhanced_copies(source_path, output_dir)
        return source_path
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    _adopt_legacy_enhanced_copy(source_path)
    if not force and _working_image_is_tagged(source_path):
        _delete_legacy_enhanced_copies(source_path, output_dir)
        return source_path

    tmp_path = _sidecar_temp(source_path, "enhancing")
    try:
        with Image.open(source_path) as image:
            enhanced = enhance_for_ai(image)
            _save_working_image(enhanced, tmp_path, source_path.suffix)
        os.replace(tmp_path, source_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    _delete_legacy_enhanced_copies(source_path, output_dir)
    return source_path


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


def _sidecar_temp(source_path: Path, purpose: str) -> Path:
    return source_path.with_name(f".{source_path.name}.{uuid4().hex}.{purpose}")


def _working_image_is_tagged(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            comment = image.info.get("comment")
            if isinstance(comment, bytes):
                if comment.rstrip(b"\x00") == _ENHANCER_COMMENT:
                    return True
            elif isinstance(comment, str) and comment.rstrip("\x00") == (
                _ENHANCER_COMMENT.decode("ascii")
            ):
                return True
            png_tag = image.info.get(_PNG_TEXT_KEY)
            if png_tag is None:
                png_tag = (getattr(image, "text", {}) or {}).get(_PNG_TEXT_KEY)
            if str(png_tag or "") == ENHANCER_VERSION:
                return True
    except Exception:
        return False
    return False


def _save_working_image(image: Image.Image, dest: Path, suffix: str) -> None:
    if suffix.lower() == ".png":
        pnginfo = PngInfo()
        pnginfo.add_text(_PNG_TEXT_KEY, ENHANCER_VERSION)
        image.save(dest, format="PNG", optimize=True, pnginfo=pnginfo)
        return
    image.save(
        dest,
        format="JPEG",
        quality=96,
        subsampling=0,
        optimize=True,
        comment=_ENHANCER_COMMENT,
    )


def _legacy_enhanced_copies(
    source_path: Path,
    output_dir: Path | None = None,
) -> list[Path]:
    try:
        source_resolved = source_path.resolve()
    except OSError:
        return []
    name = _enhanced_name(source_path)
    candidates = [
        source_path.parent / "_enhanced" / name,
        source_path.parent.parent / "_enhanced" / name,
    ]
    if output_dir is not None:
        candidates.append(Path(output_dir) / name)
    found: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == source_resolved:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        found.append(candidate)
    return found


def _adopt_legacy_enhanced_copy(source_path: Path) -> bool:
    if _working_image_is_tagged(source_path):
        return False
    copies = _legacy_enhanced_copies(source_path)
    if not copies:
        return False
    newest = max(copies, key=lambda item: item.stat().st_mtime)
    tmp_path = _sidecar_temp(source_path, "adopting")
    try:
        with Image.open(newest) as image:
            rgb = ImageOps.exif_transpose(image).convert("RGB")
            _save_working_image(rgb, tmp_path, source_path.suffix)
        os.replace(tmp_path, source_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        return False
    _delete_legacy_enhanced_copies(source_path)
    return True


def _delete_legacy_enhanced_copies(
    source_path: Path,
    output_dir: Path | None = None,
) -> None:
    parents: set[Path] = set()
    for candidate in _legacy_enhanced_copies(source_path, output_dir):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            continue
        parents.add(candidate.parent)
    for parent in parents:
        if parent.name != "_enhanced":
            continue
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            continue
