from __future__ import annotations

from pathlib import Path

import pytest

from db_manager import DBManager
from session_cleanup import hard_delete_session_from_recycle_bin


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _session_table_counts(db: DBManager, session_id: int) -> dict[str, int]:
    tables = [
        "grading_sessions",
        "session_templates",
        "answer_regions",
        "exam_papers",
        "session_results",
        "session_details",
        "session_attendance",
        "annotated_results",
    ]
    with db._connect() as conn:
        counts: dict[str, int] = {}
        for table in tables:
            if table == "session_details":
                counts[table] = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS c
                        FROM session_details sd
                        JOIN session_results sr ON sr.id = sd.result_id
                        WHERE sr.session_id = ?
                        """,
                        (session_id,),
                    ).fetchone()["c"]
                )
            else:
                counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE session_id = ?", (session_id,)).fetchone()["c"]
                    if table != "grading_sessions"
                    else conn.execute("SELECT COUNT(*) AS c FROM grading_sessions WHERE id = ?", (session_id,)).fetchone()[
                        "c"
                    ]
                )
        return counts


def _make_deleted_session(db: DBManager, data_root: Path) -> tuple[int, list[Path]]:
    rubric = _write(data_root / "config" / "uploaded" / "rubric.json")
    answer_key = _write(data_root / "config" / "uploaded" / "answer.json")
    session_id = db.create_grading_session("old session", str(rubric), str(answer_key))

    template_dir = data_root / "templates" / f"session_{session_id}"
    exams_dir = data_root / "exams" / f"session_{session_id}"
    annotated_dir = data_root / "annotated" / f"session_{session_id}"
    front_template = _write(template_dir / "front.png")
    back_template = _write(template_dir / "back.png")
    analysis = _write(template_dir / "analysis.json")
    regions = _write(template_dir / "regions.json")
    region_draft = _write(template_dir / "region_draft.json")
    front_scan = _write(exams_dir / "front.jpg")
    back_scan = _write(exams_dir / "back.jpg")
    annotated_front = _write(annotated_dir / "front.jpg")
    annotated_back = _write(annotated_dir / "back.jpg")

    template_id = db.upsert_session_template(session_id, str(front_template), str(back_template))
    db.update_session_template_analysis(
        session_id,
        ai_analysis_path=str(analysis),
        template_config_path=str(template_dir / "config.json"),
        regions_path=str(regions),
    )
    db.save_answer_regions(
        session_id,
        template_id,
        [{"page": "front", "region_order": 1, "x": 1, "y": 2, "w": 3, "h": 4, "confidence": 1.0}],
    )

    with db._connect() as conn:
        student_id = conn.execute(
            "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
        ).lastrowid
        paper_id = conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status
            ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
            """,
            (session_id, str(front_scan), str(back_scan), student_id),
        ).lastrowid
        result_id = conn.execute(
            """
            INSERT INTO session_results (
                session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json
            ) VALUES (?, ?, ?, 100, 90, 0, '{}')
            """,
            (session_id, student_id, paper_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids)
            VALUES (?, '1', 90, '', '["k1"]')
            """,
            (result_id,),
        )
        conn.execute(
            """
            INSERT INTO annotated_results (session_id, result_id, annotated_front_path, annotated_back_path)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, result_id, str(annotated_front), str(annotated_back)),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (session_id, student_id, attendance_status, matched_paper_id)
            VALUES (?, ?, 'present', ?)
            """,
            (session_id, student_id, paper_id),
        )
        conn.commit()

    db.soft_delete_grading_session(session_id)
    return session_id, [
        rubric,
        answer_key,
        front_template,
        back_template,
        analysis,
        regions,
        region_draft,
        front_scan,
        back_scan,
        annotated_front,
        annotated_back,
    ]


def test_hard_delete_session_removes_database_rows_and_owned_files(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)

    result = hard_delete_session_from_recycle_bin(db, session_id, data_root=data_root)

    assert result["db_counts"]["grading_sessions"] == 1
    assert all(count == 0 for count in _session_table_counts(db, session_id).values())
    assert result["deleted_files"] >= len(owned_files)
    assert not (data_root / "templates" / f"session_{session_id}").exists()
    assert not (data_root / "templates" / f"session_{session_id}" / "region_draft.json").exists()
    assert not (data_root / "exams" / f"session_{session_id}").exists()
    assert not (data_root / "annotated" / f"session_{session_id}").exists()
    for path in owned_files:
        assert not path.exists()


def test_hard_delete_rejects_active_session(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id = db.create_grading_session("active", str(data_root / "r.json"), str(data_root / "a.json"))

    with pytest.raises(ValueError, match="recycle bin"):
        hard_delete_session_from_recycle_bin(db, session_id, data_root=data_root)
