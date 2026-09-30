from __future__ import annotations

import uuid
from pathlib import Path

from backend.config_workspace.atomic import remove_exact_files, write_json_atomic
from backend.repositories.sessions import SessionRepositoryGateway

EMPTY_RUBRIC = {"draft": True, "total_score": 0, "questions": []}
EMPTY_ANSWER_KEY = {"draft": True, "questions": []}
DRAFT_MARKER_KEY = "_config_draft_id"


def create_session_draft(
    sessions: SessionRepositoryGateway,
    upload_config_dir: Path,
    *,
    name: str,
    curriculum_volume_id: str | None = None,
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
            {
                **EMPTY_RUBRIC,
                "exam_title": clean_name,
                DRAFT_MARKER_KEY: token,
            },
        )
        created.append(rubric_path)
        write_json_atomic(
            answer_path,
            {**EMPTY_ANSWER_KEY, DRAFT_MARKER_KEY: token},
        )
        created.append(answer_path)
        return sessions.create_grading_session(
            clean_name,
            str(rubric_path),
            str(answer_path),
            curriculum_volume_id=curriculum_volume_id,
        )
    except Exception:
        remove_exact_files(created)
        raise
