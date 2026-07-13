from __future__ import annotations

from pathlib import Path

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
