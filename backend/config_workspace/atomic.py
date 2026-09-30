from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from backend.config_workspace.secure_fs import SecureRootFilesystem


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    SecureRootFilesystem(destination.parent).write_json_atomic(destination, payload)


def remove_exact_files(paths: Iterable[Path]) -> None:
    by_root: dict[Path, list[Path]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        by_root.setdefault(path.parent, []).append(path)
    for root, owned_paths in by_root.items():
        SecureRootFilesystem(root).unlink_many(owned_paths)
