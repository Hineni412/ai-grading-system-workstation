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
    os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-12-real")
)


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-12 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if (
        not resolved.is_relative_to(REPO_ROOT.resolve())
        or resolved != ALLOWED_DATA_ROOT.resolve(strict=False)
    ):
        raise RuntimeError("P2-12 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-12 browser data root cannot be a link or junction")
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


def _seed_business_data(paths) -> tuple[int, int]:
    from db_manager import DBManager, StudentRecord
    from question_bank.services.training_task_service import TrainingTaskService

    db = DBManager(paths.db_path)
    db.initialize()
    rubric = paths.upload_config_dir / "anonymous-rubric.json"
    answer = paths.upload_config_dir / "anonymous-answer.json"
    rubric.write_text(
        json.dumps(
            {
                "total_score": 10,
                "questions": [
                    {"question_id": "Q1", "question_type": "subjective", "max_score": 10}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer.write_text(
        json.dumps({"questions": [{"question_id": "Q1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    session_id = db.create_grading_session(
        "七年级匿名期末",
        str(rubric),
        str(answer),
    )
    db.upsert_students([StudentRecord("A001", "匿名学生一", "测试班")])
    student_id = int(db.list_students()[0]["id"])
    page = paths.exams_dir / "anonymous-paper.jpg"
    page.write_bytes(b"anonymous browser fixture")
    with db._connect() as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, ?, 'matched', 'completed')
                """,
                (session_id, str(page), str(page), student_id),
            ).lastrowid
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 0, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary
            ) VALUES (?, 'Q1', 8, '计算步骤需补充', '["K1"]', 'calculation', '符号检查')
            """,
            (result_id,),
        )
        conn.commit()

    task = TrainingTaskService(paths.qb_db_path).create_task(
        {
            "scope_snapshot": {"mode": "class", "class_id": "测试班"},
            "exam_scope": {"mode": "current", "session_ids": [session_id]},
            "diagnosis_snapshot": {"students": []},
            "generation_config": {"variant_mode": "individual"},
            "warnings": [],
            "variants": [
                {
                    "variant_key": "anonymous-class",
                    "variant_type": "individual",
                    "student_ids": [],
                    "items": [],
                }
            ],
        },
        created_by="teacher",
        task_code="TRAIN-ANON-001",
    )
    return session_id, task.id


def _finish(store, job_type: str, payload: dict, *, status: str, result=None, error=None):
    job = store.create_job(job_type, payload)
    if not store.mark_running(job.id):
        raise RuntimeError("unable to start browser fixture job")
    store.finish(job.id, status, result=result, error=error)
    loaded = store.get_job(job.id)
    if loaded is None:
        raise RuntimeError("unable to load browser fixture job")
    return loaded


def _seed_jobs(paths, store, session_id: int, task_id: int) -> None:
    from backend.report_exports import score_revision
    from db_manager import DBManager

    revision = score_revision(DBManager(paths.db_path), session_id)
    for index in range(98):
        _finish(
            store,
            "report_export",
            {
                "session_id": session_id,
                "report_type": "score_excel",
                "score_revision": f"historical-revision-{index}",
            },
            status="failed",
            error="anonymous historical fixture",
            result={
                "session_id": session_id,
                "report_type": "score_excel",
                "score_revision": f"historical-revision-{index}",
                "filename": f"历史成绩表_{index + 1}.xlsx",
            },
        )
    expired_pdf = paths.reports_dir / "七年级匿名期末_旧批注原卷.pdf"
    _finish(
        store,
        "report_export",
        {
            "session_id": session_id,
            "report_type": "annotated_original_pdf",
            "score_revision": revision,
        },
        status="succeeded",
        result={
            "session_id": session_id,
            "report_type": "annotated_original_pdf",
            "score_revision": revision,
            "file_path": str(expired_pdf),
            "filename": expired_pdf.name,
        },
    )
    available_report = paths.reports_dir / "七年级匿名期末_成绩表.xlsx"
    available_report = paths.reports_dir / (
        "七年级匿名期末考试成绩汇总与班级学科分析教师复核留档"
        "（包含缺考说明与最终确认版本）_成绩表.xlsx"
    )
    available_report.write_bytes(b"anonymous xlsx fixture")
    _finish(
        store,
        "report_export",
        {
            "session_id": session_id,
            "report_type": "score_excel",
            "score_revision": revision,
        },
        status="succeeded",
        result={
            "session_id": session_id,
            "report_type": "score_excel",
            "score_revision": revision,
            "file_path": str(available_report),
            "filename": available_report.name,
        },
    )
    _finish(
        store,
        "training_export",
        {"task_id": task_id, "format": "docx"},
        status="failed",
        error="anonymous fixture failure",
        result={"task_id": task_id},
    )


def _report_handler(paths):
    def handler(context):
        report_type = str(context.payload.get("report_type") or "score_excel")
        suffix = ".pdf" if report_type == "annotated_original_pdf" else ".xlsx"
        filename = f"七年级匿名期末_{'批注原卷' if suffix == '.pdf' else '成绩表'}{suffix}"
        target = paths.reports_dir / filename
        target.write_bytes(b"%PDF anonymous" if suffix == ".pdf" else b"anonymous xlsx")
        context.report(1, "report_export", "complete")
        return {
            "session_id": int(context.payload["session_id"]),
            "report_type": report_type,
            "score_revision": context.payload.get("score_revision"),
            "file_path": str(target),
            "filename": filename,
        }

    return handler


def _training_handler(paths):
    def handler(context):
        target = paths.outputs_dir / "training" / "匿名训练材料.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PK anonymous training fixture")
        context.report(1, "training_export", "complete")
        return {
            "task_id": int(context.payload["task_id"]),
            "file_path": str(target),
            "filename": target.name,
        }

    return handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    paths = _prepare_paths(args.data_root)
    session_id, task_id = _seed_business_data(paths)

    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    import uvicorn

    store = JobStore(paths.db_path)
    manager = JobManager(store, max_workers=2)
    manager.register("report_export", _report_handler(paths))
    manager.register("training_export", _training_handler(paths))
    _seed_jobs(paths, store, session_id, task_id)

    app = create_app(path_manager=paths)
    app.dependency_overrides[get_job_manager] = lambda: manager
    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-12 browser gate")

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets",
        StaticFiles(directory=frontend_dist / "assets"),
        name="p2-12-assets",
    )

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith("api/"):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port)
    finally:
        manager.shutdown()


if __name__ == "__main__":
    main()
