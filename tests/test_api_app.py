from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


def test_fastapi_runtime_dependency_is_available() -> None:
    import fastapi

    assert fastapi.__version__ == "0.139.0"


from fastapi.testclient import TestClient


def test_healthz_returns_local_api_status() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.json() == {
        "status": "ok",
        "service": "ai-grading-api",
        "version": "v1.5.0",
    }


def test_api_healthz_alias_matches_root_healthz() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    assert client.get("/api/healthz").json() == client.get("/healthz").json()


def test_api_error_uses_unified_error_body() -> None:
    from backend.api.app import ApiError, create_app

    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise ApiError(409, "demo_conflict", "演示冲突", {"field": "name"})

    client = TestClient(app)

    response = client.get("/boom", headers={"x-request-id": "rid-test"})

    assert response.status_code == 409
    assert response.headers["x-request-id"] == "rid-test"
    assert response.json() == {
        "error": {
            "code": "demo_conflict",
            "message": "演示冲突",
            "details": {"field": "name"},
            "request_id": "rid-test",
        }
    }
