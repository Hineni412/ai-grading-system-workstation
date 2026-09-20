"""Materialize compact link decisions into response files for run_link_job_offline.

Decisions live in ``decisions/*.json`` as::

    {"<batch_hash>": {"<question_id>": {"<evidence_point_id>": ["term_id", "term_id:sup", ...]}}}

``":sup"`` marks role=supporting_prerequisite; plain entries are role=direct.
For every decided batch the script writes ``responses/batch_<hash>.json`` in the
shape the offline driver expects and reports validation problems (unknown term,
term outside the question's candidates, >3 direct links, missing point ids).

Usage:
    runtime\\python\\python.exe tools/materialize_link_responses.py --run output/link_job/v4_repair_20260918
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_ROLES = {"direct", "supporting_prerequisite"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    run = args.run
    decisions_dir = run / "decisions"
    requests_dir = run / "requests"
    responses_dir = run / "responses"
    responses_dir.mkdir(exist_ok=True)

    decisions: dict[str, dict[str, dict[str, list[str]]]] = {}
    for path in sorted(decisions_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for batch_hash, questions in payload.items():
            decisions.setdefault(batch_hash, {}).update(questions)

    problems: list[str] = []
    written = 0
    for req_path in sorted(requests_dir.glob("batch_*.json")):
        batch_hash = req_path.stem[len("batch_"):]
        decided = decisions.get(batch_hash)
        if decided is None:
            continue
        request = json.loads(req_path.read_text(encoding="utf-8"))
        response: dict[str, list[dict]] = {}
        for question in request["questions"]:
            qid = str(question["question_id"])
            allowed = {str(c["id"]) for c in question["candidates"]}
            q_decisions = decided.get(qid) or {}
            point_meta = {
                str(p["evidence_point_id"]): str(part["part_id"])
                for part in question["parts"]
                for p in part["points"]
            }
            entries = []
            for pid, part_id in point_meta.items():
                raw = q_decisions.get(pid)
                if raw is None:
                    problems.append(f"{batch_hash} q{qid} {pid}: no decision")
                    continue
                links = []
                direct = 0
                for item in raw:
                    term, _, suffix = str(item).partition(":")
                    role = "supporting_prerequisite" if suffix in {"sup", "supporting_prerequisite"} else "direct"
                    if term not in allowed:
                        problems.append(f"{batch_hash} q{qid} {pid}: {term} not in candidates")
                        continue
                    if role == "direct":
                        direct += 1
                    links.append({"fine_term_id": term, "role": role})
                if direct > 3:
                    problems.append(f"{batch_hash} q{qid} {pid}: {direct} direct links")
                if not links:
                    problems.append(f"{batch_hash} q{qid} {pid}: empty links")
                entries.append(
                    {"part_id": part_id, "evidence_point_id": pid, "links": links}
                )
            if len(entries) == len(point_meta):
                response[qid] = entries
        (responses_dir / req_path.name).write_text(
            json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        written += 1

    decided_batches = {b for b in decisions if (requests_dir / f"batch_{b}.json").exists()}
    undecided = [
        p.stem[len("batch_"):]
        for p in requests_dir.glob("batch_*.json")
        if p.stem[len("batch_"):] not in decisions
    ]
    print(f"responses written: {written}; batches undecided: {len(undecided)}")
    for msg in problems:
        print("PROBLEM:", msg)
    print(f"total problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
