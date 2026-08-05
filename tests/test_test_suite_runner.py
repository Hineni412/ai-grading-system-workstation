from __future__ import annotations

import subprocess
from pathlib import Path

from tools import run_test_suite
from tools.test_suite_manifest import (
    PROCESS_ISOLATED_TEST_PATHS,
    QUICK_TEST_PATHS,
    RELEASE_AUDIT_TEST_PATHS,
    SERIAL_TEST_PATHS,
    categories_for_path,
)


def test_manifests_reference_existing_unique_tests() -> None:
    for paths in (
        QUICK_TEST_PATHS,
        RELEASE_AUDIT_TEST_PATHS,
        SERIAL_TEST_PATHS,
        PROCESS_ISOLATED_TEST_PATHS,
    ):
        assert len(paths) == len(set(paths))
        assert all((run_test_suite.PROJECT_ROOT / path).is_file() for path in paths)
    assert set(PROCESS_ISOLATED_TEST_PATHS) < set(SERIAL_TEST_PATHS)


def test_quick_manifest_reuses_product_tests_and_marks_serial_overlap() -> None:
    assert Path("tests/api_e2e/test_five_flow.py") in QUICK_TEST_PATHS
    assert categories_for_path("tests/api_e2e/test_five_flow.py") == frozenset(
        {"acceptance_quick", "acceptance_serial"}
    )
    assert categories_for_path("tests/test_handoff_status.py") == frozenset(
        {"release_audit"}
    )


def test_default_worker_count_is_bounded() -> None:
    assert run_test_suite.default_worker_count(1) == 1
    assert run_test_suite.default_worker_count(4) == 2
    assert run_test_suite.default_worker_count(8) == 4
    assert run_test_suite.default_worker_count(64) == 6


def test_pytest_command_uses_file_distribution_and_disjoint_ignores() -> None:
    command = run_test_suite._pytest_command(
        workers=4,
        durations=12,
        paths=(Path("tests/test_api_app.py"),),
        ignored_paths=(Path("tests/test_ops_lock.py"),),
    )

    assert command[0:4] == [
        run_test_suite.sys.executable,
        "-m",
        "pytest",
        "-q",
    ]
    assert ["-n", "4", "--dist", "loadfile"] == command[6:10]
    assert "--durations=12" in command
    assert "--ignore=tests/test_ops_lock.py" in command
    assert command[-1] == "tests/test_api_app.py"


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
    assert environment["AI_GRADING_WORKTREE_DATA_DIR"] == str(
        tmp_path / "user_data"
    )
    assert environment["AI_GRADING_API_PROFILES_PATH"] == str(
        tmp_path / "local" / "AIGradingSystem" / "config" / "api_profiles.json"
    )
    assert environment["AI_GRADING_TAXONOMY_STATE_PATH"] == str(
        tmp_path
        / "local"
        / "AIGradingSystem"
        / "config"
        / "taxonomy_state_v2.json"
    )
    assert environment["AI_GRADING_OPS_STATE_DIR"] == str(
        tmp_path / "local" / "AIGradingSystem" / "ops"
    )
    assert environment["LOCALAPPDATA"] == str(tmp_path / "local")
    assert environment["PYTHONUTF8"] == "1"


def test_backend_pair_stops_before_serial_lane_when_parallel_fails(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_run(label, command, **kwargs):
        calls.append(label)
        return run_test_suite.CommandResult(
            label=label,
            command=tuple(command),
            return_code=1,
            elapsed_seconds=0.1,
            output="1 failed",
        )

    monkeypatch.setattr(run_test_suite, "_run_command", fake_run)

    results = run_test_suite._run_backend_pair(
        label_prefix="完整",
        parallel_paths=(Path("tests/test_api_app.py"),),
        serial_paths=(Path("tests/test_ops_lock.py"),),
        ignored_paths=(),
        workers=2,
        durations=10,
        environment={},
    )

    assert len(results) == 1
    assert calls == ["完整后端并行车道（2进程）"]


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

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(run_test_suite.subprocess, "run", fake_run)

    result = run_test_suite._run_command(
        "synthetic",
        ["python", "-m", "pytest"],
        cwd=tmp_path,
        environment={"SAFE": "1"},
    )

    assert result.ok
    assert seen["command"] == ["python", "-m", "pytest"]
    assert seen["cwd"] == tmp_path
    assert seen["env"] == {"SAFE": "1"}
    assert seen["capture_output"] is True


def test_summary_includes_vitest_and_node_test_counts() -> None:
    lines = run_test_suite._summary_lines(
        "\n".join(
            (
                " Test Files  73 passed (73)",
                "      Tests  751 passed (751)",
                "ℹ tests 16",
                "ℹ pass 16",
                "ℹ fail 0",
            )
        ),
        failure=False,
    )

    assert lines == [
        " Test Files  73 passed (73)",
        "      Tests  751 passed (751)",
        "ℹ tests 16",
        "ℹ pass 16",
        "ℹ fail 0",
    ]


def test_failure_summary_keeps_errors_after_a_long_captured_log() -> None:
    lines = run_test_suite._summary_lines(
        "\n".join(
            (
                "================================== FAILURES ===================================",
                "backend.secure_fs.SecureFilesystemError: denied",
                *(f"INFO migration step {index}" for index in range(200)),
                "FAILED tests/test_flow.py::test_flow",
                "1 failed, 10 passed in 5.00s",
            )
        ),
        failure=True,
    )

    assert lines == [
        "backend.secure_fs.SecureFilesystemError: denied",
        "FAILED tests/test_flow.py::test_flow",
        "1 failed, 10 passed in 5.00s",
    ]
