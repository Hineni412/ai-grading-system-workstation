from __future__ import annotations

import argparse
import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from question_bank.services.source_paper_migration_service import (  # noqa: E402
    MigrationReport,
    apply_source_paper_migration,
    plan_source_paper_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archive question-bank source papers under user_data and migrate stored paths."
    )
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--db", help="Question-bank database path; relative paths use --root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect without writing")
    mode.add_argument("--apply", action="store_true", help="Archive files and update the database")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    data_root = root / "user_data"
    if args.db:
        supplied_db = Path(args.db).expanduser()
        db_path = supplied_db.resolve() if supplied_db.is_absolute() else (root / supplied_db).resolve()
    else:
        db_path = data_root / "databases" / "question_bank.db"

    apply = bool(args.apply)
    report = (
        apply_source_paper_migration(db_path=db_path, data_root=data_root)
        if apply
        else plan_source_paper_migration(db_path=db_path, data_root=data_root)
    )
    _print_report(report, mode="apply" if apply else "dry-run")
    return 1 if report.failed_sources else 0


def _print_report(report: MigrationReport, *, mode: str) -> None:
    print(f"mode={mode}")
    print(f"scanned_sources={report.scanned_sources}")
    print(f"migrated_sources={report.migrated_sources}")
    print(f"reused_archives={report.reused_archives}")
    print(f"missing_sources={report.missing_sources}")
    print(f"failed_sources={report.failed_sources}")
    print(f"updated_rows={report.updated_rows}")
    if report.backup_path is not None:
        print(f"backup_path={report.backup_path}")
    for message in report.messages:
        print(message)


if __name__ == "__main__":
    raise SystemExit(main())
