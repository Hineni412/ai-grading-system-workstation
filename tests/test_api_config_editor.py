from __future__ import annotations

import json
import copy
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_grading_db,
    get_job_manager,
    get_upload_config_dir,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager
from backend.repositories.grading_database import open_grading_repositories


def _payload() -> dict:
    rubric_questions = []
    answer_questions = []
    for index, score in enumerate((17, 17, 17, 17, 17, 15), start=1):
        qid = f"Q{index}"
        pid = f"{qid}-P1"
        rubric_questions.append(
            {
                "question_id": qid,
                "question_type": "comprehensive",
                "max_score": score,
                "knowledge_id": f"K{index}",
                "parts": [
                    {
                        "part_id": pid,
                        "part_score": score,
                        "steps": [
                            {
                                "step_id": f"{pid}-S1",
                                "step_score": score,
                                "core_goal": "reason correctly",
                                "required_elements": ["reasoning"],
                                "allow_alternative_methods": True,
                            }
                        ],
                    }
                ],
            }
        )
        answer_questions.append(
            {
                "question_id": qid,
                "canonical_answer": f"Answer {index}",
                "accepted_forms": [f"Answer {index}"],
                "method_variants": [],
                "parts": [
                    {
                        "part_id": pid,
                        "answer": f"Answer {index}",
                        "analysis": "analysis",
                        "step_milestones": ["reasoning"],
                    }
                ],
            }
        )
    return {
        "rubric": {
            "exam_title": "Editor exam",
            "total_score": 100,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {"warnings": []},
    }


def _write_config(tmp_path: Path, db: DBManager, payload: dict | None = None) -> int:
    value = payload or _payload()
    root = tmp_path / "initial"
    root.mkdir(exist_ok=True)
    rubric = root / "rubric.json"
    answer = root / "answer.json"
    rubric.write_text(json.dumps(value["rubric"], ensure_ascii=False), encoding="utf-8")
    answer.write_text(
        json.dumps(value["answer_key"], ensure_ascii=False), encoding="utf-8"
    )
    return db.sessions.create_grading_session("Editor exam", str(rubric), str(answer))


@pytest.mark.parametrize(
    "q14_total, choice_score", [(48, 3), (68, 3), (148, 3.5), (48, -1)]
)
def test_teacher_scores_save_exactly_with_advisories_and_structure_command(
    editor_env, q14_total, choice_score
) -> None:
    client, db, _manager, tmp_path = editor_env
    payload = _payload()
    question_template = payload["rubric"]["questions"][0]
    answer_template = payload["answer_key"]["questions"][0]
    payload["rubric"]["questions"] = []
    payload["answer_key"]["questions"] = []
    requested: dict[str, list[list[float]]] = {}
    for number in [*range(1, 13), 14, 17]:
        qid = f"Q{number}"
        if number <= 6:
            scores = [[choice_score if number != 2 else 3]]
        elif number <= 11:
            scores = [[3]]
        elif number == 12:
            scores = [[1, 2] for _ in range(4)]
        elif number == 14:
            scores = [[q14_total / 16] * 2 for _ in range(8)]
            scores[3] = [q14_total / 24] * 3
        else:
            scores = [[2, 2, 2, 1]]
        requested[qid] = scores
        question = copy.deepcopy(question_template)
        answer = copy.deepcopy(answer_template)
        question.update(
            question_id=qid,
            question_type="choice"
            if number <= 6
            else "fill_blank"
            if number <= 11
            else "calculation",
        )
        answer["question_id"] = qid
        question["parts"] = []
        answer["parts"] = []
        for index, step_scores in enumerate(scores, 1):
            pid = qid if len(scores) == 1 else f"{qid}(P{index})"
            part = copy.deepcopy(question_template["parts"][0])
            part.update(
                part_id=pid,
                part_score=len(step_scores),
                steps=[
                    {
                        **copy.deepcopy(part["steps"][0]),
                        "step_id": f"S{step_index}",
                        "step_score": 1,
                    }
                    for step_index in range(1, len(step_scores) + 1)
                ],
            )
            question["parts"].append(part)
            answer["parts"].append(
                {**copy.deepcopy(answer_template["parts"][0]), "part_id": pid}
            )
        question["max_score"] = sum(part["part_score"] for part in question["parts"])
        payload["rubric"]["questions"].append(question)
        payload["answer_key"]["questions"].append(answer)
    session_id = _write_config(tmp_path, db, payload)
    url = f"/api/sessions/{session_id}/config/editor"
    first = client.get(url).json()
    desired = {
        qid: [score for part in parts for score in part]
        for qid, parts in requested.items()
    }
    edits = []
    for qid, scores in desired.items():
        rows = [row for row in first["rows"] if row["question_id"] == qid]
        edits.extend(
            {"row_id": row["row_id"], "score": score}
            for row, score in zip(rows, scores, strict=True)
        )
    # The existing browser can queue its unchanged structure to clear a stale
    # validation response, while retaining all its unsaved score edits.
    command = {
        "kind": "replace_question_structure",
        "question_id": "Q14",
        "parts": [
            {
                "part_id": f"Q14(P{index})",
                "steps": [
                    {
                        "step_id": f"S{step_index}",
                        "score": score,
                        "core_goal": "reason correctly",
                    }
                    for step_index, score in enumerate(scores, 1)
                ],
            }
            for index, scores in enumerate(requested["Q14"], 1)
        ],
    }
    saved = client.put(
        url, json={"revision": first["revision"], "edits": edits, "commands": [command]}
    )
    assert saved.status_code == 200, saved.json()
    reopened = client.get(url).json()
    assert reopened["total_score"] == pytest.approx(
        sum(sum(scores) for scores in desired.values())
    )
    for qid, scores in desired.items():
        assert [
            row["score"] for row in reopened["rows"] if row["question_id"] == qid
        ] == pytest.approx(scores)
    assert reopened["issues"] and all(
        issue["severity"] == "warning" for issue in reopened["issues"]
    )


@pytest.fixture
def editor_env(tmp_path: Path):
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    manager = JobManager(
        JobStore(tmp_path / "databases" / "grading.db"),
        max_workers=1,
        interrupted_input_root=tmp_path / "uploaded",
    )
    manager.register(
        "config_generation",
        lambda context: {
            "session_id": context.payload["session_id"],
            "outcome": "complete",
        },
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    with TestClient(app) as client:
        yield client, db, manager, tmp_path
    manager.shutdown()


def _assert_safe_editor(body: dict, tmp_path: Path) -> None:
    assert not any("path" in str(key).casefold() for key in body)
    assert str(tmp_path) not in json.dumps(body, ensure_ascii=False)
    assert re.fullmatch(r"[0-9a-f]{64}", body["revision"])


def test_put_saves_once_preserves_source_and_rejects_stale_revision(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    db.sessions.bind_grading_session_source(
        session_id,
        source_paper_path="papers/original.docx",
        source_paper_sha256="c" * 64,
    )
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    row = first["rows"][0]

    saved = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={
            "revision": first["revision"],
            "edits": [{"row_id": row["row_id"], "standard_answer": "Teacher answer"}],
            "commands": [],
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] != first["revision"]
    assert body["save_result"]["config_saved"] is True
    current = db.sessions.get_grading_session(session_id)
    assert current["source_paper_path"] == "papers/original.docx"
    assert current["source_paper_sha256"] == "c" * 64
    stored_rubric = json.loads(Path(current["rubric_path"]).read_text(encoding="utf-8"))
    stored_answer_key = json.loads(
        Path(current["answer_key_path"]).read_text(encoding="utf-8")
    )
    assert [question["question_id"] for question in stored_rubric["questions"]] == [
        f"Q{index}" for index in range(1, 7)
    ]
    assert [
        question["parts"][0]["part_id"] for question in stored_rubric["questions"]
    ] == [f"Q{index}" for index in range(1, 7)]
    assert [
        question["parts"][0]["part_id"] for question in stored_answer_key["questions"]
    ] == [f"Q{index}" for index in range(1, 7)]
    _assert_safe_editor(body, tmp_path)

    stale = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [], "commands": []},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_revision_conflict"


def test_database_failure_cleans_both_new_files_and_keeps_binding(
    editor_env, monkeypatch
) -> None:
    client, db, manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    old = db.sessions.get_grading_session(session_id)

    def fail_db(*_args, **_kwargs):
        raise RuntimeError("private database failure")

    monkeypatch.setattr(manager.store, "update_session_config_if_idle", fail_db)
    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={
            "revision": first["revision"],
            "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "x"}],
            "commands": [],
        },
    )
    assert response.status_code == 500
    current = db.sessions.get_grading_session(session_id)
    assert (current["rubric_path"], current["answer_key_path"]) == (
        old["rubric_path"],
        old["answer_key_path"],
    )
    assert list((tmp_path / "uploaded").glob("*editor*")) == []
    assert "private database failure" not in response.text
