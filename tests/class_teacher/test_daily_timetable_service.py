from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.daily_timetable_service import DailyTimetableService
from backend.class_teacher.errors import VaultError
from backend.class_teacher.ordinary_database import OrdinaryWorkDatabase
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 2026-08-31 是周一，2026-09-07 是下一周一。
MONDAY = "2026-08-31"
NEXT_MONDAY = "2026-09-07"


def _context(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )


def _service(tmp_path: Path) -> DailyTimetableService:
    return DailyTimetableService(OrdinaryWorkDatabase(_context(tmp_path)))


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    service = VaultService(_context(tmp_path))
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app), {
        "x-class-teacher-client": "class-teacher-browser-v1",
    }


def _cell(week: dict, day_of_week: int, slot_key: str) -> dict:
    for cell in week["cells"]:
        if cell["day_of_week"] == day_of_week and cell["slot_key"] == slot_key:
            return cell
    raise AssertionError(f"缺少格子 day={day_of_week} slot={slot_key}")


# ----------------------------------------------------------------------
# 周次推算


def test_week_view_defaults_to_current_week_without_anchor(tmp_path: Path) -> None:
    service = _service(tmp_path)

    week = service.get_week(today=date(2026, 9, 2))  # 周三

    assert week["week_start"] == MONDAY
    assert week["week_no"] is None
    assert [day["date"] for day in week["days"]] == [
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
    ]
    assert [slot["slot_key"] for slot in week["slots"]] == [
        f"lesson:{number}" for number in range(1, 9)
    ]
    assert len(week["cells"]) == 40
    assert all(cell["source"] == "empty" for cell in week["cells"])
    assert week["today"] == {
        "date": "2026-09-02",
        "day_of_week": 3,
        "in_week": True,
    }


def test_week_no_follows_anchor_across_weeks(tmp_path: Path) -> None:
    service = _service(tmp_path)

    # 传该周任意一天（周三），存储时规范化到周一。
    saved = service.set_week_anchor(anchor_date="2026-09-02", week_no=3)

    assert saved["anchor_monday"] == MONDAY
    assert saved["week_no"] == 3
    anchor = service.get_week_anchor()
    assert anchor is not None
    assert anchor["anchor_monday"] == MONDAY
    assert anchor["week_no"] == 3
    assert service.get_week(MONDAY)["week_no"] == 3
    assert service.get_week(NEXT_MONDAY)["week_no"] == 4
    assert service.get_week("2026-08-24")["week_no"] == 2
    # week_start 传周中日期也归一到周一。
    mid_week = service.get_week("2026-09-09")
    assert mid_week["week_start"] == NEXT_MONDAY
    assert mid_week["week_no"] == 4


def test_reanchor_replaces_single_row(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_week_anchor(anchor_date=MONDAY, week_no=3)

    service.set_week_anchor(anchor_date=MONDAY, week_no=10)

    anchor = service.get_week_anchor()
    assert anchor is not None
    assert anchor["week_no"] == 10
    assert service.get_week(NEXT_MONDAY)["week_no"] == 11


def test_invalid_dates_and_week_no_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(VaultError) as bad_week:
        service.get_week("2026-13-40")
    assert bad_week.value.code == "daily_timetable_date_invalid"
    with pytest.raises(VaultError):
        service.set_week_anchor(anchor_date="不是日期", week_no=1)
    with pytest.raises(VaultError) as bad_no:
        service.set_week_anchor(anchor_date=MONDAY, week_no=41)
    assert bad_no.value.code == "daily_timetable_week_no_invalid"


# ----------------------------------------------------------------------
# 常规格与 slot_key 校验


def test_regular_cell_set_update_and_clear(tmp_path: Path) -> None:
    service = _service(tmp_path)

    service.set_regular_cell(
        day_of_week=2, slot_key="lesson:3",
        course_text="数学", class_label="七(1)班",
    )
    cell = _cell(service.get_week(MONDAY), 2, "lesson:3")
    assert cell["source"] == "regular"
    assert cell["course_text"] == "数学"
    assert cell["class_label"] == "七(1)班"
    # 常规格对任意周生效。
    assert _cell(service.get_week(NEXT_MONDAY), 2, "lesson:3")["course_text"] == "数学"

    service.set_regular_cell(
        day_of_week=2, slot_key="lesson:3",
        course_text="语文", class_label="七(1)班",
    )
    assert _cell(service.get_week(MONDAY), 2, "lesson:3")["course_text"] == "语文"

    # 空字符串 = 删除该格。
    cleared = service.set_regular_cell(
        day_of_week=2, slot_key="lesson:3", course_text="", class_label="",
    )
    assert cleared["source"] == "empty"
    assert _cell(service.get_week(MONDAY), 2, "lesson:3")["source"] == "empty"


def test_slot_key_validation(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(VaultError) as bad_lesson:
        service.set_regular_cell(
            day_of_week=1, slot_key="lesson:9", course_text="数学",
        )
    assert bad_lesson.value.code == "daily_timetable_slot_invalid"
    with pytest.raises(VaultError):
        service.set_regular_cell(
            day_of_week=1, slot_key=f"custom:{'0' * 32}", course_text="数学",
        )
    with pytest.raises(VaultError):
        service.set_regular_cell(
            day_of_week=1, slot_key="随便写", course_text="数学",
        )
    with pytest.raises(VaultError) as bad_day:
        service.set_regular_cell(
            day_of_week=6, slot_key="lesson:1", course_text="数学",
        )
    assert bad_day.value.code == "daily_timetable_day_invalid"


def test_data_survives_service_recreation(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = DailyTimetableService(OrdinaryWorkDatabase(context))
    first.set_regular_cell(
        day_of_week=2, slot_key="lesson:3",
        course_text="数学", class_label="七(1)班",
    )

    second = DailyTimetableService(OrdinaryWorkDatabase(context))

    assert _cell(second.get_week(MONDAY), 2, "lesson:3")["course_text"] == "数学"


# ----------------------------------------------------------------------
# 当周临时覆盖


def test_override_set_and_clear_only_apply_to_their_week(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_regular_cell(
        day_of_week=2, slot_key="lesson:3",
        course_text="数学", class_label="七(1)班",
    )

    applied = service.apply_overrides(
        week_start="2026-09-02",  # 周三，归一到周一存储
        ops=[
            {
                "day_of_week": 2,
                "slot_key": "lesson:3",
                "action": "set",
                "course_text": "语文",
                "class_label": "七(2)班",
                "note": "代课",
            }
        ],
    )

    assert applied["week_start"] == MONDAY
    override_id = applied["overrides"][0]["override_id"]
    cell = _cell(service.get_week(MONDAY), 2, "lesson:3")
    assert cell["source"] == "override"
    assert cell["course_text"] == "语文"
    assert cell["class_label"] == "七(2)班"
    assert cell["override_id"] == override_id
    assert cell["note"] == "代课"
    # 下周自动恢复常规。
    next_cell = _cell(service.get_week(NEXT_MONDAY), 2, "lesson:3")
    assert next_cell["source"] == "regular"
    assert next_cell["course_text"] == "数学"

    # 同 (week_start, day, slot) 后写覆盖先写：clear 替换 set。
    service.apply_overrides(
        week_start=MONDAY,
        ops=[{"day_of_week": 2, "slot_key": "lesson:3", "action": "clear"}],
    )
    cleared = _cell(service.get_week(MONDAY), 2, "lesson:3")
    assert cleared["source"] == "override"
    assert cleared["course_text"] == ""
    assert cleared["override_id"] != override_id
    # 旧覆盖已被替换，删除旧 id 报 404。
    with pytest.raises(VaultError) as gone:
        service.delete_override(override_id)
    assert gone.value.status_code == 404

    # 删除当前覆盖后回到常规。
    service.delete_override(cleared["override_id"])
    assert _cell(service.get_week(MONDAY), 2, "lesson:3")["source"] == "regular"


def test_override_swap_and_atomic_commit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_regular_cell(
        day_of_week=2, slot_key="lesson:3",
        course_text="数学", class_label="七(1)班",
    )
    service.set_regular_cell(
        day_of_week=4, slot_key="lesson:5",
        course_text="体育", class_label="七(1)班",
    )

    # 拖拽交换 = 两条 op 一次提交。
    service.apply_overrides(
        week_start=MONDAY,
        ops=[
            {
                "day_of_week": 4, "slot_key": "lesson:5",
                "action": "set", "course_text": "数学", "class_label": "七(1)班",
            },
            {
                "day_of_week": 2, "slot_key": "lesson:3",
                "action": "set", "course_text": "体育", "class_label": "七(1)班",
            },
        ],
    )
    week = service.get_week(MONDAY)
    assert _cell(week, 4, "lesson:5")["course_text"] == "数学"
    assert _cell(week, 2, "lesson:3")["course_text"] == "体育"

    # 第二条 op 无效 → 整体回滚，第一条也不生效。
    with pytest.raises(VaultError):
        service.apply_overrides(
            week_start=MONDAY,
            ops=[
                {
                    "day_of_week": 1, "slot_key": "lesson:1",
                    "action": "set", "course_text": "英语",
                },
                {
                    "day_of_week": 1, "slot_key": "lesson:9",
                    "action": "set", "course_text": "坏数据",
                },
            ],
        )
    assert _cell(service.get_week(MONDAY), 1, "lesson:1")["source"] == "empty"

    # set 缺课程内容 → 拒绝。
    with pytest.raises(VaultError) as missing_course:
        service.apply_overrides(
            week_start=MONDAY,
            ops=[{"day_of_week": 1, "slot_key": "lesson:1", "action": "set"}],
        )
    assert missing_course.value.code == "daily_timetable_course_required"


# ----------------------------------------------------------------------
# 自定义时段


def test_custom_slots_interleave_and_cascade_delete(tmp_path: Path) -> None:
    service = _service(tmp_path)
    early = service.create_custom_slot(label="早读", position=0)
    noon = service.create_custom_slot(
        label="午休", start_text="12:30", end_text="13:30", position=4,
    )
    noon_second = service.create_custom_slot(label="午练", position=4)
    late = service.create_custom_slot(label="延时", position=8)

    keys = [slot["slot_key"] for slot in service.get_week(MONDAY)["slots"]]
    assert keys == [
        early["slot_key"],
        "lesson:1",
        "lesson:2",
        "lesson:3",
        "lesson:4",
        # 同 position 按创建先后。
        noon["slot_key"],
        noon_second["slot_key"],
        "lesson:5",
        "lesson:6",
        "lesson:7",
        "lesson:8",
        late["slot_key"],
    ]

    # 连带删除该时段的常规格与覆盖。
    service.set_regular_cell(
        day_of_week=1, slot_key=noon["slot_key"],
        course_text="午练数学", class_label="七(1)班",
    )
    service.apply_overrides(
        week_start=MONDAY,
        ops=[
            {
                "day_of_week": 2, "slot_key": noon["slot_key"],
                "action": "set", "course_text": "临时午练",
            }
        ],
    )
    removed = service.delete_custom_slot(noon["custom_slot_id"])
    assert removed["removed_regular_cells"] == 1
    assert removed["removed_overrides"] == 1
    week = service.get_week(MONDAY)
    assert noon["slot_key"] not in [slot["slot_key"] for slot in week["slots"]]
    assert all(cell["slot_key"] != noon["slot_key"] for cell in week["cells"])
    # 常规格本身也删掉了（不只是不展示）。
    with pytest.raises(VaultError):
        service.set_regular_cell(
            day_of_week=1, slot_key=noon["slot_key"], course_text="数学",
        )
    with pytest.raises(VaultError) as missing:
        service.delete_custom_slot(noon["custom_slot_id"])
    assert missing.value.status_code == 404


# ----------------------------------------------------------------------
# 进度笔记


def test_recent_notes_grouped_by_class_and_sorted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_note(
        note_date="2026-09-01", slot_key="lesson:1",
        class_label="七(1)班", content_text="有理数", homework_text="练习册P1",
    )
    service.add_note(
        note_date="2026-09-02", slot_key="lesson:2",
        class_label="七(1)班", content_text="数轴",
    )
    service.add_note(
        note_date="2026-09-02", slot_key="lesson:3",
        class_label="七(1)班", content_text="绝对值",
    )
    service.add_note(
        note_date="2026-09-01", slot_key="lesson:3",
        class_label="七(2)班", content_text="有理数",
    )

    recent = service.recent_notes(class_labels=["七(1)班", "七(2)班", "七(3)班"], limit=10)

    assert [group["class_label"] for group in recent["classes"]] == [
        "七(1)班", "七(2)班", "七(3)班",
    ]
    first_class = recent["classes"][0]["notes"]
    # note_date 倒序；同日期按创建先后倒序。
    assert [note["content_text"] for note in first_class] == [
        "绝对值", "数轴", "有理数",
    ]
    assert first_class[2]["homework_text"] == "练习册P1"
    assert first_class[0]["homework_text"] is None
    assert len(recent["classes"][1]["notes"]) == 1
    # 没有记录的班返回空列表，便于对照视图直接渲染。
    assert recent["classes"][2]["notes"] == []


def test_recent_notes_limit_applies_per_class(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for index in range(3):
        service.add_note(
            note_date=f"2026-09-0{index + 1}", slot_key="lesson:1",
            class_label="七(1)班", content_text=f"第{index + 1}次内容",
        )

    recent = service.recent_notes(class_labels=["七(1)班"], limit=2)

    notes = recent["classes"][0]["notes"]
    assert len(notes) == 2
    assert [note["note_date"] for note in notes] == ["2026-09-03", "2026-09-02"]


# ----------------------------------------------------------------------
# API 层


def test_api_fresh_install_returns_empty_week(tmp_path: Path) -> None:
    client, _headers = _client(tmp_path)

    response = client.get(
        f"/api/class-teacher/daily/timetable?week_start={MONDAY}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["week_start"] == MONDAY
    assert payload["week_no"] is None
    assert all(cell["source"] == "empty" for cell in payload["cells"])
    anchor = client.get("/api/class-teacher/daily/timetable/week-anchor")
    assert anchor.status_code == 200
    assert anchor.json() is None


def test_api_mutations_require_trusted_client_header(tmp_path: Path) -> None:
    client, _headers = _client(tmp_path)

    with pytest.raises(ApiError) as blocked:
        client.put(
            "/api/class-teacher/daily/timetable/regular-cell",
            json={
                "day_of_week": 1,
                "slot_key": "lesson:1",
                "course_text": "数学",
                "class_label": "七(1)班",
            },
        )
    assert blocked.value.status_code == 403
    with pytest.raises(ApiError):
        client.post(
            "/api/class-teacher/daily/notes",
            json={
                "note_date": "2026-09-01",
                "slot_key": "lesson:1",
                "class_label": "七(1)班",
                "content_text": "有理数",
            },
        )


def test_api_full_timetable_flow(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    anchor = client.put(
        "/api/class-teacher/daily/timetable/week-anchor",
        headers=headers,
        json={"date": "2026-09-02", "week_no": 5},
    )
    assert anchor.status_code == 200
    assert anchor.json()["anchor_monday"] == MONDAY

    regular = client.put(
        "/api/class-teacher/daily/timetable/regular-cell",
        headers=headers,
        json={
            "day_of_week": 2,
            "slot_key": "lesson:3",
            "course_text": "数学",
            "class_label": "七(1)班",
        },
    )
    assert regular.status_code == 200
    assert regular.json()["source"] == "regular"

    custom = client.post(
        "/api/class-teacher/daily/timetable/custom-slots",
        headers=headers,
        json={
            "label": "午休",
            "start_text": "12:30",
            "end_text": "13:30",
            "position": 4,
        },
    )
    assert custom.status_code == 200
    custom_slot = custom.json()

    swapped = client.post(
        "/api/class-teacher/daily/timetable/overrides",
        headers=headers,
        json={
            "week_start": "2026-09-02",
            "ops": [
                {"day_of_week": 2, "slot_key": "lesson:3", "action": "clear"},
                {
                    "day_of_week": 4,
                    "slot_key": "lesson:5",
                    "action": "set",
                    "course_text": "数学",
                    "class_label": "七(1)班",
                    "note": "与周四第五节对调",
                },
            ],
        },
    )
    assert swapped.status_code == 200
    assert swapped.json()["week_start"] == MONDAY
    override_id = swapped.json()["overrides"][0]["override_id"]

    week = client.get(
        f"/api/class-teacher/daily/timetable?week_start={MONDAY}"
    ).json()
    assert week["week_no"] == 5
    assert _cell(week, 2, "lesson:3")["source"] == "override"
    assert _cell(week, 2, "lesson:3")["course_text"] == ""
    moved = _cell(week, 4, "lesson:5")
    assert moved["source"] == "override"
    assert moved["note"] == "与周四第五节对调"
    assert custom_slot["slot_key"] in [
        slot["slot_key"] for slot in week["slots"]
    ]

    next_week = client.get(
        f"/api/class-teacher/daily/timetable?week_start={NEXT_MONDAY}"
    ).json()
    assert next_week["week_no"] == 6
    assert _cell(next_week, 2, "lesson:3")["source"] == "regular"

    removed = client.delete(
        f"/api/class-teacher/daily/timetable/overrides/{override_id}",
        headers=headers,
    )
    assert removed.status_code == 200
    restored = client.get(
        f"/api/class-teacher/daily/timetable?week_start={MONDAY}"
    ).json()
    assert _cell(restored, 2, "lesson:3")["source"] == "regular"

    note = client.post(
        "/api/class-teacher/daily/notes",
        headers=headers,
        json={
            "note_date": "2026-09-01",
            "slot_key": "lesson:3",
            "class_label": "七(1)班",
            "content_text": "有理数",
            "homework_text": "练习册P1",
        },
    )
    assert note.status_code == 200

    recent = client.get(
        "/api/class-teacher/daily/notes/recent",
        params={"class_labels": "七(1)班,七(2)班", "limit": 10},
    )
    assert recent.status_code == 200
    groups = recent.json()["classes"]
    assert [group["class_label"] for group in groups] == ["七(1)班", "七(2)班"]
    assert [item["content_text"] for item in groups[0]["notes"]] == ["有理数"]
    assert groups[1]["notes"] == []


def test_api_validation_errors(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    # pydantic 范围校验：week_no 超界。
    response = client.put(
        "/api/class-teacher/daily/timetable/week-anchor",
        headers=headers,
        json={"date": MONDAY, "week_no": 41},
    )
    assert response.status_code == 422
    # pydantic 长度校验：course_text 超过 50。
    response = client.put(
        "/api/class-teacher/daily/timetable/regular-cell",
        headers=headers,
        json={
            "day_of_week": 1,
            "slot_key": "lesson:1",
            "course_text": "长" * 51,
        },
    )
    assert response.status_code == 422
    # 服务端日期校验：非法日期。
    with pytest.raises(ApiError) as invalid:
        client.get("/api/class-teacher/daily/timetable?week_start=2026-13-99")
    assert invalid.value.status_code == 422
    # 服务端 slot 校验：不存在的自定义时段。
    with pytest.raises(ApiError) as bad_slot:
        client.put(
            "/api/class-teacher/daily/timetable/regular-cell",
            headers=headers,
            json={
                "day_of_week": 1,
                "slot_key": f"custom:{'0' * 32}",
                "course_text": "数学",
            },
        )
    assert bad_slot.value.status_code == 422
