from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import uvicorn

from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _app(tmp_path: Path) -> FastAPI:
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    service.ensure_plaintext_ready()
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return app


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_request(
    url: str,
    *,
    timeout: float = 5,
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        headers={"content-type": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_three_plaintext_reads_complete_without_session_or_worker_starvation(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}/api/class-teacher"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="error",
            timeout_graceful_shutdown=1,
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    try:
        paths = (
            "/support/directory",
            "/work?view=week",
            "/sop/affairs",
        )

        def read_status(path: str) -> tuple[str, int | str]:
            try:
                status_code, _payload = _json_request(
                    f"{base_url}{path}",
                    timeout=2,
                )
                return path, status_code
            except Exception as exc:
                return path, type(exc).__name__

        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(read_status, paths))

        assert results == [(path, 200) for path in paths]
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()
