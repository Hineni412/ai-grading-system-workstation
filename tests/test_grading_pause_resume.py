"""整卷批改的安全暂停/恢复、本批次去重与同学生冲突判定（真实数据库 + 假模型）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import grading_service
from db_manager import DBManager
from grading_run_store import GradingRunStore
from scanner import ExamPaperGroup


class _CountingGrader:
    """假 AIGrader：不调用模型，只按份计数并返回一个可保存的结果。"""

    def __init__(self, **kwargs: Any) -> None:
        self.rubric = {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]}
        self.answer_key = {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]}
        self.question_tag_context = {}
        self.call_count = 0


def _make_group(tmp_path: Path, name: str, student_id: int, content: bytes) -> ExamPaperGroup:
    front = tmp_path / f"{name}_front.png"
    back = tmp_path / f"{name}_back.png"
    Image.new("RGB", (20, 20), "white").save(front)
    back.write_bytes(content)
    return ExamPaperGroup(
        front_image=front,
        back_image=back,
        student_name=name,
        student_id=student_id,
        detected_name=name,
        source_label=name,
    )


def _seed(tmp_path: Path, students: list[tuple[int, str]]) -> tuple[DBManager, int]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("测试", "rubric.json", "answer.json")
    import sqlite3

    conn = sqlite3.connect(db.db_path)
    for sid, name in students:
        conn.execute(
            "INSERT INTO students (id, student_code, name) VALUES (?, ?, ?)",
            (sid, f"S{sid}", name),
        )
    conn.commit()
    conn.close()
    return db, session_id


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """打桩 AIGrader、扫描/模板/持久化的重环节，让整卷批改用真实账本但不触碰模型。"""
    grader = _CountingGrader()

    def fake_grade(g, group, *args, **kwargs):
        grader.call_count += 1

        class _Result:
            student_name = group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list = []

        return _Result()

    monkeypatch.setattr(grading_service, "AIGrader", lambda **kwargs: grader)
    monkeypatch.setattr(grading_service, "_grade_one_paper_with_retries", fake_grade)
    monkeypatch.setattr(grading_service, "_load_rubric_for_preflight", lambda path: grader.rubric)
    monkeypatch.setattr(grading_service, "_validate_session_exam_identity", lambda session, rubric: None)
    monkeypatch.setattr(grading_service, "_target_question_ids_from_regions", lambda regions, rubric=None: ["Q1"])
    monkeypatch.setattr(grading_service, "student_name_region_from_regions", lambda regions: None)
    monkeypatch.setattr(grading_service, "_session_front_page_parity", lambda session_id: "odd")

    import answer_region_geometry

    monkeypatch.setattr(
        answer_region_geometry, "answer_regions_with_template_source_sizes",
        lambda db, session_id, data_root=None: [],
    )
    return grader


def _service(db) -> Any:
    return grading_service.GradingService(db_manager=db, llm_client=object())


def _run(service, session_id, tmp_path, groups, **kwargs):
    # 直接注入已匹配答卷：绕过扫描，把 groups 作为 exam_papers 建好后进入批改。
    events = []
    gen = service.run_session_grading(
        session_id=session_id,
        exams_dir=tmp_path,
        rubric_path=tmp_path / "rubric.json",
        answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []},
        **kwargs,
    )
    return gen, events


def test_pause_stops_new_dispatch_and_saves_inflight(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(i, f"stu{i}") for i in range(1, 6)])
    groups = [_make_group(tmp_path, f"stu{i}", i, f"paper-{i}".encode()) for i in range(1, 6)]
    # 让服务把这些 group 当作已匹配答卷
    monkeypatch.setattr(
        grading_service, "_apply_manual_decisions", lambda analysis, decisions, students: groups
    )
    monkeypatch.setattr(db, "is_template_ready", lambda session_id: True)
    monkeypatch.setattr(db, "find_student_by_name", lambda name: {"id": int(name[3:]), "name": name})

    service = _service(db)
    store = GradingRunStore(db.db_path)
    grader = patched
    events = []
    gen = service.run_session_grading(
        session_id=session_id, exams_dir=tmp_path,
        rubric_path=tmp_path / "rubric.json", answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []}, max_workers=2, grading_mode="full_paper",
        enhance_images=False,
    )
    for event in gen:
        events.append(event)
        if event.get("event") == "grading_started":
            store.request_pause(session_id)

    run = store.latest(session_id)
    assert run.state == "paused"
    assert grader.call_count <= 2
    counts = store.counts(run.id)
    assert counts["graded"] == grader.call_count
    assert counts["pending"] == 5 - grader.call_count
    assert any(e.get("event") == "session_paused" for e in events)


def test_same_student_different_papers_flagged_conflict(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(7, "stu7")])
    groups = [
        _make_group(tmp_path, "stu7a", 7, b"paper-a"),
        _make_group(tmp_path, "stu7b", 7, b"paper-b"),
    ]
    monkeypatch.setattr(
        grading_service, "_apply_manual_decisions", lambda analysis, decisions, students: groups
    )
    monkeypatch.setattr(db, "is_template_ready", lambda session_id: True)
    monkeypatch.setattr(db, "find_student_by_name", lambda name: {"id": 7, "name": name})

    service = _service(db)
    grader = patched
    events = list(
        service.run_session_grading(
            session_id=session_id, exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json", answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []}, grading_mode="full_paper",
            enhance_images=False,
        )
    )
    assert grader.call_count == 0
    assert sum(e.get("event") == "paper_conflict" for e in events) == 2
