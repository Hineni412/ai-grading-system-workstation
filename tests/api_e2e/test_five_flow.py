from __future__ import annotations

from pathlib import Path


def test_api_five_flow_persists_config_template_and_regions(api_e2e) -> None:
    created = api_e2e.client.post(
        "/api/sessions",
        json={
            "name": "Synthetic E2E Exam",
            "rubric_path": str(api_e2e.paths.bootstrap_rubric),
            "answer_key_path": str(api_e2e.paths.bootstrap_answer),
        },
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    submitted = api_e2e.client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=api_e2e.config_request(),
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["outcome"] == "complete"

    config = api_e2e.client.get(f"/api/sessions/{session_id}/config").json()
    assert config["rubric"]["questions"][0]["question_id"] == "Q1"
    assert Path(config["rubric_path"]).is_file()
    assert Path(config["answer_key_path"]).is_file()

    committed = api_e2e.bind_and_commit_template(session_id)
    snapshot_path = Path(committed.pop("snapshot_path"))
    assert snapshot_path.is_file()
    snapshot_path.resolve().relative_to(
        (api_e2e.paths.templates_dir / f"session_{session_id}").resolve()
    )
    assert committed == {
        "committed": True,
        "snapshot_pending": False,
        "issues": [],
        "region_count": 1,
        "error": None,
    }
    assert api_e2e.db.is_template_ready(session_id) is True
    regions = api_e2e.client.get(f"/api/sessions/{session_id}/regions").json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"
