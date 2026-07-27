from __future__ import annotations

"""Regression coverage for legacy config publication service boundaries."""

import json
import threading
from pathlib import Path

import pytest

from backend.config_workspace.publish import publish_legacy_config_and_refresh_mapping
from backend.jobs.manager import JobManager
from backend.jobs.store import ConfigSessionBusyError, JobStore
from db_manager import DBManager


def test_legacy_publish_binds_config_and_source_atomically(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text("{}", encoding="utf-8")
    old_answer.write_text("{}", encoding="utf-8")
    session_id = db.create_grading_session("Exam", str(old_rubric), str(old_answer))
    template_id = db.upsert_session_template(
        session_id,
        str(tmp_path / "front.png"),
        str(tmp_path / "back.png"),
    )
    snapshot_token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [],
        confirmed=True,
    )
    store = JobStore(db.db_path)

    with db._connect() as connection:
        connection.execute(
            f"""
            CREATE TRIGGER fail_legacy_source_binding
            BEFORE UPDATE OF source_paper_path ON grading_sessions
            WHEN NEW.id = {int(session_id)}
            BEGIN
                SELECT RAISE(ABORT, 'synthetic source failure');
            END
            """
        )

    with pytest.raises(Exception, match="synthetic source failure"):
        publish_legacy_config_and_refresh_mapping(
            db,
            tmp_path / "uploaded",
            session_id=session_id,
            rubric_path=str(tmp_path / "new-rubric.json"),
            answer_key_path=str(tmp_path / "new-answer.json"),
            source_paper_path="papers/new.docx",
            source_paper_sha256="a" * 64,
            mapping_output_dir=tmp_path / "templates",
            job_store=store,
        )

    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["rubric_path"] == str(old_rubric)
    assert current["answer_key_path"] == str(old_answer)
    assert not current["source_paper_path"]
    template = db.get_session_template(session_id)
    assert template["is_confirmed"] == 1
    assert template["regions_snapshot_pending"] == 1
    assert template["regions_snapshot_token"] == snapshot_token


def test_legacy_publish_invalidates_ready_sync_when_only_config_changes(
    tmp_path: Path,
) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text("{}", encoding="utf-8")
    old_answer.write_text("{}", encoding="utf-8")
    source_path = "papers/source.docx"
    source_sha256 = "a" * 64
    session_id = db.create_grading_session(
        "Exam",
        str(old_rubric),
        str(old_answer),
        source_paper_path=source_path,
        source_paper_sha256=source_sha256,
    )
    db.update_question_bank_sync_state(
        session_id,
        state="ready",
        details={
            "config_revision": "b" * 64,
            "source_paper_sha256": source_sha256,
        },
        error="old sync",
    )

    publish_legacy_config_and_refresh_mapping(
        db,
        tmp_path / "uploaded",
        session_id=session_id,
        rubric_path=str(tmp_path / "new-rubric.json"),
        answer_key_path=str(tmp_path / "new-answer.json"),
        source_paper_path=source_path,
        source_paper_sha256=source_sha256,
        mapping_output_dir=tmp_path / "templates",
        mapping_refresher=lambda: "not_present",
    )

    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["source_paper_sha256"] == source_sha256
    assert current["question_bank_sync_state"] == "not_started"
    assert json.loads(current["question_bank_sync_details_json"]) == {}
    assert current["question_bank_sync_error"] is None
    assert current["question_bank_sync_updated_at"] is None


def test_legacy_publish_cannot_change_binding_after_grading_is_queued(
    tmp_path: Path,
) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text("{}", encoding="utf-8")
    old_answer.write_text("{}", encoding="utf-8")
    session_id = db.create_grading_session(
        "Exam",
        str(old_rubric),
        str(old_answer),
    )
    store = JobStore(db.db_path)
    store.create_job("grading_run", {"session_id": session_id})

    with pytest.raises(ConfigSessionBusyError):
        publish_legacy_config_and_refresh_mapping(
            db,
            tmp_path / "uploaded",
            session_id=session_id,
            rubric_path=str(tmp_path / "new-rubric.json"),
            answer_key_path=str(tmp_path / "new-answer.json"),
            source_paper_path="papers/new.docx",
            source_paper_sha256="b" * 64,
            mapping_output_dir=tmp_path / "templates",
            job_store=store,
            mapping_refresher=lambda: pytest.fail("mapping must not run"),
        )

    current = db.get_grading_session(session_id)
    assert current is not None
    assert current["rubric_path"] == str(old_rubric)
    assert current["answer_key_path"] == str(old_answer)
    assert not current["source_paper_path"]


def test_legacy_mapping_blocks_new_config_job_submission(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    old_rubric = tmp_path / "old-rubric.json"
    old_answer = tmp_path / "old-answer.json"
    old_rubric.write_text(json.dumps({"questions": []}), encoding="utf-8")
    old_answer.write_text(json.dumps({"questions": []}), encoding="utf-8")
    session_id = db.create_grading_session("Exam", str(old_rubric), str(old_answer))
    upload_root = tmp_path / "uploaded"
    manager = JobManager(
        JobStore(db.db_path),
        max_workers=1,
        interrupted_input_root=upload_root,
    )
    manager.register("config_generation", lambda context: {"session_id": session_id})
    mapping_started = threading.Event()
    release_mapping = threading.Event()
    publish_finished = threading.Event()
    submit_finished = threading.Event()

    def mapping() -> MappingStatus:
        mapping_started.set()
        assert release_mapping.wait(timeout=5)
        return "not_present"

    def publish() -> None:
        publish_legacy_config_and_refresh_mapping(
            db,
            upload_root,
            session_id=session_id,
            rubric_path=str(tmp_path / "new-rubric.json"),
            answer_key_path=str(tmp_path / "new-answer.json"),
            source_paper_path="papers/new.docx",
            source_paper_sha256="b" * 64,
            mapping_output_dir=tmp_path / "templates",
            job_store=manager.store,
            mapping_refresher=mapping,
        )
        publish_finished.set()

    publish_thread = threading.Thread(target=publish, daemon=True)
    publish_thread.start()
    assert mapping_started.wait(timeout=5)

    def submit() -> None:
        manager.submit("config_generation", {"session_id": session_id, "mode": "generate"})
        submit_finished.set()

    submit_thread = threading.Thread(target=submit, daemon=True)
    submit_thread.start()
    assert not submit_finished.wait(timeout=0.2)
    manager.store.assert_config_session_idle(session_id)

    release_mapping.set()
    publish_thread.join(timeout=5)
    submit_thread.join(timeout=5)
    assert publish_finished.is_set()
    assert submit_finished.is_set()
    manager.shutdown()
