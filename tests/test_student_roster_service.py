from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier

import pytest
from openpyxl import Workbook

from backend.students import (
    StudentBackupFailed,
    StudentRosterConflict,
    StudentDeleteFailed,
    StudentRosterModule,
)
from db_manager import DBManager, StudentRecord


def _roster(tmp_path):
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    return StudentRosterModule(db), db


def _seed_student_history(db: DBManager, student_id: int) -> None:
    with sqlite3.connect(db.db_path) as conn:
        session_id = conn.execute(
            """
            INSERT INTO grading_sessions (session_name, rubric_path, answer_key_path)
            VALUES ('匿名考试', 'rubric.json', 'answer.json')
            """
        ).lastrowid
        paper_id = conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, student_id, match_status
            ) VALUES (?, 'front.png', 'back.png', ?, 'matched')
            """,
            (session_id, student_id),
        ).lastrowid
        result_id = conn.execute(
            """
            INSERT INTO session_results (
                session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (?, ?, ?, 100, 90, 0, '{}')
            """,
            (session_id, student_id, paper_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (?, 'Q1', 10, '["UNKNOWN"]')
            """,
            (result_id,),
        )
        conn.execute(
            """
            INSERT INTO annotated_results (session_id, result_id)
            VALUES (?, ?)
            """,
            (session_id, result_id),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (
                session_id, student_id, attendance_status, matched_paper_id
            ) VALUES (?, ?, 'present', ?)
            """,
            (session_id, student_id, paper_id),
        )


def test_delete_student_aborts_when_backup_fails(tmp_path, monkeypatch) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "匿名学生甲", "一班")])
    student = db.list_students()[0]
    _seed_student_history(db, int(student["id"]))
    impact = roster.deletion_impact(int(student["id"]))

    def fail_backup(*_args, **_kwargs):
        raise OSError("private backup path is unavailable")

    monkeypatch.setattr(db, "create_backup", fail_backup)

    with pytest.raises(StudentBackupFailed) as caught:
        roster.delete_student(
            int(student["id"]),
            impact.roster_revision,
            confirmed=True,
        )

    assert str(caught.value) == "备份失败，学生未删除"
    assert len(db.list_students()) == 1
    assert roster.deletion_impact(int(student["id"])).counts == impact.counts


def test_delete_student_rolls_back_when_a_related_delete_fails(tmp_path) -> None:
    from grading_run_store import GradingRunStore

    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "匿名学生甲", "一班")])
    student = db.list_students()[0]
    _seed_student_history(db, int(student["id"]))
    with sqlite3.connect(db.db_path) as conn:
        session_id, paper_id, result_id = conn.execute(
            """
            SELECT result.session_id, result.paper_id, result.id
            FROM session_results result
            WHERE result.student_id = ?
            """,
            (int(student["id"]),),
        ).fetchone()
    run_store = GradingRunStore(db.db_path)
    run = run_store.begin(int(session_id), "a" * 64, "full_paper")
    run_store.add_item(
        run.id,
        source_label="rollback-ledger",
        student_id=int(student["id"]),
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="graded",
        paper_id=int(paper_id),
        result_id=int(result_id),
    )
    run_store.finish(run.run_token, "completed")
    impact = roster.deletion_impact(int(student["id"]))
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_student_detail_delete
            BEFORE DELETE ON session_details
            BEGIN
                SELECT RAISE(ABORT, 'injected delete failure');
            END
            """
        )

    with pytest.raises(StudentDeleteFailed) as caught:
        roster.delete_student(
            int(student["id"]),
            impact.roster_revision,
            confirmed=True,
        )

    assert str(caught.value) == "删除失败，学生和历史数据均未改变"
    assert len(db.list_students()) == 1
    assert roster.deletion_impact(int(student["id"])).counts == impact.counts
    assert run_store.counts(run.id)["graded"] == 1
