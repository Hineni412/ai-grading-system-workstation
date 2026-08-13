from __future__ import annotations

import os
from pathlib import Path


def project_data_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root / "data"
    configured = os.getenv("AI_GRADING_DATA_DIR")
    if configured:
        return Path(configured).resolve()
    # Prefer the unified PathManager when available
    try:
        from path_manager import get_path_manager
        pm = get_path_manager()
        # question_bank code expects this to return a dir
        # such that  <return>/question_bank/question_bank.db  exists.
        return pm.data_root
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "data"


def question_bank_db_path(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root / "user_data" / "databases" / "question_bank.db"
    configured = os.getenv("AI_GRADING_DATA_DIR")
    if configured:
        return Path(configured).resolve() / "databases" / "question_bank.db"
    # Prefer the unified PathManager when available
    try:
        from path_manager import get_path_manager
        return get_path_manager().qb_db_path
    except Exception:
        pass
    return project_data_root(project_root) / "question_bank" / "question_bank.db"
