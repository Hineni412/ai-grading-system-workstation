from __future__ import annotations

from collections import Counter
import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.recommendation.training_plan import DEFAULT_STAGE_RATIOS
from question_bank.services.concept_alignment_service import ConceptAlignmentService
from question_bank.services.source_question_link_service import SourceQuestionLinkService


@pytest.fixture
def practice_system(tmp_path: Path) -> tuple[PracticePlanService, dict]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    alignment = ConceptAlignmentService(db_path)
    direct = alignment.create_concept("math.quadratic", "二次函数")
    prerequisite = alignment.create_concept("math.quadratic_equation", "一元二次方程")
    transfer = alignment.create_concept("math.function_transfer", "函数综合")
    alignment.create_relation(direct.id, prerequisite.id, "prerequisite", weight=0.9)
    alignment.create_relation(direct.id, transfer.id, "related", weight=0.8)
    alignment.confirm_mapping("question_tag", "二次函数", direct.id, reviewed_by="teacher")
    alignment.confirm_mapping("question_tag", "一元二次方程", prerequisite.id, reviewed_by="teacher")
    alignment.confirm_mapping("question_tag", "函数综合", transfer.id, reviewed_by="teacher")

    with connect(db_path) as conn:
        direct_texts = [
            "根据二次函数解析式求图象的对称轴。",
            "利用顶点式判断二次函数的最大值。",
            "求抛物线与 x 轴两个交点的坐标。",
            "研究图象平移后二次函数解析式的变化。",
            "根据开口方向确定参数的取值范围。",
            "建立二次函数模型解决商品利润问题。",
        ]
        for index, (question_id, text) in enumerate(zip(range(210, 216), direct_texts), start=1):
            _insert_question(
                conn,
                question_id=question_id,
                title=f"直接补缺来源{index}",
                text=text,
                knowledge="二次函数",
                method=f"直接方法{index}",
                difficulty=str(4 + index % 3),
            )
        prerequisite_texts = [
            "使用因式分解法求一元二次方程的根。",
            "使用配方法解一元二次方程。",
            "根据判别式判断方程实数根的个数。",
        ]
        for index, (question_id, text) in enumerate(zip(range(301, 304), prerequisite_texts), start=1):
            _insert_question(
                conn,
                question_id=question_id,
                title=f"前置训练来源{index}",
                text=text,
                knowledge="一元二次方程",
                method=f"前置方法{index}",
                difficulty=str(1 + index),
            )
        _insert_question(
            conn,
            question_id=305,
            title="迁移训练来源",
            text="在新情境中综合运用函数知识解决问题。",
            knowledge="函数综合",
            method="建模迁移",
            difficulty=None,
        )
        _insert_question(
            conn,
            question_id=201,
            title="当前考试原题",
            text="已知二次函数 y=x²-2x-3，求其顶点坐标。",
            knowledge="二次函数",
            method="原题方法",
            difficulty="5",
        )
        _insert_question(
            conn,
            question_id=202,
            title="当前考试近重复题",
            text="已知二次函数 y=x²-2x-3，请求它的顶点坐标。",
            knowledge="二次函数",
            method="原题方法",
            difficulty="5",
        )

    SourceQuestionLinkService(db_path).confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=201,
        link_method="exact_text",
    )
    diagnosis_profile = {
        "student_id": "12",
        "student_name": "张三",
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "weak_points": [
            {
                "source_term": "二次函数",
                "concept_id": direct.id,
                "concept_name": "二次函数",
                "mapping_status": "confirmed",
                "eligible_for_recommendation": True,
                "mastery": 0.4,
            }
        ],
    }
    return PracticePlanService(db_path), diagnosis_profile


def test_default_ten_question_plan_uses_agreed_stage_mix(
    practice_system: tuple[PracticePlanService, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.recommendation import practice_plan_service as practice_plan_module

    service, diagnosis_profile = practice_system
    initialize_calls: list[Path] = []
    original_initialize = practice_plan_module.initialize_database

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(path)
        original_initialize(path)

    monkeypatch.setattr(practice_plan_module, "initialize_database", tracking_initialize)

    plan = service.generate_variant(diagnosis_profile, question_count=10)

    counts = Counter(item["stage"] for item in plan["items"])
    assert counts == {"direct": 6, "prerequisite": 3, "transfer": 1}
    assert 201 not in {item["question_id"] for item in plan["items"]}
    assert 202 not in {item["question_id"] for item in plan["items"]}
    assert initialize_calls == [service.db_path]


def test_default_stage_mix_is_teacher_friendly_sixty_thirty_ten() -> None:
    assert DEFAULT_STAGE_RATIOS == {
        "direct": 0.60,
        "prerequisite": 0.30,
        "transfer": 0.10,
    }


def test_current_exam_original_and_near_duplicate_are_excluded(
    practice_system: tuple[PracticePlanService, dict],
) -> None:
    service, diagnosis_profile = practice_system

    plan = service.generate_variant(
        diagnosis_profile,
        question_count=10,
        exclude_question_ids={201},
    )

    assert 201 not in {item["question_id"] for item in plan["items"]}
    assert 202 not in {item["question_id"] for item in plan["items"]}
    assert plan["dedupe_summary"]["removed_count"] >= 2


def test_missing_difficulty_is_allowed_with_warning(
    practice_system: tuple[PracticePlanService, dict],
) -> None:
    service, diagnosis_profile = practice_system

    plan = service.generate_variant(diagnosis_profile, question_count=8)

    item = next(item for item in plan["items"] if item["question_id"] == 305)
    assert "候选题缺少难度标签" in item["warnings"]


def test_shortage_is_reported_instead_of_unaligned_fill(
    practice_system: tuple[PracticePlanService, dict],
) -> None:
    service, diagnosis_profile = practice_system
    with connect(service.db_path) as conn:
        conn.execute("UPDATE questions SET is_deleted = 1 WHERE id IN (213, 214, 215)")

    plan = service.generate_variant(
        diagnosis_profile,
        question_count=10,
        exclude_question_ids={201},
    )

    assert len(plan["items"]) == 7
    direct_shortage = next(item for item in plan["shortages"] if item["stage"] == "direct")
    assert direct_shortage["missing_count"] == 3


def test_broad_only_candidate_requires_explicit_advanced_fallback(
    practice_system: tuple[PracticePlanService, dict],
) -> None:
    service, diagnosis_profile = practice_system
    target_concept_id = int(diagnosis_profile["weak_points"][0]["concept_id"])
    alignment = ConceptAlignmentService(service.db_path)
    alignment.confirm_mapping(
        "question_tag",
        "函数图像平移",
        target_concept_id,
        reviewed_by="teacher",
    )
    with connect(service.db_path) as conn:
        conn.execute(
            "UPDATE questions SET is_deleted = 1 WHERE id BETWEEN 210 AND 215"
        )
        _insert_question(
            conn,
            question_id=450,
            title="只有大类相同",
            text="完成函数图像的平移操作。",
            knowledge="函数图像平移",
            method="图像平移",
            difficulty="5",
        )

    strict = service.generate_variant(
        diagnosis_profile,
        question_count=8,
        exclude_question_ids={201},
    )
    fallback = service.generate_variant(
        diagnosis_profile,
        question_count=8,
        exclude_question_ids={201},
        allow_broad_fallback=True,
    )

    assert 450 not in {item["question_id"] for item in strict["items"]}
    fallback_item = next(
        item for item in fallback["items"] if item["question_id"] == 450
    )
    assert "仅按标准知识点大类补足" in fallback_item["warnings"]


def test_question_tag_plan_reuses_borrowed_connection_for_all_reads(
    practice_system: tuple[PracticePlanService, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.recommendation import practice_plan_service as practice_plan_module

    legacy_service, _diagnosis_profile = practice_system
    borrowed = sqlite3.connect(legacy_service.db_path)
    borrowed.row_factory = sqlite3.Row
    try:
        borrowed.execute(
            """
            INSERT INTO papers (
                id, title, source_file, exam_type, grade, semester, import_status
            ) VALUES (999, '借用连接期末', 'borrowed.docx', '期末', '九年级', '下学期', 'ready')
            """
        )
        borrowed.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (999, 999, '1', '解答题', '未提交的二次函数题', '5')
            """
        )
        borrowed.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (999, "knowledge_point", "二次函数"),
                (999, "method", "直接方法1"),
            ],
        )
        borrowed.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, confidence, status, evidence_json
            ) VALUES ('14', 'Q2', 210, 'manual', 1.0, 'confirmed', '{}')
            """
        )
        borrowed.execute("UPDATE questions SET is_deleted = 1 WHERE id NOT IN (210, 999)")
        borrowed.execute("PRAGMA query_only = ON")
        monkeypatch.setattr(
            practice_plan_module,
            "initialize_database",
            lambda _path: pytest.fail("borrowed question-tag plan must not initialize the database"),
        )
        service = PracticePlanService(
            legacy_service.db_path,
            external_connection=borrowed,
        )
        profile = {
            "diagnosis_identity": "question_tag",
            "student_id": "12",
            "exam_scope": {"mode": "current", "session_ids": [14]},
            "weak_points": [
                {
                    "knowledge_point": "二次函数",
                    "mastery": 0.4,
                    "tag_context": {"method": ["直接方法1"]},
                }
            ],
        }

        plan = service.generate_variant(profile, question_count=8)

        selected_ids = {item["question_id"] for item in plan["items"]}
        assert 210 not in selected_ids
        assert selected_ids == {999}
        assert plan["items"][0]["frequency"]["available"] is True
        assert borrowed.execute("SELECT 1").fetchone()[0] == 1
    finally:
        borrowed.rollback()
        borrowed.close()


def _insert_question(
    conn,
    *,
    question_id: int,
    title: str,
    text: str,
    knowledge: str,
    method: str,
    difficulty: str | None,
) -> None:
    paper_id = question_id
    conn.execute(
        """
        INSERT INTO papers (id, title, source_file, exam_type, grade, import_status)
        VALUES (?, ?, ?, '同步练习', '九年级', 'ready')
        """,
        (paper_id, title, f"{title}.docx"),
    )
    conn.execute(
        """
        INSERT INTO questions (
            id, paper_id, question_number, question_type, question_text, difficulty
        ) VALUES (?, ?, '1', '解答题', ?, ?)
        """,
        (question_id, paper_id, text, difficulty),
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
