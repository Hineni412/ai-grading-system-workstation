from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.protection import FakeCurrentUserProtection
from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake_draft import compose_sensitive_draft, merge_revision
from backend.class_teacher.vault_service import VaultService
from backend.class_teacher.work_planning import FakeWorkPlanningGateway
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_calendar_only_revision_locks_sop_and_moves_downstream_dates() -> None:
    base = {
        "summary": "原方案",
        "assumptions": [],
        "to_verify": [],
        "steps": [
            {"key": "a", "title": "步骤甲", "details": "甲", "depends_on": [], "safety_required": True},
            {"key": "b", "title": "步骤乙", "details": "乙", "depends_on": ["a"], "safety_required": False},
        ],
        "calendar_items": [
            {"key": "calendar.a", "step_key": "a", "title": "步骤甲", "due_date": "2026-08-03", "depends_on": []},
            {"key": "calendar.b", "step_key": "b", "title": "步骤乙", "due_date": "2026-08-04", "depends_on": ["calendar.a"]},
        ],
    }
    candidate = {
        **base,
        "steps": [
            {**base["steps"][0], "title": "不应改变甲"},
            {**base["steps"][1], "title": "不应改变乙"},
        ],
        "calendar_items": [
            {**base["calendar_items"][0], "due_date": "2026-08-10"},
            {**base["calendar_items"][1], "due_date": "2026-08-11"},
        ],
    }
    merged = merge_revision(base, candidate, [], ["calendar.a"])
    assert [item["title"] for item in merged["steps"]] == ["步骤甲", "步骤乙"]
    assert [item["due_date"] for item in merged["calendar_items"]] == [
        "2026-08-10",
        "2026-08-11",
    ]


def test_local_classifier_cannot_be_weakened_by_model_template() -> None:
    draft = compose_sensitive_draft(
        source_text="王明受伤流血，需要处理",
        recommended_route="affair",
        resolved_date="2026-08-03",
        model_payload={"template_key": "baseline.student_conflict"},
    )
    assert draft["template_key"] == "baseline.student_injury"
    assert any(item["safety_required"] for item in draft["steps"])


def test_negated_injury_keeps_the_student_conflict_template() -> None:
    draft = compose_sensitive_draft(
        source_text="两名学生发生推搡，教师已确认无人受伤",
        recommended_route="affair",
        resolved_date="2026-08-03",
        model_payload={},
    )

    assert draft["template_key"] == "baseline.student_conflict"


def _service(tmp_path: Path) -> tuple[VaultService, str]:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                student_code TEXT,
                name TEXT NOT NULL,
                class_name TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [
                (1, "A001", "王明", "一班"),
                (2, "A002", "张伟", "一班"),
                (3, "B001", "李华", "二班"),
            ],
        )
        connection.commit()
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
                db_path=grading,
            ),
        ),
        protection_provider=FakeCurrentUserProtection(b"R" * 32),
        model_gateway=FakeWorkPlanningGateway(
            result={
                "kind": "follow_up",
                "questions": ["是否有人受伤？"],
            }
        ),
    )
    initialized = service.initialize(
        password="合成班主任名单密码-足够长-001",
        operation_id="unified-roster-init",
    )
    return service, str(initialized["session_token"])


def test_existing_roster_filter_replaces_current_class_without_deleting_history(
    tmp_path: Path,
) -> None:
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    assert source["total"] == 2
    assert source["classes"] == ["一班", "二班"]

    first = service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-roster-a",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    assert first["active_count"] == 2
    assert first["historical_count"] == 0

    replay = service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-roster-a",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    assert replay["replayed"] is True
    assert replay["active_count"] == 2

    second_source = service.class_roster.browse(token=token, class_label="二班")
    second = service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-roster-b",
        expected_source_revision=str(second_source["source_revision"]),
        class_label="二班",
    )
    assert second["active_count"] == 1
    assert second["historical_count"] == 2
    assert {item["display_name"] for item in second["items"] if item["state"] == "historical"} == {
        "王明",
        "张伟",
    }


def test_roster_replace_rolls_back_new_profiles_when_one_identity_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    real_ensure = service.support.ensure_subject_in_connection
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic identity interruption")
        return real_ensure(*args, **kwargs)

    monkeypatch.setattr(service.support, "ensure_subject_in_connection", fail_second)
    with pytest.raises(RuntimeError, match="synthetic identity interruption"):
        service.class_roster.replace_current(
            token=token,
            operation_id="replace-current-roster-fail",
            expected_source_revision=str(source["source_revision"]),
            class_label="一班",
        )
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_subject_links").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM class_roster_memberships").fetchone()[0] == 0


def test_first_follow_up_becomes_sop_draft_and_partial_save_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    roster = service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-for-affair",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    subject_ids = [
        str(item["subject_id"])
        for item in roster["items"]
        if item["state"] == "active"
    ]

    preview = service.home_intake.prepare(
        token=token,
        text="王明和张伟发生了冲突",
        reference_date="2026-08-03",
    )
    result = service.home_intake.dispatch(
        token=token,
        preview_id=str(preview["preview_id"]),
        fingerprint=str(preview["fingerprint"]),
        operation_id="unified-intake-model-call",
    )
    assert result["state"] == "succeeded"
    assert result["result_kind"] == "affair_recommendation"
    assert len(result["result"]["steps"]) >= 5
    assert result["follow_up_questions"][0] == "是否有人受伤？"
    assert result["physical_request_count"] == 1

    real_upsert = service.projections.upsert
    calls = 0

    def fail_after_first(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic projection interruption")
        return real_upsert(**kwargs)

    monkeypatch.setattr(service.projections, "upsert", fail_after_first)
    with pytest.raises(RuntimeError, match="synthetic projection interruption"):
        service.home_intake_finalizer.adopt(
            token=token,
            source_operation_id="unified-intake-model-call",
            operation_id="unified-intake-final-save",
            result_fingerprint=str(result["result_fingerprint"]),
            subject_ids=subject_ids,
        )

    monkeypatch.setattr(service.projections, "upsert", real_upsert)
    with pytest.raises(VaultError) as changed:
        service.home_intake_finalizer.adopt(
            token=token,
            source_operation_id="unified-intake-model-call",
            operation_id="new-client-operation-after-refresh",
            result_fingerprint=str(result["result_fingerprint"]),
            subject_ids=subject_ids[:1],
        )
    assert changed.value.code == "vault_operation_conflict"
    saved = service.home_intake_finalizer.adopt(
        token=token,
        source_operation_id="unified-intake-model-call",
        operation_id="another-client-operation-after-refresh",
        result_fingerprint=str(result["result_fingerprint"]),
        subject_ids=subject_ids,
    )
    assert saved["state"] == "complete"
    assert saved["student_link_count"] == 2
    assert saved["physical_request_count"] == 0
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM affairs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM affair_student_links").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_kind='sensitive_affair'"
        ).fetchone()[0] == 1 + int(saved["calendar_projection_count"])

    student = service.student_directory.open(token=token, subject_id=subject_ids[0])
    assert student["affair_count"] == 1
    assert student["related_affairs"][0]["affair_id"] == saved["affair"]["affair_id"]

    service.support._delete_subject_once(
        token=token,
        subject_id=subject_ids[0],
        operation_id="delete-linked-student-profile",
        confirmation_phrase="确认完整删除学生支持数据",
    )
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM affairs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM affair_student_links").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM affair_participants").fetchone()[0] == 1
