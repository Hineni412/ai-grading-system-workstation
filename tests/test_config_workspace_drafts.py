from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from backend.config_workspace.drafts import create_session_draft
from backend.config_workspace.locks import session_config_lock
from db_manager import DBManager


def initialized_db(path: Path) -> DBManager:
    db = DBManager(path)
    db.initialize()
    return db


class FailingCreateSessionDb:
    def create_grading_session(
        self,
        session_name: str,
        rubric_path: str,
        answer_key_path: str,
    ) -> int:
        raise RuntimeError("insert failed")


def test_create_session_draft_creates_recoverable_created_session(tmp_path) -> None:
    db = initialized_db(tmp_path / "grading.db")

    session_id = create_session_draft(
        db,
        tmp_path / "uploaded",
        name="七年级期末",
    )

    row = db.get_grading_session(session_id)
    assert row is not None
    assert row["session_name"] == "七年级期末"
    assert row["status"] == "created"
    rubric = json.loads(Path(row["rubric_path"]).read_text("utf-8"))
    answer_key = json.loads(Path(row["answer_key_path"]).read_text("utf-8"))
    assert rubric == {
        "draft": True,
        "total_score": 0,
        "questions": [],
        "exam_title": "七年级期末",
        "_config_draft_id": rubric["_config_draft_id"],
    }
    assert answer_key == {
        "draft": True,
        "questions": [],
        "_config_draft_id": rubric["_config_draft_id"],
    }
    assert len(rubric["_config_draft_id"]) == 32


def test_create_session_draft_compensates_files_when_database_insert_fails(
    tmp_path,
) -> None:
    db = FailingCreateSessionDb()

    with pytest.raises(RuntimeError, match="insert failed"):
        create_session_draft(db, tmp_path / "uploaded", name="测试考试")

    assert list((tmp_path / "uploaded").glob("session-draft-*")) == []


def test_create_session_draft_rejects_blank_name_without_creating_files(tmp_path) -> None:
    upload_config_dir = tmp_path / "uploaded"

    with pytest.raises(ValueError, match="session name must be nonblank"):
        create_session_draft(
            FailingCreateSessionDb(),
            upload_config_dir,
            name="  ",
        )

    assert not upload_config_dir.exists()


def test_session_config_lock_serializes_the_same_session(tmp_path) -> None:
    worker_started = threading.Event()
    worker_entered = threading.Event()

    def enter_same_lock() -> None:
        worker_started.set()
        with session_config_lock(tmp_path / "uploaded", 17):
            worker_entered.set()

    with session_config_lock(tmp_path / "uploaded", 17):
        worker = threading.Thread(target=enter_same_lock)
        worker.start()
        assert worker_started.wait(timeout=1)
        assert not worker_entered.wait(timeout=0.05)

    worker.join(timeout=1)
    assert not worker.is_alive()
    assert worker_entered.is_set()
