from __future__ import annotations

import json
import hashlib
import pickle
import re
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import warnings
from pathlib import Path

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)


def test_training_display_hides_scores_without_mutating_frozen_draft(monkeypatch):
    from copy import deepcopy
    from backend.api.app import create_app
    from backend.api.routers.training import _public_training_mapping

    from question_bank.services import rich_content_service
    original_strip = rich_content_service.strip_question_source_score
    calls = []
    def counted_strip(text, **kwargs):
        calls.append((text, kwargs['question_number']))
        return original_strip(text, **kwargs)
    monkeypatch.setattr(rich_content_service, 'strip_question_source_score', counted_strip)
    draft = {'source_revision': 'a' * 64, 'selected_items': [
        {'question_number': '1', 'question_text': '（8分）小明跑了5分钟。',
         'answer_text': '（8分）答案为2。'},
        {'question_number': '1', 'question_text': '（8分）小明跑了5分钟。'},
        {'question_number': '2', 'question_text': '（8分）小明跑了5分钟。'},
    ], 'nested': ({'error_message': 'TEST-error'}, {'secret': 'TEST-secret'},
                  {'source_file': 'TEST-source', 'keep': 1}, {},
                  {'value': 'C%3A%5Cprivate%5Ctest.txt'},
                  {'latex': r'\frac{1}{2}', 'url': '/api/training/diagnosis'})}
    before = deepcopy(draft)
    public = _public_training_mapping(draft)
    assert public['selected_items'][0]['question_text'] == '小明跑了5分钟。'
    assert public['selected_items'][0]['answer_text'] == '（8分）答案为2。'
    assert public['source_revision'] == draft['source_revision']
    assert public['nested'] == [{}, {'keep': 1}, {},
                                {'latex': r'\frac{1}{2}', 'url': '/api/training/diagnosis'}]
    assert len(calls) == 2
    assert draft == before
    public['selected_items'][0]['answer_text'] = 'TEST-public-edit'
    public['nested'][1]['keep'] = 2
    assert draft == before


@pytest.mark.parametrize('purpose,mode,expected', [('training', 'individual', 0),
    ('handout', 'shared', 0), ('handout', 'individual', 2)])
def test_create_draft_consolidation_cap_is_only_for_personal_handout(purpose, mode, expected):
    from backend.api.app import create_app
    from backend.api.routers.training import create_personalized_recommendation_draft
    from backend.api.schemas.training import PersonalizedRecommendationCreateRequest
    from copy import deepcopy
    captured = []
    students = [{'student_id': 'TEST-with-evidence', 'weak_points': [{'knowledge_key': 'kp_test'}]},
                {'student_id': 'TEST-without-weakness', 'weak_points': []}]
    class Profiles:
        def build_profiles(self, **_):
            return {'students': deepcopy(students)}
        def graded_activities(self, ids):
            assert ids == ['TEST-with-evidence', 'TEST-without-weakness']
            return []
    class Module:
        def resolve_target_names(self, names):
            return ()
        def create(self, **request):
            captured.append(request)
            return {'draft_id': 'd' * 64, 'revision': 1, 'config': request['config'].to_dict(),
                'status': 'draft', 'result_version': 'a' * 64, 'source_version': 'b' * 64,
                'engine_version': 'TEST-engine', 'students': [], 'warnings': [], 'history': []}
    body = PersonalizedRecommendationCreateRequest(request_token='a' * 32, purpose=purpose,
        paper_mode=mode, question_count=10, max_consolidation_questions=2, max_unmeasured_questions=0,
        scope_keys=['kp_bnu24_math_g7_lower_4'], target_keys=[],
        scope={'mode': 'all'}, exam_scope={'mode': 'current', 'session_ids': [1]})
    create_personalized_recommendation_draft(body, Profiles(), Module())
    request = captured[0]
    assert request['config'].max_consolidation_questions == expected
    assert [student['student_id'] for student in request['diagnosis']['students']] == (
        ['TEST-with-evidence', 'TEST-without-weakness'] if expected else ['TEST-with-evidence'])


def _clear_profile_memory():
    from integration import diagnosis_profile_service as profiles
    from integration.data_generation import reset_commit_generations
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    profiles._LOCAL_SOURCE_REVISIONS.clear()
    profiles._MASTERY_INPUT_RESULTS.clear()
    from backend.api.app import create_app
    from backend.api.routers.training import _DIAGNOSIS_RESPONSE_CACHE
    _DIAGNOSIS_RESPONSE_CACHE.clear()
    reset_commit_generations()


def test_saved_profiles_survive_new_process_and_wal_checkpoint(training_services, monkeypatch):
    service = training_services
    scope, exams = {"mode": "all"}, {"mode": "current", "session_ids": [14]}
    with sqlite3.connect(service.grading_db_path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE students SET name='合成学生龘' WHERE id=12")
        writer.commit()
        service.persist_snapshots = True
        expected = service.build_profiles(scope=scope, exam_scope=exams)
        class_profile = service.build_profiles(scope={"mode":"class", "class_id":"八年级1班"}, exam_scope=exams)
        assert len(class_profile['students']) == 2
        assert list(service._local_profile_path().glob('*.entry'))
        digest = hashlib.sha256(pickle.dumps((expected, service.latest_aggregated_mastery))).hexdigest()
        from backend.api.routers.training import _diagnosis_response_bytes
        public_digest = hashlib.sha256(_diagnosis_response_bytes(service, scope=scope, exam_scope=exams)).hexdigest()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _clear_profile_memory()
    restored = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    key = restored.tag_profile_cache_key(scope=scope, exam_scope=exams)
    with monkeypatch.context() as patch:
        loads = []
        original_load = pickle.load
        def counted_load(*args, **kwargs):
            loads.append(True)
            return original_load(*args, **kwargs)
        patch.setattr(pickle, 'load', counted_load)
        entry_path = restored._local_profile_entry_path(key)
        first = restored._read_local_profile(key)
        assert first is not None and restored._read_local_profile(key) == first
        assert loads == [True]
        saved_bytes = entry_path.read_bytes()
        entry_path.write_bytes(b'TEST-corrupt-replacement')
        assert restored._read_local_profile(key) is None
        replacement = entry_path.with_suffix('.TEST-replacement')
        replacement.write_bytes(saved_bytes)
        replacement.replace(entry_path)
        assert restored._read_local_profile(key) == first
        assert len(loads) == 3
    # A new interpreter has neither memory results nor process-local commit
    # counters. Fail if it attempts any full diagnosis/model recalculation.
    script = """
import hashlib,json,pickle,sys
from backend.api.app import create_app
from backend.api.routers.training import _diagnosis_response_bytes
from integration.diagnosis_profile_service import DiagnosisProfileService
s=DiagnosisProfileService(sys.argv[1],sys.argv[2])
def fail(**kwargs): raise AssertionError('recomputed saved diagnosis')
s._compute_tag_profiles=fail
d=s.build_profiles(scope={'mode':'all'},exam_scope={'mode':'current','session_ids':[14]})
c=s.build_profiles(scope={'mode':'class','class_ids':['八年级1班']},exam_scope={'mode':'current','session_ids':[14]})
assert len(c['students'])==2
s.build_profiles=fail
public=_diagnosis_response_bytes(s,scope={'mode':'all'},exam_scope={'mode':'current','session_ids':[14]})
print(json.dumps({'digest':hashlib.sha256(pickle.dumps((d,s.latest_aggregated_mastery))).hexdigest(),'students':len(d['students']),'public_digest':hashlib.sha256(public).hexdigest()}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(service.grading_db_path),
                             str(service.question_bank_db_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"digest": digest, "students": 2, "public_digest": public_digest}


@pytest.mark.parametrize("change", ["teacher_score", "training_record", "knowledge", "parameters", "code", "cause", "corrupt"])
def test_local_profiles_refresh_changed_inputs_and_corrupt_cache(training_services, monkeypatch, change):
    from integration import diagnosis_profile_service as profiles
    service = training_services
    scope, exams = {"mode": "all"}, {"mode": "current", "session_ids": [14]}
    service.persist_snapshots = True
    before = service.build_profiles(scope=scope, exam_scope=exams)
    from backend.api.routers.training import _diagnosis_response_bytes, _public_training_mapping
    from backend.api.schemas.training import TrainingDiagnosisResponse
    expected_public = TrainingDiagnosisResponse.model_validate(_public_training_mapping(
        {k: v for k, v in before.items() if not k.startswith('_')})).model_dump(mode='json', exclude_none=True)
    assert json.loads(_diagnosis_response_bytes(service, scope=scope, exam_scope=exams)) == expected_public
    saved_key = service.tag_profile_cache_key(scope=scope, exam_scope=exams)
    assert service._local_profile_entry_path(saved_key).is_file()
    _clear_profile_memory()
    if change == "teacher_score":
        with sqlite3.connect(service.grading_db_path) as writer:
            writer.execute("UPDATE session_details SET score_awarded=10 WHERE result_id=14001 AND question_id='Q1'")
            writer.execute("UPDATE session_results SET student_score=15 WHERE id=14001")
    elif change == "training_record":
        with sqlite3.connect(service.question_bank_db_path) as writer:
            # A committed training-side change must not reuse an earlier result,
            # including when checkpointing folds it into the main file.
            writer.execute("INSERT INTO training_tasks(task_code,status) VALUES('TEST-local-profile-refresh','completed')")
    elif change == "knowledge":
        with sqlite3.connect(service.question_bank_db_path) as writer:
            writer.execute("UPDATE question_tags SET tag_value='合成更新主题' WHERE question_id=101 AND tag_type='knowledge_point'")
    elif change == "parameters":
        old = profiles._profile_semantics()
        monkeypatch.setattr(profiles, "_profile_semantics", lambda: (*old, "TEST-new-parameters"))
    elif change == "code":
        monkeypatch.setattr(profiles, "_PROFILE_CALCULATION_STATE",
                            (*profiles._PROFILE_CALCULATION_STATE, ('TEST-rule-change', 1, 1)))
    elif change == "cause":
        cause = service.data_root / "reports/.class_analysis/TEST-cause.json"
        cause.parent.mkdir(parents=True, exist_ok=True)
        cause.write_text('{}', encoding='utf-8')
    else:
        service._local_profile_entry_path(saved_key).write_bytes(b"TEST-truncated-snapshot")
    fresh = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    compute = fresh._compute_tag_profiles
    calls = []
    def tracked(**kwargs):
        calls.append(True)
        return compute(**kwargs)
    monkeypatch.setattr(fresh, "_compute_tag_profiles", tracked)
    after = fresh.build_profiles(scope=scope, exam_scope=exams)
    assert calls == [True]
    public_after = TrainingDiagnosisResponse.model_validate(_public_training_mapping(
        {k: v for k, v in after.items() if not k.startswith('_')})).model_dump(mode='json', exclude_none=True)
    assert json.loads(_diagnosis_response_bytes(fresh, scope=scope, exam_scope=exams)) == public_after
    if change == "teacher_score":
        assert next(s for s in after['students'] if s['student_id'] == '12')['score_rate'] == pytest.approx(.75)
        assert after != before
    if change == "knowledge":
        assert after != before


def test_local_profiles_reuse_within_a_week_and_refresh_next_week(training_services, monkeypatch):
    from datetime import UTC, datetime as real_datetime
    from integration import diagnosis_profile_service as profiles
    service = training_services
    scope, exams = {"mode": "all"}, {"mode": "current", "session_ids": [14]}
    service.persist_snapshots = True

    class FrozenClock:
        # 2025-01-06 12:00 UTC is Monday 20:00 Beijing; 01-08 is Wednesday.
        current = real_datetime(2025, 1, 6, 12, tzinfo=UTC)
        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)
        fromisoformat = real_datetime.fromisoformat
        combine = real_datetime.combine
        strptime = real_datetime.strptime

    monkeypatch.setattr(profiles, "datetime", FrozenClock)
    before = service.build_profiles(scope=scope, exam_scope=exams)
    _clear_profile_memory()
    FrozenClock.current = real_datetime(2025, 1, 8, 4, tzinfo=UTC)
    fresh = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    monkeypatch.setattr(
        fresh, "_compute_tag_profiles",
        lambda *args, **kwargs: pytest.fail("same-week profile recomputed"),
    )
    wednesday = fresh.build_profiles(scope=scope, exam_scope=exams)
    assert wednesday == before
    # 周三重新计算的结果与周一一致：周内日期不是输入。
    checker = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    uncached = checker._compute_tag_profiles(
        scope=profiles._normalized_profile_scope(scope, exams),
        exam_scope=exams,
    )[0]
    assert uncached == before
    FrozenClock.current = real_datetime(2025, 1, 13, 4, tzinfo=UTC)  # next Monday
    calls = []
    real = DiagnosisProfileService._compute_tag_profiles
    def tracked(self, **kwargs):
        calls.append(True)
        return real(self, **kwargs)
    monkeypatch.setattr(
        DiagnosisProfileService, "_compute_tag_profiles", tracked,
    )
    monday = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    monday.build_profiles(scope=scope, exam_scope=exams)
    assert calls == [True]


def test_session_error_cause_index_reuses_unchanged_sessions(training_services, monkeypatch):
    import integration.persistent_entries as persistent_entries
    from backend import class_analysis
    from integration import diagnosis_profile_service as profiles
    service = training_services
    (service.data_root / "reports").mkdir(exist_ok=True)
    with sqlite3.connect(service.grading_db_path) as writer:
        writer.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path,"
            " answer_key_path, status, is_deleted)"
            " VALUES (15, '另一场考试', '', '', 'completed', 0)")
        writer.execute(
            "INSERT INTO exam_papers (id, session_id, front_image, back_image,"
            " student_id, match_status, processing_status)"
            " VALUES (1501, 15, '', '', 15, 'matched', 'graded')")
        writer.execute(
            "INSERT INTO session_results (id, session_id, student_id, paper_id,"
            " total_score, student_score, needs_human_review, raw_json)"
            " VALUES (15001, 15, 15, 1501, 20, 12, 0, '{}')")
        writer.execute(
            "INSERT INTO session_details (result_id, question_id, score_awarded,"
            " knowledge_ids, secondary_errors_json)"
            " VALUES (15001, 'Q1', 7, '[\"UNKNOWN\"]', '[]')")
        writer.commit()

    def counted():
        calls = []
        original = class_analysis.session_error_records
        def spy(db, session_id, reports_dir, **kwargs):
            calls.append(int(session_id))
            return original(db, session_id, reports_dir, **kwargs)
        monkeypatch.setattr(class_analysis, "session_error_records", spy)
        return calls

    calls = counted()
    first = service._error_cause_index([14, 15])
    assert sorted(calls) == [14, 15]
    calls.clear()
    assert service._error_cause_index([14, 15]) == first
    assert calls == []

    # 只有改了分的场次重新校验；未动的场次继续复用。
    with sqlite3.connect(service.grading_db_path) as writer:
        writer.execute(
            "UPDATE session_details SET score_awarded=4"
            " WHERE result_id=14001 AND question_id='Q2'")
        writer.commit()
    rescored = service._error_cause_index([14, 15])
    assert calls == [14]
    assert {key: rows for key, rows in rescored.items() if key[0] == 15} == {
        key: rows for key, rows in first.items() if key[0] == 15}

    # 班级归因状态文件也是输入；它变化时同样只重算对应场次。
    calls.clear()
    state_file = service.data_root / "reports" / ".class_analysis" / "14.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"TEST-state": 1}', encoding='utf-8')
    service._error_cause_index([14, 15])
    assert calls == [14]

    # 缓存结果与不经缓存的计算一致。
    calls.clear()
    monkeypatch.setattr(profiles.DiagnosisProfileService,
                        "_session_error_fingerprint", lambda self, sid: None)
    uncached = service._error_cause_index([14, 15])
    monkeypatch.undo()
    calls = counted()
    assert uncached == service._error_cause_index([14, 15])
    assert calls == []

    # 模拟重启：清掉进程内记忆与共享条目库，两个场次都从磁盘复用。
    with profiles._SESSION_ERROR_LOCK:
        profiles._SESSION_ERROR_MEMO.clear()
    with persistent_entries._STORES_LOCK:
        persistent_entries._STORES.clear()
    restarted = DiagnosisProfileService(
        service.grading_db_path, service.question_bank_db_path)
    restarted._error_cause_index([14, 15])
    assert calls == []


def test_session_error_rows_hash_tracks_the_captured_snapshot(training_services, monkeypatch):
    # A commit landing between snapshot capture and the fingerprint call must
    # not register the old snapshot's row hash under the new generation.
    from backend import class_analysis
    from backend.api.read_connections import request_read_context
    service = training_services
    (service.data_root / "reports").mkdir(exist_ok=True)
    paths = SimpleNamespace(db_path=service.grading_db_path,
                            qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    calls = []
    original = class_analysis.session_error_records
    def spy(db, session_id, reports_dir, **kwargs):
        calls.append(int(session_id))
        return original(db, session_id, reports_dir, **kwargs)
    monkeypatch.setattr(class_analysis, "session_error_records", spy)

    with request_read_context(paths) as ctx:
        with sqlite3.connect(service.grading_db_path) as writer:
            writer.execute(
                "UPDATE session_details SET score_awarded=4"
                " WHERE result_id=14001 AND question_id='Q2'")
            writer.commit()
        stale_fingerprint = ctx.diagnosis_service._session_error_fingerprint(14)
        ctx.diagnosis_service._error_cause_index([14])
    assert calls == [14]

    # A fresh read context sees the new generation: the fingerprint differs,
    # the session is recomputed, and the output matches a live computation.
    calls.clear()
    with request_read_context(paths) as ctx:
        fresh_fingerprint = ctx.diagnosis_service._session_error_fingerprint(14)
        assert fresh_fingerprint != stale_fingerprint
        recomputed = ctx.diagnosis_service._error_cause_index([14])
    assert calls == [14]
    assert recomputed == service._error_cause_index([14])


def test_idle_prewarm_saves_local_profiles_without_blocking_grading(training_services, monkeypatch):
    # Production starts this worker after the app has registered its routers.
    # Preserve that startup order when this test is selected on its own.
    import backend.api.app
    from integration.training_prewarm import TrainingPrewarmWorker, clear_recent_requests, record_request
    clear_recent_requests()
    service = training_services
    paths = SimpleNamespace(db_path=service.grading_db_path, qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    jobs = SimpleNamespace(active=True)
    jobs.list = lambda **kwargs: ([{'status': 'running'}] if jobs.active else [], 0)
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    operation = lambda: worker._compute("diagnosis", {"mode":"all"}, {"mode":"current","session_ids":[14]}, {})
    monkeypatch.setattr(worker, "_refresh_plan", lambda: [operation])
    assert not worker.tick()
    assert not service._local_profile_path().exists()
    jobs.active = False
    assert worker.tick()
    assert worker.batches[-1][0] == 1
    assert not worker.tick()
    # A newly visited scope also needs persistence without waiting for a write.
    record_request('grouped_diagnosis', scope={'mode': 'all'},
        exam_scope={'mode': 'current', 'session_ids': [14]}, params={'grouping': {'TEST': 1}})
    jobs.active = True
    assert not worker.tick()
    jobs.active = False
    assert worker.tick() and worker.batches[-1][0] == 1
    assert not worker.tick()
    clear_recent_requests()
    # Startup prepares the same complete default group request as the page.
    planned = []
    monkeypatch.setattr(worker, '_class_names', lambda: ['TEST-class-1', 'TEST-class-2'])
    assert [scope['mode'] for scope in worker._startup_scopes()] == ['class', 'class', 'all']
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: 'bnu24-math-g8-upper')
    monkeypatch.setattr(worker, '_startup_scopes', lambda: [{'mode': 'all', 'student_ids': []}])
    monkeypatch.setattr(worker, '_compute', lambda *args: planned.append(args))
    for task in worker._startup_tasks(set()):
        task()
    kinds = [item[0] for item in planned]
    # Per-class exam evidence replaces the old assistant default body, which
    # the current panel never sends.
    assert kinds == ['diagnosis', 'overview', 'graph', 'grouped_diagnosis',
                     'assembly_exam', 'assembly_exam']
    group_request = next(item[3]['grouping'] for item in planned if item[0] == 'grouped_diagnosis')
    assert group_request['scope_keys'] == ['kp_bnu24_math_g8_upper_1']
    assert (group_request['question_count'], group_request['difficulty_max'],
            group_request['max_written_questions'], group_request['recent_activity_count']) == (10, 8, 2, 3)
    exam_params = [item[3] for item in planned if item[0] == 'assembly_exam']
    assert exam_params == [{'class_ids': ['TEST-class-1'], 'curriculum_volume_id': 'bnu24-math-g8-upper'},
                           {'class_ids': ['TEST-class-2'], 'curriculum_volume_id': 'bnu24-math-g8-upper'}]
    assert service._local_profile_path().exists()
    _clear_profile_memory()
    monkeypatch.setattr(DiagnosisProfileService, "_compute_tag_profiles",
        lambda *args, **kwargs: pytest.fail("prewarm recomputed saved profile"))
    worker._startup_done = False
    assert worker.tick()
    assert worker.batches[-1][0] == 1


def test_prewarm_yields_to_foreground_requests(training_services, monkeypatch):
    import threading
    from integration.training_prewarm import (
        TrainingPrewarmWorker, clear_recent_requests, foreground_request,
    )
    clear_recent_requests()
    service = training_services
    paths = SimpleNamespace(db_path=service.grading_db_path, qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    jobs = SimpleNamespace(list=lambda **kwargs: ([], 0), is_shutdown=False)
    ran = []
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=30)
    monkeypatch.setattr(worker, '_refresh_plan', lambda: [lambda: ran.append(True)])
    finished = []
    with foreground_request():
        thread = threading.Thread(target=lambda: finished.append(worker.tick()), daemon=True)
        thread.start()
        thread.join(timeout=3)
        assert thread.is_alive() and ran == []
    worker._stop.set()
    thread.join(timeout=5)
    assert ran == [] and finished == [True] and worker.batches[-1][0] == 0
    # With the quiet window disabled the same plan runs immediately.
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    monkeypatch.setattr(worker, '_refresh_plan', lambda: [lambda: ran.append(True)])
    assert worker.tick() and ran == [True]


def test_recent_requests_round_trip_and_run_first_on_startup(training_services, monkeypatch):
    from integration import training_prewarm as prewarm
    from integration.training_prewarm import (
        TrainingPrewarmWorker, clear_recent_requests, recent_requests,
        record_request, record_target,
    )
    clear_recent_requests()
    service = training_services
    paths = SimpleNamespace(db_path=service.grading_db_path, qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    jobs = SimpleNamespace(list=lambda **kwargs: ([], 0), is_shutdown=False)
    scope = {'mode': 'class', 'class_ids': ['八年级1班'], 'use_historical_fallback': False}
    exams = {'mode': 'manual', 'session_ids': [14], 'curriculum_volume_id': 'bnu24-math-g8-upper'}
    record_request('assistant', scope=scope, exam_scope=exams, params={
        'class_ids': ['八年级1班'], 'session_ids': [14],
        'curriculum_volume_id': 'bnu24-math-g8-upper', 'difficulty_max': 8,
        'purpose': 'handout'})
    generation = prewarm._recent_generation()
    # Prefetch-style target recording must not restart the refresh loop.
    record_target('assistant', scope=scope, exam_scope=exams, target_keys=['sk_a'])
    record_target('assistant', scope=scope, exam_scope=exams, target_keys=None)
    assert prewarm._recent_generation() == generation
    record_request('diagnosis', scope={'mode': 'all'},
        exam_scope={'mode': 'current', 'session_ids': [14]}, params={})
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    worker._persist_recents()
    recents_file = service.data_root / 'reports' / '.training_diagnosis' / 'recent_requests.json'
    assert recents_file.is_file()
    clear_recent_requests()
    assert recent_requests() == []
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    worker._load_recents()
    entries = recent_requests()
    assert [entry['targets'].get('assistant') for entry in entries] == [[('sk_a',), None], None]
    planned = []
    monkeypatch.setattr(worker, '_compute', lambda *args: planned.append(args[0]))
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: 'bnu24-math-g8-upper')
    monkeypatch.setattr(worker, '_class_names', lambda: [])
    monkeypatch.setattr(worker, '_startup_scopes', lambda: [])
    for task in worker._refresh_plan():
        task()
    # Newest recent first: the all-scope diagnosis entry precedes the class
    # entry's evidence and per-target assistant tasks.
    assert planned == ['diagnosis', 'diagnosis', 'assembly_exam', 'assistant', 'assistant']


def test_prewarm_reuses_the_foreground_assistant_cache_keys(training_services, monkeypatch):
    from tests.current_knowledge_support import install_current_knowledge
    from backend.api.read_connections import request_read_context
    from backend.api.routers import assembly as router
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from integration.training_prewarm import TrainingPrewarmWorker, clear_recent_requests
    from question_bank.recommendation.personalized import PersonalizedRecommendationModule
    from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
    from question_bank.services.question_read_service import QuestionBankReadService

    clear_recent_requests()
    service = training_services
    install_current_knowledge(service.question_bank_db_path)
    paths = SimpleNamespace(db_path=service.grading_db_path, qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    # The cache keys do not depend on the shortlist outcome; the fixture bank
    # cannot resolve this volume's evidence scope for a real one.
    monkeypatch.setattr(router, 'shortlist_candidates', lambda **kwargs: {
        'student_count': 0, 'weaknesses': [], 'candidates': [], 'candidate_total': 0})
    calls = []
    real_compute = router.compute_assistant_candidates
    monkeypatch.setattr(router, 'compute_assistant_candidates',
        lambda **kwargs: (calls.append(kwargs), real_compute(**kwargs))[1])
    memory_keys, persistent_keys, entry_paths = [], [], []
    original_get = router._ASSISTANT_CACHE.get_or_compute
    monkeypatch.setattr(router._ASSISTANT_CACHE, 'get_or_compute',
        lambda key, compute: (memory_keys.append(key), original_get(key, compute))[1])
    original_read = DiagnosisProfileService.read_persistent_result
    original_save = DiagnosisProfileService.save_persistent_result
    def track(action):
        def wrapper(self, key, entry=None):
            persistent_keys.append((action, key))
            if 'assistant-shortlist-persist' in str(key):
                entry_paths.append(self._local_profile_entry_path(key))
            return (original_read(self, key) if action == 'read'
                    else original_save(self, key, entry))
        return wrapper
    monkeypatch.setattr(DiagnosisProfileService, 'read_persistent_result', track('read'))
    monkeypatch.setattr(DiagnosisProfileService, 'save_persistent_result', track('save'))

    with request_read_context(paths) as ctx:
        router.compute_assistant_candidates(
            diagnosis_service=ctx.diagnosis_service,
            read_service=QuestionBankReadService(paths.qb_db_path, data_root=paths.data_root),
            recommendations=PersonalizedRecommendationModule(
                db_path=paths.qb_db_path, data_root=paths.data_root,
                semester_mastery=ctx.diagnosis_service.semester_mastery),
            workspace=AssemblyWorkspaceService(paths.data_root),
            class_ids=['八年级1班'], session_ids=[14], target_keys=['sk_probe'],
            curriculum_volume_id='bnu24-math-g8-upper',
            # Int inputs must hash to the same entry file as the float values
            # the recorded params become after the JSON round trip.
            difficulty_min=1, difficulty_max=8,
            recent_activity_count=0, purpose='handout')
    assert calls[-1]['recent_activity_count'] == 0
    router._ASSISTANT_CACHE.clear()
    jobs = SimpleNamespace(list=lambda **kwargs: ([], 0), is_shutdown=False)
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    worker._persist_recents()
    clear_recent_requests()
    worker = TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)
    worker._load_recents()
    monkeypatch.setattr(worker, '_startup_tasks', lambda seen: [])
    for task in worker._refresh_plan():
        try:
            task()
        except Exception:
            pass  # The worker itself also ignores per-task failures.
    # The prewarm's per-target task looked up exactly the foreground keys, and
    # the foreground-written persistent entry answered it; both refer to the
    # same entry file despite the int/float round trip, and a recorded 0 for
    # recent_activity_count is replayed as 0 rather than the default.
    assert memory_keys[0] == memory_keys[-1]
    saves = [key for action, key in persistent_keys if action == 'save']
    reads = [key for action, key in persistent_keys if action == 'read']
    assert saves and reads and reads[-1] == saves[0]
    assert len(set(entry_paths)) == 1
    assert calls[-1]['recent_activity_count'] == 0
    assert calls[-1]['target_keys'] == ['sk_probe']


def test_local_profile_entries_evict_oldest_beyond_caps(training_services, monkeypatch):
    import os
    import time
    from integration import diagnosis_profile_service as profiles
    service = training_services
    base = service.tag_profile_cache_key(scope={'mode': 'all'},
                                       exam_scope={'mode': 'current', 'session_ids': [14]})
    paths = []
    for index in range(3):
        key = (base[0], f'TEST-evict-{index}')
        service._save_local_profile(key, (pickle.dumps({'index': index}), pickle.dumps({})))
        paths.append(service._local_profile_entry_path(key))
    # Real timestamps: Windows clamps invalid nanosecond mtimes to one value.
    for index, path in enumerate(paths):
        moment = time.time_ns() - (len(paths) - index) * 10**9
        os.utime(path, ns=(moment, moment))
    monkeypatch.setattr(profiles, '_LOCAL_PROFILE_MAX_ENTRIES', 2)
    key = (base[0], 'TEST-evict-newest')
    service._save_local_profile(key, (pickle.dumps({'index': 3}), pickle.dumps({})))
    paths.append(service._local_profile_entry_path(key))
    assert not paths[0].exists() and not paths[1].exists()
    assert paths[2].exists() and paths[3].exists()
    assert len(list(service._local_profile_path().glob('*.entry'))) == 2


def test_semester_mastery_reuses_model_inputs_but_keeps_source_changes(training_services, monkeypatch):
    from datetime import datetime, UTC
    from tests.current_knowledge_support import install_current_knowledge
    from integration import diagnosis_profile_service as profiles
    from question_bank.mastery.model import week_of
    install_current_knowledge(training_services.question_bank_db_path)
    _clear_profile_memory()
    occurred = datetime(2026, 9, 7, tzinfo=UTC)
    observation = dict(student='12', item=(14, 'Q1'), qkey=(14, 'Q1'), activity=('exam', 14),
        session=14, source='exam', occurred_at=occurred, week=week_of(occurred), d=3.1, y=.6,
        links={'kp_alg_linear_equation': 1.})
    profile = {'exam_scope': {'mode': 'semester', 'curriculum_volume_id': 'bnu24-math-g8-upper'},
               '_mastery_population': True, '_mastery_observations': [observation]}
    calculate = profiles.CurrentMasteryCalculator.calculate
    calls = []
    def tracked(self, *args, **kwargs):
        calls.append(True)
        return calculate(self, *args, **kwargs)
    monkeypatch.setattr(profiles.CurrentMasteryCalculator, 'calculate', tracked)
    # Request grading snapshots use different temporary paths. The original
    # namespace still permits identical model inputs to share a result.
    from backend.api.read_connections import request_read_context
    paths = SimpleNamespace(db_path=training_services.grading_db_path, qb_db_path=training_services.question_bank_db_path)
    training_services.data_root = training_services.question_bank_db_path.parent.parent
    with request_read_context(paths) as ctx:
        before = ctx.diagnosis_service.semester_mastery(profile)
    before = training_services.semester_mastery(profile)
    assert before and calls == [True]
    with sqlite3.connect(training_services.grading_db_path) as writer:
        writer.execute("UPDATE session_details SET deduction_reason='TEST-description-only' WHERE result_id=14001 AND question_id='Q1'")
    after = training_services.semester_mastery(profile)
    assert after == before and calls == [True]
    after.clear()
    assert training_services.semester_mastery(profile) == before
    with sqlite3.connect(training_services.grading_db_path) as writer:
        writer.execute("UPDATE session_details SET score_awarded=10 WHERE result_id=14001 AND question_id='Q1'")
    changed = {**profile, '_mastery_observations': [{**observation, 'y': 1.}]}
    scored = training_services.semester_mastery(changed)
    assert scored != before and calls == [True, True]
    with sqlite3.connect(training_services.question_bank_db_path) as writer:
        writer.execute("INSERT INTO training_tasks(task_code,status) VALUES('TEST-mastery-input-version','completed')")
    training_services.semester_mastery(changed)
    assert calls == [True, True, True]
    from dataclasses import replace
    training_services.semester_mastery(changed, parameters=replace(profiles.CURRENT_MASTERY_PARAMETERS, sigma_theta=.9))
    assert calls == [True, True, True, True]
    from datetime import timedelta
    # The mastery input is keyed by Beijing week; +7 days always differs.
    training_services.semester_mastery(changed, as_of=datetime.now(UTC) + timedelta(days=7))
    assert len(calls) == 5
    training_services.semester_mastery(changed, exclude_training_evidence_ids=frozenset({'TEST-excluded-evidence'}))
    assert len(calls) == 6


def test_local_snapshot_save_failure_preserves_profile_result(training_services, monkeypatch):
    from integration import diagnosis_profile_service as profiles
    service = training_services
    service.persist_snapshots = True
    monkeypatch.setattr(profiles.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("TEST disk unavailable")))
    result = service.build_profiles(scope={"mode":"all"}, exam_scope={"mode":"current","session_ids":[14]})
    assert len(result['students']) == 2
    entries_dir = service._local_profile_path()
    assert not entries_dir.exists() or not list(entries_dir.iterdir())


def test_source_changed_during_preparation_does_not_publish_stale_snapshot(training_services, monkeypatch):
    service = training_services
    service.persist_snapshots = True
    original = service._compute_tag_profiles
    def changed_after_read(**kwargs):
        result = original(**kwargs)
        with sqlite3.connect(service.grading_db_path) as writer:
            writer.execute("UPDATE students SET name='TEST-after-read' WHERE id=12")
        return result
    monkeypatch.setattr(service, "_compute_tag_profiles", changed_after_read)
    result = service.build_profiles(scope={"mode":"all"}, exam_scope={"mode":"current","session_ids":[14]})
    assert next(s for s in result['students'] if s['student_id']=='12')['student_name'] != 'TEST-after-read'
    entries_dir = service._local_profile_path()
    assert not entries_dir.exists() or not list(entries_dir.iterdir())


@pytest.fixture
def training_services(tmp_path: Path) -> DiagnosisProfileService:
    # db 必须放在名为 "databases" 的目录下,生产代码据此把 tmp_path 推断为
    # data root(受控根),否则 resolve_stored_file_path 会拒绝 tmp_path 下的
    # rubric.json 等存储路径。
    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    grading_db_path = db_dir / "grading.db"
    question_bank_db_path = db_dir / "question_bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    initialize_database(question_bank_db_path)

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                    {"question_id": "Q3", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with grading_db._connect() as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S12", "学生甲", "八年级1班"),
                (15, "S15", "学生乙", "八年级1班"),
            ],
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '当前考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 20, 11, 0, '{}')
            """
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (14001, ?, ?, ?, '["UNKNOWN"]', ?, ?, ?)
            """,
            [
                (
                    "Q1",
                    6,
                    "缺少辅助线",
                    "逻辑断裂",
                    "辅助线思路缺失",
                    json.dumps(
                        [{"category": "审题错误", "summary": "条件识别不完整"}],
                        ensure_ascii=False,
                    ),
                ),
                ("Q2", 5, "", None, None, "[]"),
            ],
        )

    with connect(question_bank_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (?, ?, ?, 'success')
            """,
            [(1, "合成考试", "C:/private/source.docx")]
            + [
                (question_id, f"合成练习 {question_id}", f"practice-{question_id}.docx")
                for question_id in range(201, 209)
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, '解答题', ?, '5')
            """,
            [
                (101, 1, "1", "当前考试原题"),
                (102, 1, "2", "已掌握的题"),
                (201, 201, "11", "利用边角关系证明两个三角形全等"),
                (202, 202, "12", "根据中点条件构造全等三角形"),
                (203, 203, "13", "在折叠图形中寻找对应边并完成证明"),
                (204, 204, "14", "结合平行线性质判定三角形全等"),
                (205, 205, "15", "运用角平分线条件求未知线段长度"),
                (206, 206, "16", "从旋转图形中识别全等关系"),
                (207, 207, "17", "添加辅助线后证明两条线段相等"),
                (208, 208, "18", "在复杂几何图中选择合适的全等判定"),
            ],
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (question_id, "knowledge_point", "三角形全等")
                for question_id in [101, 102, *range(201, 209)]
            ]
            + [(101, "method", "构造辅助线")],
        )

    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=101,
        link_method="paper_question_number",
    )
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q2",
        bank_question_id=102,
        link_method="paper_question_number",
    )

    return DiagnosisProfileService(grading_db_path, question_bank_db_path)


@pytest.fixture
def training_client(training_services) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_request_diagnosis_profile_service,
    )

    diagnosis = training_services
    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: diagnosis
    return TestClient(app)


def test_training_diagnosis_uses_question_tag_identity(
    training_client: TestClient,
    training_services, monkeypatch,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert not any(key.startswith("_") for key in payload)
    assert payload["diagnosis_identity"] == "question_tag"
    assert payload["scope"]["mode"] == "student"
    assert payload["scope"]["student_ids"] == ["12"]
    assert payload["scope"]["matched_student_count"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["scope"]["scope_revision"])
    assert (
        payload["scope"]["student_score_profiles"]["12"]["score_rate_source"]
        == "current_exam"
    )
    assert payload["exam_scope"]["session_ids"] == [14]
    weak = payload["students"][0]["weak_points"][0]
    assert weak["knowledge_point"] == "三角形全等"
    assert weak["mastery"] == pytest.approx(11 / 15, abs=0.0001)
    assert weak["tag_context"]["method"] == ["构造辅助线"]
    assert payload["coverage"] == {
        "covered_items": 2,
        "total_items": 3,
        "missing_items": {"Q3": "missing_link"},
    }
    assert "C:/private" not in response.text
    with monkeypatch.context() as patch:
        patch.setattr(training_services, 'build_profiles', lambda **kwargs: pytest.fail('repeated public conversion'))
        repeated = training_client.post('/api/training/diagnosis', json={
            'scope': {'mode': 'student', 'student_ids': ['12']},
            'exam_scope': {'mode': 'current', 'session_ids': [14]},
        })
        assert repeated.content == response.content
    from backend.api.routers.training import _grouping_module
    from datetime import datetime, UTC
    grouped_calls = []
    def chapter_groups(**kwargs):
        grouped_calls.append(kwargs['config'].question_count)
        return {'groups': [], 'selection': None, 'unassigned': [],
                'warnings': [f"TEST-count-{kwargs['config'].question_count}"]}
    grouping = SimpleNamespace(db_path=training_services.question_bank_db_path,
        current_knowledge=SimpleNamespace(release_id='TEST-release'), clock=lambda: datetime(2026, 10, 3, tzinfo=UTC),
        chapter_groups=chapter_groups)
    training_client.app.dependency_overrides[_grouping_module] = lambda: grouping
    grouped_body = {'scope': {'mode': 'student', 'student_ids': ['12']},
        'exam_scope': {'mode': 'current', 'session_ids': [14]},
        'grouping': {'scope_keys': ['kp_test_scope'], 'curriculum_volume_id': 'bnu24-math-g8-upper',
                     'question_count': 10, 'difficulty_max': 8, 'exclude_current_exam_originals': True}}
    grouped = training_client.post('/api/training/diagnosis', json=grouped_body)
    assert grouped.status_code == 200, grouped.text
    assert grouped.json()['grouping']['warnings'] == ['TEST-count-10']
    from backend.api.routers.training import _public_training_mapping
    from backend.api.schemas.training import TrainingDiagnosisResponse
    original = training_services.build_profiles(scope=grouped_body['scope'], exam_scope=grouped_body['exam_scope'])
    original['grouping'] = grouped.json()['grouping']
    expected = TrainingDiagnosisResponse.model_validate(_public_training_mapping(
        {k: v for k, v in original.items() if not k.startswith('_')})).model_dump(mode='json', exclude_none=True)
    assert grouped.content == json.dumps(expected, ensure_ascii=False, allow_nan=False,
                                       separators=(',', ':')).encode('utf-8')
    from copy import deepcopy
    from backend.api.routers.training import _validated_diagnosis_json
    # Direct serialization must preserve finite values and reject invalid
    # numbers, including numeric strings coerced by the response model.
    for value in [float('nan'), float('inf'), 'NaN', 'Infinity']:
        invalid = deepcopy(expected)
        invalid['students'][0]['weak_points'][0]['score_sum'] = value
        with pytest.raises(ValueError):
            _validated_diagnosis_json(_public_training_mapping(invalid, reject_nonfinite=True))
    invalid = deepcopy(expected)
    invalid['grouping']['TEST-value'] = float('inf')
    with pytest.raises(ValueError):
        _public_training_mapping(invalid, reject_nonfinite=True)
    assert training_client.post('/api/training/diagnosis', json=grouped_body).content == grouped.content
    assert grouped_calls == [10]
    from backend.api.routers.training import _grouped_diagnosis_response_bytes
    from backend.api.schemas.training import TrainingDiagnosisRequest
    request = TrainingDiagnosisRequest.model_validate(grouped_body)
    training_services.persist_snapshots = True
    saved = _grouped_diagnosis_response_bytes(training_services, grouping,
        scope=request.scope.model_dump(exclude_none=True), exam_scope=request.exam_scope.model_dump(exclude_none=True),
        grouping=request.grouping)
    assert saved == grouped.content
    script = '''
import hashlib,json,sys
from types import SimpleNamespace
from datetime import datetime,UTC
from backend.api.app import create_app
from backend.api.routers import training
from backend.api.schemas.training import TrainingDiagnosisRequest
from integration.diagnosis_profile_service import DiagnosisProfileService
s=DiagnosisProfileService(sys.argv[1],sys.argv[2])
def fail(*args,**kwargs): raise AssertionError('recomputed full grouped response')
s.build_profiles=fail
training._build_grouped_diagnosis=fail
r=TrainingDiagnosisRequest.model_validate_json(sys.argv[3])
m=SimpleNamespace(current_knowledge=SimpleNamespace(release_id='TEST-release'),
    clock=lambda:datetime(2026,10,3,tzinfo=UTC))
result=training._grouped_diagnosis_response_bytes(s,m,scope=r.scope.model_dump(exclude_none=True),
    exam_scope=r.exam_scope.model_dump(exclude_none=True),grouping=r.grouping)
print(hashlib.sha256(result).hexdigest())
'''
    restored = subprocess.run([sys.executable, '-c', script, str(training_services.grading_db_path),
        str(training_services.question_bank_db_path), json.dumps(grouped_body)],
        capture_output=True, text=True, timeout=30)
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout.strip() == hashlib.sha256(saved).hexdigest()
    from backend.api.routers import training as router_module
    with monkeypatch.context() as patch:
        patch.setattr(router_module, 'get_personalized_recommendation_module',
            lambda: pytest.fail('cached grouped response constructed a module'))
        training_client.app.dependency_overrides.pop(_grouping_module)
        assert training_client.post('/api/training/diagnosis', json=grouped_body).content == grouped.content
    training_client.app.dependency_overrides[_grouping_module] = lambda: grouping
    grouped_body['grouping']['question_count'] = 8
    changed = training_client.post('/api/training/diagnosis', json=grouped_body)
    assert changed.status_code == 200
    assert changed.json()['grouping']['warnings'] == ['TEST-count-8']
    assert grouped_calls == [10, 8]
    with monkeypatch.context() as patch:
        patch.setattr(training_services, 'build_profiles', lambda **kwargs: pytest.fail('repeated grouped profile'))
        assert training_client.post('/api/training/diagnosis', json=grouped_body).content == changed.content
    asset = training_services.data_root / 'question_bank/rich_content/TEST-input.json'
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text('{}', encoding='utf-8')
    assert training_client.post('/api/training/diagnosis', json=grouped_body).content == changed.content
    assert grouped_calls == [10, 8, 8]
