from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

import objective_crop_calibration
from choice_recognition_chain import crop_choice_region


def test_active_choice_crop_still_uses_calibration_box(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(
        objective_crop_calibration,
        "get_path_manager",
        lambda: SimpleNamespace(data_root=data_root),
    )
    front_image = tmp_path / "front.png"
    image = Image.new("RGB", (80, 60), "white")
    ImageDraw.Draw(image).line((10, 12, 39, 12), fill="black", width=3)
    image.save(front_image)

    output_path, contaminated, edge_touched, source = crop_choice_region(
        front_image,
        tmp_path / "missing-back.png",
        "Q1",
        [
            {
                "mapped_question_id": "Q1",
                "page": "front",
                "x": 0,
                "y": 0,
                "w": 80,
                "h": 60,
                "recognition_box": {"x": 10, "y": 12, "w": 30, "h": 20},
            }
        ],
        tmp_path / "crops",
        session_id="isolated-session",
    )

    assert output_path is not None
    assert output_path.is_file()
    assert Image.open(output_path).size == (30, 20)
    assert source == "recognition_box"
    assert contaminated is False
    assert edge_touched is True
