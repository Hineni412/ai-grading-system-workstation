"""Build the reproducible, read-only P3-01 structural baseline.

The report is deliberately an inventory, not a refactoring tool.  It scans only
versioned source and evidence files and never imports application modules, opens
a database, or traverses ``user_data``.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

if os.name == "nt":
    from ctypes import wintypes


PACKAGE = "P3-01"
REPORT_VERSION = 1
REPORT_STEM = "p3-01-structural-baseline"
PUBLICATION_VERSION = 1
JSON_REPORT_NAME = f"{REPORT_STEM}.json"
MARKDOWN_REPORT_NAME = f"{REPORT_STEM}.md"
RELEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CORE_TARGETS = (
    "db_manager.py",
    "session_manager.py",
    "grading_service.py",
    "manual_review_service.py",
    "web_app.py",
    "backend/api/app.py",
    "question_bank/database/schema.py",
)
SOURCE_DIRECTORIES = (
    "backend",
    "integration",
    "pages",
    "pages_shared",
    "question_bank",
    "tools",
    "update_tools",
)
TEST_DIRECTORY = "tests"
SCHEMA_DIRECTORIES = {
    "grading": "migrations/grading",
    "question_bank": "migrations/question_bank",
}
DEFAULT_P1_26_REPORT = "docs/performance/p1-26-api-db-baseline.json"
DEFAULT_P1_27_REPORT = "docs/performance/p1-27-request-connection-comparison.json"
PROCESS_EVIDENCE = {
    "P1-29": "docs/user-testing/checkpoints/P1-29-v1.5.0-phase1-formal.md",
    "P2-20": "docs/user-testing/checkpoints/P2-20-v1.5.0-new-ui-five-flow-formal.md",
}


class BaselineInputError(ValueError):
    """A required, versioned P3-01 input is absent or invalid."""


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _python_paths(root: Path) -> list[Path]:
    paths = set(root.glob("*.py")) | {
        root / relative
        for relative in CORE_TARGETS
    }
    for directory in SOURCE_DIRECTORIES:
        source_root = root / directory
        if source_root.is_dir():
            paths.update(path for path in source_root.rglob("*.py") if not path.is_symlink())
    return sorted(paths, key=lambda path: _relative(path, root))


def _module_name(relative_path: str) -> str:
    without_suffix = relative_path.removesuffix(".py")
    if without_suffix.endswith("/__init__"):
        without_suffix = without_suffix[: -len("/__init__")]
    return without_suffix.replace("/", ".")


def _literal_argument(node: ast.Call) -> str:
    if not node.args:
        return "<missing>"
    value = node.args[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return "<nonliteral>"


def _imports_for_tree(tree: ast.AST) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    dynamic_imports: list[dict[str, Any]] = []
    importlib_names = {"importlib"}
    import_module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_names.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    {
                        "kind": "import",
                        "line": node.lineno,
                        "module": alias.name,
                        "name": alias.asname,
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                {
                    "kind": "from",
                    "line": node.lineno,
                    "module": node.module or "",
                    "name": ",".join(sorted(alias.name for alias in node.names)),
                    "relative_level": node.level,
                }
            )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                dynamic_imports.append(
                    {
                        "kind": "__import__",
                        "line": node.lineno,
                        "module": _literal_argument(node),
                    }
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_names
            ):
                dynamic_imports.append(
                    {
                        "kind": "importlib.import_module",
                        "line": node.lineno,
                        "module": _literal_argument(node),
                    }
                )
            elif isinstance(node.func, ast.Name) and node.func.id in import_module_names:
                dynamic_imports.append(
                    {
                        "kind": "importlib.import_module",
                        "line": node.lineno,
                        "module": _literal_argument(node),
                    }
                )
    return (
        sorted(imports, key=lambda item: (item["line"], item["kind"], item["module"], item["name"])),
        sorted(dynamic_imports, key=lambda item: (item["line"], item["kind"], item["module"])),
    )


def _public_symbols(tree: ast.Module) -> tuple[list[str], list[str], dict[str, list[str]]]:
    public_classes: list[str] = []
    public_functions: list[str] = []
    class_methods: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            public_classes.append(node.name)
            class_methods[node.name] = sorted(
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            public_functions.append(node.name)
    return sorted(public_classes), sorted(public_functions), class_methods


def _read_tree(path: Path, root: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=_relative(path, root))
    except SyntaxError as exc:
        raise BaselineInputError(f"invalid Python source: {_relative(path, root)}: {exc.msg}") from exc


def _target_imported(imports: Iterable[dict[str, Any]], target_modules: dict[str, str]) -> set[str]:
    matches: set[str] = set()
    for item in imports:
        if item["kind"] == "from" and item["relative_level"]:
            continue
        module = str(item["module"])
        for target_module, relative_path in target_modules.items():
            if module == target_module or module.startswith(target_module + "."):
                matches.add(relative_path)
    return matches


def _source_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BaselineInputError("missing required input: Git revision")
    return completed.stdout.strip()


def _require_file(root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    path = candidate if candidate.is_absolute() else root / candidate
    _reject_user_data(path.resolve(), root, "input")
    if not path.is_file():
        raise BaselineInputError(f"missing required input: {raw_path}")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BaselineInputError("required input must be inside the repository") from exc
    return path


def _reject_user_data(path: Path, root: Path, description: str) -> None:
    data_root = (root / "user_data").resolve()
    try:
        path.relative_to(data_root)
    except ValueError:
        return
    raise BaselineInputError(f"user_data is not an allowed {description}")


def _schema_inputs(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, relative_directory in SCHEMA_DIRECTORIES.items():
        directory = root / relative_directory
        if not directory.is_dir():
            raise BaselineInputError(f"missing required input: {relative_directory}")
        files = sorted(directory.glob("*.sql"))
        if not files:
            raise BaselineInputError(f"missing required input: {relative_directory}/*.sql")
        result[name] = {
            "files": [
                {"path": _relative(path, root), "sha256": _sha256(path)}
                for path in files
            ],
        }
    return result


def _performance_input(root: Path, raw_path: str, *, expected_package: str) -> dict[str, Any]:
    path = _require_file(root, raw_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineInputError(f"invalid performance evidence: {raw_path}") from exc
    if payload.get("package") != expected_package:
        raise BaselineInputError(f"invalid performance evidence package: {raw_path}")
    result = {
        "path": _relative(path, root),
        "sha256": _sha256(path),
        "package": expected_package,
        "code_sha": payload.get("code_sha", ""),
        "repeatability": payload.get("repeatability", ""),
        "limitations": list(payload.get("limitations", [])),
    }
    if expected_package == "P1-26":
        result["scenario_count"] = sum(
            len(repetition.get("scenarios", []))
            for scale in payload.get("scales", [])
            for repetition in scale.get("repetitions", [])
        )
    else:
        result["p1_26_provenance_code_sha"] = payload.get(
            "p1_26_provenance_code_sha", ""
        )
    return result


def build_report(root: Path, *, p1_26_report: str, p1_27_report: str) -> dict[str, Any]:
    for relative in CORE_TARGETS:
        _require_file(root, relative)
    schema_inputs = _schema_inputs(root)
    performance_evidence = {
        "p1_26": _performance_input(root, p1_26_report, expected_package="P1-26"),
        "p1_27": _performance_input(root, p1_27_report, expected_package="P1-27"),
    }
    process_evidence: dict[str, dict[str, str]] = {}
    for label, relative in PROCESS_EVIDENCE.items():
        path = _require_file(root, relative)
        process_evidence[label] = {
            "path": _relative(path, root),
            "sha256": _sha256(path),
        }

    paths = _python_paths(root)
    source_imports: list[dict[str, Any]] = []
    dynamic_imports: list[dict[str, Any]] = []
    source_manifest: list[dict[str, str]] = []
    parsed_trees: dict[str, ast.Module] = {}

    for path in paths:
        relative = _relative(path, root)
        tree = _read_tree(path, root)
        parsed_trees[relative] = tree
        imports, dynamic = _imports_for_tree(tree)
        source_imports.extend({"source": relative, **item} for item in imports)
        dynamic_imports.extend({"source": relative, **item} for item in dynamic)
        source_manifest.append({"path": relative, "sha256": _sha256(path)})

    core_targets: dict[str, dict[str, Any]] = {}
    for relative in CORE_TARGETS:
        public_classes, public_functions, class_methods = _public_symbols(parsed_trees[relative])
        core_targets[relative] = {
            "public_classes": public_classes,
            "public_functions": public_functions,
            "public_methods": class_methods,
            "static_import_count": sum(
                1 for item in source_imports if item["source"] == relative
            ),
        }

    target_modules = {
        _module_name(relative): relative
        for relative in CORE_TARGETS
    }
    coverage: dict[str, list[str]] = defaultdict(list)
    tests_root = root / TEST_DIRECTORY
    if not tests_root.is_dir():
        raise BaselineInputError(f"missing required input: {TEST_DIRECTORY}")
    for test_path in sorted(tests_root.rglob("test_*.py")):
        tree = _read_tree(test_path, root)
        imports, _ = _imports_for_tree(tree)
        for target in _target_imported(imports, target_modules):
            coverage[target].append(_relative(test_path, root))
    test_coverage_map = [
        {"target": target, "tests": sorted(coverage.get(target, []))}
        for target in CORE_TARGETS
    ]
    callers: dict[str, set[str]] = defaultdict(set)
    for item in source_imports:
        for target in _target_imported((item,), target_modules):
            if item["source"] != target:
                callers[target].add(item["source"])
    core_callers = {
        target: sorted(callers.get(target, set()))
        for target in CORE_TARGETS
    }

    return {
        "package": PACKAGE,
        "report_version": REPORT_VERSION,
        "source_revision": _source_revision(root),
        "source_manifest": sorted(source_manifest, key=lambda item: item["path"]),
        "static_imports": sorted(
            source_imports,
            key=lambda item: (item["source"], item["line"], item["kind"], item["module"], item["name"]),
        ),
        "dynamic_imports": sorted(
            dynamic_imports,
            key=lambda item: (item["source"], item["line"], item["kind"], item["module"]),
        ),
        "core_targets": core_targets,
        "core_callers": core_callers,
        "test_coverage_map": test_coverage_map,
        "schema_inputs": schema_inputs,
        "performance_evidence": performance_evidence,
        "real_process_evidence": process_evidence,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P3-01 结构基线",
        "",
        f"- 源码提交：`{report['source_revision']}`",
        f"- 扫描源码文件：{len(report['source_manifest'])}",
        f"- 静态导入：{len(report['static_imports'])}",
        f"- 动态导入线索：{len(report['dynamic_imports'])}",
        "",
        "## 后续拆分对象",
        "",
        "| 文件 | 公开类 | 公开函数 | 公开方法数 | 静态导入数 | 直接调用方 | 直接测试文件 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    test_map = {item["target"]: item["tests"] for item in report["test_coverage_map"]}
    for path in CORE_TARGETS:
        target = report["core_targets"][path]
        lines.append(
            "| {path} | {classes} | {functions} | {methods} | {imports} | {callers} | {tests} |".format(
                path=f"`{path}`",
                classes=len(target["public_classes"]),
                functions=len(target["public_functions"]),
                methods=sum(len(values) for values in target["public_methods"].values()),
                imports=target["static_import_count"],
                callers=len(report["core_callers"][path]),
                tests=len(test_map[path]),
            )
        )
    lines.extend(
        [
            "",
            "## Schema 与性能证据",
            "",
            f"- 阅卷库迁移文件：{len(report['schema_inputs']['grading']['files'])}",
            f"- 题库迁移文件：{len(report['schema_inputs']['question_bank']['files'])}",
            f"- P1-26：`{report['performance_evidence']['p1_26']['path']}`，{report['performance_evidence']['p1_26']['scenario_count']} 个测量场景。",
            f"- P1-27：`{report['performance_evidence']['p1_27']['path']}`，P1-26 来源提交为 `{report['performance_evidence']['p1_27']['p1_26_provenance_code_sha']}`。",
            "",
            "性能数据来自生成数据，机器相关，不能当作服务等级承诺；本包没有重跑性能测量。",
            "",
            "## 真实流程证据入口",
            "",
        ]
    )
    for label, evidence in report["real_process_evidence"].items():
        lines.append(f"- {label}：`{evidence['path']}`")
    lines.extend(
        [
            "",
            "本报告只记录版本库内的结构与证据索引；不包含业务正文、绝对路径或运行数据。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_text_durable(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x40000000,  # GENERIC_WRITE is required by FlushFileBuffers.
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS permits directory handles.
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), f"unable to open directory for sync: {path.name}")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), f"unable to sync directory: {path.name}")
    finally:
        kernel32.CloseHandle(handle)


def _is_reparse_point(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _ensure_publication_layout(output_dir: Path) -> tuple[Path, Path]:
    if output_dir.exists() and (_is_reparse_point(output_dir) or not output_dir.is_dir()):
        raise BaselineInputError("report output directory must be a regular directory")
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
        _sync_directory(output_dir.parent)
    publication_dir = output_dir / REPORT_STEM
    releases_dir = publication_dir / "releases"
    for path, parent in ((publication_dir, output_dir), (releases_dir, publication_dir)):
        if path.exists() or path.is_symlink():
            if _is_reparse_point(path) or not path.is_dir():
                raise BaselineInputError("report publication layout must not use reparse points")
        else:
            path.mkdir()
            _sync_directory(parent)
    return publication_dir, releases_dir


def _text_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _release_id_for_contents(contents: dict[str, str]) -> str:
    release_hash = hashlib.sha256()
    for name in (JSON_REPORT_NAME, MARKDOWN_REPORT_NAME):
        release_hash.update(name.encode("utf-8"))
        release_hash.update(b"\0")
        release_hash.update(contents[name].encode("utf-8"))
        release_hash.update(b"\0")
    return release_hash.hexdigest()


def _publication_manifest(release_id: str, contents: dict[str, str]) -> dict[str, Any]:
    return {
        "publication_version": PUBLICATION_VERSION,
        "active_release": release_id,
        "files": {
            "json": {
                "name": JSON_REPORT_NAME,
                "sha256": _text_digest(contents[JSON_REPORT_NAME]),
            },
            "markdown": {
                "name": MARKDOWN_REPORT_NAME,
                "sha256": _text_digest(contents[MARKDOWN_REPORT_NAME]),
            },
        },
}


def _legacy_report_pair(output_dir: Path) -> tuple[Path, Path] | None:
    json_path = output_dir / JSON_REPORT_NAME
    markdown_path = output_dir / MARKDOWN_REPORT_NAME
    present = [path.exists() or path.is_symlink() for path in (json_path, markdown_path)]
    if not any(present):
        return None
    if not all(present):
        raise BaselineInputError("legacy report pair is incomplete")
    if any(_is_reparse_point(path) or not path.is_file() for path in (json_path, markdown_path)):
        raise BaselineInputError("legacy report pair must contain regular files")
    try:
        report = json.loads(json_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")
        if not isinstance(report, dict) or render_markdown(report) != markdown:
            raise BaselineInputError("legacy report pair does not match")
    except BaselineInputError:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BaselineInputError("legacy report pair is invalid") from exc
    return json_path, markdown_path


def resolve_published_report(output_dir: Path) -> tuple[Path, Path]:
    publication_dir = output_dir / REPORT_STEM
    manifest_path = publication_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        legacy = _legacy_report_pair(output_dir)
        if legacy is not None:
            return legacy
        raise BaselineInputError("published report manifest is missing or invalid") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineInputError("published report manifest is missing or invalid") from exc
    release_id = manifest.get("active_release")
    if (
        manifest.get("publication_version") != PUBLICATION_VERSION
        or not isinstance(release_id, str)
        or RELEASE_ID_PATTERN.fullmatch(release_id) is None
    ):
        raise BaselineInputError("published report manifest is invalid")
    files = manifest.get("files")
    expected_names = {
        "json": JSON_REPORT_NAME,
        "markdown": MARKDOWN_REPORT_NAME,
    }
    if not isinstance(files, dict) or set(files) != set(expected_names):
        raise BaselineInputError("published report manifest files are invalid")
    release_dir = publication_dir / "releases" / release_id
    if _is_reparse_point(publication_dir) or _is_reparse_point(release_dir.parent) or _is_reparse_point(release_dir):
        raise BaselineInputError("published report release must not use reparse points")
    resolved: dict[str, Path] = {}
    resolved_contents: dict[str, str] = {}
    for label, expected_name in expected_names.items():
        entry = files.get(label)
        if (
            not isinstance(entry, dict)
            or entry.get("name") != expected_name
            or not isinstance(entry.get("sha256"), str)
            or RELEASE_ID_PATTERN.fullmatch(entry["sha256"]) is None
        ):
            raise BaselineInputError("published report manifest files are invalid")
        path = release_dir / expected_name
        if not path.is_file() or _is_reparse_point(path):
            raise BaselineInputError("published report release is missing or invalid")
        content = path.read_text(encoding="utf-8")
        if _text_digest(content) != entry["sha256"]:
            raise BaselineInputError("published report release is missing or invalid")
        resolved[label] = path
        resolved_contents[expected_name] = content
    if _release_id_for_contents(resolved_contents) != release_id:
        raise BaselineInputError("published report release identity is invalid")
    return resolved["json"], resolved["markdown"]


def _publish_contents(output_dir: Path, contents: dict[str, str]) -> tuple[Path, Path]:
    release_id = _release_id_for_contents(contents)
    manifest = _publication_manifest(release_id, contents)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    publication_dir, releases_dir = _ensure_publication_layout(output_dir)
    release_dir = releases_dir / release_id
    manifest_path = publication_dir / "manifest.json"
    staging: Path | None = None
    manifest_staging: Path | None = None
    try:
        releases_dir.mkdir(parents=True, exist_ok=True)
        if not release_dir.exists():
            staging = Path(tempfile.mkdtemp(prefix=f".staging-{release_id}-", dir=releases_dir))
            for name, content in contents.items():
                _write_text_durable(staging / name, content)
            _sync_directory(staging)
            os.replace(staging, release_dir)
            staging = None
            _sync_directory(releases_dir)
        else:
            if _is_reparse_point(release_dir) or not release_dir.is_dir():
                raise BaselineInputError("existing report release is invalid")
            expected_manifest = _publication_manifest(release_id, contents)
            for entry in expected_manifest["files"].values():
                existing = release_dir / entry["name"]
                if (
                    not existing.is_file()
                    or _is_reparse_point(existing)
                    or _text_digest(existing.read_text(encoding="utf-8")) != entry["sha256"]
                ):
                    raise BaselineInputError("existing report release is invalid")
        descriptor, raw_manifest_staging = tempfile.mkstemp(
            prefix=".manifest-", suffix=".json", dir=publication_dir
        )
        os.close(descriptor)
        manifest_staging = Path(raw_manifest_staging)
        _write_text_durable(manifest_staging, manifest_text)
        os.replace(manifest_staging, manifest_path)
        manifest_staging = None
        _sync_directory(publication_dir)
    except BaselineInputError:
        raise
    except OSError as exc:
        raise BaselineInputError("unable to publish report release") from exc
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if manifest_staging is not None and manifest_staging.exists():
            manifest_staging.unlink(missing_ok=True)
    return resolve_published_report(output_dir)


def publish_report(output_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    publication_dir = output_dir / REPORT_STEM
    manifest_exists = (publication_dir / "manifest.json").exists()
    legacy = None if manifest_exists else _legacy_report_pair(output_dir)
    if legacy is not None:
        legacy_contents = {
            JSON_REPORT_NAME: legacy[0].read_text(encoding="utf-8"),
            MARKDOWN_REPORT_NAME: legacy[1].read_text(encoding="utf-8"),
        }
        _publish_contents(output_dir, legacy_contents)
    contents = {
        JSON_REPORT_NAME: json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        MARKDOWN_REPORT_NAME: render_markdown(report),
    }
    return _publish_contents(output_dir, contents)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 P3-01 只读结构基线")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--p1-26-report", default=DEFAULT_P1_26_REPORT)
    parser.add_argument("--p1-27-report", default=DEFAULT_P1_27_REPORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    try:
        _reject_user_data(output_dir, root, "output directory")
        report = build_report(
            root,
            p1_26_report=args.p1_26_report,
            p1_27_report=args.p1_27_report,
        )
        json_path, markdown_path = publish_report(output_dir, report)
    except BaselineInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{PACKAGE} baseline: {json_path.name}, {markdown_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
