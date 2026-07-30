from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.personalized_papers import (
    CreatePaperCommand,
    FreezePaperCommand,
    PersonalizedPaperModule,
)
from question_bank.personalized_papers.rendering import _qr_png
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)
from question_bank.training_submissions import (
    CreateScanBatchCommand,
    IngestUploadCommand,
    ResolvePageCommand,
    SubmissionRequestConflict,
    SubmissionRevisionConflict,
    TrainingSubmissionModule,
)
from tests.phase4.test_personalized_papers import (
    _create_command,
    paper_workspace,
)
from tests.phase4.test_personalized_recommendation import (
    NOW,
    _diagnosis,
    _seed_recommendation_sources,
)


class VariablePagePdfConverter:
    def __init__(self) -> None:
        self.calls = 0

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        self.calls += 1
        document = fitz.open()
        try:
            for page_number in range(1, self.calls + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic variable page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


def test_five_mixed_papers_with_different_page_counts_group_correctly(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    _seed_recommendation_sources(db_path, data_root)
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )
    draft = recommendation.create(
        request_token="1" * 32,
        diagnosis=_diagnosis(),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    paper = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=VariablePagePdfConverter(),
        clock=lambda: NOW,
    )
    frozen: list[dict[str, object]] = []
    for index, student in enumerate(draft["students"], start=1):
        instance = paper.create_review_instance(
            str(draft["draft_id"]),
            CreatePaperCommand(
                operation_token=f"{index + 1:032x}",
                expected_draft_revision=int(draft["revision"]),
                student_id=str(student["student_id"]),
                actor_ref="teacher-1",
            ),
        )
        review, _ = paper.artifact_path(
            str(instance["paper_instance_id"]),
            "review-docx",
        )
        content = review.read_bytes()
        frozen.append(
            paper.freeze(
                str(instance["paper_instance_id"]),
                FreezePaperCommand(
                    operation_token=f"{index + 10:032x}",
                    expected_revision=int(instance["revision"]),
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    filename="reviewed.docx",
                    actor_ref="teacher-1",
                ),
                BytesIO(content),
            )
        )
    # The converter emits 1, 2, 3, 4 and 5 pages in creation order.
    page_refs: list[tuple[Path, int]] = []
    for expected_pages, instance in enumerate(frozen, start=1):
        path, _ = paper.artifact_path(
            str(instance["paper_instance_id"]),
            "frozen-pdf",
        )
        page_refs.extend((path, page_number) for page_number in range(expected_pages))
    mixed = fitz.open()
    try:
        for path, page_number in reversed(page_refs):
            with fitz.open(path) as source:
                mixed.insert_pdf(source, from_page=page_number, to_page=page_number)
        payload = mixed.tobytes(garbage=4, deflate=True)
    finally:
        mixed.close()

    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="f" * 32,
            paper_instance_ids=tuple(
                str(instance["paper_instance_id"]) for instance in frozen
            ),
            actor_ref="teacher-1",
        )
    )
    batch = module.ingest(
        str(batch["batch_id"]),
        IngestUploadCommand(
            operation_token="e" * 32,
            expected_revision=1,
            filename="mixed.pdf",
            media_type="application/pdf",
            content_sha256=hashlib.sha256(payload).hexdigest(),
            actor_ref="teacher-1",
        ),
        BytesIO(payload),
    )
    assert batch["status"] == "ready", {
        "pages": [
            (page["page_number"], page["state"], page["issue_code"])
            for page in batch["pages"]
        ],
        "submissions": [
            (
                submission["expected_total_pages"],
                submission["missing_pages"],
                submission["issue_codes"],
            )
            for submission in batch["submissions"]
        ],
    }
    assert sorted(
        submission["expected_total_pages"]
        for submission in batch["submissions"]
    ) == [1, 2, 3, 4, 5]
    assert all(
        submission["missing_pages"] == []
        for submission in batch["submissions"]
    )
    assert len(batch["pages"]) == 15


def test_shuffled_pages_group_by_signed_identity_and_survive_restart(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="d")
    frozen_path, _ = paper.artifact_path(
        str(instance["paper_instance_id"]),
        "frozen-pdf",
    )
    pages = _pdf_pages(frozen_path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="e" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )

    shuffled = (pages[1], _rotate_png(pages[0]))
    for index, content in enumerate(shuffled, start=1):
        batch = module.ingest(
            str(batch["batch_id"]),
            _upload(
                token=f"{index + 1:x}",
                revision=int(batch["revision"]),
                content=content,
            ),
            BytesIO(content),
        )

    assert batch["status"] == "ready", [
        (
            page["page_number"],
            page["state"],
            page["issue_code"],
            page["rotation_degrees"],
            page["width_pixels"],
            page["height_pixels"],
        )
        for page in batch["pages"]
    ]
    assert batch["submissions"][0]["missing_pages"] == []
    assert [
        page["page_number"]
        for page in sorted(
            batch["pages"],
            key=lambda item: int(item["page_number"] or 0),
        )
    ] == [1, 2]
    assert all(page["state"] == "assigned" for page in batch["pages"])
    assert any(page["rotation_degrees"] != 0 for page in batch["pages"])
    restarted = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    assert restarted.get_batch(str(batch["batch_id"])) == batch
    finalized = restarted.finalize_submission(
        str(batch["submissions"][0]["submission_id"])
    )
    assert finalized["submission_revision"] == 1
    assert [page["page_number"] for page in finalized["pages"]] == [1, 2]


def test_duplicate_conflict_external_and_manual_replacement_are_explicit(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="7")
    frozen_path, _ = paper.artifact_path(
        str(instance["paper_instance_id"]),
        "frozen-pdf",
    )
    first, second = _pdf_pages(frozen_path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="8" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="9", revision=1, content=first),
        BytesIO(first),
    )
    duplicate_pdf = _images_pdf((first, first))
    batch = module.ingest(
        str(batch["batch_id"]),
        IngestUploadCommand(
            operation_token="a" * 32,
            expected_revision=int(batch["revision"]),
            filename="duplicate.pdf",
            media_type="application/pdf",
            content_sha256=hashlib.sha256(duplicate_pdf).hexdigest(),
            actor_ref="teacher-1",
        ),
        BytesIO(duplicate_pdf),
    )
    assert any(
        page["issue_code"] == "duplicate_page" for page in batch["pages"]
    )

    altered = _mark_image(first)
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="b", revision=int(batch["revision"]), content=altered),
        BytesIO(altered),
    )
    conflicts = [
        page
        for page in batch["pages"]
        if page["issue_code"] == "page_content_conflict"
    ]
    assert conflicts, [
        (page["page_number"], page["state"], page["issue_code"])
        for page in batch["pages"]
    ]
    conflict = conflicts[0]
    blank = _blank_png()
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="c", revision=int(batch["revision"]), content=blank),
        BytesIO(blank),
    )
    assert any(
        page["issue_code"] == "identity_unreadable" for page in batch["pages"]
    )
    with pytest.raises(SubmissionRevisionConflict):
        module.resolve_page(
            str(batch["batch_id"]),
            ResolvePageCommand(
                operation_token="d" * 32,
                expected_revision=int(batch["revision"]) - 1,
                scan_page_id=str(conflict["scan_page_id"]),
                action="replace",
                paper_instance_id=str(instance["paper_instance_id"]),
                page_number=1,
                actor_ref="teacher-1",
            ),
        )

    batch = module.resolve_page(
        str(batch["batch_id"]),
        ResolvePageCommand(
            operation_token="e" * 32,
            expected_revision=int(batch["revision"]),
            scan_page_id=str(conflict["scan_page_id"]),
            action="replace",
            paper_instance_id=str(instance["paper_instance_id"]),
            page_number=1,
            actor_ref="teacher-1",
        ),
    )
    assigned = [
        page
        for page in batch["pages"]
        if page["state"] == "assigned" and page["page_number"] == 1
    ]
    assert [page["scan_page_id"] for page in assigned] == [
        conflict["scan_page_id"]
    ]
    assert any(page["state"] == "replaced" for page in batch["pages"])
    assert batch["status"] == "manual_review"
    assert batch["submissions"][0]["missing_pages"] == [2]

    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="f", revision=int(batch["revision"]), content=second),
        BytesIO(second),
    )
    unresolved = [
        page
        for page in batch["pages"]
        if page["state"] in {"duplicate", "conflict", "unassigned"}
    ]
    for index, page in enumerate(unresolved):
        batch = module.resolve_page(
            str(batch["batch_id"]),
            ResolvePageCommand(
                operation_token=f"{index + 1:032x}",
                expected_revision=int(batch["revision"]),
                scan_page_id=str(page["scan_page_id"]),
                action="dismiss",
                actor_ref="teacher-1",
            ),
        )
    assert batch["status"] == "ready"


def test_same_upload_is_idempotent_without_counting_pages_twice(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="1")
    path, _ = paper.artifact_path(str(instance["paper_instance_id"]), "frozen-pdf")
    content = _pdf_pages(path)[0]
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="2" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    command = _upload(token="3", revision=1, content=content)
    first = module.ingest(str(batch["batch_id"]), command, BytesIO(content))
    repeated = module.ingest(str(batch["batch_id"]), command, BytesIO(content))
    assert repeated == first
    duplicate_command = _upload(
        token="4",
        revision=int(first["revision"]),
        content=content,
    )
    duplicate = module.ingest(
        str(batch["batch_id"]),
        duplicate_command,
        BytesIO(content),
    )
    assert duplicate["duplicate_upload"] is True
    assert len(duplicate["pages"]) == 1
    repeated_duplicate = module.ingest(
        str(batch["batch_id"]),
        duplicate_command,
        BytesIO(content),
    )
    assert repeated_duplicate["revision"] == duplicate["revision"]
    with pytest.raises(SubmissionRequestConflict):
        module.ingest(
            str(batch["batch_id"]),
            IngestUploadCommand(
                operation_token=duplicate_command.operation_token,
                expected_revision=int(duplicate["revision"]),
                filename="other.png",
                media_type="image/png",
                content_sha256=hashlib.sha256(_blank_png()).hexdigest(),
                actor_ref="teacher-1",
            ),
            BytesIO(_blank_png()),
        )


def test_blur_crop_and_invalid_signature_stay_in_manual_review(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="5")
    path, _ = paper.artifact_path(str(instance["paper_instance_id"]), "frozen-pdf")
    first, second = _pdf_pages(path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="6" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    samples = (
        ("7", _crop_png(first)),
        ("8", _blur_png(second)),
        (
            "9",
            _tampered_identity_png(
                db_path,
                str(instance["paper_instance_id"]),
            ),
        ),
    )
    for token, content in samples:
        batch = module.ingest(
            str(batch["batch_id"]),
            _upload(
                token=token,
                revision=int(batch["revision"]),
                content=content,
            ),
            BytesIO(content),
        )
    issues = {page["issue_code"] for page in batch["pages"]}
    assert "severe_crop" in issues
    assert "image_blurry" in issues
    assert "invalid_identity" in issues
    assert batch["status"] == "manual_review"


def _freeze(paper, draft, *, token: str):
    instance = paper.create_review_instance(
        str(draft["draft_id"]),
        _create_command(token, draft),
    )
    review, _ = paper.artifact_path(str(instance["paper_instance_id"]), "review-docx")
    content = review.read_bytes()
    return paper.freeze(
        str(instance["paper_instance_id"]),
        FreezePaperCommand(
            operation_token=f"{int(token, 16) + 1:x}" * 32,
            expected_revision=int(instance["revision"]),
            content_sha256=hashlib.sha256(content).hexdigest(),
            filename="reviewed.docx",
            actor_ref="teacher-1",
        ),
        BytesIO(content),
    )


def _pdf_pages(path: Path) -> tuple[bytes, ...]:
    result: list[bytes] = []
    with fitz.open(path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                3,
            )
            success, encoded = cv2.imencode(
                ".png",
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            )
            assert success
            result.append(bytes(encoded))
    return tuple(result)


def _upload(
    *,
    token: str,
    revision: int,
    content: bytes,
) -> IngestUploadCommand:
    return IngestUploadCommand(
        operation_token=token * 32,
        expected_revision=revision,
        filename="scan.png",
        media_type="image/png",
        content_sha256=hashlib.sha256(content).hexdigest(),
        actor_ref="teacher-1",
    )


def _mark_image(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    cv2.rectangle(image, (80, 80), (180, 130), (0, 0, 0), thickness=-1)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _rotate_png(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    success, encoded = cv2.imencode(".png", rotated)
    assert success
    return bytes(encoded)


def _crop_png(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    cropped = image[:, int(image.shape[1] * 0.22) :]
    success, encoded = cv2.imencode(".png", cropped)
    assert success
    return bytes(encoded)


def _blur_png(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    blurred = cv2.GaussianBlur(image, (41, 41), 14)
    success, encoded = cv2.imencode(".png", blurred)
    assert success
    return bytes(encoded)


def _tampered_identity_png(db_path: Path, paper_instance_id: str) -> bytes:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT page_identity FROM personalized_paper_pages
            WHERE paper_instance_id = ? AND page_number = 1
            """,
            (paper_instance_id,),
        ).fetchone()
    identity = str(row["page_identity"])
    tampered = f"{identity[:-1]}{'0' if identity[-1] != '0' else '1'}"
    qr = cv2.imdecode(
        np.frombuffer(_qr_png(tampered), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    image = np.full((1800, 1273, 3), 255, dtype=np.uint8)
    qr = cv2.resize(qr, (220, 220), interpolation=cv2.INTER_NEAREST)
    image[-260:-40, -260:-40] = qr
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _blank_png() -> bytes:
    image = np.full((1200, 850, 3), 255, dtype=np.uint8)
    cv2.putText(
        image,
        "external synthetic page",
        (80, 140),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 0),
        2,
    )
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _images_pdf(images: tuple[bytes, ...]) -> bytes:
    document = fitz.open()
    try:
        for content in images:
            image = cv2.imdecode(
                np.frombuffer(content, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            height, width = image.shape[:2]
            page = document.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=content)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
