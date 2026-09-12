"""One-off backfill: link historically imported exact-duplicate questions.

Groups every active question by full text, formulas, options and actual image content, keeps the smallest id as each group's representative, and inserts
``question_duplicate_links`` rows for the remaining members.  Existing links
are never overwritten (``question_id`` is the primary key and ``INSERT OR
IGNORE`` keeps the original target).  Tags are not copied by this script.

Run from the project root:

    runtime/python/python.exe -m question_bank.database.backfill_question_duplicate_links
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from question_bank.database.schema import connect
from question_bank.services.duplicate_analysis_copy_service import exact_identity_map

_DEFAULT_DB_PATH = Path("user_data") / "databases" / "question_bank.db"


def backfill_duplicate_links(db_path: Path) -> dict[str, int]:
    """Backfill exact-duplicate links; returns run statistics."""
    with connect(db_path) as conn:
        identities = exact_identity_map(conn, data_root=db_path.parent.parent)
        groups: dict[str, list[int]] = defaultdict(list)
        for question_id, key in identities.items():
            if key:
                groups[key].append(question_id)
        duplicate_groups = {
            key: ids for key, ids in groups.items() if len(ids) > 1
        }
        existing = {
            int(row["question_id"])
            for row in conn.execute(
                "SELECT question_id FROM question_duplicate_links"
            ).fetchall()
        }
        inserted = 0
        skipped = 0
        for key, ids in duplicate_groups.items():
            representative = min(ids)
            for question_id in ids:
                if question_id == representative:
                    continue
                if question_id in existing:
                    # 已有关联保留原指向，不覆盖。
                    skipped += 1
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO question_duplicate_links (
                        question_id, duplicate_of_question_id,
                        match_kind, signature
                    ) VALUES (?, ?, 'exact', ?)
                    """,
                    (question_id, representative, key),
                )
                inserted += cursor.rowcount
    return {
        "groups": len(duplicate_groups),
        "inserted": inserted,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_DEFAULT_DB_PATH,
        help="question bank database path (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    db_path = args.db_path.resolve()
    if not db_path.is_file():
        parser.error(f"database not found: {db_path}")
    stats = backfill_duplicate_links(db_path)
    print(
        "duplicate groups: {groups}, links inserted: {inserted}, "
        "members skipped (already linked): {skipped}".format(**stats)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
