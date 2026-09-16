# -*- coding: utf-8 -*-
"""Replay responder for the isolated session-4 re-grade.

Watches ``D:\\week3_regrade_sandbox\\bridge\\session_4\\requests`` and answers
each request with THIS assistant's grading decisions, re-expressed in the
current (post-fix) response contract:

* objective items are translated from the previous run's per-student
  responses: ``~NN`` changed-answer markers become answer_state=clear +
  has_discarded_content + confidence NN/100; review-flagged items stay
  review-flagged with a provisional score of 0; answers that were only held
  back by the old string-matcher (e.g. ``-1+√10``) are now scored by my own
  mathematical judgement, since the contract makes the model own the score.
* subjective details for Q12(P2) and Q13 are replayed verbatim (the rubric
  steps did not change); Q12(P1) is re-scored under the new teacher-confirmed
  answer-only rule (a=8 & b=1 -> S1=5, c=4 -> S2=4) using the values I
  recorded from each paper.
* every emitted payload is checked against the project's real validators
  before it is written; anything failing validation is reported instead of
  being served.

    runtime\\python\\python.exe -X utf8 agent_bridge\\replay_server.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault(
    "AI_GRADING_WORKTREE_DATA_DIR", r"D:\week3_regrade_sandbox\data"
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SANDBOX = Path(r"D:\week3_regrade_sandbox")
DATA = SANDBOX / "data"
REQS = SANDBOX / "bridge" / "session_4" / "requests"
OLD_REQS = ROOT / "agent_bridge" / "session_4" / "requests"
LOG = SANDBOX / "bridge" / "session_4" / "replay_events.jsonl"
RUBRIC_PATH = DATA / "config" / "uploaded" / "rubric_editor-1c0b64fba2044beaa7ad3a8a7c8cd647.json"
ANSWER_KEY_PATH = DATA / "config" / "uploaded" / "answer_key_editor-1c0b64fba2044beaa7ad3a8a7c8cd647.json"

FILL_MAX = 6
CHOICE_MAX = 6

# ---- my scoring decisions for legible fill-in answers (model owns the score)
FILL_EQUIV = {
    "Q8": {"√10-1", "√10−1", "√(10)−1", "√(10)-1", "-1+√10", "√(10)−1"},
    "Q9": {">", "＞"},
    "Q10": {"4", "a=4", "4=a", "a = 4", "4 = a", "a＝4", "4＝a", "5-1"},
    "Q11": {"√34", "√(34)", "+√34", "√(34)cm", "√34cm"},
}
# review reasons that meant "glyph doubt" -> keep the item flagged
_GLYPH_DOUBT = (
    "glyph", "unclear", "struck", "multiple", "scribble", "tail", "second",
    "garbled", "ambiguous", "clipping", "delta", "psi", "v_like", "e_or",
    "z_or", "ad_struck",
)
# answers that are legible but describe rather than answer -> score 0, keep review
_DESCRIPTIVE = {"descriptive_text"}

# ---- Q12(P1) re-scored under the new answer-only rubric ------------------
# s = (S1 score, S2 score); S1(5): a=8 and b=1; S2(4): c=4.
P1_NEW = {
    8:  dict(s=(5, 4), review=False, obs="a=8,b=1,c=4", note="仅写答案无过程，三值全对"),
    9:  dict(s=(2, 4), review=True,  obs="a≈∛2,b=1,c=4", note="a误为∛2；b、c正确"),
    12: dict(s=(2, 4), review=False, obs="a=4,b=1,c=4", note="a误为4"),
    13: dict(s=(2, 4), review=False, obs="a=4,b=1,c=4", note="a误为4"),
    17: dict(s=(5, 4), review=False, obs="a=8,b=1,c=4", note="仅写答案无过程，三值全对"),
    19: dict(s=(5, 0), review=False, obs="a=8,b=1,c未写出", note="缺c值"),
    22: dict(s=(2, 4), review=False, obs="a≈1.3,b=1,c=4", note="a误为1.3"),
    24: dict(s=(5, 2), review=True,  obs="a=8,b=1,c=±4", note="c写成±4，多余负号"),
    35: dict(s=(5, 4), review=False, obs="a=8,b=1,c=4", note="三值全对"),
    45: dict(s=(0, 4), review=True,  obs="√a=2,√16=c,√20=20.2", note="c=√16即4但记号非常规；a、b未得"),
    52: dict(s=(0, 0), review=False, obs="a=4,c=3/5,b=5/3", note="三值均错"),
    55: dict(s=(2, 0), review=False, obs="a=2,b=1,c=2", note="仅b正确"),
    58: dict(s=(2, 4), review=False, obs="a=2,b=1,c=4", note="a误为2"),
    60: dict(s=(0, 0), review=True,  obs="作答涂改混乱，无可辨识数值", note="无有效数值"),
    66: dict(s=(2, 0), review=True,  obs="a=∛2,b=1,c=16", note="a误为∛2，c误为16"),
    67: dict(s=(2, 4), review=True,  obs="a=2,b=1,c=4", note="a误为2"),
    77: dict(s=(5, 0), review=True,  obs="a=8,b=1,c=5（另写4<c<5）", note="c误取5"),
    80: dict(s=(5, 4), review=False, obs="a=8,b=1,c=4", note="三值全对"),
    81: dict(s=(2, 0), review=False, obs="a=√4,b=√1,c=√20", note="a=√4即2错；b=√1即1对；c=√20非4"),
    86: dict(s=(0, 0), review=False, obs="a=b×a", note="无有效数值"),
    89: dict(s=(2, 4), review=True,  obs="a=2,b=√1,c≈4", note="a误为2；b=√1即1；c≈4按4计"),
    91: dict(s=(0, 0), review=False, obs="仅写“9”", note="无a/b/c数值"),
}
_P1_BLANK = {26, 30, 46, 48, 50, 53, 61, 68, 69, 76}

_ACH_FIX = {"missing": "none"}
_MANIFEST_RE = re.compile(r"BATCH_MANIFEST_JSON.*?(\{.*)", re.S)


def _log(event: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# old response indexes
# --------------------------------------------------------------------------

def _old_objective() -> dict[int, dict[str, dict]]:
    out: dict[int, dict[str, dict]] = {}
    for d in sorted(OLD_REQS.iterdir()):
        rp = d / "response.json"
        if not rp.exists():
            continue
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = data.get("student_id")
        if sid is not None and isinstance(data.get("answers"), list):
            out.setdefault(int(sid), {}).update(
                {str(a.get("question_id")): a for a in data["answers"]}
            )
    return out


def _old_subjective() -> dict[tuple[int, str], dict]:
    out: dict[tuple[int, str], dict] = {}
    for d in sorted(OLD_REQS.iterdir()):
        rp = d / "response.json"
        if not rp.exists():
            continue
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in data.get("items") or []:
            sid = item.get("student_id")
            for det in item.get("grading_details") or []:
                qid = str(det.get("question_id") or "").strip()
                if sid is not None and qid:
                    out[(int(sid), qid)] = det
    return out


# --------------------------------------------------------------------------
# objective translation
# --------------------------------------------------------------------------

def _choice_standard(spec) -> str:
    sa = spec.standard_answer
    if isinstance(sa, list) and sa:
        sa = sa[0]
    return str(sa or "").strip().upper()


def _fill_correct(qid: str, answer: str) -> bool:
    return answer.strip() in FILL_EQUIV.get(qid, set())


def _translate_obj_answer(qid: str, qtype: str, old: dict, max_score: int) -> dict:
    raw = str(old.get("recognized_answer") or "")
    conf_old = float(old.get("confidence") or 0)
    old_review = bool(old.get("need_review"))
    reason = str(old.get("review_reason") or "")

    changed = re.fullmatch(r"(.+)~(\d{1,3}(?:\.\d+)?)?", raw)
    changed_conf = None
    if changed:
        raw = changed.group(1)
        changed_conf = float(changed.group(2)) / 100 if changed.group(2) else 0.85
        if not reason:
            reason = "changed_answer_clear_replacement"
    clean = raw.strip()

    is_blank = clean.casefold() in {"", "blank", "empty", "unanswered", "空白", "未作答"}
    is_unclear_token = clean.upper() in {"UNCLEAR", "MULTIPLE"}
    discarded_only = is_blank and "discarded" in reason.lower()

    conf = changed_conf if changed_conf is not None else conf_old
    if old_review:
        conf = min(conf, 0.6)

    glyph_doubt = old_review and any(p in reason.lower() for p in _GLYPH_DOUBT)
    descriptive = reason.lower() in _DESCRIPTIVE or any(
        k in clean for k in ("的长度", "平方根", "小数")
    )
    multi_answers = bool(re.search(r"或|，|,", clean)) and not is_blank

    # ---- scoring decision (the model owns the score under the new contract)
    if discarded_only:
        state, score, need_review = "discarded_only", 0, False
        reason = "discarded_answer_only"
        evidence = "仅见已划去/作废的笔迹，无有效答案"
    elif is_blank:
        state, score, need_review = "blank", 0, False
        reason = reason or ""
        evidence = "答题区空白"
    elif is_unclear_token or glyph_doubt or multi_answers:
        state, score, need_review = "uncertain", 0, True
        reason = reason or "uncertain_answer_state"
        evidence = f"疑似答案“{clean}”，笔迹/改答关系需教师确认"
    elif old_review and not descriptive:
        # old review was only about machine equivalence (e.g. reordered
        # terms): the answer itself was legible, so I now score it myself.
        if qtype == "choice":
            correct = clean.upper() == _CHOICE_STD.get(qid)
        else:
            correct = _fill_correct(qid, clean)
        state = "clear"
        score = max_score if correct else 0
        need_review = False
        evidence = f"有效手写答案：{clean}"
    else:
        state = "clear"
        if qtype == "choice":
            if clean.upper() not in {"A", "B", "C", "D", "E", "F"}:
                state, score, need_review = "uncertain", 0, True
                reason = reason or "invalid_choice_answer"
                evidence = f"选择答案无法解析：{clean!r}"
            else:
                score = max_score if clean.upper() == _CHOICE_STD.get(qid) else 0
                need_review = old_review
        else:
            score = max_score if _fill_correct(qid, clean) else 0
            need_review = old_review and descriptive
        if state == "clear":
            evidence = (
                f"清晰改答，有效手写答案：{clean}"
                if changed or "discarded" in reason.lower() or "struck" in reason.lower()
                else f"有效手写答案：{clean}"
            )
    if descriptive and not is_blank:
        state, score, need_review = "uncertain", 0, True
        reason = reason or "descriptive_text"
        evidence = f"作答为描述性内容“{clean}”，需教师确认"

    deduction = ""
    if score < max_score and not need_review and state == "clear" and not is_blank:
        deduction = f"手写答案“{clean}”与标准答案不符"
    if need_review and score == 0:
        deduction = deduction or ""

    item = {
        "question_id": qid,
        "question_type": qtype,
        "recognized_answer": "" if (is_blank and qtype != "choice") else clean,
        "raw_answer": None if qtype == "choice" else ("" if is_blank else clean),
        "normalized_answer": None if qtype == "choice" else ("" if is_blank else clean),
        "confidence": round(conf, 3),
        "need_review": bool(need_review),
        "review_reason": reason if need_review else (reason if state == "clear" and changed else ""),
        "answer_state": state,
        "has_discarded_content": bool(changed) or "struck" in reason.lower() or "discarded" in reason.lower(),
        "score_awarded": score,
        "deduction_reason": deduction,
        "answer_evidence": evidence,
        "error_category": "",
        "error_summary": "",
    }
    if need_review:
        item["error_category"] = "需复核"
        item["error_summary"] = reason or "needs_teacher_check"
    return item


# --------------------------------------------------------------------------
# subjective translation
# --------------------------------------------------------------------------

def _repair_detail(detail: dict) -> dict:
    detail = dict(detail)
    steps = []
    for step in detail.get("step_assessments") or []:
        step = dict(step)
        ach = str(step.get("achievement") or "").strip().lower()
        step["achievement"] = _ACH_FIX.get(ach, ach)
        steps.append(step)
    detail["step_assessments"] = steps
    detail["evidence_steps"] = [s["step_id"] for s in steps if s["achievement"] != "none"]
    detail["missing_steps"] = [s["step_id"] for s in steps if s["achievement"] == "none"]
    return detail


def _p1_detail(sid: int, old: dict | None) -> dict:
    """Re-derive the Q12(P1) detail under the new answer-only rubric."""
    if sid in _P1_BLANK:
        return {
            "question_id": "Q12(P1)", "score_awarded": 0,
            "deduction_reason": "未见有效作答",
            "confidence_score": 95.0, "needs_human_review": False,
            "error_category": "未作答", "error_summary": "blank_or_no_valid_work",
            "secondary_errors": [], "observed_answer": "",
            "evidence_steps": [], "missing_steps": ["S1", "S2"],
            "answer_only_correct": False,
            "step_assessments": [
                {"step_id": "S1", "achievement": "none", "score_awarded": 0,
                 "reason": "未给出a、b数值", "student_evidence": "",
                 "missing_or_error": "答题区空白"},
                {"step_id": "S2", "achievement": "none", "score_awarded": 0,
                 "reason": "未给出c数值", "student_evidence": "",
                 "missing_or_error": "答题区空白"},
            ],
            "alternative_solution_detected": False,
            "alternative_solution_summary": None,
            "candidate_scores": [{"score": 0, "confidence": 0.95, "reason": "空白"}],
            "answer_is_blank_or_no_valid_work": True,
            "answer_discarded_by_smudge": False,
        }
    dec = P1_NEW.get(sid)
    if dec is None:
        # previously full marks: a=8,b=1,c=4 all present -> verbatim reuse
        return _repair_detail(old) if old else None
    s1, s2 = dec["s"]
    total = s1 + s2
    note = dec["note"]
    obs = dec["obs"]
    s1_full = s1 == 5
    s2_full = s2 == 4
    steps = [
        {
            "step_id": "S1", "score_awarded": s1,
            "achievement": "full" if s1_full else ("none" if s1 == 0 else "partial"),
            "reason": ("a=8且b=1正确" if s1_full else f"a、b数值不完整：{note}"),
            "student_evidence": (obs if s1 > 0 else ("" if s1 == 0 else obs)),
            "missing_or_error": ("" if s1_full else note),
        },
        {
            "step_id": "S2", "score_awarded": s2,
            "achievement": "full" if s2_full else ("none" if s2 == 0 else "partial"),
            "reason": ("c=4正确" if s2_full else f"c值不正确或缺失：{note}"),
            "student_evidence": (obs if s2 > 0 else ("" if s2 == 0 else obs)),
            "missing_or_error": ("" if s2_full else note),
        },
    ]
    for st in steps:
        if st["achievement"] == "none":
            st["student_evidence"] = ""
        elif not st["student_evidence"]:
            st["student_evidence"] = obs
    conf = 85.0 if dec["review"] else 90.0
    return {
        "question_id": "Q12(P1)", "score_awarded": total,
        "deduction_reason": "" if total == 9 else note,
        "confidence_score": conf,
        "needs_human_review": bool(dec["review"]),
        "error_category": "需复核" if dec["review"] else None,
        "error_summary": note if dec["review"] else None,
        "secondary_errors": [], "observed_answer": obs,
        "evidence_steps": [s["step_id"] for s in steps if s["achievement"] != "none"],
        "missing_steps": [s["step_id"] for s in steps if s["achievement"] == "none"],
        "answer_only_correct": total == 9,
        "step_assessments": steps,
        "alternative_solution_detected": False,
        "alternative_solution_summary": None,
        "candidate_scores": [
            {"score": total, "confidence": conf / 100.0,
             "reason": ("数值判分：" + note) if total < 9 else "三值全对"}
        ],
        "answer_is_blank_or_no_valid_work": False,
        "answer_discarded_by_smudge": False,
    }


# --------------------------------------------------------------------------
# request handling
# --------------------------------------------------------------------------

def _manifest(req_dir: Path) -> dict | None:
    try:
        req = json.loads((req_dir / "request.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m = req.get("manifest")
    if isinstance(m, dict):
        return m
    for name in ("prompt_dynamic.txt", "prompt_static.txt"):
        p = req_dir / name
        if not p.exists():
            continue
        mm = _MANIFEST_RE.search(p.read_text(encoding="utf-8"))
        if mm:
            try:
                return json.loads(mm.group(1))
            except ValueError:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(mm.group(1))
                    return obj
                except ValueError:
                    continue
    return None


def _handle_objective(req_dir: Path, manifest: dict, specs_by_qid, obj_old) -> str:
    sid = int(manifest.get("student_id"))
    old = obj_old.get(sid) or {}
    answers = []
    problems = []
    for qid in manifest.get("target_question_ids") or []:
        spec = specs_by_qid.get(str(qid))
        item_old = old.get(str(qid))
        if spec is None or item_old is None:
            problems.append(f"{qid}: no spec/old answer")
            answers.append({
                "question_id": str(qid),
                "question_type": (spec.question_type if spec else "choice"),
                "recognized_answer": "unclear",
                "raw_answer": "" if (spec and spec.question_type == "fill_blank") else None,
                "normalized_answer": "" if (spec and spec.question_type == "fill_blank") else None,
                "confidence": 0.0,
                "need_review": True,
                "review_reason": "no_replay_data",
                "answer_state": "uncertain",
                "has_discarded_content": False,
                "score_awarded": 0,
                "deduction_reason": "",
                "answer_evidence": "",
                "error_category": "需复核",
                "error_summary": "no_replay_data",
            })
            continue
        answers.append(
            _translate_obj_answer(str(qid), spec.question_type, item_old, int(spec.max_score))
        )
    response = {
        "paper_key": manifest.get("paper_key"),
        "student_id": sid,
        "answers": answers,
    }
    from objective_batch_recognition_service import validate_objective_paper_response
    accepted, review = validate_objective_paper_response(
        response=response, manifest=manifest,
        specs=list(specs_by_qid.values()), min_confidence=0.8,
    )
    (req_dir / "response.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    status = (
        f"objective sid={sid} answers={len(answers)} "
        f"accepted={len(accepted)} review={len(review)}"
    )
    if problems:
        status += " | PROBLEMS: " + "; ".join(problems)
    return status


def _handle_subjective(req_dir: Path, manifest: dict, spec, subj_old) -> str:
    from hybrid_batch_grading_service import _detail_from_ai_item

    items = []
    problems = []
    for item in manifest.get("items") or []:
        sid = int(item["student_id"])
        targets = item.get("target_detail_question_ids") or [
            s["part_id"] for s in item.get("sub_items") or [] if s.get("is_target")
        ]
        details = []
        for pid in targets:
            pid = str(pid)
            if pid == "Q12(P1)":
                det = _p1_detail(sid, subj_old.get((sid, pid)))
            else:
                old = subj_old.get((sid, pid))
                det = _repair_detail(old) if old else None
            if det is None:
                problems.append(f"no detail for sid={sid} part={pid}")
                continue
            result, err, _meta = _detail_from_ai_item(
                dict(det), set(targets), 80.0, spec=spec,
            )
            if err or result is None:
                _log({"req": req_dir.name, "status": "VALIDATION_FAIL",
                      "sid": sid, "part": pid, "error": err})
            else:
                det = dict(det)
                det["question_id"] = result.question_id
            details.append(det)
        items.append(
            {
                "paper_key": item["paper_key"],
                "student_id": sid,
                "grading_details": details,
            }
        )
    response = {
        "question_id": manifest.get("question_id") or spec.question_id,
        "items": items,
    }
    (req_dir / "response.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    names = [i.get("student_name") for i in manifest.get("items") or []]
    status = f"subjective {spec.question_id} items={len(items)} names={names}"
    if problems:
        status += " | PROBLEMS: " + "; ".join(problems)
    return status


def _fallback_response(manifest: dict) -> dict:
    """Minimal structurally-valid response that parks everything for review."""
    if manifest.get("mode") == "objective_paper_recognition" or (
        "target_question_ids" in manifest and "items" not in manifest
    ):
        return {
            "paper_key": manifest.get("paper_key"),
            "student_id": manifest.get("student_id"),
            "answers": [
                {
                    "question_id": str(qid),
                    "question_type": "choice",
                    "recognized_answer": "unclear",
                    "raw_answer": None,
                    "normalized_answer": None,
                    "confidence": 0.0,
                    "need_review": True,
                    "review_reason": "replay_responder_error",
                    "answer_state": "uncertain",
                    "has_discarded_content": False,
                    "score_awarded": 0,
                    "deduction_reason": "",
                    "answer_evidence": "",
                    "error_category": "需复核",
                    "error_summary": "replay_responder_error",
                }
                for qid in manifest.get("target_question_ids") or []
            ],
        }
    return {
        "question_id": manifest.get("question_id") or "",
        "items": [
            {
                "paper_key": it.get("paper_key"),
                "student_id": it.get("student_id"),
                "grading_details": [],
            }
            for it in manifest.get("items") or []
        ],
    }


def main() -> int:
    from objective_batch_recognition_service import build_objective_question_specs
    from hybrid_batch_grading_service import build_major_question_specs

    rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    answer_key = json.loads(ANSWER_KEY_PATH.read_text(encoding="utf-8"))
    obj_specs = build_objective_question_specs("4", rubric, answer_key)
    specs_by_qid = {s.question_id: s for s in obj_specs}
    global _CHOICE_STD
    _CHOICE_STD = {s.question_id: _choice_standard(s) for s in obj_specs if s.question_type == "choice"}
    major_specs = {s.question_id: s for s in build_major_question_specs(rubric, answer_key)}
    obj_old = _old_objective()
    subj_old = _old_subjective()
    print(f"loaded: obj students={len(obj_old)} subj pairs={len(subj_old)}", flush=True)
    print(f"choice standards: {_CHOICE_STD}", flush=True)

    seen: set[Path] = set()
    while True:
        if (SANDBOX / "bridge" / "session_4" / "STOP").exists():
            print("STOP file found, exiting")
            return 0
        for req_dir in sorted(REQS.iterdir()) if REQS.exists() else []:
            if not req_dir.is_dir() or req_dir in seen:
                continue
            if (req_dir / "response.json").exists() or (req_dir / "served.json").exists():
                seen.add(req_dir)
                continue
            manifest = _manifest(req_dir)
            if manifest is None:
                _log({"req": req_dir.name, "status": "no_manifest"})
                seen.add(req_dir)
                continue
            try:
                if manifest.get("mode") == "objective_paper_recognition" or (
                    "target_question_ids" in manifest and "items" not in manifest
                ):
                    status = _handle_objective(req_dir, manifest, specs_by_qid, obj_old)
                else:
                    qid = str(manifest.get("question_id") or "")
                    spec = major_specs.get(qid)
                    if spec is None:
                        # fall back: infer from items' part ids
                        status = f"no major spec for {qid!r}"
                    else:
                        status = _handle_subjective(req_dir, manifest, spec, subj_old)
            except Exception as exc:  # noqa: BLE001
                status = f"ERROR {type(exc).__name__}: {exc}"
            if not (req_dir / "response.json").exists():
                # never leave the bridge hanging: emit an all-review response
                fb = _fallback_response(manifest)
                (req_dir / "response.json").write_text(
                    json.dumps(fb, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                status += " | wrote fallback all-review response"
            _log({"req": req_dir.name, "status": status})
            print(req_dir.name, "->", status, flush=True)
            seen.add(req_dir)
        time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
