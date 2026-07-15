from __future__ import annotations

import io
import json
import warnings
from pathlib import Path
from urllib.parse import quote

import fitz

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_config_source_service,
    get_grading_db,
    get_upload_config_dir,
)
from backend.config_workspace.sources import ConfigSourceService
from db_manager import DBManager


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Synthetic private exam text\n1. Compute 1 + 1.\nA. 1  B. 2\nAnswer\n1. B",
        fontsize=12,
    )
    payload = document.tobytes()
    document.close()
    return payload


def _client(tmp_path: Path) -> tuple[TestClient, DBManager, Path]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    upload_root = tmp_path / "uploaded"
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_upload_config_dir] = lambda: upload_root
    return TestClient(app), db, upload_root


def _session(db: DBManager, tmp_path: Path) -> int:
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    return db.create_grading_session("Config Source", str(rubric), str(answer))


def _upload(client: TestClient, session_id: int, *, filename: str = "数学卷.pdf"):
    return client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=_pdf_bytes(),
        headers={
            "content-type": "application/octet-stream",
            "x-upload-filename": quote(filename),
        },
    )


def test_upload_and_restart_get_return_only_bounded_public_projection(tmp_path: Path) -> None:
    client, db, _upload_root = _client(tmp_path)
    session_id = _session(db, tmp_path)

    uploaded = _upload(client, session_id, filename="../数学卷.pdf")

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["safe_filename"] == "数学卷.pdf"
    assert body["parse_state"] == "ready"
    assert len(body["sha256_prefix"]) == 12
    assert len(body["source_id"]) == 32
    assert len(body["source_revision"]) == 64
    encoded = json.dumps(body, ensure_ascii=False)
    assert "Synthetic private exam text" not in encoded
    assert str(tmp_path) not in encoded
    assert "base64" not in encoded.casefold()

    reloaded = client.get(
        f"/api/sessions/{session_id}/config/sources/{body['source_id']}"
    )
    assert reloaded.status_code == 200
    assert reloaded.json() == body


def test_upload_rejects_missing_session_without_creating_source(tmp_path: Path) -> None:
    client, _db, upload_root = _client(tmp_path)

    response = _upload(client, 404)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert list(upload_root.rglob("manifest.json")) == []


def test_upload_maps_size_type_and_parser_errors_without_echoing_input(tmp_path: Path) -> None:
    client, db, upload_root = _client(tmp_path)
    session_id = _session(db, tmp_path)
    client.app.dependency_overrides[get_config_source_service] = lambda: ConfigSourceService(
        upload_root,
        max_upload_bytes=4,
    )
    too_large = client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=b"12345",
        headers={"x-upload-filename": quote("C:/private/secret.pdf")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "config_source_too_large"
    assert "secret" not in too_large.text

    client.app.dependency_overrides[get_config_source_service] = (
        lambda: ConfigSourceService(upload_root)
    )
    unsupported = client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=b"private",
        headers={"x-upload-filename": quote("C:/private/secret.txt")},
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "config_source_type_unsupported"
    assert "secret" not in unsupported.text

    invalid = client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=b"%PDF-private-parser-detail",
        headers={"x-upload-filename": quote("C:/private/secret.pdf")},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "config_source_invalid"
    assert "secret" not in invalid.text
    assert "parser" not in invalid.text


def test_controlled_question_and_answer_assets_are_private_no_store(tmp_path: Path) -> None:
    client, db, _upload_root = _client(tmp_path)
    session_id = _session(db, tmp_path)
    uploaded = _upload(client, session_id).json()
    question = uploaded["questions"][0]

    for kind in ("question", "answer"):
        response = client.get(
            f"/api/sessions/{session_id}/config/sources/{uploaded['source_id']}"
            f"/questions/{question['question_id']}/assets/{kind}"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-type"].startswith("image/")
        assert response.content

    missing = client.get(
        f"/api/sessions/{session_id}/config/sources/{uploaded['source_id']}"
        f"/questions/{question['question_id']}/assets/private-path"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "config_asset_not_found"


def test_replaced_source_get_fails_closed_but_new_source_remains_available(tmp_path: Path) -> None:
    client, db, _upload_root = _client(tmp_path)
    session_id = _session(db, tmp_path)
    first = _upload(client, session_id, filename="first.pdf").json()
    second = _upload(client, session_id, filename="second.pdf").json()

    stale = client.get(
        f"/api/sessions/{session_id}/config/sources/{first['source_id']}"
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "config_source_changed"
    current = client.get(
        f"/api/sessions/{session_id}/config/sources/{second['source_id']}"
    )
    assert current.status_code == 200


def test_source_ids_cannot_select_another_session_or_path(tmp_path: Path) -> None:
    client, db, _upload_root = _client(tmp_path)
    first_session = _session(db, tmp_path)
    second_session = db.create_grading_session(
        "Other",
        str(tmp_path / "rubric.json"),
        str(tmp_path / "answer.json"),
    )
    source = _upload(client, first_session).json()

    mismatch = client.get(
        f"/api/sessions/{second_session}/config/sources/{source['source_id']}"
    )
    assert mismatch.status_code == 404
    assert mismatch.json()["error"]["code"] == "config_source_not_found"
    invalid_id = client.get(
        f"/api/sessions/{first_session}/config/sources/{'A' * 32}"
    )
    assert invalid_id.status_code == 404
    assert invalid_id.json()["error"]["code"] == "config_source_not_found"
