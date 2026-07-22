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
    os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-18-real")
)


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-18 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if (
        not resolved.is_relative_to(REPO_ROOT.resolve())
        or resolved != ALLOWED_DATA_ROOT.resolve(strict=False)
    ):
        raise RuntimeError("P2-18 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-18 browser data root cannot be a link or junction")
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


def _seed_training_data(paths) -> None:
    from db_manager import DBManager
    from question_bank.database.schema import connect, initialize_database
    from question_bank.services.source_question_link_service import (
        SourceQuestionLinkService,
    )

    grading_db = DBManager(paths.db_path)
    grading_db.initialize()
    initialize_database(paths.qb_db_path, seed_skills=False)

    rubric_path = paths.upload_config_dir / "p2-18-rubric.json"
    answer_path = paths.upload_config_dir / "p2-18-answer-key.json"
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 20,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "subjective",
                        "max_score": 10,
                    },
                    {
                        "question_id": "Q2",
                        "question_type": "subjective",
                        "max_score": 5,
                    },
                    {
                        "question_id": "Q3",
                        "question_type": "subjective",
                        "max_score": 5,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1"},
                    {"question_id": "Q2"},
                    {"question_id": "Q3"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with grading_db._connect() as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S012", "匿名学生甲", "七年级一班"),
                (15, "S015", "匿名学生乙", "七年级一班"),
            ],
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '匿名阶段测验', ?, ?, 'completed', 0)
            """,
            (str(rubric_path), str(answer_path)),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 20, 11, 0, '{}')
            """
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_id, knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (14001, ?, ?, ?, 'UNKNOWN', '[]', ?, ?, ?)
            """,
            [
                (
                    "Q1",
                    6,
                    "缺少辅助线",
                    "逻辑断裂",
                    "证明步骤缺少依据",
                    json.dumps(
                        [{"category": "审题错误", "summary": "条件识别不完整"}],
                        ensure_ascii=False,
                    ),
                ),
                ("Q2", 5, "", None, None, "[]"),
            ],
        )

    with connect(paths.qb_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (?, ?, ?, 'success')
            """,
            [(1, "匿名阶段测验", "anonymous-source.docx")]
            + [
                (question_id, f"匿名训练题组 {question_id}", f"practice-{question_id}.docx")
                for question_id in range(201, 209)
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, '解答题', ?, '5')
            """,
            [
                (101, 1, "1", "匿名当前考试原题"),
                (102, 1, "2", "匿名已掌握题"),
                (201, 201, "11", "利用边角关系证明两个三角形全等"),
                (202, 202, "12", "根据中点条件构造全等三角形"),
                (203, 203, "13", "在折叠图形中寻找对应边并完成证明"),
                (204, 204, "14", "结合平行线性质判定三角形全等"),
                (205, 205, "15", "运用角平分线条件求未知线段长度"),
                (206, 206, "16", "从旋转图形中识别全等关系"),
                (207, 207, "17", "添加辅助线后证明两条线段相等"),
                (208, 208, "18", "在复杂几何图中选择合适的全等判定"),
            ],
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (question_id, "knowledge_point", "三角形全等")
                for question_id in [101, 102, *range(201, 209)]
            ]
            + [(101, "method", "构造辅助线")],
        )

    links = SourceQuestionLinkService(paths.qb_db_path)
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=101,
        link_method="paper_question_number",
    )
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q2",
        bank_question_id=102,
        link_method="paper_question_number",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8018)
    args = parser.parse_args()

    paths = _prepare_paths(args.data_root)
    _seed_training_data(paths)

    from backend.api.app import create_app
    import uvicorn

    app = create_app(path_manager=paths)
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-18 browser gate")

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    @app.post("/__p2_18__/change-candidate", include_in_schema=False)
    def change_candidate():
        with sqlite3.connect(paths.qb_db_path) as conn:
            conn.execute(
                "UPDATE questions SET question_text = ? WHERE id = 201",
                ("匿名候选题内容已在另一窗口更新",),
            )
            conn.commit()
        return {"changed": True}

    app.mount(
        "/assets",
        StaticFiles(directory=frontend_dist / "assets"),
        name="p2-18-assets",
    )

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
