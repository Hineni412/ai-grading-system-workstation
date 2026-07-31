from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _lesson_tree
from .test_a03_material_units import _pdf, _register


def _region(unit_id: str, x0: float, y0: float, x1: float, y1: float):
    return {
        "material_unit_id": unit_id,
        "crop": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
    }


def _exercise_setup(
    service: TeachingPrepService,
    tmp_path: Path,
):
    _curriculum_id, _chapter_id, _section_id, lesson_ids = _lesson_tree(
        service
    )
    lesson_id = lesson_ids[0]
    exercise_version = _register(
        service,
        _pdf(
            tmp_path / "synthetic-double-column.pdf",
            [
                "Question 1 left column    Question 2 right column",
                "Question 3 continues with diagram",
            ],
        ),
        token="a04-exercise-material",
        name="合成双栏教辅",
    )
    answer_version = _register(
        service,
        _pdf(tmp_path / "synthetic-answers.pdf", ["Answers 1 2 3"]),
        token="a04-answer-material",
        name="合成答案册",
    )
    exercise_units = service.parse_material_version(exercise_version.id)
    answer_units = service.parse_material_version(answer_version.id)
    service.create_material_link(
        request_token="a04-exercise-link",
        lesson_node_id=lesson_id,
        material_version_id=exercise_version.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="exercise",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    service.create_material_link(
        request_token="a04-answer-link",
        lesson_node_id=lesson_id,
        material_version_id=answer_version.id,
        start_unit=1,
        end_unit=1,
        crop=None,
        purpose="answer",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    return lesson_id, exercise_version, exercise_units, answer_units


def _create_candidate(
    service: TeachingPrepService,
    *,
    token: str,
    lesson_id: str,
    question_number: str,
    content_label: str,
    question_regions: list[dict[str, object]],
    answer_regions: list[dict[str, object]] | None = None,
    answer_status: str | None = None,
):
    answers = answer_regions or []
    return service.create_exercise_candidate(
        request_token=token,
        lesson_node_id=lesson_id,
        question_number=question_number,
        content_label=content_label,
        difficulty="medium",
        classroom_use="guided_practice",
        estimated_minutes=5,
        teaching_focus="合成教学重点",
        teacher_note=None,
        selection_status="classroom_candidate",
        answer_status=answer_status or ("candidate" if answers else "missing"),
        question_regions=question_regions,
        answer_regions=answers,
    )


def _update_payload(candidate, **overrides):
    payload = {
        "expected_revision": candidate.revision,
        "question_number": candidate.question_number,
        "content_label": candidate.content_label,
        "difficulty": candidate.difficulty,
        "classroom_use": candidate.classroom_use,
        "estimated_minutes": candidate.estimated_minutes,
        "teaching_focus": candidate.teaching_focus,
        "teacher_note": candidate.teacher_note,
        "selection_status": candidate.selection_status,
        "answer_status": candidate.answer_status,
        "question_regions": [
            _region(
                region.material_unit_id,
                region.crop["x0"],
                region.crop["y0"],
                region.crop["x1"],
                region.crop["y1"],
            )
            for region in candidate.question_regions
        ],
        "answer_regions": [
            _region(
                region.material_unit_id,
                region.crop["x0"],
                region.crop["y0"],
                region.crop["x1"],
                region.crop["y1"],
            )
            for region in candidate.answer_regions
        ],
        "is_active": candidate.is_active,
    }
    payload.update(overrides)
    return payload


def test_double_column_page_keeps_two_candidates_and_crop_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _version, exercise_units, _answer_units = _exercise_setup(
        service,
        tmp_path,
    )

    first, first_created = _create_candidate(
        service,
        token="a04-candidate-left",
        lesson_id=lesson_id,
        question_number="1",
        content_label="左栏一次方程",
        question_regions=[_region(exercise_units[0].id, 0.02, 0.05, 0.49, 0.45)],
    )
    repeated, repeated_created = _create_candidate(
        service,
        token="a04-candidate-left",
        lesson_id=lesson_id,
        question_number="1",
        content_label="左栏一次方程",
        question_regions=[_region(exercise_units[0].id, 0.02, 0.05, 0.49, 0.45)],
    )
    second, second_created = _create_candidate(
        service,
        token="a04-candidate-right",
        lesson_id=lesson_id,
        question_number="2",
        content_label="右栏几何图形",
        question_regions=[_region(exercise_units[0].id, 0.51, 0.05, 0.98, 0.45)],
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert second_created is True
    assert len(service.list_exercise_candidates(lesson_id)) == 2
    assert first.question_regions[0].crop["x1"] == 0.49
    preview = service.exercise_region_preview_path(
        second.question_regions[0].id
    )
    assert preview.read_bytes().startswith(b"\x89PNG")
    assert str(tmp_path) not in second.question_regions[0].preview_url


def test_cross_page_regions_are_ordered_and_saved_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _version, exercise_units, _answer_units = _exercise_setup(
        service,
        tmp_path,
    )

    candidate, _created = _create_candidate(
        service,
        token="a04-cross-page",
        lesson_id=lesson_id,
        question_number="3",
        content_label="跨页图文题",
        question_regions=[
            _region(exercise_units[0].id, 0.05, 0.72, 0.95, 0.98),
            _region(exercise_units[1].id, 0.05, 0.02, 0.95, 0.55),
        ],
    )

    assert [item.sequence for item in candidate.question_regions] == [1, 2]
    assert [item.unit_index for item in candidate.question_regions] == [1, 2]

    unlinked_version = _register(
        service,
        _pdf(tmp_path / "unlinked.pdf", ["Unlinked question"]),
        token="a04-unlinked-material",
        name="未关联合成资料",
    )
    unlinked_unit = service.parse_material_version(unlinked_version.id)[0]
    with pytest.raises(TeachingPrepValidationError):
        _create_candidate(
            service,
            token="a04-invalid-region",
            lesson_id=lesson_id,
            question_number="4",
            content_label="不应保存",
            question_regions=[
                _region(exercise_units[0].id, 0.1, 0.1, 0.8, 0.8),
                _region(unlinked_unit.id, 0.1, 0.1, 0.8, 0.8),
            ],
        )
    assert len(service.list_exercise_candidates(lesson_id)) == 1


def test_correcting_answer_region_invalidates_then_allows_reverification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _version, exercise_units, answer_units = _exercise_setup(
        service,
        tmp_path,
    )
    candidate, _created = _create_candidate(
        service,
        token="a04-verified-answer",
        lesson_id=lesson_id,
        question_number="1",
        content_label="教师已核对答案",
        question_regions=[_region(exercise_units[0].id, 0.02, 0.05, 0.49, 0.45)],
        answer_regions=[
            _region(answer_units[0].id, 0.02, 0.05, 0.49, 0.30),
            _region(answer_units[0].id, 0.52, 0.05, 0.98, 0.30),
        ],
        answer_status="teacher_verified",
    )

    corrected = service.update_exercise_candidate(
        candidate.id,
        **_update_payload(
            candidate,
            answer_status="teacher_verified",
            answer_regions=[
                _region(answer_units[0].id, 0.05, 0.35, 0.48, 0.65),
                _region(answer_units[0].id, 0.52, 0.05, 0.98, 0.30),
            ],
        ),
    )
    assert corrected.answer_status == "candidate"

    verified = service.update_exercise_candidate(
        corrected.id,
        **_update_payload(corrected, answer_status="teacher_verified"),
    )
    assert verified.answer_status == "teacher_verified"

    metadata_only = service.update_exercise_candidate(
        verified.id,
        **_update_payload(
            verified,
            teaching_focus="只修改教学重点，不改变原图",
        ),
    )
    assert metadata_only.answer_status == "teacher_verified"

    with pytest.raises(TeachingPrepConflictError):
        service.update_exercise_candidate(
            metadata_only.id,
            **_update_payload(
                candidate,
                selection_status="excluded",
            ),
        )


def test_duplicate_hints_are_explained_without_automatic_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, _version, exercise_units, _answer_units = _exercise_setup(
        service,
        tmp_path,
    )
    first, _created = _create_candidate(
        service,
        token="a04-duplicate-one",
        lesson_id=lesson_id,
        question_number="5",
        content_label="同一道合成题线索",
        question_regions=[_region(exercise_units[0].id, 0.05, 0.50, 0.45, 0.80)],
    )
    second, _created = _create_candidate(
        service,
        token="a04-duplicate-two",
        lesson_id=lesson_id,
        question_number="5",
        content_label="同一道合成题线索",
        question_regions=[_region(exercise_units[1].id, 0.05, 0.05, 0.95, 0.55)],
    )

    items = service.list_exercise_candidates(lesson_id)

    assert len(items) == 2
    first_hint = next(item for item in items if item.id == first.id)
    assert first_hint.duplicate_suggestions[0].candidate_id == second.id
    assert set(first_hint.duplicate_suggestions[0].basis) == {
        "same_teacher_clue",
        "same_question_number_and_source",
    }


def test_api_keeps_paths_private_and_source_references_restrict_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, version, exercise_units, _answer_units = _exercise_setup(
        service,
        tmp_path,
    )
    candidate, _created = _create_candidate(
        service,
        token="a04-api-candidate",
        lesson_id=lesson_id,
        question_number="6",
        content_label="接口合成题",
        question_regions=[_region(exercise_units[0].id, 0.1, 0.1, 0.9, 0.9)],
    )
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)

    response = client.get(
        f"/api/teaching-prep/lessons/{lesson_id}/exercise-candidates"
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == candidate.id
    assert str(tmp_path) not in response.text
    with sqlite3.connect(service.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM material_versions WHERE id = ?",
                (version.id,),
            )
