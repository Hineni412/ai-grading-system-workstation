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
from backend.repositories.grading_database import open_grading_repositories
ALLOWED_DATA_ROOT = Path(os.path.abspath(REPO_ROOT / "frontend" / "test-results" / "p2-11-real"))


def _prepare_paths(data_root: Path):
    candidate = Path(os.path.abspath(data_root))
    if candidate != ALLOWED_DATA_ROOT:
        raise RuntimeError("P2-11 browser data root must be the dedicated test directory")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(REPO_ROOT.resolve()) or resolved != ALLOWED_DATA_ROOT.resolve(strict=False):
        raise RuntimeError("P2-11 browser data root resolves outside the repository")
    if candidate.exists():
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise RuntimeError("P2-11 browser data root cannot be a link or junction")
        shutil.rmtree(candidate)

    from path_manager import PathManager

    paths = PathManager.__new__(PathManager)
    paths._project_root = REPO_ROOT
    paths._cfg = {}
    paths._data_root = candidate / "data"
    paths._logs_root = candidate / "logs"
    paths._api_profiles_path = candidate / "machine-config" / "api_profiles.json"
    paths._taxonomy_state_path = candidate / "machine-config" / "taxonomy_state_v2.json"
    paths._ops_state_dir = candidate / "ops"
    paths.ensure_directories()
    os.environ["AI_GRADING_DATA_DIR"] = str(paths.data_root)
    os.environ["AI_GRADING_API_PROFILES_PATH"] = str(paths.api_profiles_path)
    os.environ["AI_GRADING_OPS_STATE_DIR"] = str(paths.ops_state_dir)

    import path_manager

    path_manager._instance = paths
    return paths


def _seed(paths) -> None:
    from PIL import Image, ImageDraw
    from backend.repositories.students import StudentRecord

    db = open_grading_repositories(paths.db_path)
    db.initialize()
    rubric = paths.upload_config_dir / "anonymous-rubric.json"
    answer = paths.upload_config_dir / "anonymous-answer.json"
    rubric.write_text(json.dumps({"total_score": 10, "questions": [
        {"question_id": "Q1", "question_type": "subjective", "max_score": 10},
    ]}), encoding="utf-8")
    answer.write_text(json.dumps({"questions": [{"question_id": "Q1"}]}), encoding="utf-8")
    session_id = db.sessions.create_grading_session("匿名浏览器批改考试", str(rubric), str(answer))
    if session_id != 1:
        raise RuntimeError("isolated P2-11 session must have id 1")
    db.students.upsert_students([
        StudentRecord("A001", "匿名学生一", "测试班"),
        StudentRecord("A002", "匿名学生二", "测试班"),
        StudentRecord("A003", "匿名学生三", "测试班"),
        StudentRecord("A004", "匿名学生四", "测试班"),
    ])
    fixture = ALLOWED_DATA_ROOT / "anonymous-class-scan.jpg"
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 860, 1160), outline="black", width=4)
    draw.text((90, 100), "ANONYMOUS CLASS SCAN", fill="black")
    image.save(fixture, format="JPEG", quality=90)
    # The real preflight endpoint requires a confirmed sample before dispatch.
    # The model substitute below tests controls, not sample detection quality.
    sample_dir = paths.templates_dir / f"session_{session_id}"
    sample_dir.mkdir(parents=True, exist_ok=True)
    front, back = sample_dir / "front.png", sample_dir / "back.png"
    image.save(front)
    image.save(back)
    db.templates.upsert_session_template(session_id, str(front), str(back))
    db.templates.mark_template_confirmed(session_id, True)


def _scan_handler(paths):
    def handler(context):
        session_id = int(context.payload["session_id"])
        scans = sorted(Path(str(context.payload["exams_dir"])).glob("*"))
        if not scans:
            raise RuntimeError("anonymous scan fixture is missing")
        students = open_grading_repositories(paths.db_path).students.list_students()
        target = paths.templates_dir / f"session_{session_id}" / "scan_analysis_latest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        context.report(0.4, "scan_analysis", "matching anonymous pages")
        target.write_text(json.dumps({
            "scan_batch_id": str(context.payload.get("scan_batch_id") or ""),
            "config_revision": context.payload["config_revision"],
            "template_id": context.payload["template_id"],
            "template_fingerprint": context.payload["template_fingerprint"],
            "template_first_page_role": context.payload["template_first_page_role"],
            "front_page_parity": context.payload["front_page_parity"],
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
        from backend.scan_grading.config_fingerprint import (
            session_grading_config_fingerprint,
        )
        from grading_run_store import GradingRunStore

        session_id = int(context.payload["session_id"])
        mode = str(context.payload.get("grading_mode") or "ai")
        db = open_grading_repositories(paths.db_path)
        config_fingerprint = session_grading_config_fingerprint(
            db=db,
            data_root=paths.data_root,
            session_id=session_id,
            grading_mode=mode,
        )
        store = GradingRunStore(paths.db_path)
        resume_run_id = context.payload.get("resume_run_id")
        supplement_run_id = context.payload.get("supplement_run_id")
        if resume_run_id:
            run = store.resume_exact(
                int(resume_run_id),
                session_id,
                config_fingerprint,
                mode,
            )
        elif supplement_run_id:
            run = store.reopen_for_supplement(
                int(supplement_run_id),
                session_id,
                config_fingerprint,
                mode,
            )
        else:
            run = store.begin(session_id, config_fingerprint, mode)
        students = db.students.list_students()
        if supplement_run_id:
            item_id = store.add_item(
                run.id,
                source_label="anonymous-supplement-004",
                student_id=int(students[3]["id"]),
                paper_fingerprint="e" * 64,
                config_fingerprint=config_fingerprint,
                status="pending",
            )
            store.set_item_status(item_id, "graded")
            store.finish(run.run_token, "completed")
            context.report(1.0, "grading_run", "anonymous supplement complete")
            return {"state": "completed", "run_id": run.id}
        if not resume_run_id:
            for index, status in enumerate(("graded", "failed", "pending")):
                store.add_item(
                    run.id, source_label=f"anonymous-{index + 1:03d}", student_id=int(students[index]["id"]),
                    paper_fingerprint=chr(98 + index) * 64,
                    config_fingerprint=config_fingerprint,
                    status=status,
                )
        context.report(0.42, "grading_run", "anonymous grading active")
        # Keep the first run open for browser pause/cancel controls. A resumed
        # run completes on its own; real processing time is not under test.
        for _ in range(600 if not resume_run_id else 40):
            current = store.get_run(run.id)
            if current is not None and current.state == "pause_requested":
                store.finish(run.run_token, "paused")
                return {"state": "paused", "run_id": run.id}
            if context.cancel_requested:
                store.finish(run.run_token, "paused")
                context.raise_if_cancelled()
            time.sleep(0.1)
        if not resume_run_id:
            raise RuntimeError("synthetic initial run expected a pause or cancel request")
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
    frontend_dist = Path(os.environ.get(
        "SCAN_BROWSER_FRONTEND_DIST", str(REPO_ROOT / "frontend" / "dist")
    )).resolve()
    if not (frontend_dist / "index.html").is_file():
        raise RuntimeError("build the frontend before running the P2-11 browser gate")
    from unittest.mock import patch
    from backend.api.frontend import mount_frontend
    with patch("backend.api.app.mount_frontend", lambda app, _dist: mount_frontend(app, frontend_dist)):
        app = create_app(path_manager=paths)
    app.dependency_overrides[get_job_manager] = lambda: manager

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
