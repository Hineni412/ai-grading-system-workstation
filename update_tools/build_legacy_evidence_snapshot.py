"""旧会话证据快照迁移工具 — 为无快照的历史考试重建冻结证据快照(设计 E §7.4)。

对每个已建立「考试题 ↔ 题库题」确认关联的场次:
    1. 读题库当前可用证据版本与其知识链接,生成
       user_data/config/uploaded/evidence_snapshot-<session>.json;
    2. 用 match_rubric_parts 精确对位,把 evidence_point_ids 盖进评分依据步骤,
       并在题级记录 evidence_snapshot_ref / source_evidence_version_id /
       graph_release_id;
    3. 对位失败的评分步骤输出人工对照表,由教师在评分依据编辑器手工勾选。

用法(默认 dry-run,只生成报告不写任何文件):
    py update_tools/build_legacy_evidence_snapshot.py --session-id 2 3 4
确认报告无误后加 --apply 才真正写入(属于改写已有真实数据,须逐次授权)。

注意:
    - --apply 会把评分依据 JSON 与快照文件写入 user_data/config/uploaded/;
      写入后该场次的配置 revision 会随之变化,未完成的同步任务需重新提交。
    - 不调用模型、不产生费用;不动评分结果与题库数据。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from path_manager import resolve_stored_file_path  # noqa: E402
from question_bank.database.schema import connect  # noqa: E402
from question_bank.solution_evidence.evidence_snapshot import (  # noqa: E402
    annotate_rubric_payload,
    build_session_snapshot,
    question_point_ids,
    snapshot_file_name,
    write_snapshot,
)


def _session_row(grading_db: Path, session_id: int) -> dict | None:
    import sqlite3

    conn = sqlite3.connect(
        f"file:{Path(grading_db).resolve()}?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM grading_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _confirmed_links(bank_db: Path, session_id: int) -> dict[str, int]:
    with connect(Path(bank_db)) as conn:
        rows = conn.execute(
            """
            SELECT source_question_id, bank_question_id
            FROM grading_question_links
            WHERE grading_session_id = ? AND status = 'confirmed'
            """,
            (str(session_id),),
        ).fetchall()
    return {
        str(row["source_question_id"]): int(row["bank_question_id"])
        for row in rows
    }


def _apply_step_overrides(
    candidate: dict,
    snapshot: dict,
    overrides: dict,
    report: dict,
) -> None:
    """Apply manual step→point mappings after exact matching.

    ``overrides`` shape: ``{question_ref: {step_locator: [evidence_point_ids]}}``
    where step_locator is ``<part_id>/<step_id>`` or a bare ``step_id`` unique
    within the question. Every listed point id must belong to the question's
    frozen snapshot points; violations are recorded in ``report`` and the step
    is left untouched.
    """
    snap_questions = snapshot.get("questions") or {}
    for index, question in enumerate(candidate.get("questions") or [], start=1):
        if not isinstance(question, dict):
            continue
        ref = str(
            question.get("question_id")
            or question.get("id")
            or question.get("number")
            or f"Q{index}"
        ).strip()
        per_question = overrides.get(ref)
        if not per_question:
            continue
        snap_question = snap_questions.get(ref)
        if not isinstance(snap_question, dict) or not snap_question.get("usable"):
            report.setdefault("override_errors", []).append(
                f"{ref}: no usable snapshot"
            )
            continue
        valid_points = set(question_point_ids(snap_question))
        parts = [
            part for part in (question.get("parts") or [question])
            if isinstance(part, dict)
        ]
        steps_by_locator: dict[str, dict] = {}
        for part in parts:
            part_id = str(part.get("part_id") or ref)
            for step in part.get("steps") or ():
                if not isinstance(step, dict):
                    continue
                step_id = str(step.get("step_id") or "")
                steps_by_locator.setdefault(f"{part_id}/{step_id}", step)
                steps_by_locator.setdefault(step_id, step)
        for locator, point_ids in per_question.items():
            step = steps_by_locator.get(str(locator))
            if step is None:
                report.setdefault("override_errors", []).append(
                    f"{ref}: step {locator} not found"
                )
                continue
            ids = [str(pid) for pid in point_ids]
            unknown = [pid for pid in ids if pid not in valid_points]
            if unknown:
                report.setdefault("override_errors", []).append(
                    f"{ref}: {locator} unknown points {unknown}"
                )
                continue
            step["evidence_point_ids"] = ids


def plan_session(
    bank_db: Path,
    session: dict,
    *,
    data_root: Path,
    overrides: dict | None = None,
) -> dict:
    """Read-only plan: snapshot payload + per-question match report."""
    session_id = int(session["id"])
    source_to_bank = _confirmed_links(bank_db, session_id)
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"), data_root=data_root
    )
    rubric: dict = {"questions": []}
    if rubric_path.is_file():
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
        rubric = (
            payload.get("rubric")
            if isinstance(payload.get("rubric"), dict)
            else payload
        )
    snapshot = build_session_snapshot(
        Path(bank_db), source_to_bank=source_to_bank, data_root=data_root
    )
    report = {
        "session_id": session_id,
        "rubric_path": str(rubric_path),
        "linked_questions": len(source_to_bank),
        "snapshot_questions": len(snapshot["questions"]),
        "questions": [],
    }
    if not isinstance(rubric.get("questions"), list):
        report["error"] = "评分依据缺少 questions 列表"
        return report
    candidate = copy.deepcopy(rubric)
    annotate_rubric_payload(candidate, snapshot, session_id)
    if overrides:
        _apply_step_overrides(
            candidate, snapshot, overrides.get(str(session_id), {}), report
        )
    snap_questions = snapshot.get("questions") or {}
    for index, question in enumerate(rubric.get("questions") or [], start=1):
        if not isinstance(question, dict):
            continue
        ref = str(
            question.get("question_id")
            or question.get("id")
            or question.get("number")
            or f"Q{index}"
        ).strip()
        snap_question = snap_questions.get(ref)
        entry = {
            "question_ref": ref,
            "usable": bool(
                isinstance(snap_question, dict) and snap_question.get("usable")
            ),
            "unmatched_steps": [],
            "frozen_points": 0,
        }
        if isinstance(snap_question, dict) and snap_question.get("usable"):
            entry["frozen_points"] = len(question_point_ids(snap_question))
            cand_question = candidate["questions"][index - 1]
            covered = {
                str(pid)
                for part in cand_question.get("parts") or [cand_question]
                if isinstance(part, dict)
                for step in part.get("steps") or ()
                if isinstance(step, dict)
                for pid in step.get("evidence_point_ids") or ()
            }
            entry["covered_points"] = len(covered & set(question_point_ids(snap_question)))
            entry["unmatched_steps"] = [
                str(step.get("step_id") or "")
                for part in cand_question.get("parts") or [cand_question]
                if isinstance(part, dict)
                for step in part.get("steps") or ()
                if isinstance(step, dict)
                and not step.get("evidence_point_ids")
            ]
        report["questions"].append(entry)
    return report, snapshot, candidate, rubric_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", type=int, nargs="+", required=True)
    parser.add_argument(
        "--grading-db",
        type=Path,
        default=_PROJECT_ROOT / "user_data" / "databases" / "grading_system.db",
    )
    parser.add_argument(
        "--question-bank-db",
        type=Path,
        default=_PROJECT_ROOT / "user_data" / "databases" / "question_bank.db",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写入快照与评分依据(改写已有真实数据,须授权)",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
        help="人工步骤对位表 JSON：{session_id: {question_ref: {step: [point_ids]}}}",
    )
    args = parser.parse_args()
    data_root = Path(args.grading_db).parent.parent
    overrides = (
        json.loads(args.overrides.read_text(encoding="utf-8"))
        if args.overrides
        else {}
    )

    reports = []
    for session_id in args.session_id:
        session = _session_row(args.grading_db, session_id)
        if session is None:
            reports.append({"session_id": session_id, "error": "场次不存在"})
            continue
        report, snapshot, candidate, rubric_path = plan_session(
            Path(args.question_bank_db),
            session,
            data_root=data_root,
            overrides=overrides,
        )
        reports.append(report)
        if args.apply and not report.get("error"):
            upload_dir = data_root / "config" / "uploaded"
            write_snapshot(upload_dir, session_id, snapshot)
            rubric_path.write_text(
                json.dumps(candidate, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            report["applied"] = True
            report["snapshot_file"] = snapshot_file_name(session_id)
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
