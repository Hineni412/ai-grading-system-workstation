from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from question_bank.personalized_papers import PaperBudgetExceeded


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


class FakePaperModule:
    def __init__(self, artifact: Path) -> None:
        self.artifact = artifact
        self.created = None
        self.frozen = None
        self.create_error: Exception | None = None

    def create_review_instance(self, draft_id, command):
        if self.create_error is not None:
            raise self.create_error
        self.created = (draft_id, command)
        return _instance()

    def list_for_draft(self, draft_id):
        return (_instance(),)

    def get(self, paper_instance_id):
        return _instance()

    def freeze(self, paper_instance_id, command, source):
        self.frozen = (paper_instance_id, command, source.read())
        return _instance(status="frozen")

    def artifact_path(self, paper_instance_id, kind):
        return self.artifact, (
            "application/pdf"
            if kind == "frozen-pdf"
            else "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )


@pytest.fixture()
def paper_client(
    tmp_path: Path,
) -> tuple[TestClient, FakePaperModule]:
    from backend.api.app import create_app
    from backend.api.dependencies import get_personalized_paper_module

    artifact = tmp_path / "paper.docx"
    artifact.write_bytes(b"synthetic-docx")
    module = FakePaperModule(artifact)
    app = create_app()
    app.dependency_overrides[get_personalized_paper_module] = lambda: module
    return TestClient(app), module


def test_paper_create_list_freeze_and_download_use_public_contract(
    paper_client: tuple[TestClient, FakePaperModule],
) -> None:
    client, module = paper_client
    created = client.post(
        f"/api/training/personalized-drafts/{'d' * 64}/paper-instances",
        json={
            "operation_token": "1" * 32,
            "expected_draft_revision": 1,
            "student_id": "SYN-S01",
            "context_window_tokens": 32768,
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "review_pending"
    assert module.created[1].actor_ref == "local_teacher"
    assert "path" not in created.text.casefold()

    listed = client.get(
        f"/api/training/personalized-drafts/{'d' * 64}/paper-instances"
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    payload = b"synthetic-reviewed-docx"
    frozen = client.post(
        f"/api/training/paper-instances/{'e' * 64}/freeze"
        "?expected_revision=1",
        content=payload,
        headers={
            "content-type": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            "x-operation-token": "2" * 32,
            "x-content-sha256": hashlib.sha256(payload).hexdigest(),
            "x-upload-filename": "reviewed.docx",
        },
    )
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert module.frozen[1].content_sha256 == hashlib.sha256(payload).hexdigest()
    assert module.frozen[2] == payload

    downloaded = client.get(
        f"/api/training/paper-instances/{'e' * 64}/files/review-docx"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"synthetic-docx"
    assert downloaded.headers["cache-control"] == "no-store"


def test_paper_budget_and_invalid_upload_fail_safely(
    paper_client: tuple[TestClient, FakePaperModule],
) -> None:
    client, module = paper_client
    module.create_error = PaperBudgetExceeded(
        {
            "status": "blocked",
            "blockers": ["context_window_limit"],
            "estimated_total_tokens": 40000,
            "source_file": str(module.artifact),
            "internal": {"output_path": str(module.artifact), "api_key": "TEST-secret"},
        }
    )
    blocked = client.post(
        f"/api/training/personalized-drafts/{'d' * 64}/paper-instances",
        json={
            "operation_token": "3" * 32,
            "expected_draft_revision": 1,
            "student_id": "SYN-S01",
        },
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == (
        "personalized_paper_budget_exceeded"
    )
    assert "path" not in blocked.text.casefold()
    assert blocked.json()["error"]["details"]["budget"] == {
        "status": "blocked",
        "blockers": ["context_window_limit"],
        "estimated_total_tokens": 40000,
    }
    assert "TEST-secret" not in blocked.text

    invalid = client.post(
        f"/api/training/paper-instances/{'e' * 64}/freeze"
        "?expected_revision=1",
        content=b"not-docx",
        headers={"content-type": "text/plain"},
    )
    assert invalid.status_code == 415
    assert invalid.json()["error"]["code"] == (
        "personalized_paper_docx_invalid"
    )


def _instance(
    *,
    status: str = "review_pending",
) -> dict[str, object]:
    frozen = status == "frozen"
    return {
        "paper_instance_id": "e" * 64,
        "paper_batch_id": "b" * 64,
        "draft_id": "d" * 64,
        "draft_revision": 1,
        "student_id": "SYN-S01",
        "student_code": "S01",
        "student_name": "合成学生",
        "class_id": "SYN-C01",
        "series_version": 1,
        "status": status,
        "revision": 2 if frozen else 1,
        "layout_version": "personalized-paper-school-a4-v1",
        "budget": {
            "version": "whole-paper-context-budget-v1",
            "status": "ready",
            "context_window_tokens": 32768,
            "question_count": 2,
            "criterion_point_count": 2,
            "image_count": 0,
            "page_count": 2,
            "page_count_is_estimate": not frozen,
            "estimated_input_tokens": 5000,
            "estimated_output_tokens": 1200,
            "estimated_total_tokens": 6200,
            "limits": {
                "questions": 12,
                "criterion_points": 120,
                "images": 48,
                "pages": 20,
            },
            "blockers": [],
        },
        "question_count": 2,
        "criterion_point_count": 2,
        "items": [],
        "pages": (
            [
                {
                    "page_number": 1,
                    "total_pages": 1,
                    "identity_short": "1234567890abcdef",
                    "page_content_hash": "f" * 64,
                }
            ]
            if frozen
            else []
        ),
        "review_docx_sha256": "1" * 64,
        "reviewed_docx_sha256": "2" * 64 if frozen else None,
        "frozen_pdf_sha256": "3" * 64 if frozen else None,
        "downloads": {
            "review_docx": (
                f"/api/training/paper-instances/{'e' * 64}/files/review-docx"
            ),
            "reviewed_docx": (
                f"/api/training/paper-instances/{'e' * 64}/files/reviewed-docx"
                if frozen
                else None
            ),
            "frozen_pdf": (
                f"/api/training/paper-instances/{'e' * 64}/files/frozen-pdf"
                if frozen
                else None
            ),
        },
        "error_code": None,
        "created_at": "2026-07-30 08:00:00",
        "frozen_at": "2026-07-30 08:05:00" if frozen else None,
    }
