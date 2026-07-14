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

import pytest

from tools import p1_29_acceptance as acceptance


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
