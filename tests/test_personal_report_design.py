"""Formula boundaries and knowledge/mastery semantics in offline reports."""

from copy import deepcopy
import pytest

from tests.test_analysis_report import analysis_db  # noqa: F401


def test_graph_uses_current_mastery_separately_from_exam_scores(analysis_db):
    from backend.reporting.analysis_report_exporter import _personal_knowledge_view, _class_knowledge_view, _knowledge_view_html
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
        "topic": {"mastery": 0.91, "evidence_count": 4, "tier": "insufficient", "observation_count": 4,
                  "full_correct_count": 4, "interval_low": .45, "interval_high": .99},
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


def test_exam_points_require_specific_evidence_for_losses(analysis_db):
    from backend.reporting.analysis_report_exporter import _personal_exam_points, _personal_knowledge_view
    from backend.session_analysis import assemble_session_analysis

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    lost = next(r for r in student.records if r.lost)
    data.knowledge_backfill = {lost.question_id: [
        {"stable_key": key, "label": label, "node_kind": "skill"}
        for key, label in (("sk_transform", "等式变形"), ("sk_calculate", "整数计算"), ("sk_unknown", "书写依据"))
    ]}
    student.knowledge_mastery = {
        "sk_transform": {"source_question_refs": [
            {"session_id": session, "question_id": lost.question_id,
             "assessment": {"point_observations": [{"stable_key": "sk_transform", "achieved": True}]}},
        ]},
        "sk_calculate": {"source_question_refs": [
            {"session_id": session, "question_id": lost.question_id,
             "assessment": {"point_observations": [{"stable_key": "sk_calculate", "achieved": False}]}},
            {"session_id": 99, "session_name": "以前测试", "question_id": "Q3(P2)",
             "assessment": {"point_observations": [{"stable_key": "sk_calculate", "achieved": False}]}},
        ]},
        "sk_unknown": {"source_question_refs": [
            {"session_id": session, "question_id": lost.question_id,
             "assessment": {"point_observations": [{"stable_key": "sk_unknown", "achieved": None}]}},
            {"session_id": 99, "session_name": "以前测试", "question_id": "Q3(P2)",
             "score_awarded": 0, "full_score": 10},
        ]},
    }
    before = [(r.score, r.max_score) for r in student.records]
    points = _personal_exam_points(_personal_knowledge_view(data, student), student,
        {q.question_id: q for q in data.questions}, session, {99})
    by_key = {p["key"]: p for p in points}
    assert by_key["sk_transform"]["status"] == "good"
    assert by_key["sk_calculate"]["status"] == "bad"
    assert by_key["sk_unknown"]["status"] == "unknown"
    assert by_key["sk_calculate"]["recur"][0]["question_id"] == "Q3(P2)"
    assert by_key["sk_unknown"]["recur"] == []
    assert before == [(r.score, r.max_score) for r in student.records]


def test_followups_show_actions_and_overlapping_question_losses(analysis_db):
    from backend.reporting.analysis_report_exporter import _render_personal_html
    from backend.session_analysis import assemble_session_analysis
    from tests.test_analysis_report import PERSONAL_NARRATIVE

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    narrative = deepcopy(PERSONAL_NARRATIVE)
    narrative["problems"] = [
        {"title": "平方根、立方根概念与根号内取值条件", "parent_summary": "知道结论，但还没有说清理由", "detail": "第2题需要补齐依据", "question_ids": ["Q2"]},
        {"title": "证明的表达", "detail": "第2题需要写清步骤", "question_ids": ["Q2"]},
    ]
    narrative["suggestions"] = [
        {"first_action": "先说清第2题的理由", "detail": "先订正第2题的证明", "timeframe": "本周", "completion_check": "独立说出每步依据", "parent_help": "听学生说明即可"},
        {"detail": "重做第2题", "timeframe": "长期"},
    ]
    text = _render_personal_html(data, student, narrative, {}, history=[
        {"session_id": 99, "name": "上次测试", "score": 80, "full": 100, "avg": 65},
        {"session_id": session, "name": "本次测试", "score": 90, "full": 100, "avg": 70},
    ])
    assert text.count('data-open-q="Q2"') == 2
    assert text.count("关联题目失分 10 分") == 2
    assert "各项关联失分不能相加" in text
    assert "发现：" in text and "先做：" in text and "检查：" in text
    assert "知道结论，但还没有说清理由" in text
    assert "平方根、立方根概念与根号内取值条件" in text
    assert "先说清第2题的理由" in text and "先订正第2题的证明" in text
    assert "独立说出每步依据" in text and "听学生说明即可" in text
    assert '<span class="when">本周</span>' in text
    assert '<span class="when">长期</span>' in text
    assert text.index('<h2>历次成绩') < text.index('<h2>本卷答题一览') < text.index('<h2>这次重点跟进')


def test_personal_payload_exposes_exact_knowledge_focus_without_inventing_weaknesses(analysis_db):
    from backend.reporting.analysis_report_exporter import build_personal_payload
    from backend.session_analysis import assemble_session_analysis

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    lost = next(record for record in student.records if record.lost)
    data.knowledge_backfill = {
        "Q2": [{"path": "本册｜本章｜平方根概念理解", "label": "平方根概念理解"}],
        "Q2(P1)": [
            {"path": "本册｜本章｜求一个数的平方根", "label": "求一个数的平方根"},
            {"path": "本册｜本章｜技能·求平方根", "label": "技能·求平方根", "stable_key": "sk_root"},
            {"path": "本册｜本章｜技能·求平方根", "label": "技能·求平方根", "stable_key": "sk_root"},
        ],
        "Q2(P2)": [],
    }
    lost.question_id = "Q2(P1)"
    before = [(record.score, record.max_score) for record in student.records]
    item = next(q for q in build_personal_payload(data, student)["questions"] if q["lost"])
    assert item["knowledge_focus"] == {"topics": ["求一个数的平方根"], "skills": ["求平方根"]}
    assert item["direct_knowledge"] == data.knowledge_backfill["Q2(P1)"]
    lost.question_id = "Q2(P2)"
    item = next(q for q in build_personal_payload(data, student)["questions"] if q["lost"])
    assert item["direct_knowledge"] == []
    assert item["knowledge_focus"] == {"topics": [], "skills": []}
    assert before == [(record.score, record.max_score) for record in student.records]


def test_history_distinguishes_same_pattern_and_same_category(analysis_db, monkeypatch):
    from backend.reporting.analysis_report_exporter import _load_personal_error_histories, _render_personal_html
    from backend.session_analysis import assemble_session_analysis
    from tests.test_analysis_report import PERSONAL_NARRATIVE

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    history = {student.student_id: [{"session_id": 99, "name": "以前测试"}]}
    calls = []

    def records(_db, sid, _reports, **_kwargs):
        calls.append(sid)
        return {student.student_id: {"Q3(P2)": [{"category": "计算与化简", "pattern": "约分遗漏"}]}}

    monkeypatch.setattr("backend.class_analysis.session_error_records", records)
    index = _load_personal_error_histories(db, history, session, root / "reports", root)
    assert calls == [99]
    errors = {"Q2": [{"category": "计算与化简", "pattern": "移项忘记变号"}]}
    text = _render_personal_html(data, student, PERSONAL_NARRATIVE, {}, error_map=errors,
        error_history=index[student.student_id])
    assert "同类问题曾出现" in text
    assert "同一错法再次出现" not in text
    assert "以前测试 · 第3(2)题 · 约分遗漏" in text
    assert "不能据此认定是同一错法" in text
    errors["Q2"][0]["pattern"] = "约分遗漏"
    text = _render_personal_html(data, student, PERSONAL_NARRATIVE, {}, error_map=errors,
        error_history=index[student.student_id])
    assert "同一错法再次出现" in text
    assert '<span class="recur">同类问题曾出现</span>' not in text


def test_partial_narrative_uses_data_followups_without_raw_grading_reason(analysis_db):
    from backend.reporting.analysis_report_exporter import _render_personal_html
    from backend.session_analysis import assemble_session_analysis

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    partial = {"question_analyses": [{"question_id": "Q2", "feedback": "应写清证明依据"}]}
    text = _render_personal_html(data, student, partial, {})
    assert '<div class="dcard">' in text
    assert 'data-open-q="Q2"' in text
    assert "关联题目失分 10 分" in text
    assert "缺关键步骤" not in text
    assert "应写清证明依据" in text
    # A missing or malformed paired suggestion must still leave a usable action.
    partial["problems"] = [{"title": "证明依据", "detail": "第2题需订正", "question_ids": ["Q2"]}]
    partial["suggestions"] = [None]
    text = _render_personal_html(data, student, partial, {})
    assert "先做：" in text and "检查：" in text


@pytest.mark.parametrize("student_index,previous_score,previous_rank,previous_full,expected", [
    (0, 80, 4, 100, ("+10分", "名次上升3名")),
    (1, 60, 1, 100, ("−10分", "名次下降1名")),
    (0, 90, 1, 100, ("分数持平", "名次持平")),
    (0, 80, None, 100, ("+10分", "名次暂无对比")),
    (0, 80, 4, 120, ("分数不直接比", "名次上升3名")),
])
def test_score_comparison_reports_correct_direction_and_equal_full_scores(
    analysis_db, student_index, previous_score, previous_rank, previous_full, expected,
):
    from bs4 import BeautifulSoup
    from backend.reporting.analysis_report_exporter import _render_personal_html
    from backend.session_analysis import assemble_session_analysis

    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[student_index]
    before = [(record.score, record.max_score) for record in student.records]
    text = _render_personal_html(data, student, None, {}, history=[
        {"session_id": 99, "name": "以前测试", "score": previous_score,
         "rank": previous_rank, "full": previous_full, "present": data.present + 1},
    ])
    comparison = BeautifulSoup(text, "html.parser").select_one(".cell.change").get_text()
    assert all(value in comparison for value in expected)
    assert "参考人数" in comparison if previous_rank else "参考人数" not in comparison
    assert "40≤分数＜60" in text and "85≤分数≤100" in text
    assert before == [(record.score, record.max_score) for record in student.records]

    no_history = BeautifulSoup(_render_personal_html(data, student, None, {}), "html.parser")
    assert "暂无对比" in no_history.select_one(".cell.change").get_text()
