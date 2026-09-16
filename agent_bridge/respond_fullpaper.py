# -*- coding: utf-8 -*-
"""Auto-responder for the FULL-PAPER grading run in D:\\week3_fullpaper_sandbox.

Each full-paper request dir contains the whole-paper images (atlas_0=front,
atlas_1=back) and a user prompt carrying ``已识别学生姓名: <name>``.  For every
pending dir we look up that student's per-question decisions recorded in the
hybrid-rerun sandbox DB (my latest visual judgments, already conforming to the
new step_assessments contract) and emit a full-paper response JSON that
satisfies ``AIGrader._validate_and_convert``.

Scoring semantics preserved:
* objective items -> ``observed_answer`` is emitted; the pipeline re-scores it
  against question-level accepted_forms (fixing the Q8 equivalence gap).
* uncertain items -> needs_human_review + low confidence + candidate_scores.
* blank / discarded -> 0 score, 未作答/作废答案 category, no review needed.
* subjective items -> step_assessments / evidence / flags copied verbatim.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

BRIDGE_DIR = Path(r"D:\week3_fullpaper_sandbox\bridge\session_4")
REQUESTS = BRIDGE_DIR / "requests"
EVENTS = BRIDGE_DIR / "replay_events.jsonl"
DONE_MARK = BRIDGE_DIR / "RUN_FINISHED"
DECISION_DB = r"D:\week3_regrade_sandbox\data\databases\grading_system.db"

OBJECTIVE_QIDS = [f"Q{i}" for i in range(1, 12)]
SUBJECTIVE_QIDS = ["Q12(P1)", "Q12(P2)", "Q13"]
ALL_QIDS = OBJECTIVE_QIDS + SUBJECTIVE_QIDS
MAX_SCORES = {
    **{q: 6 for q in OBJECTIVE_QIDS},
    "Q12(P1)": 9, "Q12(P2)": 9, "Q13": 16,
}

_NAME_RE = re.compile(r"已识别学生姓名[:：]\s*([^\\\s]+)")


def _log(event: dict) -> None:
    event.setdefault("t", time.strftime("%H:%M:%S"))
    with EVENTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_decisions() -> dict[int, dict[str, dict]]:
    """student_id -> {qid: {score, deduction, meta}}"""
    conn = sqlite3.connect(DECISION_DB)
    conn.row_factory = sqlite3.Row
    out: dict[int, dict[str, dict]] = {}
    rows = conn.execute(
        """select r.student_id, r.raw_json, d.question_id, d.score_awarded,
                  d.deduction_reason, d.error_category, d.error_summary,
                  d.confidence_score
           from session_details d join session_results r on d.result_id=r.id
           where r.session_id=4"""
    ).fetchall()
    raw_by_sid = {}
    for r in conn.execute("select student_id, raw_json from session_results where session_id=4"):
        try:
            raw_by_sid[r["student_id"]] = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw_by_sid[r["student_id"]] = {}
    for row in rows:
        sid = row["student_id"]
        qid = row["question_id"]
        meta = (raw_by_sid.get(sid, {}).get("detail_metadata") or {}).get(qid) or {}
        out.setdefault(sid, {})[qid] = {
            "score": float(row["score_awarded"] or 0),
            "deduction": row["deduction_reason"] or "",
            "error_category": row["error_category"],
            "error_summary": row["error_summary"],
            "confidence": row["confidence_score"],
            "meta": meta,
        }
    conn.close()
    return out


def _objective_item(qid: str, rec: dict) -> dict:
    meta = rec["meta"]
    state = str(meta.get("answer_state") or "clear")
    need_review = bool(meta.get("need_review"))
    conf = float(meta.get("confidence") or 0.0) * 100.0
    obs = meta.get("normalized_answer") or meta.get("recognized_answer") or ""
    maxs = MAX_SCORES[qid]
    score = float(rec["score"] or 0)

    if state == "blank":
        obs, cat, summ, ded, need_review = "", "未作答", "未作答", "未作答", False
        conf = max(conf, 90)
        score = 0.0
    elif state == "discarded_only":
        obs, cat, summ = "", "作废答案", "有效答案仅出现在涂抹/划掉区域"
        ded = rec["deduction"] or "涂抹/划掉内容不采信"
        need_review = False
        conf = max(conf, 85)
        score = 0.0
    elif need_review:
        cat, summ = "需复核", meta.get("review_reason") or "作答存疑需教师确认"
        ded = rec["deduction"] or ""
    elif score >= maxs - 1e-6:
        cat = summ = ded = None
    else:
        cat = "答案不等价" if qid in {"Q8", "Q9", "Q10", "Q11"} else "其他"
        summ = rec["deduction"] or "与标准答案不符"
        ded = rec["deduction"] or summ

    item = {
        "question_id": qid,
        "score_awarded": score,
        "deduction_reason": ded,
        "confidence_score": round(min(max(conf, 5.0), 99.0), 1),
        "error_category": cat,
        "error_summary": summ,
        "secondary_errors": [],
        "observed_answer": obs,
        "needs_human_review": need_review,
        "candidate_scores": [
            {"score": score, "confidence": round(conf / 100.0, 2),
             "reason": summ or "按识别结果判分"},
        ],
    }
    if need_review:
        alt = maxs if score == 0 else 0
        item["candidate_scores"].append(
            {"score": alt, "confidence": round(1 - conf / 100.0, 2),
             "reason": "复核备选分"}
        )
    if state == "discarded_only":
        item["smudged_or_crossed_out"] = True
        item["answer_discarded_by_smudge"] = True
    if state == "blank":
        item["answer_is_blank_or_no_valid_work"] = True
    return item


def _subjective_item(qid: str, rec: dict) -> dict:
    meta = rec["meta"]
    score = float(rec["score"] or 0)
    maxs = MAX_SCORES[qid]
    need_review = bool(meta.get("needs_human_review"))
    conf = rec["confidence"]
    try:
        conf = float(conf) if conf is not None else 90.0
    except (TypeError, ValueError):
        conf = 90.0
    cat = rec["error_category"]
    summ = rec["error_summary"]
    if score >= maxs - 1e-6 and not need_review:
        cat = summ = None
    item = {
        "question_id": qid,
        "score_awarded": score,
        "deduction_reason": rec["deduction"],
        "confidence_score": round(min(max(conf, 5.0), 99.0), 1),
        "error_category": cat,
        "error_summary": summ,
        "secondary_errors": [],
        "observed_answer": meta.get("observed_answer") or "",
        "evidence_steps": meta.get("evidence_steps") or [],
        "missing_steps": meta.get("missing_steps") or [],
        "step_assessments": meta.get("step_assessments") or [],
        "candidate_scores": meta.get("candidate_scores") or [
            {"score": score, "confidence": round(conf / 100.0, 2), "reason": "按步骤给分"}
        ],
        "answer_only_correct": meta.get("answer_only_correct"),
        "alternative_solution_detected": bool(meta.get("alternative_solution_detected")),
        "alternative_solution_summary": meta.get("alternative_solution_summary"),
        "answer_is_blank_or_no_valid_work": bool(meta.get("answer_is_blank_or_no_valid_work")),
        "answer_discarded_by_smudge": bool(meta.get("answer_discarded_by_smudge")),
        "smudged_or_crossed_out": bool(meta.get("smudged_or_crossed_out")),
        "needs_human_review": need_review,
    }
    return item


def build_response(name: str, sid: int, decisions: dict[int, dict[str, dict]]) -> dict:
    per = decisions.get(sid) or {}
    details = []
    any_review = False
    for qid in ALL_QIDS:
        rec = per.get(qid)
        if rec is None:
            # should not happen; emit a safe review-flagged zero
            rec = {"score": 0.0, "deduction": "", "error_category": "需复核",
                   "error_summary": "无判定记录", "confidence": 5, "meta": {}}
        if qid in OBJECTIVE_QIDS:
            item = _objective_item(qid, rec)
        else:
            item = _subjective_item(qid, rec)
        if item.get("needs_human_review") or (item.get("confidence_score") or 100) < 80:
            any_review = True
        details.append(item)
    return {
        "student_name": name,
        "total_score": 100,
        "student_score": sum(d["score_awarded"] for d in details),
        "needs_human_review": any_review,
        "grading_details": details,
    }


def main() -> int:
    index_path = BRIDGE_DIR / "papers_index.json"
    while not index_path.exists():
        time.sleep(2)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    name_to_sid = {}
    for _pk, info in index.items():
        name_to_sid.setdefault(info["student_name"], info["student_id"])
    decisions = load_decisions()
    _log({"event": "responder_ready", "students": len(name_to_sid)})

    served: set[str] = set()
    while True:
        if (BRIDGE_DIR / "CANCEL").exists():
            _log({"event": "cancelled"})
            return 1
        pending = []
        for d in sorted(REQUESTS.iterdir()):
            if not d.is_dir() or d.name in served:
                continue
            if (d / "served.json").exists() or (d / "response.json").exists():
                served.add(d.name)
                continue
            pending.append(d)
        for d in pending:
            try:
                prompt = (d / "prompt_static.txt").read_text(encoding="utf-8", errors="replace")
                m = _NAME_RE.search(prompt)
                if not m:
                    _log({"event": "no_name", "dir": d.name})
                    continue
                name = m.group(1)
                sid = name_to_sid.get(name)
                if sid is None:
                    _log({"event": "unknown_name", "dir": d.name, "name": name})
                    continue
                resp = build_response(name, sid, decisions)
                (d / "response.json").write_text(
                    json.dumps(resp, ensure_ascii=False), encoding="utf-8")
                served.add(d.name)
                _log({"event": "served", "dir": d.name, "student": name,
                      "score": resp["student_score"], "review": resp["needs_human_review"]})
            except Exception as exc:  # noqa: BLE001
                _log({"event": "error", "dir": d.name, "error": str(exc)})
        if DONE_MARK.exists() and not pending:
            _log({"event": "done", "served": len(served)})
            return 0
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
