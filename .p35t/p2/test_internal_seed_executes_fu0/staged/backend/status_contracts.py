"""Shared persistent-state contracts for the grading database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatusContract:
    table: str
    column: str
    allowed: frozenset[str]

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


STATUS_CONTRACTS = (
    StatusContract(
        "answer_regions",
        "mapping_status",
        frozenset({"auto", "manual", "unbound"}),
    ),
    StatusContract(
        "exam_papers",
        "match_status",
        frozenset({"matched", "student_deleted", "unmatched"}),
    ),
    StatusContract(
        "exam_papers",
        "processing_status",
        frozenset({"failed", "graded", "grading", "pending", "skipped"}),
    ),
    StatusContract(
        "grading_sessions",
        "status",
        frozenset({"completed", "created", "failed", "running"}),
    ),
)

_CONTRACT_BY_KEY = {contract.key: contract for contract in STATUS_CONTRACTS}


def validate_status(contract_key: str, value: object) -> str:
    """Return a legal status value or raise a stable boundary error."""
    contract = _CONTRACT_BY_KEY[contract_key]
    normalized = str(value) if value is not None else ""
    if normalized not in contract.allowed:
        allowed = ", ".join(sorted(contract.allowed))
        raise ValueError(
            f"invalid {contract_key}: expected one of {allowed}"
        )
    return normalized


def _classify(value: str | None, allowed: frozenset[str]) -> str:
    if value is None:
        return "null"
    if not value.strip():
        return "blank"
    if value in allowed:
        return "allowed"
    return "unknown"


def audit_status_values(database: Path) -> dict[str, dict[str, Any]]:
    """Inventory persistent status values through a read-only SQLite handle."""
    database = Path(database)
    uri = database.resolve().as_uri() + "?mode=ro"
    report: dict[str, dict[str, Any]] = {}
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        for contract in STATUS_CONTRACTS:
            rows = connection.execute(
                f"""
                SELECT {contract.column}, COUNT(*)
                FROM {contract.table}
                GROUP BY {contract.column}
                ORDER BY {contract.column}
                """
            ).fetchall()
            report[contract.key] = {
                "allowed": sorted(contract.allowed),
                "observed": [
                    {
                        "classification": _classify(value, contract.allowed),
                        "count": int(count),
                        "value": value,
                    }
                    for value, count in rows
                ],
            }
    return report
