from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from annotation_renderer import render_annotated_paper


def _source_page(path: Path) -> None:
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 900, 1300), outline="black", width=4)
    draw.line((300, 500, 700, 700), fill="black", width=10)
    draw.line((300, 700, 700, 500), fill="black", width=10)
    image.save(path)


def test_outer_margin_annotations_keep_text_outside_the_scaled_original(
    tmp_path: Path,
) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    output_front = tmp_path / "front-out.png"
    output_back = tmp_path / "back-out.png"
    _source_page(front)
    _source_page(back)
    regions = [
        {
            "page": "front",
            "x": 250,
            "y": 400,
            "w": 500,
            "h": 400,
            "source_width": 1000,
            "source_height": 1400,
            "mapped_question_id": "Q1",
        },
        {
            "page": "back",
            "x": 250,
            "y": 400,
            "w": 500,
            "h": 400,
            "source_width": 1000,
            "source_height": 1400,
            "mapped_question_id": "Q1",
        },
    ]

    render_annotated_paper(
        front_image=front,
        back_image=back,
        regions=regions,
        question_scores={
            "Q1": {
                "score_awarded": 5,
                "max_score": 10,
                "deduction_reason": "计算过程有误",
                "question_type": "calculation",
            }
        },
        output_front=output_front,
        output_back=output_back,
        annotate_only_deductions=True,
        annotation_layout="outer_margin",
        outer_margin_scale=0.95,
    )

    with Image.open(output_front).convert("RGB") as rendered_front:
        assert rendered_front.size == (1000, 1400)
        # 正面右侧为批注栏，原图缩放后仍能看到原有黑色作答笔迹。
        assert any(
            rendered_front.getpixel((x, y))[0] < 80
            for x in range(280, 680, 20)
            for y in range(500, 700, 20)
        )
        assert any(
            red > 120 and red > green * 1.4
            for red, green, _blue in (
                rendered_front.getpixel((x, y))
                for x in range(952, 998)
                for y in range(50, 1350, 5)
            )
        )

    with Image.open(output_back).convert("RGB") as rendered_back:
        assert rendered_back.size == (1000, 1400)
        # 反面左侧为批注栏，便于双面打印时始终位于外侧。
        assert any(
            red > 120 and red > green * 1.4
            for red, green, _blue in (
                rendered_back.getpixel((x, y))
                for x in range(2, 48)
                for y in range(50, 1350, 5)
            )
        )
