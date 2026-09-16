import sqlite3, json, hashlib
from pathlib import Path
con = sqlite3.connect(r"user_data/databases/grading_system.db")
con.row_factory = sqlite3.Row
jc = [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
print("jobs cols:", jc)
print("jobs_active:", [dict(r) for r in con.execute("select * from jobs where status in ('running','pending','queued')")])
print("grading_runs_s4:", [dict(r) for r in con.execute("select * from grading_runs where session_id=4")])
print("exam_papers_s4:", con.execute("select count(*) from exam_papers where session_id=4").fetchone()[0])
print("attendance_s4:", con.execute("select count(*) from session_attendance where session_id=4").fetchone()[0])
work = Path("user_data/templates/session_4")
analysis = (work/"scan_analysis_latest.json").read_bytes()
state = json.loads((work/"scan_decisions_state.json").read_text(encoding="utf-8"))
identity = hashlib.sha256(analysis).hexdigest()
print("identity_match:", state.get("analysis_identity") == identity)
print("decisions:", len(state.get("internal_decisions") or []))
