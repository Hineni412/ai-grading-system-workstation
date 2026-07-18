from __future__ import annotations

import json

import pytest

from question_bank.services.assembly_workspace_service import (
    AssemblyDraftConflict,
    AssemblyRecordCreate,
    AssemblyRecordFileForbidden,
    AssemblyWorkspaceService,
)


def test_workspace_loads_legacy_draft_and_rejects_stale_overwrite(tmp_path) -> None:
    data_root = tmp_path / "data"
    draft_path = data_root / "question_bank" / "assembly_draft.json"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text(
        json.dumps(
            {
                "basket_ids": [11, "11", 12, "bad", 13],
                "order_ids": [13, 11, 999],
                "sections": [
                    {"title": "选择题", "question_ids": [11, 11, 999]},
                    {"title": "解答题", "question_ids": [13]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = AssemblyWorkspaceService(data_root)

    loaded = service.load_draft()

    assert loaded.basket_ids == (11, 12, 13)
    assert loaded.order_ids == (11, 13, 12)
    assert [(section.title, section.question_ids) for section in loaded.sections] == [
        ("选择题", (11,)),
        ("解答题", (13,)),
        ("未分节", (12,)),
    ]

    saved = service.save_draft(
        expected_revision=loaded.revision,
        draft={
            **loaded.to_payload(),
            "title": "七年级单元练习",
            "order_ids": [12, 13, 11],
            "sections": [
                {"id": "section-a", "title": "填空题", "question_ids": [12]},
                {"id": "section-b", "title": "解答题", "question_ids": [13, 11]},
            ],
        },
    )
    assert saved.title == "七年级单元练习"
    assert saved.order_ids == (12, 13, 11)
    assert service.load_draft() == saved

    with pytest.raises(AssemblyDraftConflict):
        service.save_draft(
            expected_revision=loaded.revision,
            draft=loaded.to_payload(),
        )


def test_workspace_adds_questions_and_keeps_record_files_private(tmp_path) -> None:
    data_root = tmp_path / "data"
    service = AssemblyWorkspaceService(data_root)
    empty = service.load_draft()

    added = service.add_questions(
        expected_revision=empty.revision,
        question_ids=[7, "7", 8],
    )
    assert added.basket_ids == (7, 8)
    assert added.order_ids == (7, 8)

    output_path = data_root / "question_bank" / "assembly_exports" / "练习卷.docx"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"docx")
    record = service.create_record(
        AssemblyRecordCreate(
            title="练习卷",
            draft=added,
            output_path=output_path,
            export_format="docx",
            question_type_summary={"选择题": 2},
        )
    )

    public = record.to_public_payload()
    assert "output_path" not in public
    assert public["filename"] == "练习卷.docx"
    assert service.list_records() == [record]
    assert service.resolve_record_file(record.id).path == output_path.resolve()

    outside_path = tmp_path / "outside.docx"
    outside_path.write_bytes(b"private")
    outside_record = service.create_record(
        AssemblyRecordCreate(
            title="越界",
            draft=added,
            output_path=outside_path,
            export_format="docx",
        )
    )
    with pytest.raises(AssemblyRecordFileForbidden):
        service.resolve_record_file(outside_record.id)

    assert service.delete_record(record.id) is True
    assert output_path.exists()


def test_workspace_preserves_empty_named_sections(tmp_path) -> None:
    data_root = tmp_path / "data"
    service = AssemblyWorkspaceService(data_root)
    empty = service.load_draft()
    added = service.add_questions(
        expected_revision=empty.revision,
        question_ids=[17, 18],
    )

    saved = service.save_draft(
        expected_revision=added.revision,
        draft={
            **added.to_payload(),
            "layout_mode": "sections",
            "sections": [
                {"id": "section-a", "title": "第一部分", "question_ids": []},
            ],
        },
    )

    assert [(section.title, section.question_ids) for section in saved.sections] == [
        ("第一部分", ()),
        ("未分节", (17, 18)),
    ]
    assert saved.layout_mode == "sections"
