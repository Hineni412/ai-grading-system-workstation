from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any


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
    question_rows = conn.execute(
        f"""
        SELECT id, is_deleted, updated_at
        FROM questions
        WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    tag_rows = conn.execute(
        f"""
        SELECT
            id, question_id, tag_type, tag_value,
            confidence, source, model_name
        FROM question_tags
        WHERE question_id IN ({placeholders})
        ORDER BY
            question_id, tag_type, tag_value,
            COALESCE(source, ''), COALESCE(confidence, -1),
            COALESCE(model_name, ''), id
        """,
        ids,
    ).fetchall()
    tags_by_question: dict[int, list[dict[str, Any]]] = {value: [] for value in ids}
    for row in tag_rows:
        tags_by_question[int(row["question_id"])].append(
            {
                "tag_type": row["tag_type"],
                "tag_value": row["tag_value"],
                "confidence": row["confidence"],
                "source": row["source"],
                "model_name": row["model_name"],
            }
        )
    revisions: dict[int, str] = {}
    for row in question_rows:
        question_id = int(row["id"])
        payload = {
            "id": question_id,
            "is_deleted": bool(row["is_deleted"]),
            "updated_at": str(row["updated_at"] or ""),
            "tags": tags_by_question.get(question_id, []),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        revisions[question_id] = hashlib.sha256(serialized).hexdigest()
    return revisions
