from __future__ import annotations

import json

from db_manager import DBManager


def test_session_source_and_sync_state_are_persistent(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试",
        "rubric.json",
        "answer.json",
        source_paper_path="question_bank/raw_papers/paper_abc.docx",
        source_paper_sha256="a" * 64,
    )

    db.update_question_bank_sync_state(
        session_id,
        state="partial",
        details={"assessment_total": 18, "assessment_resolved": 16},
        error="2 道题缺少技能",
    )

    row = db.get_grading_session(session_id)
    assert row is not None
    assert row["source_paper_path"] == "question_bank/raw_papers/paper_abc.docx"
    assert row["source_paper_sha256"] == "a" * 64
    assert row["question_bank_sync_state"] == "partial"
    assert json.loads(row["question_bank_sync_details_json"])["assessment_resolved"] == 16
    assert row["question_bank_sync_error"] == "2 道题缺少技能"


def test_rebinding_changed_source_resets_sync_but_same_hash_preserves_it(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试",
        "rubric.json",
        "answer.json",
        source_paper_path="question_bank/raw_papers/first.docx",
        source_paper_sha256="a" * 64,
    )
    db.update_question_bank_sync_state(session_id, state="ready", details={"confirmed": 18})

    db.bind_grading_session_source(
        session_id,
        source_paper_path="question_bank/raw_papers/renamed.docx",
        source_paper_sha256="a" * 64,
    )
    same_row = db.get_grading_session(session_id)
    assert same_row is not None
    assert same_row["question_bank_sync_state"] == "ready"

    db.bind_grading_session_source(
        session_id,
        source_paper_path="question_bank/raw_papers/replacement.docx",
        source_paper_sha256="b" * 64,
    )
    changed_row = db.get_grading_session(session_id)
    assert changed_row is not None
    assert changed_row["question_bank_sync_state"] == "not_started"
    assert changed_row["question_bank_sync_details_json"] == "{}"
    assert changed_row["question_bank_sync_error"] is None


def test_shared_source_archive_is_not_collected_as_session_owned_file(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session(
        "期中测试",
        "rubric.json",
        "answer.json",
        source_paper_path="question_bank/raw_papers/shared.docx",
        source_paper_sha256="c" * 64,
    )

    assert "question_bank/raw_papers/shared.docx" not in db.collect_session_storage_paths(session_id)


def test_invalid_sync_state_is_rejected(tmp_path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("期中测试", "rubric.json", "answer.json")

    try:
        db.update_question_bank_sync_state(session_id, state="unknown")
    except ValueError as exc:
        assert "unsupported question-bank sync state" in str(exc)
    else:
        raise AssertionError("invalid state must be rejected")
