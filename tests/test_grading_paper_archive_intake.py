from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.importers.batch_importer import BatchImportResult, PaperImportFileResult
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services import grading_paper_intake_service
from question_bank.services.ai_tagging_service import AITaggingResult
from question_bank.services.grading_paper_intake_service import (
    archive_uploaded_grading_paper,
    copy_existing_grading_paper_to_raw_dir,
    intake_grading_paper_to_question_bank,
    save_uploaded_grading_paper,
)


def test_archive_uploaded_grading_paper_returns_portable_hash_evidence(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    raw = data_root / "question_bank" / "raw_papers"

    first = archive_uploaded_grading_paper(
        filename="paper.docx",
        content=b"paper-content",
        data_root=data_root,
        raw_papers_dir=raw,
    )
    reused = archive_uploaded_grading_paper(
        filename="renamed.docx",
        content=b"paper-content",
        data_root=data_root,
        raw_papers_dir=raw,
    )

    assert first.stored_path.startswith("question_bank/raw_papers/")
    assert len(first.sha256) == 64
    assert reused.physical_path == first.physical_path
    assert reused.sha256 == first.sha256
    assert reused.reused is True


def test_uploaded_grading_paper_uses_hash_archive(tmp_path: Path) -> None:
    raw = tmp_path / "user_data" / "question_bank" / "raw_papers"
    saved = save_uploaded_grading_paper(
        filename="paper.docx",
        content=b"paper-content",
        raw_papers_dir=raw,
    )
    reused = save_uploaded_grading_paper(
        filename="renamed.docx",
        content=b"paper-content",
        raw_papers_dir=raw,
    )

    assert saved.name.startswith("paper_")
    assert saved.name.endswith(".docx")
    assert saved.read_bytes() == b"paper-content"
    assert reused == saved
    assert len(list(raw.glob("*.docx"))) == 1


def test_copy_existing_grading_paper_reuses_identical_archive(tmp_path: Path) -> None:
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper-content")
    raw = tmp_path / "user_data" / "question_bank" / "raw_papers"

    first = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)
    second = copy_existing_grading_paper_to_raw_dir(source, raw_papers_dir=raw)

    assert first == second
    assert len(list(raw.glob("*.docx"))) == 1
    assert source.exists()


def test_duplicate_intake_returns_the_archived_portable_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    archived = data_root / "question_bank" / "raw_papers" / "paper_hash.docx"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"paper-content")
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper-content")
    stored_source = "question_bank/raw_papers/paper_hash.docx"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)

    monkeypatch.setattr(
        grading_paper_intake_service,
        "import_scanned_papers",
        lambda *_args, **_kwargs: BatchImportResult(
            files=[PaperImportFileResult(source_file=stored_source, status="duplicate")],
            imported_papers=0,
            question_count=0,
            answer_match_count=0,
            review_count=0,
            skipped_duplicate_files=1,
            failed_files=0,
        ),
    )

    result = intake_grading_paper_to_question_bank(
        source_file=source,
        db_path=db_path,
        data_root=data_root,
        run_ai_tagging=False,
    )

    assert result.saved_file == archived


def test_intake_source_saves_tags_without_skill_resolution() -> None:
    source = Path("question_bank/services/grading_paper_intake_service.py").read_text(encoding="utf-8")

    assert "SkillResolutionService" not in source
    assert "build_skill_context_ranker" not in source
    assert "resolve_skills" not in source
    assert "allow_batch_fallback=False" in source
    assert "request_callback=" in source


def test_intake_reuses_one_taxonomy_contract_for_analysis_and_proposals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    stored_source = "question_bank/raw_papers/paper_contract.docx"
    saved_file = data_root / stored_source
    saved_file.parent.mkdir(parents=True)
    saved_file.write_bytes(b"paper-content")
    database = tmp_path / "question-bank.db"
    contract = {
        "taxonomy_revision": 9,
        "candidate_fingerprint": "intake-question-fingerprint",
        "knowledge_catalog_revision": 4,
        "allowed_term_ids": {"knowledge": ["kp-intake"]},
    }
    governance_calls: list[dict[str, object]] = []
    analyzed_contracts: list[dict[int, dict[str, object]]] = []

    class RecordingGovernance:
        @staticmethod
        def constrain(payload, *, context):
            del payload
            governance_calls.append(dict(context))
            return {"proposals": []}

    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": [],
            "method_tags": [],
            "thought_tags": [],
            "ability_tags": [],
            "math_model_tags": [],
            "special_type_tags": [],
            "difficulty": 1,
            "error_prone_points": [],
            "textbook_chapters": [],
            "reason": "合成结果",
            "confidence": 0.9,
            "taxonomy_revision": 9,
        }
    )

    class FakeTagger:
        taxonomy_governance = RecordingGovernance()

        @staticmethod
        def taxonomy_contracts(contexts):
            assert set(contexts) == {1}
            return {1: dict(contract)}

        @staticmethod
        def analyze_questions(contexts, *, taxonomy_contracts, **_kwargs):
            assert set(contexts) == {1}
            analyzed_contracts.append(
                {key: dict(value) for key, value in taxonomy_contracts.items()}
            )
            return {
                1: AITaggingResult(
                    ok=True,
                    mock_mode=False,
                    analysis=analysis,
                    model_name="synthetic-intake",
                    quality_status="needs_review",
                    taxonomy_revision=9,
                    proposals=[
                        {
                            "dimension": "knowledge",
                            "name": "候选知识点",
                            "definition": "候选定义",
                            "reason": "候选目录中没有",
                            "nearest_id": "",
                            "why_not_reuse": "语义边界不同",
                        }
                    ],
                )
            }

    class FakeQuestionWriteService:
        def __init__(self, _database, **_kwargs):
            pass

        @staticmethod
        def save_tag_analysis(*_args, **_kwargs):
            return True

    question = {
        "id": 1,
        "question_text": "合成题目",
        "answer_text": "合成答案",
        "question_number": "1",
        "question_type": "解答题",
    }
    monkeypatch.setattr(
        grading_paper_intake_service,
        "import_scanned_papers",
        lambda *_args, **_kwargs: BatchImportResult(
            files=[PaperImportFileResult(source_file=stored_source, status="imported")],
            imported_papers=1,
            question_count=1,
            answer_match_count=1,
            review_count=0,
            skipped_duplicate_files=0,
            failed_files=0,
        ),
    )
    monkeypatch.setattr(
        grading_paper_intake_service,
        "_questions_for_source",
        lambda *_args, **_kwargs: [question],
    )
    monkeypatch.setattr(
        grading_paper_intake_service,
        "_questions_needing_complete_tags",
        lambda *_args, **_kwargs: [question],
    )
    monkeypatch.setattr(
        grading_paper_intake_service,
        "QuestionBankWriteService",
        FakeQuestionWriteService,
    )

    result = intake_grading_paper_to_question_bank(
        source_file=saved_file,
        db_path=database,
        data_root=data_root,
        ai_service=FakeTagger(),  # type: ignore[arg-type]
    )

    assert result.tagged_questions == 1
    assert analyzed_contracts == [{1: contract}]
    assert governance_calls == [
        {
            "persist_proposals": True,
            "question_ref": "1",
            "model": "synthetic-intake",
            "request_token": (
                "grading-intake:question:1:taxonomy:9:"
                "candidates:intake-question-fingerprint"
            ),
            "expected_revision": 9,
            "allowed_term_ids": {"knowledge": ["kp-intake"]},
            "knowledge_catalog_revision": 4,
        }
    ]
