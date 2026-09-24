"""Class mastery is a distribution of independent student evidence, not scores."""
import json
from dataclasses import replace

from bs4 import BeautifulSoup

from tests.test_analysis_report import analysis_db, CLASS_NARRATIVE  # noqa: F401


def test_class_graph_excludes_missing_mastery_and_keeps_scores(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, _class_knowledge_view, _render_class_html
    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    assert len(data.students) == 2
    data.students.append(replace(data.students[0], student_id=999, knowledge_mastery={}))
    data.knowledge_backfill = {'Q2': [
        {'stable_key': 'topic', 'label': '根式', 'path': '本册｜本章｜本节｜根式'},
        {'stable_key': 'sk_test', 'label': '技能·化简', 'path': '本册｜本章｜本节｜化简'},
    ]}
    data.knowledge_structure = {'as_of': '2026-09-22 20:00', 'associations': [
        {'topic_key': 'topic', 'skill_key': 'sk_test', 'same_part_question_count': 2, 'question_count': 3},
        {'topic_key': 'topic', 'skill_key': 'outside', 'same_part_question_count': 0, 'question_count': 2},
    ]}
    data.students[0].knowledge_mastery = {'topic': {'mastery': 1}, 'sk_test': {'mastery': 0}}
    data.students[1].knowledge_mastery = {'topic': {'mastery': .6}, 'sk_test': {'mastery': .75}}
    before = [(student.student_score, student.rank, [(r.score, r.max_score) for r in student.records]) for student in data.students]
    view = _class_knowledge_view(data)
    topic, skill = view['nodes']
    assert topic['mastery'] == .8
    assert skill['mastery'] == .375
    assert topic['distribution'] == {'low': 0, 'mid': 1, 'good': 1, 'missing': 1}
    assert skill['distribution'] == {'low': 1, 'mid': 0, 'good': 1, 'missing': 1}
    assert topic['coverage'] == 2 and topic['student_count'] == 3
    assert topic['score'] == skill['score']
    assert len(view['edges']) == 1
    assert sum(topic['distribution'].values()) == 3
    assert before == [(student.student_score, student.rank, [(r.score, r.max_score) for r in student.records]) for student in data.students]
    soup = BeautifulSoup(_render_class_html(data, CLASS_NARRATIVE), 'html.parser')
    frozen = json.loads(soup.select_one('.kn-data').string)
    assert frozen['mode'] == 'class' and frozen['nodes'][0]['mastery'] == .8
    assert soup.select_one('#score-Q2')
    assert len(soup.select('.kn-distribution')) == 2
    assert not soup.select('script[src]')


def test_class_graph_uses_only_selected_class_and_preserves_missing(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, split_session_analysis_by_class, _class_knowledge_view
    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    data.knowledge_backfill = {'Q2': [{'stable_key': 'skill', 'label': '技能·化简', 'path': '化简'}]}
    data.students[0].class_name = '甲班'
    data.students[0].knowledge_mastery = {'skill': {'mastery': .9}}
    for student in data.students[1:]:
        student.class_name = '乙班'
    groups = split_session_analysis_by_class(data)
    first = _class_knowledge_view(groups['甲班'])['nodes'][0]
    second = _class_knowledge_view(groups['乙班'])['nodes'][0]
    assert first['mastery'] == .9 and first['student_count'] == 1
    assert second['mastery'] is None and second['distribution']['missing'] == 1
    assert second['coverage'] == 0


def test_class_math_covers_titles_narratives_answers_and_display_mode(analysis_db):
    from analysis_report_exporter import assemble_session_analysis, _render_class_html
    db, session, root = analysis_db
    data = assemble_session_analysis(db, session, data_root=root)
    data.questions[0].canonical_answer = '2√3/2-1'
    narrative = {'key_findings': [{'title': r'辨认$\sqrt{3}$', 'detail': r'结果\(\sqrt{3}-1\)'}],
                 'common_issues': [{'title': '化简√12', 'evidence': r'$$\frac{2\sqrt{3}}{2}-1$$',
                                    'teaching_action': '<img src=x onerror=alert(1)>'}],
                 'student_notes': [{'alias': 'S1', 'note': '保持√3', 'suggestion': r'检查\[x^2=3\]'}],
                 'grouping_advice': '练习∛2'}
    soup = BeautifulSoup(_render_class_html(data, narrative), 'html.parser')
    assert len(soup.select('h3 .qm')) == 2
    assert len(soup.select('.qm[data-display=true]')) == 2
    assert soup.select_one('.answer .qm')['data-latex'] == r'2\frac{\sqrt{3}}{2}-1'
    assert not soup.select('img[src=x]')
    assert 'displayMode:el.dataset.display' in soup.select('script')[-1].string


def test_existing_route_enriches_only_selected_class(class_analysis_api_client):
    from tests.test_class_analysis import _patch_configured, _generate_via_api
    client, db, session, _reports, manager, _holder, monkeypatch = class_analysis_api_client
    with db._connect() as connection:
        connection.execute("UPDATE students SET class_name='乙班' WHERE name='李四'")
    _patch_configured(monkeypatch, True)
    job = _generate_via_api(client, session)
    manager.wait(job['id'], timeout=5)
    calls = []
    def enrich(repositories, data, root):
        calls.append({s.class_name for s in data.students})
        data.knowledge_backfill = {'Q2': [{'stable_key': 'sk_test', 'label': '技能·证明', 'path': '证明'}]}
        for student in data.students:
            student.knowledge_mastery = {'sk_test': {'mastery': .72}}
    monkeypatch.setattr('analysis_report_exporter._enrich_personal_knowledge', enrich)
    response = client.get(f'/api/sessions/{session}/class-analysis/report', params={'class_name': '乙班'})
    assert response.status_code == 200
    assert calls == [{'乙班'}]
    soup = BeautifulSoup(response.text, 'html.parser')
    assert json.loads(soup.select_one('.kn-data').string)['student_count'] == 1


from tests.test_class_analysis import class_analysis_api_client  # noqa: E402,F401
