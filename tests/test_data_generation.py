from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from integration.data_generation import (
    commit_generation,
    reset_commit_generations,
)


@pytest.fixture(autouse=True)
def _clean_monitors():
    reset_commit_generations()
    yield
    reset_commit_generations()


def _seed(path: Path) -> None:
    # closing() is required: "with sqlite3.connect" commits but does not
    # close, and an open handle blocks os.replace on Windows.
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE items(value TEXT)")
        connection.execute("INSERT INTO items(value) VALUES ('seed')")
        connection.commit()


def test_commit_generation_is_stable_across_read_only_opens_and_checkpoints(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "probe.db"
    _seed(db_path)

    baseline = commit_generation(db_path)
    assert commit_generation(db_path) == baseline

    # Read-only opens must not invalidate caches keyed on the generation.
    readonly = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro", uri=True
    )
    try:
        readonly.execute("SELECT COUNT(*) FROM items").fetchone()
    finally:
        readonly.close()
    assert commit_generation(db_path) == baseline

    # A WAL checkpoint moves bytes but commits no data change.
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert commit_generation(db_path) == baseline


def test_commit_generation_bumps_when_another_connection_commits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "probe.db"
    _seed(db_path)

    baseline = commit_generation(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO items(value) VALUES ('written')")

    bumped = commit_generation(db_path)
    assert bumped != baseline
    assert commit_generation(db_path) == bumped


def test_commit_generation_bumps_when_file_is_replaced(tmp_path: Path) -> None:
    from integration import data_generation

    db_path = tmp_path / "probe.db"
    _seed(db_path)
    baseline = commit_generation(db_path)

    # Restores swap the database file while the application is stopped;
    # releasing the monitor handle mirrors that (Windows cannot replace a
    # file an open connection still holds).
    for monitor in data_generation._MONITORS.values():
        monitor._close()

    replacement = tmp_path / "replacement.db"
    _seed(replacement)
    os.replace(replacement, db_path)

    assert commit_generation(db_path) != baseline


def test_commit_generation_changes_every_call_for_missing_database(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "absent.db"
    first = commit_generation(missing)
    second = commit_generation(missing)
    # Missing files never share a generation, so caches cannot hold stale
    # results keyed to a vanished database.
    assert first != second
