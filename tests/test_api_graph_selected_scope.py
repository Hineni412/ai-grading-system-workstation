from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.routers.graph import get_current_graph_query_service
from backend.repositories.db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.relations.query_service import CurrentKnowledgeGraphQueryService
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)
from tests.current_knowledge_support import install_current_knowledge


def _seed_grading_db(tmp_path: Path) -> Path:
    """4 名学生 + 两场考试：乙在本次考试有证据，丙只有历史考试证据。"""
    import path_manager

    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    # rubric 必须落在受控数据根目录内，否则 _load_session_rubric 拒绝读取
    rubric_dir = path_manager.get_path_manager().data_root / "rubrics"
    rubric_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = rubric_dir / f"rubric-{tmp_path.name}.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(database) as conn:
        conn.executemany(
            """
            INSERT INTO students (id, student_code, name, class_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                (1, "S1", "甲", "一班"),
                (2, "S2", "乙", "一班"),
                (3, "S3", "丙", "一班"),
                (4, "S4", "丁", "一班"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, created_at
            ) VALUES (?, ?, ?, '', ?)
            """,
            [
                (1, "历史考试", str(rubric_path), "2026-01-01 09:00:00"),
                (2, "当前考试", str(rubric_path), "2026-02-01 09:00:00"),
            ],
        )
        # 丙只在历史考试有成绩（触发 historical fallback）
        # 乙在当前考试有成绩；甲、丁两场都没有证据
        for result_id, session_id, student_id, paper_id, graded_at in (
            (1, 1, 3, 1, "2026-01-02 10:00:00"),
            (2, 2, 2, 2, "2026-02-02 10:00:00"),
        ):
            conn.execute(
                """
                INSERT INTO exam_papers (
                    id, session_id, front_image, back_image, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, 'f.png', 'b.png', ?, 'matched', 'graded')
                """,
                (paper_id, session_id, student_id),
            )
            conn.execute(
                """
                INSERT INTO session_results (
                    id, session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json, graded_at
                ) VALUES (?, ?, ?, ?, 10, 6, 0, '{}', ?)
                """,
                (result_id, session_id, student_id, paper_id, graded_at),
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    id, result_id, question_id, score_awarded,
                    deduction_reason, knowledge_ids
                ) VALUES (?, ?, 'Q1', 6, '漏写单位', '["K"]')
                """,
                (result_id, result_id),
            )
        conn.commit()
    return database


def _seed_question_bank_db(
    tmp_path: Path, *, taxonomy_revision: int = 3, knowledge_key: str = "一元一次方程",
) -> Path:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database, taxonomy_revision=taxonomy_revision)
    with connect(database) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                question_number, question_type, question_text, answer_text
            ) VALUES ('1', 'choice', '2x = 4, x = ?', '2')
            """
        )
        bank_question_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value)
            VALUES (?, 'knowledge_point', ?)
            """,
            (bank_question_id, knowledge_key),
        )
    links = SourceQuestionLinkService(database)
    for session_id in (1, 2):
        links.confirm_link(
            grading_session_id=session_id,
            source_question_id="Q1",
            bank_question_id=bank_question_id,
            link_method="manual",
        )
    return database


def _client(grading_db: Path, question_bank_db: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: (
        DiagnosisProfileService(grading_db, question_bank_db)
    )
    app.dependency_overrides[get_current_graph_query_service] = lambda: (
        CurrentKnowledgeGraphQueryService(question_bank_db)
    )
    return TestClient(app)


def _seed_type_class_baseline(tmp_path, *, taxonomy_revision=11):
    """Two scored items, separate classes and final-score validity boundaries."""
    grading_db = _seed_grading_db(tmp_path)
    key = 'kp_bnu24_math_g8_upper_1_1_t01' if taxonomy_revision == 11 else 'sk_bnu24_math_g8_upper_1_1_101'
    bank_db = _seed_question_bank_db(tmp_path, taxonomy_revision=taxonomy_revision, knowledge_key=key)
    repositories = DiagnosisProfileService(grading_db, bank_db).db
    rubric_path = Path(repositories.sessions.get_grading_session(2)['rubric_path'])
    rubric_path.write_text(json.dumps({'questions': [
        {'question_id': 'Q1', 'max_score': 10, 'parts': []},
        {'question_id': 'Q2', 'max_score': 30, 'parts': []},
    ]}), encoding='utf-8')
    with sqlite3.connect(grading_db) as connection:
        connection.execute("UPDATE grading_sessions SET curriculum_volume_id=CASE WHEN id=2 THEN 'bnu24-math-g8-upper' ELSE 'bnu24-math-g7-lower' END")
        connection.execute("UPDATE students SET class_name='二班' WHERE id IN (3,4)")
        connection.executemany("INSERT INTO students(id,student_code,name,class_name) VALUES(?,?,?,?)", [
            (5, 'S5', 'TEST-空白', '一班'), (6, 'S6', 'TEST-未确认', '一班'),
            (7, 'S7', 'TEST-其他班', '三班'),
        ])
        detail_ids = {}
        for student, scores, state in [
            (1, (0, 0), {}), (2, (6, 30), {}), (3, (2, 0), {}),
            (4, (10, 15), {'Q2': {'need_review': True}}),
            (5, (0, 5), {'Q1': {'answer_is_blank_or_no_valid_work': True}, 'Q2': {'need_review': True}}),
            (6, (8, None), {'Q1': {'answer_discarded_by_smudge': True}}),
            (7, (10, 30), {}),
        ]:
            if student == 2:
                result_id = 2
                connection.execute("UPDATE session_results SET total_score=40,student_score=36 WHERE id=2")
                detail_ids[(student, 'Q1')] = 2
            else:
                paper_id = connection.execute("INSERT INTO exam_papers(session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(2,'TEST-f.png','TEST-b.png',?,'matched','graded')", (student,)).lastrowid
                result_id = connection.execute("INSERT INTO session_results(session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json,graded_at) VALUES(2,?,?,40,?,0,?,'2026-02-02 10:00:00')",
                    (student, paper_id, sum(score or 0 for score in scores), json.dumps({'detail_metadata': state}))).lastrowid
                detail_ids[(student, 'Q1')] = connection.execute("INSERT INTO session_details(result_id,question_id,score_awarded,knowledge_ids) VALUES(?,'Q1',?,'[\"K\"]')", (result_id, scores[0])).lastrowid
            if scores[1] is not None:
                detail_ids[(student, 'Q2')] = connection.execute("INSERT INTO session_details(result_id,question_id,score_awarded,knowledge_ids) VALUES(?,'Q2',?,'[\"K\"]')", (result_id, scores[1])).lastrowid
    links = SourceQuestionLinkService(bank_db)
    links.confirm_link(grading_session_id=2, source_question_id='Q2', bank_question_id=1, link_method='TEST-baseline')
    for student, question, score, full in [(2, 'Q1', 10, 20), (4, 'Q2', 15, 20)]:
        repositories.reviews.confirm_teacher_score_lock(session_id=2, scan_batch_id='TEST-class-baseline',
            student_id=student, question_id=question, score_awarded=score, max_score=full,
            deduction_reason='TEST-教师确认', source_target_type='session_detail',
            source_target_id=detail_ids[(student, question)], expected_revision=0)
    return grading_db, bank_db, repositories, detail_ids


@pytest.mark.parametrize('scope,expected_classes', [
    ({'mode': 'selected', 'student_ids': ['1']}, {'一班'}),
    ({'mode': 'student', 'student_ids': ['1']}, {'一班'}),
    ({'mode': 'selected', 'student_ids': ['1', '3']}, {'一班', '二班'}),
    ({'mode': 'class', 'class_ids': ['一班']}, {'一班'}),
    ({'mode': 'all'}, {'一班', '二班', '三班'}),
], ids=['selected-one', 'student-one', 'selected-mixed-classes', 'class', 'all'])
def test_type_class_baseline_keeps_complete_classes_for_selected_targets(tmp_path, scope, expected_classes):
    grading_db, bank_db, _repositories, _details = _seed_type_class_baseline(tmp_path)
    service = DiagnosisProfileService(grading_db, bank_db)
    exam_scope = {'mode': 'semester', 'curriculum_volume_id': 'bnu24-math-g8-upper'}
    profile = service.build_profiles(scope=scope, exam_scope=exam_scope)
    assert profile['target_kind'] == 'type'
    totals = profile['_type_class_question_totals']
    assert set(totals) == expected_classes
    expected = {
        '一班': {'2': {'Q1': {'score_sum': 10.0, 'full_score_sum': 40.0, 'evidence_student_count': 3},
                       'Q2': {'score_sum': 30.0, 'full_score_sum': 60.0, 'evidence_student_count': 2}}},
        '二班': {'2': {'Q1': {'score_sum': 12.0, 'full_score_sum': 20.0, 'evidence_student_count': 2},
                       'Q2': {'score_sum': 15.0, 'full_score_sum': 50.0, 'evidence_student_count': 2}}},
        '三班': {'2': {'Q1': {'score_sum': 10.0, 'full_score_sum': 10.0, 'evidence_student_count': 1},
                       'Q2': {'score_sum': 30.0, 'full_score_sum': 30.0, 'evidence_student_count': 1}}},
    }
    assert totals == {name: expected[name] for name in expected_classes}
    if scope['mode'] in {'selected', 'student'}:
        assert {student['student_id'] for student in profile['students']} == set(scope['student_ids'])
    from backend.api.routers.training import _diagnosis_response_bytes
    public = json.loads(_diagnosis_response_bytes(service, scope=scope, exam_scope=exam_scope))
    assert '_type_class_question_totals' not in public
    assert {student['student_id'] for student in public['students']} == {student['student_id'] for student in profile['students']}


def test_type_class_baseline_refreshes_when_an_unselected_classmate_is_rescored(tmp_path):
    from unittest.mock import patch
    from integration import diagnosis_profile_service as profiles

    grading_db, bank_db, repositories, details = _seed_type_class_baseline(tmp_path)
    service = DiagnosisProfileService(grading_db, bank_db, persist_snapshots=True)
    scope = {'mode': 'selected', 'student_ids': ['1']}
    exam_scope = {'mode': 'semester', 'curriculum_volume_id': 'bnu24-math-g8-upper'}
    first_key = service.tag_profile_cache_key(scope=scope, exam_scope=exam_scope)
    first = service.build_profiles(scope=scope, exam_scope=exam_scope)
    repositories.reviews.confirm_teacher_score_lock(session_id=2, scan_batch_id='TEST-class-baseline',
        student_id=2, question_id='Q1', score_awarded=20, max_score=20,
        deduction_reason='TEST-未选中同学改分', source_target_type='session_detail',
        source_target_id=details[(2, 'Q1')], expected_revision=1)
    assert service.tag_profile_cache_key(scope=scope, exam_scope=exam_scope) != first_key
    updated = service.build_profiles(scope=scope, exam_scope=exam_scope)
    assert {student['student_id'] for student in updated['students']} == {'1'}
    assert updated['_type_class_question_totals']['一班']['2']['Q1'] == {
        'score_sum': 20.0, 'full_score_sum': 40.0, 'evidence_student_count': 3,
    }
    assert first['_type_class_question_totals']['一班']['2']['Q1']['score_sum'] == 10.0
    # Persisted diagnosis snapshots carry the same private contract.
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    restored = DiagnosisProfileService(grading_db, bank_db, persist_snapshots=True)
    with patch.object(restored, '_compute_tag_profiles', side_effect=AssertionError('recomputed current saved diagnosis')):
        cached = restored.build_profiles(scope=scope, exam_scope=exam_scope)
    assert cached['_type_class_question_totals'] == updated['_type_class_question_totals']


@pytest.mark.parametrize('revision', [10, 11], ids=['v8', 'v9'])
def test_type_class_baseline_is_not_read_for_summary_or_legacy_diagnosis(tmp_path, revision):
    from unittest.mock import patch

    grading_db, bank_db, _repositories, _details = _seed_type_class_baseline(tmp_path, taxonomy_revision=revision)
    service = DiagnosisProfileService(grading_db, bank_db)
    scope = {'mode': 'selected', 'student_ids': ['1']}
    exam_scope = {'mode': 'semester', 'curriculum_volume_id': 'bnu24-math-g8-upper'}
    with patch.object(service, '_type_class_question_totals', side_effect=AssertionError('unneeded class baseline read')):
        summary = service.build_summary_profiles(scope=scope, exam_scope=exam_scope)
        assert '_type_class_question_totals' not in summary
        if revision == 10:
            legacy = service.build_profiles(scope=scope, exam_scope=exam_scope)
            assert legacy['target_kind'] == 'skill'
            assert '_type_class_question_totals' not in legacy


def test_semester_graph_and_evidence_exclude_other_terms_and_empty_scope(tmp_path):
    grading_db = _seed_grading_db(tmp_path)
    question_bank_db = _seed_question_bank_db(tmp_path)
    with sqlite3.connect(grading_db) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id=CASE WHEN id=2 THEN 'bnu24-math-g8-upper' ELSE 'bnu24-math-g7-lower' END"
        )
    client = _client(grading_db, question_bank_db)
    query = {
        "scope": {"mode": "all", "use_historical_fallback": True},
        "exam_scope": {
            "mode": "semester",
            "curriculum_volume_id": "bnu24-math-g8-upper",
            "session_ids": [1],
        },
    }
    response = client.post("/api/graph/query", json=query)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["exam_scope"]["session_ids"] == [2]
    assert payload["scope"]["student_score_profiles"]["3"]["score_rate"] is None
    assert payload["scope"]["use_historical_fallback"] is False
    assert payload["nodes"]
    from backend.api.routers.graph import compute_graph_query_payload
    from integration.mastery_overview import build_mastery_overview, overview_payload
    from question_bank.relations.query_service import CurrentGraphQuery
    service = DiagnosisProfileService(grading_db, question_bank_db)
    graph_service = CurrentKnowledgeGraphQueryService(question_bank_db)
    full = service.build_profiles(scope=query['scope'], exam_scope=query['exam_scope'])
    expected_graph = graph_service.query(full, CurrentGraphQuery(),
                                         mastery_by_key=service.latest_aggregated_mastery)
    expected_overview = build_mastery_overview(full, volume_id='bnu24-math-g8-upper')
    by_key = {item['knowledge_key']: item for item in full['group_weak_points']}
    assert any(node['definition'] for node in expected_overview['nodes'])
    for node in expected_overview['nodes']:
        assert node['group_interval_low'] == by_key.get(node['knowledge_key'], {}).get('interval_low')
        assert node['group_interval_high'] == by_key.get(node['knowledge_key'], {}).get('interval_high')
        assert sum(node['distribution'].values()) == node['evidence_student_count']
    from unittest.mock import patch
    from integration import diagnosis_profile_service as profiles
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    with patch.object(service, '_error_cause_index', side_effect=AssertionError('summary read detailed causes')):
        assert compute_graph_query_payload(service, graph_service, scope=query['scope'],
            exam_scope=query['exam_scope'], query=CurrentGraphQuery()) == expected_graph
        assert overview_payload(service, scope=query['scope'], exam_scope=query['exam_scope'],
            volume_id='bnu24-math-g8-upper') == expected_overview
    # Selection filters output after the same population fit. Empty requested
    # session_ids on a semester must reuse supplied observations as well.
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    semester = {**query['exam_scope'], 'session_ids': []}
    original_projection = service._projected_tag_evidence
    with patch.object(service, '_projected_tag_evidence', wraps=original_projection) as calls:
        service.build_summary_profiles(scope={'mode': 'all'}, exam_scope=semester)
        assert calls.call_count == 1
    service.persist_snapshots = True
    assert overview_payload(service, scope=query['scope'], exam_scope=query['exam_scope'],
        volume_id='bnu24-math-g8-upper') == expected_overview
    assert compute_graph_query_payload(service, graph_service, scope=query['scope'],
        exam_scope=query['exam_scope'], query=CurrentGraphQuery()) == expected_graph
    saved = {item.name: item.read_bytes() for item in service._local_profile_path().glob('*.entry')}
    service.persist_snapshots = False
    from integration.mastery_overview import clear_overview_caches
    from backend.api.routers.graph import _GRAPH_QUERY_CACHE
    clear_overview_caches()
    _GRAPH_QUERY_CACHE.clear()
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    restored = DiagnosisProfileService(grading_db, question_bank_db)
    with patch.object(restored, '_compute_tag_profiles', side_effect=AssertionError('recomputed saved projection')):
        assert overview_payload(restored, scope=query['scope'], exam_scope=query['exam_scope'],
            volume_id='bnu24-math-g8-upper') == expected_overview
        assert compute_graph_query_payload(restored, graph_service, scope=query['scope'],
            exam_scope=query['exam_scope'], query=CurrentGraphQuery()) == expected_graph
    assert {item.name: item.read_bytes() for item in service._local_profile_path().glob('*.entry')} == saved
    key = "kp_alg_linear_equation"
    for volume, expected in [
        ("bnu24-math-g8-upper", {2}),
        ("term-without-exams", set()),
        ("", set()),
    ]:
        query["exam_scope"]["curriculum_volume_id"] = volume
        evidence = client.post("/api/graph/evidence", json={**query, "stable_key": key})
        assert evidence.status_code == 200, evidence.text
        assert {row["session_id"] for row in evidence.json()["items"]} == expected


def _overview_input(volume_id='bnu24-math-g8-upper'):
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    volume = curriculum_volume(volume_id=volume_id)
    chapter = volume['chapters'][0]
    section = chapter['sections'][0]
    topic = section['knowledge_points'][0]['id']
    entries = [(chapter['knowledge_id'], 'chapter', None),
               (section['knowledge_id'], 'section', chapter['knowledge_id']),
               (topic, 'topic', section['knowledge_id']),
               ('skill-current', 'skill', section['knowledge_id']),
               ('old-chapter', 'chapter', None), ('old-section', 'section', 'old-chapter'),
               ('old-topic', 'topic', 'old-section'), ('old-skill', 'skill', 'old-topic'),
               ('old-empty', 'topic', 'old-section')]
    diagnosis = {'knowledge_catalog': [{'knowledge_key': key, 'knowledge_point': f'册｜章｜节｜{key}',
        'node_kind': kind, 'parent_knowledge_key': parent, 'definition': f'{key} definition'}
        for key, kind, parent in entries],
        'students': [{'student_id': '1', 'weak_points': [
            {'knowledge_key': key, 'mastery': .2, 'evidence_count': 3, 'tier': 'weak'}
            for key in (topic, 'skill-current', 'old-topic', 'old-skill')]},
            {'student_id': '2', 'weak_points': [{'knowledge_key': 'old-topic', 'mastery': .3,
                'evidence_count': 2, 'tier': 'weak'}]}],
        'group_weak_points': [{'knowledge_key': topic, 'mastery': .2, 'interval_low': .1, 'interval_high': .4}],
        'knowledge_associations': [
            {'topic_key': topic, 'skill_key': 'skill-current', 'question_count': 3,
             'same_part_question_count': 2, 'basis': 'same_part'},
            {'topic_key': 'old-topic', 'skill_key': 'skill-current', 'question_count': 1,
             'same_part_question_count': 0, 'basis': 'question_cooccurrence'},
            {'topic_key': 'old-empty', 'skill_key': 'old-skill', 'question_count': 1,
             'same_part_question_count': 0, 'basis': 'question_cooccurrence'}]}
    return diagnosis, topic


def test_overview_keeps_prior_volume_evidence_separate_from_all_current_counts():
    from integration.mastery_overview import build_mastery_overview
    from backend.api.schemas.training import TrainingOverviewResponse
    diagnosis, topic = _overview_input()
    result = build_mastery_overview(diagnosis, volume_id='bnu24-math-g8-upper')
    TrainingOverviewResponse.model_validate({**result, 'scope': {'mode': 'all', 'student_ids': ['1', '2']},
        'exam_scope': {'mode': 'semester', 'session_ids': [], 'sessions': []}})
    nodes = {n['knowledge_key']: n for n in result['nodes']}
    assert 'old-empty' not in nodes
    for key in ('old-topic', 'old-skill'):
        assert nodes[key]['in_volume'] is False
        assert nodes[key]['chapter_key'] == 'old-chapter'
        assert nodes[key]['section_key'] == 'old-section'
    current_only = {**diagnosis, 'knowledge_catalog': [n for n in diagnosis['knowledge_catalog']
        if not n['knowledge_key'].startswith('old-')]}
    baseline = build_mastery_overview(current_only, volume_id='bnu24-math-g8-upper')
    assert result['summary'] == baseline['summary']
    assert result['students'] == baseline['students']
    assert result['summary']['evidence_student_count'] == 1
    assert result['summary']['weak_topic_count'] == 1
    assert result['summary']['weak_skill_count'] == 1
    assert result['students'][1]['topics']['evidence'] == 0
    assert nodes[topic]['definition'] == f'{topic} definition'
    assert nodes[topic]['group_interval_low'] == .1
    assert nodes[topic]['group_interval_high'] == .4
    assert len(result['associations']) == 2
    for association in result['associations']:
        assert association['topic_key'] in nodes and association['skill_key'] in nodes
        assert (association['basis'] == 'same_part') == (association['same_part_question_count'] > 0)


def test_overview_mixed_release_keeps_unconverted_volume_and_associations(monkeypatch):
    from types import SimpleNamespace
    from integration import mastery_overview as module
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import (
        load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release,
    )

    release = load_release_for_taxonomy_revision(11)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    volume_id = 'bnu24-math-g7-upper'
    diagnosis, topic = _overview_input(volume_id)
    diagnosis['exam_scope'] = {'mode': 'semester', 'curriculum_volume_id': volume_id}
    baseline = module.build_mastery_overview(diagnosis, volume_id=volume_id)
    result = module.build_mastery_overview(diagnosis, volume_id=volume_id, resolver=resolver)
    assert result == baseline
    assert result['target_kind'] == 'skill'
    assert result['summary']['skill_count'] == 1
    assert result['summary']['weak_skill_count'] == 1
    assert {node['knowledge_key'] for node in result['nodes']} >= {topic, 'skill-current'}
    assert len(result['associations']) == 2

    # The cached overview path still reads skill/topic associations for seven.
    calls = []
    monkeypatch.setattr(module.CurrentKnowledgeResolver, 'from_active_database', lambda _: resolver)
    monkeypatch.setattr(module, 'load_question_facets', lambda *_: calls.append('facets') or {})
    monkeypatch.setattr(module, 'knowledge_skill_associations', lambda _: diagnosis['knowledge_associations'])
    service = SimpleNamespace(tag_profile_cache_key=lambda **_: ('TEST-v9-seven-overview',),
        build_profiles=lambda **_: diagnosis, build_summary_profiles=lambda **_: diagnosis,
        question_bank_db_path=Path('TEST-unused.db'), persist_snapshots=False)
    assert module.overview_payload(service, scope={}, exam_scope=diagnosis['exam_scope'],
                                   volume_id=volume_id) == baseline
    assert calls == ['facets']
    # A stored type flag from another volume cannot switch this requested scope.
    stored = {**diagnosis, 'target_kind': 'type',
              'exam_scope': {'curriculum_volume_id': 'bnu24-math-g8-upper'}}
    assert module.build_mastery_overview(stored, volume_id=volume_id)['target_kind'] == 'skill'


def test_overview_association_failure_preserves_mastery_and_warns(monkeypatch):
    from integration import mastery_overview as module
    diagnosis, _topic = _overview_input()
    from types import SimpleNamespace
    service = SimpleNamespace(tag_profile_cache_key=lambda **_: ('TEST-overview-association-failure',),
        build_profiles=lambda **_: diagnosis, build_summary_profiles=lambda **_: diagnosis,
        question_bank_db_path=Path('TEST-unused.db'), persist_snapshots=False)
    monkeypatch.setattr(module.CurrentKnowledgeResolver, 'from_active_database', lambda _: object())
    def fail(*_):
        raise sqlite3.OperationalError('TEST-association-unavailable')
    monkeypatch.setattr(module, 'load_question_facets', fail)
    actual = module.overview_payload(service, scope={}, exam_scope={}, volume_id='bnu24-math-g8-upper')
    expected = module.build_mastery_overview(diagnosis, volume_id='bnu24-math-g8-upper', associations=[])
    assert actual.pop('warnings') == ['知识点与技能关联暂不可用']
    expected.pop('warnings')
    assert actual == expected


def test_overview_include_student_detail_strips_lists_without_touching_cache(tmp_path):
    """include_student_detail=False 只裁剪返回体，不改动共享缓存的完整结果。"""
    grading_db = _seed_grading_db(tmp_path)
    question_bank_db = _seed_question_bank_db(tmp_path)
    client = _client(grading_db, question_bank_db)
    query = {
        "scope": {"mode": "all", "use_historical_fallback": True},
        "exam_scope": {
            "mode": "semester",
            "curriculum_volume_id": "bnu24-math-g8-upper",
            "session_ids": [1],
        },
    }
    full = client.post("/api/training/overview", json=query)
    assert full.status_code == 200, full.text
    full_payload = full.json()
    assert full_payload["students"]

    slim = client.post(
        "/api/training/overview", json={**query, "include_student_detail": False})
    assert slim.status_code == 200, slim.text
    slim_payload = slim.json()
    assert slim_payload == {
        **full_payload,
        "associations": [],
        "students": [],
        "nodes": [{**node, "students": []} for node in full_payload["nodes"]],
    }

    # 缓存的完整载荷未被裁剪：随后的完整请求仍返回逐学生数据。
    again = client.post("/api/training/overview", json=query)
    assert again.status_code == 200, again.text
    assert again.json() == full_payload


def _type_overview_input():
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume

    volume = curriculum_volume(volume_id='bnu24-math-g8-upper')
    chapter = volume['chapters'][0]
    section = chapter['sections'][0]
    chapter_key, section_key = str(chapter['knowledge_id']), str(section['knowledge_id'])
    type_a, type_b, type_hidden = (f'{section_key}_t{i:02d}' for i in (1, 2, 3))
    entries = [(chapter_key, 'chapter', None), (section_key, 'section', chapter_key),
               (type_a, 'type', section_key), (type_b, 'type', section_key),
               (type_hidden, 'type', section_key),
               ('old-chapter', 'chapter', None), ('old-section', 'section', 'old-chapter'),
               ('old-type', 'type', 'old-section'),
               # Topic/skill catalog rows stay out of the type-mode node set.
               ('topic-1', 'topic', section_key), ('skill-1', 'skill', section_key)]

    def ref(question_id, bank, score, full, *, kind='current_exam', weight=1.0):
        return {'session_id': 1, 'session_name': '期中考试', 'question_id': question_id,
                'bank_question_id': bank, 'score_awarded': score, 'full_score': full,
                'source_kind': kind, 'assessment': {'eligible': True, 'evidence_weight': weight}}

    diagnosis = {'knowledge_catalog': [{'knowledge_key': key, 'knowledge_point': f'册｜章｜节｜{key}',
        'node_kind': kind, 'parent_knowledge_key': parent, 'definition': f'{key} definition'}
        for key, kind, parent in entries],
        'students': [
            {'student_id': '1', 'weak_points': [
                {'knowledge_key': type_a, 'mastery': .4, 'evidence_count': 4, 'tier': 'weak',
                 'source_question_refs': [
                     ref('Q1', 10, 2, 4),
                     # Same (session, question) counts once per student.
                     ref('Q1', 10, 0, 4),
                     ref('Q2', 20, 1, 4),
                     # Ties on class rate pick the smaller bank question id.
                     ref('Q3', 5, 5, 8),
                     # Training evidence never feeds the class-exam aggregation.
                     ref('Q9', 90, 0, 4, kind='training'),
                     ref('Q8', 80, 0, 4, weight=0.5),
                 ]},
                {'knowledge_key': type_b, 'mastery': .5, 'evidence_count': 1, 'tier': 'weak',
                 'source_question_refs': [ref('Q5', 40, 2, 4)]}]},
            {'student_id': '2', 'weak_points': [
                {'knowledge_key': type_a, 'mastery': .9, 'evidence_count': 2, 'tier': 'stable',
                 'source_question_refs': [ref('Q1', 10, 4, 4), ref('Q2', 20, 4, 4)]},
                {'knowledge_key': 'old-type', 'mastery': .3, 'evidence_count': 1, 'tier': 'weak',
                 'source_question_refs': [ref('Q7', 30, 1, 4, kind='historical_exam')]}]}],
        'group_weak_points': [], 'knowledge_associations': [
            {'topic_key': 'topic-1', 'skill_key': 'skill-1', 'question_count': 1,
             'same_part_question_count': 0, 'basis': 'question_cooccurrence'}]}
    return diagnosis, (type_a, type_b, type_hidden)


def test_overview_type_mode_lists_types_with_typical_question():
    """Typed releases show types only, plus the class's lowest-rate exam question."""
    from types import SimpleNamespace
    from integration.mastery_overview import build_mastery_overview
    from backend.api.schemas.training import TrainingOverviewResponse
    diagnosis, (type_a, type_b, type_hidden) = _type_overview_input()
    resolver = SimpleNamespace(release_id='TEST-typed-release',
        nodes=[SimpleNamespace(stable_key=key) for key in (type_a, type_b, type_hidden)])

    result = build_mastery_overview(diagnosis, volume_id='bnu24-math-g8-upper',
                                    resolver=resolver)
    assert result['target_kind'] == 'type'
    assert result['associations'] == []
    nodes = {n['knowledge_key']: n for n in result['nodes']}
    assert {n['kind'] for n in nodes.values()} <= {'chapter', 'section', 'type'}
    assert 'topic-1' not in nodes and 'skill-1' not in nodes
    assert nodes['old-type']['in_volume'] is False
    assert result['summary']['topic_count'] == 0 and result['summary']['skill_count'] == 0
    # type_count covers the in-volume column only; weak_type_count likewise.
    assert result['summary']['type_count'] == 3 and result['summary']['weak_type_count'] == 2
    assert nodes[type_hidden]['evidence_student_count'] == 0
    assert nodes[type_hidden]['typical_question'] is None

    # type_a: Q2 and Q3 tie at 5/8; the smaller bank id (Q3 → 5) wins.
    typical = nodes[type_a]['typical_question']
    assert typical == {'bank_question_id': 5, 'session_name': '期中考试',
                       'question_label': '3', 'class_rate': 0.625}
    others = nodes[type_a]['other_questions']
    assert [(o['question_label'], o['class_rate']) for o in others] == [('2', 0.625), ('1', 0.75)]
    assert '9' not in {o['question_label'] for o in others}  # training ref excluded
    assert '8' not in {o['question_label'] for o in others}  # low-weight ref excluded

    students = {s['student_id']: s for s in result['students']}
    assert students['1']['types']['weak'] == 2 and students['1']['types']['evidence'] == 2
    assert students['1']['topics']['evidence'] == 0 and students['1']['skills']['evidence'] == 0
    TrainingOverviewResponse.model_validate({**result,
        'scope': {'mode': 'all', 'student_ids': ['1', '2']},
        'exam_scope': {'mode': 'semester', 'session_ids': [], 'sessions': []}})

    # The persisted-cache path (no resolver) follows the stored target_kind.
    legacy = build_mastery_overview({**diagnosis, 'target_kind': 'type'},
                                    volume_id='bnu24-math-g8-upper', resolver=None)
    assert legacy['target_kind'] == 'type'
    assert {n['knowledge_key'] for n in legacy['nodes']} == set(nodes)

    # The same catalog under a resolver without type nodes keeps skill mode.
    skill_resolver = SimpleNamespace(release_id='TEST-legacy-release', nodes=[])
    legacy = build_mastery_overview(diagnosis, volume_id='bnu24-math-g8-upper',
                                    resolver=skill_resolver)
    assert legacy['target_kind'] == 'skill'
    legacy_keys = {n['knowledge_key'] for n in legacy['nodes']}
    assert not {type_a, type_b, type_hidden, 'old-type'} & legacy_keys
    assert 'skill-1' in legacy_keys
    assert all(n['kind'] != 'type' for n in legacy['nodes'])


def test_overview_mixed_chapters_count_actual_training_targets():
    from types import SimpleNamespace
    from integration.mastery_overview import build_mastery_overview
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    from backend.api.schemas.training import TrainingOverviewResponse
    volume_id = "bnu24-math-g8-lower"
    first, second = curriculum_volume(volume_id=volume_id)["chapters"][:2]
    first_section, second_section = first["sections"][0], second["sections"][0]
    type_key = first_section["knowledge_id"] + "_t01"
    topic_one = first_section["knowledge_points"][0]["id"]
    topic_two = second_section["knowledge_points"][0]["id"]
    skill = "sk_bnu24_math_g8_lower_2_1_01"
    rows = [(first["knowledge_id"], "chapter", None), (second["knowledge_id"], "chapter", None),
            (first_section["knowledge_id"], "section", first["knowledge_id"]),
            (second_section["knowledge_id"], "section", second["knowledge_id"]),
            (type_key, "type", first_section["knowledge_id"]),
            (topic_one, "topic", first_section["knowledge_id"]),
            (topic_two, "topic", second_section["knowledge_id"]),
            (skill, "skill", second_section["knowledge_id"])]
    resolver = SimpleNamespace(release_id="TEST-mixed-overview", taxonomy_revision=11,
        nodes=[SimpleNamespace(stable_key=key) for key, _, _ in rows],
        relations=[SimpleNamespace(source_key=key, target_key=parent, relation_type="parent")
                   for key, _, parent in rows if parent])
    diagnosis = {"knowledge_catalog": [{"knowledge_key": key, "knowledge_point": key,
        "parent_knowledge_key": parent, "node_kind": kind} for key, kind, parent in rows],
        "students": [{"student_id": "TEST-student", "weak_points": []}], "group_weak_points": []}
    result = build_mastery_overview(diagnosis, volume_id=volume_id, resolver=resolver)
    assert result["target_kind"] == "mixed"
    nodes = {item["knowledge_key"]: item for item in result["nodes"]}
    assert nodes[type_key]["target_kind"] == "type"
    assert nodes[topic_two]["target_kind"] == "knowledge"
    assert topic_one not in nodes and skill not in nodes
    assert result["summary"]["type_count"] == 1
    assert result["summary"]["topic_count"] == 1
    assert result["summary"]["skill_count"] == 0
    assert result["summary"]["target_count"] == 2
    TrainingOverviewResponse.model_validate({**result, "scope": {"mode": "all", "student_ids": ["TEST-student"]},
        "exam_scope": {"mode": "semester", "session_ids": [], "sessions": []}})
