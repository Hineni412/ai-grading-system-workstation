from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _client(tmp_path: Path) -> TestClient:
    context = WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": VaultService(context)}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app)


def test_all_daily_vault_routes_are_absent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    post_paths = (
        "/vault/initialize",
        "/vault/pin/initialize",
        "/vault/unlock",
        "/vault/pin/unlock",
        "/vault/recover",
        "/vault/pin/recover",
        "/vault/pin/upgrade",
        "/vault/pin/change",
        "/vault/lock",
        "/vault/touch",
        "/vault/recovery-key/acknowledge",
        "/vault/change-password",
    )

    assert client.get("/api/class-teacher/vault/status").status_code == 404
    for path in post_paths:
        response = client.post(f"/api/class-teacher{path}", json={})
        assert response.status_code == 404, path


def test_openapi_has_no_vault_or_session_header_contract(tmp_path: Path) -> None:
    document = _client(tmp_path).get("/openapi.json").json()
    class_teacher_paths = {
        path: operations
        for path, operations in document["paths"].items()
        if path.startswith("/api/class-teacher")
    }

    assert class_teacher_paths
    assert not any("/vault/" in path for path in class_teacher_paths)
    for operations in class_teacher_paths.values():
        for operation in operations.values():
            parameters = operation.get("parameters", [])
            assert not any(
                str(parameter.get("name", "")).casefold()
                == "x-class-teacher-session"
                for parameter in parameters
            )


def test_service_constructor_has_no_protection_configuration_surface() -> None:
    parameters = inspect.signature(VaultService).parameters

    assert "protection_enabled" not in parameters
    assert "protection_provider" not in parameters


def test_legacy_conversion_and_decryption_dependencies_stay_retired() -> None:
    retired_paths = (
        "backend/class_teacher/legacy_vault_conversion.py",
        "backend/class_teacher/windows_dpapi.py",
        "backend/class_teacher/protection.py",
        "tools/convert_legacy_class_teacher_db.py",
        "tests/class_teacher/legacy_vault_fixture.py",
        "tests/class_teacher/test_legacy_vault_conversion.py",
    )
    assert [
        relative
        for relative in retired_paths
        if (PROJECT_ROOT / relative).exists()
    ] == []

    production_sources = sorted(
        (PROJECT_ROOT / "backend" / "class_teacher").rglob("*.py")
    )
    forbidden_production_markers = (
        "legacy_vault_conversion",
        "windows_dpapi",
        "convert_legacy_class_teacher_db",
        "from cryptography",
        "import cryptography",
    )
    violations = [
        f"{path.relative_to(PROJECT_ROOT).as_posix()}:{marker}"
        for path in production_sources
        for marker in forbidden_production_markers
        if marker in path.read_text(encoding="utf-8")
    ]
    assert violations == []

    current_test = Path(__file__).resolve()
    test_sources = sorted(
        (PROJECT_ROOT / "tests" / "class_teacher").glob("*.py")
    )
    direct_test_imports = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in test_sources
        if path.resolve() != current_test
        and any(
            marker in path.read_text(encoding="utf-8")
            for marker in ("from cryptography", "import cryptography")
        )
    ]
    assert direct_test_imports == []

    requirements = (
        PROJECT_ROOT / "requirements-class-teacher.txt"
    ).read_text(encoding="utf-8")
    active_requirements = [
        line.strip().casefold()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(
        requirement.startswith("cryptography")
        for requirement in active_requirements
    )

    crypto_source = (
        PROJECT_ROOT / "backend" / "class_teacher" / "crypto.py"
    ).read_text(encoding="utf-8")
    assert all(
        marker not in crypto_source
        for marker in (
            "AESGCM",
            "Scrypt",
            "derive_key",
            "open_sealed",
            "unb64",
            "wipe",
        )
    )
