"""P3-04 student and session repository behavior on temporary databases."""

from __future__ import annotations

import sqlite3
import inspect
from pathlib import Path

import pytest

from db_manager import DBManager


def _initialized_database(tmp_path: Path) -> Path:
    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    return database


def test_student_repository_preserves_roster_mapping_sort_and_revision(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.students import (
        StudentRecord,
        StudentRepositoryGateway,
        student_roster_revision,
    )

    database = _initialized_database(tmp_path)
    repository = StudentRepositoryGateway(SQLiteConnectionFactory(database))

    result = repository.upsert_students(
        [
            StudentRecord(student_code="B-2", name="张三", class_name="二班"),
            StudentRecord(student_code="A-1", name="李四", class_name="一班"),
        ]
    )
    rows = repository.list_students()

    assert result == {"inserted": 2, "updated": 0, "total": 2}
    assert [row["student_code"] for row in rows] == ["A-1", "B-2"]
    assert set(rows[0]) == {"id", "student_code", "name", "class_name", "created_at"}
    assert repository.student_roster_snapshot() == (
        rows,
        student_roster_revision(rows),
    )


def test_student_repository_revision_writes_reject_stale_and_duplicate_codes(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.students import (
        StudentCodeConflictError,
        StudentRecord,
        StudentRepositoryGateway,
        StudentRosterRevisionConflict,
    )

    repository = StudentRepositoryGateway(
        SQLiteConnectionFactory(_initialized_database(tmp_path))
    )
    _, empty_revision = repository.student_roster_snapshot()
    inserted = repository.upsert_students_if_revision(
        [StudentRecord("001", "甲", "一班"), StudentRecord("002", "乙", "一班")],
        expected_revision=empty_revision,
    )

    with pytest.raises(StudentRosterRevisionConflict):
        repository.upsert_students_if_revision(
            [StudentRecord("003", "丙", "二班")],
            expected_revision=empty_revision,
        )

    students = repository.list_students()
    first_id = next(int(row["id"]) for row in students if row["student_code"] == "001")
    with pytest.raises(StudentCodeConflictError):
        repository.update_student_if_revision(
            first_id,
            "002",
            "甲",
            "一班",
            expected_revision=str(inserted["roster_revision"]),
        )
    assert sorted(row["student_code"] for row in repository.list_students()) == ["001", "002"]


def test_student_repository_workspace_escapes_search_and_preserves_snapshot(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.students import StudentRecord, StudentRepositoryGateway

    repository = StudentRepositoryGateway(
        SQLiteConnectionFactory(_initialized_database(tmp_path))
    )
    repository.upsert_students(
        [
            StudentRecord("A_%", "Alpha", "Class-A"),
            StudentRecord("A12", "Beta", "Class-A"),
            StudentRecord("B01", "Gamma", "Class-B"),
        ]
    )
    all_rows, revision = repository.student_roster_snapshot()

    snapshot = repository.student_workspace_snapshot(
        search="A_%",
        class_name="Class-A",
        page=1,
        page_size=1,
    )

    assert [row["student_code"] for row in snapshot["items"]] == ["A_%"]
    assert snapshot == {
        "items": snapshot["items"],
        "total": 1,
        "class_names": ["Class-A", "Class-B"],
        "roster_revision": revision,
    }
    assert len(all_rows) == 3


def test_student_repository_name_lookup_normalizes_and_invalidates_cache(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.students import StudentRecord, StudentRepositoryGateway

    repository = StudentRepositoryGateway(
        SQLiteConnectionFactory(_initialized_database(tmp_path))
    )
    repository.upsert_students([StudentRecord("001", "Li · Ming", "Class-A")])

    assert repository.find_student_by_name(" li ming ")["student_code"] == "001"
    student_id = int(repository.list_students()[0]["id"])
    repository.update_student(student_id, "001", "New Name", "Class-A")
    assert repository.find_student_by_name("Li Ming") is None
    assert repository.find_student_by_name("newname")["student_code"] == "001"


def test_student_repository_delete_is_guarded_and_atomic(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.students import (
        StudentGradingActiveError,
        StudentRecord,
        StudentRepositoryGateway,
    )

    database = _initialized_database(tmp_path)
    repository = StudentRepositoryGateway(
        SQLiteConnectionFactory(database),
        create_backup=lambda _reason: tmp_path / "backup.db",
    )
    repository.upsert_students([StudentRecord("001", "Alpha", "Class-A")])
    student_id = int(repository.list_students()[0]["id"])
    with sqlite3.connect(database) as connection:
        session_id = int(
            connection.execute(
                """
                INSERT INTO grading_sessions (
                    session_name, rubric_path, answer_key_path, status
                ) VALUES ('Session', '', '', 'running')
                """
            ).lastrowid
        )
        connection.execute(
            """
            INSERT INTO session_attendance (
                session_id, student_id, attendance_status
            ) VALUES (?, ?, 'present')
            """,
            (session_id, student_id),
        )

    impact = repository.student_deletion_impact(student_id)
    assert impact["counts"]["deleted_attendance"] == 1
    with pytest.raises(StudentGradingActiveError):
        repository.delete_student_hard(
            student_id,
            expected_revision=str(impact["roster_revision"]),
        )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE grading_sessions SET status = 'completed' WHERE id = ?",
            (session_id,),
        )
        connection.execute(
            """
            CREATE TRIGGER fail_student_delete
            BEFORE DELETE ON students
            BEGIN
                SELECT RAISE(ABORT, 'injected delete failure');
            END
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        repository.delete_student_hard(
            student_id,
            expected_revision=str(impact["roster_revision"]),
        )
    assert repository.student_deletion_impact(student_id)["counts"] == impact["counts"]


def test_session_repository_preserves_crud_source_and_recycle_bin_rules(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.sessions import SessionRepositoryGateway

    database = _initialized_database(tmp_path)
    repository = SessionRepositoryGateway(SQLiteConnectionFactory(database))
    with pytest.raises(ValueError, match="full SHA-256"):
        repository.create_grading_session(
            "Invalid",
            "rubric.json",
            "answer.json",
            source_paper_path="paper.pdf",
        )
    first_id = repository.create_grading_session("First", "", "")
    second_id = repository.create_grading_session(
        "Second",
        "rubric.json",
        "answer.json",
        source_paper_path="paper.pdf",
        source_paper_sha256="a" * 64,
    )

    assert [row["id"] for row in repository.list_grading_sessions()] == [
        second_id,
        first_id,
    ]
    repository.rename_grading_session(first_id, "Renamed")
    assert repository.get_grading_session(first_id)["session_name"] == "Renamed"
    repository.soft_delete_grading_session(first_id)
    assert [row["id"] for row in repository.list_grading_sessions()] == [second_id]
    assert repository.get_grading_session(first_id)["is_deleted"] == 1
    repository.restore_grading_session(first_id)
    assert repository.get_grading_session(first_id)["deleted_at"] is None


def test_session_repository_attendance_replace_is_atomic(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.sessions import SessionRepositoryGateway
    from backend.repositories.students import StudentRecord, StudentRepositoryGateway

    database = _initialized_database(tmp_path)
    sessions = SQLiteConnectionFactory(database)
    students = StudentRepositoryGateway(sessions)
    sessions_repository = SessionRepositoryGateway(sessions)
    students.upsert_students(
        [
            StudentRecord("002", "Beta", "Class-A"),
            StudentRecord("001", "Alpha", "Class-A"),
        ]
    )
    student_rows = students.list_students()
    student_ids = {row["student_code"]: int(row["id"]) for row in student_rows}
    session_id = sessions_repository.create_grading_session("Session", "", "")
    original = [
        {
            "student_id": student_ids["001"],
            "attendance_status": "present",
            "source_reason": "matched",
            "matched_paper_id": None,
        }
    ]
    sessions_repository.replace_session_attendance(session_id, original)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_attendance_insert
            BEFORE INSERT ON session_attendance
            WHEN NEW.attendance_status = 'boom'
            BEGIN
                SELECT RAISE(ABORT, 'injected attendance failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        sessions_repository.replace_session_attendance(
            session_id,
            [
                {
                    "student_id": student_ids["002"],
                    "attendance_status": "boom",
                }
            ],
        )
    rows = sessions_repository.get_session_attendance(session_id)
    assert [(row["student_code"], row["attendance_status"]) for row in rows] == [
        ("001", "present")
    ]


def test_db_manager_student_session_facades_contain_no_sql() -> None:
    method_names = (
        "upsert_students",
        "student_roster_snapshot",
        "student_workspace_snapshot",
        "upsert_students_if_revision",
        "list_students",
        "update_student",
        "update_student_if_revision",
        "student_deletion_impact",
        "delete_student_hard",
        "find_student_by_name",
        "create_grading_session",
        "rename_grading_session",
        "soft_delete_grading_session",
        "restore_grading_session",
        "list_grading_sessions",
        "get_grading_session",
        "replace_session_attendance",
        "get_session_attendance",
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(DBManager, method_name)).upper()
        assert not any(
            keyword in source
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
        ), method_name


def test_db_manager_repository_reads_reuse_borrowed_request_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _initialized_database(tmp_path)
    with sqlite3.connect(database) as seed:
        seed.execute(
            "INSERT INTO students (student_code, name) VALUES ('001', 'Alpha')"
        )
    external = sqlite3.connect(database, isolation_level=None)
    external.row_factory = sqlite3.Row
    external.execute("PRAGMA query_only = ON")
    external.execute("BEGIN")
    manager = DBManager(database, external_connection=external)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("opened an extra connection")
        ),
    )
    try:
        assert manager.list_students()[0]["student_code"] == "001"
        assert manager.student_workspace_snapshot(
            search="",
            class_name="",
            page=1,
            page_size=10,
        )["total"] == 1
    finally:
        external.rollback()
        external.close()
