from __future__ import annotations

import uuid
from pathlib import Path

from backend.config_workspace.atomic import remove_exact_files, write_json_atomic
from db_manager import DBManager


EMPTY_RUBRIC = {"draft": True, "total_score": 0, "questions": []}
EMPTY_ANSWER_KEY = {"draft": True, "questions": []}


def create_session_draft(
    db: DBManager,
    upload_config_dir: Path,
    *,
    name: str,
) -> int:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("session name must be nonblank")

    token = uuid.uuid4().hex
    config_dir = Path(upload_config_dir)
    rubric_path = config_dir / f"session-draft-{token}-rubric.json"
    answer_path = config_dir / f"session-draft-{token}-answer-key.json"
    created: list[Path] = []
    try:
        write_json_atomic(
            rubric_path,
            {**EMPTY_RUBRIC, "exam_title": clean_name},
        )
        created.append(rubric_path)
        write_json_atomic(answer_path, EMPTY_ANSWER_KEY)
        created.append(answer_path)
        return db.create_grading_session(
            clean_name,
            str(rubric_path),
            str(answer_path),
        )
    except Exception:
        remove_exact_files(created)
        raise
