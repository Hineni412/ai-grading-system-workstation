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
ALLOWED_DATA_ROOT = Path(
    os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-13-real")
)


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-13 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if (
        not resolved.is_relative_to(REPO_ROOT.resolve())
        or resolved != ALLOWED_DATA_ROOT.resolve(strict=False)
    ):
        raise RuntimeError("P2-13 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-13 browser data root cannot be a link or junction")
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


def _seed(paths) -> None:
    import pandas as pd

    from db_manager import DBManager, StudentRecord

    db = DBManager(paths.db_path)
    db.initialize()
    db.upsert_students(
        [
            StudentRecord(
                f"A{index:03d}",
                (
                    "匿名超长姓名用于验证名单表格不会撑出窗口边界050"
                    if index == 50
                    else f"匿名学生{index:03d}"
                ),
                "测试一班" if index <= 60 else "测试二班",
            )
            for index in range(1, 126)
        ]
    )
    first_student = db.list_students()[0]
    rubric = paths.upload_config_dir / "anonymous-rubric.json"
    answer = paths.upload_config_dir / "anonymous-answer.json"
    rubric.write_text(
        json.dumps({"total_score": 10, "questions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    answer.write_text(
        json.dumps({"questions": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    session_id = db.create_grading_session(
        "匿名名单验收考试",
        str(rubric),
        str(answer),
    )
    with db._connect() as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, student_id, match_status,
                    processing_status, error_message
                ) VALUES (
                    ?, 'anonymous-front.png', 'anonymous-back.png', ?, 'matched',
                    'graded', 'anonymous previous grading result'
                )
                """,
                (session_id, int(first_student["id"])),
            ).lastrowid
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 0, '{}')
                """,
                (session_id, int(first_student["id"]), paper_id),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, knowledge_ids
            ) VALUES (?, 'Q1', 8, '["UNKNOWN"]')
            """,
            (result_id,),
        )
        conn.execute(
            """
            INSERT INTO annotated_results (session_id, result_id)
            VALUES (?, ?)
            """,
            (session_id, result_id),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (
                session_id, student_id, attendance_status, matched_paper_id
            ) VALUES (?, ?, 'present', ?)
            """,
            (session_id, int(first_student["id"]), paper_id),
        )
        conn.commit()

    fixture = ALLOWED_DATA_ROOT / "anonymous-students.csv"
    fixture.write_text(
        "\n".join(
            [
                "student_code,name,class_name",
                "A001,匿名学生001更新,测试一班",
                "A126,将被后行覆盖,测试三班",
                "A127,,测试三班",
                "A126,匿名学生126,测试三班",
            ]
        )
        + "\n",
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        [
            {
                "student_code": "A002",
                "name": "匿名学生002已核对",
                "class_name": "测试一班",
            },
            {
                "student_code": "A128",
                "name": "匿名学生128",
                "class_name": "测试三班",
            },
        ]
    ).to_excel(ALLOWED_DATA_ROOT / "anonymous-students.xlsx", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    paths = _prepare_paths(args.data_root)
    _seed(paths)

    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db
    from db_manager import DBManager
    import uvicorn

    backup_failure = {"next": False}

    class BrowserDBManager(DBManager):
        def create_backup(self, reason: str, *, once_per_day: bool = False):
            if backup_failure["next"]:
                backup_failure["next"] = False
                raise OSError("anonymous injected backup failure")
            return super().create_backup(reason, once_per_day=once_per_day)

    app = create_app(path_manager=paths)
    app.dependency_overrides[get_grading_db] = lambda: BrowserDBManager(paths.db_path)
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-13 browser gate")

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets",
        StaticFiles(directory=frontend_dist / "assets"),
        name="p2-13-assets",
    )

    @app.post("/test-support/fail-next-backup", include_in_schema=False)
    def fail_next_backup():
        backup_failure["next"] = True
        return {"armed": True}

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
