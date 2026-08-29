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
    assert (
        'if not exist "%FRONTEND_DIR%\\package.json" goto frontend_ready'
        in launcher
    )
    assert ":frontend_ready" in launcher


def test_portable_manifest_excludes_retired_streamlit_ui() -> None:
    packager = _load_packager()

    assert "pages" not in packager.PRODUCTION_DIRS
    assert "pages_shared" not in packager.PRODUCTION_DIRS


def test_packager_uses_current_root_readme_only() -> None:
    packager = _load_packager()

    assert "README.md" in packager.ROOT_INCLUDE_EXACT
    assert "README_工作机使用说明.md" not in packager.ROOT_INCLUDE_EXACT
    assert packager._should_copy_root_file(Path("README.md"))
    assert not packager._should_copy_root_file(Path("README_旧说明.md"))


def test_copy_private_user_data_carries_full_data(tmp_path: Path) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    package.mkdir()
    user_data = source / "user_data"
    (user_data / "config").mkdir(parents=True)
    (user_data / "workspaces" / "class-teacher").mkdir(parents=True)
    (user_data / "databases").mkdir(parents=True)
    (user_data / "__pycache__").mkdir(parents=True)
    (user_data / "config" / "api_profiles.json").write_text("{}", encoding="utf-8")
    (user_data / "workspaces" / "class-teacher" / "data.db").write_bytes(b"x")
    (user_data / "databases" / "grading_system.db").write_bytes(b"x")
    (user_data / "__pycache__" / "junk.pyc").write_bytes(b"x")

    packager.copy_private_user_data(source, package)

    dst = package / "user_data"
    assert (dst / "config" / "api_profiles.json").is_file()
    assert (dst / "workspaces" / "class-teacher" / "data.db").is_file()
    assert (dst / "databases" / "grading_system.db").is_file()
    assert not (dst / "__pycache__").exists()


def test_copy_runtime_extras_copies_bundled_assets(tmp_path: Path) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    package.mkdir()
    (source / "runtime" / "models" / "asr").mkdir(parents=True)
    (source / "runtime" / "models" / "asr" / "model.onnx").write_bytes(b"m")
    (source / "runtime" / "tectonic").mkdir(parents=True)
    (source / "runtime" / "tectonic" / "tectonic.exe").write_bytes(b"t")
    (source / "runtime" / "python").mkdir(parents=True)
    (source / "runtime" / "python" / "python.exe").write_bytes(b"p")

    extras = packager.copy_runtime_extras(source, package)

    assert extras == ["models", "tectonic"]
    assert (package / "runtime" / "models" / "asr" / "model.onnx").is_file()
    assert (package / "runtime" / "tectonic" / "tectonic.exe").is_file()
    assert not (package / "runtime" / "python").exists()


def test_copy_runtime_extras_skips_missing_assets(tmp_path: Path) -> None:
    packager = _load_packager()
    source = tmp_path / "source"
    package = tmp_path / "package"
    package.mkdir()
    (source / "runtime" / "models").mkdir(parents=True)
    (source / "runtime" / "models" / "model.onnx").write_bytes(b"m")

    extras = packager.copy_runtime_extras(source, package)

    assert extras == ["models"]
    assert not (package / "runtime" / "tectonic").exists()


def test_packager_no_longer_builds_zip_archive() -> None:
    packager = _load_packager()

    assert not hasattr(packager, "make_archive")


def test_packager_excludes_itself_from_packages() -> None:
    packager = _load_packager()

    assert "package_v1.5.0.py" in packager.ROOT_EXCLUDE_EXACT
    assert not packager._should_copy_root_file(Path("package_v1.5.0.py"))
