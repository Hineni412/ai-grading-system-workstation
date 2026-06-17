from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from grading_service import _enhance_or_original
from scanner import Scanner, _student_name_crop_box, render_pdf_to_standard_pages


fitz = pytest.importorskip("fitz")


class DummyLLMClient:
    pass


def _make_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = Image.new("RGB", (120, 90), (218, 218, 218))
    second = Image.new("RGB", (120, 90), (180, 180, 180))
    first.save(path, "PDF", save_all=True, append_images=[second])
    first.close()
    second.close()
    return path


def _mean_luma(path: Path) -> float:
    with Image.open(path) as image:
        stat = ImageStat.Stat(image.convert("L"))
        return float(stat.mean[0])


def test_render_pdf_to_enhanced_standard_pages_deletes_source_without_enhanced_copy(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "uploads" / "class.pdf")
    render_root = pdf_path.parent / "_pdf_pages"

    pages = render_pdf_to_standard_pages(
        pdf_path,
        render_root,
        enhance_images=True,
        delete_source_pdf=True,
    )

    assert not pdf_path.exists()
    assert len(pages) == 2
    assert all(page.image_path.exists() for page in pages)
    assert all(page.enhanced_image_path == page.image_path for page in pages)
    assert not (pdf_path.parent / "_enhanced").exists()

    manifest = json.loads((render_root / "class" / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_pdf_name"] == "class.pdf"
    assert manifest["enhance_images"] is True
    assert manifest["page_count"] == 2
    assert manifest["source_pdf_sha1"]


def test_render_pdf_without_enhancement_keeps_plain_standard_pages(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "uploads" / "plain.pdf")
    render_root = pdf_path.parent / "_pdf_pages"

    pages = render_pdf_to_standard_pages(
        pdf_path,
        render_root,
        enhance_images=False,
        delete_source_pdf=True,
    )

    assert not pdf_path.exists()
    assert len(pages) == 2
    assert all(page.enhanced_image_path is None for page in pages)
    manifest = json.loads((render_root / "plain" / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["enhance_images"] is False


def test_scanner_can_find_standard_pages_after_source_pdf_removed(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "uploads" / "class.pdf")
    scanner = Scanner(tmp_path / "uploads", DummyLLMClient(), enhance_images=True)
    rendered = render_pdf_to_standard_pages(pdf_path, scanner.render_dir, enhance_images=True, delete_source_pdf=True)

    page_sets = scanner._collect_rendered_pdf_page_sets()

    assert len(page_sets) == 1
    source_name, pages = page_sets[0]
    assert source_name == "class.pdf"
    assert [page.image_path for page in pages] == [page.image_path for page in rendered]
    assert all(page.enhanced_image_path == page.image_path for page in pages)


def test_scanner_keeps_external_pdf_when_rendering_standard_pages(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "external" / "class.pdf")
    scanner = Scanner(tmp_path / "external", DummyLLMClient(), enhance_images=True)

    pages = scanner._render_pdf_pages(pdf_path)

    assert pdf_path.exists()
    assert len(pages) == 2
    assert all(page.image_path.exists() for page in pages)


def test_student_name_crop_scales_template_region_to_rendered_page_size() -> None:
    region = {
        "x": 694,
        "y": 210,
        "w": 448,
        "h": 120,
        "source_image_width": 2831,
        "source_image_height": 1960,
    }

    assert _student_name_crop_box(region, width=1768, height=1224) == (433, 131, 713, 206)


def test_plain_standard_page_does_not_create_enhanced_copy_later(tmp_path: Path) -> None:
    pdf_path = _make_pdf(tmp_path / "uploads" / "plain.pdf")
    page = render_pdf_to_standard_pages(
        pdf_path,
        pdf_path.parent / "_pdf_pages",
        enhance_images=False,
        delete_source_pdf=True,
    )[0].image_path
    output_dir = pdf_path.parent / "_enhanced"

    selected = _enhance_or_original(page, output_dir)

    assert selected == page
    assert not output_dir.exists()


def test_enhanced_standard_page_is_brighter_than_plain_render(tmp_path: Path) -> None:
    enhanced_pdf = _make_pdf(tmp_path / "enhanced" / "class.pdf")
    plain_pdf = _make_pdf(tmp_path / "plain" / "class.pdf")

    enhanced_page = render_pdf_to_standard_pages(
        enhanced_pdf,
        enhanced_pdf.parent / "_pdf_pages",
        enhance_images=True,
        delete_source_pdf=True,
    )[0].image_path
    plain_page = render_pdf_to_standard_pages(
        plain_pdf,
        plain_pdf.parent / "_pdf_pages",
        enhance_images=False,
        delete_source_pdf=True,
    )[0].image_path

    assert _mean_luma(enhanced_page) > _mean_luma(plain_page)
