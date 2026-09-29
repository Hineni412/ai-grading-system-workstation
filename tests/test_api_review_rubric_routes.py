from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_grading_db
from backend.repositories.grading_database import open_grading_repositories



def _client_for_config(
    tmp_path: Path,
    *,
    rubric: dict,
    answer_key: dict,
) -> tuple[TestClient, int, Path, Path]:
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    answer_path = tmp_path / "answer.json"
    rubric_path.write_text(
        json.dumps(rubric, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session_id = db.sessions.create_grading_session(
        "Rubric compatibility",
        str(rubric_path),
        str(answer_path),
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    return TestClient(app), session_id, rubric_path, answer_path


def _multipart_config(
    part_ids: tuple[str, str, str] = (
        "Q11(1)",
        "Q11(2)",
        "Q11(3)",
    ),
) -> tuple[dict, dict]:
    rubric_parts = []
    answer_parts = []
    for index, (part_id, score) in enumerate(
        zip(part_ids, (2, 3, 7)),
        start=1,
    ):
        rubric_parts.append(
            {
                "part_id": part_id,
                "part_score": score,
                "answer_only_max_score": 1 if index == 3 else None,
                "require_final_answer": index == 3,
                "presentation_rules": [
                    {
                        "rule_id": "final_answer_required",
                        "rule": "未写最终结论最多得 1 分",
                    }
                ]
                if index == 3
                else [],
                "steps": [
                    {
                        "step_id": f"S{index}",
                        "step_score": score,
                        "core_goal": f"完成第 {index} 问",
                        "required_elements": [
                            "推导∠BCE的表达式",
                            "求出∠DCE的度数",
                        ]
                        if index == 3
                        else [f"第 {index} 问证据"],
                        "deduction_rules": ["漏写结论扣 1 分"] if index == 3 else [],
                    }
                ],
            }
        )
        answer_parts.append(
            {
                "part_id": part_id,
                "answer": "42°" if index == 3 else str(index),
                "accepted_forms": ["42 度"] if index == 3 else [],
                "analysis": "",
                "step_milestones": [],
            }
        )
    return (
        {
            "exam_title": "Legacy rubric",
            "total_score": 12,
            "questions": [
                {
                    "question_id": "Q11",
                    "question_type": "comprehensive",
                    "max_score": 12,
                    "knowledge_ids": ["K-angle"],
                    "parts": rubric_parts,
                }
            ],
        },
        {
            "questions": [
                {
                    "question_id": "Q11",
                    "parts": answer_parts,
                }
            ]
        },
    )


def test_review_rubric_scopes_bare_part_ids_to_their_parent(
    tmp_path: Path,
) -> None:
    rubric, answer_key = _multipart_config(("P1", "P2", "P3"))
    client, session_id, _rubric_path, _answer_path = _client_for_config(
        tmp_path,
        rubric=rubric,
        answer_key=answer_key,
    )

    response = client.get(f"/api/sessions/{session_id}/review/questions/Q11(P3)/rubric")

    assert response.status_code == 200
    body = response.json()
    assert body["question_id"] == "Q11(P3)"
    assert body["points"][0]["standard_answer"] == "42°"


def test_review_rubric_rejects_ambiguous_historical_parts(
    tmp_path: Path,
) -> None:
    rubric, answer_key = _multipart_config(("Q11(P1)", "Q11(1)", "Q11(3)"))
    client, session_id, _rubric_path, _answer_path = _client_for_config(
        tmp_path,
        rubric=rubric,
        answer_key=answer_key,
    )

    response = client.get(f"/api/sessions/{session_id}/review/questions/Q11(P1)/rubric")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("review_rubric_question_conflict")
