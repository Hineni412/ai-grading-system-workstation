from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(REPO_ROOT))
ALLOWED_DATA_ROOT = (REPO_ROOT / "frontend" / "test-results" / "p2-11-real").resolve()


def _prepare_paths(data_root: Path):
    resolved = data_root.resolve()
    if resolved != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-11 browser data root must be the dedicated test directory")
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
    from PIL import Image, ImageDraw
    from db_manager import DBManager, StudentRecord

    db = DBManager(paths.db_path)
    db.initialize()
    rubric = paths.upload_config_dir / "anonymous-rubric.json"
    answer = paths.upload_config_dir / "anonymous-answer.json"
    rubric.write_text(json.dumps({"total_score": 10, "questions": []}), encoding="utf-8")
    answer.write_text(json.dumps({"questions": []}), encoding="utf-8")
    session_id = db.create_grading_session("匿名浏览器批改考试", str(rubric), str(answer))
    if session_id != 1:
        raise RuntimeError("isolated P2-11 session must have id 1")
    db.upsert_students([
        StudentRecord("A001", "匿名学生一", "测试班"),
        StudentRecord("A002", "匿名学生二", "测试班"),
        StudentRecord("A003", "匿名学生三", "测试班"),
    ])
    fixture = ALLOWED_DATA_ROOT / "anonymous-class-scan.jpg"
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 860, 1160), outline="black", width=4)
    draw.text((90, 100), "ANONYMOUS CLASS SCAN", fill="black")
    image.save(fixture, format="JPEG", quality=90)


def _scan_handler(paths):
    def handler(context):
        session_id = int(context.payload["session_id"])
        scans = sorted(Path(str(context.payload["exams_dir"])).glob("*"))
        if not scans:
            raise RuntimeError("anonymous scan fixture is missing")
        students = __import__("db_manager").DBManager(paths.db_path).list_students()
        target = paths.templates_dir / f"session_{session_id}" / "scan_analysis_latest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        context.report(0.4, "scan_analysis", "matching anonymous pages")
        target.write_text(json.dumps({
            "groups": [{
                "front_image": str(scans[0]), "back_image": str(scans[0]),
                "student_name": students[0]["name"], "student_id": students[0]["id"],
                "detected_name": students[0]["name"], "source_label": "anonymous-001",
                "match_method": "exact", "match_score": 1.0,
            }],
            "issues": [{
                "issue_id": "anonymous-issue-1", "issue_type": "unmatched",
                "message": "anonymous unmatched page", "front_image": str(scans[0]),
                "back_image": str(scans[0]), "detected_name": "待核对",
                "source_label": "anonymous-002",
            }],
            "absent_students": students[1:], "warnings": ["anonymous warning"], "total_pages": 3,
        }, ensure_ascii=False), encoding="utf-8")
        context.report(1.0, "scan_analysis", "complete")
        return {"outcome": "completed", "session_id": session_id}
    return handler


def _grading_handler(paths):
    def handler(context):
        from grading_run_store import GradingRunStore

        session_id = int(context.payload["session_id"])
        mode = str(context.payload.get("grading_mode") or "full_paper")
        store = GradingRunStore(paths.db_path)
        resume_run_id = context.payload.get("resume_run_id")
        run = store.resume(session_id, "a" * 64, mode) if resume_run_id else store.begin(session_id, "a" * 64, mode)
        if run is None:
            raise RuntimeError("anonymous grading run cannot resume")
        students = __import__("db_manager").DBManager(paths.db_path).list_students()
        if not resume_run_id:
            for index, status in enumerate(("graded", "failed", "pending")):
                store.add_item(
                    run.id, source_label=f"anonymous-{index + 1:03d}", student_id=int(students[index]["id"]),
                    paper_fingerprint=chr(98 + index) * 64, config_fingerprint="a" * 64, status=status,
                )
        context.report(0.42, "grading_run", "anonymous grading active")
        for _ in range(150):
            current = store.get_run(run.id)
            if current is not None and current.state == "pause_requested":
                store.finish(run.run_token, "paused")
                return {"state": "paused", "run_id": run.id}
            if context.cancel_requested:
                store.finish(run.run_token, "paused")
                context.raise_if_cancelled()
            time.sleep(0.1)
        store.finish(run.run_token, "completed")
        return {"state": "completed", "run_id": run.id}
    return handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    paths = _prepare_paths(args.data_root)
    _seed(paths)

    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    import uvicorn

    manager = JobManager(JobStore(paths.db_path), max_workers=2)
    manager.register("scan_analysis", _scan_handler(paths))
    manager.register("grading_run", _grading_handler(paths))
    app = create_app(path_manager=paths)
    app.dependency_overrides[get_job_manager] = lambda: manager
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
