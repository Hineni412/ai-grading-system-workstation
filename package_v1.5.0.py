from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
    "config",
    "docs",
    "integration",
    "migrations",
    "pages",
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
    "README_工作机使用说明.md",
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

CORE_TEST_FILES = {
    "test_answer_normalizer.py",
    "tests/__init__.py",
    "tests/test_objective_batch_recognition_service.py",
    "tests/test_objective_escalation.py",
    "tests/test_portable_path_resolution.py",
    "tests/test_prompt_injection_guard.py",
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
    if name in CORE_TEST_FILES:
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

    for rel in sorted(CORE_TEST_FILES):
        src = src_dir / rel
        if src.exists():
            _copy_file(src, pkg_dir / rel)
            copied_files += 1

    (pkg_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    return {"root_and_core_test_files": copied_files}


def copy_private_user_data(src_dir: Path, pkg_dir: Path) -> None:
    src = src_dir / "user_data"
    dst = pkg_dir / "user_data"
    if not src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        return

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in {"__pycache__", ".pytest_cache"}:
                ignored.add(name)
            elif name.endswith((".pyc", ".pyo")):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


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


def build_runtime(src_dir: Path, cache_dir: Path, *, rebuild: bool = False) -> Path:
    runtime_dir = cache_dir / f"python-{PYTHON_VERSION}-embed-amd64-runtime"
    requirements = src_dir / "requirements.txt"
    if runtime_dir.exists() and not rebuild:
        print(f"Using cached portable Python runtime: {runtime_dir}")
        _configure_embed_pth(runtime_dir)
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
            "import streamlit, cv2, fitz; print('runtime ok', streamlit.__version__)",
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


def write_launcher(pkg_dir: Path, version: str) -> None:
    launcher = f"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0runtime\\python\\python.exe"
if not exist "%PYTHON_EXE%" (
  echo 未找到便携 Python 运行时: %PYTHON_EXE%
  echo 请确认 runtime\\python 目录完整。
  pause
  exit /b 1
)

set "AI_GRADING_DATA_DIR=%~dp0user_data"
set "PYTHONUTF8=1"
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=false"
if "%PORT%"=="" set "PORT=8501"

echo AI阅卷系统 工作机版 {version}
echo 数据目录: %AI_GRADING_DATA_DIR%
echo 启动地址: http://127.0.0.1:%PORT%
start "" "http://127.0.0.1:%PORT%"

"%PYTHON_EXE%" -m streamlit run web_app.py --server.address 127.0.0.1 --server.port %PORT%
if errorlevel 1 (
  echo.
  echo 程序异常退出，请把窗口中的报错发给 Codex 排查。
  pause
)
endlocal
"""
    (pkg_dir / "运行.bat").write_text(launcher, encoding="utf-8")


def write_core_test_launcher(pkg_dir: Path) -> None:
    tests = " ".join(
        [
            "test_answer_normalizer.py",
            "tests\\test_objective_batch_recognition_service.py",
            "tests\\test_objective_escalation.py",
            "tests\\test_prompt_injection_guard.py",
            "tests\\test_portable_path_resolution.py",
        ]
    )
    launcher = f"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0runtime\\python\\python.exe"
set "AI_GRADING_DATA_DIR=%~dp0user_data"
"%PYTHON_EXE%" -m pytest {tests} -q
pause
endlocal
"""
    (pkg_dir / "运行核心测试.bat").write_text(launcher, encoding="utf-8")


def write_private_readme(pkg_dir: Path, version: str) -> None:
    readme = f"""# AI阅卷系统 工作机版 {version}

这是私人便携开发版，包含运行所需的 Python 环境、源码和当前 `user_data` 数据。

## 启动

双击 `运行.bat`。

新电脑不需要预装 Python，也不需要重新安装 `requirements.txt`。启动脚本会直接使用：

```text
runtime\\python\\python.exe
```

## 数据

本包保留当前 `user_data/`，包括数据库、模板、历史考试、输出文件和 `user_data/config/api_profiles.json`。

这个包包含 API 密钥，只适合你自己使用，不要外发。

## 后续继续用 Codex 修改

源码保留在发布目录中，可以直接用 Codex 打开这个文件夹继续修改。

临时排查脚本、补丁脚本、旧测试脚本没有进入发布包；保留了少量核心回归测试，可双击 `运行核心测试.bat` 检查关键逻辑。
"""
    (pkg_dir / f"README_私人便携版_{version}.md").write_text(readme, encoding="utf-8")


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


def write_manifest(pkg_dir: Path, version: str, *, runtime_included: bool) -> None:
    manifest = {
        "app_version": version,
        "package_type": "private_portable_source_runtime",
        "build_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": {
            "included": runtime_included,
            "python_version": PYTHON_VERSION if runtime_included else None,
            "path": "runtime/python" if runtime_included else None,
        },
        "data": {
            "included": True,
            "api_profiles_included": True,
            "path": "user_data",
        },
        "cleanup": {
            "excluded_helper_prefixes": list(ROOT_EXCLUDE_PREFIXES),
            "core_tests_included": sorted(CORE_TEST_FILES),
        },
        "package_stats": _dir_stats(pkg_dir),
    }
    (pkg_dir / f"RELEASE_MANIFEST_{version}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def make_archive(pkg_dir: Path) -> tuple[Path, Path]:
    zip_base = pkg_dir.parent / pkg_dir.name
    print(f"Creating archive: {zip_base}.zip")
    archive_path = Path(shutil.make_archive(str(zip_base), "zip", pkg_dir.parent, pkg_dir.name))
    sha256_path = archive_path.with_suffix(archive_path.suffix + ".sha256")

    hasher = hashlib.sha256()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    sha256_path.write_text(
        f"{hasher.hexdigest()} *{archive_path.name}\n",
        encoding="utf-8",
    )
    return archive_path, sha256_path


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
    if runtime_included:
        cache_dir = src_dir / ".portable_runtime_cache"
        runtime = build_runtime(src_dir, cache_dir, rebuild=args.rebuild_runtime)
        copy_runtime(runtime, pkg_dir)

    write_launcher(pkg_dir, version)
    write_core_test_launcher(pkg_dir)
    write_private_readme(pkg_dir, version)
    clean_generated_artifacts(pkg_dir)
    write_manifest(pkg_dir, version, runtime_included=runtime_included)

    archive_path = None
    sha256_path = None
    if not args.no_zip:
        archive_path, sha256_path = make_archive(pkg_dir)

    stats = _dir_stats(pkg_dir)
    print("")
    print("Package complete")
    print(f"Folder: {pkg_dir}")
    print(f"Files: {stats['files']}")
    print(f"Size MB: {stats['size_mb']}")
    if archive_path:
        print(f"Zip: {archive_path}")
        print(f"SHA256: {sha256_path}")


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
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Only create the release folder, not the zip archive.",
    )
    return parser.parse_args(list(argv))


if __name__ == "__main__":
    build_package(parse_args(sys.argv[1:]))
