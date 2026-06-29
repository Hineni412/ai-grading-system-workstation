from __future__ import annotations

from pathlib import Path

from question_bank.database.schema import connect, initialize_database


def _rubric() -> dict:
    return {
        "grade": "八年级",
        "questions": [
            {
                "question_id": "Q1",
                "stem_summary": "证明三角形全等后计算角度",
                "knowledge_id": "G8_01",
                "knowledge_name": "全等三角形判定",
                "parts": [
                    {
                        "part_id": "Q1.1",
                        "knowledge_id": "G8_01",
                        "knowledge_name": "全等三角形判定",
                        "steps": [{"core_goal": "使用AAS判定全等"}],
                    },
                    {
                        "part_id": "Q1.2",
                        "knowledge_points": [
                            {"knowledge_id": "G8_02", "knowledge_name": "角平分线性质"},
                            {"knowledge_id": "G8_03", "knowledge_name": "三角形面积计算"},
                        ],
                        "prerequisite_points": ["三角形内角和计算"],
                        "steps": [{"core_goal": "利用面积关系求角"}],
                    },
                ],
            }
        ],
    }


def test_iter_rubric_skill_requests_uses_part_refs_and_multiple_measured_skills() -> None:
    from session_manager import iter_rubric_skill_requests

    rows = list(iter_rubric_skill_requests(_rubric(), grading_session_id="12"))

    assert [(source_ref, role.value, request.raw_label) for source_ref, role, request in rows] == [
        ("Q1.1", "measured", "全等三角形判定"),
        ("Q1.2", "measured", "角平分线性质"),
        ("Q1.2", "measured", "三角形面积计算"),
        ("Q1.2", "supporting", "三角形内角和计算"),
    ]
    assert rows[0][2].source_ref == "12:Q1.1"
    assert rows[0][2].stable_key_hint == "g8_01"
    assert "使用AAS判定全等" in rows[0][2].rubric_text


def test_effective_rubric_items_use_parts_or_question_fallback() -> None:
    from session_manager import iter_effective_rubric_items

    rows = list(iter_effective_rubric_items(_rubric()))
    assert [item_ref for item_ref, _question, _item in rows] == ["Q1.1", "Q1.2"]

    single = {"questions": [{"question_id": "Q2", "knowledge_name": "一次函数"}]}
    assert [
        item_ref
        for item_ref, _question, _item in iter_effective_rubric_items(single)
    ] == ["Q2"]


def test_resolve_rubric_links_every_effective_part(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    summary = SkillLinkService(db_path).resolve_rubric("12", _rubric())

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.source_question_id, a.role, a.raw_knowledge_id, s.name
            FROM assessment_item_skills a JOIN skills s ON s.id = a.skill_id
            WHERE a.grading_session_id = '12'
            ORDER BY a.source_question_id, a.role, s.name
            """
        ).fetchall()
    assert summary == {"items": 2, "resolved_items": 2, "conflicts": 0, "skills": 4}
    assert {(row["source_question_id"], row["role"], row["name"]) for row in rows} == {
        ("Q1.1", "measured", "全等三角形判定"),
        ("Q1.2", "measured", "角平分线性质"),
        ("Q1.2", "measured", "三角形面积计算"),
        ("Q1.2", "supporting", "三角形内角和计算"),
    }
    assert next(row for row in rows if row["source_question_id"] == "Q1.1")["raw_knowledge_id"] == "g8_01"


def test_rubric_conflict_does_not_raise_or_create_eligible_link(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    rubric = {
        "questions": [
            {
                "question_id": "Q9",
                "knowledge_id": "G7_99",
                "knowledge_name": "本校未知画法",
                "stem_summary": "按要求完成图形",
            }
        ]
    }

    summary = SkillLinkService(db_path).resolve_rubric("9", rubric)

    with connect(db_path) as conn:
        links = conn.execute(
            "SELECT COUNT(*) FROM assessment_item_skills WHERE grading_session_id = '9'"
        ).fetchone()[0]
        conflicts = conn.execute(
            """
            SELECT COUNT(*) FROM skill_resolution_conflicts
            WHERE source_type = 'assessment_item' AND source_ref = '9:Q9' AND state = 'open'
            """
        ).fetchone()[0]
    assert summary["conflicts"] == 1
    assert links == 0
    assert conflicts == 1


def test_backfill_preserves_existing_measured_assessment_link(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        skill_id = int(
            conn.execute("SELECT id FROM skills WHERE name = '角平分线性质'").fetchone()[0]
        )
        conn.execute(
            """
            INSERT INTO assessment_item_skills (
                grading_session_id, source_question_id, skill_id, role,
                source, confidence, evidence_json, status
            ) VALUES ('9', 'Q9', ?, 'measured', 'admin_resolution', 1.0, '{}', 'resolved')
            """,
            (skill_id,),
        )
    rubric = {
        "questions": [
            {
                "question_id": "Q9",
                "knowledge_name": "本校未知画法",
                "stem_summary": "按要求完成图形",
            }
        ]
    }

    summary = SkillLinkService(db_path).resolve_rubric(
        "9",
        rubric,
        preserve_existing_measured=True,
    )

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT skill_id, source FROM assessment_item_skills
            WHERE grading_session_id = '9' AND source_question_id = 'Q9'
            """
        ).fetchone()
        conflict_count = conn.execute(
            "SELECT COUNT(*) FROM skill_resolution_conflicts WHERE source_ref = '9:Q9'"
        ).fetchone()[0]
    assert summary == {"items": 1, "resolved_items": 1, "conflicts": 0, "skills": 1}
    assert tuple(row) == (skill_id, "admin_resolution")
    assert conflict_count == 0


def test_web_app_does_not_sync_legacy_skill_links_on_session_save() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")

    assert "def _sync_session_skill_links" not in source
    assert "_sync_session_skill_links(created_session_id" not in source
    assert "_sync_session_skill_links(selected_session_id" not in source
    assert "SkillLinkService" not in source
    assert "build_tag_profiles" in source


def test_rubric_prompt_describes_knowledge_id_as_local_source_reference() -> None:
    source = Path("session_manager.py").read_text(encoding="utf-8")

    assert "knowledge_id 仅是本试卷内的来源编号" in source
