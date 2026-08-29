from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable


DEFAULT_VERSION = "v1.5.0"
PYTHON_VERSION = "3.12.1"
PYTHON_EMBED_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_EMBED_ZIP}"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

PRODUCTION_DIRS = {
    "backend",
    "config",
    "docs",
    "integration",
    "migrations",
    "question_bank",
    "update_tools",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".gemini",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "dist_smoke_test",
    "logs",
    "scratch",
}

EXCLUDED_DOC_DIRS = {
    "superpowers",
}

ROOT_INCLUDE_EXACT = {
    "README.md",
    "docx2pdf.ps1",
    "manifest.json",
    "pytest.ini",
    "requirements.txt",
    "VERSION",
}

ROOT_EXCLUDE_EXACT = {
    ".env",
    "0526_parsed_result.docx",
    "database.sqlite",
    "last_batch_id.txt",
    "package_v1.4.0.py",
    "package_v1.5.0.py",
    "test.pdf",
}

ROOT_EXCLUDE_PREFIXES = (
    "check_",
    "diagnose_",
    "drill_",
    "evaluate_",
    "final_fix",
    "finalize_",
    "fix_",
    "monitor_",
    "operations_",
    "patch_",
    "prepare_",
    "pre_release_",
    "preview_",
    "production_safety_",
    "refactor",
    "release_",
    "revert_",
    "rollback_",
    "scratch_",
    "search_",
    "test_",
    "validate_",
)

ROOT_EXCLUDE_FILES = {
    "find_settings.py",
    "generate_objective_question_registry_report.py",
    "package_v1.4.0.py",
    "release_regression_suite.py",
    "run_subject_only_q1_trial.py",
    "subjective_only_trial_monitor.py",
}

ROOT_COPY_EXTENSIONS = {
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}


def _version_from_file(src_dir: Path) -> str:
    version_file = src_dir / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return DEFAULT_VERSION


def _package_name(src_dir: Path, version: str) -> str:
    base_name = src_dir.name.rsplit("_v", 1)[0]
    return f"{base_name}_{version}"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"Using cached download: {dest}")
        return
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, dest)


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    printable = " ".join(f'"{part}"' if " " in part else part for part in cmd)
    print(f">> {printable}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree_filtered(src: Path, dst: Path, *, include_docs: bool = False) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        dir_path = Path(directory)
        for name in names:
            path = dir_path / name
            if name in EXCLUDED_DIR_NAMES or name.startswith("."):
                ignored.add(name)
                continue
            if include_docs and path.is_dir() and name in EXCLUDED_DOC_DIRS:
                ignored.add(name)
                continue
            if path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}:
                ignored.add(name)
        return ignored

    if src.exists():
        shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def _is_root_helper_script(name: str) -> bool:
    lowered = name.lower()
    if lowered in ROOT_EXCLUDE_FILES:
        return True
    return any(lowered.startswith(prefix) for prefix in ROOT_EXCLUDE_PREFIXES)


def _should_copy_root_file(path: Path) -> bool:
    name = path.name
    if name in ROOT_EXCLUDE_EXACT:
        return False
    if name.startswith("README_") and path.suffix.lower() == ".md":
        return False
    if _is_root_helper_script(name):
        return False
    if name in ROOT_INCLUDE_EXACT:
        return True
    if path.suffix.lower() not in ROOT_COPY_EXTENSIONS:
        return False
    if name.startswith("."):
        return False
    return True


def copy_frontend_dist(src_dir: Path, pkg_dir: Path) -> int:
    src = src_dir / "frontend" / "dist"
    assets = src / "assets"
    if (
        not (src / "index.html").is_file()
        or not assets.is_dir()
        or not any(path.is_file() for path in assets.rglob("*"))
    ):
        raise RuntimeError(
            "frontend/dist is incomplete; run the verified frontend build before packaging"
        )

    files = [path for path in src.rglob("*") if path.is_file()]
    _copy_tree_filtered(src, pkg_dir / "frontend" / "dist")
    return len(files)


def copy_sources(src_dir: Path, pkg_dir: Path, version: str) -> dict[str, int]:
    copied_files = 0

    for path in src_dir.iterdir():
        if path.is_file() and _should_copy_root_file(path):
            _copy_file(path, pkg_dir / path.name)
            copied_files += 1

    for dirname in sorted(PRODUCTION_DIRS):
        src = src_dir / dirname
        if not src.exists():
            continue
        _copy_tree_filtered(src, pkg_dir / dirname, include_docs=(dirname == "docs"))

    frontend_dist_files = copy_frontend_dist(src_dir, pkg_dir)
    (pkg_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    return {
        "root_files": copied_files,
        "frontend_dist_files": frontend_dist_files,
    }


def copy_private_user_data(src_dir: Path, pkg_dir: Path) -> None:
    """Copy user_data into the package as a full machine snapshot.

    包内包含全部业务数据(工作区)与 api_profiles.json 中的模型密钥,
    只允许本机私有保存;不得进入 Git、普通备份同步或对外分发。
    """
    src = src_dir / "user_data"
    dst = pkg_dir / "user_data"
    if not src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        return

    shutil.copytree(src, dst, ignore=_ignore_runtime_caches)


def _ignore_runtime_caches(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in {"__pycache__", ".pytest_cache"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


RUNTIME_EXTRAS = ("models", "tectonic")


def copy_runtime_extras(src_dir: Path, pkg_dir: Path) -> list[str]:
    """Copy bundled runtime assets (local models, tectonic) when present."""
    included: list[str] = []
    for name in RUNTIME_EXTRAS:
        src = src_dir / "runtime" / name
        if not src.exists():
            continue
        shutil.copytree(
            src,
            pkg_dir / "runtime" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            dirs_exist_ok=True,
        )
        included.append(name)
    return included


def _configure_embed_pth(runtime_dir: Path) -> None:
    pth_files = list(runtime_dir.glob("python*._pth"))
    if not pth_files:
        return
    pth = pth_files[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    next_lines: list[str] = []
    has_app_root = False
    has_import_site = False
    for line in lines:
        stripped = line.strip()
        if stripped in {"..\\..", "../.."}:
            has_app_root = True
        if stripped in {"#import site", "import site"}:
            if not has_app_root:
                next_lines.append("..\\..")
                has_app_root = True
            next_lines.append("import site")
            has_import_site = True
            continue
        next_lines.append(line)
    if not has_app_root:
        next_lines.append("..\\..")
    if not has_import_site:
        next_lines.append("import site")
    pth.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    (runtime_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def _copy_tkinter_runtime(runtime_dir: Path) -> None:
    host_root = Path(sys.base_prefix)
    src_tkinter = host_root / "Lib" / "tkinter"
    src_tcl = host_root / "tcl"
    required_files = ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll")

    missing: list[str] = []
    if not src_tkinter.exists():
        missing.append(str(src_tkinter))
    if not src_tcl.exists():
        missing.append(str(src_tcl))
    for name in required_files:
        src = host_root / "DLLs" / name
        if not src.exists():
            missing.append(str(src))
    if missing:
        raise RuntimeError(
            "当前打包用 Python 缺少 Tkinter/Tcl 运行库，无法生成带本地文件选择窗口的工作机版：\n"
            + "\n".join(missing)
        )

    shutil.copytree(src_tkinter, runtime_dir / "tkinter", dirs_exist_ok=True)
    shutil.copytree(src_tcl, runtime_dir / "tcl", dirs_exist_ok=True)
    for name in required_files:
        shutil.copy2(host_root / "DLLs" / name, runtime_dir / name)


def build_runtime(src_dir: Path, cache_dir: Path, *, rebuild: bool = False) -> Path:
    runtime_dir = cache_dir / f"python-{PYTHON_VERSION}-embed-amd64-runtime"
    requirements = src_dir / "requirements.txt"
    if runtime_dir.exists() and not rebuild:
        print(f"Using cached portable Python runtime: {runtime_dir}")
        _configure_embed_pth(runtime_dir)
        _copy_tkinter_runtime(runtime_dir)
        python_exe = runtime_dir / "python.exe"
        _run(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--no-warn-script-location",
                "-r",
                str(requirements),
            ]
        )
        _run(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--no-warn-script-location",
                "pytest>=8.0.0",
            ]
        )
        return runtime_dir

    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True)

    downloads = cache_dir / "downloads"
    zip_path = downloads / PYTHON_EMBED_ZIP
    get_pip_path = downloads / "get-pip.py"

    _download(PYTHON_EMBED_URL, zip_path)
    print(f"Extracting portable Python runtime: {runtime_dir}")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(runtime_dir)

    _configure_embed_pth(runtime_dir)
    _copy_tkinter_runtime(runtime_dir)
    _download(GET_PIP_URL, get_pip_path)

    python_exe = runtime_dir / "python.exe"

    _run([str(python_exe), str(get_pip_path), "--no-warn-script-location"])
    _run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "-r",
            str(requirements),
        ]
    )
    _run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "pytest>=8.0.0",
        ]
    )
    _run(
        [
            str(python_exe),
            "-c",
            "import cv2, fitz, tkinter; print('runtime ok', cv2.__version__, tkinter.__file__)",
        ]
    )
    return runtime_dir


def copy_runtime(runtime_src: Path, pkg_dir: Path) -> None:
    dst = pkg_dir / "runtime" / "python"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        runtime_src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    _configure_embed_pth(dst)


def write_launchers(src_dir: Path, pkg_dir: Path) -> None:
    required_files = {
        Path("运行.bat"): Path("运行.bat"),
        Path("关闭系统.bat"): Path("关闭系统.bat"),
        Path("tools/run_project_module.py"): Path("tools/run_project_module.py"),
        Path("tools/stop_service.ps1"): Path("tools/stop_service.ps1"),
    }
    for source_relative, package_relative in required_files.items():
        source = src_dir / source_relative
        if not source.is_file():
            raise RuntimeError(
                f"{source_relative.as_posix()} is missing from the package source"
            )
        _copy_file(source, pkg_dir / package_relative)


def _dir_stats(path: Path) -> dict[str, int | float]:
    files = [p for p in path.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    return {"files": len(files), "size_mb": round(size / 1024 / 1024, 1)}


def clean_generated_artifacts(pkg_dir: Path) -> None:
    for cache_dir in list(pkg_dir.rglob("__pycache__")) + list(pkg_dir.rglob(".pytest_cache")):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
    for pattern in ("*.pyc", "*.pyo"):
        for file_path in pkg_dir.rglob(pattern):
            if file_path.is_file():
                file_path.unlink()


def write_manifest(
    pkg_dir: Path,
    version: str,
    *,
    runtime_included: bool,
    runtime_extras: list[str] | None = None,
) -> None:
    manifest = {
        "app_version": version,
        "package_type": "private_portable_source_runtime",
        "build_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": {
            "included": runtime_included,
            "python_version": PYTHON_VERSION if runtime_included else None,
            "path": "runtime/python" if runtime_included else None,
            "extras": list(runtime_extras or []),
        },
        "data": {
            "included": True,
            "full_data_included": True,
            "api_profiles_included": True,
            "workspaces_included": True,
            "path": "user_data",
        },
        "cleanup": {
            "excluded_helper_prefixes": list(ROOT_EXCLUDE_PREFIXES),
            "test_launchers_included": False,
        },
        "package_stats": _dir_stats(pkg_dir),
    }
    (pkg_dir / f"RELEASE_MANIFEST_{version}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_package(args: argparse.Namespace) -> None:
    src_dir = Path(__file__).resolve().parent
    version = args.version or _version_from_file(src_dir) or DEFAULT_VERSION
    dist_dir = src_dir / "dist"
    pkg_name = _package_name(src_dir, version)
    pkg_dir = dist_dir / pkg_name

    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    print(f"Building private portable package: {pkg_dir}")
    copy_sources(src_dir, pkg_dir, version)
    copy_private_user_data(src_dir, pkg_dir)

    runtime_included = not args.skip_runtime
    runtime_extras: list[str] = []
    if runtime_included:
        cache_dir = src_dir / ".portable_runtime_cache"
        runtime = build_runtime(src_dir, cache_dir, rebuild=args.rebuild_runtime)
        copy_runtime(runtime, pkg_dir)
        runtime_extras = copy_runtime_extras(src_dir, pkg_dir)

    write_launchers(src_dir, pkg_dir)
    clean_generated_artifacts(pkg_dir)
    write_manifest(
        pkg_dir,
        version,
        runtime_included=runtime_included,
        runtime_extras=runtime_extras,
    )

    stats = _dir_stats(pkg_dir)
    print("")
    print("Package complete")
    print(f"Folder: {pkg_dir}")
    print(f"Files: {stats['files']}")
    print(f"Size MB: {stats['size_mb']}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build private portable v1.5 package.")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Create the package without runtime/python for quick layout checks.",
    )
    parser.add_argument(
        "--rebuild-runtime",
        action="store_true",
        help="Rebuild the cached portable Python runtime from scratch.",
    )
    return parser.parse_args(list(argv))


if __name__ == "__main__":
    build_package(parse_args(sys.argv[1:]))
