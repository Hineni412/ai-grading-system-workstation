"""Drive the real session-4 hybrid grading job with the bridge client.

Reproduces the exact ``run_grading_job`` parameter assembly used by
``backend/jobs/default_handlers.py`` so the only substituted piece is the
model-response boundary (``llm_client_factory`` -> ``BridgeLLMClient``).

Run with the project runtime:

    runtime\\python\\python.exe -X utf8 agent_bridge\\run_session4.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_ID = 4
BRIDGE_DIR = Path(__file__).resolve().parent / "session_4"


def _current_scan_batch_id(exams_root: Path) -> str:
    batches = sorted(
        p.name
        for p in (exams_root / f"session_{SESSION_ID}" / "scan_batches").iterdir()
        if p.is_dir() and (p / "files").is_dir()
    )
    if len(batches) != 1:
        raise RuntimeError(f"scan batch directory is not unique: {batches}")
    return batches[0]


def _write_papers_index(
    work_dir: Path,
    exams_dir: Path,
    db: object,
) -> Path:
    """Reproduce the pipeline's matched paper groups for local crop helpers."""
    from grading_service import _attach_enhanced_paths, apply_scan_manual_decisions
    from ai_batch_grading_service import build_paper_entries
    from scanner import ScanAnalysis

    analysis = ScanAnalysis.from_dict(
        json.loads(
            (work_dir / "scan_analysis_latest.json").read_text(encoding="utf-8")
        )
    )
    _attach_enhanced_paths(analysis, exams_dir / "_enhanced")
    state = json.loads(
        (work_dir / "scan_decisions_state.json").read_text(encoding="utf-8")
    )
    groups = apply_scan_manual_decisions(
        analysis, state["internal_decisions"], db.students.list_students()
    )
    entries = build_paper_entries(groups)
    index = {
        entry.paper_key: {
            "student_id": entry.student_id,
            "student_name": entry.student_name,
            "front": str(
                entry.group.enhanced_front_image or entry.group.front_image
            ),
            "back": str(
                entry.group.enhanced_back_image or entry.group.back_image or ""
            ),
        }
        for entry in entries
    }
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    out = BRIDGE_DIR / "papers_index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    from agent_bridge.bridge_client import BridgeLLMClient
    from backend.jobs.grading_run import run_grading_job
    from backend.repositories.grading_database import open_grading_repositories
    from path_manager import get_path_manager

    pm = get_path_manager()
    data_root = pm.data_root
    db_path = pm.databases_dir / "grading_system.db"
    batch_id = _current_scan_batch_id(pm.exams_dir)
    work_dir = pm.templates_dir / f"session_{SESSION_ID}"
    exams_dir = (
        pm.exams_dir
        / f"session_{SESSION_ID}"
        / "scan_batches"
        / batch_id
        / "files"
    )
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    events_path = BRIDGE_DIR / "events.jsonl"
    events_path.touch(exist_ok=True)

    def report(progress: float, stage: str, detail: str) -> None:
        line = json.dumps(
            {
                "t": time.strftime("%H:%M:%S"),
                "progress": progress,
                "stage": stage,
                "detail": detail,
            },
            ensure_ascii=False,
        )
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)

    db = open_grading_repositories(db_path)
    index_path = _write_papers_index(work_dir, exams_dir, db)
    papers = json.loads(index_path.read_text(encoding="utf-8"))
    report(0.0, "bridge_ready", f"matched_papers={len(papers)} batch={batch_id}")

    client = BridgeLLMClient(
        BRIDGE_DIR,
        grading_model="agent-bridge",
        policy_profile={
            "request_speed_mode": "custom",
            "max_concurrent_requests": 8,
            "requests_per_minute": 10000,
        },
    )

    result = run_grading_job(
        db=db,
        session_id=SESSION_ID,
        exams_dir=exams_dir,
        session_work_dir=work_dir,
        data_root=data_root,
        question_bank_db_path=pm.databases_dir / "question_bank.db",
        llm_client_factory=lambda: client,
        report=report,
        grading_mode="hybrid_batch",
        scan_batch_id=batch_id,
        enhance_images=True,
        max_workers=4,
        should_cancel=lambda: (BRIDGE_DIR / "CANCEL").exists(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
