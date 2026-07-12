from __future__ import annotations

from pathlib import Path


def test_run_bat_starts_api_and_streamlit_by_default() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if "%API_PORT%"=="" set "API_PORT=8000"' in content
    assert 'if "%START_API%"=="" set "START_API=1"' in content
    assert "backend.api.app:app --host 127.0.0.1 --port %API_PORT%" in content
    assert 'if /I not "%START_API%"=="0"' in content
    assert "streamlit run web_app.py --server.address 127.0.0.1 --server.port %PORT%" in content


def test_run_bat_applies_pending_ops_before_starting_any_server() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    gate = content.index("-m backend.ops.offline --apply-pending")
    api = content.index("-m uvicorn backend.api.app:app")
    browser = content.index('start "" "http://127.0.0.1:%PORT%"')
    streamlit = content.index("-m streamlit run web_app.py")
    assert gate < api < browser < streamlit
    assert "if errorlevel 1 (" in content[gate:api]
    assert "exit /b 1" in content[gate:api]


def test_run_bat_uses_portable_runtime_fallback_for_linked_worktrees() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert "Desktop" not in content
    assert 'set "PYTHON_EXE=%~dp0..\\..\\runtime\\python\\python.exe"' in content
