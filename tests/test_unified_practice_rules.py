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
)
from question_bank.services.assembly_assistant import shortlist_candidates
from question_bank.services.question_read_service import QuestionBankReadService
from tests.phase4.test_personalized_recommendation import (
    bnu24_difficulty_module, direct_module, _direct_diagnosis, BNU_TARGET, BNU_CHAPTER4,
)


def observation(index, difficulty, score, **assessment):
    return {"session_id": index, "question_id": "Q1", "full_score": 5, "score_awarded": score,
            "assessment": {"eligible": True, "evidence_weight": 1, "granularity": "part",
                           "part_id": "p1", "part_difficulty": difficulty, **assessment}}


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


def test_difficulty_features_use_current_content_and_affect_preference():
    from question_bank.services.standard_difficulty import question_content_fingerprint
    question = {"question_text": "合成条件", "answer_text": "合成解析", "question_type": "选择题"}
    row = {"source_content_hash": question_content_fingerprint(question), "part_id": "part1",
           "features_json": json.dumps({"solo": 2, "reasoning": 1, "trap": 2})}
    features = _valid_difficulty_features(question, [row])
    assert features and features[0]["features"]["trap"] == 2
    assert _valid_difficulty_features({**question, "question_text": "已修改条件"}, [row]) == []
    source = {"difficulty_features": features}
    plain = {"stable_keys": [BNU_TARGET]}
    assert _direct_preference({**plain, "difficulty_features": features}, BNU_TARGET, source) > _direct_preference(plain, BNU_TARGET, source)


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
    assert teacher_ids == individual_ids
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


def test_graded_activity_participation_does_not_depend_on_wrong_answers_or_tags():
    service = object.__new__(DiagnosisProfileService)
    service.db = SimpleNamespace(
        list_grading_sessions=lambda: [{"id": 1, "created_at": "2020-01-01"}, {"id": 2, "is_deleted": True}],
        get_active_assessment_evidence=lambda **kwargs: [
            {"student_id": 1, "session_id": 1, "score_awarded": 5, "full_score": 5},
            {"student_id": 1, "session_id": 1, "score_awarded": 0, "full_score": 5},
            {"student_id": 1, "session_id": 2, "score_awarded": 0, "full_score": 5}])
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
    candidates[1]["duplicate_identity"] = candidates[0]["duplicate_identity"] = "same"
    with pytest.raises(ValueError, match="相似"):
        direct_module.validate_paper_questions([1,2])
