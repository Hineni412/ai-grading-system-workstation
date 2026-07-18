"""整卷批改的安全暂停/恢复、本批次去重与同学生冲突判定（真实数据库 + 假模型）。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import grading_service
from db_manager import DBManager, StudentGradingActiveError
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


def test_cancel_discards_inflight_full_paper_result(patched, tmp_path, monkeypatch):
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_grade(_grader, paper_group, *_args, **_kwargs):
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return Result()

    monkeypatch.setattr(
        grading_service,
        "_grade_one_paper_with_retries",
        blocking_grade,
    )
    service = _service(db)
    events: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                service.run_session_grading(
                    session_id=session_id,
                    exams_dir=tmp_path,
                    rubric_path=tmp_path / "rubric.json",
                    answer_key_path=tmp_path / "answer.json",
                    scan_analysis={"groups": [], "issues": []},
                    max_workers=1,
                    grading_mode="full_paper",
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
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT processing_status FROM exam_papers WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == "pending"
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None
    assert run.state == "paused"


def test_student_delete_waits_until_inflight_grading_finishes(
    patched,
    tmp_path,
    monkeypatch,
):
    import grading_run_store

    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    monkeypatch.setattr(
        grading_run_store,
        "GradingRunStore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger unavailable")),
    )
    started = threading.Event()
    release = threading.Event()

    def blocking_grade(_grader, paper_group, *_args, **_kwargs):
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return Result()

    monkeypatch.setattr(
        grading_service,
        "_grade_one_paper_with_retries",
        blocking_grade,
    )
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
                    max_workers=1,
                    grading_mode="full_paper",
                    enhance_images=False,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    started_in_time = started.wait(3)
    with pytest.raises(StudentGradingActiveError):
        db.delete_student_hard(1)
    assert list(db.backup_dir.glob("grading_before_delete_student_*.db")) == []
    release.set()
    worker.join(5)
    deletion = db.delete_student_hard(1)

    assert started_in_time, errors
    assert not worker.is_alive()
    assert errors == []
    assert deletion["unlinked_papers"] == 1
    assert not any(event.get("event") == "grading_failed" for event in events)
    assert any(event.get("event") == "graded" for event in events)
    with db._connect() as conn:
        paper = conn.execute(
            """
            SELECT student_id, match_status, processing_status, error_message
            FROM exam_papers
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        assert tuple(paper) == (None, "student_deleted", "pending", None)
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_cancel_discards_unpublished_hybrid_results(patched, tmp_path, monkeypatch):
    from hybrid_batch_grading_service import HybridBatchRunResult, PaperEntry

    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_hybrid(**kwargs: Any) -> HybridBatchRunResult:
        paper_group = kwargs["paper_groups"][0]
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return HybridBatchRunResult(
            paper_entries=[PaperEntry("paper-1", 1, "stu1", paper_group)],
            results_by_paper_key={"paper-1": Result()},
            fallback_items=[],
            usage_records=[],
            usage_summary={},
            paused=True,
        )

    monkeypatch.setattr(grading_service, "run_hybrid_batch_grading", blocking_hybrid)
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
                    grading_mode="hybrid_batch",
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
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT processing_status FROM exam_papers WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == "pending"
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None
    assert run.state == "paused"


def test_cancelled_failed_only_retry_restores_original_paper_state(
    patched,
    tmp_path,
    monkeypatch,
):
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    paper_id = db.create_exam_paper(
        session_id=session_id,
        front_image=str(group.front_image),
        back_image=str(group.back_image),
        ocr_name="stu1",
        student_id=1,
        match_status="matched",
        processing_status="failed",
        error_message="original failure",
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_grade(_grader, paper_group, *_args, **_kwargs):
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return Result()

    monkeypatch.setattr(
        grading_service,
        "_grade_one_paper_with_retries",
        blocking_grade,
    )
    events: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: events.extend(
            _service(db).run_session_grading(
                session_id=session_id,
                exams_dir=tmp_path,
                rubric_path=tmp_path / "rubric.json",
                answer_key_path=tmp_path / "answer.json",
                failed_only=True,
                max_workers=1,
                grading_mode="full_paper",
                enhance_images=False,
                should_cancel=cancelled.is_set,
            )
        )
    )
    worker.start()
    assert started.wait(3)
    cancelled.set()
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert any(event.get("event") == "session_cancelled" for event in events)
    with db._connect() as conn:
        row = conn.execute(
            "SELECT processing_status, error_message FROM exam_papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
    assert row["processing_status"] == "failed"
    assert row["error_message"] == "original failure"


def test_cancel_before_unmatched_groups_does_not_persist_papers(
    patched,
    tmp_path,
    monkeypatch,
):
    db, session_id = _seed(tmp_path, [])
    groups = [
        _make_group(tmp_path, "unknown-one", 0, b"paper-one"),
        _make_group(tmp_path, "unknown-two", 0, b"paper-two"),
    ]
    cancelled = threading.Event()
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: groups,
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    monkeypatch.setattr(db, "find_student_by_name", lambda _name: None)

    service = _service(db)
    # Set cancellation after the earlier lifecycle checks but before group handling.
    monkeypatch.setattr(service, "_record_attendance", lambda *_args: cancelled.set())
    events = list(
        service.run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            enhance_images=False,
            should_cancel=cancelled.is_set,
        )
    )

    assert [event["event"] for event in events] == ["session_cancelled"]
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == 0


def test_cancel_after_unmatched_event_does_not_persist_later_unmatched_groups(
    patched,
    tmp_path,
    monkeypatch,
):
    db, session_id = _seed(tmp_path, [])
    groups = [
        _make_group(tmp_path, "unknown-one", 0, b"paper-one"),
        _make_group(tmp_path, "unknown-two", 0, b"paper-two"),
    ]
    cancelled = threading.Event()
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: groups,
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    monkeypatch.setattr(db, "find_student_by_name", lambda _name: None)

    events: list[dict[str, Any]] = []
    for event in _service(db).run_session_grading(
        session_id=session_id,
        exams_dir=tmp_path,
        rubric_path=tmp_path / "rubric.json",
        answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []},
        enhance_images=False,
        should_cancel=cancelled.is_set,
    ):
        events.append(event)
        if event.get("event") == "paper_unmatched":
            cancelled.set()

    assert [event["event"] for event in events] == [
        "paper_unmatched",
        "session_cancelled",
    ]
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == 1


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


def test_resume_with_changed_config_does_not_fall_back_to_a_new_run(
    patched,
    tmp_path,
    monkeypatch,
):
    from grading_run_store import GradingRunResumeMismatchError

    db, session_id = _seed(tmp_path, [(1, "stu1")])
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "full_paper")
    store.add_item(
        run.id,
        source_label="001",
        student_id=1,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.finish(run.run_token, "paused")

    with pytest.raises(GradingRunResumeMismatchError):
        list(
            _service(db).run_session_grading(
                session_id=session_id,
                exams_dir=tmp_path,
                rubric_path=tmp_path / "rubric.json",
                answer_key_path=tmp_path / "answer.json",
                scan_analysis={"groups": [], "issues": []},
                grading_mode="full_paper",
                enhance_images=False,
                resume_run_id=run.id,
            )
        )

    assert store.latest(session_id).id == run.id
    assert store.latest(session_id).state == "paused"


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
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)

    first_events = list(
        _service(db).run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            grading_mode="full_paper",
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
            grading_mode="full_paper",
            enhance_images=False,
            supplement_only=True,
            supplement_run_id=run.id,
        )
    )

    assert sum(event.get("event") == "graded" for event in supplement_events) == 1
    assert patched.call_count == 2
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 2
    refreshed = GradingRunStore(db.db_path).latest(session_id)
    assert refreshed is not None and refreshed.id == run.id
    assert refreshed.state == "completed"
    assert GradingRunStore(db.db_path).counts(run.id)["graded"] == 2
