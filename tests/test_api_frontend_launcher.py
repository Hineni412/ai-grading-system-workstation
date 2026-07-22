from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace


class _Server:
    started = False
    should_exit = False


def test_browser_opens_only_after_server_reports_started() -> None:
    from backend.api.launcher import open_browser_when_started

    server = _Server()
    events: list[str] = []

    def sleep(_seconds: float) -> None:
        events.append("waited")
        server.started = True

    opened = open_browser_when_started(
        server,
        "http://127.0.0.1:8123/",
        browser_open=lambda url: events.append(url) or True,
        sleep=sleep,
        timeout_seconds=1.0,
    )

    assert opened is True
    assert events == ["waited", "http://127.0.0.1:8123/"]


def test_browser_failure_does_not_request_server_exit() -> None:
    from backend.api.launcher import open_browser_when_started

    server = _Server()
    server.started = True

    def fail_browser(_url: str) -> bool:
        raise OSError("synthetic browser failure")

    opened = open_browser_when_started(
        server,
        "http://127.0.0.1:8123/",
        browser_open=fail_browser,
        timeout_seconds=1.0,
    )

    assert opened is False
    assert server.should_exit is False


def test_browser_is_not_opened_when_server_exits_during_startup() -> None:
    from backend.api.launcher import open_browser_when_started

    server = _Server()
    opened_urls: list[str] = []

    def sleep(_seconds: float) -> None:
        server.should_exit = True

    opened = open_browser_when_started(
        server,
        "http://127.0.0.1:8123/",
        browser_open=lambda url: opened_urls.append(url) or True,
        sleep=sleep,
        timeout_seconds=1.0,
    )

    assert opened is False
    assert opened_urls == []


def test_launcher_returns_failure_when_server_never_starts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from backend.api import launcher

    class FailingServer:
        started = False
        should_exit = True

        def run(self) -> None:
            return None

    monkeypatch.setattr(
        launcher,
        "get_path_manager",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    monkeypatch.setattr(
        launcher,
        "validate_frontend_dist",
        lambda _path: tmp_path / "frontend" / "dist",
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        types.SimpleNamespace(
            Config=lambda *_args, **_kwargs: object(),
            Server=lambda _config: FailingServer(),
        ),
    )

    assert launcher.main(["--no-browser"]) == 1
