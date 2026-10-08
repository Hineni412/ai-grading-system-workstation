"""Class shortlist expectations, using synthetic evidence and a real question bank."""

from copy import deepcopy
from question_bank.recommendation.personalized import PersonalizedRecommendationModule
from tests.training.test_personalized_recommendation import (
    _approve_synthetic_criteria,
    _capture_initial_source_reads,
    _install_release_with_skills,
)

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_assembly_workspace_service,
    get_personalized_recommendation_module,
    get_question_bank_read_service,
    get_question_bank_db_path,
    get_request_diagnosis_profile_service,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

VOLUME = load_curriculum_catalog()["volumes"][2]
CHAPTER = VOLUME["chapters"][0]
POINTS = CHAPTER["sections"][0]["knowledge_points"][:3]
CHAPTER_TWO_POINT = VOLUME["chapters"][1]["sections"][0]["knowledge_points"][0]
SKILLS = tuple(f"sk_assistant_{index}" for index in range(3))
SK_CHAPTER_TWO = "sk_assistant_chapter_two"
SKILL_PARENTS = {
    **{skill: point["id"] for skill, point in zip(SKILLS, POINTS)},
    SK_CHAPTER_TWO: CHAPTER_TWO_POINT["id"],
}
SKILL_NAMES = {skill: f"合成技能 {skill}" for skill in SKILL_PARENTS}


def test_class_weaknesses_use_backend_tiers_for_attention_and_order():
    from question_bank.services.assembly_assistant import class_weaknesses
    profile = diagnosis()
    for index, student in enumerate(profile["students"][:4]):
        student["weak_points"][0]["tier"] = "unsteady" if index < 3 else "stable"
        student["weak_points"][1]["tier"] = "weak" if index == 0 else "insufficient"
    points = class_weaknesses(profile, volume_id=VOLUME["id"], chapter_id=CHAPTER["id"])
    assert points[0]["knowledge_key"] == SKILLS[1]
    assert points[0]["weak_tier_student_count"] == 1
    assert points[0]["weak_student_count"] == 1
    first = next(point for point in points if point["knowledge_key"] == SKILLS[0])
    assert first["weak_student_count"] == 3
    assert first["evidence_student_count"] == 4


def diagnosis():
    students = []
    for index in range(4):
        points = [
            {
                "knowledge_key": skill,
                "knowledge_point": SKILL_NAMES[skill],
                "mastery": (0.7 if target == 0 else 0.2)
                if index < (3 if target == 0 else 1)
                else 0.9,
                "evidence_count": 2,
                "score_sum": 3,
                "full_score_sum": 5,
            }
            for target, skill in enumerate(SKILLS[:2])
        ]
        students.append(
            {
                "student_id": str(index),
                "class_id": "合成9班",
                "score_rate": 0.6,
                "weak_points": points,
            }
        )
    students.append(
        {
            "student_id": "no-evidence",
            "class_id": "合成9班",
            "score_rate": None,
            "weak_points": [],
        }
    )
    return {
        "students": students,
        "exam_scope": {"session_ids": [7, 8]},
        "knowledge_associations": [
            {
                "skill_key": skill,
                "topic_key": point["id"],
                "same_part_question_count": 1,
            }
            for skill, point in zip(SKILLS, POINTS)
        ],
        "group_weak_points": [
            {
                "knowledge_key": skill,
                "knowledge_point": SKILL_NAMES[skill],
                "mastery": m,
                "evidence_count": 8,
            }
            for skill, m in zip(SKILLS[:2], (0.75, 0.725))
        ],
    }


@pytest.fixture
def client_and_source(tmp_path, request):
    db_path = tmp_path / "bank.db"
    initialize_database(db_path)
    _install_release_with_skills(
        db_path,
        revision=getattr(request, 'param', 4),
        skill_parents=SKILL_PARENTS,
    )
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,grade,semester,textbook_version,import_status) VALUES(1,'合成候选题库',?,?,?,'success')",
            (VOLUME["grade"], VOLUME["semester"], VOLUME["textbook_version"]),
        )
        stems = [
            "计算含根式算式",
            "判断三角形形状",
            "描述坐标点距离",
            "建立方程并检验解",
            "比较两组数据集中趋势",
            "在网格中作几何图形",
            "归纳运算规律求值",
        ]
        contexts = [
            "结合校园测量情境",
            "依据温度变化记录",
            "在直角坐标网格中",
            "按运动会成绩表",
            "围绕购物折扣问题",
        ]
        for qid in range(1, 34):
            skill = SKILLS[0 if qid <= 27 else 1]
            # qid%7 and qid%5 give every question a unique stem+context pair.
            text = f"{stems[qid % len(stems)]}，{contexts[qid % len(contexts)]}，写出完整解答过程。"
            if qid == 33:
                text = f"{stems[1 % len(stems)]}，{contexts[1 % len(contexts)]}，写出完整解答过程。"  # Duplicate of excluded exam original.
            conn.execute(
                "INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(?,1,?,'选择题',?,'合成解析','3')",
                (qid, str(qid), text),
            )
            conn.execute(
                "INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'knowledge_point',?)",
                (qid, skill),
            )
    _approve_synthetic_criteria(db_path, tmp_path, tuple(range(1, 34)))
    module = PersonalizedRecommendationModule(db_path=db_path, data_root=tmp_path)
    module._recent_question_ids = lambda ids, **kwargs: {sid: {1, 33} for sid in ids}
    reader = QuestionBankReadService(db_path, data_root=tmp_path)
    workspace = AssemblyWorkspaceService(tmp_path)
    original = workspace.load_draft()
    workspace.save_draft(
        expected_revision=original.revision,
        draft={
            **original.to_payload(),
            "basket_ids": [32],
            "order_ids": [32],
            "title": "已有教师选题",
        },
    )
    source = diagnosis()
    calls = []

    class Profiles:
        def build_profiles(self, *, scope, exam_scope):
            calls.append((scope, exam_scope))
            return deepcopy(source)

        def graded_activities(self, student_ids):
            return []

    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: Profiles()
    app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    app.dependency_overrides[get_question_bank_db_path] = lambda: db_path
    app.dependency_overrides[get_assembly_workspace_service] = lambda: workspace
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: module
    # All domain dependencies are isolated above. Application startup installs
    # an unrelated full knowledge release; it is not part of shortlist tests.
    client = TestClient(app)
    try:
        yield client, source, calls, workspace
    finally:
        client.close()


def request(**patch):
    return {
        "class_id": "合成9班",
        "curriculum_volume_id": VOLUME["id"],
        "chapter_id": CHAPTER["id"],
        **patch,
    }


def test_multiclass_manual_exam_scope_and_legacy_class_input(client_and_source):
    client, _, calls, _ = client_and_source
    response = client.post('/api/question-assembly/assistant/candidates', json=request(class_ids=['合成10班', '合成9班'], session_ids=[8], recent_activity_count=0))
    assert response.status_code == 200
    assert calls[-1][0]['class_ids'] == ['合成10班', '合成9班']
    assert calls[-1][1]['mode'] == 'manual'
    assert calls[-1][1]['session_ids'] == [8]
    assert client.post('/api/question-assembly/assistant/candidates', json=request()).status_code == 200
    assert calls[-1][0]['class_ids'] == ['合成9班']


def test_exam_question_rates_weight_students_and_preserve_missing_causes(client_and_source, monkeypatch):
    from types import SimpleNamespace as NS
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from backend.config_generation import contract
    client, _, _, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    key = 'sk_test_rate'
    students = [{'id': i, 'class_name': '合成9班' if i < 3 else '合成10班'} for i in (1, 2, 3)]
    sessions = [{'id': 7, 'session_name': 'TEST-两班考试', 'curriculum_volume_id': VOLUME['id']}, {'id': 8, 'session_name': 'TEST-单班考试', 'curriculum_volume_id': VOLUME['id']}]
    rows = [{'session_id': 7, 'student_id': i, 'question_id': 'Q1', 'score_awarded': s} for i,s in ((1,2),(2,2),(3,8))]
    rows += [{'session_id': 8, 'student_id': 3, 'question_id': 'Q1', 'score_awarded': 4, 'teacher_final_max_score': 5}, {'session_id': 7, 'student_id': 1, 'question_id': 'Q2', 'score_awarded': 0}, {'session_id': 7, 'student_id': 2, 'question_id': 'Q2', 'score_awarded': 3}, {'session_id': 7, 'student_id': 3, 'question_id': 'Q3', 'score_awarded': 2}, {'session_id': 7, 'student_id': 1, 'question_id': 'Q3', 'score_awarded': None}]
    service = DiagnosisProfileService.__new__(DiagnosisProfileService)
    service.question_bank_db_path = module.db_path
    service.db = NS(students=NS(list_students=lambda: students), sessions=NS(list_grading_sessions=lambda: sessions), results=NS(get_active_assessment_rows=lambda **_: rows, _load_session_rubric=lambda sid: {}, _load_rubric_maps_for_session=lambda sid: {'score': {'Q1': 10, 'Q2': 10, 'Q3': 10}}))
    projections = NS(items=[NS(item_ref=q, bank_question_id=2 if q=='Q1' else None, tags={'knowledge_point': [key]}, assessment={}) for q in ('Q1','Q2','Q3')])
    service._tag_projections = lambda ids: {sid: projections for sid in ids}
    service._error_cause_index = lambda ids: {(7,i,'Q1'): [{'category':'计算与化简'}, {'category':'计算与化简'}] for i in (1,2,3)} | {(7,1,'Q2'): [{'category':'审题与条件'}]}
    monkeypatch.setattr(contract, 'iter_effective_rubric_item_refs', lambda _: [(q,q,{'question_type':'choice'}, {}) for q in ('Q1','Q2','Q3')])
    result = service.assembly_exam_questions(class_ids=['合成9班','合成10班'], volume_id=VOLUME['id'])
    exam = next(e for e in result['exams'] if e['session_id']==7)
    q = exam['questions'][0]
    assert q['class_rate'] == .4
    assert [r['student_count'] for r in q['class_rates']] == [1,2]
    assert next(c['count'] for c in q['cause_category_counts'] if c['category']=='计算与化简') == 3
    assert q['cause_unclassified_count'] == 0
    assert exam['questions'][1]['bank_question_id'] is None
    assert exam['questions'][1]['student_count'] == 2
    # One classified and one unclassified lost student still expose the counts.
    assert next(c['count'] for c in exam['questions'][1]['cause_category_counts'] if c['category']=='审题与条件') == 1
    assert exam['questions'][1]['cause_unclassified_count'] == 1
    # With zero classified lost students the question stays unorganized.
    assert exam['questions'][2]['cause_category_counts'] is None
    assert exam['questions'][2]['cause_unclassified_count'] == 1
    single = next(e for e in result['exams'] if e['session_id']==8)
    assert single['class_ids'] == ['合成10班']
    assert single['questions'][0]['class_rate'] == .8


def test_api_returns_full_balanced_pool_without_creating_or_replacing_a_paper(
    client_and_source,
    monkeypatch,
):
    client, _, calls, workspace = client_and_source
    before = workspace.draft_path.read_bytes()
    response = client.post(
        "/api/question-assembly/assistant/candidates", json=request()
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["student_count"] == 5
    assert result["evidence_student_count"] == result["exam_student_count"] == 4
    assert result["exam_score_rate"] == 0.6
    first = result["weaknesses"][0]["knowledge_key"]
    assert result["selected_target_keys"] == [
        first
    ]  # Single-select default focuses the weakest point.
    expected_ids = set(range(2, 28)) if first == SKILLS[0] else set(range(28, 33))
    assert result["candidate_total"] == len(expected_ids)
    ids = [candidate["question_id"] for candidate in result["candidates"]]
    assert set(ids) == expected_ids
    assert all(candidate["direct_target_keys"] == [first] for candidate in result["candidates"])
    assert not {1, 33}.intersection(ids)
    assert len(set(ids)) == len(ids)
    both = client.post(
        "/api/question-assembly/assistant/candidates",
        json=request(target_keys=list(SKILLS[:2])),
    ).json()
    both_ids = [candidate["question_id"] for candidate in both["candidates"]]
    assert len(both_ids) == 31
    tagged_second = [
        candidate
        for candidate in both["candidates"]
        if SKILLS[1] in candidate["direct_target_keys"]
    ]
    assert (
        tagged_second and all(c["new_practice_student_count"] >= 1 for c in tagged_second)
    )
    assert not {1, 33}.intersection(both_ids)
    assert workspace.draft_path.read_bytes() == before
    assert workspace.list_records() == []
    assert calls[0] == (
        {"mode": "class", "class_ids": ["合成9班"], "use_historical_fallback": False},
        {"mode": "semester", "session_ids": [], "curriculum_volume_id": VOLUME["id"]},
    )
    assert (
        client.post(
            "/api/question-assembly/assistant/candidates", json=request()
        ).json()
        == result
    )
    preview = client.get(
        "/api/question-assembly/questions",
        params=[("question_ids", qid) for qid in both_ids],
    ).json()
    assert len(preview["items"]) == 31
    assert all(item["rich_content"] is not None for item in preview["items"])
    from question_bank.recommendation.personalized import _SOURCE_SNAPSHOT_CACHE

    page_ids = list(reversed(both_ids[:12]))
    params = [("question_ids", qid) for qid in [*page_ids, page_ids[0], 99999]]
    _SOURCE_SNAPSHOT_CACHE.clear()
    with _capture_initial_source_reads(monkeypatch, {*page_ids, 99999}, legacy=True):
        expected_page = client.get("/api/question-assembly/questions", params=params)
    _SOURCE_SNAPSHOT_CACHE.clear()
    with _capture_initial_source_reads(monkeypatch, {*page_ids, 99999}) as reads:
        actual_page = client.get("/api/question-assembly/questions", params=params)
    assert actual_page.status_code == expected_page.status_code == 200
    assert actual_page.content == expected_page.content
    assert [item["id"] for item in actual_page.json()["items"]] == page_ids
    assert actual_page.json()["missing_question_ids"] == [99999]
    assert all(set(read["returned_ids"]) <= {*page_ids, 99999} for read in reads)
    assert all(item["skill_keys"] and item["rich_content"] for item in actual_page.json()["items"])
    assert workspace.draft_path.read_bytes() == before


def test_filters_empty_evidence_and_changed_scope_never_silently_expand(
    client_and_source,
):
    client, source, _, _ = client_and_source
    for patch in (
        {"question_type": "解答题"},
        {"difficulty_min": 8},
        {"target_keys": []},
    ):
        response = client.post(
            "/api/question-assembly/assistant/candidates", json=request(**patch)
        )
        assert response.status_code == 200, response.text
        assert response.json()["candidates"] == []
    assert (
        client.post(
            "/api/question-assembly/assistant/candidates",
            json=request(target_keys=["unknown"]),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/question-assembly/assistant/candidates",
            json=request(difficulty_min=9, difficulty_max=3),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/question-assembly/assistant/candidates",
            json=request(chapter_id="another-term"),
        ).status_code
        == 422
    )
    source["group_weak_points"] = []
    for student in source["students"]:
        student.update(weak_points=[], score_rate=None)
    empty = client.post(
        "/api/question-assembly/assistant/candidates", json=request()
    ).json()
    assert empty["evidence_student_count"] == 0
    assert empty["weaknesses"]
    assert all(p["mastery"] is None for p in empty["weaknesses"])
    assert all(c["new_practice_student_count"] == 5 for c in empty["candidates"])


def test_teacher_practice_rules_persist_and_rejected_save_keeps_previous_draft(client_and_source):
    client, _, _, workspace = client_and_source
    from question_bank.services.assembly_workspace_service import _payload_revision
    legacy_payload = {**workspace.load_draft().to_payload(), 'practice_rules':True}
    compatible = workspace.normalize_draft(legacy_payload)
    assert compatible.revision == _payload_revision(legacy_payload)
    assert compatible.practice_rules['purpose'] == 'handout'
    current = client.get('/api/question-assembly/draft').json()
    payload = {key: value for key, value in current.items() if key not in {'revision', 'rule_violations'}}
    payload.update(practice_rules=True, basket_ids=[2,28], order_ids=[2,28])
    saved = client.put('/api/question-assembly/draft', json={'expected_revision': current['revision'], 'draft': payload})
    assert saved.status_code == 200, saved.text
    assert client.get('/api/question-assembly/draft').json()['practice_rules']['max_questions_per_skill'] == 1
    before = workspace.draft_path.read_bytes()
    # These are duplicate original question records; the failure must preserve the basket.
    payload.update(practice_rules=False, basket_ids=[1,33], order_ids=[1,33])
    rejected = client.put('/api/question-assembly/draft', json={'expected_revision': saved.json()['revision'], 'draft': payload})
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'assembly_practice_rule'
    assert workspace.draft_path.read_bytes() == before


def test_class_comprehensive_scope_keeps_previous_chapters_and_focused_scope_stays_local(client_and_source):
    client, _, _, _ = client_and_source
    second = VOLUME['chapters'][1]
    comprehensive = client.post('/api/question-assembly/assistant/candidates',
        json=request(chapter_id='', teaching_progress_chapter_id=second['id']))
    assert comprehensive.status_code == 200
    keys = {point['knowledge_key'] for point in comprehensive.json()['weaknesses']}
    assert SKILLS[0] in keys
    assert SK_CHAPTER_TWO in keys
    focused = client.post('/api/question-assembly/assistant/candidates', json=request()).json()
    assert SK_CHAPTER_TWO not in {p['knowledge_key'] for p in focused['weaknesses']}


def test_teacher_cannot_save_second_question_of_same_skill(client_and_source, monkeypatch):
    client, _, _, workspace = client_and_source
    candidates = [{'question_id': qid, 'question_type': '选择题', 'difficulty': 3,
                   'stable_keys': ['sk_test_limit'], 'stable_names': {'sk_test_limit': '合成技能'}}
                  for qid in (2, 3, 4)]
    monkeypatch.setattr(PersonalizedRecommendationModule, '_source_snapshot',
                        lambda self, **kwargs: (candidates, (), 'synthetic'))
    current = client.get('/api/question-assembly/draft').json()
    payload = {key: value for key, value in current.items() if key not in {'revision', 'rule_violations'}}
    payload.update(practice_rules=True, basket_ids=[2], order_ids=[2])
    saved = client.put('/api/question-assembly/draft', json={'expected_revision': current['revision'], 'draft': payload})
    assert saved.status_code == 200
    before = workspace.draft_path.read_bytes()
    payload.update(basket_ids=[2, 3], order_ids=[2, 3])
    rejected = client.put('/api/question-assembly/draft', json={'expected_revision': saved.json()['revision'], 'draft': payload})
    assert rejected.status_code == 422
    assert '同一技能最多选 1 道' in rejected.json()['error']['message']
    assert '合成技能' in rejected.json()['error']['message']
    assert workspace.draft_path.read_bytes() == before
    assert client.get('/api/question-assembly/draft').json()['order_ids'] == [2]


def test_lower_rules_can_save_and_block_export_without_removing_questions(client_and_source, monkeypatch):
    client, _, _, workspace = client_and_source
    candidates = [{'question_id': q, 'question_type': '解答题', 'difficulty': 5,
        'stable_keys': [f'sk_{q}'], 'question_text': f'合成不同题目{q}'} for q in (2, 3)]
    monkeypatch.setattr(PersonalizedRecommendationModule, '_source_snapshot', lambda self, **_: (candidates, (), 'test'))
    current = client.get('/api/question-assembly/draft').json()
    payload = {k:v for k,v in current.items() if k not in {'revision','rule_violations'}}
    payload.update(basket_ids=[2,3], order_ids=[2,3], practice_rules=True)
    first = client.put('/api/question-assembly/draft', json={'expected_revision':current['revision'],'draft':payload}).json()
    payload['practice_rules'] = {**first['practice_rules'], 'max_written_questions':1}
    saved = client.put('/api/question-assembly/draft', json={'expected_revision':first['revision'],'draft':payload})
    assert saved.status_code == 200
    assert saved.json()['order_ids'] == [2,3]
    assert saved.json()['rule_violations'] == [{'question_id':3,'code':'written','message':'学情卷最多选 1 道解答题，请先移除一道再添加。'}]
    from backend.api.dependencies import get_job_manager
    client.app.dependency_overrides[get_job_manager] = lambda: None
    rejected = client.post('/api/question-assembly/export', json={'draft_revision':saved.json()['revision'],'format':'docx'})
    assert rejected.status_code == 422
    assert workspace.load_draft().order_ids == (2,3)


@pytest.mark.parametrize('change_source', [False, True])
def test_quick_draft_fills_remaining_slots_reports_rejections_and_never_saves(client_and_source, monkeypatch, change_source):
    import sqlite3
    from types import SimpleNamespace as NS
    from backend.api.routers import assembly as router
    client, _, _, workspace = client_and_source
    descriptors = [{'question_id':q, 'question_type':'解答题' if q==2 else '选择题',
        'difficulty':9 if q==3 else 3, 'stable_keys':[f'sk_{q}'], 'question_text':f'不同合成题{q}',
        **({'duplicate_identity':'duplicate'} if q in (4,5) else {})} for q in (32,2,3,4,5,6)]
    source_reads = []
    def source_snapshot(self, **kw):
        if change_source and not source_reads:
            with sqlite3.connect(self.db_path) as writer:
                writer.execute("UPDATE questions SET question_text='TEST-updated-during-quick-draft' WHERE id=32")
        source_reads.extend(kw['question_ids'])
        return ([q for q in descriptors if q['question_id'] in kw['question_ids']], (), 'test')
    monkeypatch.setattr(PersonalizedRecommendationModule, '_source_snapshot', source_snapshot)
    needs = [(32,[32]),(2,[2]),(3,[3,4]),(5,[5]),(6,[6])]
    service = NS(assembly_exam_questions=lambda **_: {'exams':[{'session_id':7,'questions':[
        {'key':str(q),'class_rate':.2,'skill_keys':[f'sk_{q}']} for q,_ in needs]}]})
    client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: service
    pools = {f'sk_{key}': ids for key,ids in needs}
    captured = []
    monkeypatch.setattr(router, 'compute_assistant_candidates', lambda **kw: (captured.append(kw), {'candidates':[
        {'question_id':q,'suitable_student_count':10-q,'remediation_student_count':2} for q in pools[kw['target_keys'][0]]]})[1])
    before = workspace.draft_path.read_bytes()
    rules = {'purpose':'handout','question_count':3,'difficulty_max':8,'max_written_questions':0,'max_questions_per_skill':1,'recent_activity_count':0}
    response = client.post('/api/question-assembly/assistant/quick-draft', json=request(session_ids=[7],question_ids=[32],rules=rules))
    if change_source:
        assert response.status_code == 409, response.text
        assert response.json()['error']['code'] == 'assembly_source_changed'
        assert workspace.draft_path.read_bytes() == before
        return
    assert response.status_code == 200, response.text
    assert response.json()['question_ids'] == [4,6]
    assert response.json()['skipped'] == {'skill':1,'written':1,'difficulty':0,'similar':1,'unavailable':0}
    # Quick draft fills pools without registering prewarm recents.
    assert captured and all(kw['record'] is False for kw in captured)
    assert workspace.draft_path.read_bytes() == before
    assert workspace.list_records() == []
    assert source_reads == [32, 2, 3, 4, 5, 6]


@pytest.mark.parametrize('client_and_source', [8], indirect=True)
def test_surface_unfolding_only_lists_that_skill_and_preserves_training_supplements(client_and_source):
    from question_bank.recommendation.personalized import PersonalizedRecommendationConfig

    client, source, _, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    unfolding = next(node for node in module.current_knowledge.nodes if '展开曲面求最短路' in node.display_name)
    parents = {r.source_key: r.target_key for r in module.current_knowledge.relations if r.relation_type == 'parent'}
    other = next(node for node in module.current_knowledge.nodes if node.stable_key.startswith('sk_')
                 and node.stable_key != unfolding.stable_key and parents.get(node.stable_key) == parents[unfolding.stable_key])
    with connect(module.db_path) as conn:
        for qid, node, text in ((34, unfolding, '圆柱侧面展开后求两点之间的最短路程。'),
                                (35, other, '直角三角形中根据两边计算第三边。')):
            conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(?,1,?,'选择题',?,'合成解析','3')", (qid, str(qid), text))
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'knowledge_point',?)", (qid, node.display_name))
    _approve_synthetic_criteria(module.db_path, module.data_root, (34, 35))
    chosen = request(target_keys=[unfolding.stable_key])
    response = client.post('/api/question-assembly/assistant/candidates', json=chosen)
    assert response.status_code == 200, response.text
    assert [c['question_id'] for c in response.json()['candidates']] == [34]
    assert response.json()['candidates'][0]['direct_target_keys'] == [unfolding.stable_key]
    # Automatic personal/group papers retain their existing supplemental pool.
    config = PersonalizedRecommendationConfig(scope_keys=(CHAPTER['knowledge_id'],),
        curriculum_volume_id=VOLUME['id'], target_keys=(unfolding.stable_key,), paper_mode='shared')
    evaluated = module.evaluate_candidates(diagnosis=source, config=config, excluded={1, 33}, graded_activities=[])
    assert any(e['candidate']['question_id'] == 35 and e['selection_kind'] == 'supplement'
               for pool in evaluated['pools'].values() for e in pool)
    with connect(module.db_path) as conn:
        conn.execute('UPDATE questions SET is_deleted=1 WHERE id=34')
    empty = client.post('/api/question-assembly/assistant/candidates', json=chosen)
    assert empty.status_code == 200
    assert empty.json()['candidate_total'] == 0
    assert empty.json()['candidates'] == []


def test_switching_targets_reuses_source_read_but_reloads_changed_constraints_and_data(client_and_source, monkeypatch):
    from question_bank.recommendation.personalized import _SOURCE_SNAPSHOT_CACHE

    client, _, _, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    original = module._source_snapshot_uncached
    reads = []
    def counted(**kwargs):
        reads.append(1)
        return original(**kwargs)
    monkeypatch.setattr(module, '_source_snapshot_uncached', counted)
    _SOURCE_SNAPSHOT_CACHE.clear()
    first = client.post('/api/question-assembly/assistant/candidates', json=request(target_keys=[SKILLS[0]]))
    second = client.post('/api/question-assembly/assistant/candidates', json=request(target_keys=[SKILLS[1]]))
    assert first.status_code == second.status_code == 200
    assert len(reads) == 1
    assert {c['question_id'] for c in first.json()['candidates']} == set(range(2, 28))
    assert {c['question_id'] for c in second.json()['candidates']} == set(range(28, 33))
    typed = client.post('/api/question-assembly/assistant/candidates', json=request(target_keys=[SKILLS[1]], question_type='选择题'))
    assert typed.json() == second.json()
    assert len(reads) == 1
    narrower = client.post('/api/question-assembly/assistant/candidates', json=request(target_keys=[SKILLS[0]], difficulty_max=2))
    assert narrower.status_code == 200
    assert narrower.json()['candidates'] == []
    assert len(reads) == 2
    with connect(module.db_path) as conn:
        conn.execute('UPDATE questions SET is_deleted=1 WHERE id=2')
    changed = client.post('/api/question-assembly/assistant/candidates', json=request(target_keys=[SKILLS[0]]))
    assert changed.status_code == 200
    assert {c['question_id'] for c in changed.json()['candidates']} == set(range(3, 28))
    assert len(reads) == 3
    later_chapter = VOLUME['chapters'][1]
    expanded = client.post('/api/question-assembly/assistant/candidates', json=request(
        chapter_id='', teaching_progress_chapter_id=later_chapter['id'], target_keys=[SKILLS[0]]))
    assert expanded.status_code == 200
    assert len(reads) == 4
    assert SK_CHAPTER_TWO in {
        point['knowledge_key'] for point in expanded.json()['weaknesses']}
    _SOURCE_SNAPSHOT_CACHE.clear()
    rechecked = client.post('/api/question-assembly/assistant/candidates', json=request(
        chapter_id='', teaching_progress_chapter_id=later_chapter['id'], target_keys=[SKILLS[0]]))
    assert rechecked.json() == expanded.json()


def test_exam_questions_endpoint_caches_until_state_changes(client_and_source, tmp_path):
    from integration.diagnosis_profile_service import _dir_generation

    client, _, _, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    state_dir = tmp_path / 'reports' / '.class_analysis'
    calls = []

    class Service:
        data_root = tmp_path
        question_bank_db_path = module.db_path

        def tag_profile_cache_key(self, *, scope, exam_scope):
            return ('\x00'.join(_dir_generation(state_dir)), 'test')

        def assembly_exam_questions(self, *, class_ids, volume_id):
            calls.append(class_ids)
            return {'student_count': 0, 'exams': []}

    service = Service()
    client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: service
    body = {'class_ids': ['合成9班'], 'curriculum_volume_id': VOLUME['id']}
    assert client.post('/api/question-assembly/assistant/exam-questions', json=body).status_code == 200
    assert client.post('/api/question-assembly/assistant/exam-questions', json=body).status_code == 200
    assert calls == [['合成9班']]
    state_dir.mkdir(parents=True)
    (state_dir / '7.json').write_text('{}', encoding='utf-8')
    assert client.post('/api/question-assembly/assistant/exam-questions', json=body).status_code == 200
    assert calls == [['合成9班'], ['合成9班']]


def test_candidates_cache_skips_diagnosis_until_grading_data_changes(client_and_source, tmp_path):
    import sqlite3 as sqlite_driver
    from integration.diagnosis_profile_service import _path_generation

    client, source, calls, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    grading = tmp_path / 'grading-cache-probe.db'
    connection = sqlite_driver.connect(grading)
    connection.execute('CREATE TABLE probe(value)')
    connection.commit()
    connection.close()

    class KeyedProfiles:
        def build_profiles(self, *, scope, exam_scope):
            calls.append((scope, exam_scope))
            return deepcopy(source)

        def graded_activities(self, student_ids):
            return []

        def tag_profile_cache_key(self, *, scope, exam_scope):
            return ('\x00'.join((*_path_generation(grading), *_path_generation(module.db_path))), 'test')

    keyed = KeyedProfiles()
    client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: keyed
    first = client.post('/api/question-assembly/assistant/candidates', json=request())
    assert first.status_code == 200, first.text
    second = client.post('/api/question-assembly/assistant/candidates', json=request())
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(calls) == 1
    connection = sqlite_driver.connect(grading)
    connection.execute('INSERT INTO probe VALUES (1)')
    connection.commit()
    connection.close()
    third = client.post('/api/question-assembly/assistant/candidates', json=request())
    assert third.status_code == 200
    assert len(calls) == 2


def test_persistent_results_survive_restart_and_recompute_on_changes(client_and_source, tmp_path):
    import sqlite3 as sqlite_driver
    from integration import diagnosis_profile_service as profiles
    from integration.data_generation import reset_commit_generations
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from backend.api.routers import assembly as router

    client, source, calls, _ = client_and_source
    module = client.app.dependency_overrides[get_personalized_recommendation_module]()
    grading = tmp_path / 'grading-persist-probe.db'
    with sqlite_driver.connect(grading) as connection:
        connection.execute('CREATE TABLE probe(value)')

    def storage():
        # The persistent entry store on real files, without repository setup.
        real = DiagnosisProfileService.__new__(DiagnosisProfileService)
        real.grading_db_path = grading
        real.question_bank_db_path = module.db_path
        real.question_bank_connection = None
        real.data_root = tmp_path
        real.cache_identity = None
        real.persist_snapshots = False
        real.latest_aggregated_mastery = {}
        real.db = None
        return real

    exam_calls = []

    class Keyed:
        def __init__(self, store):
            self._store = store
            self.data_root = tmp_path

        def tag_profile_cache_key(self, *, scope, exam_scope):
            return self._store.tag_profile_cache_key(scope=scope, exam_scope=exam_scope)

        def build_profiles(self, *, scope, exam_scope):
            calls.append((scope, exam_scope))
            return deepcopy(source)

        def graded_activities(self, student_ids):
            return []

        def assembly_exam_questions(self, *, class_ids, volume_id):
            exam_calls.append(class_ids)
            return {'student_count': 0, 'exams': []}

        def read_persistent_result(self, cache_key):
            return self._store.read_persistent_result(cache_key)

        def save_persistent_result(self, cache_key, entry):
            return self._store.save_persistent_result(cache_key, entry)

    def restart(service):
        # Simulated process restart: every memory cache and process-local
        # generation counter is gone; only the entry files remain.
        router._ASSISTANT_CACHE.clear()
        router._EXAM_QUESTIONS_CACHE.clear()
        with profiles._TAG_PROFILE_CACHE_LOCK:
            profiles._TAG_PROFILE_CACHE.clear()
        reset_commit_generations()
        client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: service

    client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: Keyed(storage())
    evidence_body = {'class_ids': ['合成9班'], 'curriculum_volume_id': VOLUME['id']}
    assert client.post('/api/question-assembly/assistant/exam-questions', json=evidence_body).status_code == 200
    first = client.post('/api/question-assembly/assistant/candidates', json=request()).json()
    assert exam_calls == [['合成9班']] and len(calls) == 1

    restored = Keyed(storage())
    restored.build_profiles = lambda **kwargs: pytest.fail('recomputed profiles')
    restored.assembly_exam_questions = lambda **kwargs: pytest.fail('recomputed exam evidence')
    restart(restored)
    assert client.post('/api/question-assembly/assistant/exam-questions', json=evidence_body).json() == {
        'student_count': 0, 'exams': []}
    assert client.post('/api/question-assembly/assistant/candidates', json=request()).json() == first
    assert exam_calls == [['合成9班']] and len(calls) == 1

    # A new uploaded config file invalidates exam evidence and candidates:
    # rubric/evidence snapshots feed the diagnosis projections too.
    uploaded = tmp_path / 'config' / 'uploaded'
    uploaded.mkdir(parents=True)
    (uploaded / 'TEST-new-input.json').write_text('{}', encoding='utf-8')
    restored = Keyed(storage())
    restart(restored)
    assert client.post('/api/question-assembly/assistant/exam-questions', json=evidence_body).status_code == 200
    assert exam_calls == [['合成9班'], ['合成9班']]
    assert client.post('/api/question-assembly/assistant/candidates', json=request()).json() == first
    assert len(calls) == 2

    # A committed grading-side change invalidates the persisted signature.
    with sqlite_driver.connect(grading) as connection:
        connection.execute('INSERT INTO probe VALUES (1)')
    restored = Keyed(storage())
    restart(restored)
    assert client.post('/api/question-assembly/assistant/candidates', json=request()).status_code == 200
    assert len(calls) == 3

    # Corrupt entry bytes fall back to recomputation instead of failing.
    for entry_file in (tmp_path / 'reports' / '.training_diagnosis' / 'entries').glob('*.entry'):
        entry_file.write_bytes(b'TEST-corrupt-entry')
    restored = Keyed(storage())
    restart(restored)
    assert client.post('/api/question-assembly/assistant/candidates', json=request()).status_code == 200
    assert len(calls) == 4


def test_exam_evidence_marks_skills_anchored_in_another_volume(tmp_path, monkeypatch):
    # Exam questions can link skills anchored in another volume; the flag lets
    # the panel keep them out of the volume-scoped shortlist targets.
    from types import SimpleNamespace as NS
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from backend.config_generation import contract
    db_path = tmp_path / 'bank.db'
    initialize_database(db_path)
    other = next(v for v in load_curriculum_catalog()['volumes'] if v['id'] != VOLUME['id'])
    other_point = other['chapters'][0]['sections'][0]['knowledge_points'][0]
    sk_other = 'sk_other_volume_skill'
    _install_release_with_skills(db_path, revision=4,
        skill_parents={SKILLS[0]: SKILL_PARENTS[SKILLS[0]], sk_other: other_point['id']})
    service = DiagnosisProfileService.__new__(DiagnosisProfileService)
    service.question_bank_db_path = db_path
    service.db = NS(
        students=NS(list_students=lambda: [{'id': 1, 'class_name': '合成9班'}]),
        sessions=NS(list_grading_sessions=lambda: [
            {'id': 7, 'session_name': 'TEST-考试', 'curriculum_volume_id': VOLUME['id'], 'created_at': '2026-10-02'}]),
        results=NS(
            get_active_assessment_rows=lambda **_: [
                {'session_id': 7, 'student_id': 1, 'question_id': 'Q1', 'score_awarded': 2}],
            _load_session_rubric=lambda sid: {},
            _load_rubric_maps_for_session=lambda sid: {'score': {'Q1': 10}}))
    projections = NS(items=[NS(item_ref='Q1', bank_question_id=None,
        tags={'knowledge_point': [SKILLS[0], sk_other]}, assessment={})])
    service._tag_projections = lambda ids: {sid: projections for sid in ids}
    service._error_cause_index = lambda ids: {}
    monkeypatch.setattr(contract, 'iter_effective_rubric_item_refs',
        lambda _: [('Q1', 'Q1', {'question_type': 'choice'}, {})])
    result = service.assembly_exam_questions(class_ids=['合成9班'], volume_id=VOLUME['id'])
    question = result['exams'][0]['questions'][0]
    assert question['skill_keys'] == [SKILLS[0], sk_other]
    assert {s['key']: s['in_volume'] for s in question['skills']} == {SKILLS[0]: True, sk_other: False}


def test_quick_draft_counts_out_of_volume_and_rejected_targets_as_unavailable(client_and_source, monkeypatch):
    from types import SimpleNamespace as NS
    from backend.api.routers import assembly as router
    client, _, _, _ = client_and_source
    questions = [
        {'key': 'no', 'class_rate': .1, 'skill_keys': [], 'skills': []},
        {'key': 'out', 'class_rate': .2, 'skill_keys': ['sk_old'],
         'skills': [{'key': 'sk_old', 'label': '往届技能', 'in_volume': False}]},
        {'key': 'boom', 'class_rate': .3, 'skill_keys': ['sk_boom'],
         'skills': [{'key': 'sk_boom', 'label': '异常技能', 'in_volume': True}]},
        {'key': 'mixed', 'class_rate': .35, 'skill_keys': ['sk_old2', 'sk_in2'],
         'skills': [{'key': 'sk_old2', 'label': '往届技能二', 'in_volume': False},
                    {'key': 'sk_in2', 'label': '本册技能二', 'in_volume': True}]},
        {'key': 'in', 'class_rate': .4, 'skill_keys': ['sk_in'],
         'skills': [{'key': 'sk_in', 'label': '本册技能', 'in_volume': True}]},
    ]
    service = NS(assembly_exam_questions=lambda **_: {'exams': [{'session_id': 7, 'questions': questions}]})
    client.app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: service
    captured = []

    def fake_candidates(**kw):
        captured.append(kw)
        if kw['target_keys'] == ['sk_boom']:
            raise ValueError('target outside the volume')
        if kw['target_keys'] == ['sk_in2']:
            return {'candidates': [{'question_id': 2, 'suitable_student_count': 5, 'remediation_student_count': 1}]}
        return {'candidates': []}

    monkeypatch.setattr(router, 'compute_assistant_candidates', fake_candidates)
    descriptors = [{'question_id': 2, 'question_type': '选择题', 'difficulty': 3,
                    'stable_keys': ['sk_in2'], 'question_text': '合成题2'}]
    monkeypatch.setattr(PersonalizedRecommendationModule, '_source_snapshot',
        lambda self, **kw: ([q for q in descriptors if q['question_id'] in kw['question_ids']], (), 'test'))
    rules = {'purpose': 'handout', 'question_count': 3, 'difficulty_max': 8,
             'max_written_questions': 0, 'max_questions_per_skill': 1, 'recent_activity_count': 0}
    response = client.post('/api/question-assembly/assistant/quick-draft', json=request(session_ids=[7], rules=rules))
    assert response.status_code == 200, response.text
    # The mixed question fills from its in-volume subset; the rest are unavailable.
    assert response.json()['question_ids'] == [2]
    assert response.json()['additions'][0]['source']['key'] == 'mixed'
    assert response.json()['skipped'] == {'skill': 0, 'written': 0, 'difficulty': 0, 'similar': 0, 'unavailable': 4}
    assert [kw['target_keys'] for kw in captured] == [['sk_boom'], ['sk_in2'], ['sk_in']]
    assert all(kw['record'] is False for kw in captured)


def test_class_weaknesses_add_type_targets_only_on_type_releases(tmp_path):
    from types import SimpleNamespace
    from question_bank.services.assembly_assistant import class_weaknesses

    class _Resolver:
        """Hashable stand-in for CurrentKnowledgeResolver (target_index is cached)."""
        def __init__(self, release_id, nodes, relations):
            self.release_id = release_id
            self.nodes = nodes
            self.relations = relations

        def node(self, key):
            if any(getattr(node, "stable_key", None) == key for node in self.nodes):
                return SimpleNamespace(display_name=f"题型·{key}")
            return None

    TYPE = "kp_bnu24_math_g8_upper_1_1_t05"
    SECTION = CHAPTER["sections"][0]["knowledge_id"]
    resolver = _Resolver(
        "kgr_type_test",
        (SimpleNamespace(stable_key=TYPE),),
        (SimpleNamespace(relation_type="parent", source_key=TYPE,
                         target_key=SECTION),),
    )
    profile = diagnosis()
    typed_point = {
        "knowledge_key": TYPE, "knowledge_point": "题型·合成题型",
        "mastery": 0.2, "evidence_count": 2, "score_sum": 1, "full_score_sum": 5,
        "tier": "weak",
    }
    profile["students"][0]["weak_points"].append(dict(typed_point))
    profile["group_weak_points"].append({**typed_point, "mastery": 0.3})
    points = class_weaknesses(profile, volume_id=VOLUME["id"], chapter_id=CHAPTER["id"],
                              resolver=resolver)
    typed = next(p for p in points if p["knowledge_key"] == TYPE)
    assert typed["weak_student_count"] == 1 and typed["weak_tier_student_count"] == 1
    assert typed["exam_score_rate"] == pytest.approx(0.2)
    # A release without type nodes keeps the skill-only filter unchanged.
    legacy = _Resolver("kgr_no_type_test", (), ())
    legacy_points = class_weaknesses(profile, volume_id=VOLUME["id"],
                                     chapter_id=CHAPTER["id"], resolver=legacy)
    assert TYPE not in {p["knowledge_key"] for p in legacy_points}
    assert {p["knowledge_key"] for p in legacy_points} >= set(SKILLS[:2])

    # The same mixed release keeps another volume's skill targets and label.
    from question_bank.services.assembly_assistant import shortlist_candidates

    other_volume = load_curriculum_catalog()["volumes"][0]
    other_chapter = other_volume["chapters"][0]
    other_section = other_chapter["sections"][0]["knowledge_id"]
    skill = "sk_test_unconverted_assembly"
    mixed = _Resolver(
        "kgr_mixed_assembly_test",
        (SimpleNamespace(stable_key=TYPE), SimpleNamespace(stable_key=skill)),
        (*resolver.relations, SimpleNamespace(relation_type="parent",
                                             source_key=skill, target_key=other_section)),
    )
    point = {**typed_point, "knowledge_key": skill, "knowledge_point": "合成技能"}
    mixed_profile = {"students": [{"student_id": "TEST-学生", "weak_points": [point]}],
                     "group_weak_points": [point]}
    other_points = class_weaknesses(mixed_profile, volume_id=other_volume["id"],
                                  chapter_id=other_chapter["id"], resolver=mixed)
    assert [p["knowledge_key"] for p in other_points] == [skill]
    reader = SimpleNamespace(db_path=tmp_path / "TEST-unused.db", data_root=tmp_path)
    output = shortlist_candidates(
        diagnosis={**mixed_profile, "students": []}, read_service=reader,
        recommendations=SimpleNamespace(current_knowledge=mixed),
        volume_id=other_volume["id"], chapter_id=other_chapter["id"],
        target_keys=[skill], question_type="", difficulty_min=0, difficulty_max=8,
        excluded_question_ids=set(),
    )
    assert output["target_kind"] == "skill"
    assert output["selected_target_keys"] == [skill]


def test_class_weaknesses_in_knowledge_fallback_keep_point_targets():
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
    from question_bank.services.assembly_assistant import class_weaknesses
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    release = load_release_for_taxonomy_revision(11)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    volume = curriculum_volume(volume_id="bnu24-math-g8-lower")
    chapter = volume["chapters"][1]
    key = chapter["sections"][0]["knowledge_points"][0]["id"]
    point = {"knowledge_key": key, "knowledge_point": key, "mastery": .2, "tier": "weak",
             "evidence_count": 1, "score_sum": 1, "full_score_sum": 5}
    profile = {"students": [{"student_id": "TEST-student", "weak_points": [point]}],
               "group_weak_points": [point]}
    rows = class_weaknesses(profile, volume_id=volume["id"], chapter_id=chapter["id"], resolver=resolver)
    target = next(row for row in rows if row["knowledge_key"] == key)
    assert target["target_kind"] == "knowledge"
    assert target["weak_tier_student_count"] == 1
    assert target["evidence_student_count"] == 1
    assert target["exam_score_rate"] == .2
    assert all(row["target_kind"] == "knowledge" and not row["knowledge_key"].startswith("sk_") for row in rows)
