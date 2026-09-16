# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 刘宇婉 completeness + fallback fields
row = cur.execute("select s.raw_json, s.student_score from session_results s join students st on st.id=s.student_id where s.session_id=4 and st.name='刘宇婉'").fetchone()
d = json.loads(row['raw_json'])
print("score:", row['student_score'])
print("completeness:", json.dumps(d.get('grading_completeness'), ensure_ascii=False)[:1500])
print("fallback:", json.dumps(d.get('hybrid_batch_fallback'), ensure_ascii=False)[:1500])
md = d['detail_metadata']
print("Q12P2:", json.dumps(md.get('Q12(P2)'), ensure_ascii=False)[:500])

# check one Q13 response file written for batch1
print("\n== my Q13 batch1 response ==")
p = r'agent_bridge\session_4\requests\20260914_222253_0115_req_0115\response.json'
d2 = json.loads(open(p, encoding='utf-8').read())
print(json.dumps(d2, ensure_ascii=False)[:1200])
