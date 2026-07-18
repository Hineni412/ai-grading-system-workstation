from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier

import pytest
from openpyxl import Workbook

from backend.students import (
    StudentBackupFailed,
    StudentCodeConflict,
    StudentRosterConflict,
    StudentDeleteFailed,
    StudentRosterEmpty,
    StudentRosterError,
    StudentRosterModule,
)
from db_manager import DBManager, StudentRecord


def _roster(tmp_path):
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    return StudentRosterModule(db), db


def test_preview_csv_maps_aliases_and_classifies_new_students(tmp_path) -> None:
    roster, _db = _roster(tmp_path)

    preview = roster.preview_import(
        "students.csv",
        "学号,姓名,班级\nS001,匿名学生甲,七年级一班\n".encode(),
    )

    assert preview.mapping == {
        "student_code": "学号",
        "name": "姓名",
        "class_name": "班级",
    }
    assert preview.counts == {
        "insert": 1,
        "update": 0,
        "unchanged": 0,
        "invalid": 0,
        "duplicate": 0,
    }
    assert preview.rows[0].to_dict() == {
        "source_row": 2,
        "student_code": "S001",
        "name": "匿名学生甲",
        "class_name": "七年级一班",
        "operation": "insert",
        "selectable": True,
        "issues": [],
        "existing": None,
    }


def test_preview_explains_invalid_updates_and_last_duplicate_wins(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S002", "旧姓名", "旧班级")])

    preview = roster.preview_import(
        "students.csv",
        (
            "student_code,name,class_name\n"
            "S001,,七年级一班\n"
            "S002,新姓名,新班级\n"
            "S003,前一行,一班\n"
            "S003,后一行,二班\n"
        ).encode(),
    )

    assert [row.operation for row in preview.rows] == [
        "invalid",
        "update",
        "duplicate",
        "insert",
    ]
    assert preview.rows[1].existing == {
        "id": 1,
        "student_code": "S002",
        "name": "旧姓名",
        "class_name": "旧班级",
        "created_at": preview.rows[1].existing["created_at"],
    }
    assert preview.rows[2].selectable is False
    assert preview.rows[2].issues == ("同一文件中后续行将覆盖此学号",)
    assert preview.counts == {
        "insert": 1,
        "update": 1,
        "unchanged": 0,
        "invalid": 1,
        "duplicate": 1,
    }


def test_preview_rejects_file_content_that_does_not_match_xlsx(tmp_path) -> None:
    roster, _db = _roster(tmp_path)

    with pytest.raises(StudentRosterError) as caught:
        roster.preview_import("students.xlsx", b"not-an-xlsx-file")

    assert getattr(caught.value, "code", None) == "student_roster_unreadable"
    assert str(caught.value) == "无法读取学生名单，请检查文件格式"


def test_preview_reports_an_empty_file_without_internal_parser_details(tmp_path) -> None:
    roster, _db = _roster(tmp_path)

    with pytest.raises(StudentRosterEmpty) as caught:
        roster.preview_import("students.csv", b"")

    assert str(caught.value) == "学生名单为空"


def test_preview_returns_columns_before_manual_mapping(tmp_path) -> None:
    roster, _db = _roster(tmp_path)
    content = "自定义编号,中文姓名,所在组\nA001,匿名学生甲,一班\n".encode()

    unmapped = roster.preview_import("students.csv", content)

    assert unmapped.columns == ("自定义编号", "中文姓名", "所在组")
    assert unmapped.mapping == {
        "student_code": None,
        "name": None,
        "class_name": None,
    }
    assert unmapped.issues == ("请选择学号列和姓名列",)
    assert unmapped.rows == ()

    mapped = roster.preview_import(
        "students.csv",
        content,
        {
            "student_code": "自定义编号",
            "name": "中文姓名",
            "class_name": "所在组",
        },
    )
    assert mapped.rows[0].operation == "insert"


def test_preview_reads_xlsx_and_preserves_text_student_codes(tmp_path) -> None:
    roster, _db = _roster(tmp_path)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["学号", "姓名", "班级"])
    sheet.append(["001", "匿名学生甲", "一班"])
    content = BytesIO()
    workbook.save(content)

    preview = roster.preview_import("students.xlsx", content.getvalue())

    assert preview.rows[0].student_code == "001"
    assert preview.rows[0].operation == "insert"


def test_commit_import_is_atomic_and_rejects_reused_revision(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "旧姓名", "旧班级")])
    preview = roster.preview_import(
        "students.csv",
        (
            "student_code,name,class_name\n"
            "S001,新姓名,新班级\n"
            "S002,匿名学生乙,二班\n"
            "S003,,三班\n"
        ).encode(),
    )
    selected = [
        {
            "student_code": row.student_code,
            "name": row.name,
            "class_name": row.class_name,
        }
        for row in preview.rows
        if row.selectable
    ]

    result = roster.commit_import(preview.roster_revision, selected)

    assert result.to_dict() == {
        "inserted": 1,
        "updated": 1,
        "unchanged": 0,
        "total": 2,
        "roster_revision": result.roster_revision,
    }
    assert [
        (row["student_code"], row["name"], row["class_name"])
        for row in db.list_students()
    ] == [
        ("S002", "匿名学生乙", "二班"),
        ("S001", "新姓名", "新班级"),
    ]
    with pytest.raises(StudentRosterConflict):
        roster.commit_import(preview.roster_revision, selected)


def test_concurrent_import_confirmations_have_one_revision_winner(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    preview = roster.preview_import(
        "students.csv",
        "student_code,name\nS001,匿名学生甲\n".encode(),
    )
    barrier = Barrier(2)

    def commit_once():
        worker = StudentRosterModule(DBManager(db.db_path))
        barrier.wait()
        try:
            return worker.commit_import(
                preview.roster_revision,
                [{"student_code": "S001", "name": "匿名学生甲", "class_name": None}],
            )
        except StudentRosterConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: commit_once(), range(2)))

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, StudentRosterConflict) for item in outcomes) == 1
    assert len(db.list_students()) == 1


def test_preview_handles_five_thousand_rows_without_truncation(tmp_path) -> None:
    roster, _db = _roster(tmp_path)
    rows = ["student_code,name,class_name"]
    rows.extend(
        f"S{index:05d},匿名学生{index:05d},七年级一班"
        for index in range(5000)
    )

    preview = roster.preview_import(
        "students.csv",
        ("\n".join(rows) + "\n").encode(),
    )

    assert len(preview.rows) == 5000
    assert preview.counts["insert"] == 5000


def test_workspace_filters_and_pages_a_stable_roster_snapshot(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students(
        [
            StudentRecord(
                f"A{index:03d}",
                f"匿名学生{index:03d}",
                "七年级一班",
            )
            for index in range(125)
        ]
        + [StudentRecord("B001", "另一班学生", "七年级二班")]
    )

    workspace = roster.workspace(
        search="匿名学生",
        class_name="七年级一班",
        page=3,
        page_size=50,
    )

    assert workspace.total == 125
    assert workspace.page == 3
    assert workspace.page_size == 50
    assert workspace.total_pages == 3
    assert len(workspace.items) == 25
    assert workspace.class_names == ("七年级一班", "七年级二班")
    assert len(workspace.roster_revision) == 64


def test_update_student_requires_current_revision_and_unique_code(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students(
        [
            StudentRecord("S001", "匿名学生甲", "一班"),
            StudentRecord("S002", "匿名学生乙", "二班"),
        ]
    )
    workspace = roster.workspace()
    first_id = next(
        item["id"] for item in workspace.items if item["student_code"] == "S001"
    )

    updated = roster.update_student(
        first_id,
        workspace.roster_revision,
        {"student_code": "S001", "name": "匿名学生甲新", "class_name": "三班"},
    )

    assert updated.student["name"] == "匿名学生甲新"
    assert updated.student["class_name"] == "三班"
    with pytest.raises(StudentRosterConflict):
        roster.update_student(
            first_id,
            workspace.roster_revision,
            {"student_code": "S001", "name": "过期修改", "class_name": "四班"},
        )
    with pytest.raises(StudentCodeConflict):
        roster.update_student(
            first_id,
            updated.roster_revision,
            {"student_code": "S002", "name": "冲突修改", "class_name": "四班"},
        )
    assert next(
        row for row in db.list_students() if row["id"] == first_id
    )["name"] == "匿名学生甲新"


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
                result_id, question_id, score_awarded, knowledge_id
            ) VALUES (?, 'Q1', 10, '')
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


def test_deletion_impact_counts_every_destructive_effect(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "匿名学生甲", "一班")])
    student = db.list_students()[0]
    _seed_student_history(db, int(student["id"]))

    impact = roster.deletion_impact(int(student["id"]))

    assert impact.student["student_code"] == "S001"
    assert impact.counts == {
        "deleted_students": 1,
        "deleted_results": 1,
        "deleted_details": 1,
        "deleted_annotations": 1,
        "deleted_attendance": 1,
        "unlinked_papers": 1,
    }
    assert len(impact.roster_revision) == 64


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


def test_delete_student_backs_up_then_removes_history_atomically(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "匿名学生甲", "一班")])
    student = db.list_students()[0]
    _seed_student_history(db, int(student["id"]))
    impact = roster.deletion_impact(int(student["id"]))

    result = roster.delete_student(
        int(student["id"]),
        impact.roster_revision,
        confirmed=True,
    )

    assert result.to_dict() == {
        **impact.counts,
        "backup_created": True,
        "roster_revision": result.roster_revision,
    }
    assert not any("path" in key for key in result.to_dict())
    assert len(list(db.backup_dir.glob("grading_before_delete_student_*.db"))) == 1
    assert db.list_students() == []
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_results").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_details").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM annotated_results").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM session_attendance").fetchone()[0] == 0
        assert conn.execute(
            "SELECT student_id, match_status FROM exam_papers"
        ).fetchone() == (None, "student_deleted")


def test_delete_student_rolls_back_when_a_related_delete_fails(tmp_path) -> None:
    roster, db = _roster(tmp_path)
    db.upsert_students([StudentRecord("S001", "匿名学生甲", "一班")])
    student = db.list_students()[0]
    _seed_student_history(db, int(student["id"]))
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
