from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3

import fitz
import pytest

from backend.repositories.db_manager import DBManager
from backend.exam_intake.template_upload_service import (
    TemplateUploadService,
    TemplateUploadInProgressError,
)

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


def test_template_fingerprint_changes_on_reupload_and_page_swap(tmp_path) -> None:
    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(db, tmp_path)
    service = TemplateUploadService(tmp_path / "templates")
    first = service.upload(
        db=db, session_id=session_id,
        pdf_bytes=_two_page_pdf(), first_page_role="front",
    )
    # Re-uploading the same PDF writes a new version directory, so the
    # identity changes even though the rendered images are identical.
    second = service.upload(
        db=db, session_id=session_id,
        pdf_bytes=_two_page_pdf(), first_page_role="front",
    )
    assert second.template_fingerprint != first.template_fingerprint

    swapped = service.assign_first_page_role(
        db=db,
        session_id=session_id,
        first_page_role="back",
        expected_template_fingerprint=second.template_fingerprint,
    )
    assert swapped.changed is True
    assert swapped.template.first_page_role == "back"
    assert swapped.template.template_fingerprint != second.template_fingerprint


def test_legacy_content_hash_manifest_still_supports_page_reassignment(tmp_path) -> None:
    from backend.answer_regions.answer_region_draft_service import (
        legacy_template_content_hash,
    )

    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    session_id = _create_session_with_config(db, tmp_path)
    service = TemplateUploadService(tmp_path / "templates")
    uploaded = service.upload(
        db=db, session_id=session_id,
        pdf_bytes=_two_page_pdf(), first_page_role="front",
    )
    # Simulate a manifest written before the fingerprint switched from image
    # contents to stored path strings.
    manifest_path = (
        uploaded.front.path.parent / "template_upload_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["template_fingerprint"] = legacy_template_content_hash(
        uploaded.front.path, uploaded.back.path
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = service.load_current(db=db, session_id=session_id)
    assert loaded.first_page_role == "front"
    assert loaded.template_fingerprint == uploaded.template_fingerprint

    swapped = service.assign_first_page_role(
        db=db,
        session_id=session_id,
        first_page_role="back",
        expected_template_fingerprint=uploaded.template_fingerprint,
    )
    assert swapped.changed is True
    assert swapped.template.first_page_role == "back"
