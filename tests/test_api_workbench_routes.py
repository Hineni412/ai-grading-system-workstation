from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Authorization Bearer abcdefghijklmnopqrstuvwxyz",
        "ordinary unknown stage",
        "graded=-1 failed=0",
        None,
    ],
)
def test_public_diagnostic_whitelist_rejects_unrecognized_text(
    unsafe_text: object,
) -> None:
    from backend.public_data import sanitize_public_diagnostic_text

    assert sanitize_public_diagnostic_text(unsafe_text) is None


@pytest.mark.parametrize(
    "safe_text",
    [
        "processing",
        "total=3",
        "graded=3 failed=1",
        "matched=2 issues=1 pages=4",
        "processing batch 2 of 5",
        "batch 2/5",
    ],
)
def test_public_diagnostic_whitelist_keeps_known_safe_templates(
    safe_text: str,
) -> None:
    from backend.public_data import sanitize_public_diagnostic_text

    assert sanitize_public_diagnostic_text(safe_text) == safe_text


def test_public_mapping_reuses_checks_without_sharing_output_containers(monkeypatch) -> None:
    from collections import Counter
    from backend import public_data

    checked = Counter()
    original = public_data._contains_filesystem_token

    def tracked(value: str, **kwargs) -> bool:
        checked[value] += 1
        return original(value, **kwargs)

    monkeypatch.setattr(public_data, "_contains_filesystem_token", tracked)
    item = {
        "skill": "求直角边长",
        "formula": r"\sqrt{a^2-b^2}",
        "url": "/api/jobs/8",
        "internal": "C:/private/result.json",
        "api_key": "secret-value",
        "source_path": "private-value",
    }
    result = public_data.sanitize_public_mapping({"students": [item, item]})
    expected = {"skill": item["skill"], "formula": item["formula"], "url": item["url"]}
    assert result == {"students": [expected, expected]}
    assert checked["求直角边长"] == checked["C:/private/result.json"] == 1
    result["students"][0]["skill"] = "changed"
    assert result["students"][1]["skill"] == item["skill"] == "求直角边长"


def test_public_mapping_classification_cache_lives_only_for_one_call(monkeypatch) -> None:
    from backend import public_data

    value = {"items": [{"text": "same text"}, {"text": "same text"}]}
    assert public_data.sanitize_public_mapping(value) == value
    monkeypatch.setattr(public_data, "_contains_filesystem_token", lambda value: True)
    assert public_data.sanitize_public_mapping(value) == {"items": []}


@pytest.fixture
def workbench_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_scan_grading_workspace,
    )
    from backend.api.routers.workbench import router as workbench_router
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.scan_grading.workspace import ScanGradingWorkspaceError
    from db_manager import DBManager

    class _UnavailableWorkspace:
        def get_workspace(self, session_id: int) -> dict:
            raise ScanGradingWorkspaceError("workspace unavailable in tests")

        def get_preflight(self, session_id: int) -> dict:
            raise ScanGradingWorkspaceError("workspace unavailable in tests")

    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    db_path = db_dir / "grading.db"
    db = DBManager(db_path)
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps({"questions": [{"question_id": "Q1", "max_score": 10}]}),
        encoding="utf-8",
    )

    with sqlite3.connect(db_path) as conn:
        session_ids = [
            int(
                conn.execute(
                    """
                    INSERT INTO grading_sessions (
                        session_name, rubric_path, answer_key_path, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'completed', ?, ?)
                    """,
                    (
                        f"Exam {index}",
                        str(rubric_path),
                        str(tmp_path / "answer.json"),
                        f"2026-07-{index:02d} 08:00:00",
                        f"2026-07-{index:02d} 09:00:00",
                    ),
                ).lastrowid
            )
            for index in (1, 2, 3)
        ]
        session_id = session_ids[-1]
        students = []
        for code, name in (("S001", "Alice"), ("S002", "Bob"), ("S003", "Cara")):
            students.append(
                int(
                    conn.execute(
                        "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, 'Class 1')",
                        (code, name),
                    ).lastrowid
                )
            )

        matched_paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
                """,
                (session_id, "front-a.jpg", "back-a.jpg", students[0]),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id,
                match_status, processing_status, error_message
            ) VALUES (?, ?, ?, 'Unknown paper', NULL, 'unmatched', 'pending', NULL)
            """,
            (session_id, "C:/private/front.jpg", "C:/private/back.jpg"),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id,
                match_status, processing_status, error_message
            ) VALUES (?, ?, ?, 'Cara', ?, 'matched', 'failed', ?)
            """,
            (
                session_id,
                "front-c.jpg",
                "back-c.jpg",
                students[2],
                "Cannot read C:/private/cara.png",
            ),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (
                session_id, student_id, attendance_status, source_reason
            ) VALUES (?, ?, 'scan_issue', ?)
            """,
            (session_id, students[1], "See C:/private/scan.png"),
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 1, '{}')
                """,
                (session_id, students[0], matched_paper_id),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary, confidence_score
            ) VALUES (?, 'Q1', 8, '需复核：字迹不清', '["K1"]', '需复核', 'unclear', 55)
            """,
            (result_id,),
        )
        conn.commit()

    manager = JobManager(JobStore(db_path), max_workers=1)
    manager.store.create_job(
        "grading_run",
        {"session_id": session_id, "path": "C:/private/source"},
    )
    latest_job = manager.store.create_job(
        "report_export",
        {"session_id": session_id, "token": "private-token"},
    )

    app = create_app()
    app.include_router(workbench_router)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_scan_grading_workspace] = (
        lambda: _UnavailableWorkspace()
    )
    with TestClient(app) as client:
        try:
            yield client, session_id, db_path, latest_job.id
        finally:
            manager.shutdown()


def test_overview_returns_existing_counts_and_recent_sessions(workbench_client) -> None:
    client, session_id, _db_path, latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id, "recent_limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_session"]["id"] == session_id
    assert payload["progress"]["unmatched_papers"] == 1
    assert payload["review"] == {"question_count": 1, "item_count": 1}
    assert payload["anomalies"] == {
        "unmatched_papers": 1,
        "scan_issue_students": 1,
        "failed_papers": 1,
    }
    assert len(payload["recent_sessions"]) == 2
    assert payload["recent_jobs"][0]["id"] == latest_job_id
    assert "rubric_path" not in response.text
    assert "answer_key_path" not in response.text
    assert "payload" not in response.text
    assert "result" not in response.text
    assert "private-token" not in response.text
    assert "C:/private" not in response.text


def test_overview_review_count_honors_teacher_lock_in_frozen_batch(
    workbench_client,
) -> None:
    """Homepage review count must match the review queue口径.

    Regression: a legacy AI row still carrying the "需复核" marker kept
    counting as pending on the homepage even after the teacher confirmed it
    via a score lock, because the overview ignored the frozen batch context.
    """
    from backend.api.dependencies import get_scan_grading_workspace

    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        student_id = int(
            conn.execute(
                "SELECT id FROM students WHERE student_code = 'S001'"
            ).fetchone()[0]
        )

    class _FrozenWorkspace:
        def get_workspace(self, requested_session_id: int) -> dict:
            assert int(requested_session_id) == int(session_id)
            return {
                "upload_batch": {
                    "state": "frozen",
                    "batch_id": "batch-frozen",
                }
            }

        def get_preflight(self, requested_session_id: int) -> dict:
            assert int(requested_session_id) == int(session_id)
            return {
                "scan_batch_id": "batch-frozen",
                "groups": [
                    {
                        "id": "g-1",
                        "student_id": student_id,
                        "student_name": "Alice",
                        "front_media_url": "",
                        "back_media_url": None,
                    }
                ],
                "decisions": [],
                "issues": [],
            }

    client.app.dependency_overrides[get_scan_grading_workspace] = (
        lambda: _FrozenWorkspace()
    )

    pending = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )
    assert pending.status_code == 200
    assert pending.json()["review"] == {"question_count": 1, "item_count": 1}

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id, revision,
                created_at, updated_at
            ) VALUES (?, 'batch-frozen', ?, 'Q1', 8, 10, NULL,
                      'answer_region', 1, 1,
                      '2026-08-20 11:17:46', '2026-08-20 11:17:46')
            """,
            (session_id, student_id),
        )
        conn.commit()

    confirmed = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["review"] == {"question_count": 0, "item_count": 0}


def test_overview_without_session_does_not_choose_one(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    payload = client.get("/api/workbench/overview").json()

    assert payload["current_session"] is None
    assert payload["progress"] is None
    assert payload["review"] is None
    assert payload["anomalies"] is None
    assert payload["recent_jobs"] == []
    assert len(payload["recent_sessions"]) == 3


def test_student_delete_returns_unlinked_graded_paper_to_pending_workbench_state(
    workbench_client,
) -> None:
    client, _session_id, db_path, _latest_job_id = workbench_client

    with sqlite3.connect(db_path) as conn:
        rubric_path = conn.execute(
            "SELECT rubric_path FROM grading_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        session_id = int(
            conn.execute(
                """
                INSERT INTO grading_sessions (
                    session_name, rubric_path, answer_key_path, status
                ) VALUES ('Delete progress regression', ?, '', 'completed')
                """,
                (rubric_path,),
            ).lastrowid
        )
        student_id = int(
            conn.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('S-DELETE-PROGRESS', 'Delete Progress Student', 'Class D')
                """
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status, error_message
                ) VALUES (?, 'front-delete.jpg', '', 'Delete Progress Student', ?,
                          'matched', 'graded', 'old grading result')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 0, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        conn.commit()

    from grading_run_store import GradingRunStore

    run_store = GradingRunStore(db_path)
    run = run_store.begin(session_id, "a" * 64, "full_paper")
    run_store.add_item(
        run.id,
        source_label="delete-progress",
        student_id=student_id,
        paper_fingerprint="b" * 64,
        config_fingerprint="a" * 64,
        status="graded",
        paper_id=paper_id,
        result_id=result_id,
    )
    run_store.finish(run.run_token, "completed")

    impact = client.get(f"/api/students/{student_id}/deletion-impact").json()
    deleted = client.delete(
        f"/api/students/{student_id}",
        params={
            "expected_revision": impact["roster_revision"],
            "confirmed": "true",
        },
    )
    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )

    assert deleted.status_code == 200
    assert overview.status_code == 200
    assert overview.json()["progress"] == {
        "total_papers": 1,
        "matched_papers": 0,
        "unmatched_papers": 1,
        "graded_papers": 0,
        "failed_papers": 0,
        "grading_papers": 0,
        "needs_human_review": 0,
        "absent_students": 0,
        "scan_issue_students": 0,
        "progress_percent": 0.0,
    }
    assert overview.json()["anomalies"] == {
        "unmatched_papers": 1,
        "scan_issue_students": 0,
        "failed_papers": 0,
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT student_id, match_status, processing_status, error_message
            FROM exam_papers WHERE id = ?
            """,
            (paper_id,),
        ).fetchone() == (None, "student_deleted", "pending", None)


@pytest.mark.parametrize("recent_limit", [1, 20])
def test_overview_accepts_recent_limit_bounds(
    workbench_client,
    recent_limit: int,
) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"recent_limit": recent_limit},
    )

    assert response.status_code == 200
    assert len(response.json()["recent_sessions"]) == min(recent_limit, 3)


@pytest.mark.parametrize("recent_limit", [0, 21])
def test_overview_rejects_recent_limit_outside_bounds(
    workbench_client,
    recent_limit: int,
) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"recent_limit": recent_limit},
    )

    assert response.status_code == 422


def test_overview_returns_404_for_missing_session(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": 9999},
        headers={"x-request-id": "rid-missing-workbench"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing-workbench"


def test_empty_session_overview_uses_existing_zero_counts(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client
    first_session_id = client.get("/api/sessions").json()["items"][-1]["id"]

    payload = client.get(
        "/api/workbench/overview",
        params={"session_id": first_session_id},
    ).json()

    assert payload["progress"]["total_papers"] == 0
    assert payload["review"] == {"question_count": 0, "item_count": 0}
    assert payload["anomalies"] == {
        "unmatched_papers": 0,
        "scan_issue_students": 0,
        "failed_papers": 0,
    }
    assert payload["recent_jobs"] == []


def test_anomalies_are_stable_paginated_filterable_and_sanitized(
    workbench_client,
) -> None:
    client, session_id, _db_path, _latest_job_id = workbench_client

    first_page = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page": 1, "page_size": 2},
    )
    second_page = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page": 2, "page_size": 2},
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert first_page.json()["total_pages"] == 2
    items = first_page.json()["items"] + second_page.json()["items"]
    assert [(item["anomaly_type"], item["anomaly_id"]) for item in items] == sorted(
        (item["anomaly_type"], item["anomaly_id"]) for item in items
    )
    assert {item["anomaly_type"] for item in items} == {
        "unmatched_paper",
        "scan_issue",
        "grading_failed",
    }
    assert all(item["detail"] is None for item in items if item["anomaly_type"] != "unmatched_paper")
    combined_text = first_page.text + second_page.text
    assert "front_image" not in combined_text
    assert "back_image" not in combined_text
    assert "source_reason" not in combined_text
    assert "C:/private" not in combined_text

    filtered = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "scan_issue"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["anomaly_type"] == "scan_issue"


def test_anomaly_display_names_never_publish_ocr_text(workbench_client) -> None:
    ocr_name = 'opaque-student-marker'
    marker = 'opaque-student-marker'
    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        unmatched_id = int(
            conn.execute(
                "SELECT id FROM exam_papers WHERE session_id = ? AND match_status <> 'matched'",
                (session_id,),
            ).fetchone()[0]
        )
        failed_id = int(
            conn.execute(
                "SELECT id FROM exam_papers WHERE session_id = ? AND processing_status = 'failed'",
                (session_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE exam_papers SET ocr_name = ? WHERE id = ?",
            (ocr_name, unmatched_id),
        )
        conn.execute(
            "UPDATE exam_papers SET ocr_name = ?, student_id = NULL WHERE id = ?",
            (ocr_name, failed_id),
        )
        conn.commit()

    payload = client.get(f"/api/sessions/{session_id}/anomalies").json()
    by_id = {item["anomaly_id"]: item for item in payload["items"]}

    assert by_id[f"unmatched_paper:{unmatched_id:020d}"]["display_name"] == (
        f"未匹配试卷 #{unmatched_id}"
    )
    assert by_id[f"grading_failed:{failed_id:020d}"]["display_name"] == (
        f"批改失败试卷 #{failed_id}"
    )
    assert marker not in str(by_id)


def test_anomaly_display_name_uses_controlled_labels_for_known_students(
    workbench_client,
) -> None:
    client, session_id, _db_path, _latest_job_id = workbench_client

    payload = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed"},
    ).json()

    failed_item = payload["items"][0]
    paper_id = int(failed_item["anomaly_id"].split(":", 1)[1])
    assert failed_item["display_name"] == f"批改失败试卷 #{paper_id}"

    scan_payload = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "scan_issue"},
    ).json()
    scan_item = scan_payload["items"][0]
    attendance_id = int(scan_item["anomaly_id"].split(":", 1)[1])
    assert scan_item["display_name"] == f"扫描异常记录 #{attendance_id}"


def test_grading_failure_anomalies_match_legacy_retry_eligibility(
    workbench_client,
) -> None:
    from db_manager import DBManager

    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        stale_grading_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name,
                    match_status, processing_status
                ) VALUES (?, 'stale-front.jpg', 'stale-back.jpg', ?, 'matched', 'grading')
                """,
                (session_id, "token=stale-private-token"),
            ).lastrowid
        )
        degraded_ids: list[int] = []
        degraded_payloads = [
            {"hybrid_batch_fallback": [{"error": "private-fallback"}]},
            {"grading_completeness": {"status": "incomplete"}},
            {"grading_completeness": {"status": "invalid"}},
        ]
        for index, raw_payload in enumerate(degraded_payloads, start=1):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name) VALUES (?, ?)",
                    (f"D{index:03d}", f"Known Student {index}"),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"degraded-{index}-front.jpg",
                        f"degraded-{index}-back.jpg",
                        f"opaque-ocr-marker-{index}",
                        student_id,
                    ),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 0, 1, ?)
                """,
                (session_id, student_id, paper_id, json.dumps(raw_payload)),
            )
            degraded_ids.append(paper_id)
        conn.commit()

    legacy_failed_ids = [
        int(row["paper_id"])
        for row in DBManager(db_path).list_failed_papers(session_id)
    ]
    response = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed", "page_size": 100},
    )
    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    ).json()

    assert response.status_code == 200
    payload = response.json()
    anomaly_ids = [
        int(item["anomaly_id"].split(":", 1)[1])
        for item in payload["items"]
    ]
    assert anomaly_ids == sorted(legacy_failed_ids)
    assert stale_grading_id in anomaly_ids
    assert set(degraded_ids).issubset(anomaly_ids)
    assert payload["total"] == len(legacy_failed_ids)
    assert overview["anomalies"]["failed_papers"] == payload["total"]
    assert overview["progress"]["failed_papers"] == 1
    assert payload["items"] == sorted(
        payload["items"], key=lambda item: item["anomaly_id"]
    )
    assert "stale-private-token" not in response.text
    assert "opaque-ocr-marker" not in response.text
    stale_item = next(
        item
        for item in payload["items"]
        if item["anomaly_id"] == f"grading_failed:{stale_grading_id:020d}"
    )
    assert stale_item["display_name"] == f"批改失败试卷 #{stale_grading_id}"


def test_failure_lists_keep_one_row_per_paper_with_historical_results(
    workbench_client,
) -> None:
    from db_manager import DBManager

    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        failed_paper = conn.execute(
            """
            SELECT id, student_id FROM exam_papers
            WHERE session_id = ? AND processing_status = 'failed'
            """,
            (session_id,),
        ).fetchone()
        assert failed_paper is not None
        failed_paper_id = int(failed_paper[0])
        student_id = int(failed_paper[1])
        conn.executemany(
            """
            INSERT INTO session_results (
                session_id, student_id, paper_id, total_score,
                student_score, needs_human_review, raw_json
            ) VALUES (?, ?, ?, 10, 0, 0, ?)
            """,
            [
                (session_id, student_id, failed_paper_id, "{}"),
                (
                    session_id,
                    student_id,
                    failed_paper_id,
                    json.dumps({"grading_completeness": {"status": "invalid"}}),
                ),
            ],
        )
        conn.commit()

    db = DBManager(db_path)
    legacy_ids = [
        int(row["paper_id"]) for row in db.list_failed_papers(session_id)
    ]
    detailed_ids = [
        int(row["paper_id"])
        for row in db.list_failed_papers_detailed(session_id)
    ]
    first = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed", "page": 1, "page_size": 1},
    ).json()
    second = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed", "page": 1, "page_size": 1},
    ).json()

    assert legacy_ids == [failed_paper_id]
    assert detailed_ids == [failed_paper_id]
    assert first == second
    assert first["total"] == 1
    assert first["total_pages"] == 1
    assert len(first["items"]) == 1
    assert first["items"][0]["anomaly_id"] == (
        f"grading_failed:{failed_paper_id:020d}"
    )


def test_anomaly_detail_rejects_opaque_diagnostic_text(workbench_client) -> None:
    unsafe_text = 'diagnostic payload=private-payload'
    marker = 'private-payload'
    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers SET error_message = ?
            WHERE session_id = ? AND processing_status = 'failed'
            """,
            (unsafe_text, session_id),
        )
        conn.commit()

    response = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["detail"] is None
    assert marker not in response.text


def test_recent_job_detail_rejects_opaque_diagnostic_text(workbench_client) -> None:
    unsafe_text = 'diagnostic error=private-error'
    marker = 'private-error'
    client, session_id, db_path, latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET detail = ? WHERE id = ?",
            (unsafe_text, latest_job_id),
        )
        conn.commit()

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )

    assert response.status_code == 200
    assert response.json()["recent_jobs"][0]["detail"] == ""
    assert marker not in response.text


def test_workbench_preserves_short_non_sensitive_diagnostic_text(
    workbench_client,
) -> None:
    client, session_id, db_path, latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers SET error_message = 'processing'
            WHERE session_id = ? AND processing_status = 'failed'
            """,
            (session_id,),
        )
        conn.execute(
            "UPDATE jobs SET detail = 'processing batch 2 of 5' WHERE id = ?",
            (latest_job_id,),
        )
        conn.commit()

    anomalies = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed"},
    ).json()
    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    ).json()

    assert anomalies["items"][0]["detail"] == "processing"
    assert overview["recent_jobs"][0]["detail"] == "processing batch 2 of 5"


@pytest.mark.parametrize("page_size", [0, 101])
def test_anomalies_reject_invalid_page_size(workbench_client, page_size: int) -> None:
    client, session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page_size": page_size},
    )

    assert response.status_code == 422


def test_anomalies_return_404_for_missing_session(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get("/api/sessions/9999/anomalies")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_workbench_gets_do_not_change_database_fingerprint(workbench_client) -> None:
    client, session_id, db_path, _latest_job_id = workbench_client
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )
    anomalies = client.get(f"/api/sessions/{session_id}/anomalies")

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert overview.status_code == 200
    assert anomalies.status_code == 200
    assert after == before
