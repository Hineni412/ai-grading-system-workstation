"""Public repository contracts and SQLite transaction boundaries."""

from backend.repositories.access import (
    GradingRepositoryAccess,
    as_grading_repositories,
)

from backend.repositories.base import (
    ReadOnlyRepositoryError,
    Repository,
    RepositoryClosedError,
    RepositoryConnection,
    RepositoryConnectionError,
    RepositoryCursor,
    RepositoryError,
    RepositorySession,
    RepositorySessionProvider,
    RepositoryThreadError,
    RepositoryTransactionError,
    RowMapper,
    SQLiteConnectionFactory,
    map_row,
    map_rows,
)
from backend.repositories.providers import (
    BorrowedReadOnlySessionProvider,
    InstrumentedSQLiteConnectionFactory,
)

__all__ = [
    "GradingRepositoryAccess",
    "as_grading_repositories",
    "ReadOnlyRepositoryError",
    "Repository",
    "RepositoryClosedError",
    "RepositoryConnection",
    "RepositoryConnectionError",
    "RepositoryCursor",
    "RepositoryError",
    "RepositorySession",
    "RepositorySessionProvider",
    "RepositoryThreadError",
    "RepositoryTransactionError",
    "RowMapper",
    "SQLiteConnectionFactory",
    "BorrowedReadOnlySessionProvider",
    "InstrumentedSQLiteConnectionFactory",
    "map_row",
    "map_rows",
]
