from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.database.schema import connect as connect_question_bank
from question_bank.models.skill_catalog import ResolvedSkillLink
from question_bank.services.skill_catalog_service import SkillCatalogService
from question_bank.services.skill_link_service import SkillLinkService


@pytest.fixture
def service(tmp_path: Path) -> DiagnosisProfileService:
    grading_db_path = tmp_path / "grading_system.db"
    question_bank_db_path = tmp_path / "question_bank.db"
    DBManager(grading_db_path).initialize()

    rubric_12 = _write_rubric(
        tmp_path / "rubric_12.json",
        [
            ("Q1", 10, "K_QUADRATIC", "二次函数"),
            ("Q2", 5, "K_UNKNOWN", "陌生诊断词"),
        ],
    )
    rubric_14 = _write_rubric(
        tmp_path / "rubric_14.json",
        [
            ("Q1", 10, "K_QUADRATIC", "二次函数"),
            ("Q2", 5, "K_UNKNOWN", "陌生诊断词"),
        ],
    )
    with sqlite3.connect(grading_db_path) as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [(12, "S12", "张三", "九年级1班"), (15, "S15", "李四", "九年级1班")],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (?, ?, ?, '', 'completed', 0)
            """,
            [(12, "第一次考试", str(rubric_12)), (14, "当前考试", str(rubric_14))],
        )
        _insert_result(
            conn,
            session_id=12,
            student_id=12,
            paper_id=1201,
            result_id=12001,
            total_score=15,
            student_score=10,
            details=[("Q1", 8, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")],
        )
        _insert_result(
            conn,
            session_id=12,
            student_id=15,
            paper_id=1202,
            result_id=12002,
            total_score=15,
            student_score=9,
            details=[("Q1", 7, "K_QUADRATIC"), ("Q2", 2, "K_UNKNOWN")],
        )
        _insert_result(
            conn,
            session_id=14,
            student_id=12,
            paper_id=1401,
            result_id=14001,
            total_score=15,
            student_score=7,
            details=[("Q1", 6, "K_QUADRATIC"), ("Q2", 1, "K_UNKNOWN")],
        )
        conn.execute(
            """
            UPDATE session_details
            SET deduction_reason = '未作答'
            WHERE result_id = 14001 AND question_id = 'Q2'
            """
        )

    alignment = ConceptAlignmentService(question_bank_db_path)
    concept = alignment.create_concept("math.quadratic", "二次函数")
    alignment.confirm_mapping("grading_weak_point", "二次函数", concept.id, reviewed_by="teacher")
    return DiagnosisProfileService(grading_db_path, question_bank_db_path)


def test_current_exam_single_student_profile_uses_full_score_weighting(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_legacy_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(item for item in profile["students"][0]["weak_points"] if item["source_term"] == "二次函数")
    assert weak["mastery"] == pytest.approx(6 / 10)
    assert weak["source_question_refs"][0]["session_id"] == 14
    assert weak["eligible_for_recommendation"] is True


def test_diagnosis_exposes_display_value_separately_from_mapping_term(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_legacy_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(
        item
        for item in profile["students"][0]["weak_points"]
        if item["source_term"] == "二次函数"
    )
    assert weak["source_display"].endswith("二次函数")


def test_selected_students_and_manual_sessions_are_respected(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_legacy_profiles(
        scope={"mode": "selected", "student_ids": ["12", "15"]},
        exam_scope={"mode": "manual", "session_ids": [12, 14]},
    )

    assert {item["student_id"] for item in profile["students"]} == {"12", "15"}
    assert profile["exam_scope"]["session_ids"] == [12, 14]


def test_unmapped_terms_are_visible_and_not_eligible(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_legacy_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(item for item in profile["students"][0]["weak_points"] if item["source_term"] == "陌生诊断词")
    assert profile["unmapped_terms"] == ["陌生诊断词"]
    assert weak["eligible_for_recommendation"] is False
    assert weak["actionable_reasons"] == []


def test_diagnosis_reuses_legacy_numbered_teacher_confirmation(
    service: DiagnosisProfileService,
) -> None:
    concept = service.alignment.create_concept("math.legacy", "旧版确认知识点")
    service.alignment.confirm_mapping(
        "grading_weak_point",
        "K_UNKNOWN · 陌生诊断词",
        concept.id,
        reviewed_by="teacher",
    )

    profile = service.build_legacy_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    weak = next(
        item
        for item in profile["students"][0]["weak_points"]
        if item["source_term"] == "陌生诊断词"
    )
    assert weak["mapping_status"] == "confirmed"
    assert weak["concept_id"] == concept.id


def test_class_scope_selects_students_from_requested_class(
    service: DiagnosisProfileService,
) -> None:
    profile = service.build_legacy_profiles(
        scope={"mode": "class", "class_id": "九年级1班"},
        exam_scope={"mode": "cross_exam"},
    )

    assert {item["student_id"] for item in profile["students"]} == {"12", "15"}
    assert profile["exam_scope"]["session_ids"] == [12, 14]


def test_skill_mode_aggregates_two_sessions_by_same_measured_skill_id(
    service: DiagnosisProfileService,
) -> None:
    db_path = service.question_bank_db_path
    catalog = SkillCatalogService(db_path)
    measured = catalog.find_by_stable_key("math.function.quadratic.graph")
    supporting = catalog.find_by_stable_key("math.algebra.equation.quadratic_factor")
    links = SkillLinkService(db_path)
    for session_id in (12, 14):
        links.replace_assessment_links(
            str(session_id),
            "Q1",
            [
                ResolvedSkillLink(measured["id"], "measured"),
                ResolvedSkillLink(supporting["id"], "supporting"),
            ],
        )
    with connect_question_bank(db_path) as conn:
        conn.execute(
            """
            UPDATE skill_system_settings SET value = 'skill'
            WHERE key = 'recommendation_read_mode'
            """
        )
        conn.executemany(
            """
            INSERT INTO skill_resolution_conflicts (
                source_type, source_ref, raw_label, normalized_label, reason, state
            ) VALUES ('assessment_item', ?, '陌生诊断词', '陌生诊断词', '无法确定', 'open')
            """,
            [("12:Q2",), ("14:Q2",)],
        )

    profile = service.build_skill_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "manual", "session_ids": [12, 14]},
    )

    weak_points = profile["students"][0]["weak_points"]
    assert len(weak_points) == 1
    weak = weak_points[0]
    assert weak["skill_id"] == measured["id"]
    assert weak["skill_name"] == "二次函数图像与性质"
    assert weak["topic_name"] == "二次函数"
    assert weak["mastery"] == pytest.approx((8 + 6) / (10 + 10))
    assert weak["evidence_count"] == 2
    assert len(weak["source_question_refs"]) == 2
    assert "mapping_status" not in weak
    assert profile["confirmed_skill_ids"] == [measured["id"]]
    assert profile["unresolved_count"] == 2
    assert supporting["id"] not in profile["confirmed_skill_ids"]


def test_explicit_skill_profiles_ignore_legacy_mode_and_count_only_blocking_conflicts(
    service: DiagnosisProfileService,
) -> None:
    catalog = SkillCatalogService(service.question_bank_db_path)
    measured = catalog.find_by_stable_key("math.function.quadratic.graph")
    SkillLinkService(service.question_bank_db_path).replace_assessment_links(
        "14",
        "Q1",
        [ResolvedSkillLink(measured["id"], "measured")],
    )
    with connect_question_bank(service.question_bank_db_path) as conn:
        conn.execute(
            """
            UPDATE skill_system_settings SET value = 'legacy'
            WHERE key = 'recommendation_read_mode'
            """
        )
        conn.executemany(
            """
            INSERT INTO skill_resolution_conflicts (
                source_type, source_ref, raw_label, normalized_label, reason, state
            ) VALUES ('assessment_item', ?, ?, ?, '无法确定', 'open')
            """,
            [
                ("14:Q1", "已有主技能的附加词条", "已有主技能的附加词条"),
                ("14:Q2", "尚无主技能的词条", "尚无主技能的词条"),
            ],
        )

    profile = service.build_skill_profiles(
        scope={"mode": "student", "student_ids": ["12"]},
        exam_scope={"mode": "current", "session_ids": [14]},
    )

    assert profile["diagnosis_identity"] == "skill"
    assert profile["unresolved_count"] == 1
    assert profile["students"][0]["weak_points"][0]["skill_id"] == measured["id"]


def test_skill_evidence_obeys_skill_student_and_session_filters(
    service: DiagnosisProfileService,
) -> None:
    catalog = SkillCatalogService(service.question_bank_db_path)
    measured = catalog.find_by_stable_key("math.function.quadratic.graph")
    other = catalog.find_by_stable_key("math.algebra.equation.quadratic_factor")
    links = SkillLinkService(service.question_bank_db_path)
    for session_id in (12, 14):
        links.replace_assessment_links(
            str(session_id),
            "Q1",
            [ResolvedSkillLink(measured["id"], "measured")],
        )
        links.replace_assessment_links(
            str(session_id),
            "Q2",
            [ResolvedSkillLink(other["id"], "measured")],
        )

    rows = service.skill_evidence(
        skill_id=measured["id"],
        student_ids=["12"],
        session_ids=[14],
    )

    assert [(row["session_id"], row["student_id"], row["question_id"]) for row in rows] == [
        (14, 12, "Q1")
    ]
    assert rows[0]["score_awarded"] == 6
    assert rows[0]["max_score"] == 10
    assert rows[0]["full_score"] == 10


def test_active_assessment_evidence_returns_detail_level_scores(
    service: DiagnosisProfileService,
) -> None:
    rows = service.db.get_active_assessment_evidence(
        student_ids=["12"],
        session_ids=[14],
    )

    q1 = next(row for row in rows if row["question_id"] == "Q1")
    assert q1["student_id"] == 12
    assert q1["session_id"] == 14
    assert q1["score_awarded"] == 6
    assert q1["full_score"] == 10


def test_tag_diagnosis_reuses_borrowed_grading_and_question_bank_connections(
    service: DiagnosisProfileService,
) -> None:
    grading_conn = sqlite3.connect(service.db.db_path)
    grading_conn.row_factory = sqlite3.Row
    question_bank_conn = sqlite3.connect(service.question_bank_db_path)
    question_bank_conn.row_factory = sqlite3.Row
    try:
        question_bank_conn.execute(
            "INSERT INTO questions (id, question_number, question_text) VALUES (901, '1', '借用连接题目')"
        )
        question_bank_conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) "
            "VALUES (901, 'knowledge_point', '借用连接知识点')"
        )
        question_bank_conn.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, confidence, status, evidence_json
            ) VALUES ('14', 'Q1', 901, 'manual', 1.0, 'confirmed', '{}')
            """
        )
        borrowed_grading_db = DBManager(
            service.db.db_path,
            external_connection=grading_conn,
        )
        borrowed_service = DiagnosisProfileService(
            service.db.db_path,
            service.question_bank_db_path,
            grading_db=borrowed_grading_db,
            question_bank_connection=question_bank_conn,
        )

        profile = borrowed_service.build_tag_profiles(
            scope={"mode": "student", "student_ids": ["12"]},
            exam_scope={"mode": "current", "session_ids": [14]},
        )

        assert profile["students"][0]["weak_points"][0]["knowledge_point"] == "借用连接知识点"
        assert grading_conn.execute("SELECT 1").fetchone()[0] == 1
        assert question_bank_conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        question_bank_conn.rollback()
        question_bank_conn.close()
        grading_conn.close()


def _write_rubric(path: Path, questions: list[tuple[str, float, str, str]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": question_id,
                        "max_score": max_score,
                        "knowledge_id": knowledge_id,
                        "knowledge_name": knowledge_name,
                    }
                    for question_id, max_score, knowledge_id, knowledge_name in questions
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _insert_result(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    student_id: int,
    paper_id: int,
    result_id: int,
    total_score: float,
    student_score: float,
    details: list[tuple[str, float, str]],
) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, session_id, front_image, back_image, student_id, match_status, processing_status
        ) VALUES (?, ?, '', '', ?, 'matched', 'completed')
        """,
        (paper_id, session_id, student_id),
    )
    conn.execute(
        """
        INSERT INTO session_results (
            id, session_id, student_id, paper_id, total_score, student_score,
            needs_human_review, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, 0, '{}')
        """,
        (result_id, session_id, student_id, paper_id, total_score, student_score),
    )
    conn.executemany(
        """
        INSERT INTO session_details (
            result_id, question_id, score_awarded, deduction_reason, knowledge_id, knowledge_ids
        ) VALUES (?, ?, ?, '需要巩固', ?, ?)
        """,
        [
            (result_id, question_id, score, knowledge_id, json.dumps([knowledge_id]))
            for question_id, score, knowledge_id in details
        ],
    )
