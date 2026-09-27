import hashlib
import json
from copy import deepcopy

import pytest

from question_bank.recommendation.target_matching import match_target, part_facets, target_index, load_question_facets
from question_bank.recommendation.personalized import _loss_difficulty_fits


INDEX = {"sk_test": {"kind": "skill", "chapter": "chapter", "section": "section"}}


def facet(*, skills=("sk_test",), topics=("topic",), section="section", chapter="chapter", part="p1"):
    return {"part_id": part, "direct_keys": list(skills), "skill_keys": list(skills),
            "topic_keys": list(topics), "section_keys": [section], "chapter_keys": [chapter]}


def test_four_levels_and_no_unrelated_filler():
    source = [facet()]
    assert match_target("sk_test", source, [facet()], INDEX)["match_level"] == 1
    assert match_target("sk_test", source, [facet(topics=("other-topic",))], INDEX)["match_level"] == 2
    assert match_target("sk_test", source, [facet(skills=("sk_other",))], INDEX)["match_level"] == 3
    assert match_target("sk_test", source, [facet(skills=("sk_other",), topics=("other-topic",))], INDEX)["match_level"] == 4
    assert match_target("sk_test", source, [facet(skills=("sk_other",), topics=("other-topic",),
                                                  section="elsewhere", chapter="elsewhere")], INDEX) is None


def test_topic_and_skill_in_different_parts_are_not_exact_match():
    candidate = [facet(skills=("sk_other",), part="p1"), facet(topics=("different-topic",), part="p2")]
    matched = match_target("sk_test", [facet()], candidate, INDEX)
    assert matched["match_level"] == 2
    assert matched["candidate_part_id"] == "p2"
    assert matched["matched_topic_keys"] == []


def test_unknown_topic_never_claims_dual_match_or_fabricates_knowledge():
    result = match_target("sk_test", [facet(topics=())], [facet()], INDEX)
    assert result["match_level"] == 2
    assert result["matched_topic_keys"] == []


def test_matching_uses_ability_band_and_requires_score_evidence():
    ref = {"question_difficulty": 6, "full_score": 5, "score_awarded": 4}
    assert _loss_difficulty_fits({"difficulty": 5}, ref, 7)
    assert _loss_difficulty_fits({"difficulty": 6}, ref, 7)
    assert not _loss_difficulty_fits({"difficulty": 3}, ref, 7)
    assert not _loss_difficulty_fits({"difficulty": 8}, ref, 7)
    assert _loss_difficulty_fits({"difficulty": 7}, {**ref, "question_difficulty": 9}, 7)
    assert not _loss_difficulty_fits({"difficulty": 3}, {"question_difficulty": 6}, 7)
    assert _loss_difficulty_fits({"difficulty": 2}, {**ref, "score_awarded": 1}, 7, .3)
    assert not _loss_difficulty_fits({"difficulty": 5}, {**ref, "score_awarded": 1}, 7, .3)


def test_skill_not_in_source_part_does_not_use_unrelated_part_as_anchor():
    assert match_target('sk_test', [facet(skills=('sk_else',))], [facet()], INDEX) is None


def test_single_part_worked_solution_can_support_short_practice_but_not_multipart():
    from question_bank.recommendation.personalized import _task_matched_part
    source = {'practice_observations_by_key': {'skill': [
        {'observable': '由勾股定理列出AB²+BC²=AC²'}]}}
    candidate = {'target_facets': [{'part_id': 'p1'}],
        'solution_observable': '由勾股定理得AC=√(AB²+BC²)',
        'practice_observations_by_key': {'other': [
            {'part_id':'p1','response_mode':'exact_objective','observable':'答案B'}]}}
    tasks = [{'code':'process_practice'}]
    matched = _task_matched_part(candidate, 'skill', source, tasks, {'p1'})
    assert matched['practice_role'] == 'step_practice'
    assert matched['task_operations'] == ['pythagorean_equation']
    candidate['target_facets'].append({'part_id':'p2'})
    assert _task_matched_part(candidate, 'skill', source, tasks, {'p1'}) is None


def test_worked_area_relation_is_component_practice_without_retagging():
    from question_bank.recommendation.personalized import _observable_operations
    assert _observable_operations('由勾股定理，正方形A的面积＝10+4＝14') == {'pythagorean_equation'}
    assert _observable_operations('求正方形周长，列式4×3＝12') == set()


def test_class_assembly_keeps_published_skills():
    from question_bank.services.assembly_assistant import class_weaknesses
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    release = load_release_for_taxonomy_revision(7)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    key = 'sk_bnu24_math_g8_upper_1_1_101'
    point = {'knowledge_key': key, 'knowledge_point': '确认直角与斜边', 'mastery': .4, 'evidence_count': 2}
    diagnosis = {'students': [{'student_id': 'synthetic', 'weak_points': [point]}], 'group_weak_points': [point]}
    result = class_weaknesses(diagnosis, volume_id='bnu24-math-g8-upper', chapter_id='bnu24-math-g8-upper-c01', resolver=resolver)
    assert [r['knowledge_key'] for r in result] == [key]


def test_context_keeps_reused_skill_in_selected_chapter_but_never_admits_unlearned_parts():
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    from question_bank.recommendation.personalized import PersonalizedRecommendationConfig, _allowed_keys_for_config, _question_scope_allowed
    release = load_release_for_taxonomy_revision(7)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    skill = 'sk_bnu24_math_g8_upper_1_1_102'
    topic = 'kp_bnu24_math_g8_upper_4_4_6'
    config = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g8-upper', scope_keys=('kp_bnu24_math_g8_upper_4',), teaching_progress_chapter_id='bnu24-math-g8-upper-c04')
    allowed = _allowed_keys_for_config(config, resolver)
    candidate = {'stable_keys': [skill], 'required_keys': [skill], 'scope_complete': True,
                 'target_facets': [{'topic_keys': [topic]}]}
    assert _question_scope_allowed(candidate, config, allowed, resolver)
    candidate['target_facets'].append({'topic_keys': ['kp_bnu24_math_g8_upper_5_1_1']})
    assert not _question_scope_allowed(candidate, config, allowed, resolver)
    candidate['target_facets'] = [{'topic_keys': []}]
    assert not _question_scope_allowed(candidate, config, allowed, resolver)


def test_candidate_pipeline_uses_four_tiers_after_difficulty_progress_and_recent_filters(monkeypatch):
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule, PersonalizedRecommendationConfig, _choose_practice_entries
    release = load_release_for_taxonomy_revision(8)
    module = PersonalizedRecommendationModule.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    monkeypatch.setattr(module, '_enrich_source_ref', lambda ref, *args: ref)
    monkeypatch.setattr(module, '_links_for_metadata', lambda metadata: {})
    key, other = 'sk_bnu24_math_g8_upper_3_3_201', 'sk_bnu24_math_g8_upper_3_3_202'
    topic, adjacent = 'kp_bnu24_math_g8_upper_3_3_1', 'kp_bnu24_math_g8_upper_3_3_2'
    def part(skill, leaf):
        return facet(skills=(skill,), topics=(leaf,), section='kp_bnu24_math_g8_upper_3_3', chapter='kp_bnu24_math_g8_upper_3')
    source = {'bank_question_id': 99, 'question_id': 'synthetic-source', 'full_score': 5, 'score_awarded': 4,
              'question_difficulty': 6, 'target_facets': [part(key, topic)]}
    def candidate(qid, skill, leaf, difficulty=5):
        return {'question_id': qid, 'stable_keys': [skill], 'required_keys': [skill], 'scope_complete': True,
                'target_facets': [part(skill, leaf)], 'difficulty': difficulty, 'question_text': f'合成题 {qid}',
                'duplicate_identity': str(qid)}
    candidates = [candidate(1, key, topic), candidate(2, key, adjacent), candidate(3, other, topic),
                  candidate(4, other, adjacent), candidate(5, key, topic, 8), candidate(6, key, topic, 2),
                  candidate(7, key, topic), candidate(8, key, topic),
                  candidate(9, key, 'kp_bnu24_math_g8_upper_5_1_1')]
    config = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g8-upper',
        teaching_progress_chapter_id='bnu24-math-g8-upper-c03', difficulty_max=7)
    entries, _ = module._candidate_entries(profile={'student_id': 'synthetic', 'score_rate': .6},
        targets=[{'stable_key': key, 'source_question_refs': [source]}], candidates=candidates, metadata={},
        config=config, supplement_keys=(key, other), recent={7}, excluded={8})
    assert [(e['candidate']['question_id'], e['match_level'], e['selection_kind']) for e in entries] == [
        (1, 1, 'direct'), (2, 2, 'direct'), (3, 3, 'supplement'), (4, 4, 'supplement')]
    assert [e['candidate']['question_id'] for e, _ in _choose_practice_entries(entries, 4)] == [1, 2]


SKILL = 'sk_bnu24_math_g8_upper_3_3_201'
OTHER_SKILL = 'sk_bnu24_math_g8_upper_3_3_202'
TOPIC = 'kp_bnu24_math_g8_upper_3_3_1'
OTHER_TOPIC = 'kp_bnu24_math_g8_upper_3_3_2'


def test_source_reference_uses_evidence_identity_without_guessing_rubric_number():
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule
    release = load_release_for_taxonomy_revision(8)
    module = PersonalizedRecommendationModule.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    def part(part_id, skill, topic):
        return {'part_id': part_id, 'evidence_points': [{'evidence_point_id': part_id+'-point',
            'fine_term_links': [{'fine_term_id': key, 'role': 'direct'} for key in (skill, topic)]}]}
    metadata = {99: {'parts': [part('part-1', OTHER_SKILL, OTHER_TOPIC), part('part-2', SKILL, TOPIC)]}}
    ref = {'bank_question_id': 99, 'assessment': {'part_id': 'Q12(P2)', 'evidence_part_id': 'part-2'}}
    enriched = module._enrich_source_ref(ref, metadata)
    assert [p['part_id'] for p in enriched['target_facets']] == ['part-2']
    assert enriched['target_facets'][0]['skill_keys'] == [SKILL]
    assert enriched['target_facets'][0]['topic_keys'] == [TOPIC]
    assert enriched['assessment']['part_id'] == 'Q12(P2)'
    # Missing or unresolved correspondence cannot borrow the same-position part.
    for assessment in ({'part_id': 'Q12(P2)'}, {'part_id': 'Q12(P2)', 'evidence_part_id': 'missing'}):
        assert 'target_facets' not in module._enrich_source_ref({**ref, 'assessment': assessment}, metadata)


@pytest.fixture
def current_link_module(tmp_path):
    from question_bank.database.schema import connect, initialize_database
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule
    from question_bank.training_criteria import QuestionAnalysisInputLoader
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from question_bank.solution_evidence.knowledge_links import replace_point_links
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    from tests.current_knowledge_support import install_current_knowledge
    from tests.phase4.test_personalized_recommendation import _approve_synthetic_criteria

    db = tmp_path / 'bank.db'
    initialize_database(db)
    release_id = install_current_knowledge(db, taxonomy_revision=8)
    volume = curriculum_volume(volume_id='bnu24-math-g8-upper')
    stems = ['合成原题：观察图象中的点并写出坐标', '合成练习：利用位置关系确定变量的取值',
             '合成基础题：从表格中读取数值', '合成难题：推导一般表达式',
             '合成超进度题：建立新的方程模型', '合成练习：利用位置关系确定变量的取值',
             '合成近期题：比较两个观测量的变化']
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status,grade,semester,textbook_version) VALUES(1,'合成题库','success',?,?,?)",
                     (volume['grade'], volume['semester'], volume['textbook_version']))
        for qid, stem in enumerate(stems, 1):
            difficulty = {1: 6, 3: 2, 4: 8}.get(qid, 5)
            conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(?,1,?,'填空题',?,'合成答案',?)",
                         (qid, str(qid), stem, str(difficulty)))
    _approve_synthetic_criteria(db, tmp_path, tuple(range(1, 8)))
    inputs = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(tuple(range(1, 8)))
    with connect(db) as conn:
        for question in inputs:
            qid = question.question_id
            semantic = hashlib.sha256(f'semantic:{qid}'.encode()).hexdigest()
            stored = hashlib.sha256(f'storage:{qid}:{release_id}'.encode()).hexdigest()
            evidence = {'version_id': semantic, 'question_id': qid,
                        'source_content_hash': solution_evidence_source_content_hash(question),
                        'parts': [{'part_id': 'part1', 'response_mode': 'exact_objective',
                                   'evidence_points': [{'evidence_point_id': 'point1', 'fine_term_links': []}]}]}
            conn.execute("""INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,
                source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id)
                VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','backfill',?,'synthetic',?)""",
                (stored, qid, evidence['source_content_hash'], semantic, json.dumps(evidence), f'synthetic:{qid}', release_id))
            row = conn.execute('SELECT version_id,criteria_json FROM training_criterion_versions WHERE question_id=?', (qid,)).fetchone()
            criterion = json.loads(row['criteria_json'])
            criterion['solution_evidence'] = evidence
            conn.execute('UPDATE training_criterion_versions SET criteria_json=? WHERE version_id=?', (json.dumps(criterion), row['version_id']))
            topic = 'kp_bnu24_math_g8_upper_5_1_1' if qid == 5 else TOPIC
            replace_point_links(conn, evidence_version_id=stored, question_id=qid, graph_release_id=release_id,
                points=[{'part_id': 'part1', 'evidence_point_id': 'point1',
                         'links': [{'term_id': key, 'role': 'direct'} for key in (SKILL, topic)]}])
        # Reproduce an upgraded bank whose coarse tags still contain only topics.
        conn.execute("DELETE FROM question_tags WHERE tag_type='knowledge_point' AND tag_value=?", (SKILL,))
        conn.execute("DELETE FROM question_tags WHERE question_id=2 AND tag_type='knowledge_point'")
    return PersonalizedRecommendationModule(db_path=db, data_root=tmp_path)


def test_figure_referenced_in_question_requires_question_image_not_answer_image(current_link_module):
    from PIL import Image
    from question_bank.database.schema import connect
    from tests.phase4.test_personalized_recommendation import _approve_synthetic_criteria
    module = current_link_module
    picture = module.data_root / 'synthetic-diagram.png'
    Image.new('RGB', (30, 30), 'black').save(picture)
    with connect(module.db_path) as conn:
        for qid, text in ((8, '如图求边长'), (9, '图中求周长'), (10, '图示三角形求面积')):
            answer = f'答案：[[IMAGE:{picture}]]' if qid == 9 else '合成答案'
            conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty,image_paths,has_images) VALUES(?,1,?,'填空题',?,?,'3',?,?)",
                (qid, str(qid), text, answer, json.dumps([str(picture)] if qid == 10 else []), int(qid == 10)))
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(?,'knowledge_point',?,'synthetic')", (qid, SKILL))
    _approve_synthetic_criteria(module.db_path, module.data_root, (8, 9, 10))
    candidates, _, _ = module._source_snapshot()
    ids = {q['question_id'] for q in candidates}
    assert 8 not in ids and 9 not in ids
    assert 10 in ids


def test_whole_question_topics_still_block_future_chapters_after_skill_refinement(current_link_module):
    from dataclasses import replace
    from question_bank.database.schema import connect
    from question_bank.recommendation.personalized import PersonalizedRecommendationConfig, _question_scope_allowed, _allowed_keys_for_config
    module = current_link_module
    future = 'kp_bnu24_math_g8_upper_5_1_1'
    with connect(module.db_path) as conn:
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(2,'knowledge_point',?,'synthetic')", (future,))
    candidates, _, _ = module._source_snapshot()
    candidate = next(q for q in candidates if q['question_id'] == 2)
    assert future in candidate['required_keys']
    assert future not in candidate['stable_keys']
    assert future not in candidate['target_facets'][0]['topic_keys']
    config = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g8-upper', target_keys=(SKILL,), teaching_progress_chapter_id='bnu24-math-g8-upper-c03')
    assert not _question_scope_allowed(candidate, config, _allowed_keys_for_config(config, module.current_knowledge), module.current_knowledge)
    later = replace(config, teaching_progress_chapter_id='bnu24-math-g8-upper-c05')
    assert _question_scope_allowed(candidate, later, _allowed_keys_for_config(later, module.current_knowledge), module.current_knowledge)


def test_table_links_without_difficulty_profiles_reach_filtered_pool_and_real_selection(current_link_module):
    from question_bank.recommendation.personalized import PersonalizedRecommendationConfig, _choose_practice_entries
    module = current_link_module
    full, _, _ = module._source_snapshot()
    filtered, _, _ = module._source_snapshot(knowledge_keys=(SKILL,))
    assert {q['question_id'] for q in filtered} == {q['question_id'] for q in full} == {1, 2, 3, 4, 5, 7}
    facets = load_question_facets(module.db_path, module.current_knowledge)
    for candidate in filtered:
        # 小问难度唯一来源是公式特征行：没有有效特征的题仍回退整题难度，
        # part_assessment 可能存在但所有小问难度为 None。
        assessment = candidate['part_assessment']
        if assessment is not None:
            assert all(part['difficulty'] is None for part in assessment['parts'])
        assert SKILL in candidate['stable_keys']
        assert candidate['target_facets'] == facets[candidate['question_id']]['parts']
        assert candidate['scope_complete']
    source = {'bank_question_id': 1, 'question_id': 'synthetic-source', 'full_score': 5, 'score_awarded': 4,
              'question_difficulty': 6, 'assessment': {'part_id': 'part1', 'eligible': True, 'part_difficulty': 6}}
    target = {'stable_key': SKILL, 'source_question_refs': [source]}
    diagnosis = {'students': [{'weak_points': [target]}]}
    metadata = module._source_practice_metadata(diagnosis)
    config = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g8-upper',
        target_keys=(SKILL,), teaching_progress_chapter_id='bnu24-math-g8-upper-c03')
    combined = []
    for student, score in [('synthetic-A', .6), ('synthetic-B', .7)]:
        entries, warnings = module._candidate_entries(profile={'student_id': student, 'score_rate': score},
            targets=[target], candidates=filtered, metadata=metadata, config=config,
            supplement_keys=(SKILL,), recent={7}, excluded={1})
        assert not warnings
        assert [(e['candidate']['question_id'], e['match_level'], e['selection_kind']) for e in entries] == [(2, 1, 'direct')]
        combined.extend(entries)
    # The same exercise is printed once and explicitly benefits both members.
    selected = _choose_practice_entries(combined, 8)
    assert len(selected) == 1
    assert {entry['student_id'] for entry in selected[0][1]} == {'synthetic-A', 'synthetic-B'}


def test_class_shortlist_uses_the_same_current_small_part_topics(current_link_module):
    from question_bank.services.assembly_assistant import shortlist_candidates
    from question_bank.services.question_read_service import QuestionBankReadService
    module = current_link_module
    point = {'knowledge_key': SKILL, 'knowledge_point': module.current_knowledge.node(SKILL).display_name,
             'mastery': .4, 'evidence_count': 1, 'source_question_refs': [
                 {'bank_question_id': 1, 'full_score': 5, 'score_awarded': 1,
                  'assessment': {'part_id': 'part1', 'part_difficulty': 6, 'eligible': True}}]}
    diagnosis = {'students': [{'student_id': 'synthetic', 'score_rate': .6, 'weak_points': [point]}],
                 'group_weak_points': [point]}
    result = shortlist_candidates(diagnosis=diagnosis,
        read_service=QuestionBankReadService(module.db_path, data_root=module.data_root),
        volume_id='bnu24-math-g8-upper', chapter_id='bnu24-math-g8-upper-c03', target_keys=[SKILL],
        question_type='', difficulty_min=5, difficulty_max=6, excluded_question_ids={1, 7})
    assert [q['question_id'] for q in result['candidates']] == [2]
    assert result['candidates'][0]['match_level'] == 1


@pytest.mark.parametrize('paper_mode', ['individual', 'shared'])
def test_new_skill_links_generate_and_persist_personal_and_shared_drafts(current_link_module, monkeypatch, paper_mode):
    from question_bank.recommendation.personalized import PersonalizedRecommendationConfig
    from tests.phase4.test_personalized_recommendation import _direct_diagnosis
    module = current_link_module
    diagnosis = _direct_diagnosis((('synthetic-A', .6, 1, SKILL), ('synthetic-B', .7, 1, SKILL)))
    monkeypatch.setattr(module, 'current_exam_question_ids', lambda diagnosis: {1})
    monkeypatch.setattr(module, '_recent_question_ids', lambda student_ids, **kwargs: {sid: {7} for sid in student_ids})
    config = PersonalizedRecommendationConfig(paper_mode=paper_mode, target_keys=(SKILL,), question_count=8,
        curriculum_volume_id='bnu24-math-g8-upper', teaching_progress_chapter_id='bnu24-math-g8-upper-c03')
    request = dict(request_token=('1' if paper_mode == 'individual' else '2') * 32,
                   diagnosis=diagnosis, config=config, actor_ref='synthetic')
    draft = module.create(**request)
    assert module.create(**request) == draft  # Stored draft reload uses the original request.
    assert len(draft['students']) == 2
    for student in draft['students']:
        assert [item['question_id'] for item in student['items']] == [2]
        assert student['shortages'][0]['missing_count'] == 7
        if paper_mode == 'shared':
            assert set(student['items'][0]['beneficiary_student_ids']) == {'synthetic-A', 'synthetic-B'}


def test_current_topics_override_embedded_topics_and_skill_only_links_keep_compatible_context(current_link_module):
    from question_bank.solution_evidence.knowledge_links import KnowledgeLink
    resolver = current_link_module.current_knowledge
    evidence = {'parts': [{'part_id': 'p1', 'evidence_points': [{'evidence_point_id': 'e1',
        'fine_term_links': [{'fine_term_id': OTHER_TOPIC, 'role': 'direct',
                            'core_resolution': {'status': 'resolved', 'stable_keys': [OTHER_TOPIC]}}]}]},
                          {'part_id': 'p2', 'evidence_points': [{'evidence_point_id': 'e2', 'fine_term_links': []}]}]}
    skill = KnowledgeLink(SKILL, SKILL, 'direct', 1)
    topic = KnowledgeLink(TOPIC, TOPIC, 'direct', 1)
    links = {'e1': [skill, topic], 'e2': [skill]}
    parts = part_facets(evidence, links, resolver, target_index(resolver), [TOPIC])
    assert parts[0]['topic_keys'] == [TOPIC]
    assert parts[1]['topic_keys'] == []  # Never spread the whole-question topic over other parts.
    links['e1'] = [skill]
    assert part_facets(evidence, links, resolver, target_index(resolver))[0]['topic_keys'] == [OTHER_TOPIC]
    links['e1'] = [skill, KnowledgeLink(TOPIC, TOPIC, 'supporting_prerequisite', 1),
                   KnowledgeLink(TOPIC, TOPIC, 'direct', 0), KnowledgeLink(TOPIC, TOPIC, 'direct', 1, 'unresolved')]
    assert part_facets(evidence, links, resolver, target_index(resolver))[0]['topic_keys'] == [OTHER_TOPIC]


def test_approved_criterion_does_not_borrow_tags_from_newer_profile(current_link_module, monkeypatch):
    from question_bank.database.schema import connect
    from question_bank.solution_evidence.knowledge_links import replace_point_links
    from question_bank.solution_evidence import part_assessments
    module = current_link_module
    with connect(module.db_path) as conn:
        row = conn.execute('SELECT * FROM question_solution_evidence_versions WHERE question_id=2').fetchone()
        evidence = json.loads(row['evidence_json'])
        new_id = hashlib.sha256(b'newer-different-evidence').hexdigest()
        evidence['version_id'] = new_id
        evidence['parts'][0]['evidence_points'][0]['target'] = '新版小问考查另一项技能'
        conn.execute("""INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,
            source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id,created_at)
            VALUES(?,2,?,'question-solution-evidence-v2',?,?,'approved','backfill','synthetic-new','synthetic',?,'2099-01-01')""",
            (new_id, row['source_content_hash'], new_id, json.dumps(evidence), module.current_knowledge.release_id))
        replace_point_links(conn, evidence_version_id=new_id, question_id=2, graph_release_id=module.current_knowledge.release_id,
            points=[{'part_id': 'part1', 'evidence_point_id': 'point1',
                     'links': [{'term_id': key, 'role': 'direct'} for key in (OTHER_SKILL, OTHER_TOPIC)]}])
    original = part_assessments.load_profiles
    def profiles(db_path, ids, **kwargs):
        result = original(db_path, ids, **kwargs)
        if 2 in ids:
            result[2] = {'available': True, 'evidence_version_id': new_id, 'revision': 'a' * 16,
                         'evidence': deepcopy(evidence), 'parts': [{'part_id': 'part1', 'difficulty': 6}]}
        return result
    monkeypatch.setattr(part_assessments, 'load_profiles', profiles)
    candidates, _, _ = module._source_snapshot(knowledge_keys=(SKILL,))
    candidate = next(q for q in candidates if q['question_id'] == 2)
    assert SKILL in candidate['stable_keys'] and OTHER_SKILL not in candidate['stable_keys']
    assert candidate['target_facets'][0]['topic_keys'] == [TOPIC]
    assert candidate['difficulty'] == 6  # Existing hardest-part rule still applies.


def test_task_equivalence_requires_explicit_operation_context_and_same_part(monkeypatch):
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule, PersonalizedRecommendationConfig, _choose_practice_entries, _draft_item
    release = load_release_for_taxonomy_revision(8)
    module = PersonalizedRecommendationModule.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    monkeypatch.setattr(module, '_enrich_source_ref', lambda ref, *args: ref)
    monkeypatch.setattr(module, '_links_for_metadata', lambda metadata: {})
    key, other = 'sk_bnu24_math_g8_upper_1_1_102', 'sk_bnu24_math_g8_upper_1_1_103'
    topic, chapter = 'kp_bnu24_math_g8_upper_1_1_1', 'kp_bnu24_math_g8_upper_1'
    def part(skill, leaf=topic, part_id='p1'):
        return facet(skills=(skill,), topics=(leaf,), section=chapter+'_1', chapter=chapter, part=part_id)
    def observation(mode, text):
        return {'part_id': 'p1', 'response_mode': mode, 'observable': text}
    source = {'session_id': 1, 'bank_question_id': 99, 'question_id': 'source', 'full_score': 5, 'score_awarded': 4,
              'question_difficulty': 4, 'assessment': {'granularity': 'part', 'part_id': 'p1'},
              'target_facets': [part(key)], 'practice_observations_by_key': {
                  key: [observation('process_required', '由勾股定理列出AB²+BC²=AC²')]}}
    texts = ['选出边长符合条件的组合', '测量风筝高度并写出理由', '建立梯子滑动前后的方程',
             '写出几何推理的一般依据', '利用勾股定理写出等式并解答']
    def candidate(qid, skill, mode, text, leaf=topic):
        return {'question_id': qid, 'stable_keys': [skill], 'required_keys': [skill], 'scope_complete': True,
                'target_facets': [part(skill, leaf)], 'difficulty': 4, 'question_text': texts[qid-1],
                'question_number': str(qid), 'stable_names': {skill: skill}, 'source_paper': 'synthetic',
                'criterion_version_id': 'a'*64, 'criterion_point_count': 1,
                'practice_observations_by_key': {skill: [observation(mode, text)]}}
    candidates = [candidate(1, key, 'exact_objective', '答案B'),
                  candidate(2, other, 'process_required', '由勾股定理写出AB²+BC²=AC²并求长'),
                  candidate(3, other, 'process_required', '由勾股定理写出AB²+BC²=AC²并求长', chapter+'_1_2'),
                  candidate(4, other, 'process_required', '写出推理依据'),
                  candidate(5, key, 'process_required', '由勾股定理列出等式')]
    config = PersonalizedRecommendationConfig(scope_keys=(chapter,), curriculum_volume_id='bnu24-math-g8-upper',
        teaching_progress_chapter_id='bnu24-math-g8-upper-c01')
    entries, _ = module._candidate_entries(profile={'student_id': 'A', 'score_rate': .9},
        targets=[{'stable_key': key, 'source_question_refs': [source]}], candidates=candidates, metadata={},
        config=config, supplement_keys=(key, other), recent=set(), excluded=set())
    assert {e['candidate']['question_id']: e['selection_kind'] for e in entries} == {
        1: 'direct', 2: 'task_matched', 3: 'supplement', 4: 'supplement', 5: 'direct'}
    selected = _choose_practice_entries(entries, 8)
    assert {e['candidate']['question_id'] for e, _ in selected} == {1, 2, 5}
    short = next(e for e in entries if e['candidate']['question_id'] == 1)
    assert short['practice_role'] == 'step_practice'
    entry = next(e for e in entries if e['selection_kind'] == 'task_matched')
    item = _draft_item(entry['candidate'], stage='direct', slot=1, student_id='A', target=entry['target'],
                       matched_key=entry['matched_key'], maintenance=False, selection_kind=entry['selection_kind'], match_details=entry)
    assert item['task_match_evidence'].startswith('由勾股定理')
    assert item['task_operations'] == ['pythagorean_equation']
    assert item['practice_tasks'][0]['code'] == 'process_practice'
    assert '不声称同技能命中' in item['reason']
    # A process response elsewhere in the whole question must not be credited
    # to the objective part that actually matched this training need.
    short['candidate']['practice_observations_by_key'][key].append({
        'part_id': 'p2', 'response_mode': 'process_required', 'observable': '由勾股定理写出完整证明过程'})
    item = _draft_item(short['candidate'], stage='direct', slot=1, student_id='A', target=short['target'],
        matched_key=key, maintenance=False, selection_kind='direct', match_details=short)
    assert item['practice_role'] == 'step_practice'
    assert item['practice_tasks'] == []
