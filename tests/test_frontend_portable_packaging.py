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


@pytest.mark.parametrize("missing", ["dist", "index", "assets", "empty_assets"])
def test_copy_sources_rejects_incomplete_frontend_dist(
    tmp_path: Path,
    missing: str,
) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    source.mkdir()
    package.mkdir()
    dist = source / "frontend" / "dist"
    if missing != "dist":
        dist.mkdir(parents=True)
    if missing != "index" and missing != "dist":
        (dist / "index.html").write_text("<main></main>", encoding="utf-8")
    if missing != "assets" and missing != "dist":
        (dist / "assets").mkdir()

    with pytest.raises(RuntimeError, match="frontend/dist"):
        packager.copy_sources(source, package, "v1.5.0")


def test_packaged_launchers_and_required_helpers_are_copied_from_source(
    tmp_path: Path,
) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    source.mkdir()
    package.mkdir()
    files = {
        Path("运行.bat"): b"@echo off\r\necho start\r\n",
        Path("关闭系统.bat"): b"@echo off\r\necho stop\r\n",
        Path("tools/run_project_module.py"): b"print('run')\n",
        Path("tools/stop_service.ps1"): b"Write-Host 'stop'\r\n",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    packager.write_launchers(source, package)

    for relative, content in files.items():
        assert (package / relative).read_bytes() == content


def test_portable_manifest_excludes_retired_streamlit_ui() -> None:
    packager = _load_packager()

    assert "pages" not in packager.PRODUCTION_DIRS
    assert "pages_shared" not in packager.PRODUCTION_DIRS


def test_private_readme_does_not_offer_retired_streamlit_fallback(
    tmp_path: Path,
) -> None:
    packager = _load_packager()

    packager.write_private_readme(tmp_path, "v1.5.0")
    readmes = list(tmp_path.glob("README_*.md"))
    assert len(readmes) == 1
    content = readmes[0].read_text(encoding="utf-8")

    assert "USE_STREAMLIT" not in content
    assert "START_API" not in content
    assert "Streamlit" not in content
    assert "运行核心测试.bat" not in content
    assert "关闭系统.bat" in content
