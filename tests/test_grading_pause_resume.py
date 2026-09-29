"""AI 批改的安全暂停/取消、补批去重与同学生冲突判定（真实数据库 + 假批次运行）。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import grading_service
from ai_batch_grading_service import AIBatchRunResult, PaperEntry
from db_manager import DBManager, StudentGradingActiveError
from grading_run_store import GradingRunStore
from scanner import ExamPaperGroup
from backend.repositories.grading_database import open_grading_repositories


class _CountingGrader:
    """假 AIGrader：不调用模型，只承载 rubric/answer_key 供服务读取。"""

    def __init__(self, **kwargs: Any) -> None:
        self.rubric = {
            "questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]
        }
        self.answer_key = {
            "questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]
        }
        self.question_tag_context = {}
        self.call_count = 0


def _stub_result(student_name: str) -> Any:
    class _Result:
        pass

    result = _Result()
    result.student_name = student_name
    result.student_score = 1.0
    result.total_score = 1.0
    result.needs_human_review = False
    result.raw_json = {"questions": []}
    result.grading_details = []
    return result


def _batch_result_for_groups(
    paper_groups: list[ExamPaperGroup],
    *,
    paused: bool = False,
) -> AIBatchRunResult:
    entries = [
        PaperEntry(
            paper_key=f"paper-{group.student_id}",
            student_id=int(group.student_id),
            student_name=group.student_name,
            group=group,
        )
        for group in paper_groups
    ]
    results = {
        entry.paper_key: _stub_result(entry.student_name) for entry in entries
    }
    return AIBatchRunResult(
        paper_entries=entries,
        results_by_paper_key=results,
        fallback_items=[],
        usage_records=[],
        usage_summary={},
        paused=paused,
    )


def _make_group(
    tmp_path: Path, name: str, student_id: int, content: bytes
) -> ExamPaperGroup:
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
    # db 放在 databases/ 子目录下，data_root 才能推断为 tmp_path；
    # 否则解析相对路径（如 "rubric.json"）会落到会话级共享临时根，全量跑时被其他测试的同名文件干扰。
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir()
    db = open_grading_repositories(databases_dir / "grading.db")
    db.initialize()
    session_id = db.sessions.create_grading_session("测试", "rubric.json", "answer.json")
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
    """打桩 AIGrader、AI 批次运行与扫描/模板重环节，用真实账本但不触碰模型。"""
    grader = _CountingGrader()

    def fake_batch(**kwargs: Any) -> AIBatchRunResult:
        grader.call_count += len(kwargs["paper_groups"])
        return _batch_result_for_groups(kwargs["paper_groups"])

    monkeypatch.setattr(grading_service, "AIGrader", lambda **kwargs: grader)
    monkeypatch.setattr(grading_service, "run_ai_batch_grading", fake_batch)
    monkeypatch.setattr(
        grading_service, "_load_rubric_for_preflight", lambda path: grader.rubric
    )
    monkeypatch.setattr(
        grading_service, "_validate_session_exam_identity", lambda session, rubric: None
    )
    monkeypatch.setattr(
        grading_service,
        "_target_question_ids_from_regions",
        lambda regions, rubric=None: ["Q1"],
    )
    monkeypatch.setattr(
        grading_service, "student_name_region_from_regions", lambda regions: None
    )
    monkeypatch.setattr(
        grading_service, "_session_front_page_parity", lambda session_id: "odd"
    )

    import answer_region_geometry

    monkeypatch.setattr(
        answer_region_geometry,
        "answer_regions_with_template_source_sizes",
        lambda db, session_id, data_root=None: [],
    )
    return grader


def _service(db) -> Any:
    return grading_service.GradingService(db_manager=db, llm_client=object())


def test_pause_stops_batch_and_marks_run_paused(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(i, f"stu{i}") for i in range(1, 6)])
    groups = [
        _make_group(tmp_path, f"stu{i}", i, f"paper-{i}".encode()) for i in range(1, 6)
    ]
    # 让服务把这些 group 当作已匹配答卷
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda analysis, decisions, students: groups,
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda session_id: True)
    monkeypatch.setattr(
        db.students,
        "find_student_by_name",
        lambda name: {"id": int(name[3:]), "name": name},
    )

    service = _service(db)
    store = GradingRunStore(db.db_path)

    def pausing_batch(**kwargs: Any) -> AIBatchRunResult:
        # 暂停请求在 grading_started 事件后到达；批次遵循 should_pause 返回 paused。
        assert kwargs["should_pause"]()
        return _batch_result_for_groups(kwargs["paper_groups"], paused=True)

    monkeypatch.setattr(grading_service, "run_ai_batch_grading", pausing_batch)

    events = []
    gen = service.run_session_grading(
        session_id=session_id,
        exams_dir=tmp_path,
        rubric_path=tmp_path / "rubric.json",
        answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []},
        max_workers=2,
        grading_mode="ai",
        enhance_images=False,
    )
    for event in gen:
        events.append(event)
        if event.get("event") == "grading_started":
            store.request_pause(session_id)

    run = store.latest(session_id)
    assert run.state == "paused"
    counts = store.counts(run.id)
    assert counts["graded"] == 5
    assert any(e.get("event") == "session_paused" for e in events)


def test_cancel_discards_unpublished_batch_results(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_batch(**kwargs: Any) -> AIBatchRunResult:
        started.set()
        assert release.wait(3)
        return _batch_result_for_groups(kwargs["paper_groups"], paused=True)

    monkeypatch.setattr(grading_service, "run_ai_batch_grading", blocking_batch)
    events: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                _service(db).run_session_grading(
                    session_id=session_id,
                    exams_dir=tmp_path,
                    rubric_path=tmp_path / "rubric.json",
                    answer_key_path=tmp_path / "answer.json",
                    scan_analysis={"groups": [], "issues": []},
                    grading_mode="ai",
                    enhance_images=False,
                    should_cancel=cancelled.is_set,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    started_in_time = started.wait(3)
    cancelled.set()
    release.set()
    worker.join(5)

    assert started_in_time, errors
    assert not worker.is_alive()
    assert errors == []
    assert any(event.get("event") == "session_cancelled" for event in events)
    with db._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT processing_status FROM exam_papers WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == "pending"
        )
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None
    assert run.state == "failed"


def test_supplement_grades_only_new_sources_without_clearing_prior_results(
    patched,
    tmp_path,
    monkeypatch,
):
    db, session_id = _seed(tmp_path, [(1, "stu1"), (2, "stu2")])
    first = _make_group(tmp_path, "stu1", 1, b"paper-one")
    second = _make_group(tmp_path, "stu2", 2, b"paper-two")
    visible_groups = [first]
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: list(visible_groups),
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)

    first_events = list(
        _service(db).run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            grading_mode="ai",
            enhance_images=False,
        )
    )
    assert any(event.get("event") == "graded" for event in first_events)
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None and run.state == "completed"
    assert patched.call_count == 1

    visible_groups[:] = [first, second]
    supplement_events = list(
        _service(db).run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            grading_mode="ai",
            enhance_images=False,
            supplement_only=True,
            supplement_run_id=run.id,
        )
    )

    assert sum(event.get("event") == "graded" for event in supplement_events) == 1
    assert patched.call_count == 2
    with db._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 2
        )
    refreshed = GradingRunStore(db.db_path).latest(session_id)
    assert refreshed is not None and refreshed.id == run.id
    assert refreshed.state == "completed"
    assert GradingRunStore(db.db_path).counts(run.id)["graded"] == 2


def test_legacy_grading_mode_is_rejected(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)

    for legacy_mode in ("full_paper", "hybrid_batch"):
        with pytest.raises(ValueError, match="旧批改方式已停用"):
            list(
                _service(db).run_session_grading(
                    session_id=session_id,
                    exams_dir=tmp_path,
                    rubric_path=tmp_path / "rubric.json",
                    answer_key_path=tmp_path / "answer.json",
                    scan_analysis={"groups": [], "issues": []},
                    grading_mode=legacy_mode,
                    enhance_images=False,
                )
            )


def test_legacy_run_cannot_be_resumed(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)
    store = GradingRunStore(db.db_path)
    legacy_run = store.begin(session_id, "a" * 64, "full_paper")
    store.finish(legacy_run.run_token, "paused")

    from grading_run_store import GradingRunResumeMismatchError

    with pytest.raises(GradingRunResumeMismatchError, match="旧批改方式已停用"):
        list(
            _service(db).run_session_grading(
                session_id=session_id,
                exams_dir=tmp_path,
                rubric_path=tmp_path / "rubric.json",
                answer_key_path=tmp_path / "answer.json",
                scan_analysis={"groups": [], "issues": []},
                grading_mode="ai",
                enhance_images=False,
                resume_run_id=legacy_run.id,
            )
        )
