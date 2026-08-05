from __future__ import annotations

from pathlib import Path


def test_run_bat_starts_fastapi_frontend_by_default() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if "%API_PORT%"=="" set "API_PORT=8035"' in content
    assert '"%PROJECT_RUNNER%" backend.api.launcher --check-frontend' in content
    assert (
        '"%PROJECT_RUNNER%" backend.api.launcher '
        "--host 127.0.0.1 --port %API_PORT%"
    ) in content
    assert "http://127.0.0.1:%API_PORT%/" in content


def test_run_bat_has_no_retired_streamlit_fallback() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert "USE_STREAMLIT" not in content
    assert "START_API" not in content
    assert "streamlit_fallback" not in content
    assert "streamlit run" not in content
    assert "web_app.py" not in content
    assert "8501" not in content


def test_run_bat_applies_pending_ops_before_starting_any_server() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    storage_preflight = content.index(
        '"%PROJECT_RUNNER%" backend.startup_storage_preflight'
    )
    frontend_check = content.index(
        '"%PROJECT_RUNNER%" backend.api.launcher --check-frontend'
    )
    gate = content.index('"%PROJECT_RUNNER%" backend.ops.offline --apply-pending')
    default_api = content.index(
        '"%PROJECT_RUNNER%" backend.api.launcher --host 127.0.0.1'
    )
    assert storage_preflight < frontend_check < gate < default_api
    assert "if errorlevel 1 goto taxonomy_storage_error" in content[
        storage_preflight:frontend_check
    ]
    assert "if errorlevel 1 goto frontend_error" in content[frontend_check:gate]
    assert "if errorlevel 1 goto offline_error" in content[gate:default_api]


def test_run_bat_uses_portable_runtime_fallback_for_linked_worktrees() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert "Desktop" not in content
    assert 'set "PYTHON_EXE=%~dp0..\\..\\runtime\\python\\python.exe"' in content


def test_run_bat_checks_the_port_with_portable_python() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    port_check = content.index('"%PYTHON_EXE%" -c "import socket, sys;')
    storage_preflight = content.index(
        '"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.startup_storage_preflight'
    )
    assert port_check < storage_preflight
    assert "connect_ex(('127.0.0.1', int(sys.argv[1])))" in content
    assert "WindowsPowerShell" not in content
    assert "P3.5" not in content


def test_shutdown_bat_requires_powershell_7_and_generic_stop_helper() -> None:
    content = Path("关闭系统.bat").read_text(encoding="utf-8")

    assert 'set "STOP_SCRIPT=%~dp0tools\\stop_service.ps1"' in content
    assert 'set "PWSH_EXE=C:\\Program Files\\PowerShell\\7\\pwsh.exe"' in content
    assert "where pwsh.exe" in content
    assert "PowerShell 7 is required" in content
    assert "WindowsPowerShell" not in content
    assert "P3.5" not in content


def test_stop_helper_keeps_identity_checks_under_generic_name() -> None:
    content = Path("tools/stop_service.ps1").read_text(encoding="utf-8")

    assert "function Test-ServiceLauncherProcess" in content
    assert content.count("Test-ServiceLauncherProcess") == 3
    assert "Get-LoopbackListenerProcessIds" in content
    assert "CreationDate" in content
    assert "Stop-Process -Id $ownerProcessId" in content
    assert "P3.5" not in content
