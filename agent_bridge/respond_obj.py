"""Write an objective-paper response from compact answer arguments.

    runtime\\python\\python.exe -X utf8 agent_bridge\\respond_obj.py <req_dir> <answers...>

``answers`` are per-target-question values in manifest order.  Markers:

* ``conf=95:blank`` -> clear empty answer (auto 0)
* ``conf=95:discarded`` -> only cancelled content remains (auto 0 marked 作废答案)
* suffix ``?``      -> need_review=true, confidence capped at 0.6 (reason ``needs_teacher_check``)
* suffix ``?reason``-> need_review=true with that reason
* suffix ``~``      -> changed answer, clear replacement (confidence 0.85)
* suffix ``~82``    -> changed answer, clear replacement (confidence 0.82)
* prefix ``conf=XX:`` on any value sets confidence (0-100)
* no confidence    -> pending review; no invented default confidence

Prefer ``<req_dir> --answers-file <json-file>`` with an ordered JSON list of
objects containing recognized_answer, confidence (0-1), need_review and
review_reason. Paper identity and question ids are copied from the request.

Example:

    respond_obj.py <req_dir> A C "C~85" B D "D?ambiguous_glyph" A "√10-1" ">" 4 √34
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path


def _parse(raw: str) -> tuple[str, float, bool, str]:
    confidence = None
    need_review = False
    reason = ""
    value = raw.strip()
    if value.startswith("conf="):
        head, _, value = value.partition(":")
        confidence = float(head.split("=", 1)[1]) / 100.0
    if "?" in value:
        value, _, tail = value.partition("?")
        need_review = True
        reason = tail or "needs_teacher_check"
    changed = re.fullmatch(r"(.+)~(\d{1,3}(?:\.\d+)?)?", value)
    if changed:
        value = changed.group(1)
        marked_confidence = float(changed.group(2)) / 100 if changed.group(2) else 0.85
        if confidence is not None and abs(confidence - marked_confidence) > 1e-9:
            raise ValueError('conflicting confidence fields')
        confidence = marked_confidence
        if not need_review:
            reason = "changed_answer_clear_replacement"
    if value.lower() == "discarded":
        value = ""
        reason = "discarded_answer_only" if confidence is not None and not need_review else "recognition_confidence_missing"
    if confidence is None:
        confidence = 0.0
        need_review = True
        reason = reason or 'recognition_confidence_missing'
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('confidence must be between 0 and 100')
    if need_review:
        confidence = min(confidence, 0.6)
    return value, confidence, need_review, reason


def build_response(manifest: dict, values: list) -> dict:
    targets = list(manifest.get('target_question_ids') or [])
    if not targets or len(values) != len(targets):
        raise ValueError(f'expected {len(targets)} answers, got {len(values)}')
    answers = []
    qtypes = manifest.get('question_types') or {}
    for qid, raw in zip(targets, values):
        extras = {}
        if isinstance(raw, dict):
            value = str(raw.get('raw_answer') if raw.get('raw_answer') is not None else raw.get('recognized_answer', '')).strip()
            confidence = raw.get('confidence')
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError(f'{qid}: explicit confidence in [0,1] is required')
            need_review = raw.get('need_review', False)
            if not isinstance(need_review, bool):
                raise ValueError(f'{qid}: need_review must be a boolean')
            reason = str(raw.get('review_reason') or '')
            if raw.get('answer_state') is not None:
                if raw['answer_state'] not in {'clear', 'blank', 'discarded_only', 'uncertain'}:
                    raise ValueError(f'{qid}: invalid answer_state')
                extras['answer_state'] = raw['answer_state']
            if 'has_discarded_content' in raw:
                if not isinstance(raw['has_discarded_content'], bool):
                    raise ValueError(f'{qid}: has_discarded_content must be a boolean')
                extras['has_discarded_content'] = raw['has_discarded_content']
        else:
            value, confidence, need_review, reason = _parse(str(raw))
        qtype = qtypes.get(qid) or 'choice'
        recognized = value.upper() if qtype == 'choice' else value
        if qtype == 'choice' and recognized not in {'A', 'B', 'C', 'D', 'E', 'F', '', 'BLANK', 'MULTIPLE', 'UNCLEAR'}:
            raise ValueError(f'{qid}: invalid choice answer; send answer and confidence separately')
        if qtype != 'choice' and '~' in value:
            raise ValueError(f'{qid}: unparsed answer marker')
        answers.append({
            'question_id': qid, 'question_type': qtype,
            'recognized_answer': recognized,
            'raw_answer': None if qtype == 'choice' else ('' if value.lower() == 'blank' else value),
            'normalized_answer': None if qtype == 'choice' else ('' if value.lower() == 'blank' else value),
            'confidence': round(confidence, 3), 'need_review': need_review,
            'review_reason': reason, **extras,
        })
    return {'paper_key': manifest.get('paper_key'), 'student_id': manifest.get('student_id'), 'answers': answers}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: respond_obj.py <req_dir> <answers...>")
        return 2
    req_dir = Path(argv[0])
    request = json.loads((req_dir / "request.json").read_text(encoding="utf-8"))
    manifest = request["manifest"]
    if len(argv) == 3 and argv[1] == '--answers-file':
        values = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
        if not isinstance(values, list):
            raise ValueError('answers file must contain a list')
    else:
        values = argv[1:]
    payload = build_response(manifest, values)
    out = req_dir / "response.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out.name} ({len(payload['answers'])} answers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
