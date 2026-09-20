"""Repair decisions must have observable evidence, not a cluster name."""
from tools.repair_g8_upper_skills import build_v6, select_skills
import json
import pytest


def test_vague_clusters_retire_to_sections_without_copying_mastery():
    release, _, retired = build_v6()
    nodes = {node['stable_key']: node for node in release['core_nodes']}
    assert 'sk_bnu24_math_g8_upper_3_2_01' in retired
    assert 'sk_bnu24_math_g8_upper_3_3_101' in retired
    assert all(nodes[key]['status'] == 'retired' for key in retired)
    replacements = {item['retired_key']: item for item in release['replacements']}
    assert all(replacements[key]['replacement_kind'] == 'broader' for key in retired)
    assert all(replacements[key]['replacement_key'].startswith('kp_') for key in retired)
    assert nodes['sk_bnu24_math_g8_upper_1_1_101']['status'] == 'active'


def test_opaque_objective_answer_does_not_invent_a_method():
    assert select_skills({'target': '作答为B', 'justification': '可用加减消元法'}, {}, {}, 'kp_bnu24_math_g8_upper_5_2') == []


def test_substitution_of_a_number_is_not_substitution_elimination():
    assert select_skills({'target': '回代求y并写出方程组的解'}, {}, {}, 'kp_bnu24_math_g8_upper_5_2') == ['sk_bnu24_math_g8_upper_5_2_203']


def test_two_distinct_observations_remain_for_manual_review():
    assert select_skills({'target': '求中位数与众数'}, {}, {}, 'kp_bnu24_math_g8_upper_6_2') == []


def test_weighted_average_has_its_own_operation():
    assert select_skills({'target': '按权重计算加权平均数'}, {}, {}, 'kp_bnu24_math_g8_upper_6_1') == ['sk_bnu24_math_g8_upper_6_1_206']


def synthetic_bank(tmp_path):
    from question_bank.database.schema import initialize_database, connect
    from tests.current_knowledge_support import install_current_knowledge
    from question_bank.solution_evidence.knowledge_links import replace_point_links, refresh_question_scope_summary
    db = tmp_path / 'bank.db'
    initialize_database(db)
    release = install_current_knowledge(db, taxonomy_revision=7)
    evidence = {'parts': [{'part_id': 'part-1', 'evidence_points': [
        {'evidence_point_id': 'p1', 'target': '求关于x轴对称的点坐标'},
        {'evidence_point_id': 'p2', 'target': '求结果'},
    ]}]}
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(1,'合成修复验收','ready')")
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(1,1,'1','合成题')")
        conn.execute('''INSERT INTO question_solution_evidence_versions(
            evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,
            source_kind,source_reference,created_by,graph_release_id) VALUES(?,1,?,'question-solution-evidence-v2',?,?,'approved','backfill','synthetic','test',?)''',
            ('a'*64, 'b'*64, 'c'*64, json.dumps(evidence), release))
        replace_point_links(conn, evidence_version_id='a'*64, question_id=1, graph_release_id=release,
            points=[{'part_id': 'part-1', 'evidence_point_id': pid, 'links': [
                {'term_id': 'sk_bnu24_math_g8_upper_3_2_01', 'role': 'direct'}]} for pid in ('p1', 'p2')])
        refresh_question_scope_summary(conn, 1)
    return db, release


@pytest.mark.parametrize('fail', [False, True])
def test_repair_preserves_evidence_and_rolls_back_failed_links(tmp_path, monkeypatch, fail):
    from question_bank.database.schema import connect
    from tools.repair_g8_upper_skills import make_plan, apply_plan
    import question_bank.services.question_write_service as writes
    db, old_release = synthetic_bank(tmp_path)
    payload, vocabulary, retired = build_v6()
    plans, counts = make_plan(db, payload, vocabulary, retired)
    assert counts['questions_repaired'] == 1 and counts['new_specific_points'] == 1
    with connect(db) as conn:
        original = conn.execute('SELECT evidence_json FROM question_solution_evidence_versions').fetchone()[0]
    if fail:
        def reject(*args, **kwargs):
            raise ValueError('synthetic failure')
        monkeypatch.setattr(writes, 'refresh_derived_ownership_tags', reject)
        with pytest.raises(ValueError, match='synthetic failure'):
            apply_plan(db, payload, vocabulary, plans)
    else:
        apply_plan(db, payload, vocabulary, plans)
    with connect(db) as conn:
        assert conn.execute('SELECT evidence_json FROM question_solution_evidence_versions').fetchone()[0] == original
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == (old_release if fail else payload['release_id'])
        assert conn.execute('SELECT count(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?', (old_release,)).fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?', (payload['release_id'],)).fetchone()[0] == (0 if fail else 2)
