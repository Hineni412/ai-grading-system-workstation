from __future__ import annotations

from pathlib import Path


def test_run_bat_starts_fastapi_frontend_by_default() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if "%API_PORT%"=="" set "API_PORT=8000"' in content
    assert 'if "%USE_STREAMLIT%"=="" set "USE_STREAMLIT=0"' in content
    assert 'if "%START_API%"=="" set "START_API=1"' in content
    assert "-m backend.api.launcher --check-frontend" in content
    assert "-m backend.api.launcher --host 127.0.0.1 --port %API_PORT%" in content
    assert "http://127.0.0.1:%API_PORT%/" in content


def test_run_bat_keeps_explicit_streamlit_fallback_for_one_version() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if /I "%USE_STREAMLIT%"=="1" goto streamlit_fallback' in content
    fallback = content.index(":streamlit_fallback")
    assert 'if /I not "%START_API%"=="0"' in content[fallback:]
    assert (
        "backend.api.app:app --host 127.0.0.1 --port %API_PORT%"
        in content[fallback:]
    )
    assert (
        "streamlit run web_app.py --server.address 127.0.0.1 --server.port %PORT%"
        in content[fallback:]
    )


def test_run_bat_applies_pending_ops_before_starting_any_server() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    frontend_check = content.index("-m backend.api.launcher --check-frontend")
    gate = content.index("-m backend.ops.offline --apply-pending")
    default_api = content.index("-m backend.api.launcher --host 127.0.0.1")
    fallback = content.index(":streamlit_fallback")
    assert frontend_check < gate < default_api < fallback
    assert "if errorlevel 1 (" in content[frontend_check:gate]
    assert "exit /b 1" in content[frontend_check:gate]
    assert "if errorlevel 1 (" in content[gate:default_api]
    assert "exit /b 1" in content[gate:default_api]


def test_run_bat_uses_portable_runtime_fallback_for_linked_worktrees() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert "Desktop" not in content
    assert 'set "PYTHON_EXE=%~dp0..\\..\\runtime\\python\\python.exe"' in content
