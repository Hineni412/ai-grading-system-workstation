from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import path_manager as path_manager_module


_TEMPORARY_ROOT: tempfile.TemporaryDirectory[str] | None = None
_ORIGINAL_PATH_MANAGER: Any | None = None
_ORIGINAL_DATA_DIR: str | None = None


def pytest_configure() -> None:
    global _ORIGINAL_DATA_DIR
    global _ORIGINAL_PATH_MANAGER
    global _TEMPORARY_ROOT

    _ORIGINAL_PATH_MANAGER = path_manager_module._instance
    _ORIGINAL_DATA_DIR = os.environ.get("AI_GRADING_DATA_DIR")

    _TEMPORARY_ROOT = tempfile.TemporaryDirectory(
        prefix="ai_grading_pytest_",
        ignore_cleanup_errors=True,
    )
    temporary_root = Path(_TEMPORARY_ROOT.name)
    isolated_paths = path_manager_module.PathManager()
    isolated_paths._data_root = temporary_root / "user_data"
    isolated_paths._logs_root = temporary_root / "logs"
    isolated_paths._api_profiles_path = temporary_root / "config" / "api_profiles.json"
    isolated_paths._ops_state_dir = temporary_root / "ops"
    isolated_paths.ensure_directories()

    path_manager_module._instance = isolated_paths
    os.environ["AI_GRADING_DATA_DIR"] = str(isolated_paths.data_root)


def pytest_unconfigure() -> None:
    global _TEMPORARY_ROOT

    path_manager_module._instance = _ORIGINAL_PATH_MANAGER
    if _ORIGINAL_DATA_DIR is None:
        os.environ.pop("AI_GRADING_DATA_DIR", None)
    else:
        os.environ["AI_GRADING_DATA_DIR"] = _ORIGINAL_DATA_DIR
    if _TEMPORARY_ROOT is not None:
        _TEMPORARY_ROOT.cleanup()
        _TEMPORARY_ROOT = None
