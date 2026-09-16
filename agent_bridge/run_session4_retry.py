"""Drive the session-4 *failed-only retry* with the bridge client.

Same wiring as ``run_session4.py`` but passes ``failed_only=True`` so the
production pipeline re-grades only papers whose persisted result is
incomplete/failed (the ``score_contract_error:step_assessments`` fallbacks),
through ``replace_result_details_atomic``.

Run with the project runtime:

    runtime\\python\\python.exe -X utf8 agent_bridge\\run_session4_retry.py
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


def main() -> int:
    from agent_bridge.bridge_client import BridgeLLMClient
    from backend.jobs.grading_run import run_grading_job
    from backend.repositories.compat import open_grading_repositories
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
    events_path = BRIDGE_DIR / "events_retry.jsonl"
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
        failed_only=True,
        enhance_images=True,
        max_workers=4,
        should_cancel=lambda: (BRIDGE_DIR / "CANCEL").exists(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
