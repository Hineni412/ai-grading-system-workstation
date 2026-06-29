from __future__ import annotations

import sqlite3
from pathlib import Path

from question_bank.database.schema import connect
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.question_service import QuestionService
from question_bank.services.skill_resolution_service import SkillResolutionService


def _analysis(
    measured: list[str],
    *,
    supporting: list[str] | None = None,
    knowledge: str = "线与角",
) -> TagAnalysis:
    return TagAnalysis.from_dict(
        {
            "knowledge_points": [knowledge],
            "method_tags": ["几何推理"],
            "ability_tags": ["推理能力"],
            "math_model_tags": [],
            "difficulty": 4,
            "error_prone_points": ["图形关系识别错误"],
            "prerequisite_points": [],
            "textbook_chapter": "八年级上册",
            "teaching_stage": "巩固",
            "suitable_student_level": "基础巩固",
            "reason": "测试具体训练技能。",
            "confidence": 0.96,
            "measured_skills": measured,
            "supporting_skills": supporting or [],
        }
    )


def test_save_tag_analysis_writes_measured_and_supporting_skill_links(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_text="利用角平分线性质计算角度。",
            answer_text="由角平分线定义可得。",
        )
    )

    assert service.save_tag_analysis(
        question_id,
        _analysis(["角平分线性质"], supporting=["角平分线尺规作图"]),
        resolve_skills=True,
    )

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT l.role, s.name
            FROM question_skill_links l JOIN skills s ON s.id = l.skill_id
            WHERE l.question_id = ? ORDER BY l.role
            """,
            (question_id,),
        ).fetchall()
    assert {(row["role"], row["name"]) for row in rows} == {
        ("measured", "角平分线性质"),
        ("supporting", "角平分线尺规作图"),
    }


def test_retagging_conflict_clears_old_links_but_keeps_raw_tags(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(question_number="1", question_text="完成指定作图。", answer_text="略")
    )
    service.save_tag_analysis(question_id, _analysis(["角平分线性质"]), resolve_skills=True)

    assert service.save_tag_analysis(
        question_id,
        _analysis(["未登记的校本作法"], knowledge="尺规作图"),
        resolve_skills=True,
    )

    with connect(db_path) as conn:
        link_count = conn.execute(
            "SELECT COUNT(*) FROM question_skill_links WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
        tags = conn.execute(
            "SELECT tag_type, tag_value FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchall()
        conflicts = conn.execute(
            """
            SELECT COUNT(*) FROM skill_resolution_conflicts
            WHERE source_type = 'question_bank_item'
              AND source_ref = ? AND state = 'open'
            """,
            (str(question_id),),
        ).fetchone()[0]
    assert link_count == 0
    assert ("knowledge_point", "尺规作图") in {
        (row["tag_type"], row["tag_value"]) for row in tags
    }
    assert conflicts == 1


class _FailingRanker:
    def rank(self, request, candidates):
        raise RuntimeError("offline")


class _UnexpectedResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, request):
        self.calls += 1
        raise AssertionError("default question tag save must not resolve legacy skills")


def test_save_tag_analysis_defaults_to_raw_tags_without_skill_resolution(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(question_number="1", question_text="角平分线性质", answer_text="略")
    )
    resolver = _UnexpectedResolver()

    assert service.save_tag_analysis(
        question_id,
        _analysis(["角平分线性质"], supporting=["角平分线尺规作图"]),
        skill_resolver=resolver,
    )

    with sqlite3.connect(db_path) as conn:
        raw_skill_tags = conn.execute(
            """
            SELECT tag_type, tag_value FROM question_tags
            WHERE question_id = ?
              AND tag_type IN ('measured_skill_name', 'supporting_skill_name')
            ORDER BY tag_type, tag_value
            """,
            (question_id,),
        ).fetchall()
        link_count = conn.execute(
            "SELECT COUNT(*) FROM question_skill_links WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]

    assert resolver.calls == 0
    assert raw_skill_tags == [
        ("measured_skill_name", "角平分线性质"),
        ("supporting_skill_name", "角平分线尺规作图"),
    ]
    assert link_count == 0


def test_ai_outage_does_not_block_deterministic_question_skill(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(question_number="1", question_text="角平分线性质", answer_text="略")
    )
    resolver = SkillResolutionService(db_path, context_ranker=_FailingRanker())

    assert service.save_tag_analysis(
        question_id,
        _analysis(["角平分线性质"]),
        skill_resolver=resolver,
        resolve_skills=True,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM question_skill_links WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0] == 1


def test_two_questions_reuse_same_resolved_skill_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_ids = [
        service.add_question(QuestionCreate(question_number=str(index), question_text="角平分线变式", answer_text="略"))
        for index in (1, 2)
    ]
    for question_id in question_ids:
        service.save_tag_analysis(
            question_id,
            _analysis(["角平分线的性质"]),
            resolve_skills=True,
        )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT question_id, skill_id FROM question_skill_links ORDER BY question_id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] == rows[1][1]
