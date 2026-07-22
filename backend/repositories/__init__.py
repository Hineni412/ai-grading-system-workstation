"""Public repository contracts and SQLite transaction boundaries."""

from backend.repositories.base import (
    ReadOnlyRepositoryError,
    Repository,
    RepositoryClosedError,
    RepositoryConnectionError,
    RepositoryError,
    RepositorySession,
    RepositoryThreadError,
    RepositoryTransactionError,
    RowMapper,
    SQLiteConnectionFactory,
    map_row,
    map_rows,
)

__all__ = [
    "ReadOnlyRepositoryError",
    "Repository",
    "RepositoryClosedError",
    "RepositoryConnectionError",
    "RepositoryError",
    "RepositorySession",
    "RepositoryThreadError",
    "RepositoryTransactionError",
    "RowMapper",
    "SQLiteConnectionFactory",
    "map_row",
    "map_rows",
]
