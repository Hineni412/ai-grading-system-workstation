"""历史遗留 job 行回归：已下线 job 类型（如 ai_assembly_spec）的旧记录仍可列出与查看。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_job_manager
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore


_LEGACY_JOB_TYPE = "ai_assembly_spec"
_LEGACY_RESULT = {
    "spec": {"title": "历史细目表", "rows": [{"question_type": "选择题", "count": 2}]},
    "model_name": "legacy-model",
}
_LEGACY_PAYLOAD = {
    "request": {
        "type_counts": {"选择题": 2},
        "free_text": "不要暴露的口语描述",
        "locked_question_ids": [7],
        "bank_profile": {"total_questions": 99},
    }
}


def _seed_legacy_job(store: JobStore, *, status: str, with_result: bool) -> int:
    job = store.create_job(_LEGACY_JOB_TYPE, dict(_LEGACY_PAYLOAD))
    if status != "queued" or with_result:
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, progress = 1.0
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(_LEGACY_RESULT, ensure_ascii=False),
                    job.id,
                ),
            )
    return job.id


def _client_with_manager(manager: JobManager) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app)


def test_legacy_unsupported_job_row_lists_and_details_without_crash(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = _seed_legacy_job(store, status="succeeded", with_result=True)
    manager = JobManager(store, max_workers=1)
    try:
        client = _client_with_manager(manager)

        listing = client.get("/api/jobs")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert any(item["id"] == job_id for item in items)
        row = next(item for item in items if item["id"] == job_id)
        assert row["job_type"] == _LEGACY_JOB_TYPE
        assert row["status"] == "succeeded"

        detail = client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["job_type"] == _LEGACY_JOB_TYPE
        # 脱敏红线对历史行仍生效：result 只剩细目表与模型名，payload 不原文外泄。
        assert set(body["result"]) == {"spec", "model_name"}
        assert "不要暴露的口语描述" not in repr(body)
        assert "bank_profile" not in repr(body)
    finally:
        manager.shutdown()


def test_legacy_unsupported_queued_job_row_marks_failed_on_startup(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = _seed_legacy_job(store, status="queued", with_result=False)

    manager = JobManager(store, max_workers=1, cleanup_interrupted=True)
    try:
        record = manager.get(job_id)
        assert record is not None
        assert record.status == "failed"
        assert record.error == "interrupted by process restart"
    finally:
        manager.shutdown()
