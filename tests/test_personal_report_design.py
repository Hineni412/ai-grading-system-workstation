"""Formula boundaries and knowledge/mastery semantics in offline reports."""
import io
import json
from types import SimpleNamespace

from bs4 import BeautifulSoup

from tests.test_analysis_report import analysis_db  # noqa: F401


def test_authored_math_delimiters_keep_whole_formulas():
    from analysis_report_exporter import _report_inline_math
    source = r'结果$\sqrt{3}-1$，\(\frac{a+b}{c}\)。' + '\n' + r'$$\sqrt{\frac{1}{3}}$$，\[x^2=2\]'
    soup = BeautifulSoup(_report_inline_math(source), 'html.parser')
    formulas = soup.select('.qm')
    assert [f['data-latex'] for f in formulas] == [r'\sqrt{3}-1', r'\frac{a+b}{c}', r'\sqrt{\frac{1}{3}}', 'x^2=2']
    assert [f.get('data-display') for f in formulas] == [None, None, 'true', 'true']
    assert not soup.select('.qm .qm')


def test_linear_radicals_preserve_grouping_and_do_not_simplify():
    from analysis_report_exporter import _report_linear_tex, _report_inline_math
    assert _report_linear_tex('√((1)/(3))') == r'\sqrt{\frac{1}{3}}'
    assert _report_linear_tex('(1)/(√(5)−√(3))') == r'\frac{1}{\sqrt{5}-\sqrt{3}}'
    assert _report_linear_tex('2√3/2-1') == r'2\frac{\sqrt{3}}{2}-1'
    assert _report_linear_tex('3√2') == r'3\sqrt{2}'
    assert _report_linear_tex('∛2') == r'\sqrt[3]{2}'
    assert _report_linear_tex('y=1/2x') == 'y=1/2x'
    assert _report_linear_tex('√(2') is None
    assert _report_inline_math('保留0/6分') == '保留0/6分'
    assert r'data-latex="\frac{1}{\sqrt{512}}"' in _report_inline_math('1/√512（上方过程划去）')
    assert r'\frac{1}{\sqrt{5} ? \sqrt{2}}' in _report_inline_math('1/(√5 ? √2)（字迹待核对）')


def test_native_word_root_index_wins_over_plain_notation():
    from docx import Document
    from docx.oxml import parse_xml
    from analysis_report_exporter import _source_formula_markup, _QuestionInfo, _report_stem_html
    document = Document()
    document.add_paragraph()._p.append(parse_xml('''<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:rad><m:deg><m:r><m:t>3</m:t></m:r></m:deg><m:e><m:r><m:t>2</m:t></m:r></m:e></m:rad></m:oMath>'''))
    blob = io.BytesIO()
    document.save(blob)
    markup = _source_formula_markup(SimpleNamespace(suffix='.docx', private_source_bytes=blob.getvalue()))
    assert 'data-latex="\\sqrt[3]{2}"' in markup['<sup>3</sup>√(2)']
    info = _QuestionInfo('Q1', 'calculation', 6, '', '', question_markup=markup['<sup>3</sup>√(2)'])
    soup = BeautifulSoup(_report_stem_html(info, ''), 'html.parser')
    assert len(soup.select('.qm')) == 1
    assert soup.select_one('.qm')['data-latex'] == r'\sqrt[3]{2}'


def test_graph_uses_current_mastery_separately_from_exam_scores(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, _personal_knowledge_view
    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    student = data.students[0]
    record = next(r for r in student.records if r.lost)
    data.knowledge_backfill = {record.question_id: [
        {'stable_key':'topic', 'path':'本册｜本章｜本节｜根式', 'label':'根式'},
        {'stable_key':'sk_test', 'path':'本册｜本章｜本节｜技能·化简', 'label':'技能·化简'},
        {'stable_key':'sk_missing', 'path':'本册｜本章｜本节｜技能·比较', 'label':'技能·比较'},
    ]}
    student.knowledge_mastery = {'topic':{'mastery':.91,'evidence_count':4}, 'sk_test':{'mastery':.42,'evidence_count':2}}
    data.knowledge_structure = {'as_of':'2026-09-22 20:00', 'associations':[
        {'topic_key':'topic','skill_key':'sk_test','question_count':3,'same_part_question_count':2,'basis':'same_part'},
        {'topic_key':'topic','skill_key':'outside','question_count':9,'same_part_question_count':0,'basis':'question_cooccurrence'},
    ]}
    before = [(r.score, r.max_score) for r in student.records]
    view = _personal_knowledge_view(data, student)
    assert [n['mastery'] for n in view['nodes']] == [.91,.42,None]
    assert len(view['edges']) == 1
    assert all(n['score'] == record.score and n['full'] == record.max_score for n in view['nodes'])
    assert before == [(r.score, r.max_score) for r in student.records]


def test_batch_mastery_uses_one_semester_read_and_rejects_exam_fallback(analysis_db, monkeypatch):
    from analysis_report_exporter import assemble_session_analysis, _enrich_personal_knowledge
    from backend.repositories.access import as_grading_repositories
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    with db._connect() as conn:
        conn.execute("UPDATE grading_sessions SET curriculum_volume_id='test-semester' WHERE id=?",(session,))
    (root/'databases/question_bank.db').touch()
    monkeypatch.setattr(CurrentKnowledgeResolver,'from_active_database',lambda path:SimpleNamespace(resolve=lambda value:()))
    calls=[]
    def profile(self, *, scope, exam_scope):
        calls.append((scope,exam_scope))
        return {'students':[{'student_id':str(data.students[0].student_id),'weak_points':[
            {'knowledge_key':'exam-only','mastery':1.0},
            {'knowledge_key':'current','mastery':.63,'effective_weight':2,'direct_evidence_count':2},
        ]}], 'knowledge_catalog':[], 'knowledge_associations':[]}
    monkeypatch.setattr(DiagnosisProfileService,'build_tag_profiles',profile)
    _enrich_personal_knowledge(as_grading_repositories(db),data,root,student_ids={data.students[0].student_id})
    assert len(calls) == 1
    assert calls[0][1] == {'mode':'semester','curriculum_volume_id':'test-semester'}
    assert calls[0][0]['student_ids'] == [str(data.students[0].student_id)]
    assert set(data.students[0].knowledge_mastery) == {'current'}


def test_display_revision_keeps_completed_model_analysis_usable():
    from backend.report_exports import (
        LEGACY_PERSONAL_NARRATIVE_VERSIONS,
        report_rendition_version,
        report_narrative_version,
    )
    assert report_rendition_version('personal_analysis_html') != report_narrative_version('personal_analysis_html')
    assert report_narrative_version('personal_analysis_html') == 'personal_analysis_html_v9_problem_refs'
    # 旧版 v8 叙述仍兼容读取，不重新调用模型。
    assert 'personal_analysis_html_v8_parts' in LEGACY_PERSONAL_NARRATIVE_VERSIONS


# ---------------------------------------------------------------------------
# 家长版一页式（v3）：历次成绩、跟进卡题号、降级块与对外可见文本
# ---------------------------------------------------------------------------

import re
import sqlite3
from tests.test_analysis_report import COMPLETE_RAW_JSON, PERSONAL_NARRATIVE


def _seed_prior_session(db, root, session_name, scores, *, volume="vol-1", graded_at=""):
    """同学期的另一场考试；scores={姓名: 分数}，未列出的学生该场无记录。"""
    rubric_path = root / "config" / "uploaded" / "rubric.json"
    answer_path = root / "config" / "uploaded" / "answer_key.json"
    sid = db.create_grading_session(session_name, str(rubric_path), str(answer_path))
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id=? WHERE id=?",
            (volume, sid),
        )
        for name, awarded in scores.items():
            student_id = conn.execute(
                "SELECT id FROM students WHERE name=?", (name,)
            ).fetchone()[0]
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, student_id,
                        match_status, processing_status
                    ) VALUES (?, '', '', ?, 'matched', 'graded')
                    """,
                    (sid, student_id),
                ).lastrowid
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json, graded_at
                    ) VALUES (?, ?, ?, 100, ?, 0, ?, ?)
                    """,
                    (sid, student_id, paper_id, awarded, COMPLETE_RAW_JSON, graded_at),
                ).lastrowid
            )
            conn.execute(
                "INSERT INTO session_details (result_id, question_id, score_awarded, knowledge_ids) "
                "VALUES (?, 'Q1', ?, '[\"UNKNOWN\"]')",
                (result_id, min(awarded, 60)),
            )
            conn.execute(
                "INSERT INTO session_details (result_id, question_id, score_awarded, knowledge_ids) "
                "VALUES (?, 'Q2', ?, '[\"UNKNOWN\"]')",
                (result_id, max(0.0, awarded - 60)),
            )
    return sid


def test_history_section_shows_multiple_sessions_and_absence(analysis_db):
    from analysis_report_exporter import (
        assemble_session_analysis,
        _load_student_histories,
        _render_personal_html,
        split_session_analysis_by_class,
    )
    from backend.repositories.access import as_grading_repositories

    db, session_id, root = analysis_db
    _seed_prior_session(db, root, "上上周测", {"张三": 70}, graded_at="2026-09-06")
    _seed_prior_session(db, root, "上周测", {"李四": 60}, graded_at="2026-09-13")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id='vol-1' WHERE id=?",
            (session_id,),
        )
        conn.execute(
            "UPDATE session_results SET graded_at='2026-09-20' WHERE session_id=?",
            (session_id,),
        )
    data = assemble_session_analysis(db, session_id, data_root=root)
    histories = _load_student_histories(as_grading_repositories(db), data, root)
    group = split_session_analysis_by_class(data)["1 班"]
    zhangsan = next(s for s in group.students if s.student_name == "张三")
    hist = histories[zhangsan.student_id]
    assert [e["name"] for e in hist] == ["上上周测", "上周测", "单元测试"]
    assert [e["score"] for e in hist] == [70, None, 90]

    rendered = _render_personal_html(group, zhangsan, PERSONAL_NARRATIVE, {}, history=hist)
    assert "历次成绩" in rendered
    assert "未参加" in rendered
    # 「上次」取本场之前最近一次有成绩的场次（上上周测 70 分，班内第 1/1）。
    assert "上次 第1/1名" in rendered


def test_history_section_hidden_with_single_scored_session(analysis_db):
    import sqlite3 as _sqlite3
    from analysis_report_exporter import (
        assemble_session_analysis,
        _load_student_histories,
        _render_personal_html,
        split_session_analysis_by_class,
    )
    from backend.repositories.access import as_grading_repositories

    db, session_id, root = analysis_db
    with _sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id='vol-1' WHERE id=?",
            (session_id,),
        )
    data = assemble_session_analysis(db, session_id, data_root=root)
    histories = _load_student_histories(as_grading_repositories(db), data, root)
    group = split_session_analysis_by_class(data)["1 班"]
    zhangsan = next(s for s in group.students if s.student_name == "张三")
    assert len(histories[zhangsan.student_id]) == 1
    rendered = _render_personal_html(
        group, zhangsan, PERSONAL_NARRATIVE, {}, history=histories[zhangsan.student_id]
    )
    # 只有 1 场有成绩 → 不渲染 B 版块（CSS 注释里的“历次成绩”不算）。
    assert "<h2>历次成绩</h2>" not in rendered
    assert 'class="hist"' not in rendered
    assert "首次记录" in rendered


def _student_with_lost_questions(analysis_db):
    """合成失分题集合：Q7/Q10 大题 + Q14(P2)/Q14(P6)/Q12(P1..P4) 小问，Q5 满分。"""
    from dataclasses import replace
    from analysis_report_exporter import assemble_session_analysis

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    base = next(r for r in student.records if r.lost)
    info = next(q for q in data.questions if q.question_id == base.question_id)
    qids = ["Q7", "Q10", "Q14(P2)", "Q14(P6)",
            "Q12(P1)", "Q12(P2)", "Q12(P3)", "Q12(P4)", "Q5"]
    types = {"Q7": "fill_blank", "Q10": "choice"}
    student.records = [
        replace(
            base,
            question_id=qid,
            score=base.max_score if qid == "Q5" else 0,
            deduction_reason="",
            error_category="",
            error_summary="",
        )
        for qid in qids
    ]
    data.questions = [
        replace(info, question_id=qid, question_type=types.get(qid, "proof"))
        for qid in qids
    ]
    return data, student


def test_follow_up_question_ids_prefer_narrative_ids(analysis_db):
    from analysis_report_exporter import _follow_up_question_ids

    _data, student = _student_with_lost_questions(analysis_db)
    info_by_qid = {q.question_id: q for q in _data.questions}
    problem = {
        "title": "计算失分",
        "detail": "第7题也错了",
        "question_ids": ["Q14(P2)", "Q5", "Q99", "q14(p2)"],
    }
    # question_ids 优先于正文解析；非失分题(Q5)、未知题(Q99)、重复项都被过滤。
    assert _follow_up_question_ids(problem, None, student, info_by_qid) == ["Q14(P2)"]


def test_follow_up_text_fallback_parses_references(analysis_db):
    from analysis_report_exporter import _follow_up_question_ids

    _data, student = _student_with_lost_questions(analysis_db)
    info_by_qid = {q.question_id: q for q in _data.questions}

    def ids(detail, suggestion=None):
        return _follow_up_question_ids(
            {"title": "", "detail": detail}, suggestion, student, info_by_qid
        )

    assert ids("第7、10题") == ["Q7", "Q10"]
    assert ids("第14(2)(6)题") == ["Q14(P2)", "Q14(P6)"]
    assert ids("第12(1)-(4)题") == ["Q12(P1)", "Q12(P2)", "Q12(P3)", "Q12(P4)"]
    assert ids("第７题") == ["Q7"]  # 全角数字
    assert ids("填空题书写不规范") == ["Q7"]  # 题型类别
    assert ids("选择题要看清") == ["Q10"]
    assert ids("整体还可以") == []


def test_follow_up_card_meta_hides_empty_associations(analysis_db):
    from analysis_report_exporter import _render_personal_html

    data, student = _student_with_lost_questions(analysis_db)
    narrative = {
        **PERSONAL_NARRATIVE,
        "problems": [
            {"title": "过程分丢失", "detail": "细节",
             "question_ids": ["Q14(P2)", "Q14(P6)"]},
            {"title": "没有可关联题号", "detail": "细节", "question_ids": ["Q5"]},
        ],
    }
    rendered = _render_personal_html(data, student, narrative, {})
    # 关联到失分小问时展示丢分合计与题号标签；无关联的卡片不显示元信息行。
    assert '丢 80 分<span class="qtag">第14(2)题</span><span class="qtag">第14(6)题</span>' in rendered
    assert rendered.count('class="meta2"') == 1
    assert "丢 0 分" not in rendered


def test_no_narrative_fallback_hides_raw_grading_records(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    rendered = _render_personal_html(data, student, None, {})
    assert "AI 分析生成失败" not in rendered
    assert "缺关键步骤" not in rendered  # Q2 deduction_reason 原文不外露
    assert "人工复核已确认" not in rendered
    assert "这次重点跟进" in rendered

    # 真实老师批语在降级块中显示；占位确认文案一律视为无批语。
    record = next(r for r in student.records if r.question_id == "Q2")
    record.teacher_comment = "步骤要写完整"
    rendered = _render_personal_html(data, student, None, {})
    assert "老师批语：步骤要写完整" in rendered
    record.teacher_comment = "人工复核已确认"
    rendered = _render_personal_html(data, student, None, {})
    assert "人工复核已确认" not in rendered
    assert "老师批语" not in rendered


def test_visible_text_hides_internal_ids_and_mastery(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    rendered = _render_personal_html(data, data.students[0], PERSONAL_NARRATIVE, {})
    soup = BeautifulSoup(rendered, "html.parser")
    for tag in soup(["script", "style", "template"]):
        tag.decompose()
    text = soup.get_text()
    assert not re.search(r"Q\d+\(P\d+\)", text)
    assert "掌握度" not in text
    assert "%" not in text
    assert 'href="#' not in rendered
