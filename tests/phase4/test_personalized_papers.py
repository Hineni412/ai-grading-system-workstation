from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest
from docx import Document

from question_bank.database.schema import connect, initialize_database
from question_bank.personalized_papers import (
    CreatePaperCommand,
    FreezePaperCommand,
    PaperArtifactNotFound,
    PaperBudgetExceeded,
    PaperInvalid,
    PaperRenderUnavailable,
    PaperRevisionConflict,
    PersonalizedPaperModule,
)
from question_bank.personalized_papers.rendering import (
    decode_page_identity,
    page_identity,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)
from tests.phase4.test_personalized_recommendation import (
    NOW,
    _diagnosis,
    _seed_recommendation_sources,
)
from tests.current_knowledge_support import install_current_knowledge


class SyntheticPdfConverter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        if self.fail:
            raise OSError("synthetic conversion failure")
        document = fitz.open()
        try:
            for page_number in (1, 2):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic reviewed page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


@pytest.fixture()
def paper_workspace(
    tmp_path: Path,
) -> tuple[PersonalizedPaperModule, dict[str, object], Path, Path]:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    _seed_recommendation_sources(db_path, data_root)
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )
    draft = recommendation.create(
        request_token="1" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    module = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=SyntheticPdfConverter(),
        clock=lambda: NOW,
    )
    return module, draft, db_path, data_root


def test_page_identity_uses_compact_v2_qr_format_and_reads_legacy_v1() -> None:
    instance_id = "a" * 64
    signature = "b" * 32

    current = page_identity(
        paper_instance_id=instance_id,
        series_version=1,
        page_number=2,
        total_pages=3,
        signature=signature,
    )
    legacy = f"P4P1|{instance_id}|1|2|3|{signature}"

    assert current == f"P4P2:{instance_id.upper()}:1:2:3:{signature.upper()}"
    assert decode_page_identity(current) == {
        "identity_version": "P4P2",
        "paper_instance_id": instance_id,
        "series_version": 1,
        "page_number": 2,
        "total_pages": 3,
        "page_signature": signature,
    }
    assert decode_page_identity(legacy)["identity_version"] == "P4P1"


def test_create_review_docx_is_idempotent_versioned_and_immutable(
    paper_workspace,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    command = _create_command("2", draft)

    first = module.create_review_instance(str(draft["draft_id"]), command)
    repeated = module.create_review_instance(str(draft["draft_id"]), command)
    second = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("3", draft),
    )

    assert repeated == first
    assert second["paper_instance_id"] != first["paper_instance_id"]
    assert (first["series_version"], second["series_version"]) == (1, 2)
    assert first["status"] == second["status"] == "review_pending"
    review_path, _media_type = module.artifact_path(
        first["paper_instance_id"],
        "review-docx",
    )
    review_hash = hashlib.sha256(review_path.read_bytes()).hexdigest()
    document = Document(review_path)
    all_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert first["paper_instance_id"] in all_text
    assert "任务题码" in all_text
    assert "分值" not in all_text
    assert review_hash == first["review_docx_sha256"]

    with connect(db_path) as connection:
        stored = connection.execute(
            """
            SELECT snapshot_json
            FROM personalized_paper_instances
            WHERE paper_instance_id = ?
            """,
            (first["paper_instance_id"],),
        ).fetchone()
        items = connection.execute(
            """
            SELECT task_item_code, criterion_version_id,
                   criterion_snapshot_json, recommendation_snapshot_json
            FROM personalized_paper_items
            WHERE paper_instance_id = ?
            ORDER BY item_order
            """,
            (first["paper_instance_id"],),
        ).fetchall()
    snapshot = json.loads(str(stored["snapshot_json"]))
    assert snapshot["draft"]["config"]
    assert len(items) == first["question_count"]
    assert all(
        str(item["task_item_code"]).startswith(
            f"P4-{first['paper_instance_id'][:20].upper()}"
        )
        and json.loads(str(item["criterion_snapshot_json"]))["status"]
        == "approved"
        and json.loads(str(item["recommendation_snapshot_json"]))["reason"]
        for item in items
    )

    with connect(db_path) as connection:
        connection.execute(
            "UPDATE knowledge_relations SET revision = revision + 1"
        )
    after_legacy_change = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("4", draft),
    )
    assert after_legacy_change["series_version"] == 3
    assert hashlib.sha256(review_path.read_bytes()).hexdigest() == review_hash


def test_freeze_stamps_every_page_and_rejects_identity_tampering(
    paper_workspace,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    instance = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("5", draft),
    )
    review_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "review-docx",
    )
    payload = review_path.read_bytes()
    command = FreezePaperCommand(
        operation_token="6" * 32,
        expected_revision=instance["revision"],
        content_sha256=hashlib.sha256(payload).hexdigest(),
        filename="reviewed.docx",
        actor_ref="teacher-1",
    )

    frozen = module.freeze(
        instance["paper_instance_id"],
        command,
        BytesIO(payload),
    )
    repeated = module.freeze(
        instance["paper_instance_id"],
        command,
        BytesIO(b""),
    )

    assert repeated == frozen
    assert frozen["status"] == "frozen"
    assert frozen["revision"] == 2
    assert frozen["pages"] == [
        {
            **frozen["pages"][0],
            "page_number": 1,
            "total_pages": 2,
        },
        {
            **frozen["pages"][1],
            "page_number": 2,
            "total_pages": 2,
        },
    ]
    pdf_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "frozen-pdf",
    )
    with fitz.open(pdf_path) as pdf:
        assert pdf.page_count == 2
        assert "Synthetic reviewed page 1" in pdf[0].get_text()
        assert "Synthetic reviewed page 2" in pdf[1].get_text()
        pixmap = pdf[0].get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
        image = cv2.cvtColor(
            np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                pixmap.n,
            ),
            cv2.COLOR_RGB2BGR,
        )
        height, width = image.shape[:2]
        qr_region = image[
            int(height * 0.88) : height,
            int(width * 0.82) : width,
        ]
        decoded, _points, _straight = cv2.QRCodeDetector().detectAndDecode(
            qr_region
        )
    with connect(db_path) as connection:
        identities = [
            str(row["page_identity"])
            for row in connection.execute(
                """
                SELECT page_identity
                FROM personalized_paper_pages
                WHERE paper_instance_id = ?
                ORDER BY page_number
                """,
                (instance["paper_instance_id"],),
            ).fetchall()
        ]
    assert decoded == identities[0]
    assert "SYN-S01" not in decoded
    assert module.verify_page_identity(identities[0])["page_number"] == 1
    tampered = f"{identities[0][:-1]}{'0' if identities[0][-1] != '0' else '1'}"
    with pytest.raises(PaperInvalid):
        module.verify_page_identity(tampered)


def test_conversion_failure_and_concurrent_confirmation_leave_no_half_pdf(
    paper_workspace,
) -> None:
    module, draft, db_path, data_root = paper_workspace
    instance = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("7", draft),
    )
    review_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "review-docx",
    )
    payload = review_path.read_bytes()
    failing = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=SyntheticPdfConverter(fail=True),
        clock=lambda: NOW,
    )
    with pytest.raises(PaperRenderUnavailable):
        failing.freeze(
            instance["paper_instance_id"],
            _freeze_command("8", instance, payload),
            BytesIO(payload),
        )
    assert module.get(instance["paper_instance_id"])["status"] == "review_pending"
    with pytest.raises(PaperArtifactNotFound):
        module.artifact_path(instance["paper_instance_id"], "frozen-pdf")

    def freeze(token: str) -> str:
        try:
            module.freeze(
                instance["paper_instance_id"],
                _freeze_command(token, instance, payload),
                BytesIO(payload),
            )
            return "frozen"
        except (PaperInvalid, PaperRevisionConflict):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(freeze, ("9", "a")))
    assert sorted(results) == ["conflict", "frozen"]
    assert module.get(instance["paper_instance_id"])["status"] == "frozen"


def test_whole_paper_budget_blocks_before_document_creation(
    paper_workspace,
) -> None:
    module, _draft, db_path, data_root = paper_workspace
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT version_id, criteria_json
            FROM training_criterion_versions
            """
        ).fetchall()
        for row in rows:
            criteria = json.loads(str(row["criteria_json"]))
            criteria["rationale"] = "超长合成判定依据" * 5000
            encoded = json.dumps(
                criteria,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                UPDATE training_criterion_versions
                SET criteria_json = ?, criteria_hash = ?
                WHERE version_id = ?
                """,
                (
                    encoded,
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    row["version_id"],
                ),
            )
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )
    draft = recommendation.create(
        request_token="b" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    with pytest.raises(PaperBudgetExceeded) as error:
        module.create_review_instance(
            str(draft["draft_id"]),
            _create_command("c", draft),
        )
    assert "context_window_limit" in error.value.budget["blockers"]
    with connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM personalized_paper_instances"
        ).fetchone()[0] == 0


def _create_command(
    token_character: str,
    draft: dict[str, object],
) -> CreatePaperCommand:
    return CreatePaperCommand(
        operation_token=token_character * 32,
        expected_draft_revision=int(draft["revision"]),
        student_id="SYN-S01",
        actor_ref="teacher-1",
    )


def _freeze_command(
    token_character: str,
    instance: dict[str, object],
    payload: bytes,
) -> FreezePaperCommand:
    return FreezePaperCommand(
        operation_token=token_character * 32,
        expected_revision=int(instance["revision"]),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        filename="reviewed.docx",
        actor_ref="teacher-1",
    )
