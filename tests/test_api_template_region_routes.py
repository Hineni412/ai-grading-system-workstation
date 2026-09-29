from __future__ import annotations

import hashlib
import itertools
import json
import warnings
from pathlib import Path


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories


def _client_with_db(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_templates_dir,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    return TestClient(app), db


_session_counter = itertools.count(1)


def _session(db) -> int:
    root = Path(db.db_path).parent
    rubric = root / "rubric.json"
    answer = root / "answer.json"
    rubric.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "subjective",
                        "max_score": 10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    answer.write_text(
        json.dumps({"questions": [{"question_id": "Q1"}]}), encoding="utf-8"
    )
    # Session names are unique per database (migration 012), so each helper
    # call needs a distinct name.
    return db.sessions.create_grading_session(
        f"Template Exam {next(_session_counter)}", str(rubric), str(answer)
    )


def _template_files(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    front.write_bytes(b"front-template")
    back.write_bytes(b"back-template")
    return front, back


def _two_page_template_pdf() -> bytes:
    import fitz

    document = fitz.open()
    try:
        first = document.new_page(width=300, height=500)
        first.insert_text((36, 48), "ANONYMOUS BACK")
        second = document.new_page(width=400, height=600)
        second.insert_text((36, 48), "ANONYMOUS FRONT")
        return document.tobytes()
    finally:
        document.close()


def _upload_headers(
    pdf_bytes: bytes, request_token: str, filename: str
) -> dict[str, str]:
    return {
        "content-type": "application/pdf",
        "x-upload-filename": filename,
        "x-client-request-token": request_token,
        "x-content-sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }


def _region(region_uuid: str = "r1", *, mapped_question_id: str | None = "Q1") -> dict:
    return {
        "region_uuid": region_uuid,
        "page": "front",
        "region_order": 1,
        "x": 10,
        "y": 20,
        "w": 100,
        "h": 80,
        "mapped_question_id": mapped_question_id,
        "mapping_status": "manual" if mapped_question_id else "unbound",
        "is_confirmed": True,
        "multi_region_confirmed": False,
    }


def _bind_template(
    client: TestClient, session_id: int, front: Path, back: Path
) -> dict:
    response = client.put(
        f"/api/sessions/{session_id}/template",
        json={
            "front_template_path": str(front),
            "back_template_path": str(back),
            "ai_analysis_path": "analysis.json",
            "template_config_path": "mapping.json",
            "regions_path": "regions.json",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_current_template_page_assignment_swaps_regions_and_is_idempotent(
    tmp_path,
) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    pdf_bytes = _two_page_template_pdf()
    uploaded = client.post(
        f"/api/sessions/{session_id}/template",
        params={"first_page_role": "front"},
        content=pdf_bytes,
        headers=_upload_headers(pdf_bytes, "0" * 32, "anonymous-sample.pdf"),
    ).json()
    draft_region = _region()
    saved_draft = client.put(
        f"/api/sessions/{session_id}/regions/draft",
        json={
            "expected_template_fingerprint": uploaded["template_fingerprint"],
            "expected_revision": 0,
            "revision": 1,
            "regions": [draft_region],
        },
    )
    assert saved_draft.status_code == 200
    db.templates.save_answer_regions(session_id, uploaded["template_id"], [draft_region])
    db.templates.mark_template_confirmed(session_id, True)
    before_template = db.templates.get_session_template(session_id)
    assert before_template is not None
    before_region = db.templates.list_answer_regions(session_id)[0]
    assert db.templates.is_template_ready(session_id) is True

    changed = client.put(
        f"/api/sessions/{session_id}/template/page-assignment",
        json={
            "first_page_role": "back",
            "expected_template_fingerprint": uploaded["template_fingerprint"],
        },
    )

    assert changed.status_code == 200
    body = changed.json()
    assert body["changed"] is True
    assert body["draft_sync_pending"] is False
    assert body["template"]["first_page_role"] == "back"
    assert body["template"]["template_fingerprint"] != uploaded["template_fingerprint"]
    assert (
        body["template"]["pages"]["front"]["width"]
        == uploaded["pages"]["back"]["width"]
    )
    assert (
        body["template"]["pages"]["back"]["width"]
        == uploaded["pages"]["front"]["width"]
    )
    assert str(tmp_path) not in changed.text

    after_template = db.templates.get_session_template(session_id)
    assert after_template is not None
    assert (
        after_template["front_template_path"] == before_template["back_template_path"]
    )
    assert (
        after_template["back_template_path"] == before_template["front_template_path"]
    )
    assert after_template["is_confirmed"] == 0
    assert after_template["regions_snapshot_pending"] == 0
    after_region = db.templates.list_answer_regions(session_id)[0]
    assert after_region["page"] == "back"
    assert after_region["is_confirmed"] == 0
    assert {key: after_region[key] for key in ("x", "y", "w", "h")} == {
        key: before_region[key] for key in ("x", "y", "w", "h")
    }
    assert db.templates.is_template_ready(session_id) is False
    draft = client.get(f"/api/sessions/{session_id}/regions/draft").json()
    assert draft["status"] == "compatible"
    assert draft["template_fingerprint"] == body["template"]["template_fingerprint"]
    assert draft["draft"]["revision"] == 2
    assert draft["draft"]["regions"][0]["page"] == "back"

    retried = client.put(
        f"/api/sessions/{session_id}/template/page-assignment",
        json={
            "first_page_role": "back",
            "expected_template_fingerprint": uploaded["template_fingerprint"],
        },
    )

    assert retried.status_code == 200
    assert retried.json()["changed"] is False
    assert retried.json()["draft_sync_pending"] is False
    assert db.templates.get_session_template(session_id) == after_template
    assert db.templates.list_answer_regions(session_id)[0]["page"] == "back"
    assert (
        client.get(f"/api/sessions/{session_id}/regions/draft").json()["draft"][
            "revision"
        ]
        == 2
    )


def test_draft_save_rejects_a_stale_revision_and_returns_no_internal_paths(
    tmp_path,
) -> None:
    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    pdf_bytes = _two_page_template_pdf()
    upload = client.post(
        f"/api/sessions/{session_id}/template",
        params={"first_page_role": "front"},
        content=pdf_bytes,
        headers=_upload_headers(pdf_bytes, "5" * 32, "anonymous-sample.pdf"),
    ).json()
    first_request = {
        "expected_template_fingerprint": upload["template_fingerprint"],
        "expected_revision": 0,
        "revision": 1,
        "regions": [_region()],
    }

    first = client.put(f"/api/sessions/{session_id}/regions/draft", json=first_request)
    stale = client.put(
        f"/api/sessions/{session_id}/regions/draft",
        json={**first_request, "revision": 2},
    )

    assert first.status_code == 200
    assert first.json() == {
        "status": "compatible",
        "session_id": session_id,
        "template_id": upload["template_id"],
        "template_fingerprint": upload["template_fingerprint"],
        "draft": {"revision": 1, "regions": [_region()]},
    }
    assert str(tmp_path) not in first.text
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "region_draft_revision_conflict"


def test_pending_snapshot_can_be_retried_without_recommitting_regions(
    tmp_path, monkeypatch
) -> None:
    import answer_region_commit_service as commit_module

    client, db = _client_with_db(tmp_path)
    session_id = _session(db)
    front, back = _template_files(tmp_path / "templates")
    _bind_template(client, session_id, front, back)
    draft = client.put(
        f"/api/sessions/{session_id}/regions/draft",
        json={"revision": 1, "regions": [_region()]},
    ).json()
    original_atomic_write = commit_module._atomic_write_json
    failed_once = False

    def fail_first_snapshot(path, data) -> None:
        nonlocal failed_once
        if not failed_once and path.name.startswith("regions_confirmed_"):
            failed_once = True
            raise OSError("synthetic snapshot failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(commit_module, "_atomic_write_json", fail_first_snapshot)
    committed = client.post(
        f"/api/sessions/{session_id}/regions/commit",
        json={
            "regions": [_region()],
            "image_sizes": {"front": [1000, 1000], "back": [1000, 1000]},
            "expected_template_fingerprint": draft["template_fingerprint"],
        },
    )
    monkeypatch.setattr(commit_module, "_atomic_write_json", original_atomic_write)
    before_retry = db.templates.list_answer_regions(session_id)

    retried = client.post(
        f"/api/sessions/{session_id}/regions/snapshot/retry",
        json={"expected_template_fingerprint": draft["template_fingerprint"]},
    )

    assert committed.json()["committed"] is True
    assert committed.json()["snapshot_pending"] is True
    assert retried.status_code == 200
    assert retried.json()["committed"] is True
    assert retried.json()["snapshot_pending"] is False
    assert db.templates.list_answer_regions(session_id) == before_retry
    assert db.templates.get_session_template(session_id)["regions_snapshot_pending"] == 0
    assert str(tmp_path) not in retried.text
