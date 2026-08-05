from pathlib import PureWindowsPath

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
