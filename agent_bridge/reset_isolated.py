# -*- coding: utf-8 -*-
"""Reset the sandbox DB to a clean pre-grading state for session 4.

Deletes all session-4 run artifacts (results, details, papers, attendance,
locks, run ledger rows) in FK-safe order and resets the session status so a
fresh run can start. Sandbox copy only — never touches real user_data.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

SANDBOX = Path(os.environ.get("AI_GRADING_SANDBOX_DIR", r"D:\week3_regrade_sandbox"))
DB = str(SANDBOX / "data" / "databases" / "grading_system.db")
WORK_DIR = SANDBOX / "data" / "templates" / "session_4"


def _fix_scan_decision_identity() -> None:
    """Align scan_decisions_state.json analysis_identity with the copied
    scan_analysis_latest.json so the pipeline loads the manual decisions."""
    analysis_path = WORK_DIR / "scan_analysis_latest.json"
    state_path = WORK_DIR / "scan_decisions_state.json"
    if not analysis_path.exists() or not state_path.exists():
        return
    digest = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("analysis_identity") != digest:
        state["analysis_identity"] = digest
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print("scan_decisions_state identity aligned")


def main() -> int:
    if "sandbox" not in DB:
        raise SystemExit("refusing to touch non-sandbox db")
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys=ON")
    rids = [r[0] for r in conn.execute(
        "select id from session_results where session_id=4")]
    runs = [r[0] for r in conn.execute(
        "select id from grading_runs where session_id=4")]

    def ph(ids):
        return "(" + ",".join("?" * len(ids)) + ")" if ids else "(-1)"

    conn.execute(f"delete from grading_run_items where run_id in {ph(runs)}", runs)
    conn.execute("delete from grading_runs where session_id=?", (4,))
    conn.execute(
        f"delete from annotated_results where session_id=? or result_id in {ph(rids)}",
        [4] + rids,
    )
    conn.execute(f"delete from session_details where result_id in {ph(rids)}", rids)
    conn.execute("delete from teacher_score_locks where session_id=?", (4,))
    conn.execute("delete from session_attendance where session_id=?", (4,))
    conn.execute("delete from session_results where session_id=?", (4,))
    conn.execute("delete from exam_papers where session_id=?", (4,))
    conn.execute(
        "update grading_sessions set status='completed' where id=4 and status='running'"
    )
    conn.commit()
    _fix_scan_decision_identity()
    print("fk violations:", len(conn.execute("PRAGMA foreign_key_check").fetchall()))
    print("session4 status:", conn.execute(
        "select status from grading_sessions where id=4").fetchone()[0])
    print("session4 rows:",
          conn.execute("select count(*) from session_results where session_id=4").fetchone()[0],
          conn.execute("select count(*) from exam_papers where session_id=4").fetchone()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
