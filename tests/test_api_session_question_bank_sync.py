from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_grading_db, get_job_manager
from backend.config_workspace.publish import load_editor_config
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


def _configured_client(
    tmp_path: Path,
) -> tuple[TestClient, DBManager, JobManager, int, str]:
    db_path = tmp_path / "data" / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True)
    db = DBManager(db_path)
    db.initialize()
    rubric = tmp_path / "data" / "config" / "rubric.json"
    answer = tmp_path / "data" / "config" / "answer.json"
    rubric.parent.mkdir(parents=True)
    rubric.write_text(
        json.dumps(
            {
                "total_score": 1,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 1,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 1,
                                "response_mode": "exact_objective",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 1,
                                        "core_goal": "Select B",
                                        "required_elements": ["B"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    answer.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "canonical_answer": "B",
                        "parts": [{"part_id": "Q1", "answer": "B"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "data" / "question_bank" / "raw_papers" / "paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"archived source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    session_id = db.create_grading_session(
        "Sync API",
        str(rubric),
        str(answer),
        source_paper_path=str(source),
        source_paper_sha256=source_sha256,
    )
    revision = load_editor_config(db, session_id).revision
    manager = JobManager(JobStore(db_path), max_workers=1)
    manager.register(
        "question_bank_sync",
        lambda context: {
            "session_id": int(context.payload["session_id"]),
            "outcome": "complete",
            "imported_count": 1,
            "tagged_count": 1,
            "linked_count": 1,
            "failed_count": 0,
            "successful_question_ids": [11],
            "failed_question_ids": [],
            "review_count": 0,
            "proposal_ids": [],
            "retryable": False,
        },
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), db, manager, session_id, revision


def test_session_question_bank_sync_is_version_bound_and_public(
    tmp_path: Path,
) -> None:
    client, _db, manager, session_id, revision = _configured_client(tmp_path)
    token = "a" * 32

    first = client.post(
        f"/api/sessions/{session_id}/question-bank-sync",
        json={"config_revision": revision, "client_request_token": token},
    )
    replay = client.post(
        f"/api/sessions/{session_id}/question-bank-sync",
        json={"config_revision": revision, "client_request_token": token},
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["job_type"] == "question_bank_sync"
    assert first.json()["payload"] == {
        "session_id": session_id,
        "mode": "sync",
        "config_revision": revision,
    }
    assert "source_paper" not in first.text
    manager.wait(first.json()["id"], timeout=5)
    queried = client.get(f"/api/jobs/{first.json()['id']}")
    assert queried.json()["result"]["tagged_count"] == 1


def test_session_question_bank_sync_rejects_stale_config_revision(
    tmp_path: Path,
) -> None:
    client, _db, manager, session_id, _revision = _configured_client(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/question-bank-sync",
        json={
            "config_revision": "f" * 64,
            "client_request_token": "b" * 32,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_revision_conflict"
    jobs, total = manager.list(
        session_id=session_id,
        job_types=("question_bank_sync",),
    )
    assert jobs == []
    assert total == 0


def test_failed_sync_without_result_can_retry_only_the_sync_chain(
    tmp_path: Path,
) -> None:
    client, db, manager, session_id, revision = _configured_client(tmp_path)
    source_sha256 = str(
        db.get_grading_session(session_id)["source_paper_sha256"]
    )
    source_job = manager.store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "sync",
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "client_request_token": "c" * 32,
            "client_request_fingerprint": "d" * 64,
        },
    )
    manager.store.finish(
        source_job.id,
        "failed",
        error="private network failure",
        result={},
    )

    response = client.post(
        f"/api/sessions/{session_id}/question-bank-sync/{source_job.id}/retry",
        json={
            "config_revision": revision,
            "client_request_token": "e" * 32,
        },
    )

    assert response.status_code == 202
    assert response.json()["job_type"] == "question_bank_sync"
    assert response.json()["payload"] == {
        "session_id": session_id,
        "mode": "sync_retry",
        "config_revision": revision,
        "retry_of_job_id": source_job.id,
    }
