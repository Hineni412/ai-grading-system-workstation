from __future__ import annotations

import json
import os
from pathlib import Path

from api_profiles import active_api_profile, get_api_profile_store
from answer_region_geometry import answer_regions_with_template_source_sizes
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from grading_run_identity import grading_config_fingerprint
from path_manager import resolve_stored_file_path


def active_grading_model() -> str:
    profile = active_api_profile(get_api_profile_store().load())
    return str(
        profile.get("grading_model")
        or os.getenv("LLM_GRADING_MODEL")
        or "gpt-4o"
    )


def session_grading_config_fingerprint(
    *,
    db: GradingRepositoryAccess,
    data_root: Path,
    session_id: int,
    grading_mode: str,
    grading_model: str | None = None,
) -> str:
    db = as_grading_repositories(db)
    session = db.get_grading_session(int(session_id))
    if session is None:
        raise ValueError("grading session was not found")
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        data_root=Path(data_root),
    )
    answer_key_path = resolve_stored_file_path(
        session.get("answer_key_path"),
        data_root=Path(data_root),
    )
    rubric = _read_object(rubric_path, "rubric")
    answer_key = _read_object(answer_key_path, "answer key")
    regions = answer_regions_with_template_source_sizes(
        db,
        int(session_id),
        data_root=Path(data_root),
    )
    return grading_config_fingerprint(
        rubric=rubric,
        answer_key=answer_key,
        answer_regions=regions,
        grading_mode=(
            "hybrid_batch" if grading_mode == "hybrid_batch" else "full_paper"
        ),
        grading_model=grading_model or active_grading_model(),
    )


def _read_object(path: Path, label: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain an object")
    return payload
