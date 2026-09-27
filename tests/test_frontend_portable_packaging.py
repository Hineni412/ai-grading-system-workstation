from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_packager():
    path = ROOT / "package_v1.5.0.py"
    spec = importlib.util.spec_from_file_location("portable_packager", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copy_sources_carries_only_built_frontend_assets(tmp_path: Path) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    package.mkdir()
    (source / "frontend" / "dist" / "assets").mkdir(parents=True)
    (source / "frontend" / "src").mkdir(parents=True)
    (source / "frontend" / "node_modules").mkdir(parents=True)
    (source / "backend" / "api").mkdir(parents=True)
    (source / "backend" / "api" / "app.py").write_text(
        "APP_READY = True\n", encoding="utf-8"
    )
    (source / "frontend" / "dist" / "index.html").write_text(
        "<div id='app'></div>", encoding="utf-8"
    )
    (source / "frontend" / "dist" / "assets" / "app.js").write_text(
        "console.log('ready')", encoding="utf-8"
    )
    (source / "frontend" / "src" / "main.ts").write_text(
        "throw new Error('source noise')", encoding="utf-8"
    )
    (source / "frontend" / "node_modules" / "noise.js").write_text(
        "dependency noise", encoding="utf-8"
    )

    stats = packager.copy_sources(source, package, "v1.5.0")

    assert stats["frontend_dist_files"] == 2
    assert (package / "frontend" / "dist" / "index.html").is_file()
    assert (package / "frontend" / "dist" / "assets" / "app.js").is_file()
    assert (package / "backend" / "api" / "app.py").is_file()
    assert not (package / "frontend" / "src").exists()
    assert not (package / "frontend" / "node_modules").exists()
    assert not (package / "pages").exists()


def test_packaged_launcher_accepts_prebuilt_frontend_without_source(
    tmp_path: Path,
) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    package.mkdir()
    (source / "frontend" / "dist" / "assets").mkdir(parents=True)
    (source / "frontend" / "dist" / "index.html").write_text(
        "<main></main>", encoding="utf-8"
    )
    (source / "frontend" / "dist" / "assets" / "app.js").write_text(
        "console.log('ready')", encoding="utf-8"
    )
    launcher_files = {
        Path("运行.bat"): (ROOT / "运行.bat").read_bytes(),
        Path("关闭系统.bat"): b"@echo off\r\nexit /b 0\r\n",
        Path("tools/run_project_module.py"): b"raise SystemExit(0)\n",
        Path("tools/stop_service.ps1"): b"exit 0\r\n",
    }
    for relative, content in launcher_files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    packager.copy_sources(source, package, "v1.5.0")
    packager.write_launchers(source, package)

    assert not (package / "frontend" / "package.json").exists()
    launcher = (package / "运行.bat").read_text(encoding="utf-8")
    assert 'if not exist "%FRONTEND_DIR%\\package.json" goto frontend_ready' in launcher
    assert ":frontend_ready" in launcher
