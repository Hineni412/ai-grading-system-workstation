"""Shared local text OCR for scan names, score labels and document preparation."""
from __future__ import annotations

import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Any


MODEL_DIRECTORY = (
    Path(__file__).resolve().parent / "runtime" / "models" / "mineru"
    / "MinerU-4_models_onnx" / "OCR" / "paddleocr"
)
MODEL_FILENAMES = (
    "ch_PP-OCRv6_tiny_det_infer.onnx",
    "ch_PP-OCRv6_small_rec_infer.onnx",
)


class MineruOcr:
    """Lazy CPU OCR with pixel polygons; no PDF pipeline or model downloads.

    The application shares one instance. Initialization and inference are serialized
    because scan workers and document/export jobs can use the same model together.
    """

    def __init__(self, model_directory: Path = MODEL_DIRECTORY) -> None:
        self._model_directory = model_directory
        self._engine: Any = None
        self._lock = threading.Lock()

    @property
    def engine_version(self) -> str:
        try:
            return f"mineru/{metadata.version('mineru')}/pp-ocrv6-onnx"
        except metadata.PackageNotFoundError:
            return "mineru/unknown/pp-ocrv6-onnx"

    def _load_engine(self) -> Any:
        paths = [self._model_directory / name for name in MODEL_FILENAMES]
        if not all(path.is_file() for path in paths):
            raise FileNotFoundError(
                "MinerU 本地文字识别模型缺失，请补齐 runtime/models/mineru 中的模型文件。"
            )
        from mineru.model.ocr.pp_ocr_v6_onnx import PPOCRv6ONNX

        return PPOCRv6ONNX(str(paths[0]), str(paths[1]), intra_op_num_threads=4)

    def __call__(self, image: Any) -> tuple[list[Any], float]:
        """Accept a BGR image and return (pixel polygon, text, confidence) rows."""
        with self._lock:
            if self._engine is None:
                self._engine = self._load_engine()
            started = time.perf_counter()
            pages = self._engine.ocr(image)
            rows = pages[0] if pages else None
            result = [
                [box, text_score[0], float(text_score[1])]
                for box, text_score in (rows or [])
            ]
            return result, time.perf_counter() - started


_local_ocr = MineruOcr()


def get_local_ocr() -> MineruOcr:
    return _local_ocr
