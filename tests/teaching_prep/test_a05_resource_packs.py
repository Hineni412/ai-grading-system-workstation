from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.evidence import (
    ReadOnlyAssessmentEvidenceReader,
    ReadOnlyQuestionEvidenceReader,
)
from backend.teaching_prep.infrastructure.fakes import (
    FakeAssessmentEvidenceReader,
    FakeQuestionEvidenceReader,
)

from .test_a01_foundation import _migrated_service
from .test_a03_material_units import _pptx, _register
from .test_a04_exercise_candidates import (
    _create_candidate,
    _exercise_setup,
    _region,
    _update_payload,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_fakes(class_name: str = "合成七年级一班"):
    question = FakeQuestionEvidenceReader(
        {
            "kind": "question",
            "availability": "ready",
            "coverage": 1.0,
            "items": [
                {
                    "question_id": 101,
                    "question_number": "1",
                    "question_text": "合成题目",
                    "answer_text": "合成答案",
                    "knowledge_points": ["一元一次方程"],
                }
            ],
            "missing": [],
            "source_version": "synthetic-question-v1",
        }
    )
    assessment = FakeAssessmentEvidenceReader(
        {
            "kind": "assessment",
            "availability": "ready",
            "class_name": class_name,
            "coverage": 1.0,
            "student_count": 30,
            "question_count": 1,
            "assessments": [
                {
                    "assessment_id": 7,
                    "title": "合成月考",
                    "assessment_date": "2026-07-01",
                }
            ],
            "knowledge_summary": [
                {
                    "knowledge_point": "一元一次方程",
                    "question_count": 1,
                    "observed_error_rate": 0.25,
                }
            ],
            "missing": [],
            "source_version": "synthetic-assessment-v1",
        }
    )
    return question, assessment


def _freeze_ready_setup(service, tmp_path: Path):
    lesson_id, exercise_version, exercise_units, answer_units = (
        _exercise_setup(service, tmp_path)
    )
    service.create_material_link(
        request_token="a05-textbook-link",
        lesson_node_id=lesson_id,
        material_version_id=exercise_version.id,
        start_unit=1,
        end_unit=1,
        crop=None,
        purpose="textbook",
        teacher_note="合成教材范围",
        confirmation_status="confirmed",
    )
    reference = _register(
        service,
        _pptx(tmp_path / "a05-reference.pptx"),
        token="a05-reference-material",
        name="合成参考课件",
    )
    service.parse_material_version(reference.id)
    reference_link, _created = service.create_material_link(
        request_token="a05-reference-link",
        lesson_node_id=lesson_id,
        material_version_id=reference.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="reference_ppt",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    candidate, _created = _create_candidate(
        service,
        token="a05-verified-exercise",
        lesson_id=lesson_id,
        question_number="1",
        content_label="资源包合成题",
        question_regions=[
            _region(exercise_units[0].id, 0.05, 0.05, 0.95, 0.45)
        ],
        answer_regions=[
            _region(answer_units[0].id, 0.05, 0.05, 0.95, 0.35)
        ],
        answer_status="teacher_verified",
    )
    return lesson_id, reference_link, candidate


def _freeze(
    service,
    *,
    token: str,
    lesson_id: str,
    reference_link_id: str,
    class_name: str = "合成七年级一班",
):
    return service.freeze_resource_pack(
        request_token=token,
        lesson_node_id=lesson_id,
        class_name=class_name,
        lesson_type="new_lesson",
        teacher_context="仅包含班级整体的合成说明",
        reference_ppt_intents={reference_link_id: "keep"},
        question_ids=[101],
        assessment_ids=[7],
        knowledge_scope=["一元一次方程"],
    )


def test_freeze_is_complete_idempotent_and_old_version_is_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes()
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )

    first, created = _freeze(
        service,
        token="a05-freeze-pack-one",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    repeated, repeated_created = _freeze(
        service,
        token="a05-freeze-pack-one",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id
    assert len(question_reader.calls) == 1
    assert len(assessment_reader.calls) == 1
    assert first.version_number == 1
    assert first.payload["lesson"]["lesson_node_id"] == lesson_id
    assert first.payload["materials"][0]["content_sha256"]
    assert next(
        item
        for item in first.payload["materials"]
        if item["purpose"] == "reference_ppt"
    )["teacher_intent"] == "keep"
    exercise = first.payload["exercises"][0]
    assert exercise["formal_answer_usable"] is True
    assert first.payload["evidence"]["assessment"]["class_name"] == (
        "合成七年级一班"
    )
    frozen_payload = json.dumps(first.payload, sort_keys=True)
    cached_preview = service.material_preview_path(
        candidate.question_regions[0].material_unit_id
    )
    cached_preview.unlink()
    service.material_preview_path(
        candidate.question_regions[0].material_unit_id
    )
    assert service.resource_pack_status(lesson_id)[
        "local_sources_changed"
    ] is False
    unit_id = candidate.question_regions[0].material_unit_id
    with service.database.connect(immediate=True) as connection:
        row = connection.execute(
            "SELECT object_summary_json FROM material_units WHERE id = ?",
            (unit_id,),
        ).fetchone()
        summary = json.loads(str(row["object_summary_json"] or "{}"))
        summary["preview_render_status"] = "completed"
        summary["preview_notice"] = "derived preview changed"
        summary["rendered_source_sha256"] = "a" * 64
        summary["width"] = 1280
        summary["height"] = 720
        connection.execute(
            """
            UPDATE material_units
            SET object_summary_json = ?, preview_sha256 = ?
            WHERE id = ?
            """,
            (json.dumps(summary), "f" * 64, unit_id),
        )
    assert service.resource_pack_status(lesson_id)[
        "local_sources_changed"
    ] is False

    changed = service.update_exercise_candidate(
        candidate.id,
        **_update_payload(
            candidate,
            content_label="资源包合成题（教师已修正）",
        ),
    )
    assert changed.revision > candidate.revision
    status = service.resource_pack_status(lesson_id)
    assert status["local_sources_changed"] is True

    second, second_created = _freeze(
        service,
        token="a05-freeze-pack-two",
        lesson_id=lesson_id,
        reference_link_id=reference_link.id,
    )
    assert second_created is True
    assert second.version_number == 2
    assert second.pack_sha256 != first.pack_sha256
    assert json.dumps(
        service.get_resource_pack(first.id).payload,
        sort_keys=True,
    ) == frozen_payload


def test_missing_evidence_can_freeze_and_reference_intent_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )

    with pytest.raises(TeachingPrepValidationError):
        service.freeze_resource_pack(
            request_token="a05-missing-intent",
            lesson_node_id=lesson_id,
            class_name=None,
            lesson_type="new_lesson",
            teacher_context=None,
            reference_ppt_intents={},
            question_ids=[],
            assessment_ids=[],
            knowledge_scope=[],
        )

    pack, created = service.freeze_resource_pack(
        request_token="a05-no-evidence",
        lesson_node_id=lesson_id,
        class_name=None,
        lesson_type="new_lesson",
        teacher_context=None,
        reference_ppt_intents={reference_link.id: "candidate_delete"},
        question_ids=[],
        assessment_ids=[],
        knowledge_scope=[],
    )

    assert created is True
    assert pack.payload["evidence"]["question"]["availability"] == (
        "not_selected"
    )
    assert pack.payload["evidence"]["assessment"]["availability"] == (
        "not_selected"
    )
    codes = {
        item["code"]
        for item in pack.payload["missing_and_uncertain"]
    }
    assert "no_selected_questions" in codes
    assert "no_selected_assessments" in codes


def test_assessment_class_boundary_rejects_mixed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    question_reader, assessment_reader = _evidence_fakes(
        "合成七年级一班"
    )
    service.question_evidence_reader = question_reader
    service.assessment_evidence_reader = assessment_reader
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )

    with pytest.raises(TeachingPrepConflictError):
        _freeze(
            service,
            token="a05-wrong-class",
            lesson_id=lesson_id,
            reference_link_id=reference_link.id,
            class_name="合成七年级二班",
        )
    assert service.list_resource_packs(lesson_id) == ()
    assert assessment_reader.calls[0]["class_name"] == "合成七年级二班"


def test_live_evidence_readers_are_read_only_aggregate_and_remove_pii(
    tmp_path: Path,
) -> None:
    grading_db = tmp_path / "grading.db"
    question_db = tmp_path / "questions.db"
    with sqlite3.connect(grading_db) as connection:
        connection.executescript(
            """
            CREATE TABLE grading_sessions (
                id INTEGER PRIMARY KEY,
                session_name TEXT,
                status TEXT,
                is_deleted INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                student_code TEXT,
                name TEXT,
                class_name TEXT
            );
            CREATE TABLE session_results (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                student_id INTEGER,
                graded_at TEXT
            );
            CREATE TABLE session_details (
                id INTEGER PRIMARY KEY,
                result_id INTEGER,
                question_id TEXT,
                score_awarded REAL,
                deduction_reason TEXT,
                error_category TEXT
            );
            INSERT INTO grading_sessions VALUES (
                7, '合成月考', 'completed', 0,
                '2026-07-01', '2026-07-02'
            );
            INSERT INTO students VALUES
                (1, 'S001', '合成学生甲', '合成七年级一班'),
                (2, 'S002', '合成学生乙', '合成七年级二班');
            INSERT INTO session_results VALUES
                (11, 7, 1, '2026-07-03'),
                (12, 7, 2, '2026-07-03');
            INSERT INTO session_details VALUES
                (21, 11, '1', 2.0, NULL, NULL),
                (22, 12, '1', 0.0, '合成扣分', '计算错误');
            """
        )
    with sqlite3.connect(question_db) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                question_number TEXT,
                question_type TEXT,
                question_text TEXT,
                answer_text TEXT,
                difficulty TEXT,
                needs_review INTEGER,
                is_deleted INTEGER,
                updated_at TEXT
            );
            CREATE TABLE question_tags (
                id INTEGER PRIMARY KEY,
                question_id INTEGER,
                tag_type TEXT,
                tag_value TEXT
            );
            CREATE TABLE grading_question_links (
                id INTEGER PRIMARY KEY,
                grading_session_id TEXT,
                source_question_id TEXT,
                bank_question_id INTEGER,
                status TEXT
            );
            INSERT INTO questions VALUES (
                101, '1', '计算题', 'x+1=2', 'x=1',
                'easy', 0, 0, '2026-07-02'
            );
            INSERT INTO question_tags VALUES (
                1, 101, 'knowledge_point', '一元一次方程'
            );
            INSERT INTO grading_question_links VALUES (
                1, '7', '1', 101, 'confirmed'
            );
            """
        )
    grading_before = _sha256(grading_db)
    question_before = _sha256(question_db)
    assessment_reader = ReadOnlyAssessmentEvidenceReader(
        grading_db,
        question_db,
    )
    question_reader = ReadOnlyQuestionEvidenceReader(question_db)

    choices = assessment_reader.list_assessments()
    question_choices = question_reader.list_questions()
    assessment = assessment_reader.read(
        {
            "assessment_ids": [7],
            "class_name": "合成七年级一班",
            "knowledge_scope": ["一元一次方程"],
        }
    )
    questions = question_reader.read({"question_ids": [101]})

    assert choices[0]["classes"][0]["class_name"] == "合成七年级一班"
    assert question_choices[0]["question_number"] == "1"
    assert "source_file" not in question_choices[0]
    assert assessment["student_count"] == 1
    assert assessment["coverage"] == 1.0
    assert assessment["knowledge_summary"][0]["observed_error_rate"] == 0.0
    assert questions["items"][0]["question_text"] == "x+1=2"
    serialized = json.dumps(assessment, ensure_ascii=False)
    assert "合成学生甲" not in serialized
    assert "S001" not in serialized
    assert grading_before == _sha256(grading_db)
    assert question_before == _sha256(question_db)


def test_manifest_export_contains_no_absolute_paths_or_personal_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, service = _migrated_service(tmp_path, monkeypatch)
    lesson_id, reference_link, _candidate = _freeze_ready_setup(
        service,
        tmp_path,
    )
    pack, _created = service.freeze_resource_pack(
        request_token="a05-safe-manifest",
        lesson_node_id=lesson_id,
        class_name=None,
        lesson_type="new_lesson",
        teacher_context=None,
        reference_ppt_intents={reference_link.id: "keep"},
        question_ids=[],
        assessment_ids=[],
        knowledge_scope=[],
    )
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    response = TestClient(app).get(
        f"/api/teaching-prep/resource-packs/{pack.id}/manifest"
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert str(tmp_path) not in response.text
    assert "student_name" not in response.text
    assert "student_code" not in response.text
    assert "front_image" not in response.text
