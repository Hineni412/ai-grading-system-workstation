from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

import pytest

from tools import p1_29_acceptance as acceptance


SENSITIVE_API_ENV_NAMES = (
    "LLM_API_KEY",
    "LLM_CONFIG_API_KEY",
    "LLM_OBJECTIVE_API_KEY",
    "OPENAI_API_KEY",
    "QUESTION_BANK_TAGGING_API_KEY",
    "QUESTION_BANK_TAGGING_REVIEW_API_KEY",
)


def _tar_member(name: str, member_type: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = member_type
    member.size = 0
    return member


def test_bootstrap_loads_staged_only_module_without_repository_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "staged"
    staged_tools = workspace / "tools"
    staged_tools.mkdir(parents=True)
    shutil.copy2(Path(acceptance.__file__), staged_tools / "p1_29_acceptance.py")
    config = workspace / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text(
        "DATA_DIR: synthetic_data\nLOGS_DIR: acceptance_logs\n",
        encoding="utf-8",
    )
    acceptance.write_anonymous_runtime_state(workspace)
    (workspace / "staged_only_marker.py").write_text(
        "MARKER = 'staged-only'\n",
        encoding="utf-8",
    )
    launcher_root = Path(acceptance.__file__).resolve().parents[1]
    runtime_repository = Path(sys.executable).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path / "must-not-be-used")

    completed = subprocess.run(
        [
            sys.executable,
            str(staged_tools / "p1_29_acceptance.py"),
            "_bootstrap",
            "--workspace",
            str(workspace),
            "--target",
            "probe",
            "--probe-module",
            "staged_only_marker",
            "--forbid-root",
            str(launcher_root),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip())
    assert Path(payload["origin"]).resolve() == (
        workspace / "staged_only_marker.py"
    ).resolve()
    assert Path(payload["sys_path"][0]).resolve() == workspace.resolve()
    resolved_paths = {Path(value).resolve() for value in payload["sys_path"]}
    assert runtime_repository not in resolved_paths
    assert launcher_root not in resolved_paths


def test_dotted_origin_probe_does_not_import_parent_package(tmp_path: Path) -> None:
    workspace = tmp_path / "staged"
    staged_tools = workspace / "tools"
    staged_tools.mkdir(parents=True)
    shutil.copy2(Path(acceptance.__file__), staged_tools / "p1_29_acceptance.py")
    config = workspace / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text(
        "DATA_DIR: synthetic_data\nLOGS_DIR: acceptance_logs\n",
        encoding="utf-8",
    )
    acceptance.write_anonymous_runtime_state(workspace)
    package = workspace / "sentinel_pkg"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('imported')\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(staged_tools / "p1_29_acceptance.py"),
            "_bootstrap",
            "--workspace",
            str(workspace),
            "--target",
            "probe",
            "--probe-module",
            "sentinel_pkg.missing",
            "--forbid-root",
            str(Path(acceptance.__file__).resolve().parents[1]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not (package / "imported.txt").exists()


def test_bootstrap_validates_config_before_module_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / "staged"
    staged_tools = workspace / "tools"
    staged_tools.mkdir(parents=True)
    shutil.copy2(Path(acceptance.__file__), staged_tools / "p1_29_acceptance.py")
    config = workspace / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text("DATA_DIR: unsafe\nLOGS_DIR: logs\n", encoding="utf-8")
    acceptance.write_anonymous_runtime_state(workspace)
    package = workspace / "config_sentinel"
    package.mkdir()
    package.joinpath("__init__.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('imported')\n",
        encoding="utf-8",
    )
    package.joinpath("child.py").write_text("VALUE = 1\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(staged_tools / "p1_29_acceptance.py"),
            "_bootstrap",
            "--workspace",
            str(workspace),
            "--target",
            "probe",
            "--probe-module",
            "config_sentinel.child",
            "--forbid-root",
            str(Path(acceptance.__file__).resolve().parents[1]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "config" in completed.stderr
    assert not (package / "imported.txt").exists()


def test_bootstrap_clears_parent_api_secrets_and_forces_private_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "staged"
    staged_tools = workspace / "tools"
    staged_tools.mkdir(parents=True)
    shutil.copy2(Path(acceptance.__file__), staged_tools / "p1_29_acceptance.py")
    config = workspace / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text(
        "DATA_DIR: synthetic_data\nLOGS_DIR: acceptance_logs\n",
        encoding="utf-8",
    )
    private_config = workspace / "acceptance_config"
    private_config.mkdir()
    profile_path = private_config / "api_profiles.json"
    profile_path.write_text(
        json.dumps(
            [
                {
                    "name": "P1-29 Anonymous",
                    "api_key": "",
                    "config_api_key": "",
                    "objective_api_key": "",
                    "tagging_api_key": "",
                    "tagging_review_api_key": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    profile_path.with_name("api_profiles.json.migration-v1").write_text(
        "migration-v1-complete\n",
        encoding="utf-8",
    )
    ops_state = workspace / "acceptance_ops"
    ops_state.mkdir()
    module = workspace / "environment_capture.py"
    module.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        f"NAMES = {SENSITIVE_API_ENV_NAMES!r}\n"
        "payload = {name: os.getenv(name) for name in NAMES}\n"
        "payload['AI_GRADING_API_PROFILES_PATH'] = os.getenv('AI_GRADING_API_PROFILES_PATH')\n"
        "payload['AI_GRADING_OPS_STATE_DIR'] = os.getenv('AI_GRADING_OPS_STATE_DIR')\n"
        "Path(__file__).with_name('captured_environment.json').write_text(json.dumps(payload))\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    for name in SENSITIVE_API_ENV_NAMES:
        environment[name] = "parent-sentinel-value"
    environment["AI_GRADING_API_PROFILES_PATH"] = str(tmp_path / "outside.json")
    environment["AI_GRADING_OPS_STATE_DIR"] = str(tmp_path / "outside-ops")

    completed = subprocess.run(
        [
            sys.executable,
            str(staged_tools / "p1_29_acceptance.py"),
            "_bootstrap",
            "--workspace",
            str(workspace),
            "--target",
            "probe",
            "--probe-module",
            "environment_capture",
            "--forbid-root",
            str(Path(acceptance.__file__).resolve().parents[1]),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    captured = json.loads(
        (workspace / "captured_environment.json").read_text(encoding="utf-8")
    )
    leaked_names = [name for name in SENSITIVE_API_ENV_NAMES if captured.get(name)]
    assert leaked_names == []
    assert Path(captured["AI_GRADING_API_PROFILES_PATH"]).resolve() == profile_path
    assert Path(captured["AI_GRADING_OPS_STATE_DIR"]).resolve() == ops_state


def test_anonymous_runtime_state_has_only_empty_api_keys(tmp_path: Path) -> None:
    workspace = tmp_path / "staged"
    workspace.mkdir()

    acceptance.write_anonymous_runtime_state(workspace)

    profile_path = workspace / "acceptance_config" / "api_profiles.json"
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["name"] == "P1-29 Anonymous"
    key_names = [name for name in profile if "key" in name.casefold()]
    assert key_names
    assert all(profile[name] == "" for name in key_names)
    assert profile_path.with_name("api_profiles.json.migration-v1").is_file()
    assert (workspace / "acceptance_ops").is_dir()


def test_workspace_must_be_strictly_below_system_temp_root() -> None:
    with pytest.raises(acceptance.AcceptanceError, match="system temporary"):
        acceptance.validate_workspace(Path.cwd() / "acceptance-outside-temp")

    with pytest.raises(acceptance.AcceptanceError, match="system temporary"):
        acceptance.validate_workspace(Path(tempfile.gettempdir()))


@pytest.mark.parametrize(
    "parts",
    [
        ("user_data",),
        ("safe", "USER_DATA", "nested"),
    ],
)
def test_workspace_rejects_user_data_path_segment(
    tmp_path: Path,
    parts: tuple[str, ...],
) -> None:
    with pytest.raises(acceptance.AcceptanceError, match="user_data"):
        acceptance.validate_workspace(tmp_path.joinpath(*parts))


def test_workspace_rejects_existing_non_empty_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "occupied.txt").write_text("occupied", encoding="utf-8")

    with pytest.raises(acceptance.AcceptanceError, match="empty"):
        acceptance.validate_workspace(workspace)


@pytest.mark.parametrize("source_ref", ["abc123", "g" * 40, "0" * 39])
def test_source_ref_must_be_resolvable_full_commit_sha(
    monkeypatch: pytest.MonkeyPatch,
    source_ref: str,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad ref")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)

    with pytest.raises(acceptance.AcceptanceError, match="40-character|resolve"):
        acceptance.resolve_source_sha(source_ref, repo_root=Path.cwd())

    if len(source_ref) != 40 or any(char not in "0123456789abcdef" for char in source_ref):
        assert calls == []
    else:
        assert calls and calls[0][0:3] == ["git", "rev-parse", "--verify"]


@pytest.mark.parametrize(
    "member",
    [
        _tar_member("/absolute.txt"),
        _tar_member("C:/absolute.txt"),
        _tar_member("safe/../escape.txt"),
        _tar_member("link", tarfile.SYMTYPE),
        _tar_member("hardlink", tarfile.LNKTYPE),
    ],
    ids=["posix-absolute", "windows-absolute", "parent", "symlink", "hardlink"],
)
def test_archive_rejects_unsafe_member(member: tarfile.TarInfo) -> None:
    with pytest.raises(acceptance.AcceptanceError, match="archive"):
        acceptance.validate_archive_members([member])


@pytest.mark.parametrize(
    "name",
    [
        "user_data/databases/grading_system.db",
        "prefix/USER_DATA/file.txt",
    ],
)
def test_archive_rejects_any_user_data_member(name: str) -> None:
    with pytest.raises(acceptance.AcceptanceError, match="user_data"):
        acceptance.validate_archive_members([_tar_member(name)])


def test_git_archive_command_excludes_tracked_user_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[str] = []

    def fake_run(command, **kwargs):
        captured.extend(command)
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
            archive.addfile(_tar_member("safe.txt"), io.BytesIO(b""))
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=b"")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    acceptance._archive_commit("a" * 40, repo_root=Path.cwd(), workspace=workspace)

    assert captured == [
        "git",
        "archive",
        "--format=tar",
        "a" * 40,
        "--",
        ".",
        ":(exclude)user_data",
    ]
    assert (workspace / "safe.txt").is_file()


@pytest.mark.parametrize(
    "config_text",
    [
        "LOGS_DIR: logs\n",
        "DATA_DIR: user_data\n",
        "DATA_DIR: one\nDATA_DIR: two\nLOGS_DIR: logs\n",
        "DATA_DIR: user_data\nLOGS_DIR: one\nLOGS_DIR: two\n",
        'DATA_DIR: user_data\n"DATA_DIR": override\nLOGS_DIR: logs\n',
    ],
)
def test_staged_config_requires_exactly_one_data_and_logs_key(
    tmp_path: Path,
    config_text: str,
) -> None:
    config = tmp_path / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text(config_text, encoding="utf-8")

    with pytest.raises(acceptance.AcceptanceError, match="exactly one"):
        acceptance.rewrite_staged_config(tmp_path)


def test_staged_config_rewrites_only_required_unique_keys(tmp_path: Path) -> None:
    config = tmp_path / "config" / "app_config.yaml"
    config.parent.mkdir()
    config.write_text(
        "# keep\nDATA_DIR: user_data\nLOGS_DIR: logs\nVERSION: test\n",
        encoding="utf-8",
    )

    acceptance.rewrite_staged_config(tmp_path)

    assert config.read_text(encoding="utf-8") == (
        "# keep\nDATA_DIR: synthetic_data\n"
        "LOGS_DIR: acceptance_logs\nVERSION: test\n"
    )


@pytest.mark.parametrize(
    ("api_port", "streamlit_port"),
    [(1023, 8501), (65536, 8501), (8501, 8501)],
)
def test_ports_must_be_unprivileged_in_range_and_distinct(
    api_port: int,
    streamlit_port: int,
) -> None:
    with pytest.raises(acceptance.AcceptanceError, match="port"):
        acceptance.validate_ports(api_port, streamlit_port)


def test_archive_is_fully_validated_before_any_file_is_extracted(
    tmp_path: Path,
) -> None:
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        safe = _tar_member("safe.txt")
        safe.size = 4
        archive.addfile(safe, io.BytesIO(b"safe"))
        archive.addfile(_tar_member("../escape.txt"), io.BytesIO(b""))
    archive_bytes.seek(0)

    with pytest.raises(acceptance.AcceptanceError, match="archive"):
        acceptance.extract_validated_archive(archive_bytes, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_metadata_schema_is_allowlisted_and_paths_are_logical(tmp_path: Path) -> None:
    workspace = tmp_path / "prepared"
    (workspace / "synthetic_data" / "databases").mkdir(parents=True)
    (workspace / "synthetic_data" / "reports").mkdir(parents=True)
    (workspace / "synthetic_data" / "databases" / "grading_system.db").touch()
    (workspace / "synthetic_data" / "databases" / "question_bank.db").touch()
    (workspace / "synthetic_data" / "reports" / "report.xlsx").touch()
    metadata = {
        "package": "P1-29",
        "source_sha": "a" * 40,
        "session_id": 1,
        "counts": {"students": 2, "sessions": 1, "results": 2, "reports": 1},
        "files": {
            "grading_database": "synthetic_data/databases/grading_system.db",
            "question_bank_database": "synthetic_data/databases/question_bank.db",
            "report": "synthetic_data/reports/report.xlsx",
        },
    }
    (workspace / acceptance.METADATA_FILENAME).write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    assert acceptance.load_prepared_metadata(workspace) == metadata

    metadata["absolute_path"] = str(workspace)
    (workspace / acceptance.METADATA_FILENAME).write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    with pytest.raises(acceptance.AcceptanceError, match="metadata"):
        acceptance.load_prepared_metadata(workspace)


def test_internal_seed_executes_full_synthetic_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "staged"
    workspace.mkdir()
    repo_root = Path(acceptance.__file__).resolve().parents[1]
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    acceptance._archive_commit(
        source_sha,
        repo_root=repo_root,
        workspace=workspace,
    )
    shutil.copy2(
        Path(acceptance.__file__),
        workspace / "tools" / "p1_29_acceptance.py",
    )
    acceptance.rewrite_staged_config(workspace)
    acceptance.write_anonymous_runtime_state(workspace)

    acceptance._run_internal_seed(workspace, source_sha)
    metadata = acceptance.load_prepared_metadata(workspace)

    assert metadata["counts"] == {
        "students": 2,
        "sessions": 1,
        "results": 2,
        "reports": 1,
    }
    assert metadata["source_sha"] == source_sha
    assert all((workspace / filename).is_file() for filename in metadata["files"].values())


def _log_root_cases(workspace: Path) -> tuple[dict[str, tuple[Path, str]], str]:
    roots = {
        "workspace": (workspace.resolve(), "<workspace>"),
        "launcher": (
            Path(acceptance.__file__).resolve().parents[1],
            "<launcher-repo>",
        ),
        "runtime": (acceptance._runtime_repository_root(), "<runtime-repo>"),
    }
    lines: list[str] = []
    for label, (root, _placeholder) in roots.items():
        native = str(root)
        lines.extend(
            (
                f"{label}-native={native}",
                f"{label}-forward={native.replace(chr(92), '/')}",
                f"{label}-backward={native.replace('/', chr(92))}",
                f"{label}-case={native.upper()}",
            )
        )
    lines.append("health=http://127.0.0.1:8501/_stcore/health")
    return roots, "\n".join(lines) + "\n"


def _leaked_root_labels(
    text: str,
    roots: dict[str, tuple[Path, str]],
) -> list[str]:
    folded = text.casefold()
    leaked: list[str] = []
    for label, (root, _placeholder) in roots.items():
        native = str(root)
        variants = {
            native,
            native.replace("\\", "/"),
            native.replace("/", "\\"),
        }
        if any(variant.casefold() in folded for variant in variants):
            leaked.append(label)
    return leaked


def test_sanitized_log_writer_replaces_known_roots_and_preserves_urls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    roots, raw_text = _log_root_cases(workspace)
    log_path = workspace / "acceptance_logs" / "service.log"

    acceptance._write_sanitized_log(log_path, raw_text, workspace)

    sanitized = log_path.read_text(encoding="utf-8")
    assert _leaked_root_labels(sanitized, roots) == []
    assert all(placeholder in sanitized for _root, placeholder in roots.values())
    assert "http://127.0.0.1:8501/_stcore/health" in sanitized


def test_internal_seed_sanitizes_captured_output_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "staged"
    workspace.mkdir()
    roots, raw_text = _log_root_cases(workspace)
    completed = subprocess.CompletedProcess(
        args=["seed"],
        returncode=0,
        stdout=raw_text,
        stderr="",
    )
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: completed)

    acceptance._run_internal_seed(workspace, "a" * 40)

    sanitized = (workspace / "acceptance_logs" / "seed.log").read_text(
        encoding="utf-8"
    )
    assert _leaked_root_labels(sanitized, roots) == []
    assert all(placeholder in sanitized for _root, placeholder in roots.values())


def test_second_service_start_failure_cleans_process_handles_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    roots, raw_text = _log_root_cases(workspace)
    metadata = {"source_sha": "a" * 40}
    opened_handles: list[BinaryIO] = []
    terminated: list[object] = []

    class FakeProcess:
        pid = 12345

    first_process = FakeProcess()
    calls = 0

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal calls
        calls += 1
        handle = kwargs["stdout"]
        assert hasattr(handle, "write")
        opened_handles.append(handle)  # type: ignore[arg-type]
        handle.write(raw_text.encode("utf-8"))
        handle.flush()
        if calls == 2:
            raise OSError("simulated second service startup failure")
        return first_process

    monkeypatch.setattr(
        acceptance,
        "validate_workspace",
        lambda *args, **kwargs: workspace,
    )
    monkeypatch.setattr(acceptance, "validate_ports", lambda *args: None)
    monkeypatch.setattr(acceptance, "_validate_staged_config", lambda *args: None)
    monkeypatch.setattr(acceptance, "load_prepared_metadata", lambda *args: metadata)
    monkeypatch.setattr(acceptance.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        acceptance,
        "_terminate_process_tree",
        lambda process: terminated.append(process),
    )

    with pytest.raises(OSError, match="second service startup failure"):
        acceptance.run_servers(workspace, 18901, 18902, serve=False)

    assert terminated == [first_process]
    assert len(opened_handles) == 2
    assert all(handle.closed for handle in opened_handles)
    for log_name in ("api.log", "streamlit.log"):
        sanitized = (workspace / "acceptance_logs" / log_name).read_text(
            encoding="utf-8"
        )
        assert _leaked_root_labels(sanitized, roots) == []
        assert all(placeholder in sanitized for _root, placeholder in roots.values())
