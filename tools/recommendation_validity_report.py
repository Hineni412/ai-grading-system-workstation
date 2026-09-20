#!/usr/bin/env python3
"""推荐有效性回看（只读，redesign §9.2）。

对每张已发布的个性化训练卷：取卷面发布时点 draft 中被推荐技能的基线
掌握度作为背景，同时比较发布前后 N 天内同技能的加权达成率。
掌握度与达成率不相减；跨卷后测单列。该描述性回看不证明训练因果效果。

用法::

    python tools/recommendation_validity_report.py \
        --question-bank-db user_data/databases/question_bank.db --days 30
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_report(question_bank_db: Path, *, days: int) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    conn = _connect_ro(Path(question_bank_db))
    instances = conn.execute(
        """
        SELECT paper_instance_id, draft_id, student_id, created_at, status
        FROM personalized_paper_instances
        WHERE status IN ('frozen', 'published', 'finalized')
        ORDER BY created_at
        """
    ).fetchall()
    drafts = {
        str(row["draft_id"]): row["draft_json"]
        for row in conn.execute(
            "SELECT draft_id, draft_json FROM personalized_recommendation_drafts"
        )
    }
    evidence = conn.execute(
        """
        SELECT s.paper_instance_id, e.student_id, e.stable_key,
               e.achieved_points, e.total_points, e.evidence_weight,
               e.occurred_at, e.status
        FROM training_evidence_records e
        JOIN training_submissions s ON s.submission_id = e.submission_id
        """
    ).fetchall()
    evidence_by_student: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in evidence:
        evidence_by_student[str(row["student_id"])].append(row)

    def rate(records: list[sqlite3.Row]) -> float | None:
        usable = [r for r in records if float(r['total_points'] or 0) > 0 and float(r['evidence_weight'] or 0) > 0]
        weight = sum(float(r['evidence_weight']) for r in usable)
        return (sum(float(r['achieved_points']) / float(r['total_points']) * float(r['evidence_weight'])
                    for r in usable) / weight) if weight else None

    rows: list[dict[str, Any]] = []
    paper_summaries: list[dict[str, Any]] = []
    for instance in instances:
        paper_id = str(instance["paper_instance_id"])
        student_id = str(instance["student_id"])
        draft_raw = drafts.get(str(instance["draft_id"]))
        baseline: dict[str, float | None] = {}
        target_names: dict[str, str] = {}
        if draft_raw:
            try:
                draft = json.loads(draft_raw)
            except ValueError:
                draft = {}
            for student in draft.get("students") or []:
                if str(student.get("student_id")) != student_id:
                    continue
                for target in student.get("targets") or []:
                    key = str(target.get("stable_key") or "")
                    if not key:
                        continue
                    value = target.get("value")
                    baseline[key] = (
                        float(value)
                        if isinstance(value, (int, float))
                        else None
                    )
                    target_names[key] = str(target.get("display_name") or "")
        published = _parse_time(instance["created_at"])
        window_end = published + timedelta(days=days) if published else None
        window_start = published - timedelta(days=days) if published else None
        post: dict[str, list[sqlite3.Row]] = defaultdict(list)
        before: dict[str, list[sqlite3.Row]] = defaultdict(list)
        transfer: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for record in evidence_by_student.get(student_id, []):
            if str(record["status"] or "") != 'active':
                continue
            occurred = _parse_time(record["occurred_at"])
            if published is None or occurred is None:
                continue
            key = str(record['stable_key'])
            if window_start <= occurred < published:
                before[key].append(record)
            elif published <= occurred <= window_end:
                post[key].append(record)
                if str(record['paper_instance_id']) != paper_id:
                    transfer[key].append(record)

        deltas: list[float] = []
        for key in sorted(baseline):
            records = post.get(key, [])
            post_rate = rate(records)
            pre_rate = rate(before.get(key, []))
            base = baseline.get(key)
            delta = (
                round(post_rate - pre_rate, 4)
                if post_rate is not None and pre_rate is not None
                else None
            )
            if delta is not None:
                deltas.append(delta)
            rows.append(
                {
                    "paper_instance_id": paper_id,
                    "student_id": student_id,
                    "stable_key": key,
                    "display_name": target_names.get(key, ""),
                    "baseline_mastery": base,
                    "pre_score_rate": pre_rate,
                    "pre_evidence_count": len(before.get(key, [])),
                    "transfer_score_rate": rate(transfer.get(key, [])),
                    "transfer_evidence_count": len(transfer.get(key, [])),
                    "post_score_rate": (
                        round(post_rate, 4) if post_rate is not None else None
                    ),
                    "post_evidence_count": len(records),
                    "delta": delta,
                }
            )
        paper_summaries.append(
            {
                "paper_instance_id": paper_id,
                "student_id": student_id,
                "published_at": str(instance["created_at"]),
                "target_count": len(baseline),
                "keys_with_post_evidence": sum(
                    1 for key in baseline if post.get(key)
                ),
                "mean_delta": (
                    round(sum(deltas) / len(deltas), 4) if deltas else None
                ),
            }
        )
    conn.close()
    return {
        "interpretation": "同技能前后加权达成率的描述性比较；缺少前测时不计算提升，不能据此认定训练的因果效果。",
        "days": days,
        "paper_count": len(paper_summaries),
        "papers": paper_summaries,
        "details": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question-bank-db", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    text = json.dumps(
        build_report(args.question_bank_db, days=args.days),
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
