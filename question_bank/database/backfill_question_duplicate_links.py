"""Preview historical exact duplicates; --apply writes reviewed logical links.

Existing question ids and their answers, evidence and exam references are kept.
The default is a read-only audit. Similarity is never used for consolidation.
"""
from __future__ import annotations

import argparse
import sqlite3
import json
from collections import defaultdict
from pathlib import Path
from question_bank.models.question import normalize_identity_text
from question_bank.services.duplicate_analysis_copy_service import (
    exact_identity_map, canonical_question_ranks,
)


def backfill_duplicate_links(db_path: Path, *, apply: bool = False,
                             data_root: Path | None = None) -> dict:
    """Audit first; explicit apply repairs links atomically without deleting rows."""
    db_path = Path(db_path).resolve()
    root = data_root or db_path.parent.parent
    connection = sqlite3.connect(str(db_path) if apply else db_path.as_uri() + "?mode=ro", uri=not apply)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
        identities = exact_identity_map(connection, data_root=root, persist=apply)
        ranks = canonical_question_ranks(connection, list(identities))
        rows = {int(row["id"]): dict(row) for row in connection.execute(
            "SELECT id,paper_id,question_number,answer_text,difficulty FROM questions")}
        groups = defaultdict(list)
        for qid, key in identities.items():
            if key:
                groups[key].append(qid)
        old = {int(row["question_id"]): dict(row) for row in connection.execute(
            "SELECT question_id,duplicate_of_question_id,match_kind,signature FROM question_duplicate_links")}
        invalid = [qid for qid, link in old.items() if
            not identities.get(qid) or identities.get(qid) != identities.get(int(link["duplicate_of_question_id"]))
            or qid == int(link["duplicate_of_question_id"]) or link["match_kind"] != "exact"]
        desired = {}
        report_groups = []
        for key, members in groups.items():
            if len(members) < 2:
                continue
            canonical = min(members, key=lambda qid: ranks[qid])
            answers = {normalize_identity_text(rows[qid]["answer_text"]) for qid in members if rows[qid]["answer_text"]}
            difficulties = {str(rows[qid]["difficulty"]) for qid in members if rows[qid]["difficulty"] is not None}
            conflicts = []
            if len(answers) > 1:
                conflicts.append("answer_difference")
            if len(difficulties) > 1:
                conflicts.append("difficulty_difference")
            report_groups.append({"question_ids": sorted(members), "canonical_id": canonical,
                                  "conflicts": conflicts, "action": "review" if conflicts else "link"})
            if not conflicts:
                for qid in members:
                    if qid != canonical:
                        desired[qid] = (canonical, key)
        changes = {qid: target for qid, target in desired.items() if
            qid not in old or int(old[qid]["duplicate_of_question_id"]) != target[0]
            or old[qid]["signature"] != target[1] or old[qid]["match_kind"] != "exact"}
        # A representative must not itself point to an alias, even in a legacy cycle.
        canonical_ids = {canonical for canonical, _ in desired.values()}
        removals = sorted(set(invalid) | (canonical_ids & old.keys()))
        occurrences = 0
        if apply:
            for qid in removals:
                connection.execute("DELETE FROM question_duplicate_links WHERE question_id=?", (qid,))
            for qid, (canonical, key) in changes.items():
                connection.execute("""INSERT INTO question_duplicate_links
                    (question_id,duplicate_of_question_id,match_kind,signature)
                    VALUES (?,?,'exact',?) ON CONFLICT(question_id) DO UPDATE SET
                    duplicate_of_question_id=excluded.duplicate_of_question_id,
                    match_kind='exact',signature=excluded.signature""", (qid,canonical,key))
            # Historical rows already carry the paper/number occurrence. Keep
            # those stable ids; inserting another occurrence would display the
            # same question twice in a paper. New imports use the occurrence
            # table directly through the existing import path.
            connection.commit()
        return {"applied": apply, "questions": len(identities), "groups": len(report_groups),
                "incomplete_questions": sum(not key for key in identities.values()),
                "review_groups": sum(bool(group["conflicts"]) for group in report_groups),
                "invalid_links": len(invalid), "links_to_remove": len(removals),
                "links_to_write": len(changes), "inserted": len(changes) if apply else 0,
                "skipped": len(desired)-len(changes), "occurrences": occurrences,
                "group_details": report_groups}
    except BaseException:
        if apply:
            connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=Path("user_data/databases/question_bank.db"))
    parser.add_argument("--apply", action="store_true", help="apply the audited logical links; requires data-operation authorization")
    args = parser.parse_args(argv)
    if not args.db_path.is_file():
        parser.error("database not found")
    stats = backfill_duplicate_links(args.db_path, apply=args.apply)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
