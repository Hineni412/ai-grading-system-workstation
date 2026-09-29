"""Class shortlist expectations, using synthetic evidence and a real question bank."""

from copy import deepcopy
from question_bank.recommendation.personalized import PersonalizedRecommendationModule
from tests.phase4.test_personalized_recommendation import _approve_synthetic_criteria

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
from tests.current_knowledge_support import install_current_knowledge

VOLUME = load_curriculum_catalog()["volumes"][2]
CHAPTER = VOLUME["chapters"][0]
POINTS = CHAPTER["sections"][0]["knowledge_points"][:3]


def diagnosis():
    students = []
    for index in range(4):
        points = [
            {
                "knowledge_key": point["id"],
                "knowledge_point": point["display_name"],
                "mastery": (0.7 if target == 0 else 0.2)
                if index < (3 if target == 0 else 1)
                else 0.9,
                "evidence_count": 2,
                "score_sum": 3,
                "full_score_sum": 5,
            }
            for target, point in enumerate(POINTS[:2])
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
        "group_weak_points": [
            {
                "knowledge_key": p["id"],
                "knowledge_point": p["display_name"],
                "mastery": m,
                "evidence_count": 8,
            }
            for p, m in zip(POINTS[:2], (0.75, 0.725))
        ],
    }


@pytest.fixture
def client_and_source(tmp_path):
    db_path = tmp_path / "bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path, taxonomy_revision=4)
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
            point = POINTS[0 if qid <= 27 else 1]
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
                (qid, point["display_name"]),
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


def test_api_returns_full_balanced_pool_without_creating_or_replacing_a_paper(
    client_and_source,
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
    assert result["candidate_total"] == 31  # Same-section new exercises are also eligible.
    ids = [candidate["question_id"] for candidate in result["candidates"]]
    assert not {1, 33}.intersection(ids)
    assert len(set(ids)) == len(ids)
    both = client.post(
        "/api/question-assembly/assistant/candidates",
        json=request(target_keys=[point["id"] for point in POINTS[:2]]),
    ).json()
    both_ids = [candidate["question_id"] for candidate in both["candidates"]]
    assert len(both_ids) == 31
    tagged_second = [
        candidate
        for candidate in both["candidates"]
        if POINTS[1]["id"] in candidate["direct_target_keys"]
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
    current = client.get('/api/question-assembly/draft').json()
    payload = {key: value for key, value in current.items() if key != 'revision'}
    payload.update(practice_rules=True, basket_ids=[2,3], order_ids=[2,3])
    saved = client.put('/api/question-assembly/draft', json={'expected_revision': current['revision'], 'draft': payload})
    assert saved.status_code == 200, saved.text
    assert client.get('/api/question-assembly/draft').json()['practice_rules'] is True
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
    assert POINTS[0]['id'] in keys
    assert second['sections'][0]['knowledge_points'][0]['id'] in keys
    focused = client.post('/api/question-assembly/assistant/candidates', json=request()).json()
    assert second['sections'][0]['knowledge_points'][0]['id'] not in {p['knowledge_key'] for p in focused['weaknesses']}


def test_teacher_cannot_save_second_question_of_same_skill(client_and_source, monkeypatch):
    client, _, _, workspace = client_and_source
    candidates = [{'question_id': qid, 'question_type': '选择题', 'difficulty': 3,
                   'stable_keys': ['sk_test_limit'], 'stable_names': {'sk_test_limit': '合成技能'}}
                  for qid in (2, 3, 4)]
    monkeypatch.setattr(PersonalizedRecommendationModule, '_source_snapshot',
                        lambda self, **kwargs: (candidates, (), 'synthetic'))
    current = client.get('/api/question-assembly/draft').json()
    payload = {key: value for key, value in current.items() if key != 'revision'}
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
