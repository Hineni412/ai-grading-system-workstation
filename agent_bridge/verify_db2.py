# -*- coding: utf-8 -*-
import sqlite3, json

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

def cols(t):
    return [r[1] for r in cur.execute(f'pragma table_info("{t}")')]

print("== grading_runs ==")
print("cols:", cols('grading_runs'))
for r in cur.execute("select * from grading_runs order by id"):
    d = dict(r)
    for k, v in d.items():
        s = str(v)
        print(f"  run {d.get('id')}: {k}={s[:140]}")

print("\n== grading_run_items (session 4) ==")
print("cols:", cols('grading_run_items'))
for r in cur.execute("select * from grading_run_items limit 5"):
    print(" ", {k: str(v)[:80] for k, v in dict(r).items()})
