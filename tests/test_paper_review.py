from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_grading_db, get_reports_dir
from backend.class_analysis import ClassAnalysisStateStore
from backend.repositories.grading_database import open_grading_repositories
from question_bank.database.schema import initialize_database


@pytest.fixture
def paper_api(tmp_path, monkeypatch):
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    bank = tmp_path / "databases" / "question_bank.db"
    initialize_database(bank)
    config = tmp_path / "config" / "uploaded"
    config.mkdir(parents=True)
    qids = ["Q1", "Q2(P1)", "Q2(P2)", "Q3"]
    rubric = {"total_score": 20, "subject": "数学", "questions": [
        {"question_id": "Q1", "question_type": "choice", "max_score": 4, "stem_summary": "合成选择题"},
        {"question_id": "Q2", "question_type": "calculation", "max_score": 6, "stem_summary": "合成两问大题", "parts": [
            {"part_id": "Q2(P1)", "part_score": 3, "stem_summary": "测试第一问"},
            {"part_id": "Q2(P2)", "part_score": 3, "stem_summary": "测试第二问"},
        ]},
        {"question_id": "Q3", "question_type": "calculation", "max_score": 10, "stem_summary": "合成末尾题"},
    ]}
    rubric_path, answer_path = config / "rubric.json", config / "answers.json"
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
    answer_path.write_text(json.dumps({"questions": [{"question_id": qid, "canonical_answer": "B"} for qid in qids]}), encoding="utf-8")
    session = db.sessions.create_grading_session("试卷评价合成测试", str(rubric_path), str(answer_path))
    values = [(0, 0, 0, 0), (1, 0, 1, 2), (2, 1, 1, 4), (2, 2, 2, 0),
              (3, 2, 2, 6), (3, 3, 2, 8), (4, 3, 3, 9), (4, 3, 3, 10)]
    with sqlite3.connect(db.db_path) as conn:
        for i, row in enumerate(values):
            student = conn.execute("INSERT INTO students(student_code,name,class_name) VALUES(?,?,?)", (f"TEST{i}", f"测试学生{i}", "测试甲班" if i < 4 else "测试乙班")).lastrowid
            paper = conn.execute("INSERT INTO exam_papers(session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(?,'','',?,'matched','graded')", (session, student)).lastrowid
            result = conn.execute("INSERT INTO session_results(session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json) VALUES(?,?,?,20,?,0,?)", (session, student, paper, sum(row), json.dumps({"grading_completeness": {"status": "complete"}}))).lastrowid
            for qid, score in zip(qids, row):
                blank = qid == "Q3" and score == 0 or i == 0 and qid.startswith("Q2")
                conn.execute("INSERT INTO session_details(result_id,question_id,score_awarded,deduction_reason,error_category,error_summary,knowledge_ids) VALUES(?,?,?,?,?,?,'[\"UNKNOWN\"]')", (result, qid, score, "未作答" if blank else "计算有误", "未作答" if blank else "计算错误", "blank" if blank else ""))
    # 与教材目录使用同一小节标识。
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    section = curriculum_volume(volume_id="bnu24-math-g8-upper")["chapters"][0]["sections"][0]["id"]
    with sqlite3.connect(bank) as conn:
        bank_id = conn.execute("INSERT INTO questions(question_number,question_text,question_type,difficulty) VALUES('1','合成测试题','计算题',2)").lastrowid
        conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES(?,'Q1',?,'manual','confirmed')", (str(session), bank_id))
        conn.execute("INSERT INTO question_scope_summary(question_id,primary_section_id) VALUES(?,?)", (bank_id, section))
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'knowledge_point','kp_test_t01')", (bank_id,))
        parent_bank = conn.execute("INSERT INTO questions(question_number,question_text,question_type,difficulty) VALUES('1','合成测试大题','计算题',5)").lastrowid
        conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES(?,'Q2',?,'manual','confirmed')", (str(session), parent_bank))
        conn.execute("INSERT INTO question_scope_summary(question_id,primary_section_id) VALUES(?,?)", (parent_bank, section))
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'secondary_type','kp_test_t02')", (parent_bank,))
        for part, difficulty in (("part-1", 3), ("part-2", 5)):
            conn.execute("INSERT INTO question_part_difficulty_features(question_id,part_id,features_json,formula_difficulty,formula_version) VALUES(?,?,'{}',?,'test')", (parent_bank, part, difficulty))
        hard_id = conn.execute("INSERT INTO questions(question_number,question_text,question_type,difficulty) VALUES('1','合成偏难题','计算题',8)").lastrowid
    profile = {"chapters": [{"label": "测试章", "sections": [{"id": section, "label": "测试小节", "skills": [
        {"key": "kp_test_t01", "name": "已选主题型", "group_count": 10, "cells": {}, "typical": []},
        {"key": "kp_test_t02", "name": "已选次题型", "group_count": 8, "cells": {}, "typical": []},
        {"key": "kp_test_t03", "name": "更高频未选", "group_count": 20, "cells": {}, "typical": []},
        {"key": "kp_test_t04", "name": "偏难可不选", "group_count": 30, "cells": {"midterm": {"hard": [hard_id]}}, "typical": []},
    ]}]}]}
    monkeypatch.setattr("question_bank.services.question_read_service.QuestionBankReadService.chapter_exam_profile", lambda self, volume: profile)
    reports = tmp_path / "reports"
    store = ClassAnalysisStateStore(reports)
    store.save(session, cause_analysis={"questions": {}}, error_records={"test": {"reason": "保留已保存错因"}})
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_reports_dir] = lambda: reports
    with TestClient(app) as client:
        yield client, db, session, store, bank


def test_dimensions_groups_and_null_weight(paper_api):
    client, db, session, store, bank = paper_api
    response = client.get(f"/api/sessions/{session}/paper-review")
    assert response.status_code == 200, response.text
    result = response.json()
    assert {d["key"]: d["score"] for d in result["dimensions"]} == {
        "coverage": 47, "time_fit": 43, "distribution": 40, "basic_hits": 100, "discrimination": 100}
    assert result["total"] == 72
    group = result["groups"][0]
    assert group["id"] == "Q2" and group["parts"] == ["Q2(P1)", "Q2(P2)"]
    assert group["rate"] == .5833
    assert group["r_rest"] == .870
    items = {item["id"]: item for item in result["items"]}
    assert items["Q2(P1)"]["r_rest"] == .854
    assert items["Q2(P2)"]["r_rest"] == .849
    assert items["Q2(P1)"]["predicted_difficulty"] == 3
    assert items["Q2(P2)"]["predicted_difficulty"] == 5
    assert items["Q1"]["class_rates"] == {"测试甲班": .3125, "测试乙班": .875}
    assert result["dimensions"][1]["detail"]["unfinished"] == 1
    assert result["dimensions"][0]["detail"]["missing"] == ["更高频未选"]
    # Q3 主考在另一小节，但本卷仍考到了本节题型。
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    other_section = curriculum_volume(volume_id="bnu24-math-g8-upper")["chapters"][0]["sections"][1]["id"]
    with sqlite3.connect(bank) as conn:
        outside = conn.execute("INSERT INTO questions(question_number,question_text,question_type) VALUES('3','合成跨小节题','计算题')").lastrowid
        conn.execute("INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES(?,'Q3',?,'manual','confirmed')", (str(session), outside))
        conn.execute("INSERT INTO question_scope_summary(question_id,primary_section_id) VALUES(?,?)", (outside, other_section))
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(?,'knowledge_point','kp_test_t03')", (outside,))
    cross_section = client.get(f"/api/sessions/{session}/paper-review").json()
    assert cross_section["dimensions"][0]["score"] == 89
    assert cross_section["total"] == 80
    assert cross_section["coverage_types"]["kp_test_t03"]["covered_by"] == ["Q3"]
    from question_bank.solution_evidence.evidence_snapshot import SNAPSHOT_SCHEMA_VERSION
    rubric_path = db.db_path.parent.parent / "config/uploaded/rubric.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    parts = rubric["questions"][1]["parts"]
    parts[0]["steps"] = [{"step_id": "S1", "step_score": 3, "evidence_point_ids": ["test-b"]}]
    parts[1]["steps"] = [{"step_id": "S1", "step_score": 3, "evidence_point_ids": ["test-a"]}]
    rubric_path.write_text(json.dumps(rubric), encoding="utf-8")
    with sqlite3.connect(bank) as conn:
        parent_bank = conn.execute("SELECT bank_question_id FROM grading_question_links WHERE source_question_id='Q2'").fetchone()[0]
    snapshot = {"schema_version": SNAPSHOT_SCHEMA_VERSION, "questions": {"Q2": {
        "usable": True, "bank_question_id": parent_bank, "evidence": {"parts": [
            {"part_id": "part-1", "evidence_points": [{"evidence_point_id": "test-a"}]},
            {"part_id": "part-2", "evidence_points": [{"evidence_point_id": "test-b"}]},
        ]},
    }}}
    snapshot_path = rubric_path.parent / f"evidence_snapshot-{session}.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    linked = client.get(f"/api/sessions/{session}/paper-review").json()
    linked_items = {item["id"]: item for item in linked["items"]}
    assert linked_items["Q2(P1)"]["predicted_difficulty"] == 5
    assert linked_items["Q2(P2)"]["predicted_difficulty"] == 3
    assert "已保存的小问关联" in linked_items["Q2(P1)"]["predicted_source"]
    assert linked["dimensions"][3]["detail"]["hit_items"] == ["Q1", "Q2(P2)"]
    assert linked["total"] == 80
    with sqlite3.connect(bank) as conn:
        conn.execute("DELETE FROM grading_question_links")
    result = client.get(f"/api/sessions/{session}/paper-review").json()
    assert {d["key"]: d["score"] for d in result["dimensions"]} == {
        "coverage": None, "time_fit": 43, "distribution": 40, "basic_hits": None, "discrimination": 100}
    assert result["total"] == 61


def test_marks_reentry_clear_and_readonly(paper_api):
    client, db, session, store, bank = paper_api
    state_path = store._path(session)
    before_state = state_path.read_bytes()
    with sqlite3.connect(db.db_path) as conn:
        before_db = list(conn.iterdump())
    payload = client.get(f"/api/sessions/{session}/paper-review").json()
    assert payload["status"] == "ready"
    assert state_path.read_bytes() == before_state
    with sqlite3.connect(db.db_path) as conn:
        assert list(conn.iterdump()) == before_db
    assert "student_name" not in json.dumps(payload) and "student_id" not in json.dumps(payload)
    url = f"/api/sessions/{session}/paper-review/marks/Q2"
    assert client.put(url, json={"mark": "keep"}).json() == {"marks": {"Q2": "keep"}}
    assert client.get(f"/api/sessions/{session}/paper-review").json()["marks"] == {"Q2": "keep"}
    assert client.put(url, json={"mark": None}).json() == {"marks": {}}
    assert client.get(f"/api/sessions/{session}/paper-review").json()["marks"] == {}
    assert store.load(session)["error_records"] == {"test": {"reason": "保留已保存错因"}}
    assert store.load(session)["cause_analysis"] == {"questions": {}}
    assert client.put(f"{url}(P1)", json={"mark": "fix"}).status_code == 422
    assert client.put(url, json={"mark": "invalid"}).status_code == 422
    assert client.get("/api/sessions/99999/paper-review").status_code == 404
    empty = db.sessions.create_grading_session("试卷评价空考试测试", "", "")
    assert client.get(f"/api/sessions/{empty}/paper-review").json()["status"] == "no_data"
