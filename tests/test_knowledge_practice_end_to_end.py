from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.services.source_question_link_service import SourceQuestionLinkService
from question_bank.services.training_export_service import TrainingExportService
from question_bank.services.training_task_service import TrainingTaskService


def test_teacher_can_generate_save_and_export_grouped_practice(tmp_path: Path) -> None:
    grading_db = tmp_path / "grading_system.db"
    question_bank_db = tmp_path / "question_bank.db"
    output_dir = tmp_path / "exports"
    _build_grading_fixture(grading_db, tmp_path)
    original_question_id = _build_question_bank_fixture(question_bank_db)

    diagnosis = DiagnosisProfileService(grading_db, question_bank_db).build_legacy_profiles(
        scope={"mode": "selected", "student_ids": ["12", "15"]},
        exam_scope={"mode": "manual", "session_ids": [12, 14]},
    )
    plan = PracticePlanService(question_bank_db).generate(
        diagnosis,
        variant_mode="auto_group",
        question_count=10,
    )
    task = TrainingTaskService(question_bank_db).create_task(plan, created_by="teacher")
    exports = TrainingExportService(question_bank_db, output_dir).export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
    )
    loaded = TrainingTaskService(question_bank_db).get_task(task.id)

    assert diagnosis["unmapped_terms"] == ["陌生诊断词"]
    assert len(plan["variants"]) == 1
    assert plan["variants"][0]["variant_type"] == "group"
    assert original_question_id not in {
        item["question_id"]
        for variant in plan["variants"]
        for item in variant["items"]
    }
    assert loaded["diagnosis_snapshot"] == diagnosis
    assert {item["audience"] for item in exports["exports"]} == {"student", "teacher"}
    assert all(item["status"] == "succeeded" for item in exports["exports"])


def test_numbered_weak_point_confirmation_survives_simplified_training_flow(
    tmp_path: Path,
) -> None:
    grading_db = tmp_path / "grading_system.db"
    question_bank_db = tmp_path / "question_bank.db"
    output_dir = tmp_path / "numbered_exports"
    _build_numbered_grading_fixture(
        grading_db,
        tmp_path,
        knowledge_id="G7_15",
        knowledge_name="角平分线性质",
    )
    question_id, concept_id = _build_angle_bisector_question_bank(question_bank_db)
    alignment = ConceptAlignmentService(question_bank_db)
    alignment.confirm_mapping(
        "grading_weak_point",
        "G7_15 · 角平分线性质",
        concept_id,
        reviewed_by="teacher",
    )

    diagnosis = DiagnosisProfileService(grading_db, question_bank_db).build_legacy_profiles(
        scope={"mode": "student", "student_ids": ["70"]},
        exam_scope={"mode": "current", "session_ids": [1]},
    )
    plan = PracticePlanService(question_bank_db).generate(
        diagnosis,
        question_count=8,
    )
    task = TrainingTaskService(question_bank_db).create_task(
        plan,
        created_by="teacher",
    )
    exports = TrainingExportService(question_bank_db, output_dir).export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
    )

    weak = diagnosis["students"][0]["weak_points"][0]
    assert weak["source_term"] == "角平分线性质"
    assert weak["mapping_status"] == "confirmed"
    assert question_id in {
        item["question_id"]
        for variant in plan["variants"]
        for item in variant["items"]
    }
    assert all(item["status"] == "succeeded" for item in exports["exports"])


def _build_numbered_grading_fixture(
    db_path: Path,
    root: Path,
    *,
    knowledge_id: str,
    knowledge_name: str,
) -> None:
    DBManager(db_path).initialize()
    rubric_path = root / "numbered_rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 10,
                        "knowledge_id": knowledge_id,
                        "knowledge_name": knowledge_name,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (70, 'S70', '测试学生', '10')"
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (1, '编号知识点考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        _insert_result(
            conn,
            session_id=1,
            student_id=70,
            paper_id=7001,
            result_id=70001,
            student_score=0,
            details=[("Q1", 0, knowledge_id)],
        )


def _build_angle_bisector_question_bank(db_path: Path) -> tuple[int, int]:
    initialize_database(db_path)
    alignment = ConceptAlignmentService(db_path)
    concept = alignment.create_concept("math.angle_bisector", "角平分线")
    alignment.confirm_mapping(
        "question_tag",
        "角平分线性质",
        concept.id,
        reviewed_by="teacher",
    )
    with connect(db_path) as conn:
        for index, question_id in enumerate(range(710, 718), start=1):
            _insert_question(
                conn,
                question_id,
                f"角平分线训练{index}",
                f"利用角平分线性质完成第 {index} 个计算。",
                "角平分线性质",
                f"角平分线方法{index}",
                "5",
            )
    return 710, concept.id


def _build_grading_fixture(db_path: Path, root: Path) -> None:
    DBManager(db_path).initialize()
    rubric_12 = _write_rubric(root / "rubric_12.json")
    rubric_14 = _write_rubric(root / "rubric_14.json")
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S12", "张三", "九年级1班"),
                (15, "S15", "李四", "九年级1班"),
                (18, "S18", "王五", "九年级2班"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (?, ?, ?, '', 'completed', 0)
            """,
            [(12, "第一次考试", str(rubric_12)), (14, "当前考试", str(rubric_14))],
        )
        _insert_result(conn, 12, 12, 1201, 12001, 9, [("Q1", 7, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")])
        _insert_result(conn, 12, 15, 1202, 12002, 8, [("Q1", 6, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")])
        _insert_result(conn, 12, 18, 1203, 12003, 12, [("Q1", 9, "K_QUADRATIC"), ("Q2", 3, "K_UNKNOWN")])
        _insert_result(conn, 14, 12, 1401, 14001, 7, [("Q1", 6, "K_QUADRATIC"), ("Q2", 1, "K_UNKNOWN")])
        _insert_result(conn, 14, 15, 1402, 14002, 7, [("Q1", 5, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")])


def _build_question_bank_fixture(db_path: Path) -> int:
    initialize_database(db_path)
    alignment = ConceptAlignmentService(db_path)
    direct = alignment.create_concept("math.quadratic", "二次函数")
    prerequisite = alignment.create_concept("math.quadratic_equation", "一元二次方程")
    transfer = alignment.create_concept("math.function_transfer", "函数综合")
    alignment.create_relation(direct.id, prerequisite.id, "prerequisite", weight=0.9)
    alignment.create_relation(direct.id, transfer.id, "related", weight=0.8)
    alignment.confirm_many(
        [
            ("grading_weak_point", "二次函数", direct.id),
            ("question_tag", "二次函数", direct.id),
            ("question_tag", "一元二次方程", prerequisite.id),
            ("question_tag", "函数综合", transfer.id),
        ]
    )

    with connect(db_path) as conn:
        for index, question_id in enumerate(range(210, 216), start=1):
            _insert_question(
                conn,
                question_id,
                f"深圳中考直接训练{index}",
                f"二次函数直接训练变式 {index}",
                "二次函数",
                f"直接方法{index}",
                str(4 + index % 3),
            )
        for index, question_id in enumerate(range(301, 304), start=1):
            _insert_question(
                conn,
                question_id,
                f"深圳中考前置训练{index}",
                f"一元二次方程前置训练 {index}",
                "一元二次方程",
                f"前置方法{index}",
                str(index + 1),
            )
        _insert_question(
            conn,
            305,
            "深圳中考迁移训练",
            "在新情境中综合运用函数知识解决问题。",
            "函数综合",
            "建模迁移",
            None,
        )
        _insert_question(
            conn,
            201,
            "当前考试原题",
            "已知二次函数 y=x²-2x-3，求其顶点坐标。",
            "二次函数",
            "原题方法",
            "5",
        )
        _insert_question(
            conn,
            202,
            "当前考试近重复题",
            "已知二次函数 y=x²-2x-3，请求它的顶点坐标。",
            "二次函数",
            "原题方法",
            "5",
        )
    SourceQuestionLinkService(db_path).confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=201,
        link_method="exact_text",
    )
    return 201


def _write_rubric(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 10,
                        "knowledge_id": "K_QUADRATIC",
                        "knowledge_name": "二次函数",
                    },
                    {
                        "question_id": "Q2",
                        "max_score": 5,
                        "knowledge_id": "K_UNKNOWN",
                        "knowledge_name": "陌生诊断词",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _insert_result(
    conn: sqlite3.Connection,
    session_id: int,
    student_id: int,
    paper_id: int,
    result_id: int,
    student_score: float,
    details: list[tuple[str, float, str]],
) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, session_id, front_image, back_image, student_id, match_status, processing_status
        ) VALUES (?, ?, '', '', ?, 'matched', 'graded')
        """,
        (paper_id, session_id, student_id),
    )
    conn.execute(
        """
        INSERT INTO session_results (
            id, session_id, student_id, paper_id, total_score, student_score,
            needs_human_review, raw_json
        ) VALUES (?, ?, ?, ?, 15, ?, 0, '{}')
        """,
        (result_id, session_id, student_id, paper_id, student_score),
    )
    conn.executemany(
        """
        INSERT INTO session_details (
            result_id, question_id, score_awarded, deduction_reason, knowledge_ids
        ) VALUES (?, ?, ?, '需要巩固', ?)
        """,
        [
            (result_id, question_id, score, json.dumps([knowledge_id]))
            for question_id, score, knowledge_id in details
        ],
    )


def _insert_question(
    conn,
    question_id: int,
    title: str,
    text: str,
    knowledge: str,
    method: str,
    difficulty: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO papers (
            id, title, source_file, city, exam_type, grade, import_status
        ) VALUES (?, ?, ?, '深圳', '中考', '九年级', 'ready')
        """,
        (question_id, title, f"{title}.docx"),
    )
    conn.execute(
        """
        INSERT INTO questions (
            id, paper_id, question_number, question_type, question_text, answer_text, difficulty
        ) VALUES (?, ?, '1', '解答题', ?, '参考答案', ?)
        """,
        (question_id, question_id, text, difficulty),
    )
    conn.executemany(
        """
        INSERT INTO question_tags (question_id, tag_type, tag_value, source)
        VALUES (?, ?, ?, 'manual')
        """,
        [
            (question_id, "knowledge_point", knowledge),
            (question_id, "method", method),
        ],
    )
