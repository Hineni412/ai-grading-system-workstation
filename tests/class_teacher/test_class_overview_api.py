from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
            ),
        )
    )
    service.ensure_plaintext_ready()
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app), {
        "x-class-teacher-client": "class-teacher-browser-v1",
    }


def _create_subject(
    client: TestClient,
    headers: dict[str, str],
    operation_id: str,
    source_student_id: str,
    display_name: str,
) -> str:
    response = client.post(
        "/api/class-teacher/support/subjects",
        headers=headers,
        json={
            "operation_id": operation_id,
            "source_student_id": source_student_id,
            "display_name": display_name,
            "class_label": "合成一班",
        },
    )
    assert response.status_code == 200
    return str(response.json()["subject_id"])


def _create_record(
    client: TestClient,
    headers: dict[str, str],
    subject_id: str,
    operation_id: str,
    content: str,
    observed_at: str,
    record_kind: str = "fact",
    review_at: str | None = None,
    expires_at: str | None = None,
) -> None:
    response = client.post(
        f"/api/class-teacher/support/subjects/{subject_id}/records",
        headers=headers,
        json={
            "operation_id": operation_id,
            "record_kind": record_kind,
            "content": content,
            "scene": "合成场景",
            "source": "合成来源",
            "basis": None,
            "counterexample": None,
            "category": "general",
            "observed_at": observed_at,
            "review_at": review_at,
            "expires_at": expires_at,
        },
    )
    assert response.status_code == 200


def _utc_iso(local_date: date) -> str:
    return (
        datetime.combine(local_date, datetime.min.time())
        .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        .astimezone(UTC)
        .isoformat()
    )


def test_support_overview_ranks_due_reviews_first_and_limits_timeline(
    tmp_path: Path,
) -> None:
    client, headers = _client(tmp_path)
    due_subject = _create_subject(
        client, headers, "ov-subject-due", "synthetic-ov-001", "合成到期学生"
    )
    plain_subject = _create_subject(
        client, headers, "ov-subject-plain", "synthetic-ov-002", "合成普通学生"
    )
    soon = (date.today() + timedelta(days=3)).isoformat()
    later = (date.today() + timedelta(days=3 + 30)).isoformat()
    _create_record(
        client, headers, due_subject, "ov-record-due",
        "合成观察正文，需要到期复查。",
        "2026-08-01",
        record_kind="teacher_observation",
        review_at=soon,
        expires_at=later,
    )
    _create_record(
        client, headers, plain_subject, "ov-record-plain",
        "合成事实记录。",
        "2026-08-05",
    )

    response = client.get("/api/class-teacher/support/overview", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    payload = response.json()
    follow_ups = payload["follow_ups"]
    assert [item["subject_id"] for item in follow_ups] == [
        due_subject,
        plain_subject,
    ]
    assert follow_ups[0]["display_name"] == "合成到期学生"
    assert follow_ups[0]["next_review_at"] == _utc_iso(date.fromisoformat(soon))
    assert follow_ups[1]["next_review_at"] is None
    assert follow_ups[1]["active_record_count"] == 1
    recent = payload["recent_records"]
    assert [item["subject_id"] for item in recent] == [plain_subject, due_subject]
    assert recent[0]["display_name"] == "合成普通学生"
    assert recent[0]["excerpt"] == "合成事实记录。"
    assert payload["has_records"] is True

    empty_client, empty_headers = _client(tmp_path / "empty")
    empty = empty_client.get(
        "/api/class-teacher/support/overview", headers=empty_headers
    ).json()
    assert empty["follow_ups"] == []
    assert empty["recent_records"] == []
    assert empty["has_records"] is False


def test_support_overview_includes_plan_review_dates(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)
    subject_id = _create_subject(
        client, headers, "ov-plan-subject", "synthetic-ov-003", "合成计划学生"
    )
    soon = (date.today() + timedelta(days=5)).isoformat()
    plan_response = client.post(
        f"/api/class-teacher/support/subjects/{subject_id}/plans",
        headers=headers,
        json={
            "operation_id": "ov-plan-create",
            "goal": "合成支持目标",
            "support_actions": ["合成行动"],
            "review_at": soon,
            "action_id": None,
        },
    )
    assert plan_response.status_code == 200

    payload = client.get(
        "/api/class-teacher/support/overview", headers=headers
    ).json()

    assert payload["follow_ups"][0]["subject_id"] == subject_id
    assert payload["follow_ups"][0]["next_review_at"] == _utc_iso(date.fromisoformat(soon))
    assert payload["follow_ups"][0]["active_record_count"] == 0


def test_academic_overview_lists_sessions_stats_and_attention(
    tmp_path: Path,
) -> None:
    client, headers = _client(tmp_path)
    first = _create_subject(
        client, headers, "ov-ac-subject-1", "synthetic-ov-101", "合成学业甲"
    )
    second = _create_subject(
        client, headers, "ov-ac-subject-2", "synthetic-ov-102", "合成学业乙"
    )
    batch_response = client.post(
        "/api/class-teacher/evidence/batches",
        headers=headers,
        json={
            "operation_id": "ov-ac-confirm",
            "batch": {
                "source_kind": "confirmed_spreadsheet",
                "teacher_confirmed": True,
                "source_label": "合成总览证据",
                "assessments": [{
                    "title": "合成期中",
                    "subject_name": "数学",
                    "occurred_on": "2026-08-03",
                    "max_score": 100,
                    "session": {
                        "title": "合成期中",
                        "academic_year": "2025-2026",
                        "term": "下学期",
                        "grade": "八年级",
                        "exam_type": "期中",
                        "comparison_series": "学期大考",
                        "occurred_on": "2026-08-03",
                        "source_reference": "synthetic-mid",
                    },
                    "results": [
                        {"subject_id": first, "result_state": "normal", "score": 90},
                        {"subject_id": second, "result_state": "normal", "score": 50},
                    ],
                }],
            },
        },
    )
    assert batch_response.status_code == 200
    evidence = client.get(
        f"/api/class-teacher/evidence/subjects/{first}", headers=headers
    ).json()["items"][0]
    card_response = client.post(
        "/api/class-teacher/attention-cards",
        headers=headers,
        json={
            "operation_id": "ov-ac-attention",
            "evidence_version_id": evidence["evidence_version_id"],
            "observed_fact": "合成关注事实",
            "comparability": "insufficient_information",
            "limitations": ["只有一条证据"],
            "verification_question": "是否需要核实？",
            "low_risk_next_step": "先了解情况",
            "evidence_sufficiency": "不足",
            "review_suggestion": "后续复查",
        },
    )
    assert card_response.status_code == 200

    response = client.get("/api/class-teacher/evidence/overview", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sessions"]) == 1
    session = payload["sessions"][0]
    assert session["title"] == "合成期中"
    assert session["occurred_on"] == "2026-08-03"
    assert session["subject_names"] == ["数学"]
    assert session["member_count"] == 2
    latest = payload["latest_session"]
    assert latest["session_id"] == session["session_id"]
    math = latest["subjects"][0]
    assert math["subject_name"] == "数学"
    assert math["count"] == 2
    assert math["average"] == 70.0
    assert math["maximum"] == 90.0
    assert math["minimum"] == 50.0
    bands = {band["label"]: band["count"] for band in math["bands"]}
    assert bands["90-100%"] == 1
    assert bands["60%以下"] == 1
    assert payload["attention_pending_count"] == 1
    attention = payload["attention_students"]
    assert attention[0]["subject_id"] == first
    assert attention[0]["display_name"] == "合成学业甲"
    assert attention[0]["pending_count"] == 1

    empty_client, empty_headers = _client(tmp_path / "empty")
    empty = empty_client.get(
        "/api/class-teacher/evidence/overview", headers=empty_headers
    ).json()
    assert empty["sessions"] == []
    assert empty["latest_session"] is None
    assert empty["attention_students"] == []
    assert empty["attention_pending_count"] == 0
