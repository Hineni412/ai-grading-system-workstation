from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import cv2
import fitz
import numpy as np
import pytest
from docx import Document
from PIL import Image

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TaggingContext
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
from question_bank.personalized_papers import module as personalized_paper_module
from question_bank.personalized_papers.latex_render import LatexRenderError
from question_bank.personalized_papers.rendering import (
    decode_page_identity,
    page_identity,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)
from question_bank.training_criteria import QuestionAnalysisImage, QuestionAnalysisInput
from tests.phase4.test_personalized_recommendation import (
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
                ref["score_awarded"] = .8 * ref["full_score"]
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


def test_question_snapshot_rewrites_word_images_to_frozen_assets(
    tmp_path: Path,
) -> None:
    buffer = BytesIO()
    Image.new("RGB", (80, 40), "white").save(buffer, format="PNG")
    image = QuestionAnalysisImage(
        role="question",
        mime_type="image/png",
        content=buffer.getvalue(),
    )
    question = QuestionAnalysisInput(
        question_id=1,
        tagging_context=TaggingContext(question_text="富文本题干", has_images=True),
        word_question_blocks=({
            "text": "富文本题干",
            "xml": (
                '<w:p xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:r><w:t>富文本题干</w:t>'
                "</w:r></w:p>"
            ),
            "image_relationships": {"rId9": f"sha256:{image.sha256}"},
        },),
        images=(image,),
    )
    module = PersonalizedPaperModule(
        db_path=tmp_path / "question-bank.db",
        data_root=tmp_path / "data",
        pdf_converter=SyntheticPdfConverter(),
        latex_compiler=SyntheticLatexCompiler(),
    )

    snapshot = module._question_snapshot(  # noqa: SLF001
        question,
        paper_instance_id="synthetic",
    )

    asset_path = snapshot["images"][0]["asset_path"]
    assert snapshot["rich_question_blocks"][0]["image_relationships"] == {
        "rId9": asset_path,
    }
    assert "sha256:" not in json.dumps(snapshot, ensure_ascii=False)
    assert (tmp_path / "data" / asset_path).is_file()


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
    all_text = "\n".join(
        [*(paragraph.text for paragraph in document.paragraphs)]
        + [
            paragraph.text
            for table in document.tables
            for row in table.rows
            for cell in row.cells
            for paragraph in cell.paragraphs
        ]
    )
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


def test_each_public_paper_operation_prepares_the_database_once(
    paper_workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    initialize_calls = _record_database_initialization(monkeypatch)

    instance = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("d", draft),
    )
    assert initialize_calls == [db_path]

    initialize_calls.clear()
    assert module.get(instance["paper_instance_id"]) == instance
    assert initialize_calls == [db_path]

    initialize_calls.clear()
    review_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "review-docx",
    )
    assert initialize_calls == [db_path]

    initialize_calls.clear()
    review_payload = review_path.read_bytes()
    frozen = module.freeze(
        instance["paper_instance_id"],
        _freeze_command("e", instance, review_payload),
        BytesIO(review_payload),
    )
    assert frozen["status"] == "frozen"
    assert initialize_calls == [db_path]


def test_invalid_paper_identifiers_and_batch_token_do_not_prepare_database(
    paper_workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, draft, _db_path, _data_root = paper_workspace
    initialize_calls = _record_database_initialization(monkeypatch)

    with pytest.raises(ValueError, match="draft_id is invalid"):
        module.create_review_instance("invalid", _create_command("d", draft))
    with pytest.raises(ValueError, match="paper_instance_id is invalid"):
        module.get("invalid")
    with pytest.raises(ValueError, match="paper_instance_id is invalid"):
        module.artifact_path("invalid", "review-docx")
    with pytest.raises(ValueError, match="operation_token is invalid"):
        module.create_review_batch(
            str(draft["draft_id"]),
            operation_token="invalid",
            expected_draft_revision=int(draft["revision"]),
            actor_ref="teacher-1",
        )

    assert initialize_calls == []


def test_review_batch_keeps_individual_instances_and_publishes_manifest(
    paper_workspace,
) -> None:
    module, draft, _db_path, _data_root = paper_workspace

    result = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="9" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )

    assert result["status"] == "complete"
    assert result["requested_count"] == result["succeeded_count"] == 1
    assert result["failed_count"] == 0
    assert result["items"][0]["student_id"] == "SYN-S01"
    bundle, media_type = module.batch_artifact_path(result["batch_run_id"], "bundle")
    manifest, _ = module.batch_artifact_path(result["batch_run_id"], "manifest")
    assert media_type == "application/zip"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["items"][0]["paper_instance_id"] == result["items"][0]["paper_instance_id"]
    with ZipFile(bundle) as archive:
        assert "manifest.json" in archive.namelist()
        assert len([name for name in archive.namelist() if name.endswith(".docx")]) == 1

    repeated = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="9" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )
    assert repeated == result
    assert module.get_batch(result["batch_run_id"]) == result
    assert module.list_batches_for_draft(str(draft["draft_id"])) == (result,)

    another_batch = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="a" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )
    assert another_batch["items"][0]["paper_instance_id"] == result["items"][0]["paper_instance_id"]
    assert another_batch["items"][0]["series_version"] == 1

    review_path, _ = module.artifact_path(
        result["items"][0]["paper_instance_id"], "review-docx"
    )
    review_payload = review_path.read_bytes()
    module.freeze(
        result["items"][0]["paper_instance_id"],
        FreezePaperCommand(
            operation_token="b" * 32,
            expected_revision=result["items"][0]["revision"],
            content_sha256=hashlib.sha256(review_payload).hexdigest(),
            filename="reviewed.docx",
            actor_ref="teacher-1",
        ),
        BytesIO(review_payload),
    )
    frozen_bundle, _ = module.batch_artifact_path(
        result["batch_run_id"], "frozen-bundle"
    )
    with ZipFile(frozen_bundle) as archive:
        assert "frozen-manifest.json" in archive.namelist()
        assert len([name for name in archive.namelist() if name.endswith(".pdf")]) == 1


def test_multi_student_batch_prepares_database_once_and_keeps_all_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        request_token="f" * 32,
        diagnosis=_diagnosis(
            student_ids=("SYN-S01", "SYN-S02", "SYN-S03"),
        ),
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
    initialize_calls = _record_database_initialization(monkeypatch)

    result = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="1" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )

    assert result["status"] == "complete"
    assert result["requested_count"] == result["succeeded_count"] == 3
    assert result["failed_count"] == 0
    assert [item["student_id"] for item in result["items"]] == [
        "SYN-S01",
        "SYN-S02",
        "SYN-S03",
    ]
    assert initialize_calls == [db_path]

    initialize_calls.clear()
    bundle, _media_type = module.batch_artifact_path(
        result["batch_run_id"],
        "bundle",
    )
    with ZipFile(bundle) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".docx")]) == 3
    assert initialize_calls == [db_path]


def test_concurrent_batches_reuse_one_student_draft_instance(paper_workspace) -> None:
    module, draft, _db_path, _data_root = paper_workspace

    def create(token: str):
        return module.create_review_batch(
            str(draft["draft_id"]),
            operation_token=token * 32,
            expected_draft_revision=int(draft["revision"]),
            actor_ref="teacher-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        left, right = list(executor.map(create, ("c", "d")))

    assert left["status"] == right["status"] == "complete"
    assert left["items"][0]["paper_instance_id"] == right["items"][0]["paper_instance_id"]
    assert left["items"][0]["series_version"] == right["items"][0]["series_version"] == 1


def test_batch_validates_and_recovers_a_missing_review_artifact(paper_workspace) -> None:
    module, draft, _db_path, _data_root = paper_workspace
    first = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="e" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )
    paper_id = first["items"][0]["paper_instance_id"]
    review_path, _ = module.artifact_path(paper_id, "review-docx")
    review_path.unlink()

    recovered = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="f" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )

    assert recovered["status"] == "complete"
    assert recovered["items"][0]["paper_instance_id"] == paper_id
    restored, _ = module.artifact_path(paper_id, "review-docx")
    assert restored.is_file()


def test_failed_batch_item_can_be_retried_without_replacing_success(paper_workspace) -> None:
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


def test_retrying_a_batch_reuses_its_database_preparation(
    paper_workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    batch = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="0" * 32,
        expected_draft_revision=int(draft["revision"]),
        actor_ref="teacher-1",
    )
    paper_id = batch["items"][0]["paper_instance_id"]
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE personalized_paper_batch_items
            SET status = 'failed', paper_instance_id = NULL,
                error_code = 'synthetic_interruption'
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
    initialize_calls = _record_database_initialization(monkeypatch)

    retried = module.retry_batch(batch["batch_run_id"])

    assert retried["status"] == "complete"
    assert retried["items"][0]["paper_instance_id"] == paper_id
    assert initialize_calls == [db_path]


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
            height, width = image.shape[:2]
            qr_region = image[
                int(height * 0.88) : height,
                int(width * 0.82) : width,
            ]
            decoded, _points, _straight = cv2.QRCodeDetector().detectAndDecode(
                qr_region
            )
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


def test_whole_paper_budget_blocks_only_hard_limits(
    paper_workspace,
) -> None:
    module, draft, db_path, data_root = paper_workspace

    def _rewrite_criteria(mutate) -> None:
        with connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT version_id, criteria_json
                FROM training_criterion_versions
                """
            ).fetchall()
            for row in rows:
                criteria = json.loads(str(row["criteria_json"]))
                mutate(criteria)
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

    # 出卷不调用模型：内容体积再大也只是诊断信息，不再拦截出卷。
    _rewrite_criteria(
        lambda criteria: criteria.update(
            {"rationale": "超长合成判定依据" * 5000}
        )
    )
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )
    fat_draft = recommendation.create(
        request_token="2" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    instance = module.create_review_instance(
        str(fat_draft["draft_id"]),
        _create_command("c", fat_draft),
    )
    assert instance["budget"]["status"] == "ready"
    assert instance["budget"]["estimated_total_tokens"] > 32768

    # 硬上限仍然拦截：判定点超过 120 拒绝出卷，不留半成品实例。
    _rewrite_criteria(
        lambda criteria: criteria.update(
            {
                "points": [
                    {"point_id": f"p{index}", "text": f"合成判定点{index}"}
                    for index in range(130)
                ]
            }
        )
    )
    heavy_draft = recommendation.create(
        request_token="3" * 32,
        diagnosis=_diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    with pytest.raises(PaperBudgetExceeded) as error:
        module.create_review_instance(
            str(heavy_draft["draft_id"]),
            _create_command("d", heavy_draft),
        )
    assert "criterion_point_limit" in error.value.budget["blockers"]
    with connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM personalized_paper_instances"
        ).fetchone()[0] == 1


def _direct_freeze_workspace(
    tmp_path: Path,
    *,
    latex_pages: int = 2,
) -> tuple[PersonalizedPaperModule, dict[str, object], Path, Path]:
    """直接出卷工作区：LaTeX 路径需要冻结富文本块，先写结构化富文本
    再播种，保证判定点哈希与后续加载一致。"""
    from docx import Document as _DocxDocument
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    rich_dir = data_root / "question_bank" / "rich_content"
    rich_dir.mkdir(parents=True, exist_ok=True)
    seed_texts = {
        1: "1. 坐标基础选择题",
        2: "2. 代数基础填空题",
        3: "3. 解一元一次方程",
        4: "4. 证明两个三角形全等",
        5: "5. 尺规作图",
        6: "6. 解另一道一元一次方程",
        7: "7. 解第三道一元一次方程",
        8: "8. 解基础一元一次方程",
        9: "9. 再解一道基础一元一次方程",
        10: "10. 基础全等三角形证明",
    }
    for qid, text in seed_texts.items():
        source = _DocxDocument()
        source.add_paragraph(text)
        rich_payload = {
            "version": 3,
            "question_id": qid,
            "question_blocks": [
                {"text": text, "xml": source.paragraphs[0]._p.xml},  # noqa: SLF001
            ],
            "answer_blocks": [],
        }
        (rich_dir / f"question_{qid}.json").write_text(
            json.dumps(rich_payload, ensure_ascii=False),
            encoding="utf-8",
        )
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
        latex_compiler=SyntheticLatexCompiler(pages=latex_pages),
        clock=lambda: NOW,
    )
    return module, draft, db_path, data_root


def test_direct_freeze_creates_stamped_pdf_without_upload(
    tmp_path: Path,
) -> None:
    module, draft, db_path, _data_root = _direct_freeze_workspace(tmp_path)
    command = CreatePaperCommand(
        operation_token="4" * 32,
        expected_draft_revision=int(draft["revision"]),
        student_id="SYN-S01",
        actor_ref="teacher-1",
        direct_freeze=True,
    )

    instance = module.create_review_instance(str(draft["draft_id"]), command)

    assert instance["status"] == "frozen"
    assert instance["frozen_pdf_sha256"]
    assert len(instance["pages"]) == 2  # SyntheticPdfConverter 固定两页
    with connect(db_path) as connection:
        stored = json.loads(str(connection.execute(
            "SELECT snapshot_json FROM personalized_paper_instances"
            " WHERE paper_instance_id = ?",
            (instance["paper_instance_id"],),
        ).fetchone()[0]))
    renderers = {
        str(item.get("question_snapshot", {}).get("question_id") or 0)
        for item in stored["items"]
    }
    assert renderers  # 快照包含题目快照
    assert stored["render_info"]["renderer"] == "latex", stored["render_info"]
    assert stored["render_info"]["fallback_reason"] is None
    # 两页已是偶数双面，不需要补草稿页。
    assert stored["render_info"]["scratch_page_added"] is False
    frozen_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "frozen-pdf",
    )
    with fitz.open(frozen_path) as document:
        assert document.page_count == 2

    # 幂等：同一操作令牌重复请求返回同一份冻结卷。
    repeated = module.create_review_instance(str(draft["draft_id"]), command)
    assert repeated == instance


def test_direct_freeze_pads_odd_page_count_with_scratch_page(
    tmp_path: Path,
) -> None:
    module, draft, db_path, _data_root = _direct_freeze_workspace(
        tmp_path,
        latex_pages=1,
    )
    command = CreatePaperCommand(
        operation_token="7" * 32,
        expected_draft_revision=int(draft["revision"]),
        student_id="SYN-S01",
        actor_ref="teacher-1",
        direct_freeze=True,
    )

    instance = module.create_review_instance(str(draft["draft_id"]), command)

    assert instance["status"] == "frozen"
    # 内容只有一页时补一页演算草稿区，凑满一张 A4 双面。
    assert len(instance["pages"]) == 2
    with connect(db_path) as connection:
        stored = json.loads(str(connection.execute(
            "SELECT snapshot_json FROM personalized_paper_instances"
            " WHERE paper_instance_id = ?",
            (instance["paper_instance_id"],),
        ).fetchone()[0]))
    assert stored["render_info"]["renderer"] == "latex"
    assert stored["render_info"]["scratch_page_added"] is True
    frozen_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "frozen-pdf",
    )
    with fitz.open(frozen_path) as document:
        assert document.page_count == 2
        scratch_text = document[1].get_text()
        assert "Synthetic latex page" not in scratch_text
        assert (
            "演算草稿区" in scratch_text or "Scratch Paper" in scratch_text
        )
        # 草稿页与其余页面一样盖章：页脚身份与页码都在。
        normalized = scratch_text.replace("\xa0", " ")
        assert (
            "第 2 页 / 共 2 页" in normalized
            or "Page 2/2" in normalized
        )


def test_teacher_reviewed_freeze_keeps_teacher_layout_unpadded(
    paper_workspace,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    module.pdf_converter = SyntheticPdfConverter(pages=1)
    instance = module.create_review_instance(
        str(draft["draft_id"]),
        _create_command("8", draft),
    )
    review_path, _media_type = module.artifact_path(
        instance["paper_instance_id"],
        "review-docx",
    )
    payload = review_path.read_bytes()
    command = FreezePaperCommand(
        operation_token="9" * 32,
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

    # 教师上传的审阅稿保持教师版面：即使只有一页也不补草稿页。
    assert frozen["status"] == "frozen"
    assert len(frozen["pages"]) == 1
    with connect(db_path) as connection:
        stored = json.loads(str(connection.execute(
            "SELECT snapshot_json FROM personalized_paper_instances"
            " WHERE paper_instance_id = ?",
            (instance["paper_instance_id"],),
        ).fetchone()[0]))
    assert stored["render_info"]["scratch_page_added"] is False


def test_direct_freeze_falls_back_to_docx_when_latex_fails(
    paper_workspace,
) -> None:
    module, draft, db_path, _data_root = paper_workspace
    module.latex_compiler = SyntheticLatexCompiler(fail=True)
    command = CreatePaperCommand(
        operation_token="6" * 32,
        expected_draft_revision=int(draft["revision"]),
        student_id="SYN-S01",
        actor_ref="teacher-1",
        direct_freeze=True,
    )

    instance = module.create_review_instance(str(draft["draft_id"]), command)

    assert instance["status"] == "frozen"
    with connect(db_path) as connection:
        stored = json.loads(str(connection.execute(
            "SELECT snapshot_json FROM personalized_paper_instances"
            " WHERE paper_instance_id = ?",
            (instance["paper_instance_id"],),
        ).fetchone()[0]))
    assert stored["render_info"]["renderer"] == "docx"
    assert "LatexRenderError" in str(stored["render_info"]["fallback_reason"])


def test_direct_freeze_batch_freezes_each_student(
    paper_workspace,
) -> None:
    module, draft, _db_path, _data_root = paper_workspace

    batch = module.create_review_batch(
        str(draft["draft_id"]),
        operation_token="5" * 32,
        expected_draft_revision=int(draft["revision"]),
        student_ids=("SYN-S01",),
        actor_ref="teacher-1",
        direct_freeze=True,
    )

    assert batch["succeeded_count"] == 1
    assert batch["failed_count"] == 0
    assert batch["items"][0]["status"] == "frozen"
    assert batch["downloads"]["frozen_bundle"]
    frozen_zip, _media_type = module.batch_artifact_path(
        batch["batch_run_id"],
        "frozen-bundle",
    )
    with ZipFile(frozen_zip) as archive:
        names = archive.namelist()
    assert any(name.endswith(".pdf") for name in names)


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


def _record_database_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    calls: list[Path] = []
    original = personalized_paper_module.initialize_database

    def tracking_initialize(path: Path) -> None:
        calls.append(Path(path))
        original(path)

    monkeypatch.setattr(
        personalized_paper_module,
        "initialize_database",
        tracking_initialize,
    )
    return calls
