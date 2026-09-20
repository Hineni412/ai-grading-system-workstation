from question_bank.recommendation.target_matching import match_target
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


def test_new_matching_does_not_change_existing_difficulty_band():
    ref = {"question_difficulty": 6}
    assert _loss_difficulty_fits({"difficulty": 5}, ref, 7)
    assert _loss_difficulty_fits({"difficulty": 6}, ref, 7)
    assert not _loss_difficulty_fits({"difficulty": 3}, ref, 7)
    assert not _loss_difficulty_fits({"difficulty": 8}, ref, 7)
    assert _loss_difficulty_fits({"difficulty": 7}, {"question_difficulty": 9}, 7)


def test_skill_not_in_source_part_does_not_use_unrelated_part_as_anchor():
    assert match_target('sk_test', [facet(skills=('sk_else',))], [facet()], INDEX) is None


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
    source = {'bank_question_id': 99, 'question_id': 'synthetic-source', 'full_score': 5, 'score_awarded': 1,
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
    assert [e['candidate']['question_id'] for e, _ in _choose_practice_entries(entries, 4)] == [1, 2, 3, 4]
