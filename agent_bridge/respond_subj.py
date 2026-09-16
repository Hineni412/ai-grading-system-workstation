# -*- coding: utf-8 -*-
"""Build a subjective batch response.json from a compact grading spec.

usage: respond_subj.py <request_dir> <spec.json>

spec format (keys = tile label prefix like "01", or paper_key):
{
  "01": {
    "Q12(P1)": {
      "s": 9,                       // total score (must equal sum of step scores)
      "obs": "a=8,b=1,c=4",          // observed final answer
      "conf": 95,                    // required, explicit recognition confidence
      "review": false,               // optional
      "err": null,                   // error_category, null when clean
      "sum": null,                   // error_summary
      "ded": "",                     // deduction_reason
      "miss": [],                    // missing step ids (defaults: steps scored 0)
      "blank": false,                // answer_is_blank_or_no_valid_work
      "smudge": false,               // answer_discarded_by_smudge
      "alt": null,                   // alternative solution summary
      "ans_ok": null,                // answer_only_correct
      "steps": {
        "S1": [5, "full", "理由", "学生证据", "缺失或错误说明"],
        ...
      }
    },
    "Q12(P2)": { ... }
  }
}
step entry: [score, achievement, reason, evidence?, missing_or_error?]
"""
import json
import re
import sys
from pathlib import Path


def _step(item, sid):
    arr = item["steps"][sid]
    if isinstance(arr[0], bool) or not isinstance(arr[0], (int, float)) or arr[0] < 0 or not float(arr[0]).is_integer():
        raise ValueError(f"{sid}: score must be a nonnegative integer")
    sc, ach, reason = int(arr[0]), arr[1], arr[2]
    if ach == "missing":
        ach = "none"
    ev = arr[3] if len(arr) > 3 and arr[3] else ""
    if sc > 0 and not str(ev).strip():
        raise ValueError(f"{sid}: positive score requires observed student evidence")
    miss = arr[4] if len(arr) > 4 else ""
    return {
        "step_id": sid,
        "achievement": ach,
        "score_awarded": sc,
        "reason": reason,
        "student_evidence": ev,
        "missing_or_error": miss,
    }


def _detail(qid, spec):
    confidence = spec.get("conf")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        raise ValueError(f"{qid}: explicit confidence in 0..100 is required")
    steps = [_step(spec, sid) for sid in spec["steps"]]
    total = sum(s["score_awarded"] for s in steps)
    if "s" in spec and spec["s"] != total:
        raise ValueError(f"{qid}: total must equal the sum of step scores")
    missing = spec.get("miss")
    if missing is None:
        missing = [s["step_id"] for s in steps if s["achievement"] == "none"]
    err = spec.get("err")
    return {
        "question_id": qid,
        "score_awarded": total,
        "deduction_reason": spec.get("ded") or (spec.get("sum") or "" if err else ""),
        "confidence_score": confidence,
        "needs_human_review": bool(spec.get("review", False)),
        "error_category": err,
        "error_summary": spec.get("sum"),
        "secondary_errors": spec.get("sec") or [],
        "observed_answer": spec.get("obs") or "",
        "evidence_steps": [
            s["step_id"] for s in steps if s["achievement"] != "none"
        ],
        "missing_steps": missing,
        "answer_only_correct": spec.get("ans_ok"),
        "step_assessments": steps,
        "alternative_solution_detected": bool(spec.get("alt")),
        "alternative_solution_summary": spec.get("alt"),
        "candidate_scores": [
            {
                "score": total,
                "confidence": confidence / 100.0,
                "reason": spec.get("sum") or "按步骤给分",
            }
        ],
        "answer_is_blank_or_no_valid_work": bool(spec.get("blank")),
        "answer_discarded_by_smudge": bool(spec.get("smudge")),
        "smudged_or_crossed_out": bool(spec.get("crossed") or spec.get("smudge")),
    }


def main() -> None:
    req_dir = Path(sys.argv[1])
    spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    dyn = (req_dir / "prompt_dynamic.txt").read_text(encoding="utf-8")
    m = re.search(r"BATCH_MANIFEST_JSON.*?(\{.*)", dyn, re.S)
    manifest = json.loads(m.group(1))

    items = []
    for item in manifest["items"]:
        label = item["sub_items"][0]["tile_label"].split(".")[0]
        key = item["paper_key"]
        pkey = label if label in spec else key
        if pkey not in spec:
            raise SystemExit(f"spec missing paper {label} {item.get('student_name')}")
        pspec = spec[pkey]
        details = []
        for sub in item["sub_items"]:
            if not sub.get("is_target"):
                continue
            pid = sub["part_id"]
            if pid not in pspec:
                raise SystemExit(f"spec missing {label} {pid}")
            details.append(_detail(pid, pspec[pid]))
        items.append(
            {
                "paper_key": key,
                "student_id": item["student_id"],
                "grading_details": details,
            }
        )
    out = {"question_id": manifest.get("question_id") or manifest["items"][0]["question_id"], "items": items}
    (req_dir / "response.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"wrote response.json ({len(items)} items)")


if __name__ == "__main__":
    main()
