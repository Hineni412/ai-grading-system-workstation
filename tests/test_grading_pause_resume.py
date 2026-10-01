"""AI 批改的安全暂停/取消、补批去重与同学生冲突判定（真实数据库 + 假批次运行）。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import grading_service
from ai_batch_grading_service import AIBatchRunResult, PaperEntry
from backend.repositories.students import StudentGradingActiveError
from db_manager import DBManager
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
        grading_service,
        "_session_front_page_parity",
        lambda session_id, scan_analysis=None: "odd",
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


def test_released_scan_pdf_is_not_read_when_grading_existing_pages(patched, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from session_originals import release_session_scans
    from tests.test_session_originals import _scans

    db, session_id = _seed(tmp_path, [(1, "stu1")])
    exams = tmp_path / "exams"
    directory = exams / f"session_{session_id}"
    directory.mkdir(parents=True)
    group = _make_group(directory, "stu1", 1, b"paper-one")
    pdf, page, _, _ = _scans(SimpleNamespace(exams_dir=exams, templates_dir=tmp_path / "templates", session_id=session_id))
    release_session_scans(db, tmp_path, session_id)
    assert not pdf.exists() and page.exists()
    monkeypatch.setattr(grading_service, "_apply_manual_decisions", lambda *_args: [group])
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)
    def unexpected_scan(*_args, **_kwargs):
        pytest.fail("released PDF must not be scanned again")
    monkeypatch.setattr(grading_service.Scanner, "analyze", unexpected_scan)
    events = list(_service(db).run_session_grading(
        session_id=session_id, exams_dir=directory,
        rubric_path=tmp_path / "rubric.json", answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []}, grading_mode="ai", enhance_images=False,
    ))
    assert any(event.get("event") == "graded" for event in events)
    assert patched.call_count == 1 and group.front_image.exists()


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


def _templates_dir_paths(tmp_path: Path) -> Any:
    """伪造的 path manager：只提供 _session_front_page_parity 需要的目录。"""
    return type("_Paths", (), {"templates_dir": tmp_path / "templates"})()


def test_front_page_parity_reads_scan_analysis_payload(tmp_path, monkeypatch):
    """预检载荷里的配对状态优先于一切文件。"""
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    assert (
        grading_service._session_front_page_parity(
            7, scan_analysis={"front_page_parity": "even"}
        )
        == "even"
    )
    assert (
        grading_service._session_front_page_parity(
            7, scan_analysis={"template_first_page_role": "back"}
        )
        == "even"
    )
    assert (
        grading_service._session_front_page_parity(
            7, scan_analysis={"front_page_parity": "odd"}
        )
        == "odd"
    )


def test_front_page_parity_reads_scan_analysis_file(tmp_path, monkeypatch):
    """载荷缺配对字段时回落到 scan_analysis_latest.json。"""
    session_dir = tmp_path / "templates" / "session_7"
    session_dir.mkdir(parents=True)
    (session_dir / "scan_analysis_latest.json").write_text(
        '{"groups": [], "front_page_parity": "even"}', encoding="utf-8"
    )
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    assert grading_service._session_front_page_parity(7) == "even"


def test_front_page_parity_uses_workflow_state_compat(tmp_path, monkeypatch):
    """兼容入口：workflow_state.json 的 extra 曾承载配对状态。"""
    session_dir = tmp_path / "templates" / "session_7"
    session_dir.mkdir(parents=True)
    (session_dir / "workflow_state.json").write_text(
        '{"extra": {"front_page_parity": "even"}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    assert grading_service._session_front_page_parity(7) == "even"


def test_front_page_parity_defaults_to_odd_only_when_unrecorded(
    tmp_path, monkeypatch
):
    """没有任何状态记录时才沿用 odd 默认。"""
    (tmp_path / "templates" / "session_7").mkdir(parents=True)
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    assert grading_service._session_front_page_parity(7) == "odd"


def test_front_page_parity_rejects_corrupt_state_file(tmp_path, monkeypatch):
    """状态文件读不出来必须报错，绝不能按奇数页猜测。"""
    session_dir = tmp_path / "templates" / "session_7"
    session_dir.mkdir(parents=True)
    (session_dir / "scan_analysis_latest.json").write_text(
        "{not json", encoding="utf-8"
    )
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    with pytest.raises(ValueError, match="配对状态读取失败"):
        grading_service._session_front_page_parity(7)


def test_front_page_parity_rejects_invalid_recorded_value(
    tmp_path, monkeypatch
):
    """已记录但取值无效同样属于损坏状态。"""
    session_dir = tmp_path / "templates" / "session_7"
    session_dir.mkdir(parents=True)
    (session_dir / "scan_analysis_latest.json").write_text(
        '{"front_page_parity": "sideways"}', encoding="utf-8"
    )
    monkeypatch.setattr(
        grading_service,
        "get_path_manager",
        lambda: _templates_dir_paths(tmp_path),
    )
    with pytest.raises(ValueError, match="配对状态无效"):
        grading_service._session_front_page_parity(7)


def test_run_record_write_failure_keeps_grades_and_surfaces_warning(
    patched,
    tmp_path,
    monkeypatch,
):
    """账本（附加层）写失败不丢成绩，但终态事件必须带 run_record_write_failed。"""
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-one")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)

    real_store_class = GradingRunStore

    class _FailingRunStore:
        """读写正常的账本，但所有写操作都抛错（模拟磁盘写失败）。"""

        _failing_writes = {"finish", "set_item_status", "mark_grading", "add_item"}

        def __init__(self, db_path: Any) -> None:
            self._real = real_store_class(db_path)

        def __getattr__(self, name: str) -> Any:
            if name in self._failing_writes:
                def _boom(*args: Any, **kwargs: Any) -> Any:
                    raise RuntimeError(f"ledger write failed: {name}")

                return _boom
            return getattr(self._real, name)

    monkeypatch.setattr(
        "grading_run_store.GradingRunStore",
        lambda db_path: _FailingRunStore(db_path),
    )

    events = list(
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

    terminal = events[-1]
    assert terminal["event"] == "session_completed"
    assert terminal["run_record_write_failed"] is True
    # 成绩本身不受影响：结果行仍然写入。
    with db._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )


def test_rubric_preflight_raises_for_unreadable_file(tmp_path):
    """评分依据读不出来必须中止批改预检，并点名文件。"""
    rubric = tmp_path / "rubric.json"
    rubric.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="rubric.json"):
        grading_service._load_rubric_for_preflight(rubric)

    rubric.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="不是 JSON 对象"):
        grading_service._load_rubric_for_preflight(rubric)

    with pytest.raises(ValueError, match="missing.json"):
        grading_service._load_rubric_for_preflight(tmp_path / "missing.json")
