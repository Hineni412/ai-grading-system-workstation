from __future__ import annotations


def test_scan_failure_can_be_retried_without_half_published_state(
    api_e2e,
    prepared_session_id,
) -> None:
    api_e2e.controls.scan_failures_remaining = 1
    failed = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert failed.status_code == 202
    first_job = api_e2e.poll_job(failed.json()["id"], "failed")
    assert first_job["error"] == "Job failed; see local logs for details."
    assert not (
        api_e2e.paths.templates_dir
        / f"session_{prepared_session_id}"
        / "scan_analysis_latest.json"
    ).exists()
    assert api_e2e.paper_statuses(prepared_session_id) == []

    retried = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert retried.status_code == 202
    second_job = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert second_job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }


def test_partial_grading_uses_completed_semantics_and_failed_only_recovery(
    api_e2e,
    scanned_session_id,
) -> None:
    submitted = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"grading_mode": "full_paper", "enhance_images": False},
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["state"] == "completed"
    assert job["result"]["summary"]["graded"] == 1
    assert job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(scanned_session_id) == ["failed", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {"SYN-001": 85.0}
    assert api_e2e.result_review_flags(scanned_session_id) == {
        "SYN-001": True,
    }
    assert api_e2e.result_details(scanned_session_id) == {
        "SYN-001": [
            ("Q1", 12.0),
            ("Q2", 17.0),
            ("Q3", 17.0),
            ("Q4", 17.0),
            ("Q5", 17.0),
            ("Q6", 5.0),
        ]
    }
    successful_result_ids = api_e2e.result_ids(scanned_session_id)

    retried = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"failed_only": True, "enhance_images": False},
    )
    assert retried.status_code == 202
    recovered = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert recovered["result"]["state"] == "completed"
    assert recovered["result"]["summary"]["graded"] == 1
    assert recovered["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(scanned_session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {
        "SYN-001": 85.0,
        "SYN-002": 70.0,
    }
    assert api_e2e.result_ids(scanned_session_id)["SYN-001"] == (
        successful_result_ids["SYN-001"]
    )
    details = api_e2e.result_details(scanned_session_id)
    assert details["SYN-001"] == [
        ("Q1", 12.0),
        ("Q2", 17.0),
        ("Q3", 17.0),
        ("Q4", 17.0),
        ("Q5", 17.0),
        ("Q6", 5.0),
    ]
    assert [question_id for question_id, _score in details["SYN-002"]] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert sum(score for _question_id, score in details["SYN-002"]) == 70.0
