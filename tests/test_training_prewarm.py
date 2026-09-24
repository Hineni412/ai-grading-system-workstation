from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from integration.data_generation import reset_commit_generations
from integration.training_prewarm import (
    TrainingPrewarmWorker,
    clear_recent_requests,
    prewarm_enabled,
    recent_requests,
    record_request,
)


@pytest.fixture(autouse=True)
def _clean_state():
    clear_recent_requests()
    reset_commit_generations()
    yield
    clear_recent_requests()
    reset_commit_generations()


class _FakeManager:
    def __init__(
        self, active: bool = False, job_statuses: tuple[str, ...] = ()
    ) -> None:
        self._active = active
        self._job_statuses = list(job_statuses)

    @property
    def is_shutdown(self) -> bool:
        return False

    def list(self, *, statuses=(), **kwargs):
        records = [
            object() for status in self._job_statuses if status in statuses
        ]
        if self._active:
            records.append(object())
        return records, len(records)


def _paths(tmp_path: Path) -> SimpleNamespace:
    grading = tmp_path / "grading.db"
    qb = tmp_path / "question_bank.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE grading_sessions ("
            "id INTEGER PRIMARY KEY, is_deleted INTEGER DEFAULT 0,"
            " curriculum_volume_id TEXT)"
        )
        connection.execute(
            "INSERT INTO grading_sessions (is_deleted, curriculum_volume_id)"
            " VALUES (0, 'vol-x')"
        )
        connection.execute("CREATE TABLE students (class_name TEXT)")
        connection.executemany(
            "INSERT INTO students (class_name) VALUES (?)",
            [("9",), ("10",)],
        )
        connection.commit()
    with closing(sqlite3.connect(qb)) as connection:
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.commit()
    return SimpleNamespace(db_path=grading, qb_db_path=qb)


def _recorder(worker: TrainingPrewarmWorker, monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def record(kind, scope, exam_scope, params):
        calls.append(
            (
                kind,
                json.dumps(scope, sort_keys=True, default=str),
                json.dumps(exam_scope, sort_keys=True, default=str),
                json.dumps(dict(params), sort_keys=True, default=str),
            )
        )

    monkeypatch.setattr(worker, "_compute", record)
    return calls


def test_recent_requests_are_bounded_and_merge_kinds() -> None:
    for index in range(10):
        record_request(
            "diagnosis",
            scope={"mode": "student", "student_ids": [f"s{index}"]},
            exam_scope={"mode": "semester"},
            params={},
        )
    recent = recent_requests()
    assert len(recent) == 8
    kept = {entry["scope"]["student_ids"][0] for entry in recent}
    assert "s0" not in kept and "s1" not in kept
    assert "s9" in kept

    record_request(
        "overview",
        scope={"mode": "student", "student_ids": ["s9"]},
        exam_scope={"mode": "semester"},
        params={"volume_id": "vol-x"},
    )
    recent = recent_requests()
    entry = next(
        item for item in recent if item["scope"]["student_ids"] == ["s9"]
    )
    assert set(entry["kinds"]) == {"diagnosis", "overview"}


def test_prewarm_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_GRADING_PREWARM", "0")
    assert prewarm_enabled() is False
    monkeypatch.setenv("AI_GRADING_PREWARM", "1")
    assert prewarm_enabled() is True


def test_worker_runs_startup_batch_then_idles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    worker = TrainingPrewarmWorker(paths, _FakeManager())
    calls = _recorder(worker, monkeypatch)

    assert worker.tick() is True
    # all students + 2 classes, diagnosis + overview each;
    # plus assistant (and its own diagnosis scope) per class
    assert len(calls) == 10
    kinds = [call[0] for call in calls]
    assert sorted(set(kinds)) == ["assistant", "diagnosis", "overview"]
    scopes = {call[1] for call in calls}
    assert any('"all"' in scope for scope in scopes)
    assert any('"9"' in scope for scope in scopes)
    assert any('"10"' in scope for scope in scopes)
    assistant_calls = [call for call in calls if call[0] == "assistant"]
    assert len(assistant_calls) == 2
    seen_params = [
        json.loads(call[3]) for call in assistant_calls
    ]
    for params, class_name in zip(seen_params, ("10", "9")):
        assert params == {
            "class_id": class_name,
            "curriculum_volume_id": "vol-x",
            "chapter_id": "",
            "target_keys": None,
            "question_type": "",
            "difficulty_min": 1,
            "difficulty_max": 10,
            "exclude_exam_originals": True,
            "exclude_recent": True,
        }

    calls.clear()
    assert worker.tick() is False
    assert calls == []


def test_worker_recomputes_recent_keys_after_generation_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    worker = TrainingPrewarmWorker(paths, _FakeManager())
    calls = _recorder(worker, monkeypatch)
    assert worker.tick() is True
    calls.clear()

    scope = {"mode": "student", "student_ids": ["s1"]}
    exam_scope = {"mode": "semester", "curriculum_volume_id": "vol-x"}
    record_request(
        "overview", scope=scope, exam_scope=exam_scope,
        params={"volume_id": "vol-x"},
    )

    # No commit yet -> nothing to refresh.
    assert worker.tick() is False
    assert calls == []

    with closing(sqlite3.connect(paths.db_path)) as connection:
        connection.execute("INSERT INTO students (class_name) VALUES ('11')")
        connection.commit()

    assert worker.tick() is True
    kinds = [call[0] for call in calls]
    # The recorded scope's diagnosis + overview are recomputed; the startup
    # batch does not repeat.
    assert kinds == ["diagnosis", "overview"]
    assert all('"s1"' in call[1] for call in calls)


def test_worker_recomputes_recent_assistant_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    worker = TrainingPrewarmWorker(paths, _FakeManager())
    calls = _recorder(worker, monkeypatch)
    assert worker.tick() is True
    calls.clear()

    scope = {
        "mode": "class",
        "class_ids": ["9"],
        "use_historical_fallback": False,
    }
    exam_scope = {
        "mode": "semester",
        "session_ids": [],
        "curriculum_volume_id": "vol-x",
    }
    body = {
        "class_id": "9",
        "curriculum_volume_id": "vol-x",
        "chapter_id": "",
        "target_keys": None,
        "question_type": "",
        "difficulty_min": 1,
        "difficulty_max": 10,
        "exclude_exam_originals": True,
        "exclude_recent": True,
    }
    record_request(
        "assistant", scope=scope, exam_scope=exam_scope, params=body
    )

    with closing(sqlite3.connect(paths.db_path)) as connection:
        connection.execute("INSERT INTO students (class_name) VALUES ('11')")
        connection.commit()

    assert worker.tick() is True
    kinds = [call[0] for call in calls]
    assert kinds == ["diagnosis", "assistant"]
    assistant = next(call for call in calls if call[0] == "assistant")
    assert json.loads(assistant[3]) == body


def test_worker_skips_while_job_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    manager = _FakeManager(active=True)
    worker = TrainingPrewarmWorker(paths, manager)
    calls = _recorder(worker, monkeypatch)

    assert worker.tick() is False
    assert calls == []

    manager._active = False
    assert worker.tick() is True
    assert len(calls) == 10


def test_paused_job_does_not_block_prewarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    worker = TrainingPrewarmWorker(paths, _FakeManager(job_statuses=("paused",)))
    calls = _recorder(worker, monkeypatch)

    assert worker.tick() is True
    assert len(calls) == 10

    for status in ("queued", "running"):
        blocking = TrainingPrewarmWorker(
            paths, _FakeManager(job_statuses=(status,))
        )
        blocking_calls = _recorder(blocking, monkeypatch)
        assert blocking.tick() is False
        assert blocking_calls == []


def test_worker_stops_batch_when_a_job_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    manager = _FakeManager()
    worker = TrainingPrewarmWorker(paths, manager)
    calls = _recorder(worker, monkeypatch)

    real_active = worker._jobs_active
    state = {"activated": False}

    def job_starts_mid_batch() -> bool:
        # The job becomes active once the first task has run, just once.
        if calls and not state["activated"]:
            state["activated"] = True
            return True
        return real_active()

    monkeypatch.setattr(worker, "_jobs_active", job_starts_mid_batch)

    assert worker.tick() is True
    assert 0 < len(calls) < 10
    completed = len(calls)

    # The interrupted batch did not mark generations seen: the next tick
    # retries the plan (already-computed keys are cache hits).
    calls.clear()
    assert worker.tick() is True
    assert len(calls) == 10
    assert completed < 10
