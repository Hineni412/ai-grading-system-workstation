from __future__ import annotations

import argparse
import gc
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.test_suite_manifest import (
    PROCESS_ISOLATED_TEST_PATHS,
    SERIAL_TEST_PATHS,
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".test-runs",
    ".worktrees",
    "__pycache__",
    "runtime",
    "user_data",
}

DEFAULT_SERIAL_TEST_PATHS = SERIAL_TEST_PATHS


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    return_code: int
    elapsed_seconds: float
    messages: list[str]
    skipped: bool = False


def iter_python_files(project_root: Path) -> list[Path]:
    root = Path(project_root).resolve()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in EXCLUDED_DIR_NAMES
        ]
        current = Path(dirpath)
        for filename in filenames:
            if filename.endswith(".py"):
                files.append(current / filename)
    return sorted(files)


def run_documentation_check(project_root: Path = PROJECT_ROOT) -> StepResult:
    from tools.check_documentation import run_checks

    started = time.perf_counter()
    issues = run_checks(Path(project_root).resolve())
    elapsed = time.perf_counter() - started
    if issues:
        messages = [
            f"[{issue.code}] {issue.path}:{issue.line} {issue.message}"
            for issue in issues
        ]
        return StepResult("文档治理", False, 1, elapsed, messages)
    return StepResult(
        "文档治理",
        True,
        0,
        elapsed,
        ["项目文档治理检查通过。"],
    )


def run_static_compile(project_root: Path = PROJECT_ROOT) -> StepResult:
    started = time.perf_counter()
    root = Path(project_root).resolve()
    files = iter_python_files(root)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ai_grading_compile_") as cache_dir_name:
        cache_dir = Path(cache_dir_name)
        for source in files:
            rel = source.relative_to(root)
            target = cache_dir / rel.with_suffix(".pyc")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                py_compile.compile(
                    str(source),
                    cfile=str(target),
                    doraise=True,
                )
            except py_compile.PyCompileError as exc:
                failures.append(f"{rel}: {exc.msg}")

    elapsed = time.perf_counter() - started
    if failures:
        return StepResult(
            "静态编译",
            False,
            1,
            elapsed,
            [f"发现 {len(failures)} 个编译错误。", *failures],
        )
    return StepResult(
        "静态编译",
        True,
        0,
        elapsed,
        [f"已编译 {len(files)} 个第一方 Python 文件。"],
    )


def _pytest_output(completed: subprocess.CompletedProcess[str]) -> tuple[str, list[str]]:
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    summary = lines[-1] if lines else "pytest 未输出摘要。"
    return summary, lines


def run_pytest(
    project_root: Path = PROJECT_ROOT,
    *,
    workers: int = 1,
    durations: int = 0,
    serial_test_paths: tuple[Path, ...] | None = None,
) -> StepResult:
    started = time.perf_counter()
    root = Path(project_root).resolve()
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if durations < 0:
        raise ValueError("durations must not be negative")

    base_command = [sys.executable, "-m", "pytest", "-q"]
    duration_args = [f"--durations={durations}"] if durations else []
    commands: list[tuple[str, list[str]]] = []
    if workers == 1:
        commands.append(("串行全量", [*base_command, *duration_args]))
    else:
        serial_paths = serial_test_paths or DEFAULT_SERIAL_TEST_PATHS
        normalized_serial_paths = tuple(path.as_posix() for path in serial_paths)
        missing = [path for path in normalized_serial_paths if not (root / path).is_file()]
        if missing:
            elapsed = time.perf_counter() - started
            return StepResult(
                "全量测试",
                False,
                2,
                elapsed,
                [f"串行测试清单包含不存在的文件：{missing[0]}"],
            )
        parallel_command = [
            *base_command,
            "-n",
            str(workers),
            "--dist",
            "loadfile",
            *duration_args,
            *(f"--ignore={path}" for path in normalized_serial_paths),
        ]
        commands.append((f"并行车道（{workers}进程）", parallel_command))
        process_isolated_keys = {
            path.as_posix() for path in PROCESS_ISOLATED_TEST_PATHS
        }
        shared_process_paths = tuple(
            path
            for path in normalized_serial_paths
            if path not in process_isolated_keys
        )
        isolated_paths = tuple(
            path
            for path in normalized_serial_paths
            if path in process_isolated_keys
        )
        if shared_process_paths:
            commands.append(
                (
                    "串行车道",
                    [*base_command, *duration_args, *shared_process_paths],
                )
            )
        for path in isolated_paths:
            commands.append(
                (
                    f"独立进程（{Path(path).name}）",
                    [*base_command, *duration_args, path],
                )
            )

    messages: list[str] = []
    return_code = 0
    for label, command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        summary, lines = _pytest_output(completed)
        messages.append(f"{label}：{summary}")
        if completed.returncode != 0:
            return_code = completed.returncode
            messages.extend([f"{label}输出尾部：", *lines[-30:]])
            break

    elapsed = time.perf_counter() - started
    return StepResult(
        "全量测试",
        return_code == 0,
        return_code,
        elapsed,
        messages,
    )


def run_database_idempotency_check(
    *,
    grading_db: Path,
    question_bank_db: Path,
    work_dir: Path | None = None,
) -> StepResult:
    started = time.perf_counter()
    cleanup_dir: Path | None = None
    if work_dir is None:
        base_dir = Path(tempfile.mkdtemp(prefix="ai_grading_db_smoke_"))
        cleanup_dir = base_dir
    else:
        base_dir = Path(work_dir)
        if base_dir.exists():
            _remove_tree_with_retries(base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

    messages: list[str] = []
    failures: list[str] = []
    try:
        checks = (
            ("阅卷库", Path(grading_db), base_dir / "grading_system.db", _initialize_grading_db),
            ("题库库", Path(question_bank_db), base_dir / "question_bank.db", _initialize_question_bank_db),
        )
        for label, source, copied, initializer in checks:
            if not source.exists():
                messages.append(f"{label}不存在，跳过：{source}")
                continue
            _copy_sqlite_database(source, copied)
            initializer(copied)
            first_schema = _schema_snapshot(copied)
            initializer(copied)
            second_schema = _schema_snapshot(copied)
            if first_schema != second_schema:
                failures.append(f"{label}第二次初始化后 Schema 发生变化。")
            integrity = _integrity_check(copied)
            if integrity.lower() != "ok":
                failures.append(f"{label} integrity_check 失败：{integrity}")
            if first_schema == second_schema and integrity.lower() == "ok":
                messages.append(f"{label}副本初始化幂等，integrity_check=ok。")
    finally:
        if cleanup_dir is not None:
            try:
                _remove_tree_with_retries(cleanup_dir)
            except OSError as exc:
                messages.append(f"临时目录清理失败，请稍后手动删除：{cleanup_dir}（{exc}）")

    elapsed = time.perf_counter() - started
    if failures:
        return StepResult(
            "两库初始化幂等",
            False,
            1,
            elapsed,
            [*messages, *failures],
        )
    if not messages:
        messages.append("未找到可检查的数据库，已跳过。")
    return StepResult("两库初始化幂等", True, 0, elapsed, messages)


def run_smoke(
    *,
    project_root: Path = PROJECT_ROOT,
    grading_db: Path | None = None,
    question_bank_db: Path | None = None,
    skip_tests: bool = False,
    pytest_workers: int = 1,
    pytest_durations: int = 0,
) -> list[StepResult]:
    from path_manager import get_path_manager

    root = Path(project_root).resolve()
    pm = get_path_manager()
    resolved_grading_db = Path(grading_db) if grading_db is not None else pm.db_path
    resolved_question_bank_db = Path(question_bank_db) if question_bank_db is not None else pm.qb_db_path
    results = [run_documentation_check(root), run_static_compile(root)]
    if skip_tests:
        results.append(
            StepResult("全量测试", True, 0, 0.0, ["收到 --skip-tests，已跳过全量测试。"], skipped=True)
        )
    else:
        if pytest_workers == 1 and pytest_durations == 0:
            results.append(run_pytest(root))
        else:
            results.append(
                run_pytest(
                    root,
                    workers=pytest_workers,
                    durations=pytest_durations,
                )
            )
    results.append(
        run_database_idempotency_check(
            grading_db=resolved_grading_db,
            question_bank_db=resolved_question_bank_db,
        )
    )
    return results


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, Path(str(destination) + suffix))
    with sqlite3.connect(destination) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _remove_tree_with_retries(
    path: Path,
    *,
    attempts: int = 5,
    delay_seconds: float = 0.2,
) -> None:
    target = Path(path)
    if not target.exists():
        return
    last_error: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            shutil.rmtree(target)
            return
        except OSError as exc:
            last_error = exc
            gc.collect()
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error


def _initialize_grading_db(db_path: Path) -> None:
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from grading_run_store import GradingRunStore

    DBManager(db_path).initialize()
    GradingRunStore(db_path).initialize()
    JobStore(db_path).initialize()


def _initialize_question_bank_db(db_path: Path) -> None:
    from question_bank.database.schema import initialize_database

    initialize_database(db_path)


def _schema_snapshot(db_path: Path) -> tuple[tuple[str, str, str, str], ...]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    return tuple(
        (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or ""),
            " ".join(str(row[3] or "").split()),
        )
        for row in rows
    )


def _integrity_check(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "")


def _print_result(result: StepResult) -> None:
    status = "SKIP" if result.skipped else ("OK" if result.ok else "FAIL")
    print(f"[{status}] {result.name} ({result.elapsed_seconds:.2f}s)")
    for message in result.messages:
        print(f"  - {message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 阅卷系统统一冒烟检查")
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="跳过全量 pytest，仅运行静态编译和数据库初始化幂等检查。",
    )
    parser.add_argument(
        "--parallel-tests",
        action="store_true",
        help="试点：把可隔离测试分配给多个进程，风险测试继续串行。",
    )
    parser.add_argument(
        "--pytest-workers",
        type=int,
        default=2,
        help="并行试点进程数，默认 2；仅在 --parallel-tests 时生效。",
    )
    parser.add_argument(
        "--pytest-durations",
        type=int,
        default=0,
        help="输出最慢的 pytest 用例数量；0 表示不额外输出。",
    )
    args = parser.parse_args(argv)

    results = run_smoke(
        skip_tests=args.skip_tests,
        pytest_workers=args.pytest_workers if args.parallel_tests else 1,
        pytest_durations=args.pytest_durations,
    )
    print("AI 阅卷系统冒烟检查")
    for result in results:
        _print_result(result)

    failures = [result for result in results if not result.ok]
    if failures:
        print("失败步骤：" + "、".join(result.name for result in failures))
        for result in failures:
            if result.return_code:
                return result.return_code
        return 1

    total = sum(result.elapsed_seconds for result in results)
    print(f"冒烟检查通过，总耗时 {total:.2f}s。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
