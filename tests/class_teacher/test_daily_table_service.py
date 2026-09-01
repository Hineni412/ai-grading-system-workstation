from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.daily_table_service import DailyTableService
from backend.class_teacher.encrypted_database import EncryptedDatabase
from backend.class_teacher.errors import VaultError
from backend.class_teacher.existing_student_roster import ExistingStudent
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(
            project_root=PROJECT_ROOT,
            migration_project_root=PROJECT_ROOT,
        ),
    )


class _FakeRosterSource:
    """注入式假名单源，不依赖真实阅卷库。"""

    def __init__(self, items: list[ExistingStudent]) -> None:
        self.items = items

    def snapshot(self) -> tuple[list[ExistingStudent], str]:
        return list(self.items), "fake-revision"


def _roster() -> list[ExistingStudent]:
    return [
        ExistingStudent(
            source_key="1", student_code="01",
            display_name="张三", class_label="七(1)班",
        ),
        ExistingStudent(
            source_key="2", student_code="02",
            display_name="李四", class_label="七(1)班",
        ),
        ExistingStudent(
            source_key="3", student_code="01",
            display_name="王五", class_label="七(2)班",
        ),
    ]


def _service(
    tmp_path: Path,
    roster_items: list[ExistingStudent] | None = None,
) -> DailyTableService:
    database = EncryptedDatabase(_context(tmp_path))

    def ready(_token: str = "") -> bytes:
        if not database.exists:
            database.initialize_schema()
        return b""

    return DailyTableService(
        database,
        ready,
        _FakeRosterSource(_roster() if roster_items is None else roster_items),
    )


def _write_fake_grading_db(context: WorkspaceContext) -> None:
    """在 VaultService 默认名单路径放一个最小 students 表。"""
    context.root.mkdir(parents=True, exist_ok=True)
    database_path = context.root / "grading.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                student_code TEXT,
                name TEXT,
                class_name TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, ?)",
            [
                ("01", "张三", "七(1)班"),
                ("02", "李四", "七(1)班"),
                ("01", "王五", "七(2)班"),
            ],
        )


def _client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    context = _context(tmp_path)
    _write_fake_grading_db(context)
    service = VaultService(context)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app), {
        "x-class-teacher-client": "class-teacher-browser-v1",
    }


def _create_table(service: DailyTableService) -> dict:
    return service.create_table(
        title="研学回执统计",
        class_labels=["七(1)班", "七(2)班"],
        columns=[
            {"name": "已交", "col_type": "check"},
            {"name": "备注", "col_type": "text"},
        ],
    )


# ----------------------------------------------------------------------
# 建表与名单快照


def test_create_table_snapshots_roster_rows(tmp_path: Path) -> None:
    service = _service(tmp_path)

    created = _create_table(service)

    assert created["empty_class_labels"] == []
    table = created["table"]
    assert table["title"] == "研学回执统计"
    assert table["status"] == "active"
    assert [column["name"] for column in created["columns"]] == ["已交", "备注"]
    assert [column["position"] for column in created["columns"]] == [0, 1]
    assert created["columns"][0]["options"] == []
    assert created["columns"][1]["options"] == []
    rows = created["rows"]
    assert [row["display_name"] for row in rows] == ["张三", "李四", "王五"]
    assert [row["student_ref"] for row in rows] == [
        "七(1)班|01",
        "七(1)班|02",
        "七(2)班|01",
    ]
    assert [row["class_label"] for row in rows] == [
        "七(1)班", "七(1)班", "七(2)班",
    ]
    assert [row["position"] for row in rows] == [0, 1, 2]
    assert created["cells"] == {}

    listed = service.list_tables()["tables"]
    assert len(listed) == 1
    assert listed[0]["id"] == table["id"]
    assert listed[0]["row_count"] == 3
    assert listed[0]["column_count"] == 2


def test_snapshot_rows_not_rewritten_by_roster_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _create_table(service)

    # 名单后续变动（转学、改名）不回写已建表格。
    service.roster_source.items = [
        ExistingStudent(
            source_key="9", student_code="99",
            display_name="新同学", class_label="七(1)班",
        )
    ]

    detail = service.get_table(created["table"]["id"])
    assert [row["display_name"] for row in detail["rows"]] == [
        "张三", "李四", "王五",
    ]


def test_create_table_with_class_without_roster(tmp_path: Path) -> None:
    service = _service(tmp_path)

    created = service.create_table(
        title="部分班级有名单",
        class_labels=["七(1)班", "七(9)班"],
        columns=[],
    )

    assert created["empty_class_labels"] == ["七(9)班"]
    assert [row["class_label"] for row in created["rows"]] == ["七(1)班"] * 2


def test_create_table_with_empty_roster(tmp_path: Path) -> None:
    service = _service(tmp_path, roster_items=[])

    assert service.roster_classes() == {"classes": []}
    created = service.create_table(
        title="空名单表格",
        class_labels=["七(1)班"],
        columns=[{"name": "已交", "col_type": "check"}],
    )

    assert created["rows"] == []
    assert created["empty_class_labels"] == ["七(1)班"]
    listed = service.list_tables()["tables"]
    assert listed[0]["row_count"] == 0


def test_roster_classes_groups_and_counts(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service.roster_classes() == {
        "classes": [
            {"class_label": "七(1)班", "student_count": 2},
            {"class_label": "七(2)班", "student_count": 1},
        ]
    }


def test_create_table_validation(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(VaultError) as empty_title:
        service.create_table(title="  ", class_labels=["七(1)班"])
    assert empty_title.value.code == "daily_table_title_required"
    with pytest.raises(VaultError) as long_title:
        service.create_table(title="长" * 51, class_labels=["七(1)班"])
    assert long_title.value.code == "daily_table_title_too_long"
    with pytest.raises(VaultError) as long_column:
        service.create_table(
            title="表格",
            class_labels=["七(1)班"],
            columns=[{"name": "列" * 31, "col_type": "text"}],
        )
    assert long_column.value.code == "daily_table_column_name_too_long"
    with pytest.raises(VaultError) as bad_type:
        service.create_table(
            title="表格",
            class_labels=["七(1)班"],
            columns=[{"name": "列", "col_type": "multiline"}],
        )
    assert bad_type.value.code == "daily_table_column_type_invalid"
    with pytest.raises(VaultError) as long_option:
        service.create_table(
            title="表格",
            class_labels=["七(1)班"],
            columns=[
                {
                    "name": "选择",
                    "col_type": "select",
                    "options": ["项" * 31],
                }
            ],
        )
    assert long_option.value.code == "daily_table_option_too_long"
    with pytest.raises(VaultError) as too_many_options:
        service.create_table(
            title="表格",
            class_labels=["七(1)班"],
            columns=[
                {
                    "name": "选择",
                    "col_type": "select",
                    "options": [f"选项{index}" for index in range(21)],
                }
            ],
        )
    assert too_many_options.value.code == "daily_table_options_too_many"


def test_missing_table_operations_raise_not_found(tmp_path: Path) -> None:
    service = _service(tmp_path)

    for action in (
        lambda: service.get_table("missing"),
        lambda: service.update_table("missing", title="x"),
        lambda: service.delete_table("missing"),
        lambda: service.add_column("missing", name="列", col_type="text"),
        lambda: service.put_cells(
            "missing", updates=[{"row_id": "r", "column_id": "c", "value_text": ""}]
        ),
        lambda: service.export_csv("missing"),
    ):
        with pytest.raises(VaultError) as missing:
            action()
        assert missing.value.code == "daily_table_not_found"
        assert missing.value.status_code == 404


def test_data_survives_service_recreation(tmp_path: Path) -> None:
    context = _context(tmp_path)

    def build() -> DailyTableService:
        database = EncryptedDatabase(context)

        def ready(_token: str = "") -> bytes:
            if not database.exists:
                database.initialize_schema()
            return b""

        return DailyTableService(database, ready, _FakeRosterSource(_roster()))

    created = _create_table(build())

    detail = build().get_table(created["table"]["id"])
    assert detail["table"]["title"] == "研学回执统计"
    assert len(detail["rows"]) == 3


# ----------------------------------------------------------------------
# 列的增删改


def test_column_add_reorder_update_and_cascade_delete(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _create_table(service)
    table_id = created["table"]["id"]
    check_column = created["columns"][0]
    first_row = created["rows"][0]
    service.put_cells(
        table_id,
        updates=[
            {
                "row_id": first_row["id"],
                "column_id": check_column["id"],
                "value_text": "1",
            }
        ],
    )

    # 默认追加到末尾。
    score = service.add_column(table_id, name="分数", col_type="number")
    assert score["position"] == 2
    # 指定位置插入，后续列顺移。
    inserted = service.add_column(
        table_id, name="插队的列", col_type="text", position=0
    )
    assert inserted["position"] == 0
    names = [column["name"] for column in service.get_table(table_id)["columns"]]
    assert names == ["插队的列", "已交", "备注", "分数"]

    # 改名 + 移动位置。
    moved = service.update_column(
        table_id, check_column["id"], name="已上交", position=2
    )
    assert moved["name"] == "已上交"
    assert moved["position"] == 2
    names = [column["name"] for column in service.get_table(table_id)["columns"]]
    assert names == ["插队的列", "备注", "已上交", "分数"]

    # 改类型只改元数据，不清空已有单元格值。
    service.put_cells(
        table_id,
        updates=[
            {
                "row_id": first_row["id"],
                "column_id": score["id"],
                "value_text": "95",
            }
        ],
    )
    changed = service.update_column(table_id, score["id"], col_type="text")
    assert changed["col_type"] == "text"
    detail = service.get_table(table_id)
    assert detail["cells"][first_row["id"]][score["id"]] == "95"

    # select 列改选项。
    select_column = service.add_column(
        table_id, name="等级", col_type="select", options=["优", "良"]
    )
    updated = service.update_column(
        table_id, select_column["id"], options=["优", "良", "差"]
    )
    assert updated["options"] == ["优", "良", "差"]
    # select 改成 text 后选项清空。
    cleared = service.update_column(table_id, select_column["id"], col_type="text")
    assert cleared["options"] == []

    # 删列连带删该列单元格，剩余列位置重新紧凑排列。
    removed = service.delete_column(table_id, check_column["id"])
    assert removed["removed_cells"] == 1
    detail = service.get_table(table_id)
    assert check_column["id"] not in [
        column["id"] for column in detail["columns"]
    ]
    assert [column["position"] for column in detail["columns"]] == [0, 1, 2, 3]
    assert all(
        check_column["id"] not in row_cells
        for row_cells in detail["cells"].values()
    )

    with pytest.raises(VaultError) as missing_column:
        service.delete_column(table_id, check_column["id"])
    assert missing_column.value.code == "daily_table_column_not_found"
    assert missing_column.value.status_code == 404


# ----------------------------------------------------------------------
# 单元格校验与批量提交


def _typed_table(service: DailyTableService) -> dict:
    return service.create_table(
        title="类型校验表",
        class_labels=["七(1)班"],
        columns=[
            {"name": "已交", "col_type": "check"},
            {"name": "分数", "col_type": "number"},
            {"name": "日期", "col_type": "date"},
            {"name": "等级", "col_type": "select", "options": ["优", "良"]},
            {"name": "备注", "col_type": "text"},
        ],
    )


def test_cells_batch_commit_and_empty_value_deletes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _typed_table(service)
    table_id = created["table"]["id"]
    row_id = created["rows"][0]["id"]
    check_col, number_col, date_col, select_col, text_col = [
        column["id"] for column in created["columns"]
    ]

    result = service.put_cells(
        table_id,
        updates=[
            {"row_id": row_id, "column_id": check_col, "value_text": "1"},
            {"row_id": row_id, "column_id": number_col, "value_text": "95.5"},
            {"row_id": row_id, "column_id": date_col, "value_text": "2026-09-01"},
            {"row_id": row_id, "column_id": select_col, "value_text": "优"},
            {"row_id": row_id, "column_id": text_col, "value_text": "已核对"},
        ],
    )
    assert result == {"updated": 5}
    cells = service.get_table(table_id)["cells"][row_id]
    assert cells == {
        check_col: "1",
        number_col: "95.5",
        date_col: "2026-09-01",
        select_col: "优",
        text_col: "已核对",
    }

    # 覆盖写与空值删除。
    service.put_cells(
        table_id,
        updates=[
            {"row_id": row_id, "column_id": text_col, "value_text": ""},
            {"row_id": row_id, "column_id": check_col, "value_text": "0"},
        ],
    )
    cells = service.get_table(table_id)["cells"][row_id]
    assert text_col not in cells
    assert cells[check_col] == "0"


def test_cells_type_validation_and_atomic_rollback(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _typed_table(service)
    table_id = created["table"]["id"]
    row_id = created["rows"][0]["id"]
    check_col, number_col, date_col, select_col, text_col = [
        column["id"] for column in created["columns"]
    ]

    invalid_batches = [
        {"row_id": row_id, "column_id": check_col, "value_text": "2"},
        {"row_id": row_id, "column_id": number_col, "value_text": "abc"},
        {"row_id": row_id, "column_id": number_col, "value_text": "inf"},
        {"row_id": row_id, "column_id": number_col, "value_text": "nan"},
        {"row_id": row_id, "column_id": date_col, "value_text": "2026-13-01"},
        {"row_id": row_id, "column_id": date_col, "value_text": "20260901"},
        {"row_id": row_id, "column_id": select_col, "value_text": "差"},
        {"row_id": row_id, "column_id": text_col, "value_text": "长" * 501},
    ]
    for invalid in invalid_batches:
        with pytest.raises(VaultError) as rejected:
            service.put_cells(table_id, updates=[invalid])
        assert rejected.value.status_code == 422
    assert service.get_table(table_id)["cells"] == {}

    # 批量中有一条无效 → 整体回滚，前面的有效修改也不落库。
    with pytest.raises(VaultError):
        service.put_cells(
            table_id,
            updates=[
                {"row_id": row_id, "column_id": check_col, "value_text": "1"},
                {"row_id": row_id, "column_id": number_col, "value_text": "坏"},
            ],
        )
    assert service.get_table(table_id)["cells"] == {}

    # 行或列引用失效。
    with pytest.raises(VaultError) as bad_ref:
        service.put_cells(
            table_id,
            updates=[
                {"row_id": "missing", "column_id": check_col, "value_text": "1"}
            ],
        )
    assert bad_ref.value.code == "daily_table_ref_invalid"
    with pytest.raises(VaultError) as empty_updates:
        service.put_cells(table_id, updates=[])
    assert empty_updates.value.code == "daily_table_updates_required"


# ----------------------------------------------------------------------
# 归档 / 恢复 / 删除


def test_archive_restore_rename_and_delete(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _create_table(service)
    table_id = created["table"]["id"]

    archived = service.update_table(table_id, status="archived")
    assert archived["table"]["status"] == "archived"
    assert service.list_tables(status="active")["tables"] == []
    assert len(service.list_tables(status="archived")["tables"]) == 1
    assert len(service.list_tables(status="all")["tables"]) == 1

    restored = service.update_table(table_id, status="active", title="新名字")
    assert restored["table"]["status"] == "active"
    assert restored["table"]["title"] == "新名字"

    with pytest.raises(VaultError) as bad_status:
        service.update_table(table_id, status="locked")
    assert bad_status.value.code == "daily_table_status_invalid"
    with pytest.raises(VaultError):
        service.list_tables(status="unknown")

    removed = service.delete_table(table_id)
    assert removed == {"deleted_table_id": table_id}
    assert service.list_tables()["tables"] == []
    with pytest.raises(VaultError) as gone:
        service.get_table(table_id)
    assert gone.value.status_code == 404


# ----------------------------------------------------------------------
# CSV 导出


def test_export_csv_bom_escaping_and_formula_guard(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.create_table(
        title='研学"回执"统计',
        class_labels=["七(1)班"],
        columns=[
            {"name": "已交", "col_type": "check"},
            {"name": "备注", "col_type": "text"},
        ],
    )
    table_id = created["table"]["id"]
    rows = created["rows"]
    check_col, text_col = [column["id"] for column in created["columns"]]
    service.put_cells(
        table_id,
        updates=[
            {"row_id": rows[0]["id"], "column_id": check_col, "value_text": "1"},
            {
                "row_id": rows[0]["id"],
                "column_id": text_col,
                "value_text": "含逗号,与\"引号\"\n与换行",
            },
            {
                "row_id": rows[1]["id"],
                "column_id": text_col,
                "value_text": "=SUM(A1:A2)",
            },
        ],
    )

    filename, content = service.export_csv(table_id)

    assert filename == "研学_回执_统计.csv"
    assert content.startswith("\ufeff")
    parsed = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
    assert parsed[0] == ["姓名", "班级", "已交", "备注"]
    assert parsed[1] == ["张三", "七(1)班", "1", '含逗号,与"引号"\n与换行']
    # 以 = 开头的值前加单引号防公式注入。
    assert parsed[2] == ["李四", "七(1)班", "", "'=SUM(A1:A2)"]


def test_export_csv_formula_prefixes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.create_table(
        title="公式注入",
        class_labels=["七(1)班"],
        columns=[{"name": "备注", "col_type": "text"}],
    )
    table_id = created["table"]["id"]
    row_id = created["rows"][0]["id"]
    text_col = created["columns"][0]["id"]
    for index, value in enumerate(["=1", "+1", "-1", "@x", "正常"]):
        service.put_cells(
            table_id,
            updates=[
                {"row_id": row_id, "column_id": text_col, "value_text": value}
            ],
        )
        _filename, content = service.export_csv(table_id)
        cell = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))[1][2]
        expected = f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
        assert cell == expected, f"第 {index} 个值 {value!r} 处理错误"


# ----------------------------------------------------------------------
# API 层


def test_api_fresh_install_returns_empty_tables(tmp_path: Path) -> None:
    context = _context(tmp_path)
    service = VaultService(context)
    app = FastAPI()
    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    client = TestClient(app)

    response = client.get("/api/class-teacher/daily/tables")

    assert response.status_code == 200
    assert response.json() == {"tables": []}
    classes = client.get("/api/class-teacher/daily/roster-classes")
    assert classes.status_code == 200
    assert classes.json() == {"classes": []}


def test_api_mutations_require_trusted_client_header(tmp_path: Path) -> None:
    client, _headers = _client(tmp_path)

    with pytest.raises(ApiError) as blocked:
        client.post(
            "/api/class-teacher/daily/tables",
            json={"title": "表格", "class_labels": ["七(1)班"]},
        )
    assert blocked.value.status_code == 403
    with pytest.raises(ApiError):
        client.put(
            "/api/class-teacher/daily/tables/any/cells",
            json={"updates": [{"row_id": "r", "column_id": "c", "value_text": ""}]},
        )


def test_api_full_table_flow(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    classes = client.get("/api/class-teacher/daily/roster-classes")
    assert classes.status_code == 200
    assert classes.json() == {
        "classes": [
            {"class_label": "七(1)班", "student_count": 2},
            {"class_label": "七(2)班", "student_count": 1},
        ]
    }

    created = client.post(
        "/api/class-teacher/daily/tables",
        headers=headers,
        json={
            "title": "研学回执统计",
            "class_labels": ["七(1)班", "七(2)班"],
            "columns": [
                {"name": "已交", "col_type": "check"},
                {"name": "备注", "col_type": "text"},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["empty_class_labels"] == []
    assert len(payload["rows"]) == 3
    table_id = payload["table"]["id"]
    row_id = payload["rows"][0]["id"]
    check_col = payload["columns"][0]["id"]

    listed = client.get(
        "/api/class-teacher/daily/tables", params={"status": "active"}
    )
    assert listed.status_code == 200
    assert listed.json()["tables"][0]["row_count"] == 3

    updated = client.put(
        f"/api/class-teacher/daily/tables/{table_id}/cells",
        headers=headers,
        json={
            "updates": [
                {"row_id": row_id, "column_id": check_col, "value_text": "1"}
            ]
        },
    )
    assert updated.status_code == 200
    detail = client.get(f"/api/class-teacher/daily/tables/{table_id}")
    assert detail.status_code == 200
    assert detail.json()["cells"][row_id][check_col] == "1"

    archived = client.patch(
        f"/api/class-teacher/daily/tables/{table_id}",
        headers=headers,
        json={"status": "archived"},
    )
    assert archived.status_code == 200
    assert archived.json()["table"]["status"] == "archived"
    assert client.get(
        "/api/class-teacher/daily/tables", params={"status": "active"}
    ).json() == {"tables": []}

    exported = client.get(
        f"/api/class-teacher/daily/tables/{table_id}/export.csv"
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    disposition = exported.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    from urllib.parse import quote

    assert quote("研学回执统计.csv") in disposition
    assert exported.content.startswith(b"\xef\xbb\xbf")
    text = exported.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "姓名,班级,已交,备注"

    removed = client.delete(
        f"/api/class-teacher/daily/tables/{table_id}", headers=headers
    )
    assert removed.status_code == 200
    with pytest.raises(ApiError) as gone:
        client.get(f"/api/class-teacher/daily/tables/{table_id}")
    assert gone.value.status_code == 404


def test_api_validation_errors(tmp_path: Path) -> None:
    client, headers = _client(tmp_path)

    # pydantic 长度校验：表名超过 50。
    response = client.post(
        "/api/class-teacher/daily/tables",
        headers=headers,
        json={"title": "长" * 51, "class_labels": ["七(1)班"]},
    )
    assert response.status_code == 422
    # pydantic 类型校验：未知列类型。
    response = client.post(
        "/api/class-teacher/daily/tables",
        headers=headers,
        json={
            "title": "表格",
            "class_labels": ["七(1)班"],
            "columns": [{"name": "列", "col_type": "multiline"}],
        },
    )
    assert response.status_code == 422
    # 服务端校验：单元格值不合法。
    created = client.post(
        "/api/class-teacher/daily/tables",
        headers=headers,
        json={
            "title": "表格",
            "class_labels": ["七(1)班"],
            "columns": [{"name": "已交", "col_type": "check"}],
        },
    ).json()
    with pytest.raises(ApiError) as invalid:
        client.put(
            f"/api/class-teacher/daily/tables/{created['table']['id']}/cells",
            headers=headers,
            json={
                "updates": [
                    {
                        "row_id": created["rows"][0]["id"],
                        "column_id": created["columns"][0]["id"],
                        "value_text": "2",
                    }
                ]
            },
        )
    assert invalid.value.status_code == 422
