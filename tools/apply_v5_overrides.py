"""Apply per-point override files to clean dec_batch_* decisions.

Usage: python tools/apply_v5_overrides.py --run RUN_DIR --file OVERRIDE_JSON

Override format: {"<batch-prefix>": {"<qid>": {"<pid>": ["term", "term:sup"]}}}
Each entry REPLACES the seeded decision for that point, but only when the
current seed is section-only (kp_*) or empty — overrides never clobber an
existing skill decision. Validates pid existence and candidate membership.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--force", action="store_true",
                    help="replace even when the seed already has sk_ links")
    args = ap.parse_args()
    run = args.run

    upgrades = json.loads(args.file.read_text(encoding="utf-8"))
    reqs = {
        p.stem[len("batch_"):]: json.loads(p.read_text(encoding="utf-8"))
        for p in (run / "requests").glob("batch_*.json")
    }

    def resolve(b: str) -> str | None:
        if b in reqs:
            return b
        m = [k for k in reqs if k.startswith(b)]
        return m[0] if len(m) == 1 else None

    issues: list[tuple] = []
    applied = skipped = 0
    for b, questions in upgrades.items():
        bh = resolve(b)
        if not bh:
            issues.append(("batch?", b))
            continue
        req = reqs[bh]
        qmeta = {str(q["question_id"]): q for q in req["questions"]}
        decf = run / "decisions" / f"dec_batch_{bh}.json"
        dec = json.loads(decf.read_text(encoding="utf-8"))[bh]
        for qid, points in questions.items():
            q = qmeta.get(str(qid))
            if not q:
                issues.append(("qid?", b, qid))
                continue
            allowed = {str(c["id"]) for c in q["candidates"]}
            valid_pids = {
                p["evidence_point_id"] for part in q["parts"] for p in part["points"]
            }
            for pid, terms in points.items():
                if pid not in valid_pids:
                    issues.append(("pid?", b, qid, pid))
                    continue
                bad = [t.split(":")[0] for t in terms if t.split(":")[0] not in allowed]
                if bad:
                    issues.append(("term?", b, qid, pid, bad))
                    continue
                cur = dec.get(str(qid), {}).get(pid, [])
                if cur and not args.force and not all(
                    t.split(":")[0].startswith("kp_") for t in cur
                ):
                    skipped += 1
                    issues.append(("nontarget", b, qid, pid, cur))
                    continue
                dec.setdefault(str(qid), {})[pid] = terms
                applied += 1
        decf.write_text(json.dumps({bh: dec}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"applied: {applied}  skipped: {skipped}  issues: {len(issues)}")
    for i in issues:
        print(" ", i)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
