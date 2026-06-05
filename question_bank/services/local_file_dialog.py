from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocalFileDialog:
    available: bool
    tk: Any = None
    filedialog: Any = None
    message: str = ""


def get_local_file_dialog() -> LocalFileDialog:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ModuleNotFoundError as exc:
        return LocalFileDialog(
            available=False,
            message=f"当前 Python 运行环境缺少 tkinter，无法打开本地文件选择窗口：{exc}",
        )

    return LocalFileDialog(available=True, tk=tk, filedialog=filedialog)
