from __future__ import annotations

from pathlib import Path

import pytest

from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.domain.errors import TeachingPrepConflictError

from .test_a01_foundation import _migrated_service


def _lesson_tree(
    service: TeachingPrepService,
) -> tuple[str, str, str, list[str]]:
    curriculum, _created = service.create_curriculum(
        request_token="curriculum-0001",
        title="合成七年级下册",
        grade_level=7,
        volume="second",
        publisher="合成出版社",
    )
    chapter, _created = service.create_lesson_node(
        request_token="lesson-chapter-0001",
        curriculum_id=curriculum.id,
        parent_id=None,
        node_type="chapter",
        title="第八章 二元一次方程组",
    )
    section, _created = service.create_lesson_node(
        request_token="lesson-section-0001",
        curriculum_id=curriculum.id,
        parent_id=chapter.id,
        node_type="section",
        title="消元",
    )
    lessons = []
    for index, title in enumerate(
        ("代入消元", "加减消元", "实际问题"),
        start=1,
    ):
        node, _created = service.create_lesson_node(
            request_token=f"lesson-node-000{index}",
            curriculum_id=curriculum.id,
            parent_id=section.id,
            node_type="lesson",
            title=title,
            duration_minutes=45,
        )
        lessons.append(node.id)
    return curriculum.id, chapter.id, section.id, lessons


def test_teacher_can_create_section_with_multiple_ordered_lessons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    curriculum_id, chapter_id, section_id, lesson_ids = _lesson_tree(service)

    nodes = service.list_lesson_nodes(curriculum_id)

    assert [node.node_type for node in nodes] == [
        "chapter",
        "section",
        "lesson",
        "lesson",
        "lesson",
    ]
    assert nodes[0].id == chapter_id
    assert nodes[1].id == section_id
    assert [node.id for node in nodes[2:]] == lesson_ids


def test_reorder_and_edit_use_revision_conflict_protection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    curriculum_id, _chapter_id, section_id, lesson_ids = _lesson_tree(service)
    lessons = [
        item
        for item in service.list_lesson_nodes(curriculum_id)
        if item.parent_id == section_id
    ]

    reordered = service.reorder_lesson_nodes(
        curriculum_id=curriculum_id,
        parent_id=section_id,
        ordered_ids=list(reversed(lesson_ids)),
        expected_revisions={item.id: item.revision for item in lessons},
    )

    assert [item.id for item in reordered] == list(reversed(lesson_ids))
    with pytest.raises(TeachingPrepConflictError):
        service.reorder_lesson_nodes(
            curriculum_id=curriculum_id,
            parent_id=section_id,
            ordered_ids=lesson_ids,
            expected_revisions={item.id: item.revision for item in lessons},
        )
    current = reordered[0]
    inactive = service.update_lesson_node(
        current.id,
        expected_revision=current.revision,
        title=current.title,
        duration_minutes=40,
        is_active=False,
    )
    assert inactive.is_active is False
    assert inactive.duration_minutes == 40


def test_same_fingerprint_does_not_duplicate_material_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    material = tmp_path / "synthetic-textbook.pdf"
    material.write_bytes(b"%PDF-1.7 synthetic")

    first, first_created = service.register_material_file(
        request_token="material-0001",
        path=material,
        display_name="合成教材",
        unit_count=12,
        inspection_status="ready",
    )
    repeated, repeated_created = service.register_material_file(
        request_token="material-0002",
        path=material,
        display_name="同一文件再次选择",
        unit_count=12,
        inspection_status="ready",
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert len(service.list_material_versions()) == 1


def test_missing_material_requires_explicit_relocation_and_preserves_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    original = tmp_path / "synthetic-reference.pptx"
    original.write_bytes(b"synthetic-pptx-v1")
    first, _created = service.register_material_file(
        request_token="material-1001",
        path=original,
        display_name="合成参考课件",
    )
    moved = tmp_path / "moved-reference.pptx"
    original.rename(moved)

    missing = service.refresh_material_availability(first.id)
    relocated, created = service.relocate_material(
        first.id,
        request_token="material-1002",
        path=moved,
    )

    assert missing.availability == "needs_relocation"
    assert created is False
    assert relocated.id == first.id
    assert relocated.availability == "available"

    replacement = tmp_path / "replacement-reference.pptx"
    replacement.write_bytes(b"synthetic-pptx-v2")
    newer, created = service.relocate_material(
        first.id,
        request_token="material-1003",
        path=replacement,
    )

    assert created is True
    assert newer.id != first.id
    assert newer.source_id == first.source_id
    versions = service.list_material_versions(search="合成参考")
    assert {item.id for item in versions} == {first.id, newer.id}
