"""Formula boundaries and knowledge/mastery semantics in offline reports."""

import json
from types import SimpleNamespace


from tests.test_analysis_report import analysis_db  # noqa: F401


def test_graph_uses_current_mastery_separately_from_exam_scores(analysis_db):
    from analysis_report_exporter import _personal_knowledge_view, _class_knowledge_view, _knowledge_view_html
    from backend.session_analysis import assemble_session_analysis

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
        "topic": {"mastery": 0.91, "evidence_count": 4, "tier": "insufficient", "observation_count": 4, "full_correct_count": 4, "interval_low": .45, "interval_high": .99},
        "sk_test": {"mastery": 0.42, "evidence_count": 2, "tier": "weak", "observation_count": 2},
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
    assert view["nodes"][0]["tier"] == "insufficient"
    assert view["nodes"][0]["interval_low"] == .45
    html = _knowledge_view_html(view)
    assert 'kn-node kn-missing" data-key="topic"' in html
    assert "明显薄弱" in html and "还不稳" in html
    assert "需巩固" not in html
    class_view = _class_knowledge_view(data)
    topic = next(n for n in class_view["nodes"] if n["key"] == "topic")
    assert topic["mastery"] == .91
    assert topic["coverage"] == 1
    assert topic["distribution"] == {"weak": 0, "unsteady": 0, "stable": 0, "insufficient": 1}
    assert topic["missing_count"] == len(data.students)-1
    data.knowledge_structure["catalog"] = [
        {"knowledge_key": "topic", "parent_knowledge_key": "section_ref", "knowledge_point": "根式", "node_kind": "topic"},
        {"knowledge_key": "section_ref", "knowledge_point": "本节", "node_kind": "section"},
    ]
    student.knowledge_mastery["section_ref"] = {"mastery": .9, "tier": "stable", "interval_low": .8, "interval_high": .95}
    referenced = _personal_knowledge_view(data, student)
    assert referenced["nodes"][0]["parent_references"] == [{"label": "本节", "mastery": .9, "tier": "stable", "interval_low": .8, "interval_high": .95}]


# ---------------------------------------------------------------------------
# 家长版一页式（v3）：历次成绩、跟进卡题号、降级块与对外可见文本
# ---------------------------------------------------------------------------

import sqlite3
from tests.test_analysis_report import COMPLETE_RAW_JSON, PERSONAL_NARRATIVE
