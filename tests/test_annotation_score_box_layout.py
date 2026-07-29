from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from annotation_renderer import AnnotationCoverageError, render_annotated_paper


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


def test_score_box_bitmap_does_not_depend_on_question_id_or_reason(
    tmp_path: Path,
) -> None:
    first = _render_score_box(
        tmp_path,
        question_id="Q1",
        deduction_reason="过程错误，扣除五分",
        suffix="-first",
    )
    second = _render_score_box(
        tmp_path,
        question_id="Q99",
        deduction_reason="完全不同而且很长的扣分原因",
        suffix="-second",
    )

    with Image.open(first).convert("RGB") as first_image:
        first_bytes = first_image.tobytes()
    with Image.open(second).convert("RGB") as second_image:
        second_bytes = second_image.tobytes()

    assert first_bytes == second_bytes


def test_missing_score_anchor_is_reported_instead_of_silently_omitted(
    tmp_path: Path,
) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    _blank_page(front)
    _blank_page(back)

    with pytest.raises(AnnotationCoverageError, match="Q12"):
        render_annotated_paper(
            front_image=front,
            back_image=back,
            regions=[],
            question_scores={
                "Q12": {
                    "score_awarded": 3,
                    "max_score": 6,
                }
            },
            question_anchors={},
            output_front=tmp_path / "front-out.png",
            output_back=tmp_path / "back-out.png",
            annotation_layout="question_score_boxes",
        )


def test_anchor_near_print_edge_moves_score_box_above_instead_of_covering_text(
    tmp_path: Path,
) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    _blank_page(front)
    _blank_page(back)

    render_annotated_paper(
        front_image=front,
        back_image=back,
        regions=[],
        question_scores={"Q1": {"score_awarded": 4, "max_score": 5}},
        question_anchors={
            "Q1": {
                "page": "front",
                "x": 30,
                "y": 260,
                "w": 900,
                "h": 34,
                "source_width": 1000,
                "source_height": 1400,
                "kind": "ocr",
            }
        },
        output_front=tmp_path / "front-out.png",
        output_back=tmp_path / "back-out.png",
        annotation_layout="question_score_boxes",
    )

    with Image.open(tmp_path / "front-out.png").convert("RGB") as rendered:
        assert not any(
            red > 130 and red > green * 1.35 and red > blue * 1.35
            for red, green, blue in (
                rendered.getpixel((x, y))
                for x in range(25, 950)
                for y in range(260, 295)
            )
        )
        assert any(
            red > 130 and red > green * 1.35 and red > blue * 1.35
            for red, green, blue in (
                rendered.getpixel((x, y))
                for x in range(25, 150)
                for y in range(180, 255)
            )
        )


def test_legacy_subquestion_region_is_a_safe_parent_anchor_fallback(
    tmp_path: Path,
) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    _blank_page(front)
    _blank_page(back)

    render_annotated_paper(
        front_image=front,
        back_image=back,
        regions=[
            {
                "page": "front",
                "x": 240,
                "y": 300,
                "w": 500,
                "h": 240,
                "source_width": 1000,
                "source_height": 1400,
                "mapped_question_id": "Q10(P2)",
            }
        ],
        question_scores={
            "Q10": {
                "score_awarded": 9,
                "max_score": 10,
            }
        },
        question_anchors={},
        output_front=tmp_path / "front-out.png",
        output_back=tmp_path / "back-out.png",
        annotation_layout="question_score_boxes",
    )

    with Image.open(tmp_path / "front-out.png").convert("RGB") as rendered:
        assert any(
            red > 130 and red > green * 1.35 and red > blue * 1.35
            for red, green, blue in (
                rendered.getpixel((x, y))
                for x in range(25, 240)
                for y in range(220, 420)
            )
        )
