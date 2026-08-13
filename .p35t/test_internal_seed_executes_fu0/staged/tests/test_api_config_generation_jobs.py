from __future__ import annotations

import asyncio
import io
import warnings
import sqlite3
import threading
import pytest
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient
from docx import Document

from backend.api.app import create_app
from backend.api.dependencies import (
    get_config_source_service,
    get_grading_db,
    get_job_manager,
    get_upload_config_dir,
)
from backend.config_workspace.sources import ConfigSourceRecord, ConfigSourceService
from backend.jobs.config_generation import load_config_generation_input
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from db_manager import DBManager


def _client(tmp_path: Path) -> tuple[TestClient, DBManager, JobManager]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register(
        "config_generation",
        lambda context: {
            "session_id": int(context.payload["session_id"]),
            "outcome": "partial",
            "total_questions": 1,
            "generated_questions": 0,
            "failed_count": 1,
            "failed_question_ids": ["Q1"],
            "retryable": True,
        },
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "uploaded"
    app.dependency_overrides[get_config_source_service] = lambda: ConfigSourceService(
        tmp_path / "uploaded"
    )
    return TestClient(app), db, manager


async def _chunks(content: bytes):
    yield content


def _docx_bytes(question: str = "1. Prove that one equals one.\nAnswer: proven") -> bytes:
    document = Document()
    document.add_paragraph(question)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _source(tmp_path: Path, session_id: int, *, question: str = "1. Prove x = x.") -> ConfigSourceRecord:
    return asyncio.run(
        ConfigSourceService(tmp_path / "uploaded").stage_and_parse(
            session_id=session_id,
            filename="controlled.docx",
            chunks=_chunks(_docx_bytes(question)),
        )
    )


def _source_request(source: ConfigSourceRecord, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": source.source_id,
        "source_revision": source.source_revision,
        "generation_mode": "batched",
        "decisions": [
            {
                "question_id": source.questions[0].question_id,
                "question_type": "proof",
                "excluded": False,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _session(db: DBManager, tmp_path: Path) -> int:
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    return db.create_grading_session("Config Job", str(rubric), str(answer))


def _request_payload() -> dict[str, object]:
    return {
        "confirmed_blocks": [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "text": "1 + 1 = ?",
                "canonical_answer": "2",
            }
        ],
        "document_text": "private exam text that must not be returned",
        "question_images": {},
    }


def test_config_generation_route_submits_safe_queryable_job(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=_request_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "config_generation"
    assert body["payload"] == {"session_id": session_id, "mode": "generate"}
    assert "private exam text" not in response.text
    manager.wait(body["id"], timeout=5)
    queried = client.get(f"/api/jobs/{body['id']}")
    assert queried.status_code == 200
    assert queried.json()["result"]["failed_question_ids"] == ["Q1"]


def test_active_config_job_rejects_legacy_config_replacement(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    old_paths = db.get_grading_session(session_id)
    job = manager.store.create_claimed_config_job(
        {"session_id": session_id, "mode": "generate"}
    )

    response = client.put(
        f"/api/sessions/{session_id}/config",
        json={"rubric": {}, "answer_key": {}, "meta": {"warnings": []}},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_in_progress"
    current = db.get_grading_session(session_id)
    assert current["rubric_path"] == old_paths["rubric_path"]
    assert current["answer_key_path"] == old_paths["answer_key_path"]
    manager.store.finish(job.id, "failed", error="test cleanup")


def test_generate_from_source_stages_private_input_and_public_job_is_safe(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)

    response = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source),
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "session_id": session_id,
        "mode": "generate",
        "generation_mode": "batched",
        "source_id": source.source_id,
        "source_revision": source.source_revision,
    }
    assert source.private_document_text not in response.text
    assert "input_id" not in response.text
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload["input_id"]
    private_input = load_config_generation_input(
        tmp_path / "uploaded",
        str(stored.payload["input_id"]),
    )
    assert "document_text" not in private_input
    assert "confirmed_blocks" not in private_input
    assert "question_images" not in private_input
    assert "whole_page_images" not in private_input
    assert private_input["source_id"] == source.source_id
    assert private_input["source_revision"] == source.source_revision
    assert private_input["generation_mode"] == "batched"
    assert private_input["decisions"] == [
        {"excluded": False, "question_id": "Q1", "question_type": "proof"}
    ]


def test_latest_generation_job_endpoint_authoritatively_finds_matching_job(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    created = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source),
    )
    assert created.status_code == 202

    found = client.get(
        f"/api/sessions/{session_id}/config/generation-jobs/latest",
        params={
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "generation_mode": "batched",
        },
    )

    assert found.status_code == 200
    assert found.json()["id"] == created.json()["id"]
    assert found.json()["payload"] == created.json()["payload"]
    manager.wait(created.json()["id"], timeout=5)


def test_latest_generation_job_endpoint_does_not_return_a_different_request(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)

    missing = client.get(
        f"/api/sessions/{session_id}/config/generation-jobs/latest",
        params={
            "source_id": "a" * 32,
            "source_revision": "b" * 64,
            "generation_mode": "whole_document",
        },
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "config_generation_job_not_found"


def test_generation_request_token_reuses_only_the_exact_job(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    old_token = "1" * 32
    token = "2" * 32
    old = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=old_token),
    )
    first = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=token),
    )
    replay = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=token),
    )
    queried = client.get(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}"
    )

    assert old.status_code == 202
    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json()["id"] != old.json()["id"]
    assert replay.json()["id"] == first.json()["id"]
    assert queried.status_code == 200
    assert queried.json()["id"] == first.json()["id"]
    assert manager.store.find_config_job_by_request_token(
        session_id=session_id, request_token=token
    ).id == first.json()["id"]


def test_abandon_unseen_generation_token_blocks_a_late_request(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    token = "9" * 32

    abandoned = client.post(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}/abandon"
    )
    replay = client.post(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}/abandon"
    )
    late_request = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=token),
    )
    lookup = client.get(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}"
    )

    assert abandoned.status_code == 200
    assert abandoned.json() == {"status": "abandoned"}
    assert replay.status_code == 200
    assert late_request.status_code == 409
    assert late_request.json()["error"]["code"] == "config_request_token_conflict"
    assert lookup.status_code == 404
    assert manager.store.find_config_job_by_request_token(
        session_id=session_id, request_token=token
    ) is not None


def test_abandon_generation_token_rejects_an_already_received_request(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    token = "8" * 32

    created = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=token),
    )
    abandoned = client.post(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}/abandon"
    )
    lookup = client.get(
        f"/api/sessions/{session_id}/config/generation-jobs/requests/{token}"
    )

    assert created.status_code == 202
    assert abandoned.status_code == 409
    assert lookup.status_code == 200
    assert lookup.json()["id"] == created.json()["id"]
    manager.wait(created.json()["id"], timeout=5)


def test_generation_request_token_rejects_a_different_request(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    token = "3" * 32
    first = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token=token),
    )
    conflict = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(
            source,
            generation_mode="whole_document",
            client_request_token=token,
        ),
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "config_request_token_conflict"
    manager.wait(first.json()["id"], timeout=5)


def test_session_allows_exact_replay_but_rejects_another_active_config_job(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    started = threading.Event()
    release = threading.Event()

    def blocked(context):
        started.set()
        assert release.wait(timeout=5)
        return {
            "session_id": int(context.payload["session_id"]),
            "outcome": "partial",
            "retryable": True,
        }

    manager.register("config_generation", blocked)
    first_request = _source_request(source, client_request_token="4" * 32)
    first = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=first_request,
    )
    assert first.status_code == 202
    assert started.wait(timeout=5)
    replay = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=first_request,
    )
    conflict = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(source, client_request_token="5" * 32),
    )
    release.set()
    manager.wait(first.json()["id"], timeout=5)

    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "config_generation_in_progress"


@pytest.mark.parametrize(
    ("mode", "stage", "private_detail", "expected_public_detail"),
    [
        (
            "generate",
            "Split paper",
            "Parsed C:/private/exams/paper.docx; qids=Q1; raw upstream body",
            "Preparing source questions.",
        ),
        (
            "generate",
            "AI 赋分失败，使用本地均分兜底",
            "RuntimeError from C:/private/models/provider.log: raw upstream body",
            "Finalizing grading configuration.",
        ),
        (
            "retry",
            "unrecognized-stage",
            "document_text=private exam; raw upstream body",
            "",
        ),
    ],
)
def test_source_config_job_public_detail_is_fixed_and_path_free(
    tmp_path: Path,
    mode: str,
    stage: str,
    private_detail: str,
    expected_public_detail: str,
) -> None:
    client, _db, manager = _client(tmp_path)
    job = manager.store.create_job(
        "config_generation",
        {
            "session_id": 7,
            "mode": mode,
            "generation_mode": "batched",
            "source_id": "b" * 32,
            "source_revision": "c" * 64,
            "input_id": "a" * 32,
        },
    )
    assert manager.store.mark_running(job.id)
    manager.store.update_progress(
        job.id,
        progress=0.9,
        stage=stage,
        detail=private_detail,
    )
    manager.store.finish(
        job.id,
        "failed",
        error="final source/archive/publish failure at C:/private/output.json",
    )

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["detail"] == expected_public_detail
    assert private_detail not in response.text
    assert "C:/private" not in response.text
    stored = manager.store.get_job(job.id)
    assert stored is not None
    assert stored.detail == private_detail


def test_legacy_config_job_keeps_existing_public_detail_contract(
    tmp_path: Path,
) -> None:
    client, _db, manager = _client(tmp_path)
    job = manager.store.create_job(
        "config_generation",
        {"session_id": 7, "mode": "generate", "input_id": "a" * 32},
    )
    assert manager.store.mark_running(job.id)
    manager.store.update_progress(
        job.id,
        progress=0.5,
        stage="legacy generation",
        detail="legacy detail remains visible",
    )

    response = client.get(f"/api/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["detail"] == "legacy detail remains visible"


@pytest.mark.parametrize(
    ("mutator", "expected_status"),
    [
        (lambda payload, source: payload.update({"extra": True}), 422),
        (
            lambda payload, source: payload["decisions"][0].update({"extra": True}),
            422,
        ),
        (
            lambda payload, source: payload.update(
                {
                    "decisions": [
                        payload["decisions"][0],
                        dict(payload["decisions"][0]),
                    ]
                }
            ),
            422,
        ),
        (
            lambda payload, source: payload.update(
                {
                    "decisions": [
                        {
                            "question_id": "UNKNOWN",
                            "question_type": "proof",
                            "excluded": False,
                        }
                    ]
                }
            ),
            422,
        ),
        (
            lambda payload, source: payload["decisions"][0].update(
                {"question_type": "essay"}
            ),
            422,
        ),
        (
            lambda payload, source: payload["decisions"][0].update(
                {"excluded": True}
            ),
            422,
        ),
    ],
)
def test_generate_from_source_rejects_extra_unknown_duplicate_or_empty_selection(
    tmp_path: Path,
    mutator,
    expected_status: int,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    payload = _source_request(source)
    mutator(payload, source)

    response = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=payload,
    )

    assert response.status_code == expected_status
    assert source.private_document_text not in response.text


def test_generate_from_source_rejects_stale_revision_and_replaced_active_source(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    first = _source(tmp_path, session_id, question="1. Prove x = x.")

    stale = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(first, source_revision="0" * 64),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_source_changed"

    _source(tmp_path, session_id, question="1. Prove y = y.")
    replaced = client.post(
        f"/api/sessions/{session_id}/config/generate-from-source",
        json=_source_request(first),
    )
    assert replaced.status_code == 409
    assert replaced.json()["error"]["code"] == "config_source_changed"


def test_upload_cleanup_uses_private_job_payload_references_and_is_exact(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    referenced = _source(tmp_path, session_id, question="1. Prove a = a.")
    unreferenced = _source(tmp_path, session_id, question="1. Prove b = b.")
    active = _source(tmp_path, session_id, question="1. Prove c = c.")
    sentinel = unreferenced.manifest_path.parent / "not-owned.txt"
    sentinel.write_text("keep", encoding="utf-8")
    route_job_store = JobStore(db.db_path)
    route_job_store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "source_id": referenced.source_id,
            "source_revision": referenced.source_revision,
            "input_id": "a" * 32,
        },
    )
    assert manager.store.referenced_config_source_ids(session_id) == set()
    assert route_job_store.referenced_config_source_ids(session_id) == {
        referenced.source_id
    }

    uploaded = client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=_docx_bytes("1. Prove d = d."),
        headers={"x-upload-filename": "new.docx"},
    )

    assert uploaded.status_code == 201
    assert referenced.manifest_path.is_file()
    assert not unreferenced.manifest_path.is_file()
    assert active.manifest_path.is_file()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_whole_document_source_job_is_not_available_for_selected_retry(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = _source(tmp_path, session_id)
    source_job = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "whole_document",
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source_job.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1"],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source_job.id, "retry_question_ids": ["Q1"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_retry_not_available"


def test_config_generation_route_rejects_missing_session(tmp_path: Path) -> None:
    client, _db, _manager = _client(tmp_path)

    response = client.post("/api/sessions/404/config/generate", json=_request_payload())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_config_generation_route_rejects_sensitive_or_extra_fields(tmp_path: Path) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["api_key"] = "must-not-persist"

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "must-not-persist" not in response.text


def test_config_generation_route_rejects_nested_client_file_paths(tmp_path: Path) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["confirmed_blocks"][0]["image_paths"] = ["C:/private/answer.png"]

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "C:/private/answer.png" not in response.text


def test_config_generation_route_rejects_embedded_image_file_references(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["confirmed_blocks"][0]["question_html"] = '<p>题目</p><img src="secret.png">'

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "secret.png" not in response.text


def test_config_generation_route_rejects_path_value_under_neutral_key(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["confirmed_blocks"][0]["source"] = "C:/private/paper.docx"

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "C:/private/paper.docx" not in response.text


@pytest.mark.parametrize("field_name", ["question", "answer"])
def test_config_generation_route_rejects_path_values_as_question_images(
    tmp_path: Path,
    field_name: str,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["question_images"] = {
        "Q1": {"question": "aGVsbG8=", field_name: "C:/private/paper.png"}
    }

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 422
    assert "C:/private/paper.png" not in response.text


def test_config_generation_route_accepts_bounded_base64_question_images(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    payload = _request_payload()
    payload["question_images"] = {
        "Q1": {"question": "aGVsbG8=", "answer": "d29ybGQ="}
    }

    response = client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=payload,
    )

    assert response.status_code == 202


def test_generic_job_route_cannot_persist_config_generation_content(
    tmp_path: Path,
) -> None:
    client, _db, manager = _client(tmp_path)

    response = client.post(
        "/api/jobs/config_generation",
        json={
            "payload": {
                "session_id": 1,
                "document_text": "must not reach jobs payload",
                "confirmed_blocks": [{"source": "C:/private/paper.docx"}],
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dedicated_job_endpoint_required"
    with sqlite3.connect(manager.store.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_type = 'config_generation'"
        ).fetchone()[0]
    assert count == 0


def test_config_generation_retry_route_accepts_partial_source_job(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "per_question",
            "source_id": "b" * 32,
            "source_revision": "c" * 64,
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1", "Q2"],
            "failed_batches": [
                {"batch_id": "B001", "question_ids": ["Q1", "Q2"]}
            ],
            "retryable": True,
        },
    )

    request = {
        "source_job_id": source.id,
        "retry_question_ids": ["Q1", "Q2"],
        "client_request_token": "d" * 32,
    }
    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json=request,
    )
    replay = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json=request,
    )

    assert response.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["id"] == response.json()["id"]
    assert response.json()["payload"] == {
        "session_id": session_id,
        "mode": "retry",
        "generation_mode": "batched",
        "source_id": "b" * 32,
        "source_revision": "c" * 64,
    }
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload["input_id"] == "a" * 32
    assert "input_id" not in response.json()["payload"]


def test_config_generation_retry_accepts_score_allocation_pending_without_batch_ids(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": [],
            "failed_batches": [],
            "score_allocation_pending": True,
            "score_allocation_failed": True,
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id},
    )

    assert response.status_code == 202
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert "retry_question_ids" not in stored.payload


def test_config_generation_retry_rejects_part_of_a_failed_batch(tmp_path: Path) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1", "Q2"],
            "failed_batches": [
                {"batch_id": "B001", "question_ids": ["Q1", "Q2"]}
            ],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id, "retry_question_ids": ["Q2"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "config_generation_retry_not_available"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_config_generation_retry_accepts_checkpointed_terminal_job(
    tmp_path: Path,
    status: str,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        status,
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1"],
            "failed_batches": [
                {"batch_id": "B001", "question_ids": ["Q1"]}
            ],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id, "retry_question_ids": ["Q1"]},
    )

    assert response.status_code == 202


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_config_generation_retry_accepts_complete_checkpoint_for_local_publish(
    tmp_path: Path,
    status: str,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        status,
        result={
            "session_id": session_id,
            "outcome": "complete",
            "failed_question_ids": [],
            "failed_batches": [],
            "retryable": False,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id},
    )

    assert response.status_code == 202
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert "retry_question_ids" not in stored.payload


def test_config_generation_retry_route_returns_404_for_missing_source_job(
    tmp_path: Path,
) -> None:
    client, db, _manager = _client(tmp_path)
    session_id = _session(db, tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": 404},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "config_generation_job_not_found"


def test_config_generation_retry_source_cannot_be_replayed(
    tmp_path: Path,
) -> None:
    client, db, manager = _client(tmp_path)
    session_id = _session(db, tmp_path)
    source = manager.store.create_job(
        "config_generation",
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "source_id": "b" * 32,
            "source_revision": "c" * 64,
            "input_id": "a" * 32,
        },
    )
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "session_id": session_id,
            "outcome": "partial",
            "failed_question_ids": ["Q1"],
            "failed_batches": [
                {"batch_id": "B001", "question_ids": ["Q1"]}
            ],
            "retryable": True,
        },
    )

    first = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id},
    )
    assert first.status_code == 202
    manager.wait(first.json()["id"], timeout=5)

    replay = client.post(
        f"/api/sessions/{session_id}/config/generate/retry",
        json={"source_job_id": source.id},
    )

    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "config_generation_retry_not_available"
