# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

rids = [r['id'] for r in cur.execute("select id from session_results where session_id=4")]
print("session4 results:", len(rids))
ph = ",".join("?" * len(rids))

print("\n== details per question ==")
for r in cur.execute(f"select question_id, count(*) c, sum(score_awarded) s from session_details where result_id in ({ph}) group by question_id order by question_id", rids):
    print(" ", dict(r))

print("\n== papers status ==")
for r in cur.execute("select processing_status, count(*) c from exam_papers where session_id=4 group by processing_status"):
    print(" ", dict(r))
for r in cur.execute("select match_status, count(*) c from exam_papers where session_id=4 group by match_status"):
    print(" match:", dict(r))

print("\n== attendance ==")
for r in cur.execute("select attendance_status, count(*) c from session_attendance where session_id=4 group by attendance_status"):
    print(" ", dict(r))

print("\n== score stats ==")
for r in cur.execute("select min(student_score) mn, max(student_score) mx, round(avg(student_score),1) av from session_results where session_id=4"):
    print(" ", dict(r))
for r in cur.execute("select round(student_score/10)*10 b, count(*) c from session_results where session_id=4 group by b order by b"):
    print(" ", dict(r))

print("\n== flagged papers (needs_human_review=1) ==")
for r in cur.execute("select student_id, paper_id, student_score from session_results where session_id=4 and needs_human_review=1 order by student_id"):
    s = cur.execute("select name from students where id=?", (r['student_id'],)).fetchone()
    print(" ", r['student_id'], s['name'] if s else '?', r['student_score'])

print("\n== detail review/error counts ==")
for r in cur.execute(f"select error_category, count(*) c from session_details where result_id in ({ph}) and error_category is not null group by error_category order by c desc", rids):
    print(" ", dict(r))
