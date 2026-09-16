# -*- coding: utf-8 -*-
import sqlite3

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("== run2 items status ==")
for r in cur.execute("select status, count(*) c from grading_run_items where run_id=2 group by status"):
    print(" ", dict(r))

print("\n== session_results session 4 ==")
print("cols:", [r[1] for r in cur.execute("pragma table_info(session_results)")])
n = cur.execute("select count(*) c from session_results where session_id=4").fetchone()['c']
print("count:", n)
row = cur.execute("select * from session_results where session_id=4 limit 1").fetchone()
print("sample:", {k: str(v)[:90] for k, v in dict(row).items()})
for r in cur.execute("select status, count(*) c from session_results where session_id=4 group by status"):
    print(" status:", dict(r))

print("\n== session_details session 4 ==")
print("cols:", [r[1] for r in cur.execute("pragma table_info(session_details)")])
n = cur.execute("select count(*) c from session_details where session_id=4").fetchone()['c']
print("count:", n)
for r in cur.execute("select question_id, count(*) c from session_details where session_id=4 group by question_id order by question_id"):
    print(" ", dict(r))

print("\n== review flags ==")
dc = [r[1] for r in cur.execute("pragma table_info(session_details)")]
row = cur.execute("select * from session_details where session_id=4 limit 1").fetchone()
print("sample:", {k: str(v)[:100] for k, v in dict(row).items()})

print("\n== attendance session 4 ==")
print("cols:", [r[1] for r in cur.execute("pragma table_info(session_attendance)")])
for r in cur.execute("select * from session_attendance where session_id=4 limit 3"):
    print(" ", {k: str(v)[:80] for k, v in dict(r).items()})
