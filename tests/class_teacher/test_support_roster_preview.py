from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.api.app import ApiError
from backend.class_teacher.api.router import create_router
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> VaultService:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.execute(
            "INSERT INTO students VALUES (1, 'A001', '合成预览学生', '一班')"
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
        )
    )
    service.ensure_plaintext_ready()
    return service


def _client(service: VaultService) -> TestClient:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code}},
        )

    app.state.workspace_services = {"class-teacher": service}
    app.include_router(create_router(), prefix="/api/class-teacher")
    return TestClient(app)


def _roster_ref(service: VaultService) -> str:
    candidates = service.class_roster.ai_candidates(token="", class_label="一班")
    assert len(candidates) == 1
    return str(candidates[0]["id"])


def _subject_count(service: VaultService) -> int:
    with closing(service.database.connect()) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM student_subject_links"
            ).fetchone()[0]
        )


def test_header_and_card_preview_resolve_stable_roster_ref_without_writing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    client = _client(service)
    ref = _roster_ref(service)

    header = client.get(
        f"/api/class-teacher/support/subjects/{ref}/workspace-header"
    )
    assert header.status_code == 200
    body = header.json()
    assert body["profile_state"] == "not_created"
    assert body["subject_id"] == ref
    assert body["display_name"] == "合成预览学生"
    assert body["class_label"] == "一班"
    assert body["source_student_id"] == "1"
    assert body["confirmed_entry_count"] == 0
    assert body["support_record_count"] == 0
    assert body["related_affairs"] == []

    card = client.get(f"/api/class-teacher/support/subjects/{ref}/student-card")
    assert card.status_code == 200
    card_body = card.json()
    assert card_body["profile_state"] == "not_created"
    assert card_body["subject"]["display_name"] == "合成预览学生"
    assert card_body["entries"] == []
    assert card_body["current_profile"] is None
    assert card_body["existing_records"] == []
    assert card_body["support_plans"] == []

    # Preview reads must never create a student profile as a side effect.
    assert _subject_count(service) == 0


def test_preview_legacy_opaque_ref_stays_not_found(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _client(service)
    # 兼容输入：旧格式 64 位十六进制临时编号按「档案不存在」处理。
    missing = "f" * 64

    header = client.get(
        f"/api/class-teacher/support/subjects/{missing}/workspace-header"
    )
    assert header.status_code == 404
    card = client.get(
        f"/api/class-teacher/support/subjects/{missing}/student-card"
    )
    assert card.status_code == 404


def test_preview_after_activation_stays_not_created_until_profile_created(
    tmp_path: Path,
) -> None:
    """激活只写花名册状态、不再建档：激活后预览未建档学生仍是 not_created。"""
    service = _service(tmp_path)
    client = _client(service)
    browse = service.class_roster.browse(token="", class_label="一班")
    service.class_roster.replace_current(
        token="",
        operation_id="roster-preview-activate",
        expected_source_revision=str(browse["source_revision"]),
        class_label="一班",
    )
    assert _subject_count(service) == 0
    ref = _roster_ref(service)

    header = client.get(
        f"/api/class-teacher/support/subjects/{ref}/workspace-header"
    )
    assert header.status_code == 200
    body = header.json()
    assert body["profile_state"] == "not_created"
    assert body["subject_id"] == ref
    assert body["display_name"] == "合成预览学生"

    # 建档（经 adopt 链路的建档入口）后同一稳定标识预览到档案
    created = service.support.create_subject_for_roster_source(
        token="",
        operation_id="roster-preview-create",
        source_student_id="1",
        legacy_student_code="A001",
        display_name="合成预览学生",
        class_label="一班",
    )
    header = client.get(
        f"/api/class-teacher/support/subjects/{ref}/workspace-header"
    )
    assert header.status_code == 200
    body = header.json()
    assert body["profile_state"] == "created"
    assert body["display_name"] == "合成预览学生"
    assert body["subject_id"] == str(created["subject_id"])

    card = client.get(f"/api/class-teacher/support/subjects/{ref}/student-card")
    assert card.status_code == 200
    card_body = card.json()
    assert card_body["subject"]["subject_id"] == body["subject_id"]
    assert card_body["subject"]["display_name"] == "合成预览学生"


def test_preview_resolves_subject_created_by_adopt_without_roster_activation(
    tmp_path: Path,
) -> None:
    """adopt 直接建档不写花名册成员表，预览仍须按稳定标识找到档案。"""
    service = _service(tmp_path)
    client = _client(service)
    created = service.support.create_subject_for_roster_source(
        token="",
        operation_id="preview-adopt-created",
        source_student_id="1",
        legacy_student_code="A001",
        display_name="合成预览学生",
        class_label="一班",
    )
    assert _subject_count(service) == 1
    ref = _roster_ref(service)

    header = client.get(
        f"/api/class-teacher/support/subjects/{ref}/workspace-header"
    )
    assert header.status_code == 200
    body = header.json()
    assert body["profile_state"] == "created"
    assert body["subject_id"] == str(created["subject_id"])

    card = client.get(f"/api/class-teacher/support/subjects/{ref}/student-card")
    assert card.status_code == 200
    card_body = card.json()
    assert card_body["subject"]["subject_id"] == str(created["subject_id"])
    assert card_body["subject"]["display_name"] == "合成预览学生"
