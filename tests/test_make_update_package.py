from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_make_update():
    return _load_module("make_update_tool", "update_tools/make_update.py")


def _load_apply_update():
    return _load_module("apply_update_tool", "update_tools/apply_update.py")


def _build_fake_source(source: Path) -> None:
    (source / "frontend" / "dist" / "assets").mkdir(parents=True)
    (source / "frontend" / "dist" / "index.html").write_text(
        "<main></main>", encoding="utf-8"
    )
    (source / "frontend" / "dist" / "assets" / "app.js").write_text(
        "console.log('ready')", encoding="utf-8"
    )
    (source / "backend" / "api").mkdir(parents=True)
    (source / "backend" / "api" / "app.py").write_text(
        "APP_READY = True\n", encoding="utf-8"
    )
    (source / "migrations" / "grading").mkdir(parents=True)
    (source / "migrations" / "grading" / "0001_init.sql").write_text(
        "CREATE TABLE demo (id INTEGER);\n", encoding="utf-8"
    )
    (source / "update_tools").mkdir()
    (source / "update_tools" / "apply_update.py").write_text(
        "# tool\n", encoding="utf-8"
    )
    (source / "runtime" / "models" / "asr").mkdir(parents=True)
    (source / "runtime" / "models" / "asr" / "model.onnx").write_bytes(b"m")
    (source / "runtime" / "tectonic").mkdir(parents=True)
    (source / "runtime" / "tectonic" / "tectonic.exe").write_bytes(b"t")
    (source / "user_data" / "databases").mkdir(parents=True)
    (source / "user_data" / "databases" / "grading_system.db").write_bytes(b"x")
    launcher_files = {
        Path("运行.bat"): b"@echo off\r\n",
        Path("关闭系统.bat"): b"@echo off\r\n",
        Path("tools/run_project_module.py"): b"print('run')\n",
        Path("tools/stop_service.ps1"): b"exit 0\r\n",
    }
    for relative, content in launcher_files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_make_update_package_builds_apply_update_layout(tmp_path: Path) -> None:
    make_update = _load_make_update()
    packager = make_update.load_packager()
    source = tmp_path / "演示项目_v9.9"
    source.mkdir()
    output_dir = tmp_path / "dist"
    _build_fake_source(source)

    update_dir = make_update.make_update_package(
        source, output_dir, "v9.9", packager=packager
    )

    assert update_dir.name == "演示项目_update_v9.9"
    manifest = json.loads(
        (update_dir / "update_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["app_version"] == "v9.9"
    assert manifest["package_type"] == "incremental_update"
    assert manifest["data"]["included"] is False
    assert manifest["runtime_extras"] == ["models", "tectonic"]

    app = update_dir / "app"
    assert (app / "backend" / "api" / "app.py").is_file()
    assert (app / "frontend" / "dist" / "assets" / "app.js").is_file()
    assert (app / "运行.bat").is_file()
    assert (app / "VERSION").read_text(encoding="utf-8").strip() == "v9.9"
    assert (app / "runtime" / "models" / "asr" / "model.onnx").is_file()
    assert (app / "runtime" / "tectonic" / "tectonic.exe").is_file()
    assert not (app / "user_data").exists()

    assert (update_dir / "migrations" / "grading" / "0001_init.sql").is_file()
    assert (update_dir / "update_tools" / "apply_update.py").is_file()


def test_make_update_package_is_consumable_by_apply_update_dry_run(
    tmp_path: Path,
) -> None:
    make_update = _load_make_update()
    apply_update = _load_apply_update()
    packager = make_update.load_packager()
    source = tmp_path / "演示项目_v9.9"
    source.mkdir()
    _build_fake_source(source)

    update_dir = make_update.make_update_package(
        source, tmp_path / "dist", "v9.9", packager=packager
    )

    target = tmp_path / "deploy"
    target.mkdir()
    (target / "VERSION").write_text("v9.8\n", encoding="utf-8")

    result = apply_update.apply_update(update_dir, target, dry_run=True)

    assert result["success"] is True
    assert result["old_version"] == "v9.8"
    assert result["new_version"] == "v9.9"
    assert result["files_updated"] > 0
    assert result["error"] is None
    assert not (target / "backend").exists()
