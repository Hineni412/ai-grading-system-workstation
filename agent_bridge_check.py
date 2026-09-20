import sqlite3, json
con = sqlite3.connect(r'user_data/databases/grading_system.db')
con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute("PRAGMA table_info(grading_sessions)")]
print("session cols:", cols)
row = dict(con.execute("select * from grading_sessions where id=4").fetchone())
print(json.dumps({k: row[k] for k in row if "batch" in k or "status" in k or "parity" in k or "scan" in k}, ensure_ascii=False, default=str))
from pathlib import Path
b = Path("user_data/exams/session_4/scan_batches")
print("batch dirs:", [p.name for p in b.iterdir() if p.is_dir()])
