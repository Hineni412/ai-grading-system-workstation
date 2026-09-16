# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

rids = {r['id']: r for r in cur.execute("select id, student_id, paper_id, student_score from session_results where session_id=4")}
ph = ",".join("?" * len(rids))

# which result_ids lack each subjective detail
for q in ['Q12(P1)', 'Q12(P2)', 'Q13']:
    have = {r['result_id'] for r in cur.execute(f"select result_id from session_details where result_id in ({ph}) and question_id=?", [*rids, q])}
    missing = [rid for rid in rids if rid not in have]
    names = []
    for rid in missing:
        s = cur.execute("select name from students where id=?", (rids[rid]['student_id'],)).fetchone()
        names.append(s['name'] if s else str(rids[rid]['student_id']))
    print(q, "missing", len(missing), names)

# Q8 detail sample
print("\n== Q8 samples ==")
for r in cur.execute(f"select * from session_details where result_id in ({ph}) and question_id='Q8' limit 6", list(rids)):
    print({k: str(v)[:90] for k, v in dict(r).items()})

# raw_json of one result: check Q8 + missing-detail papers' totals
print("\n== one raw_json ==")
row = cur.execute("select raw_json from session_results where session_id=4 limit 1").fetchone()
d = json.loads(row['raw_json'])
print(json.dumps(d, ensure_ascii=False)[:3000])
