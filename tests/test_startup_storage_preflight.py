from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.startup_storage_preflight import (
    TaxonomyStoragePreflightError,
    preflight_taxonomy_storage,
)
from tools import start_p3_5_service


def test_detached_helper_prefers_the_owning_worktree_with_portable_python() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path("tools/start_p3_5_service.py").resolve()),
            "--help",
        ],
        cwd=Path.cwd().parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Start the P3.5 API" in completed.stdout


def test_preflight_preserves_existing_taxonomy_files_and_removes_probe(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "config" / "taxonomy_state_v2.json"
    state_path.parent.mkdir(parents=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    receipt_path = state_path.with_name(
        f"{state_path.stem}.review_receipts{state_path.suffix}"
    )
    originals = {
        state_path: b'{"revision": 7}\n',
        lock_path: b"\0",
        receipt_path: b'{"schema_version": 1}\n',
    }
    for path, content in originals.items():
        path.write_bytes(content)

    result = preflight_taxonomy_storage(state_path)

    assert result.state_path == state_path.resolve()
    assert set(result.existing_files_checked) == set(originals)
    assert {path: path.read_bytes() for path in originals} == originals
    assert set(state_path.parent.iterdir()) == set(originals)


def test_preflight_reports_existing_state_that_cannot_be_opened_for_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "config" / "taxonomy_state_v2.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"revision": 7}\n', encoding="utf-8")
    real_open = os.open

    def deny_state_write(path, flags, *args, **kwargs):
        if Path(path) == state_path and flags & os.O_RDWR:
            raise PermissionError("simulated read-only taxonomy state")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", deny_state_write)

    with pytest.raises(TaxonomyStoragePreflightError) as captured:
        preflight_taxonomy_storage(state_path)

    assert captured.value.reason == "existing_file_not_writable"
    assert captured.value.failing_path == state_path
    assert "taxonomy_state_v2.json" in captured.value.user_message
    assert state_path.read_text(encoding="utf-8") == '{"revision": 7}\n'


def test_preflight_reports_rename_denial_and_cleans_created_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "config" / "taxonomy_state_v2.json"
    real_replace = os.replace

    def deny_probe_rename(source, destination):
        if Path(source).name.startswith(".taxonomy-storage-preflight-"):
            raise PermissionError("simulated rename denial")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", deny_probe_rename)

    with pytest.raises(TaxonomyStoragePreflightError) as captured:
        preflight_taxonomy_storage(state_path)

    assert captured.value.reason == "probe_rename_failed"
    assert list(state_path.parent.glob(".taxonomy-storage-preflight-*")) == []


def test_detached_helper_returns_stable_storage_reason_before_process_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path = tmp_path / "taxonomy_state_v2.json"
    failure = TaxonomyStoragePreflightError(
        state_path=state_path,
        failing_path=state_path,
        reason="existing_file_not_writable",
        cause=PermissionError("simulated restricted launcher"),
    )
    monkeypatch.setattr(start_p3_5_service, "_listener_exists", lambda _port: False)
    monkeypatch.setattr(
        start_p3_5_service,
        "_taxonomy_state_path",
        lambda: state_path,
    )
    monkeypatch.setattr(
        start_p3_5_service,
        "preflight_taxonomy_storage",
        lambda _path: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        start_p3_5_service.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("API process must not start"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["start_p3_5_service.py", "--port", "8035"],
    )

    assert start_p3_5_service.main() == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["started"] is False
    assert payload["reason"] == "taxonomy_storage_unwritable"
    assert payload["storage_path"] == str(state_path)
