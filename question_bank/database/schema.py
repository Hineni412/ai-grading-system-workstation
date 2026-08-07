from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.performance.metrics import instrument_sqlite_connection
from backend.schema_migrations import ensure_schema_current
from question_bank.relations.bootstrap import (
    bootstrap_governed_knowledge_identities,
)


@contextmanager
def connect(
    db_path: Path,
    *,
    external_connection: sqlite3.Connection | None = None,
) -> Iterator[sqlite3.Connection]:
    if external_connection is not None:
        yield external_connection
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = instrument_sqlite_connection(sqlite3.connect(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(
    db_path: Path,
    *,
    preserve_governed_conflicts: bool = False,
) -> None:
    """Bring the question bank to the current migration version."""
    ensure_schema_current("question_bank", db_path)
    with connect(db_path) as conn:
        bootstrap_governed_knowledge_identities(
            conn,
            preserve_conflicts=preserve_governed_conflicts,
        )
        conn.execute(
            """
            UPDATE papers
            SET province = COALESCE(NULLIF(province, ''), '广东省'),
                city = COALESCE(NULLIF(city, ''), '深圳市')
            WHERE title LIKE '%深圳%'
               OR source_file LIKE '%深圳%'
               OR district IN ('深圳市', '福田区', '罗湖区', '南山区', '宝安区', '龙岗区', '龙华区', '盐田区', '坪山区', '光明区', '大鹏新区')
            """
        )
        conn.execute(
            """
            UPDATE papers
            SET semester = CASE
                WHEN title LIKE '%（上）%' OR title LIKE '%(上)%'
                  OR title LIKE '%上学期%' OR title LIKE '%上册%' THEN '上学期'
                WHEN title LIKE '%（下）%' OR title LIKE '%(下)%'
                  OR title LIKE '%下学期%' OR title LIKE '%下册%' THEN '下学期'
                ELSE semester
            END
            WHERE COALESCE(semester, '') = ''
            """
        )
        conn.commit()
