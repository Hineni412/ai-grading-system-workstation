from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from backend.repositories.reporting import load_question_bank_knowledge_backfill
from path_manager import get_path_manager
from report import ReportGenerator, _knowledge_bucket_labels


RUBRIC = {
    "questions": [
        {"question_id": "Q1", "max_score": 4},
        {
            "question_id": "Q2",
            "max_score": 10,
            "knowledge_points": [
                {"knowledge_id": "KP2", "knowledge_name": "因式分解"},
            ],
        },
        {"question_id": "Q3", "max_score": 4},
        {
            "question_id": "Q10",
            "max_score": 10,
            "parts": [
                {"part_id": "Q10(P1)", "part_score": 4},
                {"part_id": "Q10(P2)", "part_score": 6},
            ],
        },
        {"question_id": "Q99", "max_score": 2},
    ]
}

Q1_TAG = "七年级下册｜第五章 图形的轴对称｜1 轴对称及其性质｜轴对称图形的识别"
Q3_TAG = "八年级上册｜第三章 轴对称｜轴对称图形的识别"
Q10_TAG = "七年级下册｜第五章 图形的轴对称｜2 轴对称的性质｜轴对称的性质"
Q2_TAG = "七年级下册｜第六章 整式｜题库侧标签"
PENDING_TAG = "不应出现的标签"


def _complete_payload() -> str:
    return json.dumps(
        {
            "grading_completeness": {
                "status": "complete",
                "missing_question_ids": [],
                "duplicate_question_ids": [],
                "unexpected_question_ids": [],
                "score_out_of_range": [],
                "affected_major_question_ids": [],
            }
        },
        ensure_ascii=False,
    )


def _controlled_work_dir(tmp_path: Path) -> Path:
    """Per-test directory under the conftest-controlled data root.

    Stored rubric paths must stay inside a controlled root or
    ``resolve_stored_file_path`` rejects them, so the export fixtures live
    under the isolated ``user_data`` root that conftest installs, keyed by
    the pytest tmp dir name to avoid cross-test bleed.
    """
    work = (
        Path(get_path_manager().data_root)
        / "report_backfill_tests"
        / tmp_path.name
    )
    work.mkdir(parents=True, exist_ok=True)
    return work


def _seed_grading_db(work: Path) -> Path:
    db_path = work / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path = work / "rubric.json"
    rubric_path.write_text(json.dumps(RUBRIC, ensure_ascii=False), encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE grading_sessions (
                id INTEGER PRIMARY KEY,
                session_name TEXT,
                rubric_path TEXT,
                answer_key_path TEXT
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
                paper_id INTEGER,
                total_score REAL,
                student_score REAL,
                ai_student_score REAL,
                needs_human_review INTEGER,
                raw_json TEXT,
                graded_at TEXT
            );
            CREATE TABLE session_details (
                id INTEGER PRIMARY KEY,
                result_id INTEGER,
                question_id TEXT,
                score_awarded REAL,
                ai_score_awarded REAL,
                deduction_reason TEXT,
                knowledge_id TEXT,
                knowledge_ids TEXT,
                error_category TEXT,
                error_summary TEXT,
                confidence_score REAL
            );
            CREATE TABLE teacher_score_locks (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                scan_batch_id TEXT,
                student_id INTEGER,
                question_id TEXT,
                score_awarded REAL,
                max_score REAL,
                deduction_reason TEXT,
                source_target_type TEXT,
                source_target_id INTEGER,
                revision INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE session_attendance (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                student_id INTEGER,
                attendance_status TEXT,
                source_reason TEXT,
                created_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path) VALUES (1, '知识点回填测试', ?, '')",
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json, graded_at
            ) VALUES (1, 1, 1, 1, 34, 20, 0, ?, '2026-08-01 10:00:00')
            """,
            (_complete_payload(),),
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary
            ) VALUES (1, ?, ?, '', ?, '', '')
            """,
            [
                ("Q1", 3, '["UNKNOWN"]'),
                ("Q2", 5, '["KP2"]'),
                ("Q3", 2, '["OBJECTIVE"]'),
                ("Q10(P1)", 2, '["UNKNOWN"]'),
                ("Q10(P2)", 4, '["UNKNOWN"]'),
                ("Q99", 1, '["UNKNOWN"]'),
            ],
        )
        conn.commit()
    return db_path


def _seed_question_bank_db(work: Path) -> Path:
    qb_path = work / "databases" / "question_bank.db"
    qb_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(qb_path) as conn:
        conn.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                question_number TEXT,
                question_text TEXT,
                source_file TEXT,
                is_deleted INTEGER DEFAULT 0
            );
            CREATE TABLE grading_question_links (
                id INTEGER PRIMARY KEY,
                grading_session_id TEXT,
                source_question_id TEXT,
                bank_question_id INTEGER,
                status TEXT,
                confidence REAL
            );
            CREATE TABLE question_tags (
                id INTEGER PRIMARY KEY,
                question_id INTEGER,
                tag_type TEXT,
                tag_value TEXT,
                confidence REAL,
                source TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO questions (id, question_number, question_text, source_file, is_deleted) VALUES (?, ?, '', 'paper.docx', 0)",
            [(101, "1"), (102, "2"), (103, "3"), (110, "10"), (199, "1b")],
        )
        conn.executemany(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id, status, confidence
            ) VALUES ('1', ?, ?, ?, 1.0)
            """,
            [
                ("Q1", 101, "confirmed"),
                ("Q2", 102, "confirmed"),
                ("Q3", 103, "confirmed"),
                ("Q10", 110, "confirmed"),
                # Unconfirmed duplicate must be ignored.
                ("Q1", 199, "pending"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source)
            VALUES (?, 'knowledge_point', ?, 1.0, 'ai')
            """,
            [
                (101, Q1_TAG),
                (102, Q2_TAG),
                (103, Q3_TAG),
                (110, Q10_TAG),
                (199, PENDING_TAG),
            ],
        )
        conn.commit()
    return qb_path


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    work = _controlled_work_dir(tmp_path)
    return _seed_grading_db(work), _seed_question_bank_db(work)


def test_export_backfills_knowledge_labels_from_question_bank(tmp_path: Path) -> None:
    db_path, _ = _seed(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    sheet = pd.read_excel(export_path, sheet_name="知识点分析")

    rows = [dict(row) for _, row in sheet.iterrows()]
    assert PENDING_TAG not in set(sheet["知识点"])
    assert PENDING_TAG.split("｜")[-1] not in set(sheet["知识点"])

    # 同名末段、不同章节的路径必须分成两行。
    leaf_rows = [row for row in rows if row["知识点"] == "轴对称图形的识别"]
    assert len(leaf_rows) == 2
    assert {row["涉及题目"] for row in leaf_rows} == {"Q1", "Q3"}

    # 小问 Q10(P1)/Q10(P2) 回退到父题 Q10 的题库标签。
    (q10_row,) = [row for row in rows if row["知识点"] == "轴对称的性质"]
    assert q10_row["涉及题目"] == "Q10(P1)、Q10(P2)"
    assert q10_row["累计得分"] == 6
    assert q10_row["累计满分"] == 10

    # 老行为：rubric 自带知识点的题目不被题库回填覆盖。
    (q2_row,) = [row for row in rows if row["知识点"] == "因式分解"]
    assert q2_row["涉及题目"] == "Q2"

    # 题库中也查不到的题目仍落“未命名知识点”。
    (unnamed_row,) = [row for row in rows if row["知识点"] == "未命名知识点"]
    assert unnamed_row["涉及题目"] == "Q99"

    assert len(rows) == 5


def test_export_without_question_bank_keeps_placeholder_bucket(tmp_path: Path) -> None:
    db_path = _seed_grading_db(_controlled_work_dir(tmp_path))

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    sheet = pd.read_excel(export_path, sheet_name="知识点分析")

    labels = set(sheet["知识点"])
    assert labels == {"因式分解", "未命名知识点"}


def test_load_question_bank_knowledge_backfill_filters_and_splits(tmp_path: Path) -> None:
    _, qb_path = _seed(tmp_path)

    backfill = load_question_bank_knowledge_backfill(qb_path, 1)

    assert set(backfill) == {"Q1", "Q2", "Q3", "Q10"}
    assert backfill["Q1"] == [
        {"path": Q1_TAG, "label": "轴对称图形的识别"}
    ]
    # 未确认链接不参与回填。
    assert all(entry["label"] != PENDING_TAG for entries in backfill.values() for entry in entries)
    # 其他场次没有数据。
    assert load_question_bank_knowledge_backfill(qb_path, 999) == {}
    # 库文件缺失时安全返回空映射。
    assert load_question_bank_knowledge_backfill(tmp_path / "missing.db", 1) == {}


def test_knowledge_bucket_labels_prefers_stored_labels_over_backfill() -> None:
    item = {"question_id": "Q2", "knowledge_ids": ["KP2"]}
    backfill = {"Q2": [{"path": Q2_TAG, "label": "题库侧标签"}]}

    assert _knowledge_bucket_labels(item, {"KP2": "因式分解"}, backfill) == [
        ("因式分解", "因式分解")
    ]


def test_knowledge_bucket_labels_parent_fallback_and_unnamed() -> None:
    backfill = {"Q10": [{"path": Q10_TAG, "label": "轴对称的性质"}]}

    assert _knowledge_bucket_labels(
        {"question_id": "Q10(P1)", "knowledge_ids": ["UNKNOWN"]},
        {},
        backfill,
    ) == [(Q10_TAG, "轴对称的性质")]
    assert _knowledge_bucket_labels(
        {"question_id": "Q99", "knowledge_ids": ["OBJECTIVE"]},
        {},
        backfill,
    ) == [("未命名知识点", "未命名知识点")]
