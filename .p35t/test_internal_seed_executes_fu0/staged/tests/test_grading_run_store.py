import sqlite3

import pytest

from db_manager import DBManager
from grading_run_store import GradingRunConflictError, GradingRunStore


@pytest.fixture
def seed_session(tmp_path):
    db_path = tmp_path / "grading.db"
    db = DBManager(db_path)
    db.initialize()
    session_id = db.create_grading_session("测试考试", "rubric.json", "answer.json")
    # 建两个学生，供 grading_run_items 的外键使用（真实流程由名单导入产生）。
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO students (id, student_code, name) VALUES (1, 'S1', '甲')")
    conn.execute("INSERT INTO students (id, student_code, name) VALUES (2, 'S2', '乙')")
    conn.commit()
    conn.close()
    store = GradingRunStore(db_path)
    return store, session_id


def test_pause_and_resume_are_scoped_to_active_run(seed_session) -> None:
    store, session_id = seed_session
    run = store.begin(session_id, "cfg", "full_paper")

    assert store.control_state(run.run_token) == "running"
    assert store.request_pause(session_id) is True
    assert store.control_state(run.run_token) == "pause_requested"
    store.finish(run.run_token, "paused")
    assert store.resume(session_id, "cfg", "full_paper").id == run.id
    assert store.resume(session_id, "changed", "full_paper") is None


def test_begin_rejects_second_active_run(seed_session) -> None:
    store, session_id = seed_session
    store.begin(session_id, "cfg", "full_paper")

    with pytest.raises(GradingRunConflictError):
        store.begin(session_id, "cfg", "full_paper")


def test_request_pause_returns_false_without_running_run(seed_session) -> None:
    store, session_id = seed_session
    assert store.request_pause(session_id) is False


def test_item_status_counts_and_latest(seed_session) -> None:
    store, session_id = seed_session
    run = store.begin(session_id, "cfg", "full_paper")

    pending_item = store.add_item(
        run.id,
        source_label="paper-1",
        student_id=1,
        paper_fingerprint="fp1",
        config_fingerprint="cfg",
        status="pending",
    )
    store.add_item(
        run.id,
        source_label="paper-2",
        student_id=2,
        paper_fingerprint="fp2",
        config_fingerprint="cfg",
        status="conflict",
        disposition_reason="多份不同答卷",
    )
    store.mark_grading(pending_item)
    store.set_item_status(pending_item, "graded")

    counts = store.counts(run.id)
    assert counts["graded"] == 1
    assert counts["conflict"] == 1
    assert counts["pending"] == 0
    assert store.latest(session_id).id == run.id
    assert len(store.conflict_items(run.id)) == 1
