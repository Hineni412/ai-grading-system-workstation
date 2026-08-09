from __future__ import annotations

from pathlib import Path


def test_repository_ignores_the_entire_runtime_user_data_tree() -> None:
    project_root = Path(__file__).resolve().parents[1]
    rules = {
        line.strip()
        for line in (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "user_data/" in rules
