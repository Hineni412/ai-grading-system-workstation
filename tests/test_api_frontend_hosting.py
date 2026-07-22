from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.frontend import mount_frontend


def _write_dist(root: Path) -> Path:
    dist = root / "frontend" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><main id='app'>P2-21 SPA</main>",
        encoding="utf-8",
    )
    (assets / "index-a1b2c3.js").write_text(
        "console.log('p2-21')",
        encoding="utf-8",
    )
    return dist


def test_frontend_root_and_deep_routes_serve_non_cached_index(tmp_path: Path) -> None:
    app = FastAPI()
    mount_frontend(app, _write_dist(tmp_path))
    client = TestClient(app)

    for path in ("/", "/grading?session=1", "/settings/ops"):
        response = client.get(path)
        assert response.status_code == 200
        assert "P2-21 SPA" in response.text
        assert response.headers["cache-control"] == "no-store"


def test_hashed_assets_are_immutable_and_missing_assets_are_not_spa(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    mount_frontend(app, _write_dist(tmp_path))
    client = TestClient(app)

    asset = client.get("/assets/index-a1b2c3.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    missing = client.get("/assets/missing-a1b2c3.js")
    assert missing.status_code == 404
    assert "P2-21 SPA" not in missing.text


def test_unknown_api_route_is_not_swallowed_by_spa_fallback(tmp_path: Path) -> None:
    app = FastAPI()
    mount_frontend(app, _write_dist(tmp_path))
    client = TestClient(app)

    response = client.get("/api/not-a-real-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}
    assert "P2-21 SPA" not in response.text
    assert "/{frontend_path}" not in app.openapi()["paths"]


def test_missing_frontend_dist_has_safe_non_cached_message(tmp_path: Path) -> None:
    app = FastAPI()
    missing_dist = tmp_path / "private" / "frontend" / "dist"
    mount_frontend(app, missing_dist)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "前端文件缺失" in response.text
    assert str(tmp_path) not in response.text
    assert client.get("/api/not-a-real-route").status_code == 404


def test_empty_frontend_assets_are_treated_as_incomplete(tmp_path: Path) -> None:
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>empty</main>", encoding="utf-8")

    app = FastAPI()
    mount_frontend(app, dist)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 503
    assert "前端文件缺失" in response.text


def test_create_app_mounts_frontend_after_real_api_routes(tmp_path: Path) -> None:
    from backend.api.app import create_app

    _write_dist(tmp_path)
    paths = SimpleNamespace(project_root=tmp_path, version="v-test")
    client = TestClient(create_app(path_manager=paths))

    assert client.get("/").status_code == 200
    assert client.get("/workbench").status_code == 200
    assert client.get("/api/healthz").json()["version"] == "v-test"
    unknown_api = client.get("/api/not-a-real-route")
    assert unknown_api.status_code == 404
    assert unknown_api.headers["content-type"].startswith("application/json")
