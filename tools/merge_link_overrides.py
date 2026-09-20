"""Merge per-point link upgrades into decisions for the v5 link run.

Input: a JSON file shaped {"<batch>": {"<qid>": {"<pid>": ["term", ...]}}}.
Each entry REPLACES the seeded decision for that point. Entries not listed
keep their seeded (carry-forward or section-fallback) links.
Writes the merged result back into decisions/seed_<batch>.json files and a
cumulative overrides log for audit.

Usage:
    runtime\\python\\python.exe tools/merge_link_overrides.py --run RUN_DIR --upgrades FILE
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--upgrades", required=True, type=Path)
    args = parser.parse_args()

    upgrades = json.loads(args.upgrades.read_text(encoding="utf-8"))
    log_path = args.run / "decisions" / "_overrides_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}

    seed_files = {
        path.stem[len("seed_"):]: path
        for path in (args.run / "decisions").glob("seed_*.json")
    }

    def resolve(batch: str) -> str:
        if batch in seed_files:
            return batch
        matches = [key for key in seed_files if key.startswith(batch)]
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"batch prefix {batch!r} matches {matches}")

    changed = 0
    for batch, questions in upgrades.items():
        batch = resolve(batch)
        seed_path = seed_files[batch]
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        for qid, points in questions.items():
            for pid, terms in points.items():
                seed[batch][qid][pid] = terms
                changed += 1
        seed_path.write_text(
            json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        log.setdefault(batch, {}).update(questions)
    log_path.write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"upgraded {changed} point decisions across {len(upgrades)} batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
