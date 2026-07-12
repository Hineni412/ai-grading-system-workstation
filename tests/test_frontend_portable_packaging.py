from __future__ import annotations

import importlib.util
from pathlib import Path


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
    assert not (package / "frontend" / "src").exists()
    assert not (package / "frontend" / "node_modules").exists()
