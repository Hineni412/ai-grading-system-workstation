from __future__ import annotations

import re
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any


class FakeMaterialReader:
    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self._records = deepcopy(records or {})
        self.calls: list[str] = []

    def inspect(self, source_ref: str) -> dict[str, Any]:
        self.calls.append(source_ref)
        if source_ref not in self._records:
            raise KeyError("synthetic material was not found")
        return deepcopy(self._records[source_ref])


class FakeEvidenceReader:
    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self.snapshot = deepcopy(
            snapshot
            or {
                "version": "synthetic-v1",
                "coverage": 0,
                "items": [],
                "missing": ["no_selected_assessment"],
            }
        )
        self.calls: list[dict[str, Any]] = []

    def read(self, query: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(deepcopy(query))
        return deepcopy(self.snapshot)

    def list_assessments(self) -> list[dict[str, Any]]:
        items = self.snapshot.get("available_assessments", [])
        return deepcopy(items if isinstance(items, list) else [])


class FakeQuestionEvidenceReader(FakeEvidenceReader):
    def list_questions(self) -> list[dict[str, Any]]:
        items = self.snapshot.get("available_questions", [])
        return deepcopy(items if isinstance(items, list) else [])


class FakeAssessmentEvidenceReader(FakeEvidenceReader):
    pass


class FakeLessonModelAdapter:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.result = deepcopy(result or {"sections": [], "citations": []})
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        operation_id: str,
        resource_pack: dict[str, Any],
        task_model_gateway: object | None = None,
    ) -> dict[str, Any]:
        del task_model_gateway
        self.calls.append(
            {
                "operation_id": operation_id,
                "resource_pack": deepcopy(resource_pack),
            }
        )
        if self.failure is not None:
            raise self.failure
        return deepcopy(self.result)


class FakeExerciseSuggestionModelAdapter:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.result = deepcopy(result or {"suggestions": []})
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        operation_id: str,
        reference_snapshot: dict[str, Any],
        task_model_gateway: object | None = None,
    ) -> dict[str, Any]:
        del task_model_gateway
        self.calls.append(
            {
                "operation_id": operation_id,
                "reference_snapshot": deepcopy(reference_snapshot),
            }
        )
        if self.failure is not None:
            raise self.failure
        return deepcopy(self.result)


class FakeWpsAdapter:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        failure: Exception | None = None,
        materialize: bool = True,
    ) -> None:
        self.result = deepcopy(result) if result is not None else None
        self.failure = failure
        self.materialize = materialize
        self.calls: list[dict[str, Any]] = []
        self.preview_calls: list[dict[str, Any]] = []

    def execute(
        self,
        *,
        operation_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            {"operation_id": operation_id, "plan": deepcopy(plan)}
        )
        if self.failure is not None:
            raise self.failure
        if self.materialize:
            _materialize_synthetic_pptx(plan)
        if self.result is not None:
            return deepcopy(self.result)
        operations = plan.get("operations")
        operation_items = operations if isinstance(operations, list) else []
        return {
            "status": "completed",
            "source_lock_check": "passed",
            "applied_operation_ids": [
                str(item.get("operation_id"))
                for item in operation_items
                if isinstance(item, dict)
            ],
            "reopened_in_wps": True,
            "rendered_all_slides": True,
            "slideshow_check_passed": True,
            "unapproved_content_preserved": True,
            "synthetic": True,
        }

    def render_previews(
        self,
        *,
        operation_id: str,
        source_copy: str,
        preview_directory: str,
        slide_indexes: list[int],
        source_sha256: str,
        timeout_milliseconds: int,
    ) -> dict[str, Any]:
        call = {
            "operation_id": operation_id,
            "source_copy": source_copy,
            "preview_directory": preview_directory,
            "slide_indexes": list(slide_indexes),
            "source_sha256": source_sha256,
            "timeout_milliseconds": timeout_milliseconds,
        }
        self.preview_calls.append(deepcopy(call))
        if self.failure is not None:
            raise self.failure
        destination = Path(preview_directory)
        destination.mkdir(parents=True, exist_ok=True)
        from PIL import Image, ImageDraw

        for index in slide_indexes:
            image = Image.new("RGB", (1600, 900), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 1599, 899), outline=(35, 94, 107), width=4)
            draw.text(
                (40, 32),
                f"Synthetic WPS source slide {index}",
                fill="black",
            )
            image.save(destination / f"slide-{index:05d}.png")
        return {
            "status": "completed",
            "source_lock_check": "passed",
            "rendered_slide_indexes": list(slide_indexes),
            "source_unchanged": True,
            "synthetic": True,
        }


def _materialize_synthetic_pptx(plan: dict[str, Any]) -> None:
    from PIL import Image, ImageDraw

    source = Path(str(plan["source"]["isolated_copy_path"]))
    candidate = Path(str(plan["output"]["candidate_path"]))
    preview_dir = Path(str(plan["output"]["preview_directory"]))
    candidate.parent.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    operations = [
        item
        for item in plan.get("operations", [])
        if isinstance(item, dict)
    ]
    delete_indexes = {
        int(item["target"]["generated_page_number"]) - 1
        for item in operations
        if item.get("kind") == "delete_slide"
        and isinstance(item.get("target"), dict)
        and isinstance(
            item["target"].get("generated_page_number"),
            int,
        )
    }
    added_slide_ids = [
        str(item.get("operation_id"))
        for item in operations
        if item.get("kind") == "add_slide"
    ]
    image_slide_ids = {
        str(item["target"].get("new_slide_operation_id"))
        for item in operations
        if item.get("kind") == "insert_static_image"
        and isinstance(item.get("target"), dict)
    }
    existing_image_counts: dict[int, int] = {}
    for item in operations:
        if item.get("kind") != "insert_static_image":
            continue
        target = item.get("target")
        if not isinstance(target, dict) or target.get("target_kind") != "existing_slide":
            continue
        page = target.get("generated_page_number")
        if isinstance(page, int) and page > 0:
            existing_image_counts[page] = existing_image_counts.get(page, 0) + 1
    slide_pattern = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        original_slides = sorted(
            (
                (int(match.group(1)), archive.read(name))
                for name in names
                if (match := slide_pattern.fullmatch(name))
            ),
            key=lambda item: item[0],
        )
        slide_payloads = [
            _with_synthetic_pictures(
                data,
                existing_image_counts.get(position + 1, 0),
            )
            for position, (_index, data) in enumerate(original_slides)
            if position not in delete_indexes
        ]
        slide_payloads.extend(
            _synthetic_slide_xml(
                f"Synthetic added slide {index}",
                with_picture=operation_id in image_slide_ids,
            )
            for index, operation_id in enumerate(
                added_slide_ids,
                start=1,
            )
        )
        with zipfile.ZipFile(candidate, "w") as output:
            for name in names:
                if slide_pattern.fullmatch(name):
                    continue
                output.writestr(name, archive.read(name))
            if "ppt/theme/theme1.xml" not in names:
                output.writestr(
                    "ppt/theme/theme1.xml",
                    (
                        '<a:theme xmlns:a="http://schemas.openxmlformats.org/'
                        'drawingml/2006/main" name="Synthetic"/>'
                    ),
                )
            if "ppt/slideMasters/slideMaster1.xml" not in names:
                output.writestr(
                    "ppt/slideMasters/slideMaster1.xml",
                    (
                        '<p:sldMaster xmlns:p="http://schemas.openxmlformats.'
                        'org/presentationml/2006/main"/>'
                    ),
                )
            for index, data in enumerate(slide_payloads, start=1):
                output.writestr(f"ppt/slides/slide{index}.xml", data)
    for existing in preview_dir.glob("slide-*.png"):
        existing.unlink()
    for index in range(1, len(slide_payloads) + 1):
        image = Image.new("RGB", (960, 540), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 959, 539), outline=(35, 94, 107), width=3)
        draw.text((24, 20), f"Synthetic WPS preview {index}", fill="black")
        image.save(preview_dir / f"slide-{index:05d}.png")


def _synthetic_slide_xml(title: str, *, with_picture: bool) -> bytes:
    picture = (
        '<p:pic><p:spPr><a:xfrm><a:off x="1000000" y="1000000"/>'
        '<a:ext cx="5000000" cy="3000000"/></a:xfrm></p:spPr></p:pic>'
        if with_picture
        else ""
    )
    return (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
        'presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:spPr/><p:txBody><a:p><a:r><a:t>"
        f"{title}</a:t></a:r></a:p></p:txBody></p:sp>"
        f"{picture}</p:spTree></p:cSld></p:sld>"
    ).encode("utf-8")


def _with_synthetic_pictures(payload: bytes, count: int) -> bytes:
    if count <= 0:
        return payload
    marker = b"</p:spTree>"
    if marker not in payload:
        return payload
    pictures = "".join(
        (
            '<p:pic><p:nvPicPr><p:cNvPr '
            f'id="{9000 + index}" name="Synthetic inserted image {index}"/>'
            '</p:nvPicPr><p:spPr><a:xfrm><a:off x="1000000" y="1000000"/>'
            '<a:ext cx="5000000" cy="3000000"/></a:xfrm></p:spPr></p:pic>'
        )
        for index in range(1, count + 1)
    ).encode("utf-8")
    return payload.replace(marker, pictures + marker, 1)
