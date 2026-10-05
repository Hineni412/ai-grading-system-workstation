from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import backend.document_parsing.local_ocr as local_ocr
from backend.document_parsing.local_ocr import MineruOcr
from backend.scan_grading.scanner import Scanner, _build_student_lookup


def test_shared_ocr_initializes_once_and_serializes_parallel_inference(
    monkeypatch,
) -> None:
    loads = []
    active = []

    class Engine:
        def ocr(self, image):
            assert not active
            active.append(True)
            try:
                time.sleep(0.005)
                return [[[[[1, 2], [3, 2], [3, 4], [1, 4]], (str(image), 0.9)]]]
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
        results = list(
            pool.map(lambda value: local_ocr.get_local_ocr()(value), range(8))
        )
    assert loads == [True]
    assert [rows[0][1] for rows, _seconds in results] == [str(i) for i in range(8)]
    assert results[0][0][0] == [[[1, 2], [3, 2], [3, 4], [1, 4]], "0", 0.9]
