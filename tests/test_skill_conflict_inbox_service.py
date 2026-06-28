from __future__ import annotations

import json
from pathlib import Path

from db_manager import DBManager
from question_bank.database.schema import connect, initialize_database


def _insert_conflict(
    db_path: Path,
    *,
    source_type: str,
    source_ref: str,
    raw_label: str,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skill_resolution_conflicts (
                source_type, source_ref, raw_label, normalized_label,
                candidate_skill_ids_json, reason, evidence_json, state
            ) VALUES (?, ?, ?, ?, '[]', 'ambiguous', '{}', 'open')
            """,
            (source_type, source_ref, raw_label, raw_label.casefold()),
        )


def _seed_inbox(tmp_path: Path) -> tuple[Path, Path]:
    qb_path = tmp_path / "question_bank.db"
    grading_path = tmp_path / "grading.db"
    initialize_database(qb_path)
    grading_db = DBManager(grading_path)
    grading_db.initialize()

    empty_rubric = tmp_path / "empty.json"
    active_rubric = tmp_path / "active.json"
    deleted_rubric = tmp_path / "deleted.json"
    answer_path = tmp_path / "answer.json"
    empty_rubric.write_text('{"questions": []}', encoding="utf-8")
    active_rubric.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "knowledge_name": "角平分线性质"},
                    {"question_id": "Q2", "knowledge_name": "三角形面积"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    deleted_rubric.write_text(
        '{"questions": [{"question_id": "Q1"}]}',
        encoding="utf-8",
    )
    answer_path.write_text("{}", encoding="utf-8")
    for index in range(1, 9):
        rubric_path = active_rubric if index == 7 else deleted_rubric if index == 8 else empty_rubric
        session_id = grading_db.create_grading_session(
            f"session-{index}",
            str(rubric_path),
            str(answer_path),
        )
        assert session_id == index
    grading_db.soft_delete_grading_session(8)

    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, source_file, import_status) VALUES (1, 'P', 'p.docx', 'ready')"
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text, answer_text, is_deleted
            ) VALUES (?, 1, ?, ?, '', ?)
            """,
            [
                (1, "1", "active resolved", 0),
                (2, "2", "active blocking", 0),
                (3, "3", "deleted", 1),
            ],
        )
        skill_id = int(
            conn.execute(
                "SELECT id FROM skills WHERE stable_key = 'math.geometry.line_angle.bisector'"
            ).fetchone()[0]
        )
        conn.execute(
            """
            INSERT INTO question_skill_links (
                question_id, skill_id, role, source, status
            ) VALUES (1, ?, 'measured', 'test', 'resolved')
            """,
            (skill_id,),
        )
        conn.execute(
            """
            INSERT INTO assessment_item_skills (
                grading_session_id, source_question_id, skill_id, role, source, status
            ) VALUES ('7', 'Q1', ?, 'measured', 'test', 'resolved')
            """,
            (skill_id,),
        )

    for source_type, source_ref, raw_label in (
        ("question_bank_item", "1", "角平分线"),
        ("question_bank_item", "question:1", "角度平分线"),
        ("question_bank_item", "question:2", "三角形面积计算"),
        ("question_bank_item", "question:3", "历史题目知识点"),
        ("assessment_item", "7:Q1", "评分规则附加词条"),
        ("assessment_item", "7:Q2", "评分规则未匹配词条"),
        ("assessment_item", "8:Q1", "已删除试卷词条"),
    ):
        _insert_conflict(
            qb_path,
            source_type=source_type,
            source_ref=source_ref,
            raw_label=raw_label,
        )
    return qb_path, grading_path


def test_groups_open_conflicts_by_source_and_severity(tmp_path: Path) -> None:
    from integration.skill_conflict_inbox_service import SkillConflictInboxService

    qb_path, grading_path = _seed_inbox(tmp_path)

    summary = SkillConflictInboxService(qb_path, grading_path).summary()

    assert [group.source_key for group in summary.blocking] == [
        "assessment:7:Q2",
        "question:2",
    ]
    assert [group.source_key for group in summary.advisory] == [
        "assessment:7:Q1",
        "question:1",
    ]
    assert [group.source_key for group in summary.historical] == [
        "assessment:8:Q1",
        "question:3",
    ]
    question_group = next(
        group for group in summary.advisory if group.source_key == "question:1"
    )
    assert len(question_group.conflicts) == 2
    assert summary.coverage["question_bank"] == {
        "total": 2,
        "resolved": 1,
        "blocking": 1,
        "advisory": 1,
    }
    assert summary.coverage["assessment"] == {
        "total": 2,
        "resolved": 1,
        "blocking": 1,
        "advisory": 1,
    }


def test_missing_active_rubric_uses_known_items_and_reports_warning(tmp_path: Path) -> None:
    from integration.skill_conflict_inbox_service import SkillConflictInboxService

    qb_path, grading_path = _seed_inbox(tmp_path)
    DBManager(grading_path).get_grading_session(7)
    (tmp_path / "active.json").unlink()

    summary = SkillConflictInboxService(qb_path, grading_path).summary()

    assert summary.coverage["assessment"]["total"] == 2
    assert any("session-7" in warning for warning in summary.warnings)
