from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(REPO_ROOT))

ALLOWED_DATA_ROOT = Path(
    os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-17-real")
)


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-17 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if (
        not resolved.is_relative_to(REPO_ROOT.resolve())
        or resolved != ALLOWED_DATA_ROOT.resolve(strict=False)
    ):
        raise RuntimeError("P2-17 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-17 browser data root cannot be a link or junction")
        shutil.rmtree(candidate)

    from path_manager import PathManager

    paths = PathManager.__new__(PathManager)
    paths._project_root = REPO_ROOT
    paths._cfg = {}
    paths._data_root = candidate / "data"
    paths._logs_root = candidate / "logs"
    paths._api_profiles_path = candidate / "machine-config" / "api_profiles.json"
    paths._ops_state_dir = candidate / "ops"
    paths.ensure_directories()
    os.environ["AI_GRADING_DATA_DIR"] = str(paths.data_root)
    os.environ["AI_GRADING_API_PROFILES_PATH"] = str(paths.api_profiles_path)
    os.environ["AI_GRADING_OPS_STATE_DIR"] = str(paths.ops_state_dir)

    import path_manager

    path_manager._instance = paths
    return paths


def _seed_question_bank(paths) -> None:
    from db_manager import DBManager
    from question_bank.database.schema import initialize_database

    DBManager(paths.db_path).initialize()
    initialize_database(paths.qb_db_path, seed_skills=False)

    with sqlite3.connect(paths.qb_db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (
                id, title, source_file, year, province, city, exam_type,
                grade, semester, import_status, content_fingerprint
            ) VALUES (
                1, '匿名九年级组卷验收卷', 'anonymous.docx', '2025', '广东省',
                '深圳市', '期末', '九年级', '下学期', 'success', 'p2-17-browser-fixture'
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, page_range, image_paths, difficulty, has_images
            ) VALUES (?, 1, ?, ?, ?, ?, '1', '[]', ?, 0)
            """,
            [
                (
                    1,
                    "1",
                    "选择题",
                    "（5分）匿名验收题 1：计算一次函数的交点。",
                    "A",
                    "3",
                ),
                (
                    2,
                    "2",
                    "解答题",
                    "（10分）匿名验收题 2：说明二次函数顶点与对称轴。",
                    "顶点公式与配方法均可。",
                    "6",
                ),
                (
                    3,
                    "3",
                    "填空题",
                    "（4分）匿名验收题 3：填写函数零点。",
                    "x=2",
                    "4",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source
            ) VALUES (?, 'knowledge_point', ?, 0.95, 'manual')
            """,
            [
                (1, "一次函数"),
                (2, "二次函数"),
                (3, "函数零点"),
            ],
        )
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8017)
    args = parser.parse_args()

    paths = _prepare_paths(args.data_root)
    _seed_question_bank(paths)

    from backend.api.app import create_app
    import uvicorn

    app = create_app(path_manager=paths)
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-17 browser gate")

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="p2-17-assets")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
