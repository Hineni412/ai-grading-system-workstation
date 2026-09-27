from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from original_paper_exporter import (
    OriginalPaperExporter,
    PrintableScoreContractError,
    aggregate_parent_question_scores,
)


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
        "original_paper_exporter.detect_printed_question_anchors",
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
