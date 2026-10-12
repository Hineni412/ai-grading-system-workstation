from __future__ import annotations
import subprocess
import pytest
from tools import check_tracked_user_data

from pathlib import Path


from tools import run_test_suite


def test_repository_check_rejects_private_files_outside_user_data(monkeypatch, capsys):
    blocked = [
        'user_data/databases/grading_system.db', 'question_bank/QUESTION_BANK.DB',
        'config/api_profiles.json.bak', 'config/.env.production', 'keys/client.pem',
        'question_bank/question_bank.db-wal', 'temporary/export.sqlite3.bak',
        'backups/private.zip', 'output/TEST-report.html', 'runtime/python/python.exe',
        'frontend/dist/index.html', 'scratch/private\nexam.json',
    ]
    paths = [
        *blocked, 'backend/repositories/db_manager.py', 'docs/security/SECURITY.md',
        'config/.env.example', 'frontend/src/api/sessions.ts',
    ]

    def git_listing(command, **kwargs):
        assert command == ['git', 'ls-files', '-z']
        return subprocess.CompletedProcess(command, 0, stdout='\0'.join(paths) + '\0')

    monkeypatch.setattr(check_tracked_user_data.subprocess, 'run', git_listing)
    assert check_tracked_user_data.main() == 1
    output = capsys.readouterr().out
    assert all(repr(path) in output for path in blocked)
    assert 'backend/repositories/db_manager.py' not in output


def test_repository_check_accepts_source_and_reports_git_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        check_tracked_user_data.subprocess, 'run',
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout='backend/scan_grading/scanner.py\0config/.env.template\0',
        ),
    )
    assert check_tracked_user_data.main() == 0
    assert 'OK:' in capsys.readouterr().out

    def git_failure(command, **kwargs):
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(check_tracked_user_data.subprocess, 'run', git_failure)
    with pytest.raises(subprocess.CalledProcessError):
        check_tracked_user_data.main()


def test_isolated_environment_removes_api_keys_and_uses_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("EXAMPLE_API_KEY", "secret")
    monkeypatch.setenv("AI_GRADING_DATA_DIR", "D:/real-data")
    monkeypatch.setenv("AI_GRADING_WORKTREE_DATA_DIR", "D:/real-worktree-data")
    monkeypatch.setenv("AI_GRADING_API_PROFILES_PATH", "D:/real-profile.json")
    monkeypatch.setenv("AI_GRADING_TAXONOMY_STATE_PATH", "D:/real-taxonomy.json")
    monkeypatch.setenv("AI_GRADING_OPS_STATE_DIR", "D:/real-ops")

    environment = run_test_suite._isolated_environment(tmp_path)

    assert "OPENAI_API_KEY" not in environment
    assert "EXAMPLE_API_KEY" not in environment
    assert environment["AI_GRADING_DATA_DIR"] == str(tmp_path / "user_data")
    assert environment["AI_GRADING_WORKTREE_DATA_DIR"] == str(tmp_path / "user_data")
    assert environment["AI_GRADING_API_PROFILES_PATH"] == str(
        tmp_path / "local" / "AIGradingSystem" / "config" / "api_profiles.json"
    )
    assert environment["AI_GRADING_TAXONOMY_STATE_PATH"] == str(
        tmp_path / "local" / "AIGradingSystem" / "config" / "taxonomy_state_v2.json"
    )
    assert environment["AI_GRADING_OPS_STATE_DIR"] == str(
        tmp_path / "local" / "AIGradingSystem" / "ops"
    )
    assert environment["LOCALAPPDATA"] == str(tmp_path / "local")
    assert all(
        environment[key] == str(tmp_path / "t") for key in ("TEMP", "TMP", "TMPDIR")
    )
    assert environment["PYTHONUTF8"] == "1"


def test_run_command_captures_output_without_shell(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve", encoding="utf-8")

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        base = Path(command[-1].removeprefix("--basetemp="))
        assert base.parent == tmp_path
        (base / "synthetic.db").write_bytes(b"synthetic")
        seen["base"] = base
        return subprocess.CompletedProcess(command, 0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(run_test_suite.subprocess, "run", fake_run)

    result = run_test_suite._run_command(
        "synthetic",
        ["python", "-m", "pytest"],
        cwd=tmp_path,
        environment={"SAFE": "1", "TEMP": str(tmp_path)},
    )

    assert result.ok
    assert seen["command"][:3] == ["python", "-m", "pytest"]
    assert not seen["base"].exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"
    assert seen["cwd"] == tmp_path
    assert seen["env"] == {"SAFE": "1", "TEMP": str(tmp_path)}
    assert seen["capture_output"] is True
