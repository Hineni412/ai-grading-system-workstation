from __future__ import annotations

import sqlite3
from collections.abc import Iterable


def question_revision(conn: sqlite3.Connection, question_id: int) -> str | None:
    return question_revisions(conn, [int(question_id)]).get(int(question_id))


def question_revisions(
    conn: sqlite3.Connection,
    question_ids: Iterable[int],
) -> dict[int, str]:
    ids = list(dict.fromkeys(int(value) for value in question_ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT q.id, COALESCE(v.version, 0) AS version
        FROM questions q LEFT JOIN question_versions v ON v.question_id=q.id
        WHERE q.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    # The existing triggers advance this counter in the same transaction as
    # question/tag/evidence writes. No content scan or hash is needed.
    return {int(row['id']): f"q{row['id']}:{row['version']}" for row in rows}
