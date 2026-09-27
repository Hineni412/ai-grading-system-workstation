from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import run_test_suite
from tools.test_suite_manifest import (
    DATABASE_BASELINE_TEST_PATHS,
    PROCESS_ISOLATED_TEST_PATHS,
    QUICK_FRONTEND_TEST_PATHS,
    QUICK_TEST_PATHS,
    RELEASE_AUDIT_TEST_PATHS,
    REVIEW_FRONTEND_TEST_PATHS,
    REVIEW_TEST_PATHS,
    SERIAL_TEST_PATHS,
)


def test_manifests_reference_existing_unique_tests() -> None:
    for paths in (
        QUICK_TEST_PATHS,
        QUICK_FRONTEND_TEST_PATHS,
        RELEASE_AUDIT_TEST_PATHS,
        SERIAL_TEST_PATHS,
        PROCESS_ISOLATED_TEST_PATHS,
        REVIEW_TEST_PATHS,
        REVIEW_FRONTEND_TEST_PATHS,
        DATABASE_BASELINE_TEST_PATHS,
    ):
        assert len(paths) == len(set(paths))
        assert all((run_test_suite.PROJECT_ROOT / path).is_file() for path in paths)
    assert set(PROCESS_ISOLATED_TEST_PATHS) < set(SERIAL_TEST_PATHS)
    assert QUICK_FRONTEND_TEST_PATHS
    assert all(path.is_relative_to("frontend") for path in QUICK_FRONTEND_TEST_PATHS)
    assert not DATABASE_BASELINE_TEST_PATHS.intersection(RELEASE_AUDIT_TEST_PATHS)
    assert all(
        not path.is_relative_to("tests/api_e2e")
        for path in DATABASE_BASELINE_TEST_PATHS
    )
    assert Path("tests/test_schema_baseline.py") not in DATABASE_BASELINE_TEST_PATHS


@pytest.mark.parametrize(
    "full, frontend_exit", [(False, 0), (False, 1), (True, 0), (True, 1)]
)
def test_suite_dispatch_selects_frontend_specs_and_preserves_full_verification(
    monkeypatch,
    full: bool,
    frontend_exit: int,
) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(label, command, **kwargs):
        commands.append(tuple(command))
        return run_test_suite.CommandResult(
            label=label,
            command=tuple(command),
            return_code=frontend_exit,
            elapsed_seconds=0.1,
            output="synthetic frontend result",
        )

    monkeypatch.setattr(run_test_suite, "_run_command", fake_run)
    monkeypatch.setattr(run_test_suite, "_npm_command", lambda *args: ["npm", *args])
    monkeypatch.setattr(run_test_suite, "_run_backend_pair", lambda **kwargs: [])
    monkeypatch.setattr(
        run_test_suite,
        "_run_review_browser",
        lambda **kwargs: fake_run("browser", ("review-browser",)),
    )

    results = run_test_suite._run_current_suite(
        mode="full" if full else "quick",
        workers=1,
        durations=0,
        skip_frontend=False,
        environment={},
    )

    if full:
        assert commands == [
            ("npm", "run", "verify"),
            *([] if frontend_exit else [("review-browser",)]),
        ]
    else:
        assert commands[0] == (
            "npm",
            "run",
            "test",
            "--",
            *(
                path.relative_to("frontend").as_posix()
                for path in QUICK_FRONTEND_TEST_PATHS
            ),
        )
        assert commands[1:] == (
            [] if frontend_exit else [("npm", "run", "test:editor")]
        )
    assert len(results) == len(commands)
    assert all(result.return_code == frontend_exit for result in results)


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


def test_serial_lane_gives_process_global_tests_a_fresh_pytest_process(
    monkeypatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(label, command, **kwargs):
        calls.append((label, tuple(command)))
        return run_test_suite.CommandResult(
            label=label,
            command=tuple(command),
            return_code=0,
            elapsed_seconds=0.1,
            output="1 passed",
        )

    monkeypatch.setattr(run_test_suite, "_run_command", fake_run)

    results = run_test_suite._run_serial_paths(
        label="后端隔离车道",
        paths=(
            Path("tests/test_job_manager.py"),
            Path("tests/test_api_ops_routes.py"),
        ),
        durations=10,
        environment={},
    )

    assert len(results) == 2
    assert calls[0][0] == "后端隔离车道"
    assert calls[0][1][-1] == "tests/test_job_manager.py"
    assert calls[1][0] == "后端隔离车道（独立进程：test_api_ops_routes.py）"
    assert calls[1][1][-1] == "tests/test_api_ops_routes.py"


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


def test_failure_summary_keeps_errors_after_a_long_captured_log() -> None:
    lines = run_test_suite._summary_lines(
        "\n".join(
            (
                "================================== FAILURES ===================================",
                "backend.secure_fs.SecureFilesystemError: denied",
                *(f"INFO migration step {index}" for index in range(200)),
                "FAILED tests/test_flow.py::test_flow",
                "1 failed, 10 passed in 5.00s",
                " FAIL src/__tests__/page.spec.ts > saves the edited score",
                "Error: Test timed out in 5000ms.",
            )
        ),
        failure=True,
    )

    assert lines == [
        "backend.secure_fs.SecureFilesystemError: denied",
        "FAILED tests/test_flow.py::test_flow",
        "1 failed, 10 passed in 5.00s",
        " FAIL src/__tests__/page.spec.ts > saves the edited score",
        "Error: Test timed out in 5000ms.",
    ]
