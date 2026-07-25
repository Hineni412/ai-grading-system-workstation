"""Read-only, deterministic archive support for retired SQLite tables."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


ARCHIVE_VERSION = 1


class LegacyArchiveError(RuntimeError):
    """Raised when a complete legacy archive cannot be produced."""


@contextmanager
def _exclusive_output_lock(output: Path) -> Iterator[None]:
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
            raise LegacyArchiveError(
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
    *,
    allow_missing: bool,
) -> dict[str, Any]:
    metadata = connection.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()
    if not metadata:
        if allow_missing:
            return {"present": False, "columns": [], "rows": []}
        raise LegacyArchiveError(
            f"required legacy table is unavailable: {table_name}"
        )
    columns = [str(row[1]) for row in metadata]
    primary_key = [
        str(row[1])
        for row in sorted(metadata, key=lambda item: int(item[5]))
        if int(row[5]) > 0
    ]
    if not primary_key:
        raise LegacyArchiveError(
            f"required legacy table has no stable primary key: {table_name}"
        )
    order_by = ", ".join(f'"{column}"' for column in primary_key)
    rows = [
        dict(row)
        for row in connection.execute(
            f'SELECT * FROM "{table_name}" ORDER BY {order_by}'
        ).fetchall()
    ]
    return {"columns": columns, "rows": rows}


def export_table_archive(
    database: Path,
    output: Path,
    *,
    tables: Sequence[str],
    archive_format: str,
    overwrite: bool = False,
    allow_missing_tables: bool = False,
) -> dict[str, int]:
    """Write a complete, self-checking JSON archive from a read-only snapshot."""
    database = Path(database).resolve()
    output = Path(output).resolve()
    normalized_tables = tuple(str(table) for table in tables)
    if not database.is_file():
        raise LegacyArchiveError("source database is unavailable")
    if output == database:
        raise LegacyArchiveError("output must not replace the source database")
    if not normalized_tables or len(set(normalized_tables)) != len(normalized_tables):
        raise LegacyArchiveError("legacy table list must be unique and non-empty")

    output.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_output_lock(output):
        if output.exists() and not overwrite:
            raise LegacyArchiveError(
                "output already exists; use --overwrite to replace it"
            )

        connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            archived_tables = {
                table_name: _read_table(
                    connection,
                    table_name,
                    allow_missing=allow_missing_tables,
                )
                for table_name in normalized_tables
            }
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        summary = {
            table_name: len(archived_tables[table_name]["rows"])
            for table_name in normalized_tables
        }
        canonical_tables = json.dumps(
            archived_tables,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload = {
            "format": archive_format,
            "version": ARCHIVE_VERSION,
            "tables": archived_tables,
            "summary": summary,
            "content_sha256": hashlib.sha256(canonical_tables).hexdigest(),
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
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
