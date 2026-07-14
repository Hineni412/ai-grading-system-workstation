from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO


PACKAGE = "P1-29"
METADATA_FILENAME = "p1-29-acceptance.json"
_SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_CONFIG_KEY_RE = re.compile(
    r"^[ \t]*(?P<key>DATA_DIR|LOGS_DIR|[\"']DATA_DIR[\"']|[\"']LOGS_DIR[\"'])[ \t]*:"
)
_EXPECTED_COUNTS = {
    "students": 2,
    "sessions": 1,
    "results": 2,
    "reports": 1,
}
_EXPECTED_FILE_KEYS = {
    "grading_database",
    "question_bank_database",
    "report",
}


class AcceptanceError(RuntimeError):
    """Raised when an acceptance workspace fails a safety requirement."""


_BOOTSTRAP_MODULES = {
    "seed": (
        "ai_grader",
        "backend.api.app",
        "db_manager",
        "question_bank.database.schema",
        "report",
        "scanner",
        "tests.api_e2e.harness",
    ),
    "uvicorn": (
        "backend.api.app",
        "backend.api.dependencies",
        "db_manager",
        "path_manager",
    ),
    "streamlit": (
        "backend.llm.policy",
        "db_manager",
        "path_manager",
        "question_bank.database.paths",
        "report",
        "scanner",
        "web_app",
    ),
}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_workspace(
    workspace: Path | str,
    *,
    require_empty: bool = True,
) -> Path:
    candidate = Path(workspace).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if candidate == temp_root or not _is_relative_to(candidate, temp_root):
        raise AcceptanceError("workspace must be strictly below the system temporary directory")
    if any(part.casefold() == "user_data" for part in candidate.parts):
        raise AcceptanceError("workspace must not contain a user_data path segment")
    if candidate.exists() and not candidate.is_dir():
        raise AcceptanceError("workspace must be an empty directory")
    if require_empty and candidate.exists() and next(candidate.iterdir(), None) is not None:
        raise AcceptanceError("workspace must be empty")
    return candidate


def _resolved_path_entry(value: str) -> Path:
    return Path(value or os.getcwd()).resolve()


def _runtime_repository_root() -> Path:
    executable = Path(sys.executable).resolve()
    try:
        return executable.parents[2]
    except IndexError as exc:
        raise AcceptanceError("runtime repository root could not be identified") from exc


def _configure_staged_imports(
    workspace: Path,
    *,
    forbidden_roots: Iterable[Path],
) -> Path:
    target = validate_workspace(workspace, require_empty=False)
    staged_launcher_root = Path(__file__).resolve().parents[1]
    if staged_launcher_root != target:
        raise AcceptanceError("bootstrap launcher must come from the staged workspace")
    runtime_library_root = Path(sys.executable).resolve().parent
    forbidden = {
        _runtime_repository_root(),
        *(Path(root).resolve() for root in forbidden_roots),
    }
    forbidden.discard(target)
    retained: list[str] = []
    for entry in sys.path:
        resolved = _resolved_path_entry(entry)
        if resolved == target or resolved in forbidden:
            continue
        if _is_relative_to(resolved, runtime_library_root):
            retained.append(str(resolved))
    sys.path[:] = [str(target), *retained]
    importlib.invalidate_caches()
    resolved_paths = {_resolved_path_entry(entry) for entry in sys.path}
    if sys.path[0] != str(target) or resolved_paths.intersection(forbidden):
        raise AcceptanceError("bootstrap retained a forbidden repository import path")
    return target


def _module_origin_in_workspace(module_name: str, workspace: Path) -> Path:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError) as exc:
        raise AcceptanceError(
            f"staged module {module_name!r} could not be resolved"
        ) from exc
    if spec is None:
        raise AcceptanceError(f"staged module {module_name!r} could not be resolved")
    candidates: list[Path] = []
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
        candidates.append(Path(spec.origin).resolve())
    if spec.submodule_search_locations is not None:
        candidates.extend(
            Path(location).resolve() for location in spec.submodule_search_locations
        )
    if not candidates or any(
        not _is_relative_to(candidate, workspace) for candidate in candidates
    ):
        raise AcceptanceError(f"staged module {module_name!r} resolved outside workspace")
    return candidates[0]


def _verify_bootstrap_modules(target: str, workspace: Path) -> None:
    for module_name in _BOOTSTRAP_MODULES[target]:
        _module_origin_in_workspace(module_name, workspace)


def _verify_loaded_project_modules(module_names: Iterable[str], workspace: Path) -> None:
    for module_name in module_names:
        module = sys.modules.get(module_name)
        origin = getattr(module, "__file__", None) if module is not None else None
        if not origin or not _is_relative_to(Path(origin).resolve(), workspace):
            raise AcceptanceError(
                f"loaded project module {module_name!r} is outside workspace"
            )


def resolve_source_sha(source_ref: str, *, repo_root: Path) -> str:
    if not _SHA_RE.fullmatch(source_ref):
        raise AcceptanceError("source ref must be a full 40-character SHA")
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"{source_ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    resolved = completed.stdout.strip()
    if completed.returncode != 0 or not _SHA_RE.fullmatch(resolved):
        raise AcceptanceError("source SHA does not resolve to a local commit")
    if resolved.casefold() != source_ref.casefold():
        raise AcceptanceError("source SHA did not resolve exactly")
    return resolved.lower()


def _normalized_archive_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise AcceptanceError("archive contains an unsafe path")
    if any(part.casefold() == "user_data" for part in posix_path.parts):
        raise AcceptanceError("archive must not contain user_data")
    return posix_path


def validate_archive_members(members: Iterable[tarfile.TarInfo]) -> None:
    seen: set[str] = set()
    for member in members:
        normalized = _normalized_archive_path(member.name)
        key = normalized.as_posix().casefold()
        if key in seen:
            raise AcceptanceError("archive contains duplicate paths")
        seen.add(key)
        if member.issym() or member.islnk():
            raise AcceptanceError("archive contains a forbidden link")
        if not member.isdir() and not member.isfile():
            raise AcceptanceError("archive contains an unsupported member type")


def extract_validated_archive(archive_file: BinaryIO, workspace: Path) -> None:
    workspace = workspace.resolve()
    with tarfile.open(fileobj=archive_file, mode="r:*") as archive:
        members = archive.getmembers()
        validate_archive_members(members)
        for member in members:
            relative = _normalized_archive_path(member.name)
            target = workspace.joinpath(*relative.parts)
            if not _is_relative_to(target.resolve(), workspace):
                raise AcceptanceError("archive member escaped the workspace")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise AcceptanceError("archive file member could not be read")
            with source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


def _archive_commit(source_sha: str, *, repo_root: Path, workspace: Path) -> None:
    descriptor, archive_name = tempfile.mkstemp(prefix="p1-29-", suffix=".tar")
    os.close(descriptor)
    archive_path = Path(archive_name)
    try:
        with archive_path.open("wb") as archive_output:
            completed = subprocess.run(
                [
                    "git",
                    "archive",
                    "--format=tar",
                    source_sha,
                    "--",
                    ".",
                    ":(exclude)user_data",
                ],
                cwd=repo_root,
                stdout=archive_output,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            raise AcceptanceError("git archive failed for the exact source SHA")
        with archive_path.open("rb") as archive_input:
            extract_validated_archive(archive_input, workspace)
    finally:
        archive_path.unlink(missing_ok=True)


def _replace_config_line(line: str, value: str) -> str:
    if line.endswith("\r\n"):
        ending = "\r\n"
    elif line.endswith("\n"):
        ending = "\n"
    else:
        ending = ""
    return f"{value}{ending}"


def rewrite_staged_config(workspace: Path) -> None:
    config_path = workspace / "config" / "app_config.yaml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AcceptanceError("staged config/app_config.yaml is unavailable") from exc
    lines = text.splitlines(keepends=True)
    positions: dict[str, list[int]] = {"DATA_DIR": [], "LOGS_DIR": []}
    for index, line in enumerate(lines):
        match = _CONFIG_KEY_RE.match(line)
        if match:
            positions[match.group("key").strip("\"'")].append(index)
    if any(len(indexes) != 1 for indexes in positions.values()):
        raise AcceptanceError("staged config must contain exactly one DATA_DIR and LOGS_DIR key")
    data_index = positions["DATA_DIR"][0]
    logs_index = positions["LOGS_DIR"][0]
    lines[data_index] = _replace_config_line(lines[data_index], "DATA_DIR: synthetic_data")
    lines[logs_index] = _replace_config_line(lines[logs_index], "LOGS_DIR: acceptance_logs")
    config_path.write_text("".join(lines), encoding="utf-8", newline="")


def _validate_staged_config(workspace: Path) -> None:
    config_path = workspace / "config" / "app_config.yaml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AcceptanceError("prepared workspace config is unavailable") from exc
    values: dict[str, list[str]] = {"DATA_DIR": [], "LOGS_DIR": []}
    for line in lines:
        match = _CONFIG_KEY_RE.match(line)
        if match:
            values[match.group("key").strip("\"'")].append(line.split(":", 1)[1].strip())
    if values != {"DATA_DIR": ["synthetic_data"], "LOGS_DIR": ["acceptance_logs"]}:
        raise AcceptanceError("prepared workspace config is not acceptance-safe")


def validate_ports(api_port: int, streamlit_port: int) -> tuple[int, int]:
    ports = (api_port, streamlit_port)
    if any(isinstance(port, bool) or not isinstance(port, int) for port in ports):
        raise AcceptanceError("ports must be integers")
    if any(port < 1024 or port > 65535 for port in ports):
        raise AcceptanceError("ports must be between 1024 and 65535")
    if api_port == streamlit_port:
        raise AcceptanceError("API and Streamlit ports must be distinct")
    return ports


def _validate_logical_file(workspace: Path, value: Any) -> None:
    if not isinstance(value, str):
        raise AcceptanceError("metadata contains a non-string logical filename")
    logical = _normalized_archive_path(value)
    target = workspace.joinpath(*logical.parts).resolve()
    if not _is_relative_to(target, workspace.resolve()) or not target.is_file():
        raise AcceptanceError("metadata logical file is missing or unsafe")


def load_prepared_metadata(workspace: Path) -> dict[str, Any]:
    metadata_path = workspace / METADATA_FILENAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("prepared workspace metadata is unavailable") from exc
    if not isinstance(metadata, dict) or set(metadata) != {
        "package",
        "source_sha",
        "session_id",
        "counts",
        "files",
    }:
        raise AcceptanceError("metadata keys are not allowlisted")
    if metadata["package"] != PACKAGE or not _SHA_RE.fullmatch(str(metadata["source_sha"])):
        raise AcceptanceError("metadata package or source SHA is invalid")
    if isinstance(metadata["session_id"], bool) or not isinstance(metadata["session_id"], int):
        raise AcceptanceError("metadata session id is invalid")
    if metadata["session_id"] < 1 or metadata["counts"] != _EXPECTED_COUNTS:
        raise AcceptanceError("metadata logical record counts are invalid")
    files = metadata["files"]
    if not isinstance(files, dict) or set(files) != _EXPECTED_FILE_KEYS:
        raise AcceptanceError("metadata logical filenames are not allowlisted")
    for value in files.values():
        _validate_logical_file(workspace, value)
    return metadata


def _write_metadata(workspace: Path, metadata: dict[str, Any]) -> None:
    target = workspace / METADATA_FILENAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(target)


def seed_workspace(workspace: Path, source_sha: str) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from db_manager import DBManager, StudentRecord
    from question_bank.database.schema import initialize_database
    from tests.api_e2e.harness import (
        ApiE2EHarness,
        E2EControls,
        build_job_manager,
        build_paths,
        install_dependency_overrides,
    )

    workspace = validate_workspace(workspace, require_empty=False)
    _verify_loaded_project_modules(
        (
            "ai_grader",
            "backend.api.app",
            "db_manager",
            "question_bank.database.schema",
            "report",
            "scanner",
            "tests.api_e2e.harness",
        ),
        workspace,
    )
    _validate_staged_config(workspace)
    if not _SHA_RE.fullmatch(source_sha):
        raise AcceptanceError("internal seed source SHA is invalid")
    paths = build_paths(workspace)
    db = DBManager(paths.db_path)
    db.initialize()
    initialize_database(paths.qb_db_path)
    db.upsert_students(
        [
            StudentRecord("SYN-001", "Synthetic Student A", "Synthetic Class"),
            StudentRecord("SYN-002", "Synthetic Student B", "Synthetic Class"),
        ]
    )
    controls = E2EControls()
    manager = build_job_manager(paths, controls=controls)
    report_filename = ""
    session_id = 0
    try:
        app = create_app()
        install_dependency_overrides(app, db=db, manager=manager, paths=paths)
        with TestClient(app) as client:
            harness = ApiE2EHarness(client, db, manager, paths, controls)
            session_id = harness.create_configured_session()
            scan_job = harness.scan(session_id)
            if scan_job["result"]["summary"]["auto_matched"] != 2:
                raise AssertionError("synthetic scan did not match both records")
            initial = harness.grade(session_id)
            initial_summary = initial["result"]["summary"]
            if (
                initial_summary["graded"] != 1
                or initial_summary["failed"] != 1
            ):
                raise AssertionError("synthetic partial grading outcome changed")
            recovered = harness.grade(session_id, failed_only=True)
            recovered_summary = recovered["result"]["summary"]
            if (
                recovered_summary["graded"] != 1
                or recovered_summary["failed"] != 0
            ):
                raise AssertionError("synthetic failed-only recovery outcome changed")

            items = client.get(f"/api/sessions/{session_id}/review/questions/Q1/items")
            if items.status_code != 200:
                raise AssertionError("synthetic Q1 review items were unavailable")
            first = next(
                item
                for item in items.json()["items"]
                if item["student_code"] == "SYN-001"
            )
            confirmed = client.post(
                f"/api/sessions/{session_id}/review/questions/Q1/confirm",
                json={
                    "items": [
                        {
                            "result_id": first["result_id"],
                            "detail_id": first["detail_id"],
                            "score_awarded": 17,
                            "deduction_reason": "synthetic teacher confirmation",
                        }
                    ]
                },
            )
            if confirmed.status_code != 200:
                raise AssertionError("synthetic Q1 confirmation failed")
            expected_scores = {"SYN-001": 90.0, "SYN-002": 70.0}
            if harness.result_scores(session_id) != expected_scores:
                raise AssertionError("synthetic final scores changed")

            exported = client.post(f"/api/sessions/{session_id}/reports/export")
            if exported.status_code != 202:
                raise AssertionError("synthetic report export was not accepted")
            report_job = harness.poll_job(exported.json()["id"], "succeeded")
            report_filename = str(report_job["result"]["filename"])
            download = client.get(report_job["result"]["download_url"])
            if download.status_code != 200:
                raise AssertionError("synthetic report download failed")
            for student_code, expected_score in expected_scores.items():
                if harness.xlsx_score(download.content, student_code) != expected_score:
                    raise AssertionError("synthetic XLSX score changed")
    finally:
        manager.shutdown()

    report_path = paths.reports_dir / report_filename
    if not report_path.is_file():
        raise AssertionError("synthetic XLSX report was not persisted")
    with db._connect() as connection:
        counts = {
            "students": int(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]),
            "sessions": int(connection.execute("SELECT COUNT(*) FROM grading_sessions").fetchone()[0]),
            "results": int(connection.execute("SELECT COUNT(*) FROM session_results").fetchone()[0]),
            "reports": len(list(paths.reports_dir.glob("*.xlsx"))),
        }
    if counts != _EXPECTED_COUNTS:
        raise AssertionError("synthetic logical record counts changed")
    metadata = {
        "package": PACKAGE,
        "source_sha": source_sha.lower(),
        "session_id": session_id,
        "counts": counts,
        "files": {
            "grading_database": paths.db_path.relative_to(workspace).as_posix(),
            "question_bank_database": paths.qb_db_path.relative_to(workspace).as_posix(),
            "report": report_path.relative_to(workspace).as_posix(),
        },
    }
    _write_metadata(workspace, metadata)
    return load_prepared_metadata(workspace)


def _bootstrap_command(
    workspace: Path,
    target: str,
    *target_arguments: str,
) -> list[str]:
    return [
        sys.executable,
        str((workspace / "tools" / "p1_29_acceptance.py").resolve()),
        "_bootstrap",
        "--workspace",
        str(workspace.resolve()),
        "--target",
        target,
        "--forbid-root",
        str(Path(__file__).resolve().parents[1]),
        *target_arguments,
    ]


def _run_bootstrap(args: argparse.Namespace) -> int:
    workspace = _configure_staged_imports(
        args.workspace,
        forbidden_roots=[Path(root) for root in args.forbid_root],
    )
    if args.target == "probe":
        if not args.probe_module:
            raise AcceptanceError("bootstrap probe module is required")
        origin = _module_origin_in_workspace(args.probe_module, workspace)
        importlib.import_module(args.probe_module)
        _verify_loaded_project_modules((args.probe_module,), workspace)
        print(
            json.dumps(
                {
                    "module": args.probe_module,
                    "origin": str(origin),
                    "sys_path": list(sys.path),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        return 0
    _verify_bootstrap_modules(args.target, workspace)
    if args.target == "seed":
        if not args.source_sha:
            raise AcceptanceError("bootstrap seed source SHA is required")
        result = seed_workspace(workspace, args.source_sha)
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    if args.api_port is None:
        raise AcceptanceError("bootstrap service port is required")
    if args.target == "uvicorn":
        sys.argv = [
            "uvicorn",
            "backend.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.api_port),
        ]
        runpy.run_module("uvicorn", run_name="__main__", alter_sys=True)
        return 0
    web_app = (workspace / "web_app.py").resolve()
    if not _is_relative_to(web_app, workspace) or not web_app.is_file():
        raise AcceptanceError("staged Streamlit script is unavailable")
    sys.argv = [
        "streamlit",
        "run",
        str(web_app),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(args.api_port),
        "--server.headless",
        "true",
    ]
    runpy.run_module("streamlit", run_name="__main__", alter_sys=True)
    return 0


def _run_internal_seed(workspace: Path, source_sha: str) -> None:
    logs_dir = workspace / "acceptance_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        _bootstrap_command(
            workspace,
            "seed",
            "--source-sha",
            source_sha,
        ),
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    (logs_dir / "seed.log").write_text(
        completed.stdout + completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AcceptanceError("internal synthetic seeding failed; see the workspace log")


def prepare_workspace(source_ref: str, workspace: Path, *, repo_root: Path) -> dict[str, Any]:
    target = validate_workspace(workspace)
    source_sha = resolve_source_sha(source_ref, repo_root=repo_root)
    target.mkdir(parents=True, exist_ok=True)
    _archive_commit(source_sha, repo_root=repo_root, workspace=target)
    rewrite_staged_config(target)
    _run_internal_seed(target, source_sha)
    return load_prepared_metadata(target)


def _process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _health_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError):
        return False


def _readiness_result(metadata: dict[str, Any], api_port: int, streamlit_port: int) -> dict[str, Any]:
    return {
        "package": PACKAGE,
        "source_sha": metadata["source_sha"],
        "ready": True,
        "api": f"http://127.0.0.1:{api_port}/healthz",
        "streamlit": f"http://127.0.0.1:{streamlit_port}/_stcore/health",
    }


def run_servers(
    workspace: Path,
    api_port: int,
    streamlit_port: int,
    *,
    serve: bool,
    readiness_timeout: float = 60.0,
) -> dict[str, Any] | None:
    target = validate_workspace(workspace, require_empty=False)
    validate_ports(api_port, streamlit_port)
    _validate_staged_config(target)
    metadata = load_prepared_metadata(target)
    logs_dir = target / "acceptance_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    commands = [
        _bootstrap_command(target, "uvicorn", "--api-port", str(api_port)),
        _bootstrap_command(
            target,
            "streamlit",
            "--api-port",
            str(streamlit_port),
        ),
    ]
    processes: list[subprocess.Popen[Any]] = []
    log_handles: list[BinaryIO] = []
    try:
        for command, log_name in zip(commands, ("api.log", "streamlit.log"), strict=True):
            log_handle = (logs_dir / log_name).open("ab")
            log_handles.append(log_handle)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=target,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    **_process_options(),
                )
            )
        urls = (
            f"http://127.0.0.1:{api_port}/healthz",
            f"http://127.0.0.1:{streamlit_port}/_stcore/health",
        )
        deadline = time.monotonic() + readiness_timeout
        ready = [False, False]
        while not all(ready):
            if any(process.poll() is not None for process in processes):
                raise AcceptanceError("acceptance child process exited before readiness")
            ready = [state or _health_ready(url) for state, url in zip(ready, urls, strict=True)]
            if all(ready):
                break
            if time.monotonic() >= deadline:
                raise AcceptanceError("acceptance services did not become ready in time")
            time.sleep(0.2)
        result = _readiness_result(metadata, api_port, streamlit_port)
        if not serve:
            return result
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)
        while True:
            if any(process.poll() is not None for process in processes):
                raise AcceptanceError("acceptance child process exited while serving")
            time.sleep(0.5)
    finally:
        for process in reversed(processes):
            _terminate_process_tree(process)
        for handle in log_handles:
            handle.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P1-29 anonymous acceptance launcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-ref", required=True)
    prepare.add_argument("--workspace", type=Path, required=True)
    for name in ("smoke", "serve"):
        command = subparsers.add_parser(name)
        command.add_argument("--workspace", type=Path, required=True)
        command.add_argument("--api-port", type=int, required=True)
        command.add_argument("--streamlit-port", type=int, required=True)
    bootstrap = subparsers.add_parser("_bootstrap", help=argparse.SUPPRESS)
    bootstrap.add_argument("--workspace", type=Path, required=True)
    bootstrap.add_argument(
        "--target",
        choices=("probe", "seed", "uvicorn", "streamlit"),
        required=True,
    )
    bootstrap.add_argument("--forbid-root", action="append", default=[])
    bootstrap.add_argument("--probe-module")
    bootstrap.add_argument("--source-sha")
    bootstrap.add_argument("--api-port", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "_bootstrap":
            return _run_bootstrap(args)
        if args.command == "prepare":
            result = prepare_workspace(args.source_ref, args.workspace, repo_root=repo_root)
        elif args.command == "smoke":
            result = run_servers(
                args.workspace,
                args.api_port,
                args.streamlit_port,
                serve=False,
            )
        elif args.command == "serve":
            run_servers(
                args.workspace,
                args.api_port,
                args.streamlit_port,
                serve=True,
            )
            return 0
        else:
            raise AcceptanceError("unsupported acceptance command")
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except KeyboardInterrupt:
        return 130
    except (AcceptanceError, AssertionError, StopIteration) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
