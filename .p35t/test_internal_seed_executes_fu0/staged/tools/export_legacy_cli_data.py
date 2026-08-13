"""Export the retired legacy CLI tables without modifying the source database."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacy_table_archive import LegacyArchiveError, export_table_archive


ARCHIVE_FORMAT = "ai-grading-legacy-cli-export"
LEGACY_TABLES = ("exam_results", "grading_details")
LegacyExportError = LegacyArchiveError


def export_legacy_cli_data(
    database: Path,
    output: Path,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    return export_table_archive(
        database,
        output,
        tables=LEGACY_TABLES,
        archive_format=ARCHIVE_FORMAT,
        overwrite=overwrite,
    )


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
    except (LegacyArchiveError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"legacy export failed: {exc}\n")

    print(
        "legacy export complete: "
        f"exam_results={summary['exam_results']}, "
        f"grading_details={summary['grading_details']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
