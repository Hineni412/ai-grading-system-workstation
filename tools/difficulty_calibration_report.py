#!/usr/bin/env python3
"""难度校准报表（只读，redesign §9.1）。

按题库题输出：估计难度、参与班级数、观测得分率、标准化残差。
|z| > 1.5 的题列为复核候选；本脚本永不写库、不改难度。

用法::

    python tools/difficulty_calibration_report.py \
        --grading-db user_data/databases/grading_system.db \
        --question-bank-db user_data/databases/question_bank.db \
        --data-root user_data --format json
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

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


def _item_max_scores(rubric: Mapping[str, Any]) -> dict[str, float]:
    """item_ref -> max score, mirroring db_manager._load_rubric_maps_for_session."""
    from backend.config_generation.contract import iter_effective_rubric_item_refs

    scores: dict[str, float] = {}
    for item_ref, _parent, question, item in iter_effective_rubric_item_refs(rubric):
        raw = item.get("part_score")
        if raw is None:
            raw = item.get("max_score") or question.get("max_score")
        try:
            scores[item_ref] = float(raw or 0.0)
        except (TypeError, ValueError):
            continue
    return scores


def collect_rows(
    grading_db: Path,
    question_bank_db: Path,
    data_root: Path,
    warnings: list[str] | None = None,
) -> dict[int, dict[str, Any]]:
    """bank_question_id -> {rates, classes, sessions, observations}."""
    from integration.question_tag_projection_service import (
        QuestionTagProjectionService,
    )
    from path_manager import resolve_stored_file_path

    grading = _connect_ro(Path(grading_db))
    sessions = grading.execute(
        "SELECT id, rubric_path FROM grading_sessions"
        " WHERE COALESCE(is_deleted, 0) = 0"
    ).fetchall()
    projector = QuestionTagProjectionService(
        question_bank_db, data_root=data_root,
        external_connection=_connect_ro(Path(question_bank_db)),
    )
    # bank question id per (session_id, item_ref)
    item_bank: dict[tuple[int, str], int] = {}
    item_full: dict[tuple[int, str], float] = {}
    for session in sessions:
        session_id = int(session["id"])
        stored = session["rubric_path"]
        try:
            rubric_path = resolve_stored_file_path(stored, data_root=data_root)
            rubric = (
                json.loads(rubric_path.read_text(encoding="utf-8"))
                if rubric_path.exists()
                else {}
            )
        except (OSError, ValueError):
            rubric = {}
        for item_ref, score in _item_max_scores(rubric).items():
            item_full[(session_id, item_ref)] = score
        try:
            projection = projector.project_session(
                grading_session_id=session_id, rubric=rubric
            )
        except Exception as exc:  # noqa: BLE001 - report stays alive on a bad session
            if warnings is not None:
                warnings.append(
                    f"session {session_id} 投影失败已跳过: {type(exc).__name__}: {exc}"
                )
            continue
        for item in projection.items:
            if item.bank_question_id is not None:
                item_bank[(session_id, str(item.item_ref))] = int(
                    item.bank_question_id
                )

    rows = grading.execute(
        """
        SELECT sr.session_id, sd.question_id, sd.score_awarded,
               s.class_name
        FROM session_details sd
        JOIN session_results sr ON sr.id = sd.result_id
        JOIN grading_sessions gs ON gs.id = sr.session_id
        JOIN students s ON s.id = sr.student_id
        WHERE COALESCE(gs.is_deleted, 0) = 0
        """
    ).fetchall()
    out: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"rates": [], "classes": set(), "sessions": set()}
    )
    for row in rows:
        key = (int(row["session_id"]), str(row["question_id"]))
        bank_qid = item_bank.get(key)
        if bank_qid is None:
            continue
        full = item_full.get(key) or 0.0
        awarded = float(row["score_awarded"] or 0.0)
        if full <= 0:
            continue
        rate = min(max(awarded, 0.0), full) / full
        bucket = out[bank_qid]
        bucket["rates"].append(rate)
        if row["class_name"]:
            bucket["classes"].add(str(row["class_name"]))
        bucket["sessions"].add(key[0])
    projector.external_connection.close()
    grading.close()
    return out


def expected_rate(difficulty: float) -> float:
    """难度 1-10 线性映射到期望得分率（1→1.0，10→0.1）。仅用于残差基准。"""
    return max(0.05, min(1.0, (11.0 - difficulty) / 10.0))


def build_report(
    grading_db: Path,
    question_bank_db: Path,
    data_root: Path,
    *,
    min_observations: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    observed = collect_rows(grading_db, question_bank_db, data_root, warnings)
    bank = _connect_ro(Path(question_bank_db))
    difficulties = {
        int(row["id"]): row["difficulty"]
        for row in bank.execute(
            "SELECT id, difficulty FROM questions"
            " WHERE COALESCE(is_deleted, 0) = 0"
        )
    }
    report: list[dict[str, Any]] = []
    for qid, bucket in sorted(observed.items()):
        rates = bucket["rates"]
        n = len(rates)
        if n < min_observations:
            continue
        raw_difficulty = difficulties.get(qid)
        try:
            difficulty = float(raw_difficulty)
        except (TypeError, ValueError):
            difficulty = None
        if difficulty is None or not 1 <= difficulty <= 10:
            continue
        mean = sum(rates) / n
        expected = expected_rate(difficulty)
        sd = math.sqrt(max(expected * (1.0 - expected), 1e-4) / n)
        z = (mean - expected) / sd
        report.append(
            {
                "question_id": qid,
                "difficulty": difficulty,
                "expected_score_rate": round(expected, 4),
                "observed_score_rate": round(mean, 4),
                "class_count": len(bucket["classes"]),
                "session_count": len(bucket["sessions"]),
                "observations": n,
                "residual": round(mean - expected, 4),
                "z": round(z, 3),
                "review_candidate": bool(abs(z) > 1.5),
            }
        )
    return report, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grading-db", type=Path, required=True)
    parser.add_argument("--question-bank-db", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("user_data"))
    parser.add_argument("--min-observations", type=int, default=5)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    report, warnings = build_report(
        args.grading_db,
        args.question_bank_db,
        args.data_root,
        min_observations=args.min_observations,
    )
    if args.format == "csv":
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(report[0]) if report else
                                ["question_id", "difficulty", "expected_score_rate",
                                 "observed_score_rate", "class_count",
                                 "session_count", "observations", "residual",
                                 "z", "review_candidate"])
        writer.writeheader()
        writer.writerows(report)
        text = buffer.getvalue()
    else:
        text = json.dumps(
            {"questions": report,
             "review_candidates": sum(1 for r in report if r["review_candidate"]),
             "warnings": warnings},
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
