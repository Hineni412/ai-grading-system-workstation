from __future__ import annotations

from pathlib import Path
from shutil import copy2

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from db_manager import DBManager, StudentRecord
from tests.api_e2e.harness import (
    ApiE2EHarness,
    E2EControls,
    build_job_manager,
    build_paths,
    install_dependency_overrides,
    valid_config_payload,
)


@pytest.fixture
def api_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = build_paths(tmp_path)
    db = DBManager(paths.db_path)
    db.initialize()
    db.upsert_students(
        [
            StudentRecord("SYN-001", "Synthetic Student A", "Synthetic Class"),
            StudentRecord("SYN-002", "Synthetic Student B", "Synthetic Class"),
        ]
    )
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: valid_config_payload(),
    )
    controls = E2EControls()
    manager = build_job_manager(paths, controls=controls)
    try:
        app = create_app()
        install_dependency_overrides(app, db=db, manager=manager, paths=paths)
        with TestClient(app) as client:
            yield ApiE2EHarness(client, db, manager, paths, controls)
    finally:
        manager.shutdown()


@pytest.fixture
def prepared_session_id(api_e2e: ApiE2EHarness) -> int:
    created = api_e2e.client.post(
        "/api/sessions",
        json={
            "name": "Synthetic E2E Exam",
            "rubric_path": str(api_e2e.paths.bootstrap_rubric),
            "answer_key_path": str(api_e2e.paths.bootstrap_answer),
        },
    )
    assert created.status_code == 201
    session_id = int(created.json()["id"])

    submitted = api_e2e.client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=api_e2e.config_request(),
    )
    assert submitted.status_code == 202
    config_job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert config_job["result"]["outcome"] == "complete"
    api_e2e.bind_and_commit_template(session_id)

    upload_dir = (
        api_e2e.paths.exams_dir
        / f"session_{session_id}"
        / "uploaded_scans"
    )
    upload_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(api_e2e.paths.exams_dir.glob("SYN-*.png")):
        copy2(source, upload_dir / source.name)
    return session_id


@pytest.fixture
def scanned_session_id(
    api_e2e: ApiE2EHarness,
    prepared_session_id: int,
) -> int:
    submitted = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }
    return prepared_session_id
