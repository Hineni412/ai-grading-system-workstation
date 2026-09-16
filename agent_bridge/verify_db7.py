# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1) Q8 recognized answers across papers
print("== Q8 recognized answers (raw_json detail_metadata) ==")
cnt = {}
for r in cur.execute("select student_id, raw_json from session_results where session_id=4"):
    d = json.loads(r['raw_json'])
    md = d.get('detail_metadata', {})
    q8 = md.get('Q8')
    if q8:
        key = (str(q8.get('recognized_answer')), str(q8.get('is_correct')), str(q8.get('need_review')), str(q8.get('primary_review_reason') or q8.get('review_reason')))
        cnt[key] = cnt.get(key, 0) + 1
for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
    print(" ", v, k)

# 2) check a missing-Q13-detail paper's raw_json: does it contain Q13 grading detail?
print("\n== 刘宇婉 raw_json structure ==")
row = cur.execute("select s.raw_json, s.student_id from session_results s join students st on st.id=s.student_id where s.session_id=4 and st.name='刘宇婉'").fetchone()
d = json.loads(row['raw_json'])
print("keys:", list(d.keys()))
det = d.get('grading_details') or d.get('details') or {}
print("detail keys:", list(det.keys()) if isinstance(det, dict) else type(det))
md = d.get('detail_metadata', {})
print("metadata keys:", list(md.keys()))
if 'Q13' in md:
    print("Q13 meta:", json.dumps(md['Q13'], ensure_ascii=False)[:800])
if 'Q12(P1)' in md:
    print("Q12P1 meta:", json.dumps(md['Q12(P1)'], ensure_ascii=False)[:800])
