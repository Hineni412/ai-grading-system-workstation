import hashlib
import json
from pathlib import Path

from answer_region_models import load_question_binding_catalog
from grading_completeness import audit_grading_details
from grading_service import _load_rubric_for_preflight, _target_question_ids_from_regions


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reading_legacy_ids_is_pure_and_all_flows_agree(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    payload = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "P1", "part_score": 2},
                    {"part_id": "P2", "part_score": 3},
                ],
            },
            {
                "question_id": "Q13",
                "parts": [
                    {"part_id": "P1", "part_score": 4},
                    {"part_id": "P2", "part_score": 5},
                ],
            },
        ]
    }
    rubric_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = digest(rubric_path)

    rubric = _load_rubric_for_preflight(rubric_path)
    binding = load_question_binding_catalog(rubric_path)
    targets = _target_question_ids_from_regions(
        [{"mapped_question_id": "Q12"}, {"mapped_question_id": "Q13(P2)"}],
        rubric=rubric,
    )
    audit = audit_grading_details(
        rubric,
        [
            {"question_id": "Q12(1)", "score_awarded": 2},
            {"question_id": "Q12-2", "score_awarded": 3},
            {"question_id": "Q13(P1)", "score_awarded": 4},
            {"question_id": "Q13_2", "score_awarded": 5},
        ],
    )

    assert binding.automatic_candidates == (
        "Q12(P1)", "Q12(P2)", "Q13(P1)", "Q13(P2)"
    )
    assert targets == ["Q12(P1)", "Q12(P2)", "Q13(P2)"]
    assert audit["status"] == "complete"
    assert digest(rubric_path) == before
