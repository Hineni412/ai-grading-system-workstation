from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        model_gateway=FakeWorkPlanningGateway(
            result={
                "kind": "follow_up",
                "questions": ["是否有人受伤？"],
            }
        ),
    )
    service.ensure_plaintext_ready()
    return service, ""


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


def test_roster_replace_writes_stable_keys_without_creating_profiles(
    tmp_path: Path,
) -> None:
    """激活只写花名册状态：稳定标识为键，不创建档案、不写 subject_id。"""
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    roster = service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-stable-keys",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    assert roster["active_count"] == 2
    assert all(item["subject_id"] is None for item in roster["items"])
    with closing(service.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_subject_links").fetchone()[0] == 0
        keys = {
            str(row[0])
            for row in connection.execute(
                "SELECT source_student_key FROM class_roster_memberships"
            ).fetchall()
        }
    assert keys == {"一班|A001", "一班|A002"}
    browse = service.class_roster.browse(token=token, class_label="一班")
    assert {str(item["roster_state"]) for item in browse["items"]} == {"active"}


def test_roster_replace_rolls_back_membership_writes_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, token = _service(tmp_path)
    seed_source = service.class_roster.browse(token=token, class_label="二班")
    service.class_roster.replace_current(
        token=token,
        operation_id="replace-current-seed-class",
        expected_source_revision=str(seed_source["source_revision"]),
        class_label="二班",
    )
    source = service.class_roster.browse(token=token, class_label="一班")
    real_ref = service.class_roster._roster_ref
    calls = 0

    def fail_late(item):
        nonlocal calls
        calls += 1
        if calls > len(source["items"]):
            raise RuntimeError("synthetic roster interruption")
        return real_ref(item)

    monkeypatch.setattr(service.class_roster, "_roster_ref", fail_late)
    with pytest.raises(RuntimeError, match="synthetic roster interruption"):
        service.class_roster.replace_current(
            token=token,
            operation_id="replace-current-fail",
            expected_source_revision=str(source["source_revision"]),
            class_label="一班",
        )
    with closing(service.database.connect()) as connection:
        rows = connection.execute(
            "SELECT source_student_key, state FROM class_roster_memberships"
        ).fetchall()
    assert [(str(row[0]), str(row[1])) for row in rows] == [("二班|B001", "active")]




def test_directory_roster_state_follows_stable_key_membership(tmp_path: Path) -> None:
    """目录的在班/历史状态从成员表（稳定标识键）读，与建档顺序无关。"""
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    item = next(
        entry for entry in source["items"] if str(entry["student_code"]) == "A001"
    )
    created = service.support.create_subject_for_roster_source(
        token=token,
        operation_id="directory-state-subject",
        source_student_id=str(item["source_key"]),
        legacy_student_code=str(item["student_code"]),
        display_name=str(item["display_name"]),
        class_label=str(item["class_label"]),
    )
    subject_id = str(created["subject_id"])

    def roster_state() -> str:
        result = service.student_directory.search(token=token, q="王明")
        matches = [
            entry for entry in result["items"] if entry["subject_id"] == subject_id
        ]
        assert len(matches) == 1
        return str(matches[0]["roster_state"])

    # 建档不经激活：不在花名册成员表，目录显示 manual
    assert roster_state() == "manual"
    service.class_roster.replace_current(
        token=token,
        operation_id="activate-class-one",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    assert roster_state() == "active"
    second = service.class_roster.browse(token=token, class_label="二班")
    service.class_roster.replace_current(
        token=token,
        operation_id="activate-class-two",
        expected_source_revision=str(second["source_revision"]),
        class_label="二班",
    )
    assert roster_state() == "historical"


def test_sop_joins_roster_membership_by_stable_ref(tmp_path: Path) -> None:
    """SOP 关联已建档学生时按稳定标识核对在班状态。"""
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    service.class_roster.replace_current(
        token=token,
        operation_id="sop-join-activate",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    item = source["items"][0]
    created = service.support.create_subject_for_roster_source(
        token=token,
        operation_id="sop-join-subject",
        source_student_id=str(item["source_key"]),
        legacy_student_code=str(item["student_code"]),
        display_name=str(item["display_name"]),
        class_label=str(item["class_label"]),
    )
    subject_id = str(created["subject_id"])
    template = service.sop_baselines.ensure_baselines(token=token)["items"][0]
    affair = service.sop.create_affair(
        token=token,
        operation_id="sop-join-affair",
        template_version_id=str(template["template_version_id"]),
        title="合成事务",
        summary=None,
        participant_refs=[],
        subject_ids=[subject_id],
    )
    assert affair["affair_id"]

    second = service.class_roster.browse(token=token, class_label="二班")
    service.class_roster.replace_current(
        token=token,
        operation_id="sop-join-activate-two",
        expected_source_revision=str(second["source_revision"]),
        class_label="二班",
    )
    with pytest.raises(VaultError) as blocked:
        service.sop.create_affair(
            token=token,
            operation_id="sop-join-affair-two",
            template_version_id=str(template["template_version_id"]),
            title="合成事务二",
            summary=None,
            participant_refs=[],
            subject_ids=[subject_id],
        )
    assert blocked.value.code == "sop_subject_not_current_roster"


def test_delete_subject_removes_membership_row(tmp_path: Path) -> None:
    """删除学生档案时移除其稳定标识对应的成员行；无成员行的手工档案不受影响。"""
    service, token = _service(tmp_path)
    source = service.class_roster.browse(token=token, class_label="一班")
    service.class_roster.replace_current(
        token=token,
        operation_id="delete-cleanup-activate",
        expected_source_revision=str(source["source_revision"]),
        class_label="一班",
    )
    item = source["items"][0]
    created = service.support.create_subject_for_roster_source(
        token=token,
        operation_id="delete-cleanup-subject",
        source_student_id=str(item["source_key"]),
        legacy_student_code=str(item["student_code"]),
        display_name=str(item["display_name"]),
        class_label=str(item["class_label"]),
    )
    service.support._delete_subject_once(
        token=token,
        subject_id=str(created["subject_id"]),
        operation_id="delete-cleanup-subject-row",
        confirmation_phrase="确认完整删除学生支持数据",
    )
    with closing(service.database.connect()) as connection:
        rows = connection.execute(
            "SELECT source_student_key, state FROM class_roster_memberships"
        ).fetchall()
    remaining = [(str(row[0]), str(row[1])) for row in rows]
    expected_remaining = (
        "一班|A002" if str(item["student_code"]) == "A001" else "一班|A001"
    )
    assert remaining == [(expected_remaining, "active")]
