from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from backend.scan_grading.scanner import Scanner, _clean_student_name_text


class _BatchClient:
    def __init__(self, *, fail_multi: bool = False) -> None:
        self.fail_multi = fail_multi
        self.calls: list[tuple[str, list[bytes]]] = []

    def text_from_images(
        self,
        prompt: str,
        images: list[bytes],
        **_kwargs: object,
    ) -> str:
        self.calls.append((prompt, images))
        match = re.search(r"共有 (\d+) 个", prompt)
        assert match is not None
        count = int(match.group(1))
        assert len(images) == 1
        if self.fail_multi and count > 1:
            return "not-json"
        return json.dumps(
            {str(index): f"学生{index}" for index in range(1, count + 1)},
            ensure_ascii=False,
        )


def _images(tmp_path: Path, count: int) -> list[Path]:
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"front-{index + 1}.jpg"
        Image.new("RGB", (800, 1100), "white").save(path, format="JPEG")
        paths.append(path)
    return paths


def test_invalid_batch_response_splits_until_each_name_is_recoverable(
    tmp_path: Path,
) -> None:
    client = _BatchClient(fail_multi=True)
    scanner = Scanner(tmp_path, client, enhance_images=False)
    scanner._do_local_ocr = lambda _image: None  # type: ignore[method-assign]

    names = scanner._extract_student_names_batch(_images(tmp_path, 4))

    assert names == ["学生1", "学生1", "学生1", "学生1"]
    assert len(client.calls) == 7


def test_wide_header_extracts_name_before_class_and_number(tmp_path):
    assert _clean_student_name_text("第3周学情反馈\n姓名：张三 班级：809 学号：123") == "张三"
    assert _clean_student_name_text("张三 班级：809 学号：123") == "张三"
    client = _BatchClient()
    scanner = Scanner(tmp_path, client, enhance_images=False,
                      name_region={"x": 0, "y": 0, "w": 800, "h": 220})
    scanner._do_local_ocr = lambda _image: "第3周学情反馈 姓名：张三 班级：809"  # type: ignore[method-assign]
    names = scanner._extract_student_names_batch(
        _images(tmp_path, 1), {"张三": {"id": 1, "name": "张三", "class_name": "9"}},
    )
    assert names == ["张三"]
    assert client.calls == []
