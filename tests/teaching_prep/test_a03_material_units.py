from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ops.archive import OpsArchivePolicy
from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.application import preparation_service
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.infrastructure.fakes import FakeWpsAdapter
from backend.teaching_prep.infrastructure.materials import MaterialParser
from backend.teaching_prep.infrastructure.materials.parser import _slide_objects
from backend.jobs import JobManager, JobStore

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _lesson_tree


def _pdf(path: Path, pages: list[str]) -> Path:
    document = fitz.open()
    try:
        for index, text in enumerate(pages, start=1):
            page = document.new_page(width=640, height=900)
            if text:
                page.insert_text((48, 64), text)
                page.insert_text((315, 870), str(index))
        document.save(path)
    finally:
        document.close()
    return path


def _text_pdf_with_image(path: Path) -> Path:
    document = fitz.open()
    try:
        page = document.new_page(width=640, height=900)
        page.insert_text((48, 64), "Embedded text with an illustration")
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 12), 0)
        pixmap.clear_with(0x65AFA3)
        page.insert_image(fitz.Rect(48, 100, 160, 212), stream=pixmap.tobytes("png"))
        document.save(path)
    finally:
        document.close()
    return path


def test_ocr_checkpoint_excludes_text_pages_that_only_contain_illustrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = _text_pdf_with_image(tmp_path / "text-with-image.pdf")
    version = _register(
        service,
        source,
        token="material-a03-text-image",
        name="合成图文教材",
    )

    service.parse_material_version(version.id)
    completed = service.get_material_version(version.id)

    assert completed.preview_completed_count == 1
    assert completed.ocr_total_count == 0
    assert completed.ocr_completed_count == 0


def _scanned_pdf(path: Path) -> Path:
    from PIL import Image, ImageDraw

    scan = Image.new("RGB", (640, 900), "white")
    ImageDraw.Draw(scan).text((80, 120), "synthetic scan", fill="black")
    buffer = io.BytesIO()
    scan.save(buffer, format="PNG")
    document = fitz.open()
    try:
        page = document.new_page(width=640, height=900)
        page.insert_image(page.rect, stream=buffer.getvalue())
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
            <p:nvSpPr>
              <p:cNvPr id="2" name="Question 1"/>
              <p:cNvSpPr/>
              <p:nvPr/>
            </p:nvSpPr>
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
    assert units[0].object_summary["printed_page_number"] == 1
    assert units[0].object_summary["printed_page_number_source"] == (
        "visible_footer_or_header"
    )
    assert units[1].text_status == "empty"
    assert all(unit.preview_url.startswith("/api/teaching-prep/") for unit in units)
    preview = service.material_preview_path(units[0].id)
    assert preview.read_bytes().startswith(b"\x89PNG")
    assert service.get_material_version(version.id).unit_count == 2


def test_interrupted_parse_keeps_page_previews_and_resumes_missing_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    source = _pdf(
        tmp_path / "synthetic-resume.pdf",
        ["Page one", "Page two", "Page three"],
    )
    version = _register(
        service,
        source,
        token="material-a03-resume",
        name="合成可恢复资料",
    )

    def interrupt_after_first_preview(
        phase: str,
        completed: int,
        _total: int,
    ) -> None:
        if phase == "preview" and completed == 1:
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        service.parse_material_version(
            version.id,
            progress_callback=interrupt_after_first_preview,
        )

    partial = service.list_material_units(version.id)
    assert [unit.unit_index for unit in partial] == [1]
    assert service.material_preview_path(partial[0].id).is_file()
    assert service.get_material_version(version.id).unit_count is None
    curriculum, semester, _created = service.create_semester_workspace(
        request_token="material-a03-resume-semester",
        title="合成八年级上册",
        grade_level=8,
        volume="first",
        publisher=None,
        edition_label=None,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    assert curriculum.id == semester.curriculum_id
    attached, _created = service.attach_semester_material(
        semester.id,
        request_token="material-a03-resume-attach",
        material_version_id=version.id,
        material_role="exercise_workbook",
    )
    assert attached.parse_status == "not_started"
    _curriculum_id, _chapter_id, _section_id, lesson_ids = _lesson_tree(
        service
    )
    with pytest.raises(
        TeachingPrepConflictError,
        match="finish parsing",
    ):
        service.create_material_link(
            request_token="material-a03-partial-link",
            lesson_node_id=lesson_ids[0],
            material_version_id=version.id,
            start_unit=1,
            end_unit=1,
            crop=None,
            purpose="exercise",
            teacher_note=None,
            confirmation_status="confirmed",
        )

    resumed_progress: list[tuple[str, int, int]] = []
    completed = service.parse_material_version(
        version.id,
        progress_callback=lambda phase, done, total: resumed_progress.append(
            (phase, done, total)
        ),
    )

    assert [unit.unit_index for unit in completed] == [1, 2, 3]
    assert ("preview", 1, 3) in resumed_progress
    assert service.get_material_version(version.id).unit_count == 3
    assert service.list_semester_materials(semester.id)[0].parse_status == "parsed"
    linked, _created = service.create_material_link(
        request_token="material-a03-complete-link",
        lesson_node_id=lesson_ids[0],
        material_version_id=version.id,
        start_unit=1,
        end_unit=1,
        crop=None,
        purpose="exercise",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    assert (linked.start_unit, linked.end_unit) == (1, 1)


def test_scanned_pdf_uses_local_ocr_without_a_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOcr:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            height, width = image.shape[:2]
            return (
                [
                    (
                        [[80, 120], [300, 120], [300, 160], [80, 160]],
                        "目录",
                        0.99,
                    ),
                    (
                        [[80, 220], [300, 220], [300, 260], [80, 260]],
                        "第1课时 探索勾股定理",
                        0.99,
                    ),
                    (
                        [[320, 220], [390, 220], [390, 260], [320, 260]],
                        "听2",
                        0.99,
                    ),
                    (
                        [[400, 220], [470, 220], [470, 260], [400, 260]],
                        "作1",
                        0.99,
                    ),
                    (
                        [
                            [width * 0.45, height * 0.92],
                            [width * 0.55, height * 0.92],
                            [width * 0.55, height * 0.97],
                            [width * 0.45, height * 0.97],
                        ],
                        "88",
                        0.99,
                    ),
                ],
                0.01,
            )

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    fake_ocr = FakeOcr()
    service.material_parser = MaterialParser(
        ocr_engine_factory=lambda: fake_ocr
    )
    version = _register(
        service,
        _scanned_pdf(tmp_path / "synthetic-scanned-workbook.pdf"),
        token="material-a03-local-ocr",
        name="合成扫描教辅",
    )

    unit = service.parse_material_version(version.id)[0]

    assert fake_ocr.calls == 2
    assert unit.text_status == "empty"
    assert "探索勾股定理" in unit.text_excerpt
    assert unit.object_summary["text_source"] == "local_ocr"
    layout = unit.object_summary["ocr_layout"]
    assert layout["version"] == 1
    assert len(layout["items"]) == 5
    assert all(
        0 <= float(item[field]) <= 1
        for item in layout["items"]
        for field in ("x0", "y0", "x1", "y1")
    )
    assert unit.object_summary["printed_page_number"] == 88
    assert unit.object_summary["printed_page_number_source"] == (
        "local_ocr_footer_or_header"
    )


def test_plain_directory_continuation_preserves_right_page_number_layout() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1000, 1600), "white").save(buffer, format="PNG")

    def fake_ocr(_image):
        rows = []
        for index, title in enumerate(
            (
                "第1课时 探索勾股定理",
                "第2课时 勾股定理的验证",
                "小专题 方程思想",
                "回顾与思考 勾股定理",
            ),
            start=1,
        ):
            y = 160 + index * 120
            rows.extend(
                [
                    ([[80, y], [700, y], [700, y + 48], [80, y + 48]], title, 0.99),
                    ([[900, y], [960, y], [960, y + 48], [900, y + 48]], str(index), 0.99),
                ]
            )
        return rows, 0.01

    parsed = MaterialParser.ocr_preview(buffer.getvalue(), fake_ocr)

    assert len(parsed.layout_items) == 8
    assert "第1课时 探索勾股定理" in parsed.extracted_text


def test_pdf_directory_high_resolution_rechecks_right_page_column(
    tmp_path: Path,
) -> None:
    class SplitColumnOcr:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            height, width = image.shape[:2]
            rows = []
            if width < height * 0.2:
                for index in range(1, 5):
                    y = height * (0.18 + index * 0.08)
                    rows.append(
                        (
                            [[20, y], [80, y], [80, y + 36], [20, y + 36]],
                            str(index),
                            0.99,
                        )
                    )
                return rows, 0.01
            for index in range(1, 5):
                y = height * (0.18 + index * 0.08)
                rows.append(
                    (
                        [[80, y], [700, y], [700, y + 36], [80, y + 36]],
                        f"第{index}课时 合成标题",
                        0.99,
                    )
                )
                if index <= 3:
                    rows.append(
                        (
                            [
                                [width * 0.93, y],
                                [width * 0.97, y],
                                [width * 0.97, y + 36],
                                [width * 0.93, y + 36],
                            ],
                            str(index),
                            0.99,
                        )
                    )
            return rows, 0.01

    fake_ocr = SplitColumnOcr()
    parsed = MaterialParser.ocr_pdf_page(
        _scanned_pdf(tmp_path / "plain-directory.pdf"),
        unit_index=1,
        ocr_engine=fake_ocr,
    )

    assert fake_ocr.calls == 2
    assert [
        item["text"]
        for item in parsed.layout_items
        if str(item["text"]).isdigit()
    ] == ["1", "2", "3", "4"]


def test_landscape_directory_rechecks_listen_and_work_columns(
    tmp_path: Path,
) -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), "white").save(buffer, format="PNG")
    document = fitz.open()
    source = tmp_path / "landscape-directory.pdf"
    try:
        page = document.new_page(width=900, height=600)
        page.insert_image(page.rect, stream=buffer.getvalue())
        document.save(source)
    finally:
        document.close()

    class TrackColumnOcr:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            height, width = image.shape[:2]
            if width < height * 0.3:
                if self.calls == 2:
                    return [
                        (
                            [[40, 1200], [180, 1200], [180, 1260], [40, 1260]],
                            "听25",
                            0.99,
                        )
                    ], 0.01
                return [], 0.01
            rows = []
            for index in range(4):
                y = 480 + index * 360
                rows.extend(
                    [
                        (
                            [[160, y], [900, y], [900, y + 80], [160, y + 80]],
                            f"第{index + 1}课时 合成标题",
                            0.99,
                        ),
                        (
                            [[1600, y], [1740, y], [1740, y + 80], [1600, y + 80]],
                            f"作{19 + index * 2}",
                            0.99,
                        ),
                    ]
                )
            return rows, 0.01

    fake_ocr = TrackColumnOcr()
    parsed = MaterialParser.ocr_pdf_page(
        source,
        unit_index=1,
        ocr_engine=fake_ocr,
    )

    assert fake_ocr.calls == 3
    assert "听25" in [item["text"] for item in parsed.layout_items]


def test_empty_ocr_result_is_persisted_and_not_repeated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyOcr:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _image):
            self.calls += 1
            return ([], 0.01)

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    empty_ocr = EmptyOcr()
    service.material_parser = MaterialParser(
        ocr_engine_factory=lambda: empty_ocr
    )
    version = _register(
        service,
        _scanned_pdf(tmp_path / "synthetic-empty-ocr.pdf"),
        token="material-a03-empty-ocr",
        name="合成空白识别页",
    )

    first = service.parse_material_version(version.id)[0]
    second = service.parse_material_version(version.id)[0]

    assert first.text_excerpt == ""
    assert first.object_summary["ocr_status"] == "completed"
    assert second.object_summary["ocr_status"] == "completed"
    assert empty_ocr.calls == 1


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


def test_pdf_printed_page_number_is_not_the_file_page_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = fitz.open()
    path = tmp_path / "textbook-with-cover.pdf"
    try:
        document.new_page(width=640, height=900)
        page = document.new_page(width=640, height=900)
        page.insert_text((48, 64), "Pythagorean theorem")
        page.insert_text((315, 870), "9")
        document.save(path)
    finally:
        document.close()
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        path,
        token="material-a03-printed-page",
        name="含封面的合成教材",
    )

    units = service.parse_material_version(version.id)

    assert units[1].unit_index == 2
    assert units[1].object_summary["printed_page_number"] == 9


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
    assert units[0].object_summary["occupied_boxes"]
    assert units[0].object_summary["objects"] == [
        {
            "object_ref": "shape:2",
            "wps_object_id": "Question 1",
            "object_type": "text_box",
            "text": "Synthetic introduction",
            "position": {
                "x": 0.098425,
                "y": 0.131234,
                "width": 0.492126,
                "height": 0.233304,
            },
            "has_animation": False,
            "protected_descendant_counts": {},
            "safe_to_delete": True,
        }
    ]
    assert units[1].formula_review_required is True


def test_reparse_enriches_legacy_pptx_units_missing_object_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pptx(tmp_path / "legacy-reference.pptx"),
        token="material-a03-legacy-pptx",
        name="旧解析参考课件",
    )
    units = service.parse_material_version(version.id)
    legacy_summary = dict(units[0].object_summary)
    legacy_summary.pop("objects")
    with service.material_units._database.connect(immediate=True) as connection:
        connection.execute(
            "UPDATE material_units SET object_summary_json = ? WHERE id = ?",
            (json.dumps(legacy_summary), units[0].id),
        )

    reparsed = service.parse_material_version(version.id)

    assert reparsed[0].object_summary["objects"][0]["object_ref"] == "shape:2"


def test_pptx_animation_blocks_only_the_targeted_shape() -> None:
    root = ElementTree.fromstring(
        """
        <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <p:cSld><p:spTree>
            <p:sp><p:nvSpPr><p:cNvPr id="2" name="Animated question"/></p:nvSpPr>
              <p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="200" cy="100"/></a:xfrm></p:spPr>
              <p:txBody><a:p><a:r><a:t>Animated</a:t></a:r></a:p></p:txBody>
            </p:sp>
            <p:sp><p:nvSpPr><p:cNvPr id="3" name="Static question"/></p:nvSpPr>
              <p:spPr><a:xfrm><a:off x="400" y="100"/><a:ext cx="200" cy="100"/></a:xfrm></p:spPr>
              <p:txBody><a:p><a:r><a:t>Static</a:t></a:r></a:p></p:txBody>
            </p:sp>
          </p:spTree></p:cSld>
          <p:timing><p:tnLst><p:par><p:spTgt spid="2"/></p:par></p:tnLst></p:timing>
        </p:sld>
        """
    )

    objects = _slide_objects(root, 1_000, 1_000)

    assert objects[0]["has_animation"] is True
    assert objects[0]["safe_to_delete"] is True
    assert objects[1]["has_animation"] is False
    assert objects[1]["safe_to_delete"] is True


def test_pptx_unknown_animation_targets_keep_the_whole_slide_protected() -> None:
    root = ElementTree.fromstring(
        """
        <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <p:cSld><p:spTree><p:sp>
            <p:nvSpPr><p:cNvPr id="2" name="Question"/></p:nvSpPr>
            <p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="200" cy="100"/></a:xfrm></p:spPr>
            <p:txBody><a:p><a:r><a:t>Question</a:t></a:r></a:p></p:txBody>
          </p:sp></p:spTree></p:cSld>
          <p:timing><p:tnLst><p:par/></p:tnLst></p:timing>
        </p:sld>
        """
    )

    objects = _slide_objects(root, 1_000, 1_000)

    assert objects[0]["has_animation"] is True
    assert objects[0]["safe_to_delete"] is False


def test_pptx_object_inventory_exposes_group_as_one_top_level_wps_object() -> None:
    root = ElementTree.fromstring(
        """
        <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <p:cSld><p:spTree>
            <p:grpSp>
              <p:nvGrpSpPr><p:cNvPr id="11" name="Question diagram"/></p:nvGrpSpPr>
              <p:grpSpPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="400" cy="300"/></a:xfrm></p:grpSpPr>
              <p:sp><p:nvSpPr><p:cNvPr id="12" name="Nested label"/></p:nvSpPr>
                <p:spPr><a:xfrm><a:off x="20" y="30"/><a:ext cx="40" cy="30"/></a:xfrm></p:spPr>
                <p:txBody><a:p><a:r><a:t>A</a:t></a:r></a:p></p:txBody>
              </p:sp>
            </p:grpSp>
          </p:spTree></p:cSld>
        </p:sld>
        """
    )

    objects = _slide_objects(root, 1_000, 1_000)

    assert len(objects) == 1
    assert objects[0]["object_ref"] == "shape:11"
    assert objects[0]["wps_object_id"] == "Question diagram"
    assert objects[0]["object_type"] == "grpSp"
    assert objects[0]["safe_to_delete"] is True


def test_pptx_archive_rejects_compression_bomb_before_reading_slide_xml(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-compression-bomb.pptx"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main"><p:sldSz cx="12192000" '
            'cy="6858000"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main"/>',
        )
        archive.writestr("ppt/media/compressed.bin", b"0" * 200_000)
    parser = MaterialParser(
        pptx_archive_policy=OpsArchivePolicy(
            max_members=20,
            max_member_bytes=1_000_000,
            max_expanded_bytes=1_000_000,
            max_compression_ratio=5,
        )
    )

    with pytest.raises(
        Exception,
        match="PPTX exceeds the safe expansion budget",
    ):
        parser.unit_count(source, material_type="pptx")


def test_pptx_archive_rejects_too_many_members_before_reading_slide_xml(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-too-many-members.pptx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main"/>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main"/>',
        )
        archive.writestr("ppt/media/one.bin", b"1")
        archive.writestr("ppt/media/two.bin", b"2")
    parser = MaterialParser(
        pptx_archive_policy=OpsArchivePolicy(
            max_members=3,
            max_member_bytes=1_000_000,
            max_expanded_bytes=1_000_000,
            max_compression_ratio=100,
        )
    )

    with pytest.raises(
        Exception,
        match="PPTX exceeds the safe expansion budget",
    ):
        parser.unit_count(source, material_type="pptx")


def test_pptx_real_preview_is_lazy_fingerprint_cached_and_keeps_source_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    adapter = FakeWpsAdapter()
    service.wps_adapter = adapter
    service.wps_adapter_is_real = True
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-lazy-preview.pptx"),
        token="material-a03-pptx-lazy-preview",
        name="合成懒加载参考课件",
    )
    controlled_source = service.catalog.get_material_location(version.id)
    before_sha = hashlib.sha256(controlled_source.read_bytes()).hexdigest()

    units = service.parse_material_version(version.id)
    assert adapter.preview_calls == []
    assert units[0].object_summary["preview_kind"] == "structural"

    preview = service.material_preview_path(units[0].id)
    rendered = service.list_material_units(version.id)[0]
    service.material_preview_path(units[0].id)

    assert len(adapter.preview_calls) == 1
    assert adapter.preview_calls[0]["slide_indexes"] == [1]
    assert preview.read_bytes().startswith(b"\x89PNG")
    assert rendered.object_summary["preview_kind"] == "rendered"
    assert rendered.object_summary["rendered_source_sha256"] == version.content_sha256
    assert hashlib.sha256(controlled_source.read_bytes()).hexdigest() == before_sha


def test_material_delete_waits_for_lazy_preview_publish_and_leaves_no_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    service.wps_adapter = FakeWpsAdapter()
    service.wps_adapter_is_real = True
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-preview-delete-race.pptx"),
        token="material-a03-preview-delete-race",
        name="合成预览删除竞态课件",
    )
    unit = service.parse_material_version(version.id)[0]
    record = service.material_units.preview_record(unit.id)
    preview_path = (service.root / record.preview_relpath).resolve()
    impact = service.preview_material_deletion(
        version.source_id,
        expected_revision=version.source_revision,
    )
    render_replace_ready = threading.Event()
    release_render_replace = threading.Event()
    real_replace = preparation_service.os.replace

    def pause_render_publish(source_path, target_path) -> None:
        source = Path(source_path).resolve()
        target = Path(target_path).resolve()
        if (
            target == preview_path
            and source.parent == preview_path.parent
            and source.name.startswith(f".{preview_path.name}.")
            and source.name.endswith(".tmp")
        ):
            render_replace_ready.set()
            if not release_render_replace.wait(timeout=5):
                raise TimeoutError("synthetic preview publish was not released")
        real_replace(source_path, target_path)

    monkeypatch.setattr(
        preparation_service.os,
        "replace",
        pause_render_publish,
    )
    render_errors: list[BaseException] = []
    delete_errors: list[BaseException] = []
    delete_results: list[dict[str, object]] = []

    def render_preview() -> None:
        try:
            service.material_preview_path(unit.id)
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            render_errors.append(exc)

    def delete_material() -> None:
        try:
            delete_results.append(
                service.delete_material_source(
                    version.source_id,
                    expected_revision=version.source_revision,
                    operation_id="material-a03-preview-delete-operation",
                    preview_version=str(impact["preview_version"]),
                    confirmation_phrase=str(impact["confirmation_phrase"]),
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            delete_errors.append(exc)

    render_worker = threading.Thread(target=render_preview)
    delete_worker = threading.Thread(target=delete_material)
    render_worker.start()
    assert render_replace_ready.wait(timeout=5)
    delete_worker.start()
    delete_worker.join(timeout=0.2)
    deletion_waited_for_render = delete_worker.is_alive()
    release_render_replace.set()
    render_worker.join(timeout=10)
    delete_worker.join(timeout=10)

    assert deletion_waited_for_render is True
    assert not render_worker.is_alive()
    assert not delete_worker.is_alive()
    assert render_errors == []
    assert delete_errors == []
    assert delete_results[0]["status"] == "succeeded"
    assert not preview_path.exists()
    preview_root = service.paths["previews"] / version.id
    assert not preview_root.exists() or not any(preview_root.rglob("*"))
    with service.database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM material_units WHERE id = ?",
            (unit.id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM material_versions WHERE id = ?",
            (version.id,),
        ).fetchone() is None


def test_pptx_rendered_preview_rebuilds_when_cached_bytes_no_longer_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    adapter = FakeWpsAdapter()
    service.wps_adapter = adapter
    service.wps_adapter_is_real = True
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-corrupt-rendered-preview.pptx"),
        token="material-a03-pptx-corrupt-rendered-preview",
        name="合成损坏缓存参考课件",
    )
    unit = service.parse_material_version(version.id)[0]
    preview = service.material_preview_path(unit.id)
    preview.write_bytes(b"corrupt rendered preview")

    rebuilt = service.material_preview_path(unit.id)
    refreshed = service.get_material_unit(unit.id)

    assert len(adapter.preview_calls) == 2
    assert rebuilt.read_bytes().startswith(b"\x89PNG")
    assert refreshed.object_summary["preview_kind"] == "rendered"
    assert refreshed.object_summary["preview_render_status"] == "completed"


def test_pptx_corrupt_rendered_preview_falls_back_to_structural_when_wps_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    adapter = FakeWpsAdapter()
    service.wps_adapter = adapter
    service.wps_adapter_is_real = True
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-corrupt-preview-fallback.pptx"),
        token="material-a03-pptx-corrupt-preview-fallback",
        name="合成损坏缓存降级课件",
    )
    unit = service.parse_material_version(version.id)[0]
    preview = service.material_preview_path(unit.id)
    preview.write_bytes(b"corrupt rendered preview")
    adapter.failure = TimeoutError("synthetic rebuild timeout")

    fallback_path = service.material_preview_path(unit.id)
    fallback = service.get_material_unit(unit.id)

    assert len(adapter.preview_calls) == 2
    assert fallback_path.read_bytes().startswith(b"\x89PNG")
    assert fallback.object_summary["preview_kind"] == "structural"
    assert fallback.object_summary["preview_notice"] == "结构预览，不是原页"
    assert fallback.object_summary["preview_render_status"] == "failed"
    assert fallback.object_summary["preview_render_error_code"] == (
        "wps_preview_timeout"
    )


def test_pptx_real_preview_timeout_keeps_labelled_structural_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    adapter = FakeWpsAdapter(failure=TimeoutError("synthetic timeout"))
    service.wps_adapter = adapter
    service.wps_adapter_is_real = True
    version = _register(
        service,
        _pptx(tmp_path / "synthetic-preview-timeout.pptx"),
        token="material-a03-pptx-preview-timeout",
        name="合成预览超时课件",
    )
    unit = service.parse_material_version(version.id)[0]

    structural_path = service.material_preview_path(unit.id)
    fallback = service.list_material_units(version.id)[0]
    service.material_preview_path(unit.id)

    assert structural_path.read_bytes().startswith(b"\x89PNG")
    assert len(adapter.preview_calls) == 1
    assert fallback.object_summary["preview_kind"] == "structural"
    assert fallback.object_summary["preview_notice"] == "结构预览，不是原页"
    assert fallback.object_summary["preview_render_status"] == "failed"


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


def test_material_parse_job_endpoint_reuses_the_active_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    version = _register(
        service,
        _pdf(tmp_path / "synthetic-job.pdf", ["Safe preview"]),
        token="material-a03-job",
        name="合成后台资料",
    )
    manager = JobManager(
        JobStore(tmp_path / "jobs.db"),
        cleanup_interrupted=False,
    )
    started = threading.Event()
    release = threading.Event()

    def blocking_parse(context):
        context.report(0.25, "preview", "正在生成原页预览：0/1 页")
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("synthetic job timed out")
        return {"material_version_id": version.id, "unit_count": 1}

    manager.register("teaching_prep.material_parse", blocking_parse)
    monkeypatch.setattr(
        manager,
        "list",
        lambda **_kwargs: pytest.fail(
            "资料任务读取不应再分页搬运完整历史"
        ),
    )
    api = FastAPI()
    api.state.workspace_services = {"teaching-prep": service}
    api.state.job_manager = manager
    api.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(api)
    try:
        first = client.post(
            f"/api/teaching-prep/materials/{version.id}/parse-job"
        )
        assert first.status_code == 202
        assert started.wait(timeout=2)
        second = client.post(
            f"/api/teaching-prep/materials/{version.id}/parse-job"
        )

        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["payload"] == {"material_version_id": version.id}
        listed = client.get("/api/teaching-prep/material-parse-jobs")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [
            first.json()["id"]
        ]
    finally:
        release.set()
        manager.shutdown()
