"""Class shortlist expectations, using synthetic evidence and a real question bank."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_assembly_workspace_service, get_personalized_recommendation_module, get_question_bank_read_service, get_request_diagnosis_profile_service
from question_bank.database.schema import connect, initialize_database
from question_bank.services.assembly_assistant import class_weaknesses
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
        points = [{
            "knowledge_key": point["id"], "knowledge_point": point["display_name"],
            "mastery": (.7 if target == 0 else .2) if index < (3 if target == 0 else 1) else .9,
            "evidence_count": 2, "score_sum": 3, "full_score_sum": 5,
        } for target, point in enumerate(POINTS[:2])]
        students.append({"student_id": str(index), "class_id": "合成9班", "score_rate": .6, "weak_points": points})
    students.append({"student_id": "no-evidence", "class_id": "合成9班", "score_rate": None, "weak_points": []})
    return {"students": students, "exam_scope": {"session_ids": [7, 8]}, "group_weak_points": [
        {"knowledge_key": p["id"], "knowledge_point": p["display_name"], "mastery": m, "evidence_count": 8}
        for p, m in zip(POINTS[:2], (.75, .725))
    ]}


def test_weakness_priority_counts_people_without_recalculating_graph_mastery():
    result = class_weaknesses(diagnosis(), volume_id=VOLUME["id"], chapter_id=CHAPTER["id"])
    assert [point["weak_student_count"] for point in result] == [3, 1]
    assert [point["mastery"] for point in result] == [.75, .725]
    assert all(point["evidence_student_count"] == 4 for point in result)
    assert all(point["exam_score_rate"] == .6 for point in result)
    source = diagnosis()
    for student in source["students"]:
        for point in student["weak_points"]:
            point["mastery"] = None
    assert class_weaknesses(source, volume_id=VOLUME["id"], chapter_id="") == []


@pytest.fixture
def client_and_source(tmp_path):
    db_path = tmp_path / "bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path, taxonomy_revision=4)
    with connect(db_path) as conn:
        conn.execute("INSERT INTO papers(id,title,grade,semester,textbook_version,import_status) VALUES(1,'合成候选题库',?,?,?,'success')", (VOLUME["grade"], VOLUME["semester"], VOLUME["textbook_version"]))
        stems = ["计算含根式算式", "判断三角形形状", "描述坐标点距离", "建立方程并检验解",
                 "比较两组数据集中趋势", "在网格中作几何图形", "归纳运算规律求值"]
        contexts = ["结合校园测量情境", "依据温度变化记录", "在直角坐标网格中", "按运动会成绩表", "围绕购物折扣问题"]
        for qid in range(1, 34):
            point = POINTS[0 if qid <= 27 else 1]
            # qid%7 and qid%5 give every question a unique stem+context pair.
            text = f"{stems[qid % len(stems)]}，{contexts[qid % len(contexts)]}，写出完整解答过程。"
            if qid == 33:
                text = f"{stems[1 % len(stems)]}，{contexts[1 % len(contexts)]}，写出完整解答过程。"  # Duplicate of excluded exam original.
            conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(?,1,?,'选择题',?,'合成解析','5')", (qid, str(qid), text))
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'knowledge_point',?)", (qid, point["display_name"]))
    reader = QuestionBankReadService(db_path, data_root=tmp_path)
    workspace = AssemblyWorkspaceService(tmp_path)
    original = workspace.load_draft()
    workspace.save_draft(expected_revision=original.revision, draft={**original.to_payload(), "basket_ids": [32], "order_ids": [32], "title": "已有教师选题"})
    source = diagnosis()
    calls = []

    class Profiles:
        def build_profiles(self, *, scope, exam_scope):
            calls.append((scope, exam_scope))
            return deepcopy(source)

    app = create_app()
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: Profiles()
    app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    app.dependency_overrides[get_assembly_workspace_service] = lambda: workspace
    app.dependency_overrides[get_personalized_recommendation_module] = lambda: SimpleNamespace(current_exam_question_ids=lambda value: {1})
    with TestClient(app) as client:
        yield client, source, calls, workspace


def request(**patch):
    return {"class_id": "合成9班", "curriculum_volume_id": VOLUME["id"], "chapter_id": CHAPTER["id"], **patch}


def test_api_returns_full_balanced_pool_without_creating_or_replacing_a_paper(client_and_source):
    client, _, calls, workspace = client_and_source
    before = workspace.draft_path.read_bytes()
    response = client.post("/api/question-assembly/assistant/candidates", json=request())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["student_count"] == 5
    assert result["evidence_student_count"] == result["exam_student_count"] == 4
    assert result["exam_score_rate"] == .6
    first = result["weaknesses"][0]["knowledge_key"]
    assert result["selected_target_keys"] == [first]  # Single-select default focuses the weakest point.
    assert result["candidate_total"] == 26  # original and its duplicate both excluded
    ids = [candidate["question_id"] for candidate in result["candidates"]]
    assert not {1, 33}.intersection(ids)
    assert len(set(ids)) == len(ids)
    both = client.post("/api/question-assembly/assistant/candidates",
                       json=request(target_keys=[point["id"] for point in POINTS[:2]])).json()
    both_ids = [candidate["question_id"] for candidate in both["candidates"]]
    assert len(both_ids) == 31
    tagged_second = [candidate for candidate in both["candidates"] if POINTS[1]["id"] in candidate["target_keys"]]
    assert tagged_second and POINTS[1]["id"] not in result["candidates"][0]["target_keys"]
    assert not {1, 33}.intersection(both_ids)
    assert workspace.draft_path.read_bytes() == before
    assert workspace.list_records() == []
    assert calls[0] == ({"mode": "class", "class_ids": ["合成9班"], "use_historical_fallback": False}, {"mode": "semester", "session_ids": [], "curriculum_volume_id": VOLUME["id"]})
    assert client.post("/api/question-assembly/assistant/candidates", json=request()).json() == result
    preview = client.get("/api/question-assembly/questions", params=[("question_ids", qid) for qid in both_ids]).json()
    assert len(preview["items"]) == 31
    assert all(item["rich_content"] is not None for item in preview["items"])


def test_filters_empty_evidence_and_changed_scope_never_silently_expand(client_and_source):
    client, source, _, _ = client_and_source
    for patch in ({"question_type": "解答题"}, {"difficulty_min": 8}, {"target_keys": []}):
        response = client.post("/api/question-assembly/assistant/candidates", json=request(**patch))
        assert response.status_code == 200, response.text
        assert response.json()["candidates"] == []
    assert client.post("/api/question-assembly/assistant/candidates", json=request(target_keys=["unknown"])).status_code == 422
    assert client.post("/api/question-assembly/assistant/candidates", json=request(difficulty_min=9, difficulty_max=3)).status_code == 422
    assert client.post("/api/question-assembly/assistant/candidates", json=request(chapter_id="another-term")).status_code == 422
    source["group_weak_points"] = []
    for student in source["students"]:
        student.update(weak_points=[], score_rate=None)
    empty = client.post("/api/question-assembly/assistant/candidates", json=request()).json()
    assert empty["evidence_student_count"] == 0
    assert empty["weaknesses"] == empty["candidates"] == []


def test_class_assistant_uses_frozen_part_identity_and_prioritizes_response_task(client_and_source, monkeypatch):
    from question_bank.services import assembly_assistant as assistant
    client, source, _, workspace = client_and_source
    key, other = POINTS[0]["id"], POINTS[1]["id"]
    def facet(skill):
        return {"part_id": "frozen-part", "direct_keys": [skill], "skill_keys": [], "topic_keys": [key],
                "section_keys": [CHAPTER["sections"][0]["knowledge_id"]], "chapter_keys": [CHAPTER["knowledge_id"]]}
    def metadata(skill, mode, text):
        return {"parts": [facet(skill)], "skill_keys": [], "topic_keys": [key], "evidence_version_id": "frozen-v1",
                "practice_observations_by_key": {skill: [{"part_id": "frozen-part", "response_mode": mode, "observable": text}]}}
    facets = {90: metadata(key, "process_required", "由勾股定理列出两边平方和等式"),
              2: metadata(key, "exact_objective", "答案B"),
              3: metadata(other, "process_required", "由勾股定理列式AB²+BC²=AC²并求长")}
    monkeypatch.setattr(assistant, "load_question_facets", lambda *args, **kwargs: facets)
    for student in source["students"][:4]:
        student["weak_points"][0]["source_question_refs"] = [{"bank_question_id": 90, "full_score": 6, "score_awarded": 3,
            "question_difficulty": 5, "assessment": {"granularity": "part", "part_id": "renamed-rubric-part",
            "evidence_part_id": "frozen-part", "evidence_version_id": "frozen-v1", "eligible": True}}]
    before = workspace.draft_path.read_bytes()
    response = client.post("/api/question-assembly/assistant/candidates", json=request(target_keys=[key]))
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert [c["question_id"] for c in candidates] == [2, 3]
    assert candidates[0]["selection_kind"] == "direct"
    assert "环节练习" in candidates[0]["match_label"]
    assert candidates[1]["selection_kind"] == "task_matched"
    assert "已有解题步骤" in candidates[1]["match_label"]
    assert workspace.draft_path.read_bytes() == before


def test_mastered_points_remain_selectable_and_foundation_questions_follow_focus(client_and_source):
    client, source, _, _ = client_and_source
    reader = client.app.dependency_overrides[get_question_bank_read_service]()
    point = POINTS[2]
    for student in source["students"][:4]:
        student["weak_points"].append({"knowledge_key": point["id"], "knowledge_point": point["display_name"], "mastery": .95, "evidence_count": 2})
    source["group_weak_points"].append({"knowledge_key": point["id"], "knowledge_point": point["display_name"], "mastery": .95, "evidence_count": 8})
    with connect(reader.db_path) as connection:
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,difficulty) VALUES(40,1,'40','选择题','合成已掌握知识巩固题','3')")
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(40,'knowledge_point',?)", (point["display_name"],))
        connection.execute("UPDATE questions SET difficulty='2' WHERE id=2")
    payload = client.post("/api/question-assembly/assistant/candidates", json=request()).json()
    assert payload["weaknesses"][-1]["mastery"] == .95
    assert payload["weaknesses"][-1]["weak_student_count"] == 0
    all_points = client.post("/api/question-assembly/assistant/candidates",
                             json=request(target_keys=[item["id"] for item in POINTS[:3]])).json()
    # No loss-evidence difficulty aim exists in this fixture, so every candidate
    # lands in the unknown band while the foundation flag stays informative.
    assert {item["question_id"] for item in all_points["candidates"] if item["practice_kind"] == "foundation"} == {2, 40}
    assert all(item["difficulty_band"] == "unknown" for item in all_points["candidates"])
    only_mastered = client.post("/api/question-assembly/assistant/candidates", json=request(target_keys=[point["id"]])).json()
    assert [item["question_id"] for item in only_mastered["candidates"]] == [40]
    restricted = client.post("/api/question-assembly/assistant/candidates", json=request(difficulty_min=3, difficulty_max=6)).json()
    assert 2 not in {item["question_id"] for item in restricted["candidates"]}


def test_candidates_sort_by_adaptive_difficulty_band(client_and_source):
    """Suitable-difficulty questions lead, then lower, then higher; unknown last."""
    client, source, _, _ = client_and_source
    reader = client.app.dependency_overrides[get_question_bank_read_service]()
    point = POINTS[0]
    for student in source["students"][:4]:
        for weak in student["weak_points"]:
            if weak["knowledge_key"] != point["id"]:
                continue
            weak["source_question_refs"] = [{
                "bank_question_id": 1, "question_id": "原卷第1题",
                "score_awarded": 0, "full_score": 5,
                "assessment": {"eligible": True, "evidence_weight": 1.0, "part_difficulty": 6},
            }]
    with connect(reader.db_path) as connection:
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(41,1,'41','选择题','合成较高难度题','解析','8')")
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(41,'knowledge_point',?)", (point["display_name"],))
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(42,1,'42','选择题','合成较低难度题','解析','3')")
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(42,'knowledge_point',?)", (point["display_name"],))
    payload = client.post("/api/question-assembly/assistant/candidates", json=request(target_keys=[point["id"]])).json()
    weakness = next(item for item in payload["weaknesses"] if item["knowledge_key"] == point["id"])
    # A zero response on this goal starts lower, even with a middling total mark.
    assert weakness["target_difficulty"] == 3
    bands = {item["question_id"]: item["difficulty_band"] for item in payload["candidates"]}
    assert bands[41] == "higher" and bands[42] == "suitable"
    assert bands[2] == "higher"
    order = [item["difficulty_band"] for item in payload["candidates"]]
    assert order == sorted(order, key={"suitable": 0, "lower": 1, "higher": 2, "unknown": 3}.get)


def test_similar_questions_fold_inside_one_band(client_and_source):
    """Near-identical stems in the same difficulty band collapse to one card."""
    client, _, _, _ = client_and_source
    reader = client.app.dependency_overrides[get_question_bank_read_service]()
    point = POINTS[0]
    base = "如图，已知直角三角形两条直角边分别为 3 和 4，求斜边长度的完整过程与结果说明"
    with connect(reader.db_path) as connection:
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(50,1,'50','选择题',?,'解析','5')", (base + "。",))
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(50,'knowledge_point',?)", (point["display_name"],))
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(51,1,'51','选择题',?,'解析','5')", (base + "，并作答。",))
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(51,'knowledge_point',?)", (point["display_name"],))
        connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(52,1,'52','选择题','完全不同：统计全班同学身高数据并求平均数。','解析','5')")
        connection.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(52,'knowledge_point',?)", (point["display_name"],))
    payload = client.post("/api/question-assembly/assistant/candidates", json=request(target_keys=[point["id"]])).json()
    candidates = {item["question_id"]: item for item in payload["candidates"]}
    reps = [qid for qid, item in candidates.items() if item["similar_question_ids"]]
    assert len(reps) == 1
    rep = reps[0]
    assert {rep, *candidates[rep]["similar_question_ids"]} == {50, 51}
    other = 51 if rep == 50 else 50
    assert other not in candidates
    assert 52 in candidates and not candidates[52]["similar_question_ids"]
    assert payload["candidate_total"] == len(payload["candidates"])


def test_unrelated_question_figures_are_not_decoded(client_and_source, monkeypatch):
    """Large unrelated pools must not trigger per-image work during filtering."""
    from question_bank.services import duplicate_analysis_copy_service as duplicates
    from PIL import Image
    client, _, _, _ = client_and_source
    reader = client.app.dependency_overrides[get_question_bank_read_service]()
    image_root = reader.data_root / "question_bank/extracted_images"
    image_root.mkdir(parents=True, exist_ok=True)
    with connect(reader.db_path) as connection:
        for qid in range(100, 700):
            image_path = image_root / f"synthetic-{qid}.png"
            Image.new("RGB", (512, 256), (qid % 255, 100, 120)).save(image_path)
            connection.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,difficulty,image_paths,has_images) VALUES(?,1,?,'选择题',?,'5',?,1)",
                               (qid, str(qid), f"合成无关知识点题目 {qid}", '["' + image_path.as_posix() + '"]'))
    original = duplicates.exact_question_key
    def checked_key(question, **kwargs):
        assert int(question["id"]) < 100, "Unrelated question was decoded"
        return original(question, **kwargs)
    monkeypatch.setattr(duplicates, "exact_question_key", checked_key)
    response = client.post("/api/question-assembly/assistant/candidates", json=request())
    assert response.status_code == 200
    assert response.json()["candidate_total"] == 26
