from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from backend.media.annotation_renderer import AnnotationCoverageError, render_annotated_paper


def _blank_page(path: Path) -> None:
    Image.new("RGB", (1000, 1400), "white").save(path)


def _render_score_box(
    tmp_path: Path,
    *,
    question_id: str = "Q1",
    deduction_reason: str = "",
    suffix: str = "",
) -> Path:
    front = tmp_path / f"front{suffix}.png"
    back = tmp_path / f"back{suffix}.png"
    output_front = tmp_path / f"front-out{suffix}.png"
    output_back = tmp_path / f"back-out{suffix}.png"
    _blank_page(front)
    _blank_page(back)

    render_annotated_paper(
        front_image=front,
        back_image=back,
        regions=[],
        question_scores={
            question_id: {
                "score_awarded": 5,
                "max_score": 5,
                "deduction_reason": deduction_reason,
            }
        },
        question_anchors={
            question_id: {
                "page": "front",
                "x": 210,
                "y": 260,
                "w": 36,
                "h": 34,
                "source_width": 1000,
                "source_height": 1400,
                "kind": "ocr",
            }
        },
        output_front=output_front,
        output_back=output_back,
        annotation_layout="question_score_boxes",
    )
    return output_front


def test_full_score_box_is_drawn_left_of_printed_number_inside_safe_area(
    tmp_path: Path,
) -> None:
    output = _render_score_box(tmp_path)

    with Image.open(output).convert("RGB") as rendered:
        red_pixels = [
            (x, y)
            for x in range(rendered.width)
            for y in range(rendered.height)
            if (
                (red := rendered.getpixel((x, y)))[0] > 130
                and red[0] > red[1] * 1.35
                and red[0] > red[2] * 1.35
            )
        ]

    assert red_pixels
    assert min(x for x, _ in red_pixels) >= 25
    assert max(x for x, _ in red_pixels) < 210
