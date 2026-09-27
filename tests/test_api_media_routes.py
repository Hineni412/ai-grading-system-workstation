from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


@pytest.fixture
def media_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_media_service,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from tests.test_review_media_service import _image, _seed_media

    seed = _seed_media(tmp_path)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: seed.db
    app.dependency_overrides[get_media_service] = lambda: seed.service
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, seed, _image
        finally:
            manager.shutdown()


def test_media_route_returns_expired_without_leaking_deleted_path(media_client) -> None:
    client, seed, _image_factory = media_client
    seed.front_path.unlink()

    response = client.get(
        f"/api/sessions/{seed.session_id}/results/{seed.result_id}/pages/front"
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "media_expired"
    assert str(seed.front_path) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"
