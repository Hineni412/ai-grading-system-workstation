from __future__ import annotations

from pathlib import Path


def test_run_bat_starts_fastapi_frontend_by_default() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if "%API_PORT%"=="" set "API_PORT=8000"' in content
    assert "-m backend.api.launcher --check-frontend" in content
    assert "-m backend.api.launcher --host 127.0.0.1 --port %API_PORT%" in content
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

    frontend_check = content.index("-m backend.api.launcher --check-frontend")
    gate = content.index("-m backend.ops.offline --apply-pending")
    default_api = content.index("-m backend.api.launcher --host 127.0.0.1")
    assert frontend_check < gate < default_api
    assert "if errorlevel 1 (" in content[frontend_check:gate]
    assert "exit /b 1" in content[frontend_check:gate]
    assert "if errorlevel 1 (" in content[gate:default_api]
    assert "exit /b 1" in content[gate:default_api]


def test_run_bat_uses_portable_runtime_fallback_for_linked_worktrees() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert "Desktop" not in content
    assert 'set "PYTHON_EXE=%~dp0..\\..\\runtime\\python\\python.exe"' in content
