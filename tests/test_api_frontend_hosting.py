from __future__ import annotations

from pathlib import Path

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
