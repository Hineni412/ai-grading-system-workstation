from __future__ import annotations

import asyncio
import io
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from docx import Document

from backend.api.app import create_app
from backend.api.dependencies import (
    get_config_source_service,
    get_grading_db,
    get_job_manager,
    get_upload_config_dir,
)
from backend.config_workspace.sources import ConfigSourceRecord, ConfigSourceService
from backend.jobs.config_generation import load_config_generation_input
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


def _client(
    tmp_path: Path,
    *,
    config_generation_handler=None,
) -> tuple[TestClient, DBManager, JobManager]:
    # db lives under a "databases" directory so load_editor_config infers
    # tmp_path as the controlled data root for the session config files.
    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register(
        "config_generation",
        config_generation_handler
        or (
            lambda context: {
                "session_id": int(context.payload["session_id"]),
                "outcome": "partial",
                "total_questions": 1,
                "generated_questions": 0,
                "failed_count": 1,
                "failed_question_ids": ["Q1"],
                "retryable": True,
            }
        ),
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    app.dependency_overrides[get_config_source_service] = lambda: ConfigSourceService(
        tmp_path / "uploaded"
    )
    return TestClient(app), db, manager


def _session(db: DBManager, tmp_path: Path) -> int:
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    return db.create_grading_session("Config Job", str(rubric), str(answer))


def test_config_generation_retry_rejects_unknown_external_request_outcome_without_confirmation(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "failed",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": [],
            "failed_batches": [],
            "uncertain_question_ids": ["Q1"],
            "needs_teacher_resolution": True,
            "retryable": False,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id, "retry_question_ids": ["Q1"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_retry_not_available"


def test_config_generation_retry_accepts_all_uncertain_questions_after_teacher_confirmation(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
            "sync_to_question_bank": True,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": [],
            "failed_batches": [],
            "uncertain_question_ids": ["Q10"],
            "needs_teacher_resolution": True,
            "uncertain_retry_available": True,
            "retryable": False,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={
            "source_job_id": source.id,
            "retry_question_ids": ["Q10"],
            "confirm_uncertain_retry": True,
            "client_request_token": "b" * 32,
        },
    )

    assert response.status_code == 202
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload["retry_question_ids"] == ["Q10"]
    assert stored.payload["confirm_uncertain_retry"] is True
    assert stored.payload["sync_to_question_bank"] is True
