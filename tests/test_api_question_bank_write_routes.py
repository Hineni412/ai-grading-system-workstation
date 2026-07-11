from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from question_bank.database.schema import initialize_database
import question_bank.services.question_write_service as write_module
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
    QuestionWriteConflict,
    QuestionWriteNotFound,
    QuestionImportUploadNotFound,
    QuestionImportTypeNotSupported,
    QuestionImportStorageForbidden,
    QuestionImportTooLarge,
)


@pytest.fixture
def write_seed(
    tmp_path: Path,
) -> tuple[QuestionBankWriteService, int, str]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (1, 'Paper', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text
            ) VALUES (1, 1, '1', 'Question')
            """
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source, model_name
            ) VALUES (1, 'knowledge_point', '旧标签', 0.7, 'ai', 'model-x')
            """
        )
        conn.commit()
    service = QuestionBankWriteService(
        db_path,
        data_root=tmp_path / "data",
    )
    return service, 1, service.get_revision(1)


def _load_tags(db_path: Path, question_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT tag_type, tag_value, confidence, source, model_name
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id
            """,
            (question_id,),
        ).fetchall()
    finally:
        conn.close()


def test_replace_tags_writes_exact_manual_set(write_seed) -> None:
    service, question_id, revision = write_seed
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, source)
            VALUES (?, 'legacy_unknown', 'must be removed', 'legacy')
            """,
            (question_id,),
        )
        conn.commit()
    revision = service.get_revision(question_id)

    result = service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[
            ConfirmedQuestionTag("knowledge_point", " 一次函数 ", 1.0),
            ConfirmedQuestionTag("method", "待定系数法", None),
            ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0),
        ],
    )

    rows = _load_tags(service.db_path, question_id)
    assert [(row["tag_type"], row["tag_value"]) for row in rows] == [
        ("knowledge_point", "一次函数"),
        ("method", "待定系数法"),
    ]
    assert [row["source"] for row in rows] == ["manual", "manual"]
    assert [row["model_name"] for row in rows] == [None, None]
    assert result.revision != revision
    assert result.deleted is False


def test_replace_tags_is_idempotent_for_identical_retry(write_seed) -> None:
    service, question_id, revision = write_seed
    tags = [ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)]
    first = service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=tags,
    )

    second = service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=tags,
    )

    assert second == first
    assert len(_load_tags(service.db_path, question_id)) == 1


def test_replace_tags_is_idempotent_when_retry_reorders_same_set(write_seed) -> None:
    service, question_id, revision = write_seed
    first = service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[
            ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0),
            ConfirmedQuestionTag("method", "待定系数法", None),
        ],
    )

    second = service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[
            ConfirmedQuestionTag("method", "待定系数法", None),
            ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0),
        ],
    )

    assert second.revision == first.revision


def test_replace_tags_conflicts_instead_of_overwriting_newer_state(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
    )

    with pytest.raises(QuestionWriteConflict) as caught:
        service.replace_tags(
            question_id,
            expected_revision=revision,
            tags=[ConfirmedQuestionTag("knowledge_point", "二次函数", 1.0)],
        )

    assert len(caught.value.current_revision) == 64
    assert _load_tags(service.db_path, question_id)[0]["tag_value"] == "一次函数"


def test_replace_tags_rejects_missing_or_deleted_question(write_seed) -> None:
    service, question_id, revision = write_seed
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("UPDATE questions SET is_deleted = 1 WHERE id = ?", (question_id,))
        conn.commit()

    with pytest.raises(QuestionWriteNotFound):
        service.replace_tags(question_id, expected_revision=revision, tags=[])
    with pytest.raises(QuestionWriteNotFound):
        service.replace_tags(999, expected_revision=revision, tags=[])


def test_replace_tags_rolls_back_and_does_not_write_legacy_skills(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    with sqlite3.connect(service.db_path) as conn:
        skill_count = conn.execute(
            "SELECT COUNT(*) FROM question_skill_links"
        ).fetchone()[0]
        conn.execute(
            """
            CREATE TRIGGER reject_manual_tag
            BEFORE INSERT ON question_tags
            WHEN NEW.source = 'manual'
            BEGIN
                SELECT RAISE(ABORT, 'injected tag failure');
            END
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        service.replace_tags(
            question_id,
            expected_revision=revision,
            tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
        )

    assert _load_tags(service.db_path, question_id)[0]["tag_value"] == "旧标签"
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM question_skill_links").fetchone()[0] == skill_count


def test_soft_delete_and_restore_preserve_question_tags(write_seed) -> None:
    service, question_id, revision = write_seed

    deleted = service.set_deleted(
        question_id,
        expected_revision=revision,
        deleted=True,
    )
    restored = service.set_deleted(
        question_id,
        expected_revision=deleted.revision,
        deleted=False,
    )

    assert deleted.deleted is True
    assert restored.deleted is False
    assert [row["tag_value"] for row in _load_tags(service.db_path, question_id)] == [
        "旧标签"
    ]


def test_soft_delete_is_idempotent_but_conflicts_with_different_state(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    first = service.set_deleted(
        question_id,
        expected_revision=revision,
        deleted=True,
    )

    repeated = service.set_deleted(
        question_id,
        expected_revision=revision,
        deleted=True,
    )
    assert repeated == first

    with pytest.raises(QuestionWriteConflict):
        service.set_deleted(
            question_id,
            expected_revision=revision,
            deleted=False,
        )


def test_soft_delete_rolls_back_when_update_fails(write_seed) -> None:
    service, question_id, revision = write_seed
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_question_delete
            BEFORE UPDATE OF is_deleted ON questions
            WHEN NEW.is_deleted = 1
            BEGIN
                SELECT RAISE(ABORT, 'injected delete failure');
            END
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        service.set_deleted(
            question_id,
            expected_revision=revision,
            deleted=True,
        )

    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute(
            "SELECT is_deleted FROM questions WHERE id = ?", (question_id,)
        ).fetchone()[0] == 0


def test_create_import_request_only_publishes_pending_manifest(
    write_seed,
) -> None:
    service, _, _ = write_seed
    upload = service.stage_upload(
        filename="../数学试卷.pdf",
        content=b"%PDF-test",
    )

    request = service.create_import_request(upload_id=upload.upload_id)

    assert upload.filename == "数学试卷.pdf"
    assert upload.size == len(b"%PDF-test")
    assert len(upload.sha256) == 64
    assert request.status == "pending"
    assert request.upload_id == upload.upload_id
    assert str(service.data_root) not in json.dumps(request.to_dict())
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_create_import_request_retry_returns_same_request(write_seed) -> None:
    service, _, _ = write_seed
    upload = service.stage_upload(filename="paper.pdf", content=b"%PDF-test")

    first = service.create_import_request(upload_id=upload.upload_id)
    second = service.create_import_request(upload_id=upload.upload_id)

    assert second == first
    requests_root = (
        service.data_root / "question_bank" / "import_staging" / "requests"
    )
    assert len(list(requests_root.glob("*.json"))) == 1


def test_create_import_request_concurrent_retry_publishes_one_manifest(
    write_seed,
) -> None:
    service, _, _ = write_seed
    upload = service.stage_upload(filename="paper.pdf", content=b"%PDF-test")

    services = [
        QuestionBankWriteService(service.db_path, data_root=service.data_root)
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda current: current.create_import_request(
                    upload_id=upload.upload_id
                ),
                services,
            )
        )

    assert results[0] == results[1]
    requests_root = service.data_root / "question_bank" / "import_staging" / "requests"
    assert len(list(requests_root.glob("*.json"))) == 1


def test_stage_upload_rejects_empty_or_unsupported_content(write_seed) -> None:
    service, _, _ = write_seed
    with pytest.raises(ValueError):
        service.stage_upload(filename="empty.pdf", content=b"")
    with pytest.raises(QuestionImportTypeNotSupported):
        service.stage_upload(filename="unsafe.exe", content=b"binary")


def test_create_import_request_rejects_missing_or_tampered_upload(
    write_seed,
) -> None:
    service, _, _ = write_seed
    with pytest.raises(QuestionImportUploadNotFound):
        service.create_import_request(upload_id="0" * 32)

    upload = service.stage_upload(filename="paper.docx", content=b"docx-data")
    source = (
        service.data_root
        / "question_bank"
        / "import_staging"
        / "uploads"
        / upload.upload_id
        / "source.docx"
    )
    source.write_bytes(b"tampered")

    with pytest.raises(QuestionImportUploadNotFound):
        service.create_import_request(upload_id=upload.upload_id)
    requests_root = (
        service.data_root / "question_bank" / "import_staging" / "requests"
    )
    assert not requests_root.exists() or list(requests_root.iterdir()) == []


def test_create_import_request_rejects_tampered_manifest_filename(write_seed) -> None:
    service, _, _ = write_seed
    upload = service.stage_upload(filename="paper.pdf", content=b"%PDF-test")
    manifest_path = (
        service.data_root
        / "question_bank"
        / "import_staging"
        / "uploads"
        / upload.upload_id
        / "upload.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["filename"] = "C:/private/internal-paper.pdf"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(QuestionImportUploadNotFound):
        service.create_import_request(upload_id=upload.upload_id)


def test_stage_upload_enforces_size_limit_and_cleans_temporary_files(
    tmp_path: Path,
) -> None:
    service = QuestionBankWriteService(
        tmp_path / "question_bank.db",
        data_root=tmp_path / "data",
        max_upload_bytes=4,
    )
    with pytest.raises(QuestionImportTooLarge):
        service.stage_upload(filename="paper.pdf", content=b"12345")
    uploads_root = (
        service.data_root / "question_bank" / "import_staging" / "uploads"
    )
    assert not uploads_root.exists() or list(uploads_root.iterdir()) == []


def test_stage_upload_publish_failure_cleans_temporary_directory(
    write_seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = write_seed

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(write_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected publish failure"):
        service.stage_upload(filename="paper.pdf", content=b"%PDF-test")

    uploads_root = service.data_root / "question_bank" / "import_staging" / "uploads"
    assert uploads_root.exists()
    assert list(uploads_root.iterdir()) == []


def test_stream_upload_overflow_cleans_temporary_directory(tmp_path: Path) -> None:
    service = QuestionBankWriteService(
        tmp_path / "question_bank.db",
        data_root=tmp_path / "data",
        max_upload_bytes=4,
    )

    async def chunks():
        yield b"123"
        yield b"45"

    with pytest.raises(QuestionImportTooLarge):
        asyncio.run(service.stage_upload_stream(filename="paper.pdf", chunks=chunks()))
    uploads_root = service.data_root / "question_bank" / "import_staging" / "uploads"
    assert uploads_root.exists()
    assert list(uploads_root.iterdir()) == []


def test_stream_upload_publish_failure_cleans_temporary_directory(
    write_seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = write_seed

    async def chunks():
        yield b"%PDF-test"

    monkeypatch.setattr(
        write_module.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("stream publish failed")),
    )
    with pytest.raises(OSError, match="stream publish failed"):
        asyncio.run(service.stage_upload_stream(filename="paper.pdf", chunks=chunks()))
    uploads_root = service.data_root / "question_bank" / "import_staging" / "uploads"
    assert list(uploads_root.iterdir()) == []


def test_import_request_publish_failure_cleans_temporary_manifest(
    write_seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = write_seed
    upload = service.stage_upload(filename="paper.pdf", content=b"%PDF-test")
    monkeypatch.setattr(
        write_module.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("request publish failed")),
    )

    with pytest.raises(OSError, match="request publish failed"):
        service.create_import_request(upload_id=upload.upload_id)

    requests_root = service.data_root / "question_bank" / "import_staging" / "requests"
    assert requests_root.exists()
    assert list(requests_root.iterdir()) == []


def test_stage_upload_rejects_redirected_staging_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    question_bank_root = data_root / "question_bank"
    question_bank_root.parent.mkdir(parents=True)
    try:
        question_bank_root.symlink_to(outside, target_is_directory=True)
    except OSError as symlink_error:
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(question_bank_root), str(outside)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("Unable to create test junction") from symlink_error
    service = QuestionBankWriteService(
        tmp_path / "question_bank.db",
        data_root=data_root,
    )

    with pytest.raises(QuestionImportStorageForbidden):
        service.stage_upload(filename="paper.pdf", content=b"%PDF-test")
    assert list(outside.iterdir()) == []


def test_import_upload_route_rejects_body_over_service_limit(tmp_path: Path) -> None:
    service = QuestionBankWriteService(
        tmp_path / "question_bank.db",
        data_root=tmp_path / "data",
        max_upload_bytes=4,
    )
    client = _write_client(service)

    response = client.post(
        "/api/question-bank/import-uploads",
        params={"filename": "paper.pdf"},
        content=b"12345",
        headers={"content-type": "application/octet-stream"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "question_import_too_large"


def _write_client(service: QuestionBankWriteService) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import get_question_bank_write_service

    app = create_app()
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app)


def test_question_tag_write_route_returns_manual_state(write_seed) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    response = client.put(
        f"/api/question-bank/questions/{question_id}/tags",
        json={
            "expected_revision": revision,
            "tags": [
                {
                    "tag_type": "knowledge_point",
                    "tag_value": "一次函数",
                    "confidence": 1.0,
                }
            ],
        },
        headers={"x-request-id": "rid-tag-write"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "rid-tag-write"
    assert response.json()["question_id"] == question_id
    assert response.json()["tags"] == [
        {
            "tag_type": "knowledge_point",
            "tag_value": "一次函数",
            "confidence": 1.0,
        }
    ]


def test_question_write_route_maps_stale_revision_to_conflict(write_seed) -> None:
    service, question_id, revision = write_seed
    service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
    )
    client = _write_client(service)

    response = client.put(
        f"/api/question-bank/questions/{question_id}/tags",
        json={
            "expected_revision": revision,
            "tags": [
                {
                    "tag_type": "knowledge_point",
                    "tag_value": "二次函数",
                }
            ],
        },
        headers={"x-request-id": "rid-tag-conflict"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "question_write_conflict"
    assert len(response.json()["error"]["details"]["current_revision"]) == 64
    assert response.json()["error"]["request_id"] == "rid-tag-conflict"


def test_question_tag_write_route_rejects_blank_value_without_server_error(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    response = client.put(
        f"/api/question-bank/questions/{question_id}/tags",
        json={
            "expected_revision": revision,
            "tags": [
                {
                    "tag_type": "knowledge_point",
                    "tag_value": "   ",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "question_tags_invalid"


def test_question_tag_write_route_rejects_out_of_range_confidence(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    response = client.put(
        f"/api/question-bank/questions/{question_id}/tags",
        json={
            "expected_revision": revision,
            "tags": [
                {
                    "tag_type": "knowledge_point",
                    "tag_value": "一次函数",
                    "confidence": 2.0,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_question_delete_and_restore_routes(write_seed) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    deleted = client.request(
        "DELETE",
        f"/api/question-bank/questions/{question_id}",
        json={"expected_revision": revision},
    )
    restored = client.post(
        f"/api/question-bank/questions/{question_id}/restore",
        json={"expected_revision": deleted.json()["revision"]},
    )

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False


def test_question_import_upload_and_request_routes(write_seed) -> None:
    service, _, _ = write_seed
    client = _write_client(service)

    uploaded = client.post(
        "/api/question-bank/import-uploads",
        params={"filename": "paper.pdf"},
        content=b"%PDF-api",
        headers={"content-type": "application/octet-stream"},
    )
    requested = client.post(
        "/api/question-bank/import-requests",
        json={"upload_id": uploaded.json()["upload_id"]},
    )

    assert uploaded.status_code == 201
    assert "path" not in uploaded.text.casefold()
    assert requested.status_code == 201
    assert requested.json()["status"] == "pending"
