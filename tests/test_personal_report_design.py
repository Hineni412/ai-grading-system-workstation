"""Formula boundaries and knowledge/mastery semantics in offline reports."""

import json
from types import SimpleNamespace


from tests.test_analysis_report import analysis_db  # noqa: F401


def test_graph_uses_current_mastery_separately_from_exam_scores(analysis_db):
    from analysis_report_exporter import (
        assemble_session_analysis,
        _personal_knowledge_view,
    )

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    record = next(r for r in student.records if r.lost)
    data.knowledge_backfill = {
        record.question_id: [
            {"stable_key": "topic", "path": "本册｜本章｜本节｜根式", "label": "根式"},
            {
                "stable_key": "sk_test",
                "path": "本册｜本章｜本节｜技能·化简",
                "label": "技能·化简",
            },
            {
                "stable_key": "sk_missing",
                "path": "本册｜本章｜本节｜技能·比较",
                "label": "技能·比较",
            },
        ]
    }
    student.knowledge_mastery = {
        "topic": {"mastery": 0.91, "evidence_count": 4},
        "sk_test": {"mastery": 0.42, "evidence_count": 2},
    }
    data.knowledge_structure = {
        "as_of": "2026-09-22 20:00",
        "associations": [
            {
                "topic_key": "topic",
                "skill_key": "sk_test",
                "question_count": 3,
                "same_part_question_count": 2,
                "basis": "same_part",
            },
            {
                "topic_key": "topic",
                "skill_key": "outside",
                "question_count": 9,
                "same_part_question_count": 0,
                "basis": "question_cooccurrence",
            },
        ],
    }
    before = [(r.score, r.max_score) for r in student.records]
    view = _personal_knowledge_view(data, student)
    assert [n["mastery"] for n in view["nodes"]] == [0.91, 0.42, None]
    assert len(view["edges"]) == 1
    assert all(
        n["score"] == record.score and n["full"] == record.max_score
        for n in view["nodes"]
    )
    assert before == [(r.score, r.max_score) for r in student.records]


# ---------------------------------------------------------------------------
# 家长版一页式（v3）：历次成绩、跟进卡题号、降级块与对外可见文本
# ---------------------------------------------------------------------------

import sqlite3
from tests.test_analysis_report import COMPLETE_RAW_JSON, PERSONAL_NARRATIVE
