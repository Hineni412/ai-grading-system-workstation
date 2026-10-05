from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from backend.reporting.original_paper_exporter import (
    OriginalPaperExporter,
    PrintableScoreContractError,
    aggregate_parent_question_scores,
    detect_printed_question_anchors,
)


def test_cleared_original_export_fails_with_readable_message(tmp_path):
    from backend.files.session_originals import clear_session_originals, OriginalPagesCleared
    from tests.test_review_media_service import _seed_media
    seed = _seed_media(tmp_path)
    clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)
    with pytest.raises(OriginalPagesCleared, match="原卷已清理"):
        OriginalPaperExporter(seed.db, seed.data_root / "reports").export_session_originals(seed.session_id)


def test_original_export_detects_template_anchors_once_and_renders_every_student(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    reports = data_root / "reports"
    databases = data_root / "databases"
    databases.mkdir(parents=True)
    rubric_path = data_root / "rubric.json"
    rubric_path.write_text(
        """
        {
          "questions": [
            {"question_id": "Q1", "max_score": 5, "parts": []}
          ]
        }
        """,
        encoding="utf-8",
    )
    front = data_root / "front.png"
    back = data_root / "back.png"
    Image.new("RGB", (1000, 1400), "white").save(front)
    Image.new("RGB", (1000, 1400), "white").save(back)

    class FakeDb:
        db_path = databases / "grading.db"

        @property
        def student_repository(self):
            return self

        @property
        def session_repository(self):
            return self

        @property
        def paper_repository(self):
            return self

        @property
        def result_repository(self):
            return self

        @property
        def review_repository(self):
            return self

        @property
        def template_repository(self):
            return self

        @property
        def settings_repository(self):
            return self

        @property
        def report_repository(self):
            return self

        def get_session_results(self, _session_id: int):
            return [
                {
                    "result_id": 1,
                    "student_code": "001",
                    "student_name": "甲",
                    "front_image": str(front),
                    "back_image": str(back),
                },
                {
                    "result_id": 2,
                    "student_code": "002",
                    "student_name": "乙",
                    "front_image": str(front),
                    "back_image": str(back),
                },
            ]

        def get_result_details(self, result_id: int):
            return [
                {
                    "question_id": "Q1",
                    "score_awarded": 5 if result_id == 1 else 3,
                    "deduction_reason": "不进入原卷",
                }
            ]

        def get_grading_session(self, _session_id: int):
            return {"session_name": "合成考试", "rubric_path": str(rubric_path)}

        def list_answer_regions(self, _session_id: int):
            return []

        def get_session_template(self, _session_id: int):
            return {
                "front_template_path": str(front),
                "back_template_path": str(back),
            }

    detection_calls: list[tuple[dict[str, Path], list[str]]] = []

    def fake_detection(
        page_paths: dict[str, Path],
        question_ids: list[str],
    ):
        detection_calls.append((page_paths, question_ids))
        return {
            "Q1": {
                "page": "front",
                "x": 180,
                "y": 240,
                "w": 45,
                "h": 38,
                "source_width": 1000,
                "source_height": 1400,
                "kind": "ocr",
                "confidence": 0.99,
            }
        }

    monkeypatch.setattr(
        "backend.reporting.original_paper_exporter.detect_printed_question_anchors",
        fake_detection,
    )

    output = OriginalPaperExporter(FakeDb(), reports).export_session_originals(7)

    assert output.is_file()
    assert len(detection_calls) == 1
    assert detection_calls[0][1] == ["Q1"]
    rendered_pages = sorted(reports.glob("*批注原卷页面*/*.jpg"))
    assert len(rendered_pages) == 4
    for front_page in rendered_pages[::2]:
        with Image.open(front_page).convert("RGB") as image:
            assert any(
                red > 130 and red > green * 1.35 and red > blue * 1.35
                for red, green, blue in (
                    image.getpixel((x, y))
                    for x in range(25, 180)
                    for y in range(180, 320)
                )
            )


def _ocr_row(text, x, y, w=250, h=24, confidence=0.95):
    return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, confidence]


def _synthetic_region_pages(tmp_path):
    from PIL import ImageDraw

    paths = {}
    for marker, page in enumerate(("front", "back"), 1):
        image = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(image)
        for x in (50, 550):
            for number, y in enumerate((200, 400, 600), 1):
                draw.text((x, y), f"{number}. Synthetic question", fill="black")
        draw.text((280, 70), "Name: TEST Class: 8", fill="black")
        image.putpixel((0, 0), (marker, marker, marker))
        path = tmp_path / f"{page}.png"
        image.save(path)
        paths[page] = path
    return paths


def test_shared_candidates_preserve_original_score_anchor_choice(tmp_path):
    pages = _synthetic_region_pages(tmp_path)

    def ocr(image):
        assert image.shape[1] == 1000  # Score export remains a single whole-page pass.
        marker = int(image[0, 0, 0])
        return [
            _ocr_row("1. printed", 80 if marker == 1 else 40, 200, confidence=0.8),
            _ocr_row("1. alternate", 40, 500, confidence=0.99),
            _ocr_row("2. faint", 10, 600, confidence=0.54),
            _ocr_row("99. unknown", 10, 800),
        ], 0.0

    anchors = detect_printed_question_anchors(pages, ["Q1", "Q2"], ocr_engine=ocr)
    assert anchors == {"Q1": {
        "page": "front", "x": 40, "y": 500, "w": 250, "h": 24,
        "source_width": 1000, "source_height": 1400, "confidence": 0.99, "kind": "ocr",
    }}


def test_auto_regions_strip_rescues_missing_line_orders_numbers_and_spans_pages(tmp_path):
    from backend.answer_regions.answer_region_auto_proposal import propose_answer_regions
    from backend.answer_regions.answer_region_models import QuestionBindingCatalog, validate_regions

    pages = _synthetic_region_pages(tmp_path)
    catalog = QuestionBindingCatalog(
        ("Q1", "Q2", "Q3(P1)", "Q3(P2)", "Q4", "Q5"), (), frozenset({"Q3"}),
    )
    calls = []

    def ocr(image):
        width = image.shape[1]
        marker = int(image[0, 0, 0])
        calls.append((marker, width))
        if marker == 1:
            if width == 350:
                return [_ocr_row("2. rescued from handwriting", 52, 400)], 0.0
            return [
                _ocr_row("姓名：测试班级：八9班", 280, 70, w=300),
                _ocr_row("1. printed", 50, 200, w=820),
                _ocr_row("4. out of order", 55, 350),
                _ocr_row("3. printed", 50, 600, w=820),
                _ocr_row("5. body number", 250, 700),
                _ocr_row("5. body number at the right side", 650, 900),
            ], 0.0
        return ([_ocr_row("Continuation working", 50, 70, w=820),
                 _ocr_row("4. printed", 50, 500, w=820)] if width == 1000 else []), 0.0

    proposal = propose_answer_regions(pages, catalog, ocr_engine=ocr)
    regions = proposal["regions"]
    assert proposal["missing_question_ids"] == ["Q5"]
    assert {item["mapped_question_id"] for item in regions} == {"Q1", "Q2", "Q3", "Q4", "__student_name__"}
    assert (1, 350) in calls and (2, 350) in calls
    q1 = next(item for item in regions if item["mapped_question_id"] == "Q1")
    assert (q1["x"], q1["y"], q1["w"], q1["h"]) == (38, 196, 862, 204)
    q3 = [item for item in regions if item["mapped_question_id"] == "Q3"]
    assert [item["page"] for item in q3] == ["front", "back"]
    assert q3[0]["y"] + q3[0]["h"] == 1388
    assert q3[1]["y"] + q3[1]["h"] == 500
    name = next(item for item in regions if item["mapped_question_id"] == "__student_name__")
    assert name["x"] < 280 and 400 < name["x"] + name["w"] < 430
    assert name["y"] == 58 and name["h"] == 48
    assert all(item["mapping_status"] == "auto" and not item["is_confirmed"] for item in regions)
    assert all(item["confidence"] >= 0.55 for item in regions)
    validation = validate_regions(regions, image_sizes={"front": (1000, 1400), "back": (1000, 1400)}, template_matches=True)
    assert [issue.code for issue in validation.issues] == ["unconfirmed_multi_region"]


def test_auto_regions_double_columns_name_label_and_missing_name_fallback(tmp_path):
    from backend.answer_regions.answer_region_auto_proposal import propose_answer_regions
    from backend.answer_regions.answer_region_models import QuestionBindingCatalog

    pages = _synthetic_region_pages(tmp_path)
    calls = []

    def ocr(image):
        calls.append(image.shape[:2])
        if image.shape[1] != 1000 or int(image[0, 0, 0]) != 1:
            return [], 0.0
        return [
            _ocr_row("姓名：", 220, 70, w=50), _ocr_row("班级：", 430, 70, w=80),
            _ocr_row("1. left top", 50, 200, w=380),
            _ocr_row("2. left bottom", 50, 600, w=380),
            _ocr_row("3. right top", 550, 200, w=380),
            _ocr_row("4. right bottom", 550, 600, w=380),
            _ocr_row("2. body number", 350, 300, w=90),
        ], 0.0

    catalog = QuestionBindingCatalog(("Q1", "Q2", "Q3", "Q4"), (), frozenset())
    result = propose_answer_regions(pages, catalog, ocr_engine=ocr)
    assert result["missing_question_ids"] == []
    questions = [item for item in result["regions"] if item["mapped_question_id"] != "__student_name__"]
    assert [item["mapped_question_id"] for item in questions] == ["Q1", "Q2", "Q3", "Q4"]
    assert questions[0]["x"] + questions[0]["w"] < questions[2]["x"]
    assert questions[0]["y"] + questions[0]["h"] == 600
    assert calls.count((1400, 175)) == 2
    name = result["regions"][0]
    assert name["x"] == 214 and name["x"] + name["w"] == 427

    def top_rescan(image):
        if image.shape[:2] == (350, 1000) and int(image[0, 0, 0]) == 1:
            return [_ocr_row("姓名：", 220, 70, w=50)], 0.0
        if image.shape[1] == 1000 and int(image[0, 0, 0]) == 1:
            return [_ocr_row("1. printed", 50, 200)], 0.0
        return [], 0.0

    fallback = propose_answer_regions(pages, catalog, ocr_engine=top_rescan)
    assert fallback["missing_question_ids"] == ["Q2", "Q3", "Q4"]
    assert fallback["regions"][0]["mapped_question_id"] == "__student_name__"
    empty = propose_answer_regions(pages, catalog, ocr_engine=lambda image: ([], 0.0))
    assert empty["regions"] == []
    assert empty["missing_question_ids"] == ["Q1", "Q2", "Q3", "Q4", "__student_name__"]
