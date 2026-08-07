from pathlib import Path, PureWindowsPath

from tools import start_service


def test_runtime_python_candidates_support_main_worktree_root() -> None:
    project_root = PureWindowsPath("D:/ai-grading-system")

    candidates = start_service._runtime_python_candidates(project_root)

    assert candidates == (
        project_root / "runtime" / "python" / "python.exe",
    )


def test_runtime_python_candidates_include_linked_worktree_fallback() -> None:
    project_root = PureWindowsPath(
        "D:/ai-grading-system/.worktrees/ui-optimization"
    )

    candidates = start_service._runtime_python_candidates(project_root)

    assert candidates == (
        project_root / "runtime" / "python" / "python.exe",
        PureWindowsPath("D:/ai-grading-system/runtime/python/python.exe"),
    )


def test_service_data_dir_preserves_explicit_runtime_data_root(tmp_path: Path) -> None:
    selected = tmp_path / "teacher-data"

    resolved = start_service._service_data_dir(
        {"AI_GRADING_DATA_DIR": str(selected)},
        tmp_path / "project",
    )

    assert resolved == selected.resolve()


def test_service_data_dir_prefers_worktree_override(tmp_path: Path) -> None:
    selected = tmp_path / "candidate-data"

    resolved = start_service._service_data_dir(
        {
            "AI_GRADING_WORKTREE_DATA_DIR": str(selected),
            "AI_GRADING_DATA_DIR": str(tmp_path / "other-data"),
        },
        tmp_path / "project",
    )

    assert resolved == selected.resolve()


def test_service_api_profiles_path_preserves_explicit_file(tmp_path: Path) -> None:
    selected = tmp_path / "machine-config" / "api_profiles.json"

    resolved = start_service._service_api_profiles_path(
        {"AI_GRADING_API_PROFILES_PATH": str(selected)}
    )

    assert resolved == selected.resolve()
