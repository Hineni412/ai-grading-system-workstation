from __future__ import annotations

import subprocess
from pathlib import Path


def test_bundled_runtime_can_import_tkinter() -> None:
    python_exe = Path("runtime/python/python.exe")
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
