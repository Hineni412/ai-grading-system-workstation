from __future__ import annotations

import json
import zipfile
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.domain.errors import TeachingPrepConflictError

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _lesson_tree


def _pdf(path: Path, pages: list[str]) -> Path:
    document = fitz.open()
    try:
        for text in pages:
            page = document.new_page(width=640, height=900)
            if text:
                page.insert_text((48, 64), text)
        document.save(path)
    finally:
        document.close()
    return path


def _pptx(path: Path) -> Path:
    presentation = """
    <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:sldSz cx="12192000" cy="6858000"/>
    </p:presentation>
    """.strip()
    slide_template = """
    <p:sld
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld>
        <p:spTree>
          <p:sp>
            <p:spPr>
              <a:xfrm>
                <a:off x="1200000" y="900000"/>
                <a:ext cx="6000000" cy="1600000"/>
              </a:xfrm>
            </p:spPr>
            <p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody>
          </p:sp>
        </p:spTree>
      </p:cSld>
    </p:sld>
    """.strip()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr(
            "ppt/slides/slide1.xml",
            slide_template.format(title="Synthetic introduction"),
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            slide_template.format(title="Synthetic example x = 2"),
        )
    return path


def _register(
    service: TeachingPrepService,
    path: Path,
    *,
    token: str,
    name: str,
):
    version, _created = service.register_material_file(
        request_token=token,
        path=path,
        display_name=name,
    )
    return version


def test_pdf_pages_render_extract_text_and_keep_formula_review_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pdf(
            tmp_path / "synthetic-textbook.pdf",
            ["Synthetic formula x = 2", ""],
        ),
        token="material-a03-pdf",
        name="合成教材",
    )

    units = service.parse_material_version(version.id)

    assert len(units) == 2
    assert units[0].text_status == "embedded"
    assert units[0].formula_review_required is True
    assert units[1].text_status == "empty"
    assert all(unit.preview_url.startswith("/api/teaching-prep/") for unit in units)
    preview = service.material_preview_path(units[0].id)
    assert preview.read_bytes().startswith(b"\x89PNG")


def test_blank_page_accepts_manual_label_and_preview_cache_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pdf(tmp_path / "synthetic-scan.pdf", [""]),
        token="material-a03-scan",
        name="合成扫描页",
    )
    unit = service.parse_material_version(version.id)[0]

    labelled = service.update_material_unit_label(
        unit.id,
        expected_revision=unit.revision,
        title="人工页码标签",
        manual_text="人工核对：含数学公式",
        formula_review_required=True,
    )
    preview = service.material_preview_path(unit.id)
    preview.unlink()
    rebuilt = service.material_preview_path(unit.id)

    assert labelled.text_status == "manual"
    assert labelled.formula_review_required is True
    assert rebuilt.is_file()
    assert service.list_material_units(version.id)[0].title == "人工页码标签"


def test_pptx_slides_expose_titles_objects_and_structural_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-reference.pptx"),
        token="material-a03-pptx",
        name="合成参考课件",
    )

    units = service.parse_material_version(version.id)

    assert [unit.title for unit in units] == [
        "Synthetic introduction",
        "Synthetic example x = 2",
    ]
    assert units[0].object_summary["preview_kind"] == "structural"
    assert units[0].object_summary["object_count"] == 1
    assert units[1].formula_review_required is True


def test_lesson_can_confirm_multiple_non_contiguous_version_frozen_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    curriculum_id, _chapter_id, _section_id, lesson_ids = _lesson_tree(service)
    assert curriculum_id
    first_version = _register(
        service,
        _pdf(
            tmp_path / "synthetic-exercises.pdf",
            ["Page one", "Page two", "Page three"],
        ),
        token="material-a03-links",
        name="合成教辅",
    )
    service.parse_material_version(first_version.id)

    first_link, _created = service.create_material_link(
        request_token="link-a03-page-one",
        lesson_node_id=lesson_ids[0],
        material_version_id=first_version.id,
        start_unit=1,
        end_unit=1,
        crop=None,
        purpose="exercise",
        teacher_note="导入练习",
        confirmation_status="confirmed",
    )
    third_link, _created = service.create_material_link(
        request_token="link-a03-page-three",
        lesson_node_id=lesson_ids[0],
        material_version_id=first_version.id,
        start_unit=3,
        end_unit=3,
        crop={"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.8},
        purpose="exercise",
        teacher_note="课堂备用",
        confirmation_status="confirmed",
    )

    links = service.list_material_links(lesson_ids[0])
    assert [(link.start_unit, link.end_unit) for link in links] == [(1, 1), (3, 3)]
    assert all(link.material_version_id == first_version.id for link in links)
    revised = service.update_material_link(
        first_link.id,
        expected_revision=first_link.revision,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="exercise",
        teacher_note="修正为连续两页",
        confirmation_status="confirmed",
        is_active=True,
    )
    assert revised.revision == first_link.revision + 1
    with pytest.raises(TeachingPrepConflictError):
        service.update_material_link(
            first_link.id,
            expected_revision=first_link.revision,
            start_unit=1,
            end_unit=2,
            crop=None,
            purpose="exercise",
            teacher_note=None,
            confirmation_status="confirmed",
            is_active=True,
        )
    deactivated = service.update_material_link(
        third_link.id,
        expected_revision=third_link.revision,
        start_unit=3,
        end_unit=3,
        crop=third_link.crop,
        purpose=third_link.purpose,
        teacher_note=third_link.teacher_note,
        confirmation_status=third_link.confirmation_status,
        is_active=False,
    )
    assert deactivated.is_active is False


def test_material_unit_api_uses_controlled_preview_url_not_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pdf(tmp_path / "private-synthetic.pdf", ["Safe preview"]),
        token="material-a03-api",
        name="合成资料",
    )
    service.parse_material_version(version.id)
    api = FastAPI()
    api.state.workspace_services = {"teaching-prep": service}
    api.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(api)

    payload = client.get(
        f"/api/teaching-prep/materials/{version.id}/units"
    ).json()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert str(tmp_path) not in serialized
    assert payload["items"][0]["preview_url"].startswith(
        "/api/teaching-prep/material-units/"
    )
    preview = client.get(payload["items"][0]["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
