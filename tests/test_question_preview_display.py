from pathlib import Path

from PIL import Image

from question_bank.services.question_preview_display import (
    image_display_width,
    resolve_preview_density,
)


def _write_image(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def test_resolve_preview_density_defaults_to_compact() -> None:
    density = resolve_preview_density("")

    assert density.name == "紧凑"
    assert density.body_font_rem == 0.96
    assert density.line_height == 1.62
    assert density.answer_font_rem == 0.92


def test_resolve_preview_density_can_use_comfortable_values() -> None:
    density = resolve_preview_density("舒适")

    assert density.name == "舒适"
    assert density.body_font_rem == 1.05
    assert density.line_height == 1.8
    assert density.answer_font_rem == 1.0


def test_image_display_width_uses_single_and_wide_image_limits(tmp_path: Path) -> None:
    normal = _write_image(tmp_path / "normal.png", (400, 300))
    wide = _write_image(tmp_path / "wide.png", (1200, 320))

    assert image_display_width(normal, image_count=1, scale_percent=100) == 320
    assert image_display_width(normal, image_count=1, scale_percent=90) == 288
    assert image_display_width(wide, image_count=1, scale_percent=100) == 520


def test_image_display_width_uses_multi_image_and_missing_file_fallbacks(tmp_path: Path) -> None:
    option = _write_image(tmp_path / "option.png", (220, 160))

    assert image_display_width(option, image_count=4, scale_percent=100) == 150
    assert image_display_width(option, image_count=4, scale_percent=70) == 105
    assert image_display_width(tmp_path / "missing.png", image_count=1, scale_percent=100) == 320
    assert image_display_width(tmp_path / "missing.png", image_count=3, scale_percent=100) == 150
