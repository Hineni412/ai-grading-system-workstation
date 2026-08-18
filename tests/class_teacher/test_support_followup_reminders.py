from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_TITLE = "学生支持待跟进"


def _service(tmp_path: Path) -> tuple[VaultService, str]:
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
    return service, ""


def _subject(service: VaultService, token: str, suffix: str) -> str:
    subject = service.support.create_subject(
        token=token,
        operation_id=f"followup-subject-{suffix}",
        source_student_id=f"synthetic-followup-student-{suffix}",
        display_name="合成跟进学生",
        class_label="合成一班",
    )
    return str(subject["subject_id"])


def _create_record(
    service: VaultService,
    token: str,
    subject_id: str,
    suffix: str,
    review_at: str | None,
) -> dict[str, object]:
    return service.support.create_record(
        token=token,
        operation_id=f"followup-record-{suffix}",
        subject_id=subject_id,
        record_kind="fact",
        content="合成支持记录正文。",
        scene="合成场景",
        source="教师观察",
        basis=None,
        counterexample=None,
        category=None,
        observed_at="2026-08-01T08:00:00+00:00",
        review_at=review_at,
        expires_at=None,
    )


def _cards(service: VaultService) -> list[dict[str, object]]:
    return [
        item
        for item in service.work.read(view="all")["nodes"]
        if item["classification"] == "restricted_projection"
        and item["title"] == CARD_TITLE
    ]


def _projection_id(service: VaultService, token: str, source_id: str, occurrence: str) -> str:
    group = service.projections.read_source_group(
        token=token,
        source_kind="student_support",
        source_id=source_id,
        occurrence_id=occurrence,
    )
    return str(group["projection_id"])


def _write_profile(
    service: VaultService,
    token: str,
    subject_id: str,
    confirmed_at: str,
    open_questions: list[str],
) -> None:
    vmk = service.ensure_plaintext_ready()
    with closing(service.database.connect()) as connection:
        with connection:
            saved = service.student_cards.upsert_current_profile_in_connection(
                connection,
                vmk=vmk,
                subject_id=subject_id,
                profile_update={
                    "summary": "合成当前认识。",
                    "dimensions": [],
                    "open_questions": open_questions,
                    "support_focus": [],
                },
                expected_revision=None,
                operation_id=f"followup-profile-{confirmed_at[:10]}",
                model_operation_id=f"followup-model-{confirmed_at[:10]}",
                teacher_quote="合成教师输入",
                model_draft="合成草稿",
            )
            payload, revision = service.repository.get(
                connection,
                vmk=vmk,
                object_id=f"student-card-current-{saved['entry_id']}",
            )
            payload["teacher_confirmed_at"] = confirmed_at
            service.repository.put(
                connection,
                vmk=vmk,
                object_id=f"student-card-current-{saved['entry_id']}",
                object_type="student_card_current_profile",
                payload=payload,
                expected_revision=revision,
            )


def test_record_review_date_creates_followup_card(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "create-001")
    record = _create_record(
        service, token, subject_id, "create-001", "2026-08-10T08:00:00+00:00"
    )

    cards = _cards(service)
    assert len(cards) == 1
    assert cards[0]["due_date"] == "2026-08-10"
    assert cards[0]["status"] == "pending"
    group = service.projections.read_source_group(
        token=token,
        source_kind="student_support",
        source_id=str(record["record_id"]),
        occurrence_id="record-review",
    )
    assert group["due_date"] == "2026-08-10"


def test_record_review_revise_updates_and_clearing_tombstones(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "revise-001")
    record = _create_record(
        service, token, subject_id, "revise-001", "2026-08-10T08:00:00+00:00"
    )
    record_id = str(record["record_id"])

    revised = service.support.revise_record(
        token=token,
        record_id=record_id,
        operation_id="followup-revise-002",
        expected_revision=int(record["current_revision"]),
        content="合成支持记录正文。",
        scene="合成场景",
        source="教师观察",
        basis=None,
        counterexample=None,
        category=None,
        observed_at="2026-08-01T08:00:00+00:00",
        review_at="2026-08-20T08:00:00+00:00",
        expires_at=None,
        revision_reason="合成修订",
    )
    assert [card["due_date"] for card in _cards(service)] == ["2026-08-20"]

    service.support.revise_record(
        token=token,
        record_id=record_id,
        operation_id="followup-revise-003",
        expected_revision=int(revised["current_revision"]),
        content="合成支持记录正文。",
        scene="合成场景",
        source="教师观察",
        basis=None,
        counterexample=None,
        category=None,
        observed_at="2026-08-01T08:00:00+00:00",
        review_at=None,
        expires_at=None,
        revision_reason="合成清空复查日期",
    )
    assert _cards(service) == []
    group = service.projections.read_source_group(
        token=token,
        source_kind="student_support",
        source_id=record_id,
        occurrence_id="record-review",
    )
    assert group["state"] == "cancelled"


def test_record_withdraw_tombstones_followup_card(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "state-001")
    record = _create_record(
        service, token, subject_id, "state-001", "2026-08-10T08:00:00+00:00"
    )
    assert len(_cards(service)) == 1

    service.support.set_record_state(
        token=token,
        record_id=str(record["record_id"]),
        operation_id="followup-state-002",
        expected_revision=int(record["current_revision"]),
        state="withdrawn",
        reason="合成撤回",
    )
    assert _cards(service) == []


def test_plan_review_card_and_completion_tombstone(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "plan-001")
    plan = service.support.create_support_plan(
        token=token,
        operation_id="followup-plan-001",
        subject_id=subject_id,
        goal="合成支持目标",
        support_actions=["合成行动一"],
        review_at="2026-08-15T08:00:00+00:00",
        action_id=None,
    )

    cards = _cards(service)
    assert len(cards) == 1
    assert cards[0]["due_date"] == "2026-08-15"

    service.support.complete_support_plan(
        token=token,
        support_plan_id=str(plan["support_plan_id"]),
        operation_id="followup-plan-002",
        expected_revision=int(plan["revision"]),
        result="合成计划结果",
    )
    assert _cards(service) == []


def test_stale_open_questions_reminder_is_idempotent(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "stale-001")
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-01T00:00:00+00:00",
        open_questions=["合成待了解问题"],
    )

    first = service.student_cards.evaluate_followup_reminders(token=token)
    second = service.student_cards.evaluate_followup_reminders(token=token)

    assert first["enqueued"] == 1
    assert second["enqueued"] == 0
    cards = _cards(service)
    assert len(cards) == 1
    assert cards[0]["due_date"] == "2026-07-15"
    group = service.projections.read_source_group(
        token=token,
        source_kind="student_support",
        source_id=subject_id,
        occurrence_id="profile-stale-2026-07-01",
    )
    assert group["due_date"] == "2026-07-15"


def test_fresh_or_answered_profile_stays_quiet(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "fresh-001")
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-01T00:00:00+00:00",
        open_questions=[],
    )
    result = service.student_cards.evaluate_followup_reminders(token=token)
    assert result["enqueued"] == 0
    assert _cards(service) == []


def test_stale_reminder_dismiss_is_sticky_until_profile_updates(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "sticky-001")
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-01T00:00:00+00:00",
        open_questions=["合成待了解问题"],
    )
    service.student_cards.evaluate_followup_reminders(token=token)
    assert len(_cards(service)) == 1

    projection_id = _projection_id(service, token, subject_id, "profile-stale-2026-07-01")
    service.projections.dismiss(token=token, projection_id=projection_id)
    assert _cards(service) == []

    # 档案未更新前，重复评估不会重新造卡。
    service.student_cards.evaluate_followup_reminders(token=token)
    assert _cards(service) == []

    # 档案再次确认后仍有待了解问题，按新的确认时间重新提醒。
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-20T00:00:00+00:00",
        open_questions=["合成待了解问题"],
    )
    service.student_cards.evaluate_followup_reminders(token=token)
    cards = _cards(service)
    assert len(cards) == 1
    assert cards[0]["due_date"] == "2026-08-03"


def test_new_record_marks_stale_reminder_handled(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "handled-001")
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-01T00:00:00+00:00",
        open_questions=["合成待了解问题"],
    )
    service.student_cards.evaluate_followup_reminders(token=token)
    assert len(_cards(service)) == 1

    # 教师为这名学生记下新记录，视为已经跟进。
    _create_record(service, token, subject_id, "handled-002", None)
    assert _cards(service) == []


def test_postpone_and_dismiss_api(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")

    from backend.api.app import ApiError
    from fastapi.responses import JSONResponse

    @app.exception_handler(ApiError)
    async def _api_error_handler(request, exc):  # noqa: ANN001, ANN202
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    client = TestClient(app)
    headers = {"x-class-teacher-client": "class-teacher-browser-v1"}

    subject_id = _subject(service, token, "api-001")
    record = _create_record(
        service, token, subject_id, "api-002", "2026-08-10T08:00:00+00:00"
    )
    projection_id = _projection_id(
        service, token, str(record["record_id"]), "record-review"
    )

    postponed = client.post(
        f"/api/class-teacher/support/follow-ups/{projection_id}/postpone",
        headers=headers,
        json={"due_date": "2026-08-25"},
    )
    assert postponed.status_code == 200
    assert postponed.json()["due_date"] == "2026-08-25"
    assert [card["due_date"] for card in _cards(service)] == ["2026-08-25"]

    dismissed = client.post(
        f"/api/class-teacher/support/follow-ups/{projection_id}/dismiss",
        headers=headers,
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed"] is True
    assert _cards(service) == []

    # 重复关闭幂等；关闭后不能再延后。
    again = client.post(
        f"/api/class-teacher/support/follow-ups/{projection_id}/dismiss",
        headers=headers,
    )
    assert again.status_code == 200
    blocked = client.post(
        f"/api/class-teacher/support/follow-ups/{projection_id}/postpone",
        headers=headers,
        json={"due_date": "2026-09-01"},
    )
    assert blocked.status_code == 409

    untrusted = client.post(
        f"/api/class-teacher/support/follow-ups/{projection_id}/dismiss",
    )
    assert untrusted.status_code in {401, 403}


def test_resolve_points_to_student_support_panel(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "resolve-001")
    record = _create_record(
        service, token, subject_id, "resolve-002", "2026-08-10T08:00:00+00:00"
    )
    projection_id = _projection_id(
        service, token, str(record["record_id"]), "record-review"
    )

    target = service.projections.resolve(token=token, projection_id=projection_id)
    assert target["gone"] is False
    assert target["surface"] == "students"
    assert target["panel"] == "support"
    assert target["subject_id"] == subject_id


def test_resolve_gone_after_source_withdrawn(tmp_path: Path) -> None:
    service, token = _service(tmp_path)
    subject_id = _subject(service, token, "gone-001")
    _write_profile(
        service,
        token,
        subject_id,
        confirmed_at="2026-07-01T00:00:00+00:00",
        open_questions=["合成待了解问题"],
    )
    service.student_cards.evaluate_followup_reminders(token=token)
    projection_id = _projection_id(service, token, subject_id, "profile-stale-2026-07-01")

    target = service.projections.resolve(token=token, projection_id=projection_id)
    assert target["gone"] is False
    assert target["subject_id"] == subject_id
