from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(REPO_ROOT))
ALLOWED_DATA_ROOT = Path(
    os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-16-real")
)


def _font(size: int):
    from PIL import ImageFont

    for family in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _seed_visual_assets(asset_path: Path, preview_path: Path) -> None:
    from PIL import Image, ImageDraw

    asset = Image.new("RGB", (960, 360), "#f8fafc")
    draw = ImageDraw.Draw(asset)
    draw.rounded_rectangle(
        (12, 12, 947, 347),
        radius=22,
        fill="#ffffff",
        outline="#2563eb",
        width=4,
    )
    draw.text(
        (48, 42),
        "ANONYMOUS FORMULA ASSET",
        fill="#1e3a5f",
        font=_font(30),
    )
    draw.text(
        (48, 108),
        "f(x) = sum[(x_k^2 + 2x_k y_k + y_k^2) / (1 + k^2)]",
        fill="#111827",
        font=_font(24),
    )
    draw.line((70, 290, 890, 290), fill="#64748b", width=3)
    draw.line((480, 190, 480, 325), fill="#64748b", width=3)
    points = []
    for x in range(-180, 181, 4):
        screen_x = 480 + x
        screen_y = 292 - int((x / 28) ** 2)
        points.append((screen_x, screen_y))
    draw.line(points, fill="#b04444", width=5)
    asset.save(asset_path, format="PNG", optimize=True)

    preview = Image.new("RGB", (1200, 1600), "#e5e7eb")
    draw = ImageDraw.Draw(preview)
    draw.rounded_rectangle(
        (70, 50, 1130, 1550),
        radius=18,
        fill="#ffffff",
        outline="#cbd5e1",
        width=4,
    )
    draw.text(
        (120, 105),
        "ANONYMOUS QUESTION PREVIEW",
        fill="#1e3a5f",
        font=_font(42),
    )
    draw.line((120, 178, 1080, 178), fill="#2563eb", width=5)
    draw.text((120, 230), "QUESTION 1", fill="#111827", font=_font(34))
    draw.multiline_text(
        (120, 305),
        (
            "LONG_FORMULA\n"
            "Let f(x) = sum from k=1 to 120 of\n"
            "(x_k^2 + 2x_k y_k + y_k^2) / (1 + k^2).\n"
            "Prove that the function has a unique minimum."
        ),
        fill="#20242a",
        font=_font(28),
        spacing=18,
    )
    draw.rounded_rectangle(
        (120, 600, 1080, 1070),
        radius=12,
        fill="#f8fafc",
        outline="#94a3b8",
        width=3,
    )
    draw.line((180, 950, 1010, 950), fill="#64748b", width=3)
    draw.line((595, 650, 595, 1015), fill="#64748b", width=3)
    points = []
    for x in range(-300, 301, 5):
        screen_x = 595 + x
        screen_y = 950 - int((x / 34) ** 2)
        points.append((screen_x, screen_y))
    draw.line(points, fill="#b04444", width=6)
    draw.text(
        (120, 1130),
        "Show the complete reasoning and verify the minimum.",
        fill="#374151",
        font=_font(28),
    )
    for y in range(1215, 1460, 55):
        draw.line((120, y, 1080, y), fill="#d1d5db", width=2)
    preview.save(preview_path, format="PNG", optimize=True)


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-16 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if (
        not resolved.is_relative_to(REPO_ROOT.resolve())
        or resolved != ALLOWED_DATA_ROOT.resolve(strict=False)
    ):
        raise RuntimeError("P2-16 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-16 browser data root cannot be a link or junction")
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
    asset_root = paths.data_root / "question_bank" / "extracted_images"
    preview_root = paths.data_root / "question_bank" / "previews"
    rich_root = paths.data_root / "question_bank" / "rich_content"
    asset_root.mkdir(parents=True, exist_ok=True)
    preview_root.mkdir(parents=True, exist_ok=True)
    rich_root.mkdir(parents=True, exist_ok=True)
    _seed_visual_assets(
        asset_root / "formula.png",
        preview_root / "question-1.png",
    )

    long_formula = (
        "LONG_FORMULA：设 f(x)=∑_{k=1}^{120} "
        "(x_k²+2x_ky_k+y_k²)/(1+k²)，证明在给定约束下函数取得唯一最小值。"
    )
    with sqlite3.connect(paths.qb_db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (
                id, title, source_file, year, province, city, exam_type,
                grade, semester, import_status, content_fingerprint
            ) VALUES (
                1, '匿名九年级期末试卷', 'anonymous.docx', '2025', '广东省',
                '深圳市', '期末', '九年级', '下学期', 'success', 'browser-fixture'
            )
            """
        )
        rows = []
        for question_id in range(1, 2006):
            text = long_formula if question_id == 1 else (
                f"匿名验收题目 {question_id}：计算并说明完整推理过程。"
            )
            image_paths = json.dumps(
                ["question_bank/extracted_images/formula.png"]
                if question_id == 1
                else []
            )
            rows.append(
                (
                    question_id,
                    1,
                    str(question_id),
                    "解答题" if question_id % 2 else "填空题",
                    text,
                    f"匿名答案 {question_id}",
                    "1",
                    image_paths,
                    str((question_id % 10) + 1),
                    int(question_id == 1),
                )
            )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, page_range, image_paths, difficulty, has_images
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source
            ) VALUES (?, ?, ?, ?, 'manual')
            """,
            [
                (1, "knowledge_point", "函数最值", 0.95),
                (1, "ability", "运算能力", 0.90),
                (1, "method", "配方法", 0.90),
                (1, "exam_scope", "期末", 1.0),
            ],
        )
        conn.execute(
            """
            INSERT INTO question_previews (
                question_id, preview_type, image_path, page_number, status
            ) VALUES (
                1, 'question', 'question_bank/previews/question-1.png', 1, 'ready'
            )
            """
        )
        conn.commit()

    rich_root.joinpath("question_1.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 1,
                "question_blocks": [
                    {
                        "text": long_formula,
                        "image_relationships": {
                            "rId1": "question_bank/extracted_images/formula.png"
                        },
                    }
                ],
                "answer_blocks": [
                    {
                        "text": "匿名答案：先配方，再比较边界条件。",
                        "image_relationships": {},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (ALLOWED_DATA_ROOT / "anonymous-import.docx").write_bytes(
        b"anonymous browser fixture"
    )


def _fake_tagging(context):
    question_ids = [int(item) for item in context.payload.get("question_ids", [])]
    context.report(0.35, "tagging", "正在生成匿名标签")
    time.sleep(0.15)
    context.raise_if_cancelled()
    failed = question_ids[-1:]
    successful = question_ids[:-1]
    return {
        "outcome": "partial" if failed else "complete",
        "requested_count": len(question_ids),
        "skipped_complete_count": 0,
        "tagged_count": len(successful),
        "failed_count": len(failed),
        "successful_question_ids": successful,
        "failed_question_ids": failed,
        "failures": [
            {
                "question_id": item,
                "category": "model_timeout",
                "message": "匿名模型超时，可安全重试",
            }
            for item in failed
        ],
        "retryable": bool(failed),
    }


def _fake_import(context):
    context.report(0.5, "importing", "正在导入匿名试卷")
    time.sleep(0.1)
    context.raise_if_cancelled()
    return {
        "request_id": context.payload.get("request_id"),
        "outcome": "complete",
        "imported_papers": 1,
        "question_count": 2,
        "failed_count": 0,
        "successful_question_ids": [2006, 2007],
        "failed_question_ids": [],
        "retryable": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    paths = _prepare_paths(args.data_root)
    _seed_question_bank(paths)

    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    import uvicorn

    manager = JobManager(JobStore(paths.db_path), max_workers=1)
    manager.register("tagging_sync", _fake_tagging)
    manager.register("question_import", _fake_import)
    app = create_app(path_manager=paths)
    app.dependency_overrides[get_job_manager] = lambda: manager
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-16 browser gate")

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="p2-16-assets")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
