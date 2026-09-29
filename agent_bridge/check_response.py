"""Pre-validate a drafted bridge response with the real pipeline validators.

Usage (project runtime):

    runtime\\python\\python.exe -X utf8 agent_bridge\\check_response.py <req_dir> <response.json>

Loads the request's manifest, rebuilds the real specs from the session's
rubric/answer-key files, and runs ``validate_objective_paper_response`` or
``validate_ai_major_response`` exactly as the pipeline will.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_ID = 4
BRIDGE_DIR = Path(__file__).resolve().parent / "session_4"


def _load_session_docs() -> tuple[dict, dict]:
    from backend.repositories.compat import open_grading_repositories
    from path_manager import get_path_manager, resolve_stored_file_path

    pm = get_path_manager()
    db = open_grading_repositories(pm.databases_dir / "grading_system.db")
    session = db.get_grading_session(SESSION_ID)
    rubric = json.loads(
        resolve_stored_file_path(session["rubric_path"], data_root=pm.data_root)
        .read_text(encoding="utf-8")
    )
    answer_key = json.loads(
        resolve_stored_file_path(session["answer_key_path"], data_root=pm.data_root)
        .read_text(encoding="utf-8")
    )
    return rubric, answer_key


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_response.py <req_dir> <response.json>")
        return 2
    req_dir = Path(argv[0])
    response = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    request = json.loads((req_dir / "request.json").read_text(encoding="utf-8"))
    manifest = request.get("manifest") or {}
    rubric, answer_key = _load_session_docs()
    mode = manifest.get("mode")

    if mode == "objective_paper_recognition":
        from objective_batch_recognition_service import (
            OBJECTIVE_AUTO_SCORE_MIN_CONFIDENCE,
            ObjectiveQuestionSpec,
            build_objective_question_specs,
            validate_objective_paper_response,
        )

        specs_all = build_objective_question_specs(str(SESSION_ID), rubric, answer_key)
        spec_by_qid = {spec.question_id: spec for spec in specs_all}
        specs = [
            spec_by_qid[qid]
            for qid in manifest.get("target_question_ids", [])
            if qid in spec_by_qid
        ]
        accepted, review = validate_objective_paper_response(
            response=response,
            manifest=manifest,
            specs=specs,
            min_confidence=OBJECTIVE_AUTO_SCORE_MIN_CONFIDENCE,
        )
        print(json.dumps({"accepted": accepted, "review": review}, ensure_ascii=False, indent=2, default=str))
        return 0

    if mode == "hybrid_major_batch":
        from ai_batch_grading_service import (
            build_major_question_specs,
            validate_ai_major_response,
        )

        spec = next(
            item
            for item in build_major_question_specs(rubric, answer_key)
            if item.question_id == manifest.get("question_id")
        )
        accepted, failed = validate_ai_major_response(
            response, manifest, spec, min_confidence=80.0
        )
        print(
            json.dumps(
                {
                    "accepted_papers": [a["paper_key"] for a in accepted],
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0

    print(f"unknown manifest mode: {mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
