from __future__ import annotations

import subprocess
from pathlib import Path


def _bundled_runtime_python() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    local_runtime = project_root / "runtime" / "python" / "python.exe"
    if local_runtime.exists():
        return local_runtime

    git_common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=project_root,
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if git_common.returncode != 0:
        return local_runtime
    common_dir = Path(git_common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = project_root / common_dir
    return common_dir.resolve().parent / "runtime" / "python" / "python.exe"


def test_bundled_runtime_can_import_tkinter() -> None:
    python_exe = _bundled_runtime_python()
    assert python_exe.exists()

    result = subprocess.run(
        [
            str(python_exe),
            "-c",
            "import tkinter; from tkinter import filedialog; print(tkinter.__file__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_local_file_dialog_uses_available_tkinter() -> None:
    from question_bank.services.local_file_dialog import get_local_file_dialog

    dialog = get_local_file_dialog()

    assert dialog.available is True
    assert dialog.tk is not None
    assert dialog.filedialog is not None
