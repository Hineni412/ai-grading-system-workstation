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
from pydantic import BaseModel


class ValidationRequestBody(BaseModel):
    worker_count: int


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


def test_healthz_identifies_an_opted_in_preview_process(monkeypatch) -> None:
    from backend.api.app import create_app

    monkeypatch.setenv(
        "AI_GRADING_PREVIEW_INSTANCE_ID",
        "teacher-platform-integration",
    )
    monkeypatch.setenv("AI_GRADING_PREVIEW_HEAD", "a72e32d1" + "0" * 32)
    client = TestClient(create_app())

    assert client.get("/api/healthz").json() == {
        "status": "ok",
        "service": "ai-grading-api",
        "version": "v1.5.0",
        "preview_instance_id": "teacher-platform-integration",
        "preview_head": "a72e32d1" + "0" * 32,
    }


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


def test_validation_error_does_not_reflect_invalid_request_values() -> None:
    from backend.api.app import create_app

    app = create_app()

    @app.post("/validation-input")
    def validate_request(body: ValidationRequestBody) -> dict[str, int]:
        return {"worker_count": body.worker_count}

    client = TestClient(app)
    for invalid_value in (
        {"accessToken": "secret-validation-token"},
        r"C:\\private\\workers",
    ):
        response = client.post("/validation-input", json={"worker_count": invalid_value})

        assert response.status_code == 422
        assert "secret-validation-token" not in response.text
        assert r"C:\\private\\workers" not in response.text
        errors = response.json()["error"]["details"]["errors"]
        assert len(errors) == 1
        assert set(errors[0]) <= {"loc", "type"}
        assert errors[0]["loc"] == ["body", "worker_count"]


def test_retired_class_teacher_has_no_routes_or_services():
    from backend.api.app import create_app
    app = create_app()
    assert app.state.workspace_registry.enabled_features == ()
    assert not any(getattr(route, "path", "").startswith("/api/class-teacher") for route in app.routes)
    assert not any(getattr(route, "path", "") == "/api/ai-diagnostics/class-teacher" for route in app.routes)
    client = TestClient(app)
    assert client.get("/api/class-teacher/status").status_code == 404
