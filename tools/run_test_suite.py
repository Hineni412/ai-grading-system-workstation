from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.test_suite_manifest import (  # noqa: E402
    PROCESS_ISOLATED_TEST_PATHS,
    QUICK_FRONTEND_TEST_PATHS,
    QUICK_TEST_PATHS,
    RELEASE_AUDIT_TEST_PATHS,
    REVIEW_FRONTEND_TEST_PATHS,
    REVIEW_TEST_PATHS,
    SERIAL_TEST_PATHS,
)


_SUMMARY_PATTERNS = (
    re.compile(r"\b\d+ passed\b"),
    re.compile(r"\b\d+ failed\b"),
    re.compile(r"^ Test Files "),
    re.compile(r"^      Tests "),
    re.compile(r"^   Duration "),
    re.compile(r"^ℹ (?:tests|pass|fail|duration_ms) "),
)
_DURATION_PATTERN = re.compile(r"^\s*\d+(?:\.\d+)?s\s+(?:setup|call|teardown)\s+")


@dataclass(frozen=True)
class CommandResult:
    label: str
    command: tuple[str, ...]
    return_code: int
    elapsed_seconds: float
    output: str
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def default_worker_count(cpu_count: int | None = None) -> int:
    available = max(1, int(cpu_count or os.cpu_count() or 1))
    if available <= 2:
        return 1
    if available <= 4:
        return 2
    if available <= 8:
        return 4
    return 6


def _validate_paths(paths: Sequence[Path]) -> None:
    missing = [path for path in paths if not (PROJECT_ROOT / path).is_file()]
    if missing:
        raise FileNotFoundError(f"测试清单包含不存在的文件：{missing[0].as_posix()}")


def _isolated_environment(sandbox_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        upper = key.upper()
        if "API_KEY" in upper or upper in {"AUTHORIZATION", "OPENAI_API_KEY"}:
            environment.pop(key, None)
    data_root = sandbox_root / "user_data"
    local_root = sandbox_root / "local"
    temporary_root = sandbox_root / "t"
    config_root = local_root / "AIGradingSystem" / "config"
    ops_root = local_root / "AIGradingSystem" / "ops"
    data_root.mkdir(parents=True, exist_ok=True)
    local_root.mkdir(parents=True, exist_ok=True)
    temporary_root.mkdir(parents=True, exist_ok=True)
    for variable in ("TEMP", "TMP", "TMPDIR"):
        environment[variable] = str(temporary_root)
    environment["AI_GRADING_DATA_DIR"] = str(data_root)
    environment["AI_GRADING_WORKTREE_DATA_DIR"] = str(data_root)
    environment["AI_GRADING_API_PROFILES_PATH"] = str(
        config_root / "api_profiles.json"
    )
    environment["AI_GRADING_TAXONOMY_STATE_PATH"] = str(
        config_root / "taxonomy_state_v2.json"
    )
    environment["AI_GRADING_OPS_STATE_DIR"] = str(ops_root)
    environment["LOCALAPPDATA"] = str(local_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_command(
    label: str,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> CommandResult:
    print(f"[开始] {label}", flush=True)
    command = [str(part) for part in command]
    temporary_base = None
    if command[1:3] == ["-m", "pytest"] and environment.get("TEMP"):
        # Short, unique paths avoid SQLite's Windows filename limit. Nested test
        # processes inherit TEMP, not this pytest instance's destructive basetemp.
        temporary_base = tempfile.TemporaryDirectory(
            prefix="p_", dir=environment["TEMP"], ignore_cleanup_errors=True,
        )
        command.append(f"--basetemp={temporary_base.name}")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        # A finished subprocess has released Windows handles. Remove its files
        # before the next lane instead of accumulating every lane's databases.
        if temporary_base is not None:
            temporary_base.cleanup()
    elapsed = time.perf_counter() - started
    output = "\n".join(
        part for part in (completed.stdout, completed.stderr) if part
    ).strip()
    return CommandResult(
        label=label,
        command=tuple(str(part) for part in command),
        return_code=completed.returncode,
        elapsed_seconds=elapsed,
        output=output,
    )


def _pytest_command(
    *,
    workers: int,
    durations: int,
    paths: Sequence[Path] = (),
    ignored_paths: Sequence[Path] = (),
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--strict-markers",
    ]
    if durations:
        command.append(f"--durations={durations}")
    if workers > 1:
        command.extend(["-n", str(workers), "--dist", "loadfile"])
    command.extend(f"--ignore={path.as_posix()}" for path in ignored_paths)
    command.extend(path.as_posix() for path in paths)
    return command


def _run_backend_pair(
    *,
    label_prefix: str,
    parallel_paths: Sequence[Path],
    serial_paths: Sequence[Path],
    ignored_paths: Sequence[Path],
    workers: int,
    durations: int,
    environment: dict[str, str],
) -> list[CommandResult]:
    results: list[CommandResult] = []
    parallel = _run_command(
        f"{label_prefix}后端并行车道（{workers}进程）",
        _pytest_command(
            workers=workers,
            durations=durations,
            paths=parallel_paths,
            ignored_paths=ignored_paths,
        ),
        cwd=PROJECT_ROOT,
        environment=environment,
    )
    results.append(parallel)
    if not parallel.ok or not serial_paths:
        return results
    results.extend(
        _run_serial_paths(
            label=f"{label_prefix}后端隔离车道",
            paths=serial_paths,
            durations=durations,
            environment=environment,
        )
    )
    return results


def _run_serial_paths(
    *,
    label: str,
    paths: Sequence[Path],
    durations: int,
    environment: dict[str, str],
) -> list[CommandResult]:
    isolated_keys = {path.as_posix() for path in PROCESS_ISOLATED_TEST_PATHS}
    shared_process_paths = tuple(
        path for path in paths if path.as_posix() not in isolated_keys
    )
    isolated_paths = tuple(
        path for path in paths if path.as_posix() in isolated_keys
    )
    results: list[CommandResult] = []
    if shared_process_paths:
        results.append(
            _run_command(
                label,
                _pytest_command(
                    workers=1,
                    durations=durations,
                    paths=shared_process_paths,
                ),
                cwd=PROJECT_ROOT,
                environment=environment,
            )
        )
    for path in isolated_paths:
        results.append(
            _run_command(
                f"{label}（独立进程：{path.name}）",
                _pytest_command(
                    workers=1,
                    durations=durations,
                    paths=(path,),
                ),
                cwd=PROJECT_ROOT,
                environment=environment,
            )
        )
    return results


def _npm_command(*arguments: str) -> list[str]:
    executable = shutil.which("npm.cmd") or shutil.which("npm")
    if executable is None:
        raise FileNotFoundError("找不到 npm，无法运行前端验收。")
    return [executable, *arguments]


def _run_frontend(
    *,
    full: bool,
    environment: dict[str, str],
) -> list[CommandResult]:
    if full:
        verification = _run_command(
            "前端静态检查、单元测试与构建",
            _npm_command("run", "verify"),
            cwd=FRONTEND_ROOT,
            environment=environment,
        )
        if not verification.ok:
            return [verification]
        return [verification, _run_review_browser(environment=environment)]
    selected_paths = tuple(
        (PROJECT_ROOT / path).relative_to(FRONTEND_ROOT).as_posix()
        for path in QUICK_FRONTEND_TEST_PATHS
    )
    result = _run_command(
        "前端关键流程测试",
        _npm_command("run", "test", "--", *selected_paths),
        cwd=FRONTEND_ROOT,
        environment=environment,
    )
    if not result.ok:
        return [result]
    return [result, _run_command(
        "题框编辑器测试",
        _npm_command("run", "test:editor"),
        cwd=FRONTEND_ROOT,
        environment=environment,
    )]


def _split_paths(
    paths: Sequence[Path],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    serial_keys = {path.as_posix() for path in SERIAL_TEST_PATHS}
    parallel = tuple(path for path in paths if path.as_posix() not in serial_keys)
    serial = tuple(path for path in paths if path.as_posix() in serial_keys)
    return parallel, serial


def _run_review_suite(
    *, workers: int, durations: int, environment: dict[str, str],
) -> list[CommandResult]:
    results = [_run_command(
        "复核保存规则与原子写入",
        _pytest_command(workers=workers, durations=durations, paths=REVIEW_TEST_PATHS),
        cwd=PROJECT_ROOT, environment=environment,
    )]
    if not results[-1].ok:
        return results
    results.append(_run_command(
        "复核页面草稿、确认与失败处理",
        _npm_command("run", "test", "--", *(
            path.relative_to("frontend").as_posix()
            for path in REVIEW_FRONTEND_TEST_PATHS
        )),
        cwd=FRONTEND_ROOT, environment=environment,
    ))
    if not results[-1].ok:
        return results

    results.append(_run_review_browser(environment=environment))
    return results


def _run_review_browser(*, environment: dict[str, str]) -> CommandResult:
    output_root = PROJECT_ROOT / "output"
    output_root.mkdir(exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="review_browser_", dir=output_root))
    browser_environment = environment.copy()
    with socket.socket() as api_socket, socket.socket() as web_socket:
        api_socket.bind(("127.0.0.1", 0))
        web_socket.bind(("127.0.0.1", 0))
        browser_environment["AI_GRADING_REVIEW_API_PORT"] = str(api_socket.getsockname()[1])
        browser_environment["AI_GRADING_REVIEW_WEB_PORT"] = str(web_socket.getsockname()[1])
    browser_environment["AI_GRADING_REVIEW_PYTHON"] = sys.executable
    browser_environment["AI_GRADING_REVIEW_ARTIFACTS"] = str(artifacts)
    # The test data use an isolated LOCALAPPDATA; the installed browser is read-only.
    browser_environment["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "ms-playwright"),
    )
    # Full runs this beside backend tests, so the browser must own its data root.
    sandbox_parent = PROJECT_ROOT / ".test-runs"
    sandbox_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="web_", dir=sandbox_parent, ignore_cleanup_errors=True) as sandbox:
        browser_environment.update(_isolated_environment(Path(sandbox)))
        result = _run_command(
            "浏览器改分、重进、冲突保护与成绩一致性",
            _npm_command("exec", "--no", "--", "playwright", "test", "--config", "playwright.review.config.ts"),
            cwd=FRONTEND_ROOT, environment=browser_environment,
        )
    (artifacts / "run.log").write_text(result.output, encoding="utf-8")
    print(f"浏览器验收截图与日志：{artifacts}", flush=True)
    return result


def _run_current_suite(
    *,
    mode: str,
    workers: int,
    durations: int,
    skip_frontend: bool,
    environment: dict[str, str],
) -> list[CommandResult]:
    if mode == "quick":
        parallel_paths, serial_paths = _split_paths(QUICK_TEST_PATHS)
        ignored_paths: tuple[Path, ...] = ()
        label_prefix = "快速"
        frontend_full = False
    else:
        release_keys = {path.as_posix() for path in RELEASE_AUDIT_TEST_PATHS}
        serial_paths = tuple(
            path
            for path in SERIAL_TEST_PATHS
            if path.as_posix() not in release_keys
        )
        parallel_paths = ()
        ignored_paths = tuple(
            dict.fromkeys((*SERIAL_TEST_PATHS, *RELEASE_AUDIT_TEST_PATHS))
        )
        label_prefix = "完整"
        frontend_full = True

    def run_backend() -> list[CommandResult]:
        return _run_backend_pair(
            label_prefix=label_prefix,
            parallel_paths=parallel_paths,
            serial_paths=serial_paths,
            ignored_paths=ignored_paths,
            workers=workers,
            durations=durations,
            environment=environment,
        )

    if skip_frontend:
        return run_backend()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="acceptance") as executor:
        backend_future = executor.submit(run_backend)
        frontend_future = executor.submit(
            _run_frontend,
            full=frontend_full,
            environment=environment,
        )
        backend_results = backend_future.result()
        frontend_results = frontend_future.result()
    return [*backend_results, *frontend_results]


def _run_release_audit(
    *,
    workers: int,
    durations: int,
    environment: dict[str, str],
) -> list[CommandResult]:
    parallel_paths, serial_paths = _split_paths(RELEASE_AUDIT_TEST_PATHS)
    return _run_backend_pair(
        label_prefix="发布证据",
        parallel_paths=parallel_paths,
        serial_paths=serial_paths,
        ignored_paths=(),
        workers=workers,
        durations=durations,
        environment=environment,
    )


def _summary_lines(output: str, *, failure: bool) -> list[str]:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    durations = [line for line in lines if _DURATION_PATTERN.match(line)]
    if failure:
        highlights: list[str] = []
        for line in lines:
            stripped = line.strip()
            if (
                re.match(r"^(?:FAILED|FAIL|ERROR)(?:\s|$)", stripped)
                or re.match(r"^E\s+", stripped)
                or re.search(
                    r"\b(?:Error|AssertionError|[A-Za-z_][\w.]*Error|"
                    r"[A-Za-z_][\w.]*Exception)(?::|$)",
                    stripped,
                )
                or re.search(r"\b\d+ failed\b", stripped)
            ):
                if line not in highlights:
                    highlights.append(line)
        if highlights:
            return [*highlights[-120:], *durations]
        return lines[-120:]
    summaries = [
        line
        for line in lines
        if any(pattern.search(line) for pattern in _SUMMARY_PATTERNS)
    ]
    if summaries or durations:
        return [*summaries[-12:], *durations]
    return lines[-8:]


def _print_results(results: Sequence[CommandResult], elapsed_seconds: float) -> None:
    print("\n测试结果")
    for result in results:
        status = "跳过" if result.skipped else ("通过" if result.ok else "失败")
        print(f"[{status}] {result.label}（{result.elapsed_seconds:.1f} 秒）")
        for line in _summary_lines(result.output, failure=not result.ok):
            print(f"  {line}")
    print(f"总墙钟耗时：{elapsed_seconds:.1f} 秒")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description="AI 阅卷系统分层验收测试")
    parser.add_argument(
        "mode",
        choices=("quick", "full", "serial", "release", "review"),
        nargs="?",
        default="quick",
        help="quick=日常快速；full=合并验收；serial=隔离组；release=含历史发布证据；review=复核保存浏览器验收",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=default_worker_count(),
        help="后端并行进程数；默认按本机处理器数选择，最多 6。",
    )
    parser.add_argument(
        "--durations",
        type=int,
        default=10,
        help="每条车道输出最慢用例数量，默认 10；0 关闭耗时明细。",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="仅排查后端测试时使用；正式 full/release 不应跳过前端。",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers 必须至少为 1")
    if args.durations < 0:
        parser.error("--durations 不能为负数")
    if args.mode == "review" and args.skip_frontend:
        parser.error("review 包含真实浏览器操作，不能使用 --skip-frontend")

    _validate_paths(
        tuple(dict.fromkeys((
            *QUICK_TEST_PATHS,
            *QUICK_FRONTEND_TEST_PATHS,
            *SERIAL_TEST_PATHS,
            *RELEASE_AUDIT_TEST_PATHS,
            *REVIEW_TEST_PATHS,
            *REVIEW_FRONTEND_TEST_PATHS,
        )))
    )
    started = time.perf_counter()
    sandbox_parent = PROJECT_ROOT / ".test-runs"
    sandbox_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="run_",
        dir=sandbox_parent,
        ignore_cleanup_errors=True,
    ) as sandbox_name:
        environment = _isolated_environment(Path(sandbox_name))
        if args.mode == "review":
            results = _run_review_suite(
                workers=args.workers, durations=args.durations, environment=environment,
            )
        elif args.mode == "serial":
            results = _run_serial_paths(
                label="后端隔离车道",
                paths=SERIAL_TEST_PATHS,
                durations=args.durations,
                environment=environment,
            )
        else:
            current_mode = "quick" if args.mode == "quick" else "full"
            results = _run_current_suite(
                mode=current_mode,
                workers=args.workers,
                durations=args.durations,
                skip_frontend=args.skip_frontend,
                environment=environment,
            )
            if args.mode == "release" and all(result.ok for result in results):
                results.extend(
                    _run_release_audit(
                        workers=args.workers,
                        durations=args.durations,
                        environment=environment,
                    )
                )

    elapsed = time.perf_counter() - started
    _print_results(results, elapsed)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
