from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.routers.graph import get_current_graph_query_service
from db_manager import DBManager
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


def _seed_question_bank_db(tmp_path: Path) -> Path:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
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
            VALUES (?, 'knowledge_point', '一元一次方程')
            """,
            (bank_question_id,),
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


def _overview_input():
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    volume = curriculum_volume(volume_id='bnu24-math-g8-upper')
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
