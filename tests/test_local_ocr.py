from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import local_ocr
from local_ocr import MineruOcr
from question_bank.document_pipeline import MineruOcrAdapter
from scanner import Scanner, _build_student_lookup


def test_shared_ocr_initializes_once_and_serializes_parallel_inference(monkeypatch) -> None:
    loads = []
    active = []

    class Engine:
        def ocr(self, image):
            assert not active
            active.append(True)
            try:
                time.sleep(.005)
                return [[[[[1, 2], [3, 2], [3, 4], [1, 4]], (str(image), .9)]]]
            finally:
                active.pop()

    def load():
        loads.append(True)
        return Engine()

    engine = MineruOcr()
    monkeypatch.setattr(engine, "_load_engine", load)
    monkeypatch.setattr(local_ocr, "_local_ocr", engine)
    assert loads == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda value: local_ocr.get_local_ocr()(value), range(8)))
    assert loads == [True]
    assert [rows[0][1] for rows, _seconds in results] == [str(i) for i in range(8)]
    assert results[0][0][0] == [[[1, 2], [3, 2], [3, 4], [1, 4]], "0", .9]


def test_missing_models_fail_without_loading_or_downloading(tmp_path: Path) -> None:
    engine = MineruOcr(tmp_path)
    with pytest.raises(FileNotFoundError, match="本地文字识别模型缺失"):
        engine(np.zeros((20, 40, 3), dtype=np.uint8))
    assert list(tmp_path.iterdir()) == []


def test_empty_ocr_page_remains_empty(monkeypatch) -> None:
    class Engine:
        def ocr(self, _image):
            return [None]

    engine = MineruOcr()
    monkeypatch.setattr(engine, "_load_engine", Engine)
    assert engine(None)[0] == []


def test_document_adapter_preserves_image_coordinates_text_and_engine_version(monkeypatch) -> None:
    class Engine:
        engine_version = "mineru/test/pp-ocrv6-onnx"

        def __call__(self, image):
            assert image[0, 0].tolist() == [0, 0, 255]  # PIL RGB becomes OCR BGR.
            return [[[[20, 10], [180, 10], [180, 30], [20, 30]], "1. 合成题目", .93]], .1

    monkeypatch.setattr(local_ocr, "get_local_ocr", lambda: Engine())
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "red").save(buffer, format="PNG")
    lines = MineruOcrAdapter().recognize(buffer.getvalue(), page_number=1)
    assert len(lines) == 1
    assert lines[0].text == "1. 合成题目"
    assert lines[0].polygon == ((.1, .1), (.9, .1), (.9, .3), (.1, .3))
    assert lines[0].confidence == .93
    assert lines[0].engine_version == "mineru/test/pp-ocrv6-onnx"


@pytest.mark.parametrize("unavailable", [False, True])
def test_names_keep_local_match_and_remote_fallback(tmp_path: Path, monkeypatch, unavailable: bool) -> None:
    class LocalEngine:
        calls = 0

        def __call__(self, image):
            self.calls += 1
            if unavailable:
                raise FileNotFoundError("synthetic missing weights")
            # Only the first crop can be matched locally.
            text = "姓名：张三" if self.calls == 1 else ""
            return ([[[], text, .98]] if text else []), .01

    class RemoteClient:
        calls = []

        def text_from_images(self, prompt, images, **_kwargs):
            self.calls.append((prompt, images))
            return '{"1":"张三","2":"李四"}' if unavailable else '{"1":"李四"}'

    local_engine = LocalEngine()
    monkeypatch.setattr(local_ocr, "get_local_ocr", lambda: local_engine)
    client = RemoteClient()
    scanner = Scanner(tmp_path / "scan", client, enhance_images=False)
    paths = []
    for i in range(2):
        path = tmp_path / f"synthetic-{i}.png"
        Image.new("RGB", (200, 300), "white").save(path)
        paths.append(path)
    lookup = _build_student_lookup([
        {"id": 1, "name": "张三", "class_name": "测试班"},
        {"id": 2, "name": "李四", "class_name": "测试班"},
    ])
    names = scanner._extract_student_names_batch(paths, lookup)
    assert names == ["张三", "李四"]
    assert local_engine.calls == 2
    assert len(client.calls) == 1
    assert ("共有 2 个" if unavailable else "共有 1 个") in client.calls[0][0]


def test_score_export_uses_shared_ocr_by_default(tmp_path: Path, monkeypatch) -> None:
    from original_paper_exporter import detect_printed_question_anchors

    image_path = tmp_path / "template.png"
    Image.new("RGB", (200, 300), "white").save(image_path)
    calls = []

    def recognize(image):
        calls.append(image.shape)
        return [[[[10, 20], [80, 20], [80, 40], [10, 40]], "1. 合成题目", .95]], .01

    monkeypatch.setattr(local_ocr, "get_local_ocr", lambda: recognize)
    anchors = detect_printed_question_anchors({"front": image_path}, ["Q1"])
    assert calls == [(300, 200, 3)]
    assert anchors["Q1"]["x"] == 10
    assert anchors["Q1"]["y"] == 20
