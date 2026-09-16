# -*- coding: utf-8 -*-
"""Rebuild a subjective retry response from the original graded details.

usage: respond_retry.py <request_dir>

For every item in the new retry manifest, looks up the SAME student's detail
in the original run's response files (matched by student_id + part_id),
rewrites invalid step achievements (``missing`` -> ``none``), recomputes
evidence_steps/missing_steps, then runs the detail through the REAL
``_detail_from_ai_item`` validator before writing response.json.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUESTS = Path(__file__).resolve().parent / "session_4" / "requests"
RUBRIC_PATH = ROOT / "user_data" / "config" / "uploaded" / "rubric_job-290.json"
ANSWER_KEY_PATH = ROOT / "user_data" / "config" / "uploaded" / "answer_key_job-290.json"

_ACH_FIX = {"missing": "none"}


def _load_specs():
    from hybrid_batch_grading_service import build_major_question_specs

    rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    answer_key = json.loads(ANSWER_KEY_PATH.read_text(encoding="utf-8"))
    return {s.question_id: s for s in build_major_question_specs(rubric, answer_key)}


def _old_detail_index():
    """(student_id, part_id) -> detail dict from the original run's responses."""
    index = {}
    for resp in sorted(REQUESTS.glob("*/response.json")):
        try:
            data = json.loads(resp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in data.get("items") or []:
            sid = item.get("student_id")
            for detail in item.get("grading_details") or []:
                qid = str(detail.get("question_id") or "").strip()
                if sid is not None and qid:
                    index[(int(sid), qid)] = detail
    return index


def _repair_detail(detail: dict) -> dict:
    detail = dict(detail)
    steps = []
    for step in detail.get("step_assessments") or []:
        step = dict(step)
        ach = str(step.get("achievement") or "").strip().lower()
        step["achievement"] = _ACH_FIX.get(ach, ach)
        steps.append(step)
    detail["step_assessments"] = steps
    detail["evidence_steps"] = [
        s["step_id"] for s in steps if s["achievement"] != "none"
    ]
    detail["missing_steps"] = [
        s["step_id"] for s in steps if s["achievement"] == "none"
    ]
    return detail


def main() -> None:
    from hybrid_batch_grading_service import _detail_from_ai_item

    req_dir = Path(sys.argv[1])
    dyn = (req_dir / "prompt_dynamic.txt").read_text(encoding="utf-8")
    m = re.search(r"BATCH_MANIFEST_JSON.*?(\{.*)", dyn, re.S)
    manifest = json.loads(m.group(1))
    specs = _load_specs()
    spec = specs.get(manifest.get("question_id"))
    if spec is None:
        raise SystemExit(f"no spec for {manifest.get('question_id')}")
    old = _old_detail_index()

    items = []
    problems = []
    for item in manifest["items"]:
        sid = int(item["student_id"])
        targets = item.get("target_detail_question_ids") or [
            s["part_id"] for s in item.get("sub_items") or [] if s.get("is_target")
        ]
        details = []
        for part_id in targets:
            detail = old.get((sid, part_id))
            if detail is None:
                problems.append(f"{item.get('student_name') or sid}: no old detail for {part_id}")
                continue
            detail = _repair_detail(detail)
            allowed = set(targets)
            result, err, _meta = _detail_from_ai_item(
                dict(detail),
                allowed,
                80.0,
                spec=spec,
            )
            if err or result is None:
                problems.append(
                    f"{item.get('student_name') or sid} {part_id}: {err}"
                )
                continue
            details.append(detail)
        items.append(
            {
                "paper_key": item["paper_key"],
                "student_id": sid,
                "grading_details": details,
            }
        )

    if problems:
        print("VALIDATION PROBLEMS:")
        for p in problems:
            print(" -", p)
        raise SystemExit(2)

    out = {
        "question_id": manifest.get("question_id") or manifest["items"][0]["question_id"],
        "items": items,
    }
    (req_dir / "response.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    names = [i.get("student_name") for i in manifest["items"]]
    print(f"wrote response.json for {names} ({len(items)} items)")


if __name__ == "__main__":
    main()
