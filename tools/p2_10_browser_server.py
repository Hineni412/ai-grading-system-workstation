from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(REPO_ROOT))
from backend.repositories.grading_database import open_grading_repositories
ALLOWED_DATA_ROOT = (
    REPO_ROOT / "frontend" / "test-results" / "p2-10-real"
).resolve()


def _prepare_isolated_paths(data_root: Path):
    resolved = data_root.resolve()
    if resolved != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-10 browser data root must be the dedicated test directory")
    if resolved.exists():
        shutil.rmtree(resolved)

    from path_manager import PathManager

    paths = PathManager.__new__(PathManager)
    paths._project_root = REPO_ROOT
    paths._cfg = {}
    paths._data_root = resolved / "data"
    paths._logs_root = resolved / "logs"
    paths._api_profiles_path = resolved / "machine-config" / "api_profiles.json"
    paths._ops_state_dir = resolved / "ops"
    paths.ensure_directories()
    os.environ["AI_GRADING_DATA_DIR"] = str(paths.data_root)
    os.environ["AI_GRADING_API_PROFILES_PATH"] = str(paths.api_profiles_path)
    os.environ["AI_GRADING_OPS_STATE_DIR"] = str(paths.ops_state_dir)

    import path_manager

    path_manager._instance = paths
    return paths


def _seed(paths) -> None:
    import fitz

    

    db = open_grading_repositories(paths.db_path)
    db.initialize()
    rubric_path = paths.upload_config_dir / "anonymous-rubric.json"
    answer_path = paths.upload_config_dir / "anonymous-answer-key.json"
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 10,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "subjective",
                        "max_score": 10,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps({"questions": [{"question_id": "Q1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    session_id = db.sessions.create_grading_session(
        "匿名浏览器验收考试", str(rubric_path), str(answer_path)
    )
    if session_id != 1:
        raise RuntimeError("isolated P2-10 session must have id 1")

    fixture_path = ALLOWED_DATA_ROOT / "anonymous-two-page.pdf"
    document = fitz.open()
    try:
        front = document.new_page(width=400, height=600)
        front.insert_text((36, 48), "ANONYMOUS FRONT")
        back = document.new_page(width=400, height=600)
        back.insert_text((36, 48), "ANONYMOUS BACK")
        document.save(fixture_path)
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    paths = _prepare_isolated_paths(args.data_root)
    _seed(paths)

    from backend.api.app import create_app
    import uvicorn

    uvicorn.run(create_app(path_manager=paths), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
