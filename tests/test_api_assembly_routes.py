from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_assembly_workspace_service,
    get_job_file_service,
    get_job_manager,
    get_question_bank_read_service,
)
from backend.files.service import JobFileService
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.database.schema import initialize_database
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
from question_bank.services.question_read_service import QuestionBankReadService


def _assembly_client(tmp_path: Path) -> tuple[TestClient, JobManager]:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (1, '匿名试卷', 'private.docx', 'success')
            """
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, difficulty, is_deleted
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            """,
            [
                (11, "1", "选择题", "（5分）第一题", "A", "2", 0),
                (12, "2", "解答题", "没有标分的题", "过程", "7", 0),
                (13, "3", "填空题", "已删除题", "0", "3", 1),
            ],
        )
        conn.commit()

    workspace = AssemblyWorkspaceService(data_root)
    read_service = QuestionBankReadService(db_path, data_root=data_root)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        data_root=data_root,
        question_bank_db_path=db_path,
    )
    app = create_app()
    app.dependency_overrides[get_assembly_workspace_service] = lambda: workspace
    app.dependency_overrides[get_question_bank_read_service] = lambda: read_service
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_job_file_service] = lambda: JobFileService(
        tmp_path / "reports",
        assembly_outputs_dir=workspace.exports_root,
    )
    return TestClient(app), manager


def test_assembly_draft_and_bulk_questions_use_safe_public_contract(tmp_path: Path) -> None:
    client, manager = _assembly_client(tmp_path)
    try:
        _exercise_assembly_draft_and_bulk_questions_use_safe_public_contract(
            client,
            manager,
        )
    finally:
        manager.shutdown()


def _exercise_assembly_draft_and_bulk_questions_use_safe_public_contract(
    client: TestClient,
    manager: JobManager,
) -> None:
    initial = client.get("/api/question-assembly/draft")
    assert initial.status_code == 200

    saved = client.put(
        "/api/question-assembly/draft",
        json={
            "expected_revision": initial.json()["revision"],
            "draft": {
                "basket_ids": [12, 11, 11, 13],
                "order_ids": [11, 12, 13],
                "sections": [],
                "title": "匿名练习",
                "header_text": "",
                "include_answer": True,
                "layout_mode": "sequential",
                "preview_mode": "student",
            },
        },
    )
    assert saved.status_code == 200
    assert saved.json()["basket_ids"] == [12, 11, 13]

    resolved = client.get(
        "/api/question-assembly/questions",
        params=[("question_ids", 11), ("question_ids", 13), ("question_ids", 12)],
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert [item["id"] for item in body["items"]] == [11, 12]
    assert body["missing_question_ids"] == [13]
    assert body["items"][0]["score_value"] == 5
    assert body["items"][1]["score_value"] is None
    assert "source_file" not in repr(body)
    assert "private.docx" not in repr(body)

    stale_draft = initial.json()
    stale_draft.pop("revision")
    stale = client.put(
        "/api/question-assembly/draft",
        json={
            "expected_revision": initial.json()["revision"],
            "draft": stale_draft,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assembly_draft_conflict"
    assert stale.json()["error"]["details"]["current_revision"] == saved.json()["revision"]

    current = client.get("/api/question-assembly/draft").json()
    export_ready = client.put(
        "/api/question-assembly/draft",
        json={
            "expected_revision": current["revision"],
            "draft": _draft_write_payload(
                current,
                basket_ids=[11, 12],
                order_ids=[11, 12],
            ),
        },
    )
    assert export_ready.status_code == 200

    submit = client.post(
        "/api/question-assembly/export",
        json={"draft_revision": export_ready.json()["revision"], "format": "markdown"},
    )
    assert submit.status_code == 202
    manager.wait(submit.json()["id"], timeout=5)

    job = client.get(f"/api/jobs/{submit.json()['id']}")
    assert job.status_code == 200
    body = job.json()
    assert body["status"] == "succeeded"
    assert body["payload"] == {
        "draft_revision": export_ready.json()["revision"],
        "format": "markdown",
        "question_count": 2,
    }
    assert body["result"]["question_count"] == 2
    assert body["result"]["download_url"] == f"/api/jobs/{submit.json()['id']}/download"
    assert "file_path" not in body["result"]
    assert "basket_ids" not in repr(body)
    assert "question_text" not in repr(body)
    assert "private.docx" not in repr(body)

    download = client.get(body["result"]["download_url"])
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert "匿名练习" in download.text

    after_export = client.get("/api/question-assembly/draft").json()
    records = client.get("/api/question-assembly/records").json()
    restore = client.post(
        f"/api/question-assembly/records/{records['items'][0]['id']}/restore",
        json={"expected_revision": after_export["revision"]},
    )
    assert restore.status_code == 200
    assert restore.json()["order_ids"] == [11, 12]
    assert restore.json()["title"] == "匿名练习"
    assert "file_path" not in repr(restore.json())


def test_assembly_export_rejects_stale_revision_and_generic_submit(tmp_path: Path) -> None:
    client, manager = _assembly_client(tmp_path)
    try:
        initial = client.get("/api/question-assembly/draft").json()
        saved = client.put(
            "/api/question-assembly/draft",
            json={
                "expected_revision": initial["revision"],
                "draft": _draft_write_payload(
                    initial,
                    basket_ids=[11],
                    order_ids=[11],
                ),
            },
        ).json()
        stale = client.post(
            "/api/question-assembly/export",
            json={"draft_revision": initial["revision"], "format": "markdown"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "assembly_draft_conflict"

        generic = client.post(
            "/api/jobs/assembly_export",
            json={
                "payload": {
                    "draft_revision": saved["revision"],
                    "draft": saved,
                    "format": "markdown",
                }
            },
        )
        assert generic.status_code == 422
        assert generic.json()["error"]["code"] == "dedicated_job_endpoint_required"
    finally:
        manager.shutdown()


def test_assembly_export_retry_reuses_private_payload_without_exposing_draft(
    tmp_path: Path,
) -> None:
    client, manager = _assembly_client(tmp_path)
    try:
        initial = client.get("/api/question-assembly/draft").json()
        failed_draft = client.put(
            "/api/question-assembly/draft",
            json={
                "expected_revision": initial["revision"],
                "draft": _draft_write_payload(
                    initial,
                    basket_ids=[999],
                    order_ids=[999],
                ),
            },
        ).json()
        submit = client.post(
            "/api/question-assembly/export",
            json={"draft_revision": failed_draft["revision"], "format": "markdown"},
        )
        manager.wait(submit.json()["id"], timeout=5)
        failed_job = client.get(f"/api/jobs/{submit.json()['id']}").json()
        assert failed_job["status"] == "failed"
        assert "basket_ids" not in repr(failed_job)

        retry = client.post(
            f"/api/question-assembly/exports/{submit.json()['id']}/retry"
        )
        assert retry.status_code == 202
        assert retry.json()["payload"] == {
            "draft_revision": failed_draft["revision"],
            "format": "markdown",
            "question_count": 1,
            "retry_of_job_id": submit.json()["id"],
        }
        assert "basket_ids" not in repr(retry.json())

        current = client.get("/api/question-assembly/draft").json()
        good_draft = client.put(
            "/api/question-assembly/draft",
            json={
                "expected_revision": current["revision"],
                "draft": _draft_write_payload(
                    current,
                    basket_ids=[11],
                    order_ids=[11],
                ),
            },
        ).json()
        good = client.post(
            "/api/question-assembly/export",
            json={"draft_revision": good_draft["revision"], "format": "markdown"},
        )
        manager.wait(good.json()["id"], timeout=5)
        rejected = client.post(
            f"/api/question-assembly/exports/{good.json()['id']}/retry"
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "assembly_export_retry_not_available"
    finally:
        manager.shutdown()


def _draft_write_payload(
    draft: dict,
    *,
    basket_ids: list[int],
    order_ids: list[int],
) -> dict:
    payload = dict(draft)
    payload.pop("revision", None)
    payload["basket_ids"] = basket_ids
    payload["order_ids"] = order_ids
    return payload
