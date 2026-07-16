from __future__ import annotations

import json
import sqlite3

import fitz
import pytest

from db_manager import DBManager
from template_upload_service import (
    TemplateUploadService,
    TemplateUploadInProgressError,
    TemplateUploadSubmissionConflictError,
)


def _two_page_pdf() -> bytes:
    document = fitz.open()
    try:
        document.new_page(width=320, height=480)
        document.new_page(width=420, height=640)
        return document.tobytes()
    finally:
        document.close()


def _create_session_with_config(db: DBManager, root, *, questions=None) -> int:
    configured_questions = questions or [
        {"question_id": "Q1", "question_type": "subjective", "max_score": 10}
    ]
    rubric_path = root / "rubric.json"
    answer_path = root / "answer.json"
    rubric_path.write_text(
        json.dumps({"questions": configured_questions}), encoding="utf-8"
    )
    answer_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": item["question_id"]} for item in configured_questions
                ]
            }
        ),
        encoding="utf-8",
    )
    return db.create_grading_session(
        "Template Exam", str(rubric_path), str(answer_path)
    )


def test_failed_database_activation_preserves_previous_template_files(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(db, tmp_path)
    templates_dir = tmp_path / "templates"
    session_dir = templates_dir / f"session_{session_id}"
    session_dir.mkdir(parents=True)
    front = session_dir / "template_front_from_pdf_page.jpg"
    back = session_dir / "template_back_from_pdf_page.jpg"
    front.write_bytes(b"previous-front")
    back.write_bytes(b"previous-back")
    db.upsert_session_template(session_id, str(front), str(back))
    with sqlite3.connect(db.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_template_activation
            BEFORE UPDATE ON session_templates
            BEGIN
                SELECT RAISE(ABORT, 'activation rejected');
            END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="activation rejected"):
        TemplateUploadService(templates_dir).upload(
            db=db,
            session_id=session_id,
            pdf_bytes=_two_page_pdf(),
            first_page_role="front",
        )

    assert front.read_bytes() == b"previous-front"
    assert back.read_bytes() == b"previous-back"


def test_upload_activates_a_mapping_package_from_the_saved_scoring_config(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(
        db,
        tmp_path,
        questions=[
            {"question_id": "Q1", "question_type": "subjective", "max_score": 10},
            {"question_id": "Q2", "question_type": "subjective", "max_score": 20},
        ],
    )

    TemplateUploadService(tmp_path / "templates").upload(
        db=db,
        session_id=session_id,
        pdf_bytes=_two_page_pdf(),
        first_page_role="front",
    )

    template = db.get_session_template(session_id)
    assert template is not None
    for field in ("ai_analysis_path", "template_config_path", "regions_path"):
        assert template[field]
        assert (tmp_path / "templates" / f"session_{session_id}" / str(template[field])).is_file()
    config = json.loads(
        (tmp_path / "templates" / f"session_{session_id}" / template["template_config_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert [item["question_id"] for item in config["questions"]] == ["Q1", "Q2"]


def test_only_an_inactive_processing_submission_can_be_abandoned(tmp_path) -> None:
    templates_dir = tmp_path / "templates"
    active_service = TemplateUploadService(templates_dir)
    token = "a" * 32
    assert active_service.begin_submission(
        session_id=1,
        request_token=token,
        filename="sample.pdf",
        content_length=100,
        first_page_role="front",
    ) == "started"

    with pytest.raises(TemplateUploadSubmissionConflictError):
        active_service.abandon_submission(session_id=1, request_token=token)

    restarted_service = TemplateUploadService(templates_dir)
    restarted_service.abandon_submission(session_id=1, request_token=token)

    assert restarted_service.submission_public(session_id=1, request_token=token) == {
        "status": "abandoned",
        "template": None,
    }


def test_one_service_allows_only_one_processing_token_per_session(tmp_path) -> None:
    service = TemplateUploadService(tmp_path / "templates")
    first = "1" * 32
    second = "2" * 32
    assert service.begin_submission(session_id=1, request_token=first,
        filename="first.pdf", content_length=100, first_page_role="front") == "started"

    with pytest.raises(TemplateUploadInProgressError):
        service.begin_submission(session_id=1, request_token=second,
            filename="second.pdf", content_length=100, first_page_role="front")

    service.finish_submission(session_id=1, request_token=first, succeeded=False)
    assert service.begin_submission(session_id=1, request_token=second,
        filename="second.pdf", content_length=100, first_page_role="front") == "started"
