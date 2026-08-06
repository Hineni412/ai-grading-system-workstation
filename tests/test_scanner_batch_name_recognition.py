from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from scanner import PageRecord, Scanner


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


def test_name_regions_are_combined_into_ten_per_model_request(tmp_path: Path) -> None:
    client = _BatchClient()
    scanner = Scanner(tmp_path, client, enhance_images=False)
    scanner._do_local_ocr = lambda _image: None  # type: ignore[method-assign]

    names = scanner._extract_student_names_batch(_images(tmp_path, 12))

    assert len(client.calls) == 2
    assert names[:10] == [f"学生{index}" for index in range(1, 11)]
    assert names[10:] == ["学生1", "学生2"]


def test_invalid_batch_response_splits_until_each_name_is_recoverable(
    tmp_path: Path,
) -> None:
    client = _BatchClient(fail_multi=True)
    scanner = Scanner(tmp_path, client, enhance_images=False)
    scanner._do_local_ocr = lambda _image: None  # type: ignore[method-assign]

    names = scanner._extract_student_names_batch(_images(tmp_path, 4))

    assert names == ["学生1", "学生1", "学生1", "学生1"]
    assert len(client.calls) == 7


def test_legacy_scanner_override_keeps_the_existing_parallel_path(tmp_path: Path) -> None:
    class LegacyScanner(Scanner):
        def _extract_student_name(  # type: ignore[override]
            self,
            image_path: Path,
            _student_lookup: object = None,
        ) -> str:
            return image_path.stem

    image_paths = _images(tmp_path, 4)
    pages = [
        PageRecord(path, "answers.pdf", index + 1)
        for index, path in enumerate(image_paths)
    ]
    scanner = LegacyScanner(
        tmp_path,
        _BatchClient(),
        enhance_images=False,
        ocr_workers=2,
    )

    scanner._extract_pdf_page_names_for_indices(pages, list(range(4)))

    assert [page.detected_name for page in pages] == [
        "front-1",
        "front-2",
        "front-3",
        "front-4",
    ]
