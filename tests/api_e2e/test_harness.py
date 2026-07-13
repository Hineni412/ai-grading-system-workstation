from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.api_e2e import conftest as e2e_conftest
from tests.api_e2e.harness import (
    ApiE2EHarness,
    E2EControls,
    build_paths,
)


class _JobResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _SingleResponseClient:
    def __init__(self, response: _JobResponse) -> None:
        self.response = response

    def get(self, path: str) -> _JobResponse:
        assert path == "/api/jobs/1"
        return self.response


class _ShutdownSpy:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_poll_job_rejects_nested_data_root_hidden_by_json_escaping(tmp_path) -> None:
    paths = build_paths(tmp_path)
    payload = {
        "id": 1,
        "job_type": "config_generation",
        "payload": {},
        "result": {"nested": {"path": str(paths.data_root / "private.json")}},
        "status": "succeeded",
        "progress": 1.0,
        "stage": "complete",
        "detail": "",
        "error": None,
        "cancel_requested": False,
        "created_at": "2026-07-14T00:00:00",
        "started_at": "2026-07-14T00:00:00",
        "updated_at": "2026-07-14T00:00:01",
        "finished_at": "2026-07-14T00:00:01",
    }
    response = _JobResponse(payload)
    assert str(paths.data_root) not in response.text
    client = _SingleResponseClient(response)
    harness = ApiE2EHarness(client, None, None, paths, E2EControls())

    with pytest.raises(AssertionError, match="temporary data root"):
        harness.poll_job(1, "succeeded")


def test_api_e2e_fixture_shuts_manager_down_when_app_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _ShutdownSpy()
    monkeypatch.setattr(
        e2e_conftest,
        "build_job_manager",
        lambda _paths, *, controls: manager,
    )

    def fail_app_creation() -> None:
        raise RuntimeError("synthetic app creation failure")

    monkeypatch.setattr(e2e_conftest, "create_app", fail_app_creation)
    fixture = e2e_conftest.api_e2e.__wrapped__(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="synthetic app creation failure"):
        next(fixture)

    assert manager.shutdown_calls == 1
