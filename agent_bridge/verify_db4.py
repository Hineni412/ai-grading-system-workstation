# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("== session_details session 4 per question ==")
for r in cur.execute("select question_id, count(*) c from session_details where session_id=4 group by question_id order by question_id"):
    print(" ", dict(r))

print("\n== details sample ==")
row = cur.execute("select * from session_details where session_id=4 limit 1").fetchone()
print({k: str(v)[:110] for k, v in dict(row).items()})

print("\n== needs_human_review counts ==")
n = cur.execute("select count(*) c from session_results where session_id=4 and needs_human_review=1").fetchone()['c']
print("papers flagged:", n)
n2 = cur.execute("select count(*) c from session_details where session_id=4 and needs_human_review=1").fetchone()['c'] if 'needs_human_review' in [r[1] for r in cur.execute("pragma table_info(session_details)")] else 'n/a'
print("details flagged:", n2)

print("\n== score stats ==")
for r in cur.execute("select min(student_score) mn, max(student_score) mx, avg(student_score) av from session_results where session_id=4"):
    print(" ", dict(r))
print("score distribution:")
for r in cur.execute("select round(student_score/10)*10 b, count(*) c from session_results where session_id=4 group by b order by b"):
    print(" ", dict(r))

print("\n== flagged papers ==")
for r in cur.execute("select student_id, student_score from session_results where session_id=4 and needs_human_review=1 order by student_id"):
    s = cur.execute("select name from students where id=?", (r['student_id'],)).fetchone()
    print(" ", r['student_id'], s['name'] if s else '?', r['student_score'])
