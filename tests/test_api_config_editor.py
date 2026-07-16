from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_grading_db, get_job_manager, get_upload_config_dir
from backend.config_workspace.drafts import create_session_draft
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


def _payload() -> dict:
    rubric_questions = []
    answer_questions = []
    for index, score in enumerate((17, 17, 17, 17, 17, 15), start=1):
        qid = f"Q{index}"
        pid = f"{qid}-P1"
        rubric_questions.append({
            "question_id": qid,
            "question_type": "comprehensive",
            "max_score": score,
            "knowledge_id": f"K{index}",
            "parts": [{
                "part_id": pid,
                "part_score": score,
                "steps": [{
                    "step_id": f"{pid}-S1",
                    "step_score": score,
                    "core_goal": "reason correctly",
                    "required_elements": ["reasoning"],
                    "allow_alternative_methods": True,
                }],
            }],
        })
        answer_questions.append({
            "question_id": qid,
            "canonical_answer": f"Answer {index}",
            "accepted_forms": [f"Answer {index}"],
            "method_variants": [],
            "parts": [{
                "part_id": pid,
                "answer": f"Answer {index}",
                "analysis": "analysis",
                "step_milestones": ["reasoning"],
            }],
        })
    return {
        "rubric": {"exam_title": "Editor exam", "total_score": 100, "questions": rubric_questions},
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
    answer.write_text(json.dumps(value["answer_key"], ensure_ascii=False), encoding="utf-8")
    return db.create_grading_session("Editor exam", str(rubric), str(answer))


@pytest.fixture
def editor_env(tmp_path: Path):
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(
        JobStore(tmp_path / "grading.db"),
        max_workers=1,
        interrupted_input_root=tmp_path / "uploaded",
    )
    manager.register("config_generation", lambda context: {"session_id": context.payload["session_id"], "outcome": "complete"})
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


def test_new_draft_editor_is_truthfully_unconfigured(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = create_session_draft(db, tmp_path / "uploaded", name="Draft exam")

    response = client.get(f"/api/sessions/{session_id}/config/editor")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": session_id,
        "configured": False,
        "revision": body["revision"],
        "rows": [],
        "total_score": 0,
        "issues": [],
        "source": None,
    }
    _assert_safe_editor(body, tmp_path)


def test_configured_editor_has_canonical_revision_rows_and_safe_source(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    source = tmp_path / "private" / "teacher-paper.docx"
    db.bind_grading_session_source(
        session_id,
        source_paper_path=str(source),
        source_paper_sha256="a" * 64,
    )

    body = client.get(f"/api/sessions/{session_id}/config/editor").json()

    assert body["configured"] is True
    assert body["total_score"] == 100
    assert len(body["rows"]) == 6
    assert body["source"] == {
        "safe_filename": "teacher-paper.docx",
        "suffix": ".docx",
        "sha256_prefix": "a" * 12,
    }
    first_revision = body["revision"]
    db.bind_grading_session_source(
        session_id,
        source_paper_path=str(source),
        source_paper_sha256="b" * 64,
    )
    assert client.get(f"/api/sessions/{session_id}/config/editor").json()["revision"] != first_revision
    _assert_safe_editor(body, tmp_path)


def test_put_is_strict_reports_stable_row_issues_and_noop(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()

    extra = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [], "commands": [], "rubric": {}},
    )
    assert extra.status_code == 422

    invalid = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [{"row_id": "missing", "score": 1}], "commands": []},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_config_editor"
    issue = invalid.json()["error"]["details"]["issues"][0]
    assert set(issue) == {"code", "severity", "row_id", "field", "message"}
    assert issue["row_id"] == "missing"

    old_paths = db.get_grading_session(session_id)
    noop = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [], "commands": []},
    )
    assert noop.status_code == 200
    assert noop.json()["save_result"]["config_saved"] is False
    current = db.get_grading_session(session_id)
    assert current["rubric_path"] == old_paths["rubric_path"]
    assert current["answer_key_path"] == old_paths["answer_key_path"]


def test_put_saves_once_preserves_source_and_rejects_stale_revision(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    db.bind_grading_session_source(session_id, source_paper_path="papers/original.docx", source_paper_sha256="c" * 64)
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
    current = db.get_grading_session(session_id)
    assert current["source_paper_path"] == "papers/original.docx"
    assert current["source_paper_sha256"] == "c" * 64
    _assert_safe_editor(body, tmp_path)

    stale = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [], "commands": []},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_revision_conflict"


def test_active_config_job_rejects_editor_save(editor_env) -> None:
    client, db, manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    old_paths = db.get_grading_session(session_id)
    job = manager.store.create_claimed_config_job(
        {"session_id": session_id, "mode": "generate"}
    )

    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={
            "revision": first["revision"],
            "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "changed"}],
            "commands": [],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_in_progress"
    current = db.get_grading_session(session_id)
    assert current["rubric_path"] == old_paths["rubric_path"]
    assert current["answer_key_path"] == old_paths["answer_key_path"]
    manager.store.finish(job.id, "failed", error="test cleanup")


def test_manual_publish_failure_cleans_only_new_file_and_keeps_binding(editor_env, monkeypatch) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    old = db.get_grading_session(session_id)
    sentinel = tmp_path / "uploaded" / "sentinel.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep", encoding="utf-8")
    from backend.config_workspace.secure_fs import SecureRootFilesystem
    original = SecureRootFilesystem.atomic_write_bytes
    calls = 0

    def fail_second(self, path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private second-file failure")
        return original(self, path, content)

    monkeypatch.setattr(SecureRootFilesystem, "atomic_write_bytes", fail_second)
    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "x"}], "commands": []},
    )
    assert response.status_code == 500
    current = db.get_grading_session(session_id)
    assert (current["rubric_path"], current["answer_key_path"]) == (old["rubric_path"], old["answer_key_path"])
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in sentinel.parent.iterdir()) == ["sentinel.json"]
    assert "private second-file failure" not in response.text


def test_database_failure_cleans_both_new_files_and_keeps_binding(editor_env, monkeypatch) -> None:
    client, db, manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    old = db.get_grading_session(session_id)

    def fail_db(*_args, **_kwargs):
        raise RuntimeError("private database failure")

    monkeypatch.setattr(manager.store, "update_session_config_if_idle", fail_db)
    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "x"}], "commands": []},
    )
    assert response.status_code == 500
    current = db.get_grading_session(session_id)
    assert (current["rubric_path"], current["answer_key_path"]) == (old["rubric_path"], old["answer_key_path"])
    assert list((tmp_path / "uploaded").glob("*editor*")) == []
    assert "private database failure" not in response.text


@pytest.mark.parametrize(
    ("mapping_result", "expected_status"),
    [
        ("not_present", "not_present"),
        ("refreshed", "refreshed"),
        (RuntimeError("private mapping path"), "reconfirm_required"),
    ],
)
def test_mapping_result_is_truthful_and_post_save_failure_is_safe(editor_env, monkeypatch, mapping_result, expected_status) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    import backend.api.routers.config as config_router

    def mapping(*_args, **_kwargs):
        if isinstance(mapping_result, Exception):
            raise mapping_result
        return mapping_result

    monkeypatch.setattr(config_router, "refresh_template_mapping_from_session", mapping, raising=False)
    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "changed"}], "commands": []},
    )
    assert response.status_code == 200
    assert response.json()["save_result"]["mapping_status"] == expected_status
    assert "private mapping path" not in response.text


def test_editor_mapping_keeps_config_job_submission_outside_the_session_claim(
    editor_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    mapping_started = threading.Event()
    release_mapping = threading.Event()
    submit_finished = threading.Event()
    responses: list[object] = []

    def blocked_mapping(*_args, **_kwargs):
        mapping_started.set()
        assert release_mapping.wait(timeout=5)
        return "not_present"

    monkeypatch.setattr(
        "backend.api.routers.config.refresh_template_mapping_from_session",
        blocked_mapping,
    )

    save_thread = threading.Thread(
        target=lambda: responses.append(client.put(
            f"/api/sessions/{session_id}/config/editor",
            json={
                "revision": first["revision"],
                "edits": [{
                    "row_id": first["rows"][0]["row_id"],
                    "standard_answer": "serialized",
                }],
                "commands": [],
            },
        )),
        daemon=True,
    )
    save_thread.start()
    assert mapping_started.wait(timeout=5)

    submitted: list[object] = []

    def submit_job() -> None:
        submitted.append(manager.submit(
            "config_generation",
            {"session_id": session_id, "mode": "generate"},
        ))
        submit_finished.set()

    submit_thread = threading.Thread(target=submit_job, daemon=True)
    submit_thread.start()
    assert not submit_finished.wait(timeout=0.2)
    manager.store.assert_config_session_idle(session_id)

    release_mapping.set()
    save_thread.join(timeout=5)
    submit_thread.join(timeout=5)
    assert responses and responses[0].status_code == 200
    assert submitted and submit_finished.is_set()


def test_existing_template_with_missing_files_requires_reconfirmation(editor_env) -> None:
    client, db, _manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    db.upsert_session_template(
        session_id,
        str(tmp_path / "missing-front.png"),
        str(tmp_path / "missing-back.png"),
    )
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    response = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={
            "revision": first["revision"],
            "edits": [
                {"row_id": first["rows"][0]["row_id"], "standard_answer": "changed"}
            ],
            "commands": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["save_result"]["mapping_status"] == "reconfirm_required"


def test_refine_accepts_only_revision_and_server_commands_and_rejects_old_revision(editor_env) -> None:
    client, db, manager, tmp_path = editor_env
    session_id = _write_config(tmp_path, db)
    first = client.get(f"/api/sessions/{session_id}/config/editor").json()
    command = {"kind": "split", "question_id": "Q1", "count": 2, "style": "subquestion"}

    nested = client.post(
        f"/api/sessions/{session_id}/config/editor/refine",
        json={"revision": first["revision"], "commands": [command], "config": _payload()},
    )
    assert nested.status_code == 422

    refine_request = {
        "revision": first["revision"],
        "commands": [command],
        "client_request_token": "e" * 32,
    }
    submitted = client.post(
        f"/api/sessions/{session_id}/config/editor/refine",
        json=refine_request,
    )
    replay = client.post(
        f"/api/sessions/{session_id}/config/editor/refine",
        json=refine_request,
    )
    assert submitted.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["id"] == submitted.json()["id"]
    assert submitted.json()["payload"] == {"session_id": session_id, "mode": "refine"}
    stored = manager.get(submitted.json()["id"])
    assert stored is not None
    assert set(stored.payload) == {
        "session_id", "mode", "input_id",
        "client_request_token", "client_request_fingerprint",
    }

    manager.wait(submitted.json()["id"], timeout=5)
    saved = client.put(
        f"/api/sessions/{session_id}/config/editor",
        json={"revision": first["revision"], "edits": [{"row_id": first["rows"][0]["row_id"], "standard_answer": "new"}], "commands": []},
    )
    assert saved.status_code == 200
    conflict = client.post(
        f"/api/sessions/{session_id}/config/editor/refine",
        json={"revision": first["revision"], "commands": [command]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "config_revision_conflict"


def test_db_conditional_publish_requires_both_old_paths_and_preserves_source(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = _write_config(tmp_path, db)
    old = db.get_grading_session(session_id)
    db.bind_grading_session_source(session_id, source_paper_path="papers/source.pdf", source_paper_sha256="d" * 64)

    assert db.publish_grading_session_config(
        session_id,
        rubric_path="new-rubric.json",
        answer_key_path="new-answer.json",
        expected_rubric_path="stale-rubric.json",
        expected_answer_key_path=old["answer_key_path"],
    ) is False
    current = db.get_grading_session(session_id)
    assert current["rubric_path"] == old["rubric_path"]
    assert current["answer_key_path"] == old["answer_key_path"]
    assert current["source_paper_path"] == "papers/source.pdf"
    assert current["source_paper_sha256"] == "d" * 64
