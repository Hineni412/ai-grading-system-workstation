import hashlib
import json
from pathlib import Path

from backend.answer_regions.answer_region_models import load_question_binding_catalog
from backend.scan_grading.grading_completeness import audit_grading_details
from backend.scan_grading.grading_service import _load_rubric_for_preflight, _target_question_ids_from_regions


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_repeated_id_lookup_keeps_alias_ambiguity_and_tracks_changed_known_scope():
    from backend.question_id_contract import question_id_coordinates, resolve_known_question_id
    known = ["Q12", "Q13(P1)", "Q13(P2)"]
    for _ in range(3):
        assert resolve_known_question_id("Q12（1）", iter(known)) == "Q12"
        assert resolve_known_question_id("Q13_1", known) == "Q13(P1)"
        assert resolve_known_question_id("P1", known) is None
        assert resolve_known_question_id("Q13(P3)", known) is None
    known.append("Q13（1）")
    assert resolve_known_question_id("Q13_1", known) is None
    known.remove("Q13（1）")
    assert resolve_known_question_id("Q13_1", known) == "Q13(P1)"
    assert question_id_coordinates(["invalid"], parent_id={"invalid": 1}) is None
    assert question_id_coordinates("P1", parent_id="Q12") == (12, 1)


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
