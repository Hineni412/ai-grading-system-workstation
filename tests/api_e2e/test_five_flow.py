from __future__ import annotations

from pathlib import Path

import pytest

import path_manager


@pytest.fixture(autouse=True)
def _controlled_synthetic_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tests/conftest.py isolates the global PathManager into its own temporary
    # directory, so the harness data root (tmp_path / "synthetic_data") falls
    # outside every controlled root and resolve_stored_file_path rejects the
    # session's stored rubric/template paths during region commit. Point the
    # data root at tmp_path so the whole synthetic tree is controlled here.
    monkeypatch.setattr(path_manager.get_path_manager(), "_data_root", tmp_path)


def test_api_five_flow_persists_reviewed_score_in_downloaded_report(
    api_e2e,
) -> None:
    session_id = api_e2e.create_configured_session()
    assert sorted(api_e2e.controls.fake_llm_calls) == [
        "question:Q1",
        "question:Q2",
        "question:Q3",
        "question:Q4",
        "question:Q5",
        "question:Q6",
    ]

    config_response = api_e2e.client.get(f"/api/sessions/{session_id}/config")
    assert config_response.status_code == 200
    config = config_response.json()
    config_questions = config["rubric"]["questions"]
    assert [question["question_id"] for question in config_questions] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert [question["max_score"] for question in config_questions] == [
        17,
        17,
        17,
        16,
        16,
        17,
    ]
    assert Path(config["rubric_path"]).is_file()
    assert Path(config["answer_key_path"]).is_file()
    assert api_e2e.db.templates.is_template_ready(session_id) is True
    regions_response = api_e2e.client.get(f"/api/sessions/{session_id}/regions")
    assert regions_response.status_code == 200
    regions = regions_response.json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"

    scan_job = api_e2e.scan(session_id)
    assert scan_job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }
    assert api_e2e.paper_statuses(session_id) == []

    grading_job = api_e2e.grade(session_id)
    assert grading_job["result"]["state"] == "completed"
    assert grading_job["result"]["summary"]["graded"] == 1
    assert grading_job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(session_id) == ["failed", "graded"]
    assert api_e2e.result_scores(session_id) == {"SYN-001": 85.0}

    recovery_job = api_e2e.grade(session_id, failed_only=True)
    assert recovery_job["result"]["state"] == "completed"
    assert recovery_job["result"]["summary"]["graded"] == 1
    assert recovery_job["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(session_id) == {
        "SYN-001": 85.0,
        "SYN-002": 70.0,
    }

    questions_response = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions"
    )
    assert questions_response.status_code == 200
    q1 = next(
        item
        for item in questions_response.json()["items"]
        if item["question_id"] == "Q1"
    )
    assert q1["needs_review_count"] == 1

    items_response = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions/Q1/items"
    )
    assert items_response.status_code == 200
    first = next(
        item
        for item in items_response.json()["items"]
        if item["student_code"] == "SYN-001"
    )
    assert first["score_awarded"] == 12.0
    assert first["max_score"] == 17.0
    assert first["needs_review"] is True

    confirmed = api_e2e.client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": first["result_id"],
                    "detail_id": first["detail_id"],
                    "score_awarded": 17,
                    "deduction_reason": "synthetic teacher confirmation",
                }
            ]
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["updated_details"] == 1
    assert confirmed.json()["updated_results"] == 1
    assert api_e2e.result_scores(session_id)["SYN-001"] == 90.0

    cleared_questions = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions"
    )
    assert cleared_questions.status_code == 200
    cleared_q1 = next(
        item
        for item in cleared_questions.json()["items"]
        if item["question_id"] == "Q1"
    )
    assert cleared_q1["needs_review_count"] == 0
    cleared_items = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions/Q1/items"
    )
    assert cleared_items.status_code == 200
    assert cleared_items.json() == {"items": [], "total": 0}

    exported = api_e2e.client.post(
        f"/api/sessions/{session_id}/reports/export"
    )
    assert exported.status_code == 202
    report_job = api_e2e.poll_job(exported.json()["id"], "succeeded")
    assert report_job["result"] == {
        "session_id": session_id,
        "filename": report_job["result"]["filename"],
        "download_url": f"/api/jobs/{report_job['id']}/download",
    }
    assert Path(report_job["result"]["filename"]).name == report_job["result"][
        "filename"
    ]
    assert str(api_e2e.paths.data_root) not in str(report_job["result"])

    download = api_e2e.client.get(report_job["result"]["download_url"])
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert api_e2e.xlsx_score(download.content, "SYN-001") == 90.0
