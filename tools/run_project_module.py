"""Run a project module from the checkout that owns this script.

The bundled Python runtime can be shared by linked Git worktrees. Its
``python312._pth`` points at the main checkout, so ``python -m`` would otherwise
load application code from ``main`` even when the working directory is a
feature worktree.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolved_path(value: str) -> Path | None:
    if not value:
        return None
    try:
        return Path(value).resolve()
    except OSError:
        return None


def _prefer_current_project() -> None:
    project_root = PROJECT_ROOT.resolve()
    try:
        shared_runtime_root = Path(sys.executable).resolve().parents[2]
    except IndexError:
        shared_runtime_root = project_root

    retained: list[str] = []
    for entry in sys.path:
        resolved = _resolved_path(entry)
        if resolved == project_root:
            continue
        if shared_runtime_root != project_root and resolved == shared_runtime_root:
            continue
        retained.append(entry)
    sys.path[:] = [str(project_root), *retained]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_project_module.py <module> [args...]")

    module_name = sys.argv[1].strip()
    if not module_name or any(not part.isidentifier() for part in module_name.split(".")):
        raise SystemExit(f"invalid module name: {module_name!r}")

    _prefer_current_project()
    sys.argv = [module_name, *sys.argv[2:]]
    runpy.run_module(module_name, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
