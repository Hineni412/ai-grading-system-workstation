from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from question_bank.training_submissions import SubmissionRevisionConflict


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


class FakeSubmissionModule:
    def __init__(self, preview: Path) -> None:
        self.preview = preview
        self.created = None
        self.ingested = None
        self.resolved = None
        self.cancelled = None
        self.error: Exception | None = None

    def create_batch(self, command):
        self.created = command
        return _batch()

    def get_batch(self, batch_id):
        if self.error is not None:
            raise self.error
        return _batch()

    def list_batches(self, paper_batch_id):
        self.listed_paper_batch = paper_batch_id
        return [{
            **{key: _batch()[key] for key in (
                "batch_id", "paper_batch_id", "status", "created_at", "updated_at",
            )},
            "submission_count": 1,
        }]

    def ingest(self, batch_id, command, source):
        self.ingested = (batch_id, command, source.read())
        return _batch(revision=2)

    def resolve_page(self, batch_id, command):
        self.resolved = (batch_id, command)
        return _batch(revision=3)

    def cancel_submission(self, submission_id, command):
        self.cancelled = (submission_id, command)
        return _batch(revision=4, status="cancelled")

    def page_artifact_path(self, scan_page_id, *, batch_id=None):
        return self.preview


@pytest.fixture()
def submission_client(
    tmp_path: Path,
) -> tuple[TestClient, FakeSubmissionModule]:
    from backend.api.app import create_app
    from backend.api.dependencies import get_training_submission_module

    preview = tmp_path / "page.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    module = FakeSubmissionModule(preview)
    app = create_app()
    app.dependency_overrides[get_training_submission_module] = lambda: module
    return TestClient(app), module


def test_scan_batch_upload_resolution_cancel_and_preview_contract(
    submission_client,
) -> None:
    client, module = submission_client
    batch_id = "b" * 64
    page_id = "d" * 64
    submission_id = "c" * 64
    created = client.post(
        "/api/training/scan-batches",
        json={
            "operation_token": "1" * 32,
            "paper_instance_ids": ["a" * 64],
        },
    )
    assert created.status_code == 201
    assert module.created.paper_instance_ids == ("a" * 64,)
    assert "path" not in created.text.casefold()

    content = b"\x89PNG\r\n\x1a\nsynthetic"
    uploaded = client.post(
        f"/api/training/scan-batches/{batch_id}/uploads?expected_revision=1",
        content=content,
        headers={
            "content-type": "image/png",
            "x-operation-token": "2" * 32,
            "x-content-sha256": hashlib.sha256(content).hexdigest(),
            "x-upload-filename": "scan.png",
        },
    )
    assert uploaded.status_code == 200
    assert module.ingested[1].expected_revision == 1
    assert module.ingested[2] == content

    resolved = client.post(
        f"/api/training/scan-batches/{batch_id}/pages/{page_id}/resolve",
        json={
            "operation_token": "3" * 32,
            "expected_revision": 2,
            "action": "replace",
            "paper_instance_id": "a" * 64,
            "page_number": 1,
        },
    )
    assert resolved.status_code == 200
    assert module.resolved[1].action == "replace"

    cancelled = client.post(
        f"/api/training/submissions/{submission_id}/cancel",
        json={
            "operation_token": "4" * 32,
            "expected_revision": 3,
            "reason": "合成取消",
        },
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    preview = client.get(
        f"/api/training/scan-batches/{batch_id}/pages/{page_id}/preview"
    )
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"


def test_scan_batch_history_can_be_found_without_a_scan_batch_id(submission_client) -> None:
    client, module = submission_client
    response = client.get("/api/training/scan-batches", params={"paper_batch_id": "f" * 64})
    assert response.status_code == 200
    assert module.listed_paper_batch == "f" * 64
    item = response.json()["items"][0]
    assert item["batch_id"] == "b" * 64
    assert item["submission_count"] == 1
    assert "pages" not in item
    assert client.get("/api/training/scan-batches").status_code == 422


def test_scan_batch_revision_conflict_is_public_and_safe(
    submission_client,
) -> None:
    client, module = submission_client
    module.error = SubmissionRevisionConflict(1, 2)
    response = client.get(f"/api/training/scan-batches/{'b' * 64}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == (
        "training_submission_revision_conflict"
    )
    assert response.json()["error"]["details"] == {"current_revision": 2}
    assert "path" not in response.text.casefold()


def _batch(
    *,
    revision: int = 1,
    status: str = "manual_review",
) -> dict[str, object]:
    return {
        "batch_id": "b" * 64,
        "paper_batch_id": "f" * 64,
        "status": status,
        "revision": revision,
        "duplicate_upload": False,
        "submissions": [
            {
                "submission_id": "c" * 64,
                "paper_instance_id": "a" * 64,
                "student_id": "SYN-S01",
                "student_name": "合成学生",
                "series_version": 1,
                "status": "manual_review",
                "revision": 1,
                "expected_total_pages": 2,
                "missing_pages": [2],
                "issue_codes": [],
                "assessment_started": False,
            }
        ],
        "pages": [
            {
                "scan_page_id": "d" * 64,
                "upload_id": "e" * 64,
                "upload_page_number": 1,
                "submission_id": "c" * 64,
                "paper_instance_id": "a" * 64,
                "page_number": 1,
                "total_pages": 2,
                "image_sha256": "1" * 64,
                "image_fingerprint": "2" * 16,
                "width_pixels": 850,
                "height_pixels": 1200,
                "blur_score": 100.0,
                "brightness_score": 240.0,
                "contrast_score": 20.0,
                "rotation_degrees": 0,
                "issue_code": None,
                "state": "assigned",
                "assignment_revision": 1,
                "preview_url": (
                    f"/api/training/scan-batches/{'b' * 64}/pages/"
                    f"{'d' * 64}/preview"
                ),
                "created_at": "2026-07-30T08:00:00+00:00",
            }
        ],
        "candidates": [
            {
                "paper_instance_id": "a" * 64,
                "student_id": "SYN-S01",
                "student_name": "合成学生",
                "series_version": 1,
                "total_pages": 2,
            }
        ],
        "history": [],
        "created_at": "2026-07-30T08:00:00+00:00",
        "updated_at": "2026-07-30T08:00:00+00:00",
    }
