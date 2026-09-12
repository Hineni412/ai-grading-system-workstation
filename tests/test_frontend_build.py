from pathlib import Path
import subprocess

import pytest

from tools import ensure_frontend as build


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    for name in ("package.json", "package-lock.json", "vite.config.ts", "src/main.ts",
                 "public/favicon.svg", "node_modules/.package-lock.json",
                 "node_modules/vite/package.json", "node_modules/@vue/shared/package.json"):
        path = frontend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assets = frontend / "dist" / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "app.js").write_text("compiled", encoding="utf-8")
        (assets / "lazy.js").write_text("lazy", encoding="utf-8")
        (frontend / "dist" / "index.html").write_text("index", encoding="utf-8")

    monkeypatch.setattr(build.shutil, "which", lambda _name: "npm.cmd")
    monkeypatch.setattr(build.subprocess, "run", run)
    return tmp_path, calls


def test_two_unchanged_starts_skip_build(checkout):
    root, calls = checkout
    assert build.ensure_frontend(root)
    assert not build.ensure_frontend(root)
    assert not build.ensure_frontend(root)
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["src/main.ts", "vite.config.ts", "package-lock.json",
                                  "node_modules/.package-lock.json", "node_modules/vite/package.json", "public/favicon.svg",
                                  ".env.production", "src/new.ts"])
def test_changed_inputs_rebuild(checkout, name):
    root, calls = checkout
    build.ensure_frontend(root)
    (root / "frontend" / name).write_text("changed content", encoding="utf-8")
    assert build.ensure_frontend(root)
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["src/main.ts", "dist/index.html", "dist/assets/lazy.js",
                                  "node_modules/@vue/shared/package.json"])
def test_removed_source_or_output_rebuilds(checkout, name):
    root, calls = checkout
    build.ensure_frontend(root)
    (root / "frontend" / name).unlink()
    assert build.ensure_frontend(root)
    assert len(calls) == 2


def test_failed_build_cannot_reuse_old_stamp(checkout, monkeypatch):
    root, _ = checkout
    build.ensure_frontend(root)
    source = root / "frontend/src/main.ts"
    source.write_text("broken", encoding="utf-8")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "npm")

    monkeypatch.setattr(build.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        build.ensure_frontend(root)
    assert not (root / "frontend/dist" / build.STAMP_NAME).exists()
    # Even reverting the bad edit cannot silently launch partially built output.
    source.write_text("original", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        build.ensure_frontend(root)


def test_source_edit_during_build_fails_closed(checkout, monkeypatch):
    root, _ = checkout
    run = build.subprocess.run

    def change(*args, **kwargs):
        run(*args, **kwargs)
        (root / "frontend/src/main.ts").write_text("edit while building", encoding="utf-8")

    monkeypatch.setattr(build.subprocess, "run", change)
    with pytest.raises(RuntimeError, match="changed during"):
        build.ensure_frontend(root)
    assert not (root / "frontend/dist" / build.STAMP_NAME).exists()
