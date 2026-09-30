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
    PaperInvalid,
    PaperRenderUnavailable,
    PaperRevisionConflict,
    PersonalizedPaperModule,
)
from question_bank.personalized_papers import module as personalized_paper_module
from question_bank.personalized_papers.latex_render import LatexRenderError
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)
from question_bank.training_submissions.module import _read_page_identity
from tests.training.test_personalized_recommendation import (
    NOW,
    _diagnosis as _base_diagnosis,
    _seed_recommendation_sources,
)
from tests.current_knowledge_support import install_current_knowledge


def _diagnosis(**kwargs):
    """Keep export fixtures within their candidate bank's 4-6 difficulty band."""
    diagnosis = _base_diagnosis(**kwargs)
    for student in diagnosis["students"]:
        for point in student["weak_points"]:
            for ref in point["source_question_refs"]:
                ref["score_awarded"] = 0.8 * ref["full_score"]
    return diagnosis


class SyntheticPdfConverter:
    def __init__(self, *, fail: bool = False, pages: int = 2) -> None:
        self.fail = fail
        self.pages = pages

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        if self.fail:
            raise OSError("synthetic conversion failure")
        document = fitz.open()
        try:
            for page_number in range(1, self.pages + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic reviewed page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


class SyntheticLatexCompiler:
    def __init__(self, *, fail: bool = False, pages: int = 2) -> None:
        self.fail = fail
        self.pages = pages

    @property
    def available(self) -> bool:
        return True

    def compile(self, tex_source: str, output_pdf: Path) -> None:
        if self.fail:
            raise LatexRenderError("synthetic latex failure")
        document = fitz.open()
        try:
            for page_number in range(1, self.pages + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic latex page {page_number}",
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
        latex_compiler=SyntheticLatexCompiler(),
        clock=lambda: NOW,
    )
    return module, draft, db_path, data_root


def test_failed_batch_item_can_be_retried_without_replacing_success(
    paper_workspace,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    batch = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="7" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )
    paper_id = batch["items"][0]["paper_instance_id"]
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE personalized_paper_batch_items
            SET status = 'failed', paper_instance_id = NULL, error_code = 'synthetic_interruption'
            WHERE batch_run_id = ?
            """,
            (batch["batch_run_id"],),
        )
        connection.execute(
            """
            UPDATE personalized_paper_batches
            SET status = 'partial', succeeded_count = 0, failed_count = 1
            WHERE batch_run_id = ?
            """,
            (batch["batch_run_id"],),
        )

    retried = module.retry_batch(batch["batch_run_id"])

    assert retried["status"] == "complete"
    assert retried["items"][0]["paper_instance_id"] == paper_id


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
    decoded_pages: list[str] = []
    with fitz.open(pdf_path) as pdf:
        assert pdf.page_count == 2
        assert "Synthetic reviewed page 1" in pdf[0].get_text()
        assert "Synthetic reviewed page 2" in pdf[1].get_text()
        visible_identity = pdf[0].get_text()
        assert (
            str(instance["student_name"]) in visible_identity
            or str(instance["student_code"]) in visible_identity
        )
        for page in pdf:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
            image = cv2.cvtColor(
                np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height,
                    pixmap.width,
                    pixmap.n,
                ),
                cv2.COLOR_RGB2BGR,
            )
            # Decode the rendered page through the same rotation and scale
            # handling used when a teacher uploads a scanned training paper.
            _oriented, decoded, _rotation = _read_page_identity(image)
            decoded_pages.append(decoded)
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
    assert decoded_pages == identities
    assert all("SYN-S01" not in decoded for decoded in decoded_pages)
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
        latex_compiler=SyntheticLatexCompiler(),
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
