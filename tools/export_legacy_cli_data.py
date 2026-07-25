"""Export the retired legacy CLI tables without modifying the source database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ARCHIVE_FORMAT = "ai-grading-legacy-cli-export"
ARCHIVE_VERSION = 1
LEGACY_TABLES = ("exam_results", "grading_details")


class LegacyExportError(RuntimeError):
    """Raised when a complete legacy archive cannot be produced."""


@contextmanager
def _exclusive_output_lock(output: Path) -> Iterator[None]:
    """Prevent two processes from publishing to the same archive path."""
    lock_path = output.with_name(f".{output.name}.lock")
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LegacyExportError(
                "another export is already targeting this output"
            ) from exc
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, Any]:
    columns = [
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    ]
    if not columns:
        raise LegacyExportError(f"required legacy table is unavailable: {table_name}")
    rows = [
        dict(row)
        for row in connection.execute(
            f'SELECT * FROM "{table_name}" ORDER BY id'
        ).fetchall()
    ]
    return {"columns": columns, "rows": rows}


def export_legacy_cli_data(
    database: Path,
    output: Path,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Write a complete, self-checking JSON archive from a read-only snapshot."""
    database = Path(database).resolve()
    output = Path(output).resolve()
    if not database.is_file():
        raise LegacyExportError("source database is unavailable")
    if output == database:
        raise LegacyExportError("output must not replace the source database")

    output.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_output_lock(output):
        if output.exists() and not overwrite:
            raise LegacyExportError(
                "output already exists; use --overwrite to replace it"
            )

        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            tables = {
                table_name: _read_table(connection, table_name)
                for table_name in LEGACY_TABLES
            }
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        summary = {
            table_name: len(tables[table_name]["rows"])
            for table_name in LEGACY_TABLES
        }
        canonical_tables = json.dumps(
            tables,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload = {
            "format": ARCHIVE_FORMAT,
            "version": ARCHIVE_VERSION,
            "tables": tables,
            "summary": summary,
            "content_sha256": hashlib.sha256(canonical_tables).hexdigest(),
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, output)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export retired main.py result tables to a JSON archive.",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = export_legacy_cli_data(
            args.database,
            args.output,
            overwrite=bool(args.overwrite),
        )
    except (LegacyExportError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"legacy export failed: {exc}\n")

    print(
        "legacy export complete: "
        f"exam_results={summary['exam_results']}, "
        f"grading_details={summary['grading_details']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
