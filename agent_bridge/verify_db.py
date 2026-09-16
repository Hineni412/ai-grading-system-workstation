# -*- coding: utf-8 -*-
"""Read-only verification of session-4 grading persistence."""
import sqlite3, json, sys

db = r'user_data\databases\grading_system.db'
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("== tables ==")
tables = [r[0] for r in cur.execute(
    "select name from sqlite_master where type='table' order by name")]
for t in tables:
    n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
    print(f"  {t}: {n}")
