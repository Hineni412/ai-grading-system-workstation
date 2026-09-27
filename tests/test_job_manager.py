from __future__ import annotations

import sqlite3
import threading
import time


def test_job_manager_restart_removes_only_interrupted_automatic_question_links(
    tmp_path,
) -> None:
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db_path = tmp_path / "grading.db"
    question_bank_db_path = tmp_path / "question-bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    session_id = grading_db.create_grading_session(
        "Exam",
        "rubric.json",
        "answer.json",
    )
    store = JobStore(grading_db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
        },
    )
    assert store.mark_running(job.id)
    grading_db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "job_id": job.id,
            "source_paper_sha256": "a" * 64,
            "config_revision": "b" * 64,
            "stage": "linking",
        },
    )

    initialize_database(question_bank_db_path)
    with connect(question_bank_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (?, ?, ?)
            """,
            [(201, "17", "automatic"), (202, "18", "manual")],
        )
    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q17",
        bank_question_id=201,
        link_method="source_metadata",
        evidence={"sync_job_id": job.id, "sync_config_revision": "b" * 64},
    )
    links.confirm_link(
        grading_session_id=session_id,
        source_question_id="Q18",
        bank_question_id=202,
        link_method="manual",
        reviewed_by="teacher",
    )

    manager = JobManager(
        store,
        max_workers=1,
        question_bank_db_path=question_bank_db_path,
    )
    try:
        assert store.get_job(job.id).status == "failed"
        remaining = links.list_links(session_id)
        assert [
            (item["source_question_id"], item["bank_question_id"]) for item in remaining
        ] == [("Q18", 202)]
        assert (
            grading_db.get_grading_session(session_id)["question_bank_sync_state"]
            == "failed"
        )
    finally:
        manager.shutdown()
