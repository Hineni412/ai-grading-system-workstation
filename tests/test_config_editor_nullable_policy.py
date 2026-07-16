from __future__ import annotations

import json

from backend.api.routers.config import _editor_edits
from backend.api.schemas.config import ConfigEditorEditRequest
from backend.config_workspace.editor import (
    apply_config_editor_changes,
    project_config_editor,
)


def _payload() -> dict:
    return {
        "rubric": {
            "total_score": 4,
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "proof",
                    "max_score": 4,
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 4,
                            "answer_only_max_score": 1,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 4,
                                    "core_goal": "Show the proof",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q1",
                    "canonical_answer": "Proof",
                    "accepted_forms": [],
                    "method_variants": [],
                    "parts": [
                        {
                            "part_id": "P1",
                            "answer": "Proof",
                            "analysis": "",
                            "step_milestones": [],
                        }
                    ],
                }
            ]
        },
        "meta": {"warnings": []},
    }


def test_explicit_null_clears_part_policy_while_omission_preserves_it() -> None:
    payload = _payload()
    row = project_config_editor(payload)[0]

    omitted_edit = _editor_edits(
        [ConfigEditorEditRequest(row_id=row.row_id, standard_answer="Updated proof")]
    )
    preserved = apply_config_editor_changes(payload, edits=omitted_edit, commands=())
    assert preserved["rubric"]["questions"][0]["parts"][0]["answer_only_max_score"] == 1

    clear_request = ConfigEditorEditRequest.model_validate(
        {"row_id": row.row_id, "answer_only_max_score": None}
    )
    cleared = apply_config_editor_changes(
        payload,
        edits=_editor_edits([clear_request]),
        commands=(),
    )

    part = cleared["rubric"]["questions"][0]["parts"][0]
    assert "answer_only_max_score" in part
    assert part["answer_only_max_score"] is None
    refreshed = json.loads(json.dumps(cleared))
    assert project_config_editor(refreshed)[0].answer_only_max_score is None
