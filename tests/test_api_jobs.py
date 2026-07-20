from __future__ import annotations

import sqlite3
import threading
import time
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_manager(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, manager
        finally:
            manager.shutdown()


def test_jobs_list_filters_session_and_returns_safe_summaries(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    first = manager.store.create_job(
        "grading_run",
        {"session_id": 7, "path": "C:/private/a"},
    )
    manager.store.create_job(
        "report_export",
        {"session_id": 8, "token": "secret"},
    )
    manager.store.update_progress(
        first.id,
        progress=0.4,
        stage="grading",
        detail="processing",
    )

    response = client.get(
        "/api/jobs",
        params={"session_id": 7, "page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == first.id
    assert response.json()["items"][0]["detail"] == "processing"
    assert set(response.json()["items"][0]) == {
        "id",
        "job_type",
        "status",
        "progress",
        "stage",
        "detail",
        "created_at",
        "started_at",
        "updated_at",
        "finished_at",
    }
    assert "payload" not in response.text
    assert "result" not in response.text
    assert "error" not in response.text
    assert "C:/private" not in response.text
    assert [item["id"] for item in response.json()["items"]] == [first.id]


def test_jobs_list_ignores_unsafe_session_payloads_without_server_error(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    rejected_payloads = (
        '{"session_id":',
        '{"session_id": true}',
        '{"session_id": 7.0}',
        '{"session_id": 0}',
        '{"session_id": -7}',
        '{"session_id": "7garbage"}',
        '{"session_id": " 7 "}',
        '{"session_id": 7e0}',
        '{"session_id": "7e0"}',
        '{"session_id": "7"}',
    )
    with sqlite3.connect(manager.store.db_path) as conn:
        for payload_json in rejected_payloads:
            conn.execute(
                "INSERT INTO jobs (job_type, payload_json, status) "
                "VALUES ('grading_run', ?, 'queued')",
                (payload_json,),
            )
        cursor = conn.execute(
            "INSERT INTO jobs (job_type, payload_json, status) "
            "VALUES ('grading_run', ?, 'queued')",
            ('{"session_id": 7}',),
        )
        accepted_id = int(cursor.lastrowid)

    response = client.get(
        "/api/jobs",
        params={"session_id": 7, "page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [accepted_id]


def test_jobs_list_sanitizes_legacy_detail_values(client_with_manager) -> None:
    client, manager = client_with_manager
    job = manager.store.create_job("grading_run", {"session_id": 7})
    manager.store.update_progress(
        job.id,
        progress=0.4,
        stage="grading",
        detail="Cannot inspect C:/private/student.png",
    )

    response = client.get("/api/jobs", params={"session_id": 7})

    assert response.status_code == 200
    assert response.json()["items"][0]["detail"] == ""
    assert "C:/private" not in response.text


@pytest.mark.parametrize(
    "unsafe_detail, marker",
    [
        ("token=plain-secret", "plain-secret"),
        ('{"payload":{"result":"private-result"}}', "private-result"),
        ("diagnostic payload=private-payload", "private-payload"),
        (
            "Traceback (most recent call last): ValueError: private-stack",
            "private-stack",
        ),
        ("Student wrote a private free-text answer", "private free-text"),
    ],
)
def test_jobs_list_rejects_unrecognized_legacy_detail_text(
    client_with_manager,
    unsafe_detail: str,
    marker: str,
) -> None:
    client, manager = client_with_manager
    job = manager.store.create_job("grading_run", {"session_id": 7})
    manager.store.update_progress(
        job.id,
        progress=0.4,
        stage="grading",
        detail=unsafe_detail,
    )

    response = client.get("/api/jobs", params={"session_id": 7})

    assert response.status_code == 200
    assert response.json()["items"][0]["detail"] == ""
    assert marker not in response.text


@pytest.mark.parametrize(
    "safe_detail",
    [
        "processing",
        "graded=3 failed=1",
        "matched=2 issues=1 pages=4",
        "processing batch 2 of 5",
    ],
)
def test_jobs_list_keeps_whitelisted_stage_and_count_details(
    client_with_manager,
    safe_detail: str,
) -> None:
    client, manager = client_with_manager
    job = manager.store.create_job("grading_run", {"session_id": 7})
    manager.store.update_progress(
        job.id,
        progress=0.4,
        stage="grading",
        detail=safe_detail,
    )

    response = client.get("/api/jobs", params={"session_id": 7})

    assert response.status_code == 200
    assert response.json()["items"][0]["detail"] == safe_detail


def test_jobs_list_accepts_repeated_job_type_and_status_filters(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    queued = manager.store.create_job("grading_run", {"session_id": 7})
    succeeded = manager.store.create_job("report_export", {"session_id": 7})
    ignored = manager.store.create_job("scan_analysis", {"session_id": 7})
    assert manager.store.mark_running(succeeded.id) is True
    manager.store.finish(succeeded.id, "succeeded")

    response = client.get(
        "/api/jobs",
        params=[
            ("job_type", "grading_run"),
            ("job_type", "report_export"),
            ("status", "queued"),
            ("status", "succeeded"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["items"]] == [
        succeeded.id,
        queued.id,
    ]
    assert ignored.id not in {item["id"] for item in response.json()["items"]}


def test_jobs_list_rejects_invalid_status(client_with_manager) -> None:
    client, _manager = client_with_manager

    response = client.get("/api/jobs", params={"status": "complete"})

    assert response.status_code == 422


def test_jobs_list_uses_id_desc_stable_pagination(client_with_manager) -> None:
    client, manager = client_with_manager
    jobs = [
        manager.store.create_job("grading_run", {"session_id": 7})
        for _ in range(4)
    ]

    first_page = client.get("/api/jobs", params={"page": 1, "page_size": 2})
    second_page = client.get("/api/jobs", params={"page": 2, "page_size": 2})

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert first_page.json()["total"] == 4
    assert first_page.json()["total_pages"] == 2
    assert [item["id"] for item in first_page.json()["items"]] == [
        jobs[3].id,
        jobs[2].id,
    ]
    assert [item["id"] for item in second_page.json()["items"]] == [
        jobs[1].id,
        jobs[0].id,
    ]


@pytest.mark.parametrize("page_size", [0, 101])
def test_jobs_list_rejects_page_size_outside_bounds(
    client_with_manager,
    page_size: int,
) -> None:
    client, _manager = client_with_manager

    response = client.get("/api/jobs", params={"page_size": page_size})

    assert response.status_code == 422


def test_jobs_api_submits_and_reads_registered_job(client_with_manager) -> None:
    from backend.jobs.manager import JobContext

    client, manager = client_with_manager

    def handler(context: JobContext) -> None:
        context.report(0.25, "exporting", "building report")

    manager.register("report_export", handler)

    create_response = client.post(
        "/api/jobs/report_export",
        json={"payload": {"session_id": 8}},
    )

    assert create_response.status_code == 202
    created = create_response.json()
    assert created["job_type"] == "report_export"
    assert created["payload"] == {"session_id": 8}

    manager.wait(created["id"], timeout=5)
    get_response = client.get(f"/api/jobs/{created['id']}")

    assert get_response.status_code == 200
    loaded = get_response.json()
    assert loaded["status"] == "succeeded"
    assert loaded["stage"] == "exporting"
    assert loaded["detail"] == "building report"


def test_config_generation_truncation_returns_fixed_actionable_public_error(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    job = manager.store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": "generate",
            "generation_mode": "whole_document",
        },
    )
    assert manager.store.mark_running(job.id)
    manager.store.finish(
        job.id,
        "failed",
        error=(
            "模型因输出长度上限停止，返回结果不完整"
            "（响应摘要: private-hash C:/private/output.json）。"
        ),
    )

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["error"].startswith("模型因输出长度上限停止")
    assert "未自动重试" in response.json()["error"]
    assert "逐题生成" in response.json()["error"]
    assert "private-hash" not in response.text
    assert "C:/private" not in response.text


def test_jobs_api_returns_unified_error_for_unsupported_type(client_with_manager) -> None:
    client, _manager = client_with_manager

    response = client.post(
        "/api/jobs/missing_type",
        json={"payload": {}},
        headers={"x-request-id": "rid-unsupported-job"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_type_not_supported"
    assert response.json()["error"]["request_id"] == "rid-unsupported-job"


@pytest.mark.parametrize(
    "job_type",
    [
        "ops_backup",
        "ops_restore_prepare",
        "ops_migration_prepare",
        "ops_transfer_import_prepare",
        "ops_transfer_export",
    ],
)
def test_generic_jobs_api_rejects_ops_types_before_persistence(
    client_with_manager,
    job_type: str,
) -> None:
    import sqlite3

    client, manager = client_with_manager
    manager.register(job_type, lambda _context: {})

    response = client.post(f"/api/jobs/{job_type}", json={"payload": {}})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dedicated_job_endpoint_required"
    with sqlite3.connect(manager.store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_jobs_api_returns_unified_error_for_missing_job(client_with_manager) -> None:
    client, _manager = client_with_manager

    response = client.get(
        "/api/jobs/404",
        headers={"x-request-id": "rid-missing-job"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing-job"


def test_jobs_api_requests_cancel_for_running_job(client_with_manager) -> None:
    from backend.jobs.manager import JobContext

    client, manager = client_with_manager
    started = threading.Event()
    allow_confirm = threading.Event()

    def handler(context: JobContext) -> None:
        started.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if context.cancel_requested:
                assert allow_confirm.wait(3)
                context.raise_if_cancelled()
            time.sleep(0.01)
        raise AssertionError("cancel request was not visible to handler")

    manager.register("grading_run", handler)
    created = client.post("/api/jobs/grading_run", json={"payload": {}}).json()
    assert started.wait(3)

    cancel_response = client.post(f"/api/jobs/{created['id']}/cancel")

    try:
        assert cancel_response.status_code == 200
        assert cancel_response.json()["cancel_requested"] is True
        assert cancel_response.json()["status"] == "running"
        assert cancel_response.json()["finished_at"] is None
    finally:
        allow_confirm.set()
    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["status"] == "cancelled"


def test_jobs_api_rejects_nested_sensitive_payload_before_persistence(
    client_with_manager,
) -> None:
    import sqlite3

    client, manager = client_with_manager
    manager.register("report_export", lambda _context: {})

    response = client.post(
        "/api/jobs/report_export",
        json={"payload": {"session_id": 8, "config": {"api_key": "secret-value"}}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_job_payload"
    assert "secret-value" not in response.text
    with sqlite3.connect(manager.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "api key",
        "configApiKey",
        "accessToken",
        "access token",
        "token",
        "authToken",
        "oauth_token",
        "id_token",
    ],
)
def test_jobs_api_rejects_canonical_sensitive_key_variants_before_persistence(
    client_with_manager,
    sensitive_key: str,
) -> None:
    import sqlite3

    client, manager = client_with_manager
    manager.register("report_export", lambda _context: {})

    response = client.post(
        "/api/jobs/report_export",
        json={
            "payload": {
                "session_id": 8,
                "nested": [{"config": {sensitive_key: "secret-value"}}],
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_job_payload"
    assert "secret-value" not in response.text
    with sqlite3.connect(manager.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_jobs_api_redacts_internal_paths_and_sensitive_keys_from_public_payload(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    manager.register("report_export", lambda _context: {})

    created = client.post(
        "/api/jobs/report_export",
        json={
            "payload": {
                "session_id": 8,
                "artifact_path": "C:/private/report.xlsx",
                "nested": {"summary": {"count": 1}, "paths": ["C:/private"]},
            }
        },
    ).json()

    assert created["payload"] == {"session_id": 8}
    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["payload"] == created["payload"]
    assert "C:/private" not in str(loaded)


def test_job_response_redacts_sensitive_keys_from_legacy_stored_payload(
    client_with_manager,
) -> None:
    from backend.api.routers.jobs import _job_response

    _client, manager = client_with_manager
    legacy = manager.store.create_job(
        "report_export",
        {
            "session_id": 8,
            "config_api_key": "old-secret",
            "nested": {"summary": {"count": 1}},
        },
    )

    payload = _job_response(legacy).model_dump()["payload"]
    assert payload == {"session_id": 8}
    assert "old-secret" not in str(payload)


@pytest.mark.parametrize(
    "sensitive_key",
    ["token", "authToken", "oauth_token", "id_token"],
)
def test_job_response_redacts_token_variants_from_legacy_payload_and_result(
    client_with_manager,
    sensitive_key: str,
) -> None:
    client, manager = client_with_manager
    legacy = manager.store.create_job(
        "scan_analysis",
        {"session_id": 8, sensitive_key: "old-token"},
    )
    assert manager.store.mark_running(legacy.id) is True
    manager.store.finish(
        legacy.id,
        "succeeded",
        result={"summary": {"count": 1}, sensitive_key: "old-token"},
    )

    response = client.get(f"/api/jobs/{legacy.id}")

    assert response.status_code == 200
    assert response.json()["payload"] == {"session_id": 8}
    assert response.json()["result"] == {"summary": {"count": 1}}
    assert "old-token" not in response.text


def test_job_response_sanitizes_legacy_generic_result_values_recursively(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    legacy = manager.store.create_job("scan_analysis", {"session_id": 8})
    assert manager.store.mark_running(legacy.id) is True
    manager.store.finish(
        legacy.id,
        "succeeded",
        result={
            "session_id": 8,
            "summary": {"count": 1},
            "legacy": {
                "credentials": {"api key": "old-secret"},
                "artifact": "C:\\private\\scan-result.json",
                "public_url": "/api/jobs/8",
                "items": [
                    "safe",
                    "\\\\server\\private\\scan-result.json",
                    "file:///C:/private/scan-result.json",
                    "Cannot inspect C:/private/embedded-scan-result.json",
                    r"Cannot inspect \Users\teacher\private\embedded-scan-result.json",
                    r"Cannot inspect \\server\private\embedded-scan-result.json",
                    "Cannot inspect /var/tmp/embedded-scan-result.json",
                    r"Cannot inspect \secret.png",
                    r"Cannot inspect \private\secret",
                    r"Cannot inspect \x\y",
                    r"Cannot inspect \资料\私密",
                    r"\AI阅卷\user_data",
                    r"Cannot inspect \sin\cos.txt",
                    r"Cannot inspect \alpha\beta.jpg",
                    r"Cannot inspect \text\mathbb.json",
                    r"Cannot inspect \sin\cos\secret.txt",
                    r"\private/foo/bar",
                    r"Cannot inspect \private/foo.txt",
                    r"\sin/cos/secret.txt",
                    "Cannot inspect /etc",
                    "Cannot inspect /private",
                    "Cannot inspect /data",
                    "/api/jobs/8/C:/private/secret.txt",
                    "/api/jobs/8?path=C:/private/secret.png",
                    "/api/jobs/8?source=file:///C:/private/secret.png",
                    "/api/jobs/8#C:/private/secret.png",
                    "/api/jobs/8?path=C%3A%2Fprivate%2Fsecret.png",
                    "/api/jobs/8?path=%2Fvar%2Ftmp%2Fsecret.png",
                    "/api/jobs/8?source=file%3A%2F%2F%2FC%3A%2Fprivate%2Fsecret.png",
                    "/api/jobs/8?path=C%253A%252Fprivate%252Fsecret.png",
                    "/api/jobs/8?path=%252Fvar%252Ftmp%252Fsecret.png",
                    "/api/jobs/8",
                    "See /api/jobs/8 for the exported report",
                    "See /api/jobs/8?download=true",
                    "See /api/jobs/8#download",
                    "A/B test",
                    "profile:///函数画像",
                    "Open https://example.test/reports/8",
                    r"\frac{a}{b}",
                    r"\text{a/b}",
                    r"Equation: \sin(x)/y",
                    r"\alpha/\beta",
                    r"\mathrm{A}/B",
                    r"\sin\cos",
                    r"\mathbf\mathrm",
                    r"\alpha\beta",
                    r"\to\infty",
                    r"\partial\nabla",
                    r"\rightarrow\leftarrow",
                    r"$\sin\cos$",
                    r"Equation: \sin\cos",
                    r"\lim\limits",
                ],
            },
        },
    )

    response = client.get(f"/api/jobs/{legacy.id}")

    assert response.status_code == 200
    assert response.json()["result"] == {
        "session_id": 8,
        "summary": {"count": 1},
        "legacy": {
            "public_url": "/api/jobs/8",
            "items": [
                "safe",
                "/api/jobs/8",
                "See /api/jobs/8 for the exported report",
                "See /api/jobs/8?download=true",
                "See /api/jobs/8#download",
                "A/B test",
                "profile:///函数画像",
                "Open https://example.test/reports/8",
                r"\frac{a}{b}",
                r"\text{a/b}",
                r"Equation: \sin(x)/y",
                r"\alpha/\beta",
                r"\mathrm{A}/B",
                r"\sin\cos",
                r"\mathbf\mathrm",
                r"\alpha\beta",
                r"\to\infty",
                r"\partial\nabla",
                r"\rightarrow\leftarrow",
                r"$\sin\cos$",
                r"Equation: \sin\cos",
                r"\lim\limits",
            ],
        },
    }
    assert "old-secret" not in response.text
    assert "C:\\private" not in response.text
