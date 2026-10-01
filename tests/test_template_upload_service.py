from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3

import fitz
import pytest

from db_manager import DBManager
from template_upload_service import (
    TemplateUploadService,
    TemplateUploadInProgressError,
    TemplateUploadSubmissionConflictError,
)

TEST_CONTENT_SHA256 = "0" * 64
from backend.repositories.grading_database import open_grading_repositories


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
                    {"question_id": item["question_id"]}
                    for item in configured_questions
                ]
            }
        ),
        encoding="utf-8",
    )
    return db.sessions.create_grading_session(
        "Template Exam", str(rubric_path), str(answer_path)
    )


def test_failed_database_activation_preserves_previous_template_files(tmp_path) -> None:
    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(db, tmp_path)
    templates_dir = tmp_path / "templates"
    session_dir = templates_dir / f"session_{session_id}"
    session_dir.mkdir(parents=True)
    front = session_dir / "template_front_from_pdf_page.jpg"
    back = session_dir / "template_back_from_pdf_page.jpg"
    front.write_bytes(b"previous-front")
    back.write_bytes(b"previous-back")
    db.templates.upsert_session_template(session_id, str(front), str(back))
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


def test_template_upload_retains_two_pages_without_full_class_pdf(tmp_path) -> None:
    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(db, tmp_path)
    service = TemplateUploadService(tmp_path / "templates")
    uploaded = service.upload(db=db, session_id=session_id, pdf_bytes=_two_page_pdf(), first_page_role="front")
    assert uploaded.template_fingerprint == service.load_current(db=db, session_id=session_id).template_fingerprint
    assert len(list((tmp_path / "templates").rglob("*.jpg"))) >= 2
    assert not list((tmp_path / "templates").rglob("template_source_full_class.pdf"))
