from __future__ import annotations
import json
import copy
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.dependencies import get_grading_db, get_job_manager, get_upload_config_dir
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from backend.repositories.db_manager import DBManager
from backend.repositories.grading_database import open_grading_repositories


from backend.api.dependencies import get_config_source_service
from backend.config_workspace.sources import ConfigSourceService


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
    'q14_total,choice_score', [(48, 3), (68, 3), (148, 3.5), (48, -1)]
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
            "edits": [{
                "row_id": row["row_id"],
                "standard_answer": "Teacher answer",
                "core_goal": "edited scoring target",
                "equivalent_rules": ["alternative method"],
            }],
            "commands": [],
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] != first["revision"]
    assert body["save_result"]["config_saved"] is True
    saved_row = next(
        item for item in body["rows"] if item["question_id"] == "Q1"
    )
    assert saved_row["core_goal"] == "edited scoring target"
    assert saved_row["equivalent_rules"] == ["alternative method"]
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
    stored_step = stored_rubric["questions"][0]["parts"][0]["steps"][0]
    assert stored_step["core_goal"] == "edited scoring target"
    assert stored_step["equivalent_rules"] == ["alternative method"]
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


def test_open_answer_round_trip_through_config_editor_api_keeps_conditions(editor_env) -> None:
    from tests.test_api_config_editor import config_generation_contract__open_generated_config as _open_generated_config
    from backend.config_generation.normalization import normalize_new_generated_config_payload

    client, db, _manager, tmp_path = editor_env
    payload = _open_generated_config()
    normalize_new_generated_config_payload(payload)
    session_id = _write_config(tmp_path, db, payload)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    assert first["rows"][0]["answer_kind"] == "conditions"
    assert first["rows"][0]["required_elements"] == ["答案是整数且大于 2"]
    response = client.put(f"/api/sessions/{session_id}/config/editor", json={
        "revision": first["revision"], "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "100"}], "commands": [],
    })
    assert response.status_code == 200
    assert response.json()["rows"][0]["answer_kind"] == "conditions"
    assert response.json()["rows"][0]["required_elements"] == ["答案是整数且大于 2"]
    current = db.sessions.get_grading_session(session_id)
    persisted = json.loads(Path(current["rubric_path"]).read_text(encoding="utf-8"))
    step = persisted["questions"][0]["parts"][0]["steps"][0]
    assert step["answer_kind"] == "conditions"
    assert step["required_elements"] == ["答案是整数且大于 2"]
    assert db.results.get_session_results(session_id) == []


def api_config_generation_jobs__client(
    tmp_path: Path,
    *,
    config_generation_handler=None,
) -> tuple[TestClient, DBManager, JobManager]:
    # db lives under a "databases" directory so load_editor_config infers
    # tmp_path as the controlled data root for the session config files.
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register(
        "config_generation",
        config_generation_handler
        or (
            lambda context: {
                "session_id": int(context.payload["session_id"]),
                "outcome": "partial",
                "total_questions": 1,
                "generated_questions": 0,
                "failed_count": 1,
                "failed_question_ids": ["Q1"],
                "retryable": True,
            }
        ),
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    app.dependency_overrides[get_config_source_service] = lambda: ConfigSourceService(
        tmp_path / "uploaded"
    )
    return TestClient(app), db, manager


def api_config_generation_jobs__session(db: DBManager, tmp_path: Path) -> int:
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    return db.sessions.create_grading_session("Config Job", str(rubric), str(answer))


def test_config_generation_retry_rejects_unknown_external_request_outcome_without_confirmation(
    tmp_path: Path,
) -> None:
    client, db, manager = api_config_generation_jobs__client(tmp_path)
    session_id = api_config_generation_jobs__session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "failed",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": [],
            "failed_batches": [],
            "uncertain_question_ids": ["Q1"],
            "needs_teacher_resolution": True,
            "retryable": False,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id, "retry_question_ids": ["Q1"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_retry_not_available"


def test_config_generation_retry_accepts_all_uncertain_questions_after_teacher_confirmation(
    tmp_path: Path,
) -> None:
    client, db, manager = api_config_generation_jobs__client(tmp_path)
    session_id = api_config_generation_jobs__session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
            "sync_to_question_bank": True,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": [],
            "failed_batches": [],
            "uncertain_question_ids": ["Q10"],
            "needs_teacher_resolution": True,
            "uncertain_retry_available": True,
            "retryable": False,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={
            "source_job_id": source.id,
            "retry_question_ids": ["Q10"],
            "confirm_uncertain_retry": True,
            "client_request_token": "b" * 32,
        },
    )

    assert response.status_code == 202
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload["retry_question_ids"] == ["Q10"]
    assert stored.payload["confirm_uncertain_retry"] is True
    assert stored.payload["sync_to_question_bank"] is True


def config_generation_contract__open_generated_config(*, second_fixed_blank: bool = False) -> dict:
    from question_bank.solution_evidence import CoreResolution, QuestionSolutionEvidence
    from question_bank.training_criteria.analysis import grading_config_skeleton_from_solution_evidence
    from question_bank.training_criteria.combined_analysis import compose_generated_config_from_skeletons

    class UnmappedResolver:
        def resolve(self, _term_id):
            return CoreResolution(status="unmapped")

    points = [{
        "evidence_point_id": "part-1-step-1", "step_index": 1,
        "answer_kind": "conditions", "target": "给出一个大于 2 的整数",
        "justification": "题目要求整数且大于 2", "answer_anchor": "3",
        "observable_evidence": "答案是整数且大于 2", "depends_on": [], "fine_term_links": [],
        "equivalent_rules": ["任意满足全部条件的整数均正确"],
        "counterexamples": ["2 不大于 2", "2.5 不是整数"],
    }]
    if second_fixed_blank:
        points.append({
            "evidence_point_id": "part-1-step-2", "step_index": 2,
            "target": "给出 9 的算术平方根", "justification": "非负平方根", "answer_anchor": "3",
            "observable_evidence": "第二空为 3", "depends_on": [], "fine_term_links": [],
            "equivalent_rules": [], "counterexamples": ["-3 不是算术平方根"],
        })
    evidence = QuestionSolutionEvidence.from_model_dict({
        "schema_version": "question-solution-evidence-v2", "question_id": 1,
        "parts": [{"part_id": "part-1", "label": "", "response_mode": "short_answer_points" if second_fixed_blank else "exact_objective",
            "canonical_answer": "3；3" if second_fixed_blank else "3", "accepted_forms": [],
            "full_answer": "第一空例如 3，第二空为 3" if second_fixed_blank else "",
            "proof_obligations": [], "visual_requirements": [], "deduction_policy": ["逐空独立判定"],
            "allow_alternative_methods": True, "evidence_points": points}],
        "auxiliary_rules": [], "rationale": "合成开放答案", "confidence": 1,
    }, question_id=1, source_content_hash="0" * 64, resolver=UnmappedResolver())
    result = compose_generated_config_from_skeletons(
        [grading_config_skeleton_from_solution_evidence(evidence, question_ref="Q5")], exam_title="TEST-open-answer")
    question = result["rubric"]["questions"][0]
    question["question_type"] = "fill_blank"
    question["max_score"] = 2 if second_fixed_blank else 1
    question["parts"][0]["part_score"] = question["max_score"]
    for step in question["parts"][0]["steps"]:
        step["step_score"] = 1
    return result
