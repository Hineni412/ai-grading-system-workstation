"""Build the current schema contracts from isolated empty databases.

Historical migration files and installed databases are never rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.schema_contracts import (  # noqa: E402
    current_schema_contract_path,
    schema_signature_document,
)
from update_tools.migrate_db import MigrationFile, run_migrations  # noqa: E402


class _QuietLogger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def error(self, *_args: object, **_kwargs: object) -> None:
        pass


def _creation_statements(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY "
            "CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 ELSE 2 END, name"
        ).fetchall()
        statements = [str(sql) for kind, _name, sql in objects if kind in {"table", "view"}]
        for kind, name, _sql in objects:
            if kind != "table" or name == "schema_migrations":
                continue
            table = '"' + str(name).replace('"', '""') + '"'
            # SQLite supplies seed timestamps so repeated generation has stable output.
            columns = [
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
                if not re.search(
                    r"current_(?:timestamp|time|date)|\b(?:datetime|strftime|date|time)\s*\(\s*['\"]now['\"]",
                    str(row[4] or ""),
                    re.IGNORECASE,
                )
            ]
            names = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
            for row in connection.execute(f"SELECT {names} FROM {table} ORDER BY {names}"):
                values = ", ".join(str(connection.execute("SELECT quote(?)", (value,)).fetchone()[0]) for value in row)
                statements.append(f"INSERT INTO {table} ({names}) VALUES ({values})")
        sequences = connection.execute(
            "SELECT name, seq FROM sqlite_sequence WHERE name != 'schema_migrations' ORDER BY name"
        ).fetchall()
        for name, sequence in sequences:
            quoted = connection.execute("SELECT quote(?)", (name,)).fetchone()[0]
            statements.append(f"INSERT INTO sqlite_sequence (name, seq) VALUES ({quoted}, {int(sequence)})")
        statements.extend(str(sql) for kind, _name, sql in objects if kind not in {"table", "view"})
        return statements


def generate_contract(target: str) -> dict[str, object]:
    migrations_dir = PROJECT_ROOT / "migrations" / target
    migrations = [
        MigrationFile.from_path(path) for path in sorted(migrations_dir.glob("*.sql"))
    ]
    if not migrations:
        raise ValueError("migration manifest is empty")
    with tempfile.TemporaryDirectory(prefix=f"TEST-current-schema-{target}-") as raw:
        root = Path(raw)
        database = root / "current.db"
        report = run_migrations(
            target,
            db_path=database,
            migrations_dir=migrations_dir,
            backup_dir_override=root / "backups",
            logger_override=_QuietLogger(),
        )
        if report.error:
            raise ValueError("migration manifest cannot build the current schema")
        return {
            "migration_checksums": [[item.name, item.checksum] for item in migrations],
            "schema": schema_signature_document(database),
            "creation_statements": _creation_statements(database),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate current schema contracts without rewriting history")
    parser.add_argument("--target", choices=("grading", "question_bank", "all"), default="all")
    parser.add_argument("--check", action="store_true", help="Check generated contracts without changing files")
    args = parser.parse_args(argv)
    targets = ("grading", "question_bank") if args.target == "all" else (args.target,)
    for target in targets:
        contract = generate_contract(target)
        destination = current_schema_contract_path(target)
        if args.check:
            try:
                current = json.loads(destination.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = None
            if current != contract:
                print(f"当前结构定义需要更新：{target}", file=sys.stderr)
                return 1
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            with destination.open("w", encoding="utf-8", newline="\n") as output:
                for start in range(0, len(content), 4096):
                    output.write(content[start : start + 4096])
        print(f"当前结构定义已{'核对' if args.check else '生成'}：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
