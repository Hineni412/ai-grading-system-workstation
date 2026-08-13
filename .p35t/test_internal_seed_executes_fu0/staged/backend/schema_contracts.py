from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


def _normalized_sql(sql: object) -> str:
    return (
        re.sub(r"\s+", " ", str(sql))
        .strip()
        .lower()
        .replace("if not exists ", "")
    )


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
        ).fetchall()
    ]


def _column_snapshot(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, tuple[str, int, str, int, int]]:
    columns: dict[str, tuple[str, int, str, int, int]] = {}
    for row in connection.execute(
        f'PRAGMA table_xinfo("{table_name}")'
    ).fetchall():
        column_name = str(row[1])
        notnull = int(row[3])
        # SQLite cannot strengthen the historical ADD COLUMN in place. Existing
        # triggers enforce the same nonblank invariant for this one legacy field.
        if table_name == "answer_regions" and column_name == "region_uuid":
            notnull = 0
        columns[column_name] = (
            str(row[2] or "").lower(),
            notnull,
            _normalized_sql(row[4]),
            int(row[5]),
            int(row[6]),
        )
    return columns


def _foreign_key_snapshot(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[tuple[object, ...]]:
    return {
        (row[2], row[3], row[4], row[5], row[6])
        for row in connection.execute(
            f'PRAGMA foreign_key_list("{table_name}")'
        ).fetchall()
    }


def _unique_constraint_snapshot(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[tuple[tuple[str, ...], int]]:
    constraints: set[tuple[tuple[str, ...], int]] = set()
    for row in connection.execute(
        f'PRAGMA index_list("{table_name}")'
    ).fetchall():
        if not int(row[2]):
            continue
        index_name = str(row[1]).replace('"', '""')
        columns = tuple(
            str(item[2])
            for item in connection.execute(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
        )
        # An inline UNIQUE constraint and a matching explicit unique index
        # enforce the same key. Historical databases can contain only the
        # explicit form while a clean migration database contains both.
        constraints.add((columns, int(row[4])))
    return constraints


def _check_expressions(create_sql: str) -> tuple[str, ...]:
    text = str(create_sql or "")
    lowered = text.casefold()
    checks: list[str] = []
    index = 0
    while index < len(text):
        match = re.search(r"\bcheck\s*\(", lowered[index:])
        if match is None:
            break
        open_index = index + match.end() - 1
        depth = 1
        cursor = open_index + 1
        quote: str | None = None
        while cursor < len(text) and depth:
            char = text[cursor]
            if quote is not None:
                if char == quote:
                    if cursor + 1 < len(text) and text[cursor + 1] == quote:
                        cursor += 2
                        continue
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        if depth:
            break
        checks.append(_normalized_sql(text[open_index + 1 : cursor - 1]))
        index = cursor
    return tuple(sorted(checks))


def _schema_objects(db_path: Path) -> dict[tuple[str, str], str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_migrations' AND type IN ('index', 'trigger')"
        ).fetchall()
    return {
        (str(type_), str(name)): _normalized_sql(sql)
        for type_, name, sql in rows
    }


def schema_signature(db_path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(db_path)) as connection:
        tables: dict[str, object] = {}
        for table_name in _table_names(connection):
            create_row = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            tables[table_name] = {
                "columns": _column_snapshot(connection, table_name),
                "foreign_keys": _foreign_key_snapshot(
                    connection,
                    table_name,
                ),
                "unique_constraints": _unique_constraint_snapshot(
                    connection,
                    table_name,
                ),
                "checks": _check_expressions(
                    str(create_row[0] if create_row else "")
                ),
            }
    return {
        "tables": tables,
        "indexes_and_triggers": _schema_objects(db_path),
    }


def schemas_equivalent(left_db: Path, right_db: Path) -> bool:
    return schema_signature(left_db) == schema_signature(right_db)


__all__ = ["schema_signature", "schemas_equivalent"]
