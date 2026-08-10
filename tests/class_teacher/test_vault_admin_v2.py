from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> VaultService:
    return VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )


def test_plaintext_ready_requires_no_credential_or_session(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert list(inspect.signature(service.ensure_plaintext_ready).parameters) == []
    assert service.ensure_plaintext_ready() == service.ensure_plaintext_ready()


def test_expired_draft_cleanup_moves_to_throttled_plaintext_maintenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[bytes] = []
    monkeypatch.setattr(
        service,
        "_cleanup_expired_drafts",
        lambda key: calls.append(key),
    )

    service.ensure_plaintext_ready()
    service.ensure_plaintext_ready()
    assert len(calls) == 1

    service._last_cleanup -= service._CLEANUP_INTERVAL_SECONDS + 1
    service.ensure_plaintext_ready()
    assert len(calls) == 2
