"""增量更新包生成工具 — 在开发机生成 apply_update.py 可应用的更新包。

用法:
    python update_tools/make_update.py [--version vX.Y.Z] [--output-dir dist]

产出(与 apply_update.py 的输入约定一致,只生成文件夹,不打 zip):
    <output-dir>/<项目名>_update_<版本>/
        update_manifest.json
        app/            代码、frontend/dist、VERSION、启动器、runtime 模型与 tectonic
        migrations/     全量迁移清单(apply_update 会整体替换目标机 migrations/)
        update_tools/   全量更新工具(apply_update 会整体替换目标机 update_tools/)

安全规则:
    - 更新包绝不包含 user_data;用户数据只保留在目标机,由 apply_update 备份并保留。
    - 代码与运行时附件的复制规则与完整安装包(package_v1.5.0.py)同源,避免规则漂移。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_PACKAGER_PATH = _PROJECT_ROOT / "package_v1.5.0.py"


def load_packager(packager_path: Path = _PACKAGER_PATH):
    """Load the full-package builder so update packages reuse its copy rules."""
    if not packager_path.is_file():
        raise RuntimeError(
            f"未找到完整打包脚本 {packager_path};更新包必须与完整包使用同一套复制规则。"
        )
    spec = importlib.util.spec_from_file_location("portable_packager", packager_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载打包脚本: {packager_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_from_root(project_root: Path) -> str:
    version_file = project_root / "VERSION"
    if not version_file.is_file():
        raise RuntimeError("项目根缺少 VERSION 文件,无法确定更新包版本。")
    version = version_file.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("VERSION 文件为空,无法确定更新包版本。")
    return version


def _copy_dir_filtered(src: Path, dst: Path) -> None:
    if not src.exists():
        raise RuntimeError(f"更新包缺少源目录: {src}")
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".pytest_cache"
        ),
        dirs_exist_ok=True,
    )


def _dir_stats(path: Path) -> dict[str, int | float]:
    files = [item for item in path.rglob("*") if item.is_file()]
    size = sum(item.stat().st_size for item in files)
    return {"files": len(files), "size_mb": round(size / 1024 / 1024, 1)}


def make_update_package(
    src_dir: Path,
    output_dir: Path,
    version: str,
    *,
    packager,
) -> Path:
    base_name = src_dir.name.rsplit("_v", 1)[0]
    update_dir = output_dir / f"{base_name}_update_{version}"
    if update_dir.exists():
        shutil.rmtree(update_dir)
    update_dir.mkdir(parents=True)

    app_dir = update_dir / "app"
    app_dir.mkdir()
    packager.copy_sources(src_dir, app_dir, version)
    packager.write_launchers(src_dir, app_dir)
    runtime_extras = packager.copy_runtime_extras(src_dir, app_dir)

    _copy_dir_filtered(src_dir / "migrations", update_dir / "migrations")
    _copy_dir_filtered(src_dir / "update_tools", update_dir / "update_tools")

    manifest = {
        "app_version": version,
        "package_type": "incremental_update",
        "build_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_extras": runtime_extras,
        "data": {"included": False},
    }
    (update_dir / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    packager.clean_generated_artifacts(update_dir)
    return update_dir


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成 apply_update.py 可应用的增量更新包(仅文件夹,不打 zip)。"
    )
    parser.add_argument("--version", default=None, help="默认取根目录 VERSION 文件")
    parser.add_argument("--output-dir", default=None, help="默认为项目根的 dist/")
    args = parser.parse_args(list(argv) if argv is not None else None)

    packager = load_packager()
    version = args.version or _version_from_root(_PROJECT_ROOT)
    output_dir = Path(args.output_dir) if args.output_dir else _PROJECT_ROOT / "dist"

    print(f"Building incremental update package: {version}")
    update_dir = make_update_package(_PROJECT_ROOT, output_dir, version, packager=packager)
    stats = _dir_stats(update_dir)

    print("")
    print("Update package complete")
    print(f"Folder: {update_dir}")
    print(f"Files: {stats['files']}")
    print(f"Size MB: {stats['size_mb']}")
    print("Apply on the target machine with: python update_tools/apply_update.py <更新包目录>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
