"""Targeted regression checks for the teaching-skill repair; synthetic only."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.build_release_v3 import build_teaching_release, apply_teaching_repair, CATALOG_DIR
from tools.build_skill_layer import match_teaching_skill
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
from question_bank.database.schema import initialize_database, connect
from tests.current_knowledge_support import install_current_knowledge


@pytest.fixture(scope='module')
def standard():
    return json.loads((CATALOG_DIR / 'teaching_skills_g8_core.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('target,chapter,suffix', [
    ('在直角三角形中列出勾股等式', 1, '1_1_102'),
    ('计算另一条直角边长', 1, '1_1_104'),
    ('用勾股定理逆定理判定三角形为直角三角形', 1, '1_2_101'),
    ('过点作垂线构造直角三角形', 1, '1_3_101'),
    ('分子分母同乘共轭式完成分母有理化', 2, '2_3_106'),
    ('用平方差公式计算(√3+1)(√3-1)', 2, '2_3_107'),
    ('求√7的整数部分与小数部分', 2, '2_1_104'),
    ('求计算', 1, None),
    ('计算AB的长度', 1, None),
    ('确认7²+24²=25²后用逆定理判断直角', 1, '1_2_101'),
    ('求斜边长并求另一条直角边长', 1, None),
    ('比较两条线段的大小', 2, None),
    ('化简二次根式加减混合运算', 2, None),
])
def test_explicit_operations_and_ambiguous_targets(standard, target, chapter, suffix):
    expected = f'sk_bnu24_math_g8_upper_{suffix}' if suffix else None
    assert match_teaching_skill(target, [f'kp_bnu24_math_g8_upper_{chapter}'], standard) == expected


def test_old_mixed_skill_resolves_to_section_never_a_new_skill(standard):
    payload, vocabulary = build_teaching_release(standard)
    resolver = CurrentKnowledgeResolver(KnowledgeGraphRelease.from_mapping(payload), vocabulary)
    assert [r.stable_key for r in resolver.resolve('sk_bnu24_math_g8_upper_1_1_01')] == ['kp_bnu24_math_g8_upper_1_1']
    node = resolver.node('sk_bnu24_math_g8_upper_1_1_104')
    assert '平方差' in node.include_scope and '求斜边' in node.exclude_scope
    assert len([n for n in resolver.nodes if 'teaching_skill_definition_2026_09' in n.evidence_source_ids]) == 30


def _synthetic_bank(tmp_path):
    database = tmp_path / 'question_bank.db'
    initialize_database(database)
    release = install_current_knowledge(database, taxonomy_revision=5)
    evidence = {'parts': [{'part_id':'part-1','evidence_points':[
        {'evidence_point_id':'p1','target':'计算另一条直角边长'},
        {'evidence_point_id':'p2','target':'求结果'},
    ]}]}
    from question_bank.solution_evidence.knowledge_links import replace_point_links
    with connect(database) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(1,'合成修复验收','ready')")
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(1,1,'1','合成题')")
        conn.execute('''INSERT INTO question_solution_evidence_versions(
            evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,
            source_kind,source_reference,created_by,graph_release_id) VALUES(?,1,?,'question-solution-evidence-v2',?,?,'approved','backfill','synthetic','test',?)''',
            ('a'*64,'b'*64,'c'*64,json.dumps(evidence),release))
        replace_point_links(conn, evidence_version_id='a'*64, question_id=1, graph_release_id=release,
                            points=[{'part_id':'part-1','evidence_point_id':pid,'links':[
                                {'term_id':'sk_bnu24_math_g8_upper_1_1_01','role':'direct'}]} for pid in ('p1','p2')])
    return database, release


def test_repair_refreshes_current_links_and_tags_retaining_old_links(tmp_path, standard):
    database, old_release = _synthetic_bank(tmp_path)
    payload, vocabulary = build_teaching_release(standard)
    report = apply_teaching_repair(database, standard, payload, vocabulary)
    assert report['new_skill_points'] == 1 and report['section_fallback_points'] == 1
    with connect(database) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?',(old_release,)).fetchone()[0] == 2
        assert conn.execute('SELECT graph_release_id FROM question_scope_summary').fetchone()[0] == payload['release_id']
        tags = {r[0] for r in conn.execute("SELECT tag_value FROM question_tags WHERE tag_type='knowledge_point'")}
        assert 'sk_bnu24_math_g8_upper_1_1_104' in tags
        assert 'sk_bnu24_math_g8_upper_1_1_01' not in tags


def test_failed_repair_rolls_back_links_and_active_release(tmp_path, standard, monkeypatch):
    database, old_release = _synthetic_bank(tmp_path)
    import question_bank.solution_evidence.knowledge_links as links
    real_replace = links.replace_point_links
    def fail_after_write(*args, **kwargs):
        real_replace(*args, **kwargs)
        raise ValueError('synthetic failure')
    monkeypatch.setattr(links, 'replace_point_links', fail_after_write)
    with pytest.raises(ValueError, match='synthetic failure'):
        apply_teaching_repair(database, standard, *build_teaching_release(standard))
    with connect(database) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old_release
        assert conn.execute('SELECT COUNT(*) FROM evidence_point_knowledge_links').fetchone()[0] == 2


def test_link_gateway_uses_configured_protocol_without_retry():
    from backend.jobs.knowledge_link_job import build_knowledge_link_gateway
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({'questions':[{'question_id':1,'points':[{'evidence_point_id':'p1','links':[]}]}]}))
    service = SimpleNamespace(mock_mode=False, model='synthetic-model', _protocol_adapter=lambda: SimpleNamespace(responses=respond))
    result = build_knowledge_link_gateway(service)({'questions':[{'question_id':1,'parts':[],'candidates':[]}]})
    assert result[1][0]['evidence_point_id'] == 'p1'
    assert calls[0]['model'] == 'synthetic-model' and calls[0]['allow_retry'] is False
    assert 'exclude_scope' in calls[0]['kwargs']['input']


def test_link_job_retries_supporting_only_points_and_uses_skill_boundaries(tmp_path, standard, monkeypatch):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from question_bank.models.tag_schema import TaggingContext
    import question_bank.training_criteria.adapters as adapters
    database, _ = _synthetic_bank(tmp_path)
    payload, vocabulary = build_teaching_release(standard)
    apply_teaching_repair(database, standard, payload, vocabulary)
    section = 'kp_bnu24_math_g8_upper_1_1'
    skill = 'sk_bnu24_math_g8_upper_1_1_104'
    with connect(database) as conn:
        conn.execute("UPDATE evidence_point_knowledge_links SET role='supporting_prerequisite' WHERE graph_release_id=? AND evidence_point_id='p2'", (payload['release_id'],))
    monkeypatch.setattr(adapters, 'QuestionAnalysisInputLoader', lambda **kwargs: SimpleNamespace(
        load=lambda ids: [SimpleNamespace(question_id=1, tagging_context=TaggingContext(question_text='合成题'))]))
    governance = SimpleNamespace(prompt_contracts=lambda contexts: {1:{'candidates':{'knowledge':[
        {'id':skill,'name':'求直角边长','usage':'direct_core'},
        {'id':section,'name':'勾股定理','usage':'direct_core'},
    ]}}})
    requests = []
    def gateway(request):
        requests.append(request)
        return {1:[{'evidence_point_id':'p2','links':[
            {'fine_term_id':section,'role':'direct'}, {'fine_term_id':section,'role':'direct'},
        ]}]}
    context = SimpleNamespace(payload={'mode':'missing_only','question_ids':[1]}, job_id=1,
                              report=lambda *args: None, raise_if_cancelled=lambda: None)
    result = run_knowledge_link_job(context=context, question_bank_db_path=database, data_root=tmp_path,
                                   link_gateway=gateway, taxonomy_governance=governance)
    assert result['questions_linked'] == 1 and result['links_written'] == 1
    question = requests[0]['questions'][0]
    assert [p['evidence_point_id'] for part in question['parts'] for p in part['points']] == ['p2']
    candidate = next(c for c in question['candidates'] if c['id'] == skill)
    assert '平方差' in candidate['include_scope'] and '求斜边' in candidate['exclude_scope']
