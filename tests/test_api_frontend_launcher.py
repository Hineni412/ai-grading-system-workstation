from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_normal_launcher_emits_request_timing_without_request_content(tmp_path, monkeypatch, capsys):
    import uvicorn
    from backend.api import launcher
    from backend.api.app import create_app

    paths = SimpleNamespace(project_root=tmp_path, version="TEST-version")
    monkeypatch.setattr(launcher, "get_path_manager", lambda: paths)
    monkeypatch.setattr(launcher, "validate_frontend_dist", lambda *_: None)
    monkeypatch.setattr(launcher, "ensure_application_schema", lambda *_: None)
    configs = []

    class Server:
        started = True
        def __init__(self, config):
            configs.append(config)
        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", Server)
    names = ("ai_grading.api", "uvicorn", "uvicorn.error", "uvicorn.access")
    previous = {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers),
                       logging.getLogger(name).propagate) for name in names}
    try:
        assert launcher.main(["--port", "8035", "--no-browser"]) == 0
        assert len(configs) == 1
        app = create_app(path_manager=paths)
        response = TestClient(app).get("/api/healthz?secret=TEST-private-query", headers={"x-request-id": "TEST-timing"})
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "TEST-timing"
        output = capsys.readouterr().err
        assert "api_request method=GET path=/api/healthz request_id=TEST-timing elapsed_ms=" in output
        assert "TEST-private-query" not in output
        assert "TEST-version" not in output
        assert "ai_grading.api" not in uvicorn.config.LOGGING_CONFIG["loggers"]
    finally:
        for name, (level, handlers, propagate) in previous.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.handlers = handlers
            logger.propagate = propagate
