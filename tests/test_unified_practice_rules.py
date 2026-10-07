"""Unified matching contracts, with synthetic observations and isolated storage."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import sqlite3
import json

import pytest

from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig, PersonalizedRecommendationModule,
    _difficulty_plan, _choose_practice_entries, _common_entries, _direct_preference,
    _valid_difficulty_features, _group_similarity,
    _paper_diversity_allowed,
)
from question_bank.services.assembly_assistant import shortlist_candidates
from question_bank.services.question_read_service import QuestionBankReadService
from tests.training.test_personalized_recommendation import (
    bnu24_difficulty_module, direct_module, _direct_diagnosis, BNU_TARGET, BNU_CHAPTER4,
)


def observation(index, difficulty, score, **assessment):
    return {"session_id": index, "question_id": "Q1", "full_score": 5, "score_awarded": score,
            "assessment": {"eligible": True, "evidence_weight": 1, "granularity": "part",
                           "part_id": "p1", "part_difficulty": difficulty, **assessment}}


def test_repeated_success_counts_deduplicate_and_ignore_ineligible_evidence():
    refs = [observation(1, 3, 5), observation(1, 3, 5), observation(2, 3, 5),
            observation(3, 3, 0, eligible=False)]
    plan = _difficulty_plan({}, .5, 8, {'source_question_refs': refs})
    assert (plan['evidence_count'], plan['correct_count']) == (2, 2)
    refs.append(observation(4, 3, 4))
    plan = _difficulty_plan({}, .5, 8, {'source_question_refs': refs})
    assert (plan['evidence_count'], plan['correct_count']) == (3, 2)


@pytest.mark.parametrize('members', [('A',), ('A', 'B')])
def test_repeated_consolidation_is_lower_but_remains_available(members):
    def entry(qid, sid, purpose, scores):
        target = {'display_name': f'技能·合成技能{qid}', 'difficulty_plan': _difficulty_plan(
            {}, .5, 8, {'source_question_refs': [observation(n, 3, score) for n, score in enumerate(scores)]})}
        return {'candidate': {'question_id': qid, 'stable_keys': [f'sk_{qid}'], 'question_type': '选择题'},
                'student_id': sid, 'key': f'sk_{qid}', 'matched_key': f'sk_{qid}', 'selection_kind': 'direct',
                'target': target, 'practice_purpose': purpose, 'match_level': 1, 'distance': 0, 'preference': 0}
    entries = [entry(q, sid, purpose, scores) for sid in members for q, purpose, scores in (
        (1, 'consolidation', [5, 5]), (2, 'new', []), (3, 'consolidation', [5]))]
    chosen = _choose_practice_entries(_common_entries(entries, members), 3)
    assert [e['candidate']['question_id'] for e, _ in chosen] == [2, 3, 1]
    # Another skill on the same question still needs remediation.
    loss = entry(1, members[0], 'remediation', [0, 5])
    loss['key'] = loss['matched_key'] = 'sk_other'
    entries.append(loss)
    assert _choose_practice_entries(_common_entries(entries, members), 1)[0][0]['candidate']['question_id'] == 1


def test_reason_includes_each_matched_need_without_duplicate_evidence():
    from question_bank.recommendation.personalized import _practice_reason_summary, _member_entries
    entries = []
    for key, purpose, scores in [('sk_a', 'consolidation', [5, 5]), ('sk_b', 'remediation', [0, 5]), ('sk_c', 'new', [])]:
        entries.append({'student_id': 'A', 'key': key, 'matched_key': key, 'selection_kind': 'direct',
                        'candidate': {'stable_names': {key: '章节｜技能·'+key}}, 'distance': 0, 'match_level': 1,
                        'target': {'stable_key': key, 'difficulty_plan': _difficulty_plan({}, .5, 8,
                            {'source_question_refs': [observation(n, 3, score) for n, score in enumerate(scores)]})},
                        'practice_purpose': purpose})
    text = _practice_reason_summary([*entries, entries[1]])
    assert text.count('补弱·sk_b') == 1
    assert '2次有效作答中1次满分、1次失分' in text
    assert '已多次答对，降低巩固优先级' in text
    assert '暂无直接作答证据' in text
    assert _member_entries(entries)['A']['key'] == 'sk_b'


def test_saved_draft_and_export_source_check_use_the_preview_scope(direct_module, monkeypatch):
    diagnosis = _direct_diagnosis()
    config = PersonalizedRecommendationConfig(scope_keys=(BNU_CHAPTER4,), target_keys=(BNU_TARGET,),
                                               curriculum_volume_id="bnu24-math-g7-lower")
    preview = direct_module.evaluate_candidates(diagnosis=diagnosis, config=config)
    expected = {sid: [e['candidate']['question_id'] for e, _ in _choose_practice_entries(entries, 10)]
                for sid, entries in preview['pools'].items()}
    calls = []
    original = direct_module._source_snapshot
    def scoped(**kwargs):
        assert kwargs.get('knowledge_keys'), 'draft/export must not load the entire bank'
        assert kwargs.get('candidate_config') == config
        calls.append(kwargs['knowledge_keys'])
        return original(**kwargs)
    monkeypatch.setattr(direct_module, '_source_snapshot', scoped)
    draft = direct_module.create(request_token='d'*32, diagnosis=diagnosis, config=config, actor_ref='test')
    for student in draft['students']:
        assert {i['question_id'] for i in student['items']} == set(expected[student['student_id']])
    assert direct_module.ensure_current(draft['draft_id'])['draft_id'] == draft['draft_id']
    assert len(calls) >= 2 and len(set(calls)) == 1


def test_comprehensive_scope_includes_earlier_chapters_and_preserves_focused_scope(direct_module):
    from question_bank.recommendation.personalized import resolve_practice_scope
    diagnosis = _direct_diagnosis()
    base = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g7-lower',
                                             teaching_progress_chapter_id='bnu24-math-g7-lower-c04')
    resolved = resolve_practice_scope(base, diagnosis, direct_module.current_knowledge)
    assert len(resolved.scope_keys) == 4
    assert resolved.scope_keys[-1] == BNU_CHAPTER4
    focused = replace(base, scope_keys=(BNU_CHAPTER4,))
    assert resolve_practice_scope(focused, diagnosis, direct_module.current_knowledge) == focused
    inferred = resolve_practice_scope(replace(base, teaching_progress_chapter_id=''), diagnosis, direct_module.current_knowledge)
    assert inferred.scope_keys == resolved.scope_keys
    many = {'students': [{'weak_points': [{'knowledge_key': f'{BNU_CHAPTER4}_synthetic_{i}', 'evidence_count': 1}
                                          for i in range(150)]}]}
    assert resolve_practice_scope(replace(base, teaching_progress_chapter_id=''), many, direct_module.current_knowledge).scope_keys == resolved.scope_keys
    with pytest.raises(ValueError, match='已学到'):
        resolve_practice_scope(replace(base, teaching_progress_chapter_id=''), {'students': []}, direct_module.current_knowledge)


def test_new_needs_and_different_methods_precede_small_distance_advantages(monkeypatch):
    def entry(qid, key, method, distance):
        return {'candidate': {'question_id': qid, 'stable_keys': [f'sk_{qid}'],
                'similarity_profile': {'tags': [{'tag_type': 'method', 'tag_value': method}]}},
                'student_id': 'A', 'key': key, 'selection_kind': 'direct', 'practice_purpose': 'consolidation',
                'distance': distance, 'preference': 0, 'match_level': 1}
    first = entry(1, 'need_a', 'method_a', 0)
    repeated_need = entry(2, 'need_a', 'method_b', .1)
    repeated_method = entry(3, 'need_b', 'method_a', .2)
    new_method = entry(4, 'need_b', 'method_b', .5)
    chosen = _choose_practice_entries([first, repeated_need, repeated_method, new_method], 2)
    assert [e['candidate']['question_id'] for e, _ in chosen] == [1, 4]
    import question_bank.recommendation.personalized as recommendation
    original = recommendation.text_similarity
    calls = []
    def counted(left, right):
        calls.append((left, right))
        return original(left, right)
    monkeypatch.setattr(recommendation, "text_similarity", counted)
    left = {"solution_template": "TEST numerical derivation " * 3}
    right = {"solution_template": "TEST numerical derivation " * 3}
    memo = {}
    assert recommendation._pattern_count(left, [right], pair_memo=memo) == 1
    assert recommendation._pattern_count(left, [right, right], pair_memo=memo) == 2
    assert len(calls) == 1
    assert recommendation._pattern_count(left, [], pair_memo=memo) == 0
    # A candidate that loses on already-covered needs must not trigger an
    # expensive text comparison. Equal leading ranks still use variety.
    pattern_candidates = []
    original_pattern = recommendation._pattern_count
    def counted_pattern(candidate, printed, **kwargs):
        if printed:
            pattern_candidates.append(candidate['question_id'])
        return original_pattern(candidate, printed, **kwargs)
    monkeypatch.setattr(recommendation, '_pattern_count', counted_pattern)
    chosen = _choose_practice_entries([first, repeated_need, repeated_method, new_method], 2)
    assert [e['candidate']['question_id'] for e, _ in chosen] == [1, 4]
    assert set(pattern_candidates) == {3, 4}


def test_same_skill_candidates_are_not_automatically_folded_as_similar(monkeypatch):
    from question_bank.recommendation.personalized import paper_similarity_allowed
    first = {'question_id': 1, 'stable_keys': ['sk_a'], 'question_text': '合成测量任务'}
    second = {'question_id': 2, 'stable_keys': ['sk_a'], 'question_text': '合成图形推理'}
    assert paper_similarity_allowed(first, [second])
    assert not _paper_diversity_allowed(first, [second])
    import question_bank.recommendation.personalized as recommendation
    similarity_calls = []

    def count_similarity(candidate, selected):
        similarity_calls.append(1)
        return paper_similarity_allowed(candidate, selected)

    monkeypatch.setattr(recommendation, "paper_similarity_allowed", count_similarity)
    pair_memo = {}
    relaxed = PersonalizedRecommendationConfig(max_questions_per_skill=2)
    assert _paper_diversity_allowed(first, [second], relaxed, pair_memo=pair_memo)
    assert _paper_diversity_allowed(first, [second], relaxed, pair_memo=pair_memo)
    assert len(similarity_calls) == 1
    # A reusable content check never bypasses the current paper's skill quota.
    assert not _paper_diversity_allowed(first, [second], pair_memo=pair_memo)
    assert _paper_diversity_allowed(first, [], pair_memo=pair_memo)
    assert not _paper_diversity_allowed(first, [second, second], relaxed, pair_memo=pair_memo)
    from question_bank.recommendation.personalized import paper_task_duplicates
    triplets = {'question_id': 21, 'question_type': '选择题', 'difficulty': 2,
        'stable_keys': ['sk_TEST_triples'], 'question_text': '下列各组数中，是勾股数的是 A. 3,4,5 B. 2,3,4'}
    triangle = {'question_id': 22, 'question_type': '选择题', 'difficulty': 2,
        'stable_keys': ['sk_TEST_triangle'], 'question_text': '下列各组边长中，不能组成直角三角形的是 A. 5,12,13 B. 3,4,6'}
    assert paper_similarity_allowed(triangle, [triplets])  # keep both as candidates
    # Different skill sets never form a task duplicate, even on similar tasks.
    assert paper_task_duplicates(triangle, [triplets]) == []
    assert _paper_diversity_allowed(triangle, [triplets])
    assert _paper_diversity_allowed({**triangle, 'question_type': '解答题'}, [triplets])
    assert _paper_diversity_allowed({**triangle, 'question_text': '下列各组边长中，不能组成直角三角形的是 A. √2,√3,√5'}, [triplets])
    assert _paper_diversity_allowed({**triangle, 'question_text': '如图，利用正方形面积关系求直角三角形边长'}, [triplets])
    assert _paper_diversity_allowed({**triangle, 'target_facets': [{'part_id': 'one'}, {'part_id': 'two'}]}, [triplets])
    root = {'question_id': 23, 'question_type': '填空题', 'stable_keys': ['sk_TEST_root'], 'question_text': '16的算术平方根是____'}
    variant = {**root, 'question_id': 24, 'question_text': '25的算术平方根是____'}
    # Same skill, single part and the same response form fold a numeric variant.
    assert paper_task_duplicates(variant, [root]) == [23]
    assert not _paper_diversity_allowed(variant, [root])
    assert not _paper_diversity_allowed(variant, [root], relaxed, pair_memo=pair_memo)
    assert paper_task_duplicates({**variant, 'question_text': '√2的算术平方根是____'}, [root]) == []
    other_skill = {**variant, 'stable_keys': ['sk_TEST_other_root']}
    assert paper_task_duplicates(other_skill, [root]) == []
    assert _paper_diversity_allowed(other_skill, [root])
    assert _paper_diversity_allowed({**other_skill, 'question_text': '25的平方根是____'}, [root])
    assert _paper_diversity_allowed({**other_skill, 'question_text': '若一个数的算术平方根是5，则这个数是____'}, [root])
    assert _paper_diversity_allowed({**other_skill, 'question_text': '0的算术平方根是____'}, [root])
    def entry(q):
        return {'candidate': q, 'student_id': 'TEST-A', 'key': q['stable_keys'][0], 'selection_kind': 'direct',
            'practice_purpose': 'remediation', 'distance': abs(q['difficulty']-2), 'preference': 0, 'match_level': 1}
    chosen = _choose_practice_entries([entry(triplets), entry({**triangle, 'difficulty': 3})], 10)
    assert [e['candidate']['question_id'] for e, _ in chosen] == [21, 22]
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule
    module = object.__new__(PersonalizedRecommendationModule)
    module._source_snapshot = lambda **_: ((triplets, triangle), (), {})
    assert module.paper_rule_violations([21, 22]) == []
    # Leading score labels such as （3分） never distinguish reprints.
    from question_bank.recommendation.personalized import _practice_template
    assert _practice_template('1．（3分）已知直角三角形两直角边分别为3和4，则斜边长为____．') == \
        _practice_template('已知直角三角形两直角边分别为3和4，则斜边长为____．')
    score_a = {'question_id': 41, 'question_type': '填空题', 'stable_keys': ['sk_TEST_hyp'],
        'question_text': '（3分）已知直角三角形两直角边分别为3和4，则斜边长为____．'}
    score_b = {**score_a, 'question_id': 42,
        'question_text': '已知直角三角形两直角边分别为3和4，则斜边长为____．'}
    assert not paper_similarity_allowed(score_a, [score_b])
    # Reprints keep the same stem and worked solution even when the imported
    # picture encodings or skill tags differ.
    reprint_stem = '如图，在直角三角形ABC中，∠C为直角，两条直角边AC与BC的长度分别为3和4，求斜边AB的长度。'
    reprint_solution = '解：由勾股定理，斜边的平方等于两条直角边的平方和，即3的平方加4的平方等于25，所以斜边AB的长度为5。'
    reprint_c = {'question_id': 43, 'question_type': '填空题', 'stable_keys': ['sk_TEST_x'],
        'question_text': reprint_stem, 'solution_observable': reprint_solution,
        'image_identity': ('TEST_img_a',)}
    reprint_d = {**reprint_c, 'question_id': 44, 'stable_keys': ['sk_TEST_y'],
        'question_text': '（3分）' + reprint_stem, 'image_identity': ('TEST_img_b',)}
    assert not paper_similarity_allowed(reprint_d, [reprint_c])
    assert not paper_similarity_allowed(reprint_c, [reprint_d])
    # A substantially different worked solution is not a reprint.
    variant_e = {**reprint_d, 'question_id': 45,
        'solution_observable': '答：将各选项逐一代入条件检验，先排除与已知长度矛盾的选项，再比较剩余选项得到唯一正确答案。'}
    assert paper_similarity_allowed(variant_e, [reprint_c])
    # Without a worked solution the reprint rule does not apply.
    no_solution = {**reprint_d, 'question_id': 46, 'solution_observable': ''}
    assert paper_similarity_allowed(no_solution, [reprint_c])
    # Identical stem text, options and formulas are the same question even
    # when picture encodings or skill tags differ.
    from question_bank.services.question_identity import text_identity_from_exact_key
    def exact_key(text, images=()):
        return 'exact-v4:' + json.dumps(
            {"text": text, "options": [], "formulas": [], "images": list(images)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    long_stem = '已知直角三角形两直角边分别为3和4，求斜边长为多少，并说明理由'
    key_a = exact_key(long_stem + '[[IMAGE:2x2:TEST_aaa]]', ['2x2:TEST_aaa'])
    key_b = exact_key(long_stem + '[[IMAGE:9x9:TEST_bbb]]', ['9x9:TEST_bbb'])
    identity = text_identity_from_exact_key(key_a)
    assert identity and identity == text_identity_from_exact_key(key_b)
    assert text_identity_from_exact_key(exact_key('如图求x的值[[IMAGE:1x1:TEST_c]]', ['1x1:TEST_c'])) == ''
    assert text_identity_from_exact_key(exact_key('如图求x的值')) != ''
    assert text_identity_from_exact_key('exam-original:TEST') == ''
    assert text_identity_from_exact_key('') == ''
    same_text_a = {'question_id': 51, 'question_type': '填空题', 'stable_keys': ['sk_TEST_p'],
        'question_text': '题面文字完全相同的两题之一', 'text_identity': 'text-v1:TEST_same',
        'image_identity': ('TEST_img_a',)}
    same_text_b = {**same_text_a, 'question_id': 52, 'stable_keys': ['sk_TEST_q'],
        'image_identity': ('TEST_img_b',)}
    assert not paper_similarity_allowed(same_text_b, [same_text_a])
    assert not paper_similarity_allowed(same_text_a, [same_text_b])
    # A supplied indexed exact key is used verbatim: the text identity derives
    # from it and exact_question_key is never recomputed.
    import question_bank.services.question_identity as identity_module
    from pathlib import Path
    monkeypatch.setattr(identity_module, "exact_question_key",
                        lambda *args, **kwargs: pytest.fail("exact_question_key recomputed"))
    supplied = identity_module.question_identities(
        {"id": 61, "question_text": long_stem, "image_paths": []},
        data_root=Path("."), image_cache={}, exact_key=key_a)
    assert supplied["duplicate_identity"] == key_a
    assert supplied["text_identity"] == text_identity_from_exact_key(key_a)
    assert supplied["practice_identity"].startswith("exam-original:")


def test_repeated_successes_survive_one_trap_and_duplicate_tags():
    refs = [observation(i, 6.5, 5) for i in range(1, 5)] + [observation(5, 3, 0)]
    plan = _difficulty_plan({}, .2, 8, {"source_question_refs": refs})
    assert plan["aim"] == 6.5
    assert plan["evidence_count"] == 5  # latest-three exclusions do not truncate ability history
    assert plan["confidence"] == "repeated"
    assert plan == _difficulty_plan({}, .2, 8, {"source_question_refs": list(reversed(refs))*3})
    assert _difficulty_plan({}, .2, 8, {"source_question_refs": [refs[-1]]})["aim"] == 1.5
    assert _difficulty_plan({}, None, 8, {})["confidence"] == "unknown"
    assert _difficulty_plan({}, None, 8, {})["evidence_count"] == 0


def test_step_observations_count_one_attempt_and_ignore_ineligible_scores():
    steps = [observation(1, 6, 5, granularity="step", step_id="a"),
             observation(1, 6, 0, granularity="step", step_id="b"),
             observation(2, 8, 5, eligible=False)]
    plan = _difficulty_plan({}, None, 8, {"source_question_refs": steps*2})
    assert plan["evidence_count"] == 1
    assert plan["aim"] == 5


def test_auxiliary_tags_and_actual_causes_help_rank_without_creating_evidence():
    source = {"practice_tags": {k: [k] for k in ("method", "model", "thought", "ability", "special_type")},
              "causes": [{"pattern": "漏看非零条件", "pattern_status": "confirmed"}]}
    plain = {"stable_keys": [BNU_TARGET]}
    rich = {**plain, "practice_tags": source["practice_tags"], "error_patterns": [{"pattern": "漏看非零条件"}]}
    before = deepcopy(source)
    assert _direct_preference(rich, BNU_TARGET, source) > _direct_preference(plain, BNU_TARGET, source)
    assert source == before
    from question_bank.recommendation.personalized import _fixed_preference, _json, _preference_candidate_input
    _fixed_preference.cache_clear()
    for candidate in (plain, rich):
        inputs = (_json(candidate), BNU_TARGET, _json(source), "[]")
        assert _fixed_preference(*inputs) == _direct_preference(candidate, BNU_TARGET, source, tasks=[])
        misses = _fixed_preference.cache_info().misses
        assert _fixed_preference(*inputs) == _direct_preference(candidate, BNU_TARGET, source, tasks=[])
        assert _fixed_preference.cache_info().misses == misses
    changed = deepcopy(source)
    changed['causes'][0]['pattern_status'] = 'rejected'
    assert _fixed_preference(_json(rich), BNU_TARGET, _json(changed), "[]") == (
        _direct_preference(rich, BNU_TARGET, changed, tasks=[]))
    assert _fixed_preference(_json(rich), BNU_TARGET, _json(changed), "[]") < (
        _fixed_preference(_json(rich), BNU_TARGET, _json(source), "[]"))
    # The scorer uses response modes, while full point text remains available to
    # the separate response-coverage check and to the eventual draft.
    rich.update(practice_observations_by_key={BNU_TARGET: [
        {"part_id": "TEST-part", "response_mode": "process_required", "observable": "TEST-observed"}]},
        target_facets=[{"part_id": "TEST-part"}], solution_observable="TEST-solution")
    tasks = [{"code": "written_reasoning", "response_modes": ["process_required"]}]
    for mode in ("process_required", "short_answer_points", "exact_objective", "unknown"):
        rich["practice_observations_by_key"][BNU_TARGET][0]["response_mode"] = mode
        compact = _preference_candidate_input(rich)
        assert "TEST-observed" not in compact and "TEST-solution" not in compact
        assert _fixed_preference(compact, BNU_TARGET, _json(source), _json(tasks)) == (
            _direct_preference(rich, BNU_TARGET, source, tasks=tasks))


def test_content_cached_matching_follows_parts_and_knowledge_location():
    from question_bank.recommendation.personalized import _fixed_target_matches, _match_facets_input
    from question_bank.recommendation.target_matching import match_target
    _fixed_target_matches.cache_clear()
    sources = [{'part_id': 'TEST-source', 'direct_keys': ['sk_TEST']}]
    candidates = [{'part_id': 'TEST-candidate', 'skill_keys': ['sk_TEST'],
                   'section_keys': ['TEST-section'], 'chapter_keys': ['TEST-chapter']}]
    def matched(section, chapter):
        cached = _fixed_target_matches('sk_TEST', _match_facets_input(sources),
            _match_facets_input(candidates), section, chapter)
        expected = match_target('sk_TEST', sources, candidates, {'sk_TEST': {'section': section, 'chapter': chapter}})
        decoded = [json.loads(value) for value in cached]
        assert decoded == ([expected] if expected else [])
        return decoded
    assert matched('TEST-section', 'TEST-chapter')[0]['match_level'] == 2
    matched('TEST-section', 'TEST-chapter')[0]['match_level'] = 99
    assert matched('TEST-section', 'TEST-chapter')[0]['match_level'] == 2
    assert _fixed_target_matches.cache_info().hits == 2
    assert matched('TEST-section', 'TEST-other')[0]['match_level'] == 4
    assert not matched('TEST-other', 'TEST-other')
    sources[0]['topic_keys'] = ['TEST-topic']
    candidates[0]['topic_keys'] = ['TEST-topic']
    assert matched('TEST-other', 'TEST-other')[0]['match_level'] == 1
    candidates[0]['skill_keys'] = ['sk_TEST-other']
    assert matched('TEST-other', 'TEST-other')[0]['match_level'] == 3


def test_difficulty_features_use_current_content_and_affect_preference():
    from question_bank.services.standard_difficulty import (
        compatible_difficulty_content_hashes, difficulty_source_content_hash_matches,
        load_assessment, question_content_fingerprint,
    )
    from question_bank.models.tag_schema import TaggingContext
    from question_bank.training_criteria import QuestionAnalysisInput
    question = {"question_text": "合成条件", "answer_text": "合成解析", "question_type": "选择题"}
    row = {"source_content_hash": question_content_fingerprint(question), "part_id": "part1",
           "features_json": json.dumps({"solo": 2, "reasoning": 1, "trap": 2})}
    features = _valid_difficulty_features(question, [row])
    assert features and features[0]["features"]["trap"] == 2
    assert _valid_difficulty_features({**question, "question_text": "已修改条件"}, [row]) == []
    source = {"difficulty_features": features}
    plain = {"stable_keys": [BNU_TARGET]}
    assert _direct_preference({**plain, "difficulty_features": features}, BNU_TARGET, source) > _direct_preference(plain, BNU_TARGET, source)
    input_obj = QuestionAnalysisInput(question_id=1, tagging_context=TaggingContext(**question))
    assert question_content_fingerprint(question) == input_obj.source_content_hash
    legacy = "4a464d806ef4c1419fb655187a9245f6df3516f52b3b5725c8f59ee7ca5cb9f6"
    assert difficulty_source_content_hash_matches(question, legacy, analysis_input=input_obj)
    assert not difficulty_source_content_hash_matches({**question, "answer_text": "已修改解析"}, legacy)
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE question_part_difficulty_features (
            id INTEGER PRIMARY KEY, question_id, part_id, features_json,
            formula_difficulty, formula_version, source_content_hash, model_name, created_at, is_active)''')
        connection.execute("INSERT INTO question_part_difficulty_features VALUES(1,1,'part1',?,4.0,'std-difficulty-v1',?,'TEST','TEST',1)",
                           (row["features_json"], legacy))
        compatible = compatible_difficulty_content_hashes(question, analysis_input=input_obj)
        assessment = load_assessment("TEST-unused.db", 1, connection=connection,
            current_fingerprint=input_obj.source_content_hash, compatible_fingerprints=compatible)
        assert assessment["needs_reevaluation"] is False
        assert assessment["question_formula"] == 4.0
        connection.execute("INSERT INTO question_part_difficulty_features SELECT 2,question_id,'part2',features_json,formula_difficulty,formula_version,?,model_name,created_at,is_active FROM question_part_difficulty_features WHERE id=1", ("0" * 64,))
        assessment = load_assessment("TEST-unused.db", 1, connection=connection,
            current_fingerprint=input_obj.source_content_hash, compatible_fingerprints=compatible)
        assert assessment["needs_reevaluation"] is True


def test_grouping_uses_skill_history_instead_of_overall_score_gap():
    left = {BNU_TARGET: {"score_rate": .1, "mastery": .6, "source_question_refs": [observation(1, 5, 5), observation(2, 5, 0)]}}
    right = deepcopy(left)
    right[BNU_TARGET]["score_rate"] = .95
    assert _group_similarity(left, right) == 1
    right[BNU_TARGET]["source_question_refs"] = [observation(i, 8, 5) for i in (1,2,3)]
    assert _group_similarity(left, right) == 0


def test_all_three_flows_share_per_student_matching_and_allow_new_exercises(direct_module):
    diagnosis = _direct_diagnosis()
    config = PersonalizedRecommendationConfig(scope_keys=(BNU_CHAPTER4,), target_keys=(BNU_TARGET,),
                                               curriculum_volume_id="bnu24-math-g7-lower")
    personal = direct_module.evaluate_candidates(diagnosis=diagnosis, config=config)
    shared = direct_module.evaluate_candidates(diagnosis=diagnosis, config=replace(config, paper_mode="shared"))
    signature = lambda result: {(sid, e["candidate"]["question_id"], e["match_level"], e["target"]["target_difficulty"], e["practice_purpose"])
                               for sid, entries in result["pools"].items() for e in entries}
    assert signature(personal) == signature(shared)
    teacher = shortlist_candidates(diagnosis=diagnosis,
        read_service=QuestionBankReadService(direct_module.db_path, data_root=direct_module.data_root),
        recommendations=direct_module, volume_id=config.curriculum_volume_id,
        chapter_id="bnu24-math-g7-lower-c04", target_keys=[BNU_TARGET], question_type="",
        difficulty_min=1, difficulty_max=8, excluded_question_ids=set())
    individual_ids = {e["candidate"]["question_id"] for entries in personal["pools"].values() for e in entries}
    teacher_ids = {q for c in teacher["candidates"] for q in [c["question_id"], *c["similar_question_ids"]]}
    direct = direct_module.evaluate_candidates(diagnosis=diagnosis, config=config, core_only=True)
    direct_ids = {e['candidate']['question_id'] for entries in direct['pools'].values() for e in entries}
    assert teacher_ids == direct_ids
    assert individual_ids

    unknown = deepcopy(diagnosis)
    unknown["students"][0]["weak_points"] = []
    result = direct_module.evaluate_candidates(diagnosis=unknown, config=config)
    entries = next(iter(result["pools"].values()))
    assert entries
    assert all(e["practice_purpose"] == "new" and not e["target"]["source_question_refs"] for e in entries)


def test_shared_paper_allows_different_purposes_and_has_no_fine_ratios():
    entries = []
    for qid in range(1, 9):
        for sid, purpose in (("A", "remediation"), ("B", "consolidation"), ("C", "new")):
            entries.append({"candidate": {"question_id": qid, "difficulty": 4, "question_type": "选择题", "stable_keys": []},
                "student_id": sid, "key": BNU_TARGET, "selection_kind": "direct" if sid != "C" else "supplement",
                "practice_purpose": purpose, "distance": 0, "preference": 0, "match_level": 1})
    common = _common_entries(entries, ["A", "B", "C"])
    selected = _choose_practice_entries(common, 8)
    assert len(selected) == 8
    assert all({e["student_id"] for e in group} == {"A", "B", "C"} for _, group in selected)
    assert [e["candidate"]["question_id"] for e, _ in selected] == [e["candidate"]["question_id"] for e, _ in _choose_practice_entries(list(reversed(common)), 8)]


@pytest.mark.parametrize('members', [('A',), ('A', 'B', 'C')])
def test_skill_cap_counts_whole_questions_and_all_direct_skills(members):
    skill_sets = [('sk_a', 'sk_a'), ('sk_a',), ('sk_a', 'sk_b'), ('sk_b',), ('sk_b',), ('sk_b',)]
    entries = [{'candidate': {'question_id': qid, 'question_type': '选择题',
                             'stable_keys': [*skills, 'kp_shared_topic']},
                'student_id': sid, 'key': f'varied-reason-{qid}', 'selection_kind': 'direct',
                'practice_purpose': 'remediation', 'distance': 0, 'preference': 0, 'match_level': 1}
               for qid, skills in enumerate(skill_sets, 1) for sid in members]
    selected = _choose_practice_entries(_common_entries(entries, members), 10)
    assert [entry['candidate']['question_id'] for entry, _ in selected] == [1, 4]
    assert all(len(group) == len(members) for _, group in selected)
    printed = [entry['candidate'] for entry, _ in selected]
    # Same topic/prerequisite is not the same directly trained skill.
    assert _paper_diversity_allowed({'question_id': 7, 'stable_keys': ['kp_shared_topic', 'sk_c'],
                                    'supporting_keys': ['sk_a', 'sk_b']}, printed)
    assert not _paper_diversity_allowed({'question_id': 8, 'stable_keys': ['sk_c', 'sk_b']}, printed)


@pytest.mark.parametrize('replaced_qid,allowed', [(1, True), (3, False)])
def test_replacement_releases_old_skill_slot_but_cannot_add_a_second(replaced_qid, allowed, monkeypatch):
    from question_bank.recommendation.personalized import RecommendationEditCommand, RecommendationEditInvalid
    from tests.training.test_personalized_recommendation import _selection_candidate
    candidates = [_selection_candidate(q, '', key='sk_a' if q in (1,4) else f'sk_{q}') for q in (1, 2, 3, 4)]
    target = {'stable_key': 'sk_a', 'source_question_refs': []}
    draft = {'revision': 1, 'config': PersonalizedRecommendationConfig().to_dict(),
             'students': [{'student_id': 'A', 'warnings': [], 'items': [
        {'question_id': q, 'item_id': str(q), 'slot': q, 'item_order': q, 'stage': 'direct',
         'selection_kind': 'direct', 'matched_key': 'sk_a', 'target': target, 'locked': False,
         'difficulty': 5, 'replacement_history': []} for q in (1, 2, 3)]}]}
    module = object.__new__(PersonalizedRecommendationModule)
    module.current_knowledge = SimpleNamespace(relations=())
    monkeypatch.setattr(module, '_recent_question_ids', lambda *args, **kwargs: {'A': set()})
    entry = {'candidate': candidates[-1], 'target': target, 'matched_key': 'sk_a',
             'key': 'sk_a', 'student_id': 'A', 'selection_kind': 'direct', 'distance': 0, 'preference': 0}
    monkeypatch.setattr(module, '_candidate_entries', lambda **kwargs: ([entry], []))
    monkeypatch.setattr(module, 'evaluate_candidates', lambda **kwargs: {'pools': {'A': [entry]}})
    command = RecommendationEditCommand(request_token='f'*32, expected_revision=1, action='replace',
        student_id='A', item_id=str(replaced_qid), actor_ref='test', reason='synthetic replacement', replacement_question_id=4)
    before = deepcopy(draft)
    def replace_item():
        return module._apply_edit(draft, draft_id='e'*64, request={'config': PersonalizedRecommendationConfig().to_dict(),
            'diagnosis': {'students': [{'student_id': 'A'}]}}, command=command, candidates=tuple(candidates))
    if allowed:
        replace_item()
        assert [i['question_id'] for i in draft['students'][0]['items']] == [4, 2, 3]
    else:
        with pytest.raises(RecommendationEditInvalid):
            replace_item()
        assert draft == before


@pytest.fixture
def activity_module(tmp_path):
    """Minimal SQL fixture for the activity reader, never an application database."""
    path = tmp_path / "activity-reader.db"
    with sqlite3.connect(path) as conn:
        conn.executescript('''
          CREATE TABLE grading_question_links(grading_session_id,bank_question_id,status);
          CREATE TABLE training_evidence_records(student_id,occurred_at,task_item_code,source_json,status);
          CREATE TABLE personalized_paper_items(paper_instance_id,task_item_code,bank_question_id);
          CREATE TABLE training_submissions(student_id,paper_instance_id,submission_id,created_at,status,revision);
          CREATE TABLE training_assessment_runs(submission_id,submission_revision,run_id,status);
          CREATE TABLE training_question_results(run_id,met_count,not_met_count);
          CREATE TABLE training_attempts(student_id,grading_session_id,created_at,task_item_code,score_awarded,full_score);
          CREATE TABLE training_task_items(variant_id,task_item_code,bank_question_id);
          CREATE TABLE questions(id,question_text,answer_text,is_deleted);
        ''')
        conn.executemany("INSERT INTO grading_question_links VALUES (?,?,'confirmed')", [(i, i) for i in range(1,6)])
        conn.executemany("INSERT INTO personalized_paper_items VALUES (?,?,?)", [("graded", "g1", 6), ("graded", "g2", 7), ("ungraded", "u", 8)])
        conn.execute("INSERT INTO training_submissions VALUES ('A','graded','submission','2020-01-04','ready',1)")
        conn.execute("INSERT INTO training_assessment_runs VALUES ('submission',1,'run','succeeded')")
        conn.execute("INSERT INTO training_question_results VALUES ('run',1,0)")
    module = object.__new__(PersonalizedRecommendationModule)
    module.db_path, module.data_root = path, tmp_path
    return module


def test_latest_three_graded_activities_merge_exams_and_unpublished_training(activity_module):
    events = [{"student_id": "A", "session_id": i, "occurred_at": f"2020-01-0{i}"} for i in (1,2,3,5)]
    events.append({"student_id": "B", "session_id": 1, "occurred_at": "2020-01-01"})
    diagnosis = {"students": [{"student_id": "A"}, {"student_id": "B"}], "_graded_activities": events}
    assert activity_module._recent_question_ids(("A", "B"), diagnosis=diagnosis) == {"A": {3,5,6,7}, "B": {1}}
    # Publishing/reviewing the same submission later does not add a fourth activity.
    with sqlite3.connect(activity_module.db_path) as conn:
        conn.execute("INSERT INTO training_evidence_records VALUES ('A','2030-01-01','g1','{}','active')")
    assert activity_module._recent_question_ids(("A", "B"), diagnosis=diagnosis) == {"A": {3,5,6,7}, "B": {1}}
    assert activity_module.current_exam_question_ids(diagnosis) == {1,3,5,6,7}
    # A text-identical bank question is excluded too, regardless of figures.
    def exact(image):
        return 'exact-v4:' + json.dumps(
            {"text": "已知直角三角形两直角边分别为3和4，求斜边长。[[IMAGE:" + image + "]]",
             "options": [], "formulas": [], "images": [image]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(activity_module.db_path) as conn:
        conn.execute("ALTER TABLE questions ADD COLUMN paper_id")
        conn.execute("CREATE TABLE papers(id,import_status)")
        conn.execute("CREATE TABLE question_content_index(question_id,content_key)")
        conn.executemany("INSERT INTO questions(id,question_text,answer_text,is_deleted) VALUES (?,?,?,0)", [(6, '题干六', '6'), (99, '题干九九', '9')])
        conn.executemany("INSERT INTO question_content_index VALUES (?,?)",
                         [(6, exact('2x2:TEST_aa')), (99, exact('9x9:TEST_bb'))])
    assert activity_module._recent_question_ids(("A", "B"), diagnosis=diagnosis) == {"A": {3,5,6,7,99}, "B": {1}}


def test_graded_activity_participation_does_not_depend_on_wrong_answers_or_tags():
    service = object.__new__(DiagnosisProfileService)
    service.db = SimpleNamespace(
        sessions=SimpleNamespace(
            list_grading_sessions=lambda: [{"id": 1, "created_at": "2020-01-01"}, {"id": 2, "is_deleted": True}]),
        results=SimpleNamespace(
            get_active_assessment_evidence=lambda **kwargs: [
            {"student_id": 1, "session_id": 1, "score_awarded": 5, "full_score": 5},
            {"student_id": 1, "session_id": 1, "score_awarded": 0, "full_score": 5},
            {"student_id": 1, "session_id": 2, "score_awarded": 0, "full_score": 5}]))
    assert service.graded_activities(["1"]) == [{"student_id": "1", "activity_id": "exam:1", "session_id": "1", "occurred_at": "2020-01-01"}]


def test_exam_and_training_receipt_for_same_activity_are_counted_once(activity_module):
    with sqlite3.connect(activity_module.db_path) as conn:
        conn.execute("INSERT INTO training_evidence_records VALUES ('A','2030-01-01','g1',?, 'active')", (json.dumps({"grading_session_id": 4}),))
        conn.execute("INSERT INTO training_attempts VALUES ('A',2,'2020-01-02','old-1',2,5)")
        conn.executemany("INSERT INTO training_task_items VALUES ('v',?,?)", [('old-1',9), ('old-2',10)])
    events = [{"student_id": "A", "session_id": i, "occurred_at": f"2020-01-0{i}"} for i in (2,3,4)]
    recent = activity_module._recent_question_ids(("A",), diagnosis={"_graded_activities": events})
    assert recent == {"A": {2,3,4,6,7,9,10}}


def test_paper_limits_preserve_raw_decimal_and_allow_two_written_questions(direct_module, monkeypatch):
    candidates = [{"question_id": q, "question_type": "解答题", "difficulty": 8., "stable_keys": []} for q in (1,2,3)]
    candidates += [{"question_id": 4, "question_type": "选择题", "difficulty": 8.4, "stable_keys": []}]
    monkeypatch.setattr(direct_module, "_source_snapshot", lambda **kw: (candidates, (), "test"))
    direct_module.validate_paper_questions([1,2])
    with pytest.raises(ValueError, match="2 道"):
        direct_module.validate_paper_questions([1,2,3])
    with pytest.raises(ValueError, match="1–8"):
        direct_module.validate_paper_questions([4])
    rules = {'purpose':'handout','question_count':4,'difficulty_max':10,
             'max_questions_per_skill':2,'max_written_questions':3,'recent_activity_count':0}
    direct_module.validate_paper_questions([1,2,3,4], rules)
    assert direct_module.paper_rule_violations([1,2], {**rules,'max_written_questions':1})[0]['question_id'] == 2
    assert direct_module.paper_rule_violations([4], rules, recent_question_ids=[4])[0]['code'] == 'recent'
    candidates[1]["duplicate_identity"] = candidates[0]["duplicate_identity"] = "same"
    with pytest.raises(ValueError, match="相似"):
        direct_module.validate_paper_questions([1,2])


TYPE_KEY = 'kp_bnu24_math_g8_upper_1_1_t05'
TYPE_OTHER = 'kp_bnu24_math_g8_upper_1_1_t07'
TYPE_THIRD = 'kp_bnu24_math_g8_upper_1_2_t03'


def test_training_keys_and_quotas_count_type_then_skills():
    from question_bank.question_types import training_keys
    from question_bank.recommendation.personalized import (
        _paper_skill_limit_exceeded, paper_task_duplicates)
    assert training_keys([TYPE_KEY, 'sk_a']) == {TYPE_KEY}
    assert training_keys(['sk_a', 'sk_b']) == {'sk_a', 'sk_b'}
    typed = {'stable_keys': [TYPE_KEY, 'sk_a']}
    # A typed candidate is capped by its type, never by its skills.
    assert _paper_skill_limit_exceeded(typed, [{'stable_keys': [TYPE_KEY, 'sk_b']}])
    assert not _paper_skill_limit_exceeded(typed, [{'stable_keys': ['sk_a', 'sk_b']}])
    # Untyped questions still count by their skills (v8 behaviour).
    assert _paper_skill_limit_exceeded({'stable_keys': ['sk_a']}, [{'stable_keys': ['sk_a']}])
    text = '计算一个较长的综合表达式并写出结果'
    base = {'question_id': 7, 'stable_keys': [TYPE_KEY], 'question_type': '填空题',
            'question_text': text, 'solution_template': '', 'image_identity': (),
            'target_facets': [], 'practice_observations_by_key': {}}
    assert paper_task_duplicates(dict(base, question_id=8), [base]) == [7]
    assert paper_task_duplicates(dict(base, question_id=9, stable_keys=[TYPE_OTHER]), [base]) == []


def test_type_need_stats_and_priority_stable_then_gap():
    from question_bank.recommendation.personalized import (
        _task_priorities, _type_class_totals, _type_need_priority, _type_need_stats)

    def ref(session, question, score, full=5, kind='current_exam'):
        return {'session_id': session, 'question_id': question,
                'score_awarded': score, 'full_score': full, 'source_kind': kind,
                'assessment': {'eligible': True, 'evidence_weight': 1, 'granularity': 'part'}}

    diagnosis = {'students': [
        {'student_id': 'A', 'weak_points': [{'knowledge_key': TYPE_KEY,
            'source_question_refs': [ref(1, 'Q1', 0), ref(1, 'Q2', 1), ref(2, 'Q1', 5)]}]},
        {'student_id': 'B', 'weak_points': [{'knowledge_key': TYPE_OTHER,
            'source_question_refs': [ref(1, 'Q1', 4), ref(1, 'Q2', 3), ref(2, 'Q1', 5)]}]},
    ]}
    totals = _type_class_totals(diagnosis)
    stats = _type_need_stats(diagnosis['students'][0]['weak_points'][0], totals)
    assert (stats['attempted'], stats['lost'], stats['stable']) == (3, 2, True)
    assert stats['rate'] == pytest.approx(6 / 15)
    assert stats['class_rate'] == pytest.approx(18 / 30)
    assert stats['gap'] == pytest.approx(18 / 30 - 6 / 15)
    assert stats['points_lost'] == pytest.approx(9)
    # Same (session, question) dedupes; training rows do not count.
    dup = {'source_question_refs': [ref(1, 'Q1', 0), ref(1, 'Q1', 0),
                                    ref(1, 'Q3', 0, kind='training')]}
    stats2 = _type_need_stats(dup, totals)
    assert stats2['attempted'] == 1 and stats2['stable'] is False
    assert _type_need_stats({'source_question_refs': []}, totals) is None
    # Priority: stable first, then gap, then points lost; skills keep 1-mastery.
    assert _type_need_priority(stats) > _type_need_priority({'stable': False, 'gap': .9, 'points_lost': 9})
    assert _type_need_priority({'stable': False, 'gap': .5, 'points_lost': 8}) > (
        _type_need_priority({'stable': False, 'gap': .5, 'points_lost': 3}))

    def entry(key, stable, gap, lost):
        return {'key': key, 'matched_key': key, 'student_id': 'A',
                'selection_kind': 'direct', 'practice_purpose': 'remediation',
                'candidate': {'question_id': 1, 'stable_keys': [key]},
                'target': {'stable_key': key, 'value': .9,
                           'need_stats': {'stable': stable, 'gap': gap, 'points_lost': lost}}}
    priorities = _task_priorities({1: [entry(TYPE_KEY, False, .9, 9),
                                       entry(TYPE_OTHER, True, 0, 2),
                                       entry(TYPE_THIRD, False, .5, 8)]})
    assert priorities[('A', TYPE_OTHER)] > priorities[('A', TYPE_KEY)] > priorities[('A', TYPE_THIRD)]
    skill = {'key': 'sk_x', 'matched_key': 'sk_x', 'student_id': 'A',
             'selection_kind': 'direct', 'practice_purpose': 'remediation',
             'candidate': {'question_id': 2, 'stable_keys': ['sk_x']},
             'target': {'stable_key': 'sk_x', 'value': .9,
                        'need_stats': {'stable': True, 'gap': 5, 'points_lost': 9}}}
    assert _task_priorities({2: [skill]})[('A', 'sk_x')] == pytest.approx(.1)


def test_type_section_spread_prefers_two_thirds_share():
    def entry(qid, key, section, distance=0):
        return {'candidate': {'question_id': qid, 'stable_keys': [key], 'question_type': '选择题'},
                'student_id': 'A', 'key': key, 'matched_key': key, 'selection_kind': 'direct',
                'practice_purpose': 'remediation', 'match_level': 1,
                'distance': distance, 'preference': 0, 'target_section': section,
                'target': {'stable_key': key,
                           'need_stats': {'stable': True, 'gap': .5, 'points_lost': 5}}}
    t1, t2 = 'kp_bnu24_math_g8_upper_1_1_t01', 'kp_bnu24_math_g8_upper_1_1_t02'
    t3 = 'kp_bnu24_math_g8_upper_1_2_t03'
    t4, t5, t6 = ('kp_bnu24_math_g8_upper_1_1_t04', 'kp_bnu24_math_g8_upper_1_2_t05',
                  'kp_bnu24_math_g8_upper_1_2_t06')
    # Every group covers two needs; q3 loses on median distance and is picked
    # last, when s1 already holds 2 of 2 remediation targets.
    entries = [entry(1, t1, 's1'), entry(1, t5, 's2'),
               entry(2, t2, 's1'), entry(2, t6, 's2'),
               entry(3, t3, 's2', distance=1), entry(3, t4, 's1')]
    chosen = _choose_practice_entries(entries, 3)
    assert [e['key'] for e, _ in chosen] == [t1, t2, t3]
    # Without the 2/3 spread preference the closer same-section entry (t4,
    # distance 0) would win over t3 (distance 1).


def test_fixed_target_matches_use_type_inputs_in_cache_key():
    from question_bank.recommendation.personalized import (
        _fixed_target_matches, _match_facets_input)
    _fixed_target_matches.cache_clear()
    sources = [{'part_id': 's1', 'direct_keys': [TYPE_KEY], 'type_keys': [TYPE_KEY]}]
    candidates = [{'part_id': 'c1', 'type_keys': [],
                   'section_keys': ['sec'], 'chapter_keys': ['ch']}]
    src_in, cand_in = _match_facets_input(sources), _match_facets_input(candidates)
    low = _fixed_target_matches(TYPE_KEY, src_in, cand_in, 'sec', 'ch', 0.4, (), ())
    high = _fixed_target_matches(TYPE_KEY, src_in, cand_in, 'sec', 'ch', 0.8, (), ())
    assert [json.loads(m)['match_level'] for m in low] == [4]
    assert [json.loads(m)['match_level'] for m in high] == [3]
    typed_cand = _match_facets_input([{'part_id': 'c1', 'type_keys': [TYPE_OTHER],
                                       'section_keys': ['sec'], 'chapter_keys': ['ch']}])
    related = _fixed_target_matches(TYPE_KEY, src_in, typed_cand, 'sec', 'ch', 0.0, (TYPE_OTHER,), ())
    assert [json.loads(m)['match_level'] for m in related] == [2]
