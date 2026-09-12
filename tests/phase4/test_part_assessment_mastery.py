from dataclasses import replace
from datetime import UTC, datetime

import pytest

from question_bank.mastery.v2 import ExamEvidence, MasteryV2Parameters, compute_mastery_v2
from question_bank.solution_evidence.part_assessments import direct_targets, match_rubric_parts


@pytest.fixture()
def refined_training_source(tmp_path):
    from copy import deepcopy
    from question_bank.database.schema import initialize_database, connect
    from question_bank.solution_evidence.contracts import CoreResolution, QuestionSolutionEvidence
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.solution_evidence.part_assessments import save_profile, load_profiles
    from question_bank.training_criteria import QuestionAnalysisInputLoader, TrainingCriterionModule
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash, training_criteria_from_solution_evidence
    from tests.current_knowledge_support import install_current_knowledge
    from tests.phase4.test_solution_evidence_semantics import _evidence_payload
    root = tmp_path / 'data'
    path = root / 'databases' / 'question_bank.db'
    initialize_database(path)
    install_current_knowledge(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(1,'合成试卷','ready')")
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(1,1,'1','解答题','合成第一问及第二问','合成答案',2)")
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(1,'knowledge_point','kp_geo_construction','synthetic')")
        conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES('1','Q1',1,'manual','confirmed')")
    from question_bank.services.rich_content_service import save_question_rich_content
    save_question_rich_content(1, root=root/'question_bank'/'rich_content',
        question_blocks=[{'type':'text', 'text':'合成题目的完整排版正文'}])
    question = QuestionAnalysisInputLoader(db_path=path, data_root=root).load((1,))[0]
    class Resolver:
        def resolve(self, key):
            return CoreResolution(status='resolved', stable_keys=(key,), reason='synthetic')
    raw = _evidence_payload(1)
    raw['parts'][0]['evidence_points'][0]['fine_term_links'] = [{'fine_term_id':'kp_alg_linear_equation','fine_term_name':'一元一次方程','role':'direct'}]
    second = deepcopy(raw['parts'][0])
    second.update(part_id='part-2',label='第2问',full_answer='证明三角形全等')
    second['evidence_points'][0].update(evidence_point_id='step-2',target='证明全等',observable_evidence='列出全等条件',
        fine_term_links=[{'fine_term_id':'kp_geo_triangle_congruence','fine_term_name':'三角形全等','role':'direct'}])
    raw['parts'].append(second)
    old = deepcopy(raw)
    old['parts'][1]['evidence_points'][0]['target'] = '旧的判定目标'
    make = lambda payload: QuestionSolutionEvidence.from_model_dict(payload, question_id=1,
        source_content_hash=solution_evidence_source_content_hash(question), resolver=Resolver())
    criteria = TrainingCriterionModule(path)
    criteria.propose(question=question,draft=training_criteria_from_solution_evidence(make(old),question=question),
        source_kind='backfill',source_reference='synthetic-old',actor_ref='test',reason='synthetic')
    old_version = criteria.read(question)['current_version']['version_id']
    evidence = make(raw)
    version = SolutionEvidenceRepository(path).save(evidence,source_kind='backfill',source_reference='synthetic-refined',created_by='test')
    save_profile(path,question_id=1,evidence_version_id=version,created_by='test',parts=[
        {'part_id':part,'difficulty':difficulty,'source':'teacher','rationale':'合成难度依据'}
        for part,difficulty in [('part-1',2),('part-2',8)]])
    return path,root,question,evidence,load_profiles(path,[1])[1],old_version


def test_batch_criteria_reuses_loaded_questions_and_still_rejects_changed_content(refined_training_source, monkeypatch):
    from question_bank.training_criteria import TrainingCriterionModule, usable_training_criterion
    from question_bank.solution_evidence import part_assessments
    path, root, question, _, profile, _ = refined_training_source
    module = TrainingCriterionModule(path, data_root=root)
    module.prepare_part_refinement(question, profile)
    expected = module.read(question)
    assert usable_training_criterion(expected) is not None
    monkeypatch.setattr(part_assessments, 'current_inputs', lambda *args, **kwargs: pytest.fail('question was loaded twice'))
    assert module.read_many((question,))[1] == expected
    changed = replace(question, tagging_context=replace(question.tagging_context, question_text='已更改的合成题目'))
    assert usable_training_criterion(module.read_many((changed,))[1]) is None


def test_real_diagnosis_dependency_path_keeps_original_assets_and_forms_groups(refined_training_source, monkeypatch):
    import json
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from db_manager import DBManager
    from backend.api import dependencies
    from backend.api.app import create_app
    from backend.api.read_connections import request_read_context
    from question_bank.solution_evidence.part_assessments import load_profiles
    from question_bank.training_criteria.analysis import grading_config_skeleton_from_solution_evidence
    path, root, question, evidence, _, _ = refined_training_source
    grading_path = root/'databases'/'grading.db'
    grading = DBManager(grading_path)
    grading.initialize()
    rubric_question = grading_config_skeleton_from_solution_evidence(evidence,question_ref='Q1')['rubric_question']
    for index, part in enumerate(rubric_question['parts'],1):
        part.update(part_id=f'Q1(P{index})',part_score=5)
    rubric_path = root/'rubric.json'
    rubric_path.write_text(json.dumps({'questions':[rubric_question]}),encoding='utf-8')
    with grading._connect() as conn:
        conn.execute("INSERT INTO grading_sessions(id,session_name,rubric_path,answer_key_path,status,is_deleted) VALUES(1,'合成考试',?,'','completed',0)",(str(rubric_path),))
        for student in (1,2):
            conn.execute("INSERT INTO students(id,student_code,name,class_name) VALUES(?,?,?,?)",(student,f'S{student}','合成同名',f'合成{student}班'))
            conn.execute("INSERT INTO exam_papers(id,session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(?,1,'','',?,'matched','graded')",(student,student))
            conn.execute("INSERT INTO session_results(id,session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json) VALUES(?,1,?,?,10,5,0,'{}')",(student,student,student))
            for part,score in (('Q1(P1)',0),('Q1(P2)',5)):
                conn.execute("INSERT INTO session_details(result_id,question_id,score_awarded,deduction_reason,knowledge_ids) VALUES(?,?,?,'',?)",(student,part,score,json.dumps(['UNKNOWN'])))
    paths = SimpleNamespace(db_path=grading_path,qb_db_path=path,data_root=root)
    with request_read_context(paths) as context:
        assert context.diagnosis_service.data_root == root
        # This reproduces the former mistake without touching any real data.
        assert not load_profiles(context.question_bank_candidate,[1],connection=context.question_bank_connection)[1]['available']
        assert load_profiles(context.question_bank_candidate,[1],connection=context.question_bank_connection,data_root=root)[1]['available']
    app = create_app()
    original_paths = dependencies.get_path_manager
    app.dependency_overrides[original_paths] = lambda: paths
    monkeypatch.setattr(dependencies,'get_path_manager',lambda: paths)
    # Only paths are replaced: use the actual diagnosis, snapshot, grouping
    # dependency and response validation, unlike the former page-only harness.
    response = TestClient(app).post('/api/training/diagnosis',json={
        'scope':{'mode':'all'},'exam_scope':{'mode':'current','session_ids':[1]},
        'grouping':{'scope_keys':['kp_alg_linear_equation']},
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload['students']) == 2
    refs = [ref for student in payload['students'] for point in student['weak_points']
            if point['knowledge_key']=='kp_alg_linear_equation' for ref in point['source_question_refs']]
    assert len(refs) == 2 and all(ref['assessment']['granularity']=='part' for ref in refs)
    assert [member['student_id'] for member in payload['grouping']['groups'][0]['members']] == ['1','2']
    assert payload['grouping']['groups'][0]['targets'][0]['knowledge_key'] == 'kp_alg_linear_equation'


def test_refined_recommendation_freezes_current_criteria_and_returns_part_evidence(refined_training_source):
    from question_bank.database.schema import connect
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule, PersonalizedRecommendationConfig, _draft_item
    from question_bank.training_criteria import TrainingCriterionModule, usable_training_criterion
    from question_bank.personalized_papers.module import PersonalizedPaperModule
    from question_bank.solution_evidence.part_assessments import training_part_observations
    path,root,question,evidence,profile,old_version = refined_training_source
    criteria = TrainingCriterionModule(path)
    assert criteria.read(question)['state'] == 'part_evidence_changed'
    assert usable_training_criterion(criteria.read(question)) is None
    with connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM training_criterion_versions').fetchone()[0] == 1
    module = PersonalizedRecommendationModule(db_path=path,data_root=root)
    candidates,_,version = module._source_snapshot(prepare_refinements=True)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['difficulty'] == 8  # Previously the whole question was tagged 2.
    assert set(candidate['stable_keys']) == {'kp_alg_linear_equation','kp_geo_triangle_congruence'}
    assert candidate['criterion_version_id'] != old_version
    assert criteria.get_version(old_version)['criteria']['points'][1]['target'] == '旧的判定目标'
    assert module._source_snapshot()[2] == version
    assert module._source_snapshot(prepare_refinements=True)[2] == version
    eligible = module._eligible_candidates(candidates,stage='direct',target_keys=('kp_alg_linear_equation',),
        maintenance=False,used=set(),recent=set(),excluded=set(),config=PersonalizedRecommendationConfig(difficulty_max=5))
    assert eligible == []
    item = _draft_item(candidate,stage='direct',slot=1,student_id='1',target={},matched_key='kp_alg_linear_equation',maintenance=True)
    frozen = PersonalizedPaperModule(db_path=path,data_root=root)._prepare_items({'items':[item]},paper_instance_id='a'*32)[0]
    assert frozen['recommendation_snapshot']['part_assessment']['parts'][1]['difficulty'] == 8
    points = frozen['criterion_snapshot']['criteria']['points']
    observed = training_part_observations(profile,frozen['criterion_snapshot']['criteria'],[
        {'point_id':point['point_id'],'state':'met' if index == 0 else 'not_met'} for index,point in enumerate(points)])
    assert [(o['stable_key'],o['achieved'],o['difficulty']) for o in observed] == [
        ('kp_alg_linear_equation',1,2),('kp_geo_triangle_congruence',0,8)]
    from tests.phase4.test_personalized_recommendation import _diagnosis
    draft = module.create(request_token='a'*32, actor_ref='test',
        diagnosis=_diagnosis(student_ids=('SYN-S05',)),
        config=PersonalizedRecommendationConfig(question_count=8,expected_minutes=45,
            direct_ratio=1,prerequisite_ratio=0,transfer_ratio=0,difficulty_min=8,difficulty_max=8,training_intent="challenge",
            target_keys=('kp_alg_linear_equation',),exclude_current_exam_originals=False))
    reopened = module.get(draft['draft_id'])
    assert len(reopened['students'][0]['items']) == 1
    assert reopened['students'][0]['items'][0]['part_assessment'] == item['part_assessment']


@pytest.mark.parametrize('action',['approve','reject'])
def test_refinement_preserves_teacher_criterion_decision(refined_training_source, action):
    from question_bank.training_criteria import TrainingCriterionModule, CriterionReviewCommand, usable_training_criterion
    from question_bank.database.schema import connect
    path,root,question,evidence,profile,old_version = refined_training_source
    module = TrainingCriterionModule(path)
    workspace = module.read(question)
    module.review(CriterionReviewCommand(question_id=1,version_id=old_version,
        expected_revision=workspace['revision'],action=action,actor_ref='teacher',reason='合成教师决定'),question=question)
    module.prepare_part_refinement(question,profile)
    with connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM training_criterion_versions').fetchone()[0] == 1
    usable = usable_training_criterion(module.read(question))
    assert (usable['version_id'] if usable else None) == (old_version if action == 'approve' else None)


def test_paper_freeze_accepts_verified_type_rename_only(refined_training_source):
    from question_bank.training_criteria import TrainingCriterionModule, CriterionReviewCommand
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash, training_criteria_from_solution_evidence
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.solution_evidence.part_assessments import save_profile
    from question_bank.personalized_papers.module import PersonalizedPaperModule, PaperSourceChanged
    from question_bank.database.schema import connect
    path,root,question,evidence,profile,_ = refined_training_source
    legacy = replace(question,tagging_context=replace(question.tagging_context,question_type='解答题（证明）'))
    old_evidence = replace(evidence,source_content_hash=solution_evidence_source_content_hash(legacy))
    module = TrainingCriterionModule(path)
    workspace = module.propose(question=legacy,draft=training_criteria_from_solution_evidence(old_evidence,question=legacy),
        source_kind='backfill',source_reference='synthetic-old-type',actor_ref='test',reason='synthetic')
    version = workspace['current_version']['version_id']
    module.review(CriterionReviewCommand(question_id=1,version_id=version,expected_revision=workspace['revision'],
        action='approve',actor_ref='teacher',reason='合成教师决定'),question=legacy)
    evidence_id = SolutionEvidenceRepository(path).save(old_evidence,source_kind='backfill',source_reference='synthetic-old-type',created_by='test')
    save_profile(path,question_id=1,evidence_version_id=evidence_id,created_by='test',parts=profile['parts'])
    papers = PersonalizedPaperModule(db_path=path,data_root=root)
    student = {'items':[{'question_id':1,'criterion_version_id':version}]}
    assert papers._prepare_items(student,paper_instance_id='b'*32)[0]['criterion_version_id'] == version
    with connect(path) as conn:
        conn.execute("UPDATE questions SET question_text='真正改变题目内容' WHERE id=1")
    with pytest.raises(PaperSourceChanged):
        papers._prepare_items(student,paper_instance_id='c'*32)


def test_report_and_heatmap_share_exact_part_targets_and_revision(refined_training_source):
    from question_bank.training_criteria.analysis import grading_config_skeleton_from_solution_evidence
    from integration.question_tag_projection_service import QuestionTagProjectionService
    from backend.repositories.reporting import load_question_bank_part_context
    from backend.report_exports import _question_bank_report_source
    from question_bank.solution_evidence.part_assessments import save_profile
    from report import _knowledge_bucket_labels
    path,root,question,evidence,profile,_ = refined_training_source
    rubric_question = grading_config_skeleton_from_solution_evidence(evidence,question_ref='Q1')['rubric_question']
    # Allocation supplies scores and canonical exam part identities.
    for index,part in enumerate(rubric_question['parts'],1):
        part.update(part_id=f'Q1(P{index})',part_score=5)
    rubric = {'questions':[rubric_question]}
    projection = QuestionTagProjectionService(path).project_session(grading_session_id=1,rubric=rubric)
    backfill,assessments = load_question_bank_part_context(path,1,rubric)
    assert len(backfill) == 3 and backfill['Q1'] == []
    assert [entry['stable_key'] for entry in backfill['Q1(P1)']] == ['kp_alg_linear_equation']
    assert [entry['stable_key'] for entry in backfill['Q1(P2)']] == ['kp_geo_triangle_congruence']
    assert [item.assessment['part_difficulty'] for item in projection.items] == [2,8]
    assert assessments['Q1(P2)']['part_difficulty'] == 8
    assert _knowledge_bucket_labels({'question_id':'Q1(P1)','knowledge_ids':['old']},{'old':'旧整题知识'},backfill) == [(backfill['Q1(P1)'][0]['path'],backfill['Q1(P1)'][0]['label'])]
    revision = _question_bank_report_source(root/'databases'/'grading.db',1)
    save_profile(path,question_id=1,evidence_version_id=profile['evidence_version_id'],created_by='test',parts=[dict(p,difficulty=3) for p in profile['parts']])
    assert _question_bank_report_source(root/'databases'/'grading.db',1) != revision
    rubric_question['parts'][0]['steps'][0]['core_goal'] = '历史题目不匹配'
    excluded,assessment = load_question_bank_part_context(path,1,rubric)
    assert excluded == {'Q1':[],'Q1(P1)':[],'Q1(P2)':[]}
    assert _knowledge_bucket_labels({'question_id':'Q1(P1)','knowledge_ids':['old']},{'old':'旧整题知识'},excluded) == [('未命名知识点','未命名知识点')]
    assert load_question_bank_part_context(path,1,{}) == ({'Q1':[]},{})
    assert _knowledge_bucket_labels({'question_id':'Q1(P1)','knowledge_ids':['old']},{'old':'旧整题知识'},{'Q1':[]}) == [('未命名知识点','未命名知识点')]


@pytest.mark.parametrize('difficulty,score,expected', [(1, 0, .4), (10, 0, 1.3/2.75), (1, 1, 2.05/2.75), (10, 1, 2.55/3.25)])
def test_difficulty_changes_positive_and_negative_evidence(difficulty, score, expected):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = ExamEvidence('exam:one', 'kp_test', now, score, 1, part_difficulty=difficulty, difficulty_weight=2)
    result = compute_mastery_v2(stable_key='kp_test', as_of=now, exam_evidence=(evidence,), parameters=MasteryV2Parameters(formula_version='mastery-v2-formula-v2'))
    assert result.value == pytest.approx(expected, abs=1e-6)
    corrected = replace(evidence, teacher_correction=1-score, teacher_correction_reason='synthetic teacher decision')
    correction = compute_mastery_v2(stable_key='kp_test', as_of=now, exam_evidence=(corrected,), parameters=MasteryV2Parameters(formula_version='mastery-v2-formula-v2'))
    assert correction.contributions[0].teacher_corrected
    assert correction.contributions[0].effective_value == 1-score


@pytest.mark.parametrize('invalid', [None, 'source', 'parts'])
def test_bank_refinement_replacement_validates_before_write_and_reopens(tmp_path, monkeypatch, invalid):
    import copy
    import json
    from question_bank.database.schema import initialize_database, connect
    from question_bank.solution_evidence import part_assessments as profiles
    from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from tests.phase4.test_solution_evidence_semantics import _question, _evidence_payload, Resolver
    from tools.refine_question_mastery import import_profiles

    data = tmp_path / 'data'
    path = data / 'databases' / 'question_bank.db'
    output = data / 'reports' / 'refinement'
    output.mkdir(parents=True)
    initialize_database(path)
    question = _question(1)
    monkeypatch.setattr(profiles, 'current_inputs', lambda *args, **kwargs: {1: question})
    with connect(path) as conn:
        conn.execute("INSERT INTO questions(id,question_number,question_text) VALUES(1,'1','synthetic')")
        conn.execute("INSERT OR IGNORE INTO knowledge_tag_identities(stable_key,display_name,origin) VALUES('kp_alg_linear_equation','synthetic','local')")
    payload = _evidence_payload(1)
    payload['parts'][0]['evidence_points'][0]['fine_term_links'] = payload['parts'][0]['evidence_points'][0]['fine_term_links'][:1]
    evidence = QuestionSolutionEvidence.from_model_dict(payload, question_id=1, source_content_hash='c'*64, resolver=Resolver())
    version = SolutionEvidenceRepository(path).save(evidence, source_kind='backfill', source_reference='old', created_by='test')
    with connect(path) as conn:
        stored = conn.execute('SELECT evidence_json FROM question_solution_evidence_versions WHERE evidence_version_id=?', (version,)).fetchone()[0]
    replacement = copy.deepcopy(payload)
    replacement['parts'][0]['full_answer'] = 'A reviewed synthetic answer'
    entry = {'review_id': 'synthetic', 'replacement_evidence': replacement, 'replacement_reason': 'Synthetic stale source correction',
             'reviewed_source_hash': solution_evidence_source_content_hash(question),
             'parts': [{'part_id': 'part-1', 'difficulty': 4, 'source': 'codex_self', 'rationale': 'Synthetic estimate'}]}
    if invalid == 'source':
        entry['reviewed_source_hash'] = 'd'*64
    elif invalid == 'parts':
        entry['parts'][0]['part_id'] = 'unknown-part'
    corpus = [{'question': {'id': 1}, 'evidence_versions': [{'evidence_version_id': version,
               'source_content_hash': 'c'*64, 'evidence': json.loads(stored)}]}]
    for name, value in [('bank_scope.json', {'essay_scope': [{'id': 1}]}),
                        ('question_inputs.json', corpus), ('reviewed_part_profiles.json', {'1': entry})]:
        (output / name).write_text(json.dumps(value), encoding='utf-8')
    if invalid:
        with pytest.raises(ValueError, match='Replacement requires' if invalid == 'source' else 'unknown part'):
            import_profiles(data, output)
        with connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM question_solution_evidence_versions').fetchone()[0] == 1
            assert conn.execute('SELECT COUNT(*) FROM question_part_assessment_profiles').fetchone()[0] == 0
    else:
        assert import_profiles(data, output)['questions'] == 1
        loaded = profiles.load_profiles(path, [1])[1]
        assert loaded['available'] and loaded['parts'][0]['difficulty'] == 4
        assert loaded['evidence']['parts'][0]['full_answer'] == 'A reviewed synthetic answer'
        assert import_profiles(data, output)['unchanged'] == 1
        with connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM question_solution_evidence_versions').fetchone()[0] == 2
            assert conn.execute('SELECT evidence_json FROM question_solution_evidence_versions WHERE evidence_version_id=?', (version,)).fetchone()[0] == stored


def _part(name, key):
    return {'part_id': name, 'response_mode': 'constructed', 'evidence_points': [
        {'evidence_point_id': name+'-step-1', 'target': name, 'observable_evidence': 'show '+name,
         'fine_term_links': [{'role': 'direct', 'core_resolution': {'status': 'resolved', 'stable_keys': [key]}},
                             {'role': 'supporting_prerequisite', 'core_resolution': {'status': 'resolved', 'stable_keys': ['kp_aux']}}]}
    ]}


def test_part_mapping_uses_obligations_not_order_or_counts():
    parts = [_part('first', 'kp_a'), _part('second', 'kp_b')]
    rubric = {'parts': [{'part_id': 'exam-b', 'response_mode': 'constructed', 'steps': [{'core_goal': 'second', 'required_elements': ['show second']}]},
                        {'part_id': 'exam-a', 'response_mode': 'constructed', 'steps': [{'core_goal': 'first', 'required_elements': ['show first']}]}]}
    matched = match_rubric_parts(rubric, {'parts': parts})
    assert direct_targets(matched['exam-a']) == ('kp_a',)
    assert direct_targets(matched['exam-b']) == ('kp_b',)
    rubric['parts'][0]['steps'][0]['required_elements'] = ['changed obligation']
    assert match_rubric_parts(rubric, {'parts': parts}) == {}


def test_profiles_save_reopen_idempotency_and_source_change(tmp_path, monkeypatch):
    import json
    from question_bank.database.schema import initialize_database, connect
    from question_bank.solution_evidence import part_assessments as module
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from tests.phase4.test_solution_evidence_semantics import _question
    path = tmp_path / 'question_bank.db'
    initialize_database(path)
    question = _question(1)
    monkeypatch.setattr(module, 'current_inputs', lambda *args, **kwargs: {1: question})
    source = solution_evidence_source_content_hash(question)
    with connect(path) as conn:
        conn.execute("INSERT INTO questions(id,question_number,question_text) VALUES(1,'1','synthetic')")
        conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,source_kind,source_reference,created_by) VALUES(?,1,?,'question-solution-evidence-v2',?,?,'backfill','synthetic','test')", ('a'*64, source, 'b'*64, json.dumps({'parts': [_part('first','kp_a'),_part('second','kp_b')]})))
    values = [{'part_id': name, 'difficulty': difficulty, 'source': 'codex_self', 'rationale': 'synthetic estimate'} for name,difficulty in [('first',2),('second',8)]]
    assert module.save_profile(path,question_id=1,evidence_version_id='a'*64,parts=values,created_by='test')['revision'] == 1
    assert module.save_profile(path,question_id=1,evidence_version_id='a'*64,parts=values,created_by='test')['unchanged']
    assert [p['difficulty'] for p in module.load_profiles(path,[1])[1]['parts']] == [2,8]
    with pytest.raises(ValueError,match='Whole-question'):
        module.save_profile(path,question_id=1,evidence_version_id='a'*64,parts=[dict(v,source='whole_question') for v in values],created_by='test')
    question = _question(1, text='changed source')
    assert module.load_profiles(path,[1])[1]['available'] is False


def test_training_dependency_failure_is_not_counted_twice():
    from question_bank.solution_evidence.part_assessments import training_part_observations
    part = _part('first', 'kp_a')
    first = part['evidence_points'][0]
    second = {**first, 'evidence_point_id': 'first-step-2', 'target': 'dependent', 'depends_on': [first['evidence_point_id']]}
    part['evidence_points'].append(second)
    profile = {'available': True, 'current_source_content_hash': 'a'*64, 'evidence': {'parts': [part]}, 'parts': [{'part_id':'first','difficulty':5}]}
    criteria = {'solution_evidence': {'source_content_hash': 'a'*64}, 'points': [
        {'point_id': p['evidence_point_id'], **{k:p.get(k,[]) for k in ('target','observable_evidence','depends_on')}} for p in (first,second)]}
    final = [{'point_id':p['evidence_point_id'],'state':'not_met'} for p in (first,second)]
    observations = training_part_observations(profile,criteria,final)
    assert len(observations) == 1
    assert observations[0]['weight'] == 1
    assert observations[0]['achieved'] == 0
    final[0]['state'] = 'met'
    observations = training_part_observations(profile,criteria,final)
    assert len(observations) == 2
    assert sum(o['weight'] for o in observations) == 1


def test_blank_review_and_teacher_decision_keep_missing_distinct_from_zero():
    from types import SimpleNamespace
    from backend.api.schemas.training import TrainingWeakPoint
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.mastery.current import _exam_evidence, CurrentMastery
    from question_bank.solution_evidence.part_assessments import exam_assessment_state
    assessment = {'granularity': 'part', 'part_difficulty': 2}
    blank = exam_assessment_state(assessment, {'answer_is_blank_or_no_valid_work': True}, teacher_final=False)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    reference = dict(session_id=1, session_name='synthetic', question_id='Q1', bank_question_id=1,
                     score_awarded=0, full_score=5, assessment=blank)
    evidence = _exam_evidence(reference, student_id='1', stable_key='kp_a', session_times={1:now})
    calculated = compute_mastery_v2(stable_key='kp_a', as_of=now, exam_evidence=(evidence,))
    assert calculated.value is None
    reviewed = exam_assessment_state(assessment, {'need_review': True}, teacher_final=True)
    assert reviewed['eligible']
    assert exam_assessment_state(assessment, {'answer_is_blank_or_no_valid_work': True}, teacher_final=True, teacher_score=3)['eligible']
    assert exam_assessment_state(assessment, {'need_review': True}, teacher_final=False)['eligible'] is False
    point = dict(knowledge_key='kp_a', knowledge_point='synthetic', mastery=0, score_sum=0,
                 full_score_sum=5, deduction_count=1, evidence_count=1, exam_count=1,
                 source_question_refs=[reference], actionable_reasons=[], tag_context={},
                 error_counts={'primary': {}, 'secondary': {}})
    students = [{'student_id': '1', 'weak_points': [point]}]
    resolver = SimpleNamespace(nodes=[SimpleNamespace(stable_key='kp_a', display_name='synthetic')], relations=[])
    current = CurrentMastery('kp_a','synthetic','insufficient_evidence',None,0,0,'test')
    service = object.__new__(DiagnosisProfileService)
    service._merge_current_mastery(students, {('1','kp_a'):current}, resolver=resolver)
    parsed = TrainingWeakPoint.model_validate(point)
    assert parsed.mastery is None and parsed.evidence_count == 0
    assert parsed.source_question_refs[0].assessment['reason'] == 'blank_or_no_valid_work'


def test_parent_receives_one_observation_with_all_target_shares(tmp_path):
    from types import SimpleNamespace
    from question_bank.mastery.current import CurrentMastery, CurrentMasteryCalculator
    nodes = [SimpleNamespace(stable_key=key, display_name=key) for key in ('kp_a','kp_b','kp_parent')]
    relations = [SimpleNamespace(source_key=key,target_key='kp_parent',relation_type='parent') for key in ('kp_a','kp_b')]
    calculator = CurrentMasteryCalculator(tmp_path/'unused.db', SimpleNamespace(nodes=nodes,relations=relations))
    direct = {('1',key):CurrentMastery(key,key,'available',.65,1,.5,'test',
                evidence_contributions=((f'exam:one:target:{key}',.5,.5 if key == 'kp_a' else 0),),
                direct_evidence_count=1) for key in ('kp_a','kp_b')}
    parent = calculator._with_parent_rollups(direct)[('1','kp_parent')]
    assert parent.evidence_count == 1
    assert parent.effective_weight == 1
    assert parent.value == pytest.approx(.6)


def test_new_analysis_estimates_survive_checkpoint_and_linked_adoption(tmp_path, monkeypatch):
    from question_bank.solution_evidence import part_assessments as profiles
    from tests.phase4.test_solution_evidence_semantics import (
        _question, _combined_payload, QueueGateway, Resolver, VOLUME_ID,
        InMemoryCombinedQuestionAnalysisModule, ConfigQuestionAnalysisSource,
        DeferredCombinedAnalysisBundle, DeferredCombinedProjectionWriter,
        ConfirmedQuestionAdoptionLink, SuccessfulTagWriter, FineTermCoreMappingRepository,
        SolutionEvidenceRepository,
    )
    from question_bank.database.schema import initialize_database, connect
    question = _question(1, source_ref='Q1')
    payload = _combined_payload(1)
    raw = payload['results'][0]
    raw['part_assessments'] = [{'part_id': p['part_id'], 'difficulty': 3,
                                'rationale': 'synthetic reasoning demand'} for p in raw['solution_evidence']['parts']]
    bundle = InMemoryCombinedQuestionAnalysisModule(gateway=QueueGateway([payload]),resolver=Resolver()).analyze(
        operation_id='synthetic-parts', curriculum_volume_id=VOLUME_ID,
        sources=(ConfigQuestionAnalysisSource('Q1',question),))
    assert bundle.status == 'succeeded'
    checkpoint = bundle.to_dict()
    assert checkpoint['items'][0]['schema_version'] == 'deferred-combined-analysis-item-v5'
    restored = DeferredCombinedAnalysisBundle.from_dict(checkpoint,resolver=Resolver())
    assert restored.to_dict() == checkpoint
    path = tmp_path/'question_bank.db'
    initialize_database(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO questions(id,question_number,question_text) VALUES(1,'1','synthetic')")
    monkeypatch.setattr(profiles,'current_inputs',lambda *args,**kwargs:{1:question})
    writer = DeferredCombinedProjectionWriter(tag_writer=SuccessfulTagWriter(),
        mapping_repository=FineTermCoreMappingRepository(path), evidence_repository=SolutionEvidenceRepository(path))
    result = writer.adopt_linked(restored.get('Q1'),question=question,
        link=ConfirmedQuestionAdoptionLink(source_question_ref='Q1',bank_question_id=1,confirmed_by='test'))
    assert result['evidence_status'] == 'succeeded'
    saved = profiles.load_profiles(path,[1])[1]
    assert saved['available']
    assert saved['parts'][0]['difficulty'] == 3
    assert saved['parts'][0]['source'] == 'model'
    assert saved['evidence_version_id'] == result['source_evidence_version_id']
    from question_bank.solution_evidence.repository import SolutionEvidenceProjectionWriter
    direct_writer = SolutionEvidenceProjectionWriter(mapping_repository=Resolver(),evidence_repository=SolutionEvidenceRepository(path))
    direct_writer.write(question,raw['solution_evidence'],model_name='synthetic',operation_id='direct-parts',
                        part_assessments=raw['part_assessments'])
    assert profiles.load_profiles(path,[1])[1]['parts'][0]['difficulty'] == 3
