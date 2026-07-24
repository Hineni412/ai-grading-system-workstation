"""Read-only audit for persistent grading status values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from backend.status_contracts import audit_status_values


def _build_payload(database: Path) -> dict[str, object]:
    contracts = audit_status_values(database)
    summary = {
        "allowed_rows": 0,
        "blank_rows": 0,
        "null_rows": 0,
        "unknown_rows": 0,
    }
    for contract in contracts.values():
        for observed in contract["observed"]:
            classification = str(observed["classification"])
            summary[f"{classification}_rows"] += int(observed["count"])
    return {
        "contracts": contracts,
        "safe_to_migrate": (
            summary["blank_rows"] == 0
            and summary["null_rows"] == 0
            and summary["unknown_rows"] == 0
        ),
        "summary": summary,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit persistent grading status values without changing data."
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args(argv)
    payload = _build_payload(args.database)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["safe_to_migrate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
