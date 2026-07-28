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


def _set_front_path(seed, path_value: object) -> None:
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers
            SET front_image = ?
            WHERE id = (SELECT paper_id FROM session_results WHERE id = ?)
            """,
            (str(path_value), seed.result_id),
        )
        conn.commit()


def test_media_routes_stream_original_annotated_and_crop(media_client) -> None:
    client, seed, image_factory = media_client
    annotated_front = (
        seed.annotated_dir
        / f"session_{seed.session_id}"
        / f"result_{seed.result_id}_front.jpg"
    )
    annotated_back = (
        seed.annotated_dir
        / f"session_{seed.session_id}"
        / f"result_{seed.result_id}_back.jpg"
    )
    image_factory(annotated_front, color=(240, 220, 220))
    image_factory(annotated_back, color=(220, 240, 220))
    seed.db.upsert_annotated_result(
        seed.session_id,
        seed.result_id,
        str(annotated_front),
        str(annotated_back),
    )

    original = client.get(
        f"/api/sessions/{seed.session_id}/results/{seed.result_id}/pages/front"
    )
    annotated = client.get(
        (
            f"/api/sessions/{seed.session_id}/results/{seed.result_id}"
            "/pages/back?variant=annotated"
        )
    )
    crop = client.get(
        (
            f"/api/sessions/{seed.session_id}/results/{seed.result_id}"
            f"/details/{seed.detail_id}/crop"
        )
    )

    assert original.status_code == 200
    assert original.headers["content-type"] == "image/jpeg"
    assert original.headers["cache-control"] == "no-store"
    assert original.content.startswith(b"\xff\xd8")
    assert annotated.status_code == 200
    assert annotated.headers["content-type"] == "image/jpeg"
    assert annotated.headers["cache-control"] == "no-store"
    assert crop.status_code == 200
    assert crop.headers["content-type"] == "image/jpeg"
    assert crop.headers["cache-control"] == "no-store"
    assert crop.content.startswith(b"\xff\xd8")


@pytest.mark.parametrize("offset_field", ["session", "result", "detail"])
def test_media_crop_rejects_wrong_ownership(media_client, offset_field: str) -> None:
    client, seed, _image_factory = media_client
    session_id = seed.session_id + int(offset_field == "session")
    result_id = seed.result_id + int(offset_field == "result")
    detail_id = seed.detail_id + int(offset_field == "detail")

    response = client.get(
        (
            f"/api/sessions/{session_id}/results/{result_id}"
            f"/details/{detail_id}/crop"
        ),
        headers={"x-request-id": "rid-media-owner"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_not_found"
    assert response.json()["error"]["request_id"] == "rid-media-owner"
    assert response.headers["cache-control"] == "no-store"


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


def test_media_route_rejects_absolute_path_outside_root(media_client, tmp_path: Path) -> None:
    client, seed, image_factory = media_client
    outside = tmp_path / "outside.jpg"
    image_factory(outside)
    _set_front_path(seed, outside)

    response = client.get(
        f"/api/sessions/{seed.session_id}/results/{seed.result_id}/pages/front"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "media_path_forbidden"
    assert str(outside) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_media_route_rejects_relative_path_traversal(media_client) -> None:
    client, seed, image_factory = media_client
    outside = seed.data_root / "outside.jpg"
    image_factory(outside)
    _set_front_path(seed, "exams/../outside.jpg")

    response = client.get(
        f"/api/sessions/{seed.session_id}/results/{seed.result_id}/pages/front"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "media_path_forbidden"
    assert str(outside) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_media_route_rejects_disallowed_extension(media_client) -> None:
    client, seed, _image_factory = media_client
    unsafe = seed.exams_dir / "session_1" / "front.svg"
    unsafe.write_text("<svg/>", encoding="utf-8")
    _set_front_path(seed, unsafe)

    response = client.get(
        f"/api/sessions/{seed.session_id}/results/{seed.result_id}/pages/front"
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "media_type_not_supported"
    assert str(unsafe) not in str(response.json())
    assert response.headers["cache-control"] == "no-store"


def test_preflight_crop_route_uses_the_owned_source_region(media_client) -> None:
    from backend.api.dependencies import get_scan_grading_workspace

    client, seed, _image_factory = media_client
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            """
            UPDATE answer_regions
            SET mapped_question_id = 'Q1(1)'
            WHERE session_id = ?
            """,
            (seed.session_id,),
        )
        region_id = int(
            conn.execute(
                "SELECT id FROM answer_regions WHERE session_id = ?",
                (seed.session_id,),
            ).fetchone()[0]
        )
        conn.commit()

    class FixtureWorkspace:
        def resolve_preflight_media(
            self,
            requested_session_id: int,
            media_id: str,
        ) -> Path:
            assert requested_session_id == seed.session_id
            return seed.back_path if media_id.endswith(":back") else seed.front_path

    client.app.dependency_overrides[get_scan_grading_workspace] = (
        lambda: FixtureWorkspace()
    )
    try:
        response = client.get(
            (
                f"/api/sessions/{seed.session_id}/review/preflight/"
                f"group/group-1/Q1(P1)/crop?source_region_id={region_id}"
            )
        )
        missing = client.get(
            (
                f"/api/sessions/{seed.session_id}/review/preflight/"
                "group/group-1/Q1(P1)/crop?source_region_id=999999"
            )
        )
    finally:
        client.app.dependency_overrides.pop(get_scan_grading_workspace, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\xff\xd8")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "media_not_found"
    assert missing.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "url",
    [
        "/api/sessions/1/results/1/pages/sideways",
        "/api/sessions/1/results/1/pages/front?variant=raw-path",
    ],
)
def test_media_route_rejects_non_enum_page_or_variant(media_client, url: str) -> None:
    client, _seed, _image_factory = media_client

    response = client.get(url)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
