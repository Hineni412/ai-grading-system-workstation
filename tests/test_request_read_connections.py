from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from db_manager import DBManager
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import (
    QuestionBankSnapshotUnavailable,
    captured_sqlite_read_connection,
)


def test_db_manager_borrowed_connection_reuses_identity_without_closing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grading.db"
    external = sqlite3.connect(db_path)
    try:
        external.execute("CREATE TEMP TABLE request_identity(value TEXT)")
        external.execute(
            "INSERT INTO request_identity(value) VALUES ('same-connection')"
        )
        manager = DBManager(db_path, external_connection=external)

        borrowed = manager._connect()
        assert borrowed.execute(
            "SELECT value FROM request_identity"
        ).fetchone()[0] == "same-connection"

        borrowed.close()
        assert external.execute("SELECT 1").fetchone()[0] == 1
    finally:
        external.close()


def test_db_manager_nested_context_does_not_commit_or_end_request_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grading.db"
    external = sqlite3.connect(db_path, isolation_level=None)
    try:
        external.execute("CREATE TABLE items(value TEXT)")
        external.execute("BEGIN")
        manager = DBManager(db_path, external_connection=external)

        with manager._connect() as outer:
            with manager._connect() as inner:
                assert outer.execute("SELECT 1").fetchone()[0] == 1
                assert inner.execute("SELECT 1").fetchone()[0] == 1
                inner.commit()
                inner.rollback()
                inner.close()
                assert external.in_transaction is True

        assert external.in_transaction is True
        external.rollback()
    finally:
        external.close()


def test_question_bank_connect_borrowed_connection_does_not_own_lifecycle(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    external = sqlite3.connect(db_path)
    try:
        external.execute("CREATE TABLE items(value TEXT)")
        external.commit()
        external.execute("INSERT INTO items(value) VALUES ('normal-exit')")

        with connect(db_path, external_connection=external) as borrowed:
            assert borrowed is external

        assert external.in_transaction is True
        with pytest.raises(RuntimeError, match="borrowed failure"):
            with connect(db_path, external_connection=external) as borrowed:
                borrowed.execute(
                    "INSERT INTO items(value) VALUES ('exception-exit')"
                )
                raise RuntimeError("borrowed failure")

        assert external.in_transaction is True
        assert external.execute(
            "SELECT value FROM items ORDER BY rowid"
        ).fetchall() == [("normal-exit",), ("exception-exit",)]
        external.rollback()
    finally:
        external.close()

    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT value FROM items").fetchall() == []


def test_snapshot_connection_allows_cross_worker_teardown_but_rejects_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    with sqlite3.connect(db_path) as seed:
        seed.execute("CREATE TABLE items(value TEXT)")
        seed.execute("INSERT INTO items(value) VALUES ('snapshot')")

    context = captured_sqlite_read_connection(
        db_path,
        required_tables=frozenset({"items"}),
        check_same_thread=False,
    )
    connection = context.__enter__()
    try:
        manager = DBManager(db_path, external_connection=connection)
        assert connection.in_transaction is True
        assert manager._connect().execute(
            "SELECT value FROM items"
        ).fetchone()["value"] == "snapshot"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            manager._connect().execute("INSERT INTO items(value) VALUES ('blocked')")
    finally:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(context.__exit__, None, None, None).result()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_legacy_connections_keep_existing_pragmas_and_close_behavior(
    tmp_path: Path,
) -> None:
    grading_path = tmp_path / "grading.db"
    grading_connection = DBManager(grading_path)._connect()
    assert grading_connection.row_factory is sqlite3.Row
    assert grading_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert grading_connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert grading_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    grading_connection.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        grading_connection.execute("SELECT 1")

    question_bank_path = tmp_path / "question-bank.db"
    with connect(question_bank_path) as question_bank_connection:
        assert question_bank_connection.row_factory is sqlite3.Row
        assert question_bank_connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1
        assert question_bank_connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0] == 5000
        assert question_bank_connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0] == "wal"

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        question_bank_connection.execute("SELECT 1")


def _request_paths(tmp_path: Path) -> SimpleNamespace:
    grading_path = tmp_path / "grading.db"
    question_bank_path = tmp_path / "question-bank.db"
    DBManager(grading_path).initialize()
    initialize_database(question_bank_path)
    return SimpleNamespace(
        db_path=grading_path,
        qb_db_path=question_bank_path,
    )


def _main_candidate_path(connection: sqlite3.Connection) -> Path:
    row = next(
        row
        for row in connection.execute("PRAGMA database_list").fetchall()
        if str(row[1]) == "main"
    )
    return Path(str(row[2]))


def _seed_request_boundary_tables(paths: SimpleNamespace) -> None:
    for db_path, value in (
        (paths.db_path, "grading-original"),
        (paths.qb_db_path, "question-bank-original"),
    ):
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE request_boundary(value TEXT NOT NULL)")
            connection.execute(
                "INSERT INTO request_boundary(value) VALUES (?)",
                (value,),
            )


def _sqlite_file_state(db_path: Path) -> dict[str, tuple[bool, bytes | None, int | None]]:
    state: dict[str, tuple[bool, bytes | None, int | None]] = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            state[suffix or "main"] = (True, path.read_bytes(), path.stat().st_mtime_ns)
        else:
            state[suffix or "main"] = (False, None, None)
    return state


def test_request_snapshot_stays_stable_while_later_request_sees_legacy_commit(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)

    with read_connections.request_read_context(paths) as current:
        assert current.grading_connection.execute(
            "SELECT value FROM request_boundary"
        ).fetchone()["value"] == "grading-original"

        writer = DBManager(paths.db_path)._connect()
        try:
            writer.execute(
                "UPDATE request_boundary SET value = ?",
                ("grading-committed",),
            )
            writer.commit()
        finally:
            writer.close()

        assert current.grading_connection.execute(
            "SELECT value FROM request_boundary"
        ).fetchone()["value"] == "grading-original"

    with read_connections.request_read_context(paths) as later:
        assert later.grading_connection.execute(
            "SELECT value FROM request_boundary"
        ).fetchone()["value"] == "grading-committed"


@pytest.mark.parametrize(
    "connection_name, table_value",
    [
        ("grading_connection", "grading-original"),
        ("question_bank_connection", "question-bank-original"),
    ],
)
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO request_boundary(value) VALUES ('blocked')",
        "UPDATE request_boundary SET value = 'blocked'",
        "CREATE TABLE blocked_ddl(value TEXT)",
    ],
    ids=["insert", "update", "ddl"],
)
def test_request_connections_reject_writes_without_changing_source_or_candidate(
    tmp_path: Path,
    connection_name: str,
    table_value: str,
    statement: str,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)

    with read_connections.request_read_context(paths) as context:
        connection = getattr(context, connection_name)
        candidate = _main_candidate_path(connection)
        source = paths.db_path if connection_name == "grading_connection" else paths.qb_db_path
        source_before = _sqlite_file_state(source)
        candidate_before = _sqlite_file_state(candidate)

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(statement)

        assert connection.execute(
            "SELECT value FROM request_boundary"
        ).fetchone()["value"] == table_value
        assert _sqlite_file_state(source) == source_before
        assert _sqlite_file_state(candidate) == candidate_before


def test_concurrent_request_cursors_keep_results_and_connections_isolated(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    _seed_request_boundary_tables(paths)
    request_count = 4
    barrier = Barrier(request_count)

    def capture_result(index: int) -> tuple[int, int, tuple[str, str], tuple[str, str]]:
        with read_connections.request_read_context(paths) as context:
            grading_cursor = context.grading_connection.execute(
                "SELECT ? AS marker, value FROM request_boundary",
                (f"grading-{index}",),
            )
            question_bank_cursor = context.question_bank_connection.execute(
                "SELECT ? AS marker, value FROM request_boundary",
                (f"question-bank-{index}",),
            )
            identities = (
                id(context.grading_connection),
                id(context.question_bank_connection),
            )
            barrier.wait(timeout=10)
            return (
                *identities,
                tuple(grading_cursor.fetchone()),
                tuple(question_bank_cursor.fetchone()),
            )

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        results = list(executor.map(capture_result, range(request_count)))

    assert len({result[0] for result in results}) == request_count
    assert len({result[1] for result in results}) == request_count
    for index, result in enumerate(results):
        assert result[2] == (f"grading-{index}", "grading-original")
        assert result[3] == (
            f"question-bank-{index}",
            "question-bank-original",
        )


def test_request_read_context_opens_each_database_once_and_cleans_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    real_capture = read_connections.captured_sqlite_read_connection
    calls: list[Path] = []
    connections: list[sqlite3.Connection] = []
    candidates: list[Path] = []

    @contextmanager
    def tracked_capture(db_path: Path, **kwargs):
        calls.append(Path(db_path))
        with real_capture(db_path, **kwargs) as connection:
            connections.append(connection)
            candidates.append(_main_candidate_path(connection))
            yield connection

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        tracked_capture,
    )

    with read_connections.request_read_context(paths) as context:
        assert calls == [paths.db_path, paths.qb_db_path]
        assert context.grading_connection is connections[0]
        assert context.question_bank_connection is connections[1]
        assert context.grading_candidate == candidates[0]
        assert context.question_bank_candidate == candidates[1]
        assert context.grading_db._external_connection is connections[0]
        assert context.diagnosis_service.db is context.grading_db
        assert context.diagnosis_service.question_bank_connection is connections[1]
        assert context.practice_service.external_connection is connections[1]
        assert all(candidate.exists() for candidate in candidates)

    assert not any(candidate.exists() for candidate in candidates)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_request_read_context_cleans_first_database_when_second_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    real_capture = read_connections.captured_sqlite_read_connection
    first_connection: sqlite3.Connection | None = None
    first_candidate: Path | None = None

    @contextmanager
    def fail_second_capture(db_path: Path, **kwargs):
        nonlocal first_connection, first_candidate
        if Path(db_path) == paths.qb_db_path:
            raise QuestionBankSnapshotUnavailable("private path must stay hidden")
        with real_capture(db_path, **kwargs) as connection:
            first_connection = connection
            first_candidate = _main_candidate_path(connection)
            yield connection

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        fail_second_capture,
    )

    with pytest.raises(QuestionBankSnapshotUnavailable):
        with read_connections.request_read_context(paths):
            pytest.fail("the context must not yield after partial setup failure")

    assert first_connection is not None
    assert first_candidate is not None
    assert not first_candidate.exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        first_connection.execute("SELECT 1")


def test_request_read_context_cleans_both_databases_after_business_error(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    context = None
    with pytest.raises(RuntimeError, match="business failure"):
        with read_connections.request_read_context(paths) as context:
            raise RuntimeError("business failure")

    assert context is not None
    assert not context.grading_candidate.exists()
    assert not context.question_bank_candidate.exists()
    for connection in (
        context.grading_connection,
        context.question_bank_connection,
    ):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_concurrent_request_read_contexts_have_distinct_owned_resources(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    paths = _request_paths(tmp_path)
    barrier = Barrier(2)

    def capture_identity(_index: int) -> tuple[Path, Path, int, int]:
        with read_connections.request_read_context(paths) as context:
            identity = (
                context.grading_candidate,
                context.question_bank_candidate,
                id(context.grading_connection),
                id(context.question_bank_connection),
            )
            barrier.wait(timeout=10)
            return identity

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(capture_identity, range(2)))

    assert identities[0][0] != identities[1][0]
    assert identities[0][1] != identities[1][1]
    assert identities[0][2] != identities[1][2]
    assert identities[0][3] != identities[1][3]
    assert not any(
        candidate.exists()
        for identity in identities
        for candidate in identity[:2]
    )


def test_request_read_context_can_teardown_on_another_worker_thread(
    tmp_path: Path,
) -> None:
    import backend.api.read_connections as read_connections

    manager = read_connections.request_read_context(_request_paths(tmp_path))
    context = manager.__enter__()
    exited = False
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(manager.__exit__, None, None, None).result(timeout=10)
        exited = True
    finally:
        if not exited:
            manager.__exit__(None, None, None)

    assert not context.grading_candidate.exists()
    assert not context.question_bank_candidate.exists()
    for connection in (
        context.grading_connection,
        context.question_bank_connection,
    ):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_route_validation_error_cleans_request_read_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    import backend.api.read_connections as read_connections
    from backend.api.app import create_app
    from path_manager import get_path_manager

    paths = _request_paths(tmp_path)
    real_capture = read_connections.captured_sqlite_read_connection
    connections: list[sqlite3.Connection] = []
    candidates: list[Path] = []

    @contextmanager
    def tracked_capture(db_path: Path, **kwargs):
        with real_capture(db_path, **kwargs) as connection:
            connections.append(connection)
            candidates.append(_main_candidate_path(connection))
            yield connection

    monkeypatch.setattr(
        read_connections,
        "captured_sqlite_read_connection",
        tracked_capture,
    )
    app = create_app(path_manager=paths)
    app.dependency_overrides[get_path_manager] = lambda: paths

    response = TestClient(app).post(
        "/api/graph/query",
        json={"scope": {"mode": "student", "student_ids": []}},
    )

    assert response.status_code == 422
    assert len(connections) == len(candidates) == 2
    assert not any(candidate.exists() for candidate in candidates)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
