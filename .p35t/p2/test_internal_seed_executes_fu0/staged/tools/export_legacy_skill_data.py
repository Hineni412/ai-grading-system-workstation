"""Export retired skill-semantics tables without modifying the source database."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacy_table_archive import LegacyArchiveError, export_table_archive


ARCHIVE_FORMAT = "ai-grading-legacy-skill-export"
LEGACY_TABLES = (
    "knowledge_concepts",
    "knowledge_relations",
    "knowledge_source_mappings",
    "skill_topics",
    "skills",
    "assessment_item_skills",
    "question_skill_links",
    "skill_resolution_conflicts",
    "skill_neighbors",
    "skill_system_settings",
    "skill_migration_runs",
)


def export_legacy_skill_data(
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
        allow_missing_tables=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export retired legacy skill-semantics tables.",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = export_legacy_skill_data(
            args.database,
            args.output,
            overwrite=bool(args.overwrite),
        )
    except (LegacyArchiveError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"legacy skill export failed: {exc}\n")

    print(
        "legacy skill export complete: "
        f"tables={len(summary)}, rows={sum(summary.values())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
