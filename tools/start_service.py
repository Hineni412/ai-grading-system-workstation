"""Start the AI grading API without keeping the caller's command pipe open.

This helper is intended for Codex-driven background restarts. Teachers should
continue to use ``运行.bat`` for normal interactive startup.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _prefer_current_project() -> None:
    project_root = str(PROJECT_ROOT)
    if not sys.path or sys.path[0] != project_root:
        sys.path.insert(0, project_root)


_prefer_current_project()

from backend.startup_storage_preflight import (  # noqa: E402
    TaxonomyStoragePreflightError,
    preflight_taxonomy_storage,
)
from path_manager import PathManager  # noqa: E402


def _taxonomy_state_path() -> Path:
    return PathManager().taxonomy_state_path


def _runtime_python_candidates(project_root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    candidates = [project_root / "runtime" / "python" / "python.exe"]
    if len(project_root.parents) > 1:
        shared_runtime = (
            project_root.parents[1] / "runtime" / "python" / "python.exe"
        )
        if shared_runtime not in candidates:
            candidates.append(shared_runtime)
    return tuple(candidates)


def _runtime_python() -> Path:
    candidates = _runtime_python_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Portable Python runtime was not found.")


def _service_data_dir(
    environment: Mapping[str, str],
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Keep an explicitly selected data directory across a detached restart."""

    configured = (
        environment.get("AI_GRADING_WORKTREE_DATA_DIR", "").strip()
        or environment.get("AI_GRADING_DATA_DIR", "").strip()
    )
    return Path(configured).resolve() if configured else project_root / "user_data"


def _service_api_profiles_path(
    environment: Mapping[str, str],
) -> Path:
    """Use the machine-local profile store unless a full-file override is explicit."""

    configured = environment.get("AI_GRADING_API_PROFILES_PATH", "").strip()
    return (
        Path(configured).resolve()
        if configured
        else PathManager().api_profiles_path.resolve()
    )


def _listener_exists(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/healthz",
            timeout=0.8,
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return (
                payload.get("status") == "ok"
                and payload.get("service") == "ai-grading-api"
            )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the AI grading API as a detached Windows process."
    )
    parser.add_argument("--port", type=int, default=8035)
    parser.add_argument("--wait-seconds", type=float, default=12.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("Port must be between 1024 and 65535.")
    if not 1 <= args.wait_seconds <= 30:
        raise ValueError("wait-seconds must be between 1 and 30.")
    if _listener_exists(args.port):
        print(
            json.dumps(
                {
                    "started": False,
                    "reason": "port_in_use",
                    "port": args.port,
                }
            )
        )
        return 2

    taxonomy_state_path = _taxonomy_state_path()
    try:
        preflight_taxonomy_storage(taxonomy_state_path)
    except TaxonomyStoragePreflightError as exc:
        print(
            json.dumps(
                {
                    "started": False,
                    "reason": "taxonomy_storage_unwritable",
                    "storage_path": str(exc.state_path),
                    "detail": exc.user_message,
                    "port": args.port,
                },
                ensure_ascii=False,
            )
        )
        return 5

    python_exe = _runtime_python()
    runner = PROJECT_ROOT / "tools" / "run_project_module.py"
    environment = os.environ.copy()
    data_dir = _service_data_dir(environment)
    api_profiles_path = _service_api_profiles_path(environment)
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    environment.update(
        {
            "AI_GRADING_WORKTREE_DATA_DIR": str(data_dir),
            "AI_GRADING_DATA_DIR": str(data_dir),
            "AI_GRADING_OPS_STATE_DIR": str(data_dir / "runtime_state" / "ops"),
            "AI_GRADING_API_PROFILES_PATH": str(api_profiles_path),
            "AI_GRADING_NO_BROWSER": "1",
            "PYTHONUTF8": "1",
        }
    )

    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )

    stdout_path = log_dir / "service.stdout.log"
    stderr_path = log_dir / "service.stderr.log"
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [
                str(python_exe),
                str(runner),
                "backend.api.launcher",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--no-browser",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creation_flags,
        )

    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        if _healthy(args.port):
            print(
                json.dumps(
                    {
                        "started": True,
                        "process_id": process.pid,
                        "port": args.port,
                    }
                )
            )
            return 0
        if process.poll() is not None:
            print(
                json.dumps(
                    {
                        "started": False,
                        "reason": "process_exited",
                        "exit_code": process.returncode,
                        "port": args.port,
                    }
                )
            )
            return 3
        time.sleep(0.2)

    print(
        json.dumps(
            {
                "started": False,
                "reason": "health_timeout",
                "process_id": process.pid,
                "port": args.port,
            }
        )
    )
    return 4


if __name__ == "__main__":
    sys.exit(main())
