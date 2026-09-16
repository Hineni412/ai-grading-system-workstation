# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

for r in cur.execute("select student_id, raw_json from session_results where session_id=4"):
    d = json.loads(r['raw_json'])
    q8 = d.get('detail_metadata', {}).get('Q8')
    if not q8:
        continue
    s = cur.execute("select name from students where id=?", (r['student_id'],)).fetchone()
    if q8.get('primary_review_reason') == 'multiple_values_uncertain' or q8.get('review_reason') == 'multiple_values_uncertain':
        print(s['name'] if s else r['student_id'], json.dumps(q8, ensure_ascii=False)[:600])
        break
