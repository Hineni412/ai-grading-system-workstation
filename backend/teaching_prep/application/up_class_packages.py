from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from xml.etree import ElementTree

import fitz

from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.teaching_prep.domain.models import (
    LessonDraftVersion,
    PptxVersion,
    ResourcePackVersion,
    SlidePlanVersion,
)


PACKAGE_FILES = (
    "lesson-slides.pptx",
    "class-exercise.pdf",
    "teacher-answer.pdf",
    "lesson-flow.pdf",
    "sources.json",
    "preflight.json",
    "manifest.json",
)


def build_up_class_package(
    staging_dir: Path,
    *,
    pptx_path: Path,
    pptx_version: PptxVersion,
    plan: SlidePlanVersion,
    draft: LessonDraftVersion,
    pack: ResourcePackVersion,
    resolve_region: Callable[[str], Path],
) -> tuple[Path, dict[str, object], str]:
    staging_dir.mkdir(parents=True, exist_ok=False)
    copied_pptx = staging_dir / "lesson-slides.pptx"
    shutil.copyfile(pptx_path, copied_pptx)
    selected = _selected_exercises(draft, pack)
    _exercise_pdf(
        staging_dir / "class-exercise.pdf",
        selected,
        role="question_regions",
        title="课堂练习（学生用）",
        resolve_region=resolve_region,
    )
    _exercise_pdf(
        staging_dir / "teacher-answer.pdf",
        selected,
        role="answer_regions",
        title="课堂练习答案（教师用）",
        resolve_region=resolve_region,
    )
    _flow_pdf(staging_dir / "lesson-flow.pdf", draft)
    dependency_report = inspect_pptx_dependencies(copied_pptx)
    sources = {
        "schema_version": 1,
        "lesson": pack.payload.get("lesson"),
        "classroom": pack.payload.get("classroom"),
        "versions": {
            "resource_pack_id": pack.id,
            "resource_pack_version": pack.version_number,
            "resource_pack_sha256": pack.pack_sha256,
            "lesson_draft_id": draft.id,
            "lesson_draft_version": draft.version_number,
            "slide_plan_id": plan.id,
            "slide_plan_version": plan.version_number,
            "pptx_version_id": pptx_version.id,
            "pptx_version": pptx_version.version_number,
            "pptx_sha256": pptx_version.output_sha256,
        },
        "materials": _source_summary(pack),
        "selected_exercises": [
            {
                "source_ref": item["source_ref"],
                "source_kind": item["render_kind"],
                "candidate_id": item.get("candidate_id"),
                "question_id": item.get("question_id"),
                "question_number": item.get("question_number"),
                "content_label": item.get("content_label"),
                "source_version_sha256": sorted(
                    {
                        str(region["source_version_sha256"])
                        for key in ("question_regions", "answer_regions")
                        for region in list(item.get(key) or [])
                    }
                ),
                "evidence_source_version": item.get(
                    "evidence_source_version"
                ),
            }
            for item in selected
        ],
    }
    _write_json(staging_dir / "sources.json", sources)
    preflight = {
        "schema_version": 1,
        "complete": True,
        "required_files": list(PACKAGE_FILES),
        "pptx_verification": pptx_version.verification_report,
        "selected_exercise_count": len(selected),
        "all_answers_available_and_frozen": True,
        "external_dependencies": dependency_report,
        "offline_ready": dependency_report["external_relationship_count"] == 0,
        "limitations": [
            "课件数学内容仍以教师最终确认为准",
            "外部链接仅做依赖声明，不会在打包时联网访问",
        ],
    }
    _write_json(staging_dir / "preflight.json", preflight)
    file_records = [
        _file_record(staging_dir / name)
        for name in PACKAGE_FILES
        if name != "manifest.json"
    ]
    manifest = {
        "schema_version": 1,
        "package_kind": "teaching_prep_up_class_package",
        "lesson_node_id": pack.lesson_node_id,
        "class_name": dict(pack.payload.get("classroom") or {}).get(
            "class_name"
        ),
        "files": file_records,
        "preflight": {
            "complete": True,
            "offline_ready": preflight["offline_ready"],
        },
    }
    _write_json(staging_dir / "manifest.json", manifest)
    archive = staging_dir / "candidate.zip"
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for name in PACKAGE_FILES:
            bundle.write(staging_dir / name, arcname=name)
    verify_package_archive(archive, manifest)
    return archive, manifest, _sha256(archive)


def verify_package_archive(
    archive: Path,
    expected_manifest: Mapping[str, object],
) -> None:
    if not archive.is_file():
        raise TeachingPrepValidationError("up-class package archive is missing")
    with zipfile.ZipFile(archive, "r") as bundle:
        names = tuple(bundle.namelist())
        if names != PACKAGE_FILES:
            raise TeachingPrepValidationError(
                "up-class package file list is incomplete or unexpected"
            )
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        if manifest != dict(expected_manifest):
            raise TeachingPrepValidationError(
                "up-class package manifest does not match"
            )
        records = manifest.get("files")
        if not isinstance(records, list):
            raise TeachingPrepValidationError(
                "up-class package manifest is invalid"
            )
        for raw in records:
            if not isinstance(raw, Mapping):
                raise TeachingPrepValidationError(
                    "up-class package manifest is invalid"
                )
            name = str(raw.get("name") or "")
            payload = bundle.read(name)
            if len(payload) != int(raw.get("size_bytes") or -1):
                raise TeachingPrepValidationError(
                    "up-class package file size does not match"
                )
            if hashlib.sha256(payload).hexdigest() != raw.get("sha256"):
                raise TeachingPrepValidationError(
                    "up-class package file hash does not match"
                )


def inspect_pptx_dependencies(path: Path) -> dict[str, object]:
    external_types: dict[str, int] = {}
    external_count = 0
    try:
        with zipfile.ZipFile(path, "r") as archive:
            rel_names = [
                name for name in archive.namelist() if name.endswith(".rels")
            ]
            for name in rel_names:
                root = ElementTree.fromstring(archive.read(name))
                for relation in root:
                    if relation.attrib.get("TargetMode") != "External":
                        continue
                    external_count += 1
                    relation_type = relation.attrib.get("Type", "")
                    label = relation_type.rsplit("/", 1)[-1] or "unknown"
                    external_types[label] = external_types.get(label, 0) + 1
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise TeachingPrepValidationError(
            "published PPTX cannot be inspected for offline use"
        ) from exc
    return {
        "external_relationship_count": external_count,
        "types": [
            {"type": key, "count": external_types[key]}
            for key in sorted(external_types)
        ],
        "targets_disclosed": False,
    }


def _selected_exercises(
    draft: LessonDraftVersion,
    pack: ResourcePackVersion,
) -> list[dict[str, object]]:
    exercises = {
        f"exercise:{item.get('candidate_id')}": {
            **dict(item),
            "source_ref": f"exercise:{item.get('candidate_id')}",
            "render_kind": "exercise_region",
        }
        for item in list(pack.payload.get("exercises") or [])
        if isinstance(item, Mapping)
    }
    selected: list[dict[str, object]] = []
    for raw in list(draft.payload.get("exercise_recommendations") or []):
        if not isinstance(raw, Mapping) or raw.get("action") != "include":
            continue
        source_ref = str(raw.get("source_ref") or "")
        item = exercises.get(source_ref)
        if item is None:
            raise TeachingPrepValidationError(
                "selected exercise has no frozen printable source"
            )
        if (
            not item.get("formal_answer_usable")
            or not list(item.get("question_regions") or [])
            or not list(item.get("answer_regions") or [])
        ):
            raise TeachingPrepValidationError(
                "selected exercise requires a teacher-verified printable answer"
            )
        selected.append(item)
    if not selected:
        raise TeachingPrepValidationError(
            "at least one printable exercise must be selected"
        )
    if len(selected) > 3:
        raise TeachingPrepValidationError(
            "up-class package supports at most three class exercises"
        )
    return selected


def _exercise_pdf(
    target: Path,
    items: list[dict[str, object]],
    *,
    role: str,
    title: str,
    resolve_region: Callable[[str], Path],
) -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text(
        (42, 48),
        title,
        fontname="china-s",
        fontsize=16,
        color=(0.08, 0.12, 0.18),
    )
    y = 74.0
    available = 842.0 - y - 38.0
    region_count = sum(
        (
            len(list(item.get(role) or []))
        )
        for item in items
    )
    if region_count <= 0:
        document.close()
        raise TeachingPrepValidationError("exercise package image is missing")
    slot_height = available / region_count
    if slot_height < 90:
        document.close()
        raise TeachingPrepValidationError(
            "selected exercises do not fit the one-page printable layout"
        )
    for item_index, item in enumerate(items, start=1):
        label = (
            item.get("question_number")
            or item.get("content_label")
            or f"第 {item_index} 题"
        )
        page.insert_text(
            (42, y),
            f"{item_index}. {label}",
            fontname="china-s",
            fontsize=10,
        )
        y += 8
        for raw_region in list(item.get(role) or []):
            if not isinstance(raw_region, Mapping):
                continue
            image_path = resolve_region(str(raw_region.get("region_id") or ""))
            rectangle = fitz.Rect(42, y, 553, y + max(32, slot_height - 16))
            page.insert_image(
                rectangle,
                filename=str(image_path),
                keep_proportion=True,
            )
            y += slot_height
    document.save(target, garbage=4, deflate=True)
    document.close()


def _flow_pdf(target: Path, draft: LessonDraftVersion) -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text(
        (42, 48),
        "课堂流程与时间清单",
        fontname="china-s",
        fontsize=16,
    )
    y = 82.0
    breakdown = list(draft.capacity.get("flow_breakdown") or [])
    for index, raw in enumerate(breakdown, start=1):
        if not isinstance(raw, Mapping):
            continue
        label = str(raw.get("label") or raw.get("phase") or "课堂环节")
        minutes = int(raw.get("planned_minutes") or 0)
        page.insert_text(
            (48, y),
            f"□ {index}. {label}（{minutes} 分钟）",
            fontname="china-s",
            fontsize=11,
        )
        y += 30
    for label, key in (
        ("课堂练习", "exercise_minutes"),
        ("机动时间", "buffer_minutes"),
        ("合计", "planned_minutes"),
    ):
        page.insert_text(
            (48, y),
            f"{label}：{int(draft.capacity.get(key) or 0)} 分钟",
            fontname="china-s",
            fontsize=10,
        )
        y += 24
    page.insert_text(
        (48, y + 12),
        "课后快速复盘：□ 超时  □ 提前  □ 难度合适  □ 需要重讲",
        fontname="china-s",
        fontsize=10,
    )
    document.save(target, garbage=4, deflate=True)
    document.close()


def _source_summary(pack: ResourcePackVersion) -> list[dict[str, object]]:
    result = []
    for raw in list(pack.payload.get("materials") or []):
        if not isinstance(raw, Mapping):
            continue
        result.append(
            {
                "link_id": raw.get("link_id"),
                "material_name": raw.get("material_name"),
                "material_type": raw.get("material_type"),
                "purpose": raw.get("purpose"),
                "source_version_sha256": raw.get("source_version_sha256"),
                "units": [
                    item.get("unit_index")
                    for item in list(raw.get("units") or [])
                    if isinstance(item, Mapping)
                ],
            }
        )
    return result


def _file_record(path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "PACKAGE_FILES",
    "build_up_class_package",
    "inspect_pptx_dependencies",
    "verify_package_archive",
]
