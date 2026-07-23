from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_tools.migrate_db import get_migration_status, run_migrations


@dataclass(frozen=True)
class SchemaGateResult:
    target: str
    current_version: str
    applied: tuple[str, ...]


class SchemaVersionError(RuntimeError):
    """The database cannot be safely opened by this application version."""


def ensure_schema_current(
    target: str,
    db_path: Path,
    *,
    migrations_dir: Path | None = None,
) -> SchemaGateResult:
    migration_root = (
        Path(migrations_dir)
        if migrations_dir is not None
        else Path(__file__).resolve().parents[1] / "migrations" / target
    )
    report = run_migrations(
        target,
        db_path=Path(db_path),
        migrations_dir=migration_root,
    )
    if report.error:
        raise SchemaVersionError(report.error)
    status = get_migration_status(
        target,
        db_path_override=Path(db_path),
        migrations_dir_override=migration_root,
    )
    applied = tuple(str(item) for item in status.get("applied") or ())
    return SchemaGateResult(
        target=target,
        current_version=applied[-1] if applied else "0",
        applied=applied,
    )


def ensure_application_schema(paths: Any) -> dict[str, SchemaGateResult]:
    project_root = Path(
        getattr(paths, "project_root", Path(__file__).resolve().parents[1])
    )
    return {
        "grading": ensure_schema_current(
            "grading",
            Path(paths.db_path),
            migrations_dir=project_root / "migrations" / "grading",
        ),
        "question_bank": ensure_schema_current(
            "question_bank",
            Path(paths.qb_db_path),
            migrations_dir=project_root / "migrations" / "question_bank",
        ),
    }


__all__ = [
    "SchemaGateResult",
    "SchemaVersionError",
    "ensure_application_schema",
    "ensure_schema_current",
]
