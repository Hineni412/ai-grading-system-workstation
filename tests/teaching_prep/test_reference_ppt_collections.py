from __future__ import annotations

import pytest

from backend.teaching_prep.application.reference_ppt_collections import (
    ReferencePptInput,
    infer_reference_ppt_collection,
)
from backend.teaching_prep.domain.errors import TeachingPrepValidationError

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client
from .test_a03_material_units import _pptx


def _ppt(
    index: int,
    relative_path: str,
    *,
    slides: int = 25,
    first_slide_title: str | None = None,
) -> ReferencePptInput:
    return ReferencePptInput(
        material_record_id=f"{index:032x}",
        relative_path=relative_path,
        file_name=relative_path.replace("\\", "/").split("/")[-1],
        unit_count=slides,
        first_slide_title=first_slide_title,
    )


def test_infers_tidy_chapter_section_and_lesson_tree_without_model() -> None:
    result = infer_reference_ppt_collection(
        [
            _ppt(
                1,
                "第1章 勾股定理/1.1 第1课时 认识勾股定理/"
                "1.1 第1课时 认识勾股定理.pptx",
            ),
            _ppt(
                2,
                "第1章 勾股定理/1.1 第2课时 验证勾股定理/"
                "1.1 第2课时 验证勾股定理.pptx",
            ),
            _ppt(
                3,
                "第1章 勾股定理/1.2 一定是直角三角形吗/"
                "1.2 一定是直角三角形吗.pptx",
            ),
            _ppt(4, "第1章 勾股定理/第一章 小结与复习.pptx"),
        ]
    )

    assert result["summary"] == {
        "ppt_count": 4,
        "lesson_candidate_count": 3,
        "special_count": 1,
        "high_confidence_count": 2,
        "needs_review_count": 1,
        "chapter_count": 1,
    }
    tree = result["tree"]
    assert [item["title"] for item in tree] == ["第1章 勾股定理"]
    assert [item["title"] for item in tree[0]["sections"]] == [
        "1.1",
        "1.2 一定是直角三角形吗",
    ]
    assert [
        lesson["title"] for lesson in tree[0]["sections"][0]["lessons"]
    ] == ["第1课时 认识勾股定理", "第2课时 验证勾股定理"]
    assert len(result["mappings"]) == 3
    assert result["mappings"][0]["end_unit"] == 25
    assert any(
        "默认不增加新授课时" in item for item in result["uncertainties"]
    )


@pytest.mark.parametrize(
    ("path", "expected_lesson", "expected_title"),
    [
        ("凌乱课件/1.2.1 勾股定理应用.pptx", 1, "勾股定理应用"),
        (
            "凌乱课件/1.2 勾股定理应用（第一课时）.pptx",
            1,
            "勾股定理应用",
        ),
        (
            "凌乱课件/1.2 勾股定理应用 (第 2 课时).pptx",
            2,
            "勾股定理应用",
        ),
        ("凌乱课件/2．3．3 二次根式混合运算.pptx", 3, "二次根式混合运算"),
    ],
)
def test_supports_flat_and_messy_common_numbering(
    path: str,
    expected_lesson: int,
    expected_title: str,
) -> None:
    result = infer_reference_ppt_collection([_ppt(1, path)])

    member = result["collection_members"][0]
    assert member["lesson_number"] == expected_lesson
    assert member["title"] == expected_title
    assert member["chapter_number"] in {1, 2}
    assert member["section_number"] in {2, 3}
    assert result["mappings"][0]["confidence"] == "high"


def test_keeps_uncertain_file_in_collection_without_guessing_lesson() -> None:
    result = infer_reference_ppt_collection(
        [_ppt(1, "全部课件/勾股定理引入.pptx")]
    )

    assert result["tree"] == []
    assert result["mappings"] == []
    assert result["summary"]["needs_review_count"] == 1
    assert "尚不能可靠对应课时" in result["uncertainties"][0]


def test_special_ppt_is_collected_but_does_not_create_lesson() -> None:
    result = infer_reference_ppt_collection(
        [
            _ppt(1, "全部课件/4.2 一次函数同步练习.pptx"),
            _ppt(2, "全部课件/第四章小结与复习.pptx"),
        ]
    )

    assert result["tree"] == []
    assert result["mappings"] == []
    assert result["summary"]["special_count"] == 2


def test_rejects_path_traversal_and_non_pptx() -> None:
    with pytest.raises(TeachingPrepValidationError):
        infer_reference_ppt_collection([_ppt(1, "../1.2 课件.pptx")])
    with pytest.raises(TeachingPrepValidationError):
        infer_reference_ppt_collection([_ppt(1, "1.2 课件.mp4")])
    with pytest.raises(TeachingPrepValidationError):
        infer_reference_ppt_collection([_ppt(1, "C:/课件/1.2 课件.pptx")])


def test_matches_existing_tree_without_replacing_it() -> None:
    nodes = [
        {
            "id": "c" * 32,
            "parent_id": None,
            "node_type": "chapter",
            "title": "第1章 勾股定理",
        },
        {
            "id": "s" * 32,
            "parent_id": "c" * 32,
            "node_type": "section",
            "title": "1.2 勾股定理的应用",
        },
        {
            "id": "l" * 32,
            "parent_id": "s" * 32,
            "node_type": "lesson",
            "title": "第1课时 勾股定理的应用",
        },
    ]

    result = infer_reference_ppt_collection(
        [_ppt(1, "全部课件/1.2 勾股定理应用（第一课时）.pptx")],
        existing_lessons=nodes,
    )

    assert result["tree"] == []
    assert result["mappings"][0]["lesson_ref"] == "l" * 32
    assert result["mappings"][0]["confidence"] in {"high", "medium"}


def test_collection_persists_virtual_path_and_reuses_mapping_review(
    tmp_path,
    monkeypatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    curriculum, semester, _created = service.create_semester_workspace(
        request_token="ppt-folder-semester-workspace-0001",
        title="八年级上册数学",
        grade_level=8,
        volume="first",
        publisher="北师版",
        edition_label=None,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    source = _pptx(tmp_path / "1.2 勾股定理应用（第一课时）.pptx")
    version, _created = service.register_material_file(
        request_token="ppt-folder-material-version-0001",
        path=source,
        display_name="勾股定理应用",
    )
    record, _created = service.attach_semester_material(
        semester.id,
        request_token="ppt-folder-material-record-0001",
        material_version_id=version.id,
        material_role="reference_ppt",
    )
    service.parse_material_version(version.id)

    collection, created = service.create_reference_ppt_collection(
        semester.id,
        request_token="ppt-folder-collection-create-0001",
        display_name="凌乱但可识别的课件",
        ignored_file_count=2,
        members=[
            {
                "material_record_id": record.id,
                "relative_path": "全部课件/1.2 勾股定理应用（第一课时）.pptx",
            }
        ],
    )

    assert created is True
    assert collection.display_name == "凌乱但可识别的课件"
    assert collection.ignored_file_count == 2
    assert collection.members[0].relative_path.startswith("全部课件/")
    assert collection.members[0].confidence == "high"
    proposals = service.list_semester_mapping_proposals(semester.id)
    assert proposals[0].id == collection.mapping_proposal_id
    assert proposals[0].payload["generation_source"] == (
        "local_reference_ppt_names"
    )
    assert proposals[0].payload["tree"][0]["sections"][0]["lessons"]

    reviewed = service.accept_local_reference_ppt_mappings(
        proposals[0].id,
        expected_revision=proposals[0].revision,
    )
    assert reviewed.payload["mappings"][0]["decision"] == "accepted"
    applied = service.apply_semester_mapping_proposal(
        reviewed.id,
        expected_revision=reviewed.revision,
    )

    assert applied.status == "applied"
    lessons = service.list_lesson_nodes(curriculum.id)
    assert any(item.node_type == "lesson" for item in lessons)
    assert service.list_reference_ppt_collections(semester.id)[0].id == collection.id
    response = _api_client(service).get(
        f"/api/teaching-prep/semesters/{semester.id}/reference-ppt-collections"
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["members"][0]["relative_path"] == (
        "全部课件/1.2 勾股定理应用（第一课时）.pptx"
    )


def _create_collection(tmp_path, monkeypatch):
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    _curriculum, semester, _created = service.create_semester_workspace(
        request_token="ppt-folder-semester-workspace-0002",
        title="八年级上册数学",
        grade_level=8,
        volume="first",
        publisher="北师版",
        edition_label=None,
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=48,
    )
    source = _pptx(tmp_path / "1.2 勾股定理应用（第一课时）.pptx")
    version, _created = service.register_material_file(
        request_token="ppt-folder-material-version-0002",
        path=source,
        display_name="勾股定理应用",
    )
    record, _created = service.attach_semester_material(
        semester.id,
        request_token="ppt-folder-material-record-0002",
        material_version_id=version.id,
        material_role="reference_ppt",
    )
    service.parse_material_version(version.id)
    collection, created = service.create_reference_ppt_collection(
        semester.id,
        request_token="ppt-folder-collection-create-0002",
        display_name="凌乱但可识别的课件",
        ignored_file_count=0,
        members=[
            {
                "material_record_id": record.id,
                "relative_path": "全部课件/1.2 勾股定理应用（第一课时）.pptx",
            }
        ],
    )
    assert created is True
    return service, semester, collection


def test_deactivated_collection_leaves_list_until_restored(
    tmp_path,
    monkeypatch,
) -> None:
    service, semester, collection = _create_collection(tmp_path, monkeypatch)
    client = _api_client(service)

    assert collection.is_active is True
    response = client.get(
        f"/api/teaching-prep/semesters/{semester.id}/reference-ppt-collections"
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["is_active"] is True

    deactivated = client.patch(
        f"/api/teaching-prep/reference-ppt-collections/{collection.id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert deactivated.json()["revision"] == collection.revision + 1
    assert service.list_reference_ppt_collections(semester.id) == ()
    response = client.get(
        f"/api/teaching-prep/semesters/{semester.id}/reference-ppt-collections"
    )
    assert response.status_code == 200
    assert response.json()["items"] == []

    restored = client.patch(
        f"/api/teaching-prep/reference-ppt-collections/{collection.id}",
        json={"is_active": True},
    )
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert restored.json()["revision"] == collection.revision + 2
    assert service.list_reference_ppt_collections(semester.id)[0].id == (
        collection.id
    )


def test_include_inactive_lists_deactivated_collections(
    tmp_path,
    monkeypatch,
) -> None:
    service, semester, collection = _create_collection(tmp_path, monkeypatch)
    client = _api_client(service)

    deactivated = client.patch(
        f"/api/teaching-prep/reference-ppt-collections/{collection.id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200

    response = client.get(
        f"/api/teaching-prep/semesters/{semester.id}/reference-ppt-collections"
    )
    assert response.status_code == 200
    assert response.json()["items"] == []

    response = client.get(
        f"/api/teaching-prep/semesters/{semester.id}/reference-ppt-collections"
        "?include_inactive=true"
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [collection.id]
    assert items[0]["is_active"] is False
    assert service.list_reference_ppt_collections(semester.id) == ()
    assert service.list_reference_ppt_collections(
        semester.id,
        include_inactive=True,
    )[0].id == collection.id


def test_update_unknown_collection_returns_404(
    tmp_path,
    monkeypatch,
) -> None:
    service, _semester, _collection = _create_collection(tmp_path, monkeypatch)
    client = _api_client(service)

    response = client.patch(
        f"/api/teaching-prep/reference-ppt-collections/{'f' * 32}",
        json={"is_active": False},
    )
    assert response.status_code == 404
