"""Teacher duplicate decisions on the generation reuse and intake paths."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.config_workspace.deferred_analysis import DeferredAnalysisArtifactStore
from backend.config_workspace.sources import QuestionDecision
from backend.jobs.config_generation import (
    _intake_confirmed_duplicates,
    _reused_analysis_items,
    config_analysis_source_questions,
)
from backend.jobs.question_bank_sync import _adopt_deferred_analysis_with_links
from question_bank.importers.batch_importer import ScannedPaper
from question_bank.importers import batch_importer
from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.solution_evidence import SolutionEvidenceRepository
from question_bank.training_criteria import (
    ConfigQuestionAnalysisSource,
    DeferredCombinedQuestionAnalysisModule,
    question_analysis_input_from_config_source,
)
from question_bank.training_criteria.combined_analysis import DeferredCombinedAnalysisItem

from tests.current_knowledge_support import install_current_knowledge
from tests.test_config_source_duplicates import (
    _EmptyResolver,
    _insert_bank_questions,
    _seed_analysis,
)
from tests.test_question_import_duplicates import _fake_extract, _single_question_id
from tests.test_session_question_bank_sync_job import (
    _DeferredAdoptionTaggingService,
    _DeferredSyncGateway,
    _PassThroughTaxonomyGovernance,
    _deferred_sync_contract,
)


@pytest.fixture(autouse=True)
def empty_fine_term_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CurrentFineTermResolver,
        "from_active_database",
        classmethod(lambda cls, _db_path: _EmptyResolver()),
    )


@pytest.fixture()
def bank(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    return {"db": db_path, "data_root": data_root}


BANK_TEXT = "这是长度足够的测试题目"
BANK_ANSWER = "42"


def _seed_bank(bank: dict[str, Path]) -> None:
    _insert_bank_questions(
        bank["db"],
        [
            (1, "1", BANK_TEXT, BANK_ANSWER, 0),
            (2, "2", "另一道完全不同的题库题目求矩形面积", "B", 0),
            (3, "3", "解方程3x+5=11", "x=2", 0),
        ],
    )
    _seed_analysis(bank["db"], bank["data_root"], 1)
    _seed_analysis(bank["db"], bank["data_root"], 2)


def _sources(*blocks: dict[str, object]):
    sources, _blocks = config_analysis_source_questions(
        list(blocks),
        {},
        curriculum_volume_id="unresolved",
        volume={},
    )
    return sources


def _reused(
    bank: dict[str, Path],
    sources,
    decisions=(),
):
    return _reused_analysis_items(
        bank["db"],
        data_root=bank["data_root"],
        sources=sources,
        operation_id="config:test",
        decisions=decisions,
    )


def test_same_decision_reuses_teacher_selected_analysis_without_model(
    bank: dict[str, Path],
) -> None:
    _seed_bank(bank)
    # The stems differ, so no exact or uncertain match exists; only the
    # teacher's "same" decision can reuse bank question 2.
    sources = _sources(
        {
            "question_id": "Q9",
            "question_number": "9",
            "question_text": "求长方形面积是多少",
            "answer_text": "B",
            "question_type": "choice",
        }
    )

    undecided = _reused(bank, sources)
    assert undecided == {}

    decided = _reused(
        bank,
        sources,
        decisions=[
            QuestionDecision(
                question_id="Q9",
                excluded=False,
                bank_match="same",
                bank_question_id=2,
            )
        ],
    )
    item = decided["Q9"]
    assert isinstance(item, DeferredCombinedAnalysisItem)
    assert item.reused_from_question_id == 2


def test_answer_override_removes_conflict_and_allows_reuse(
    bank: dict[str, Path],
) -> None:
    _seed_bank(bank)
    def sources_with(answer: str):
        return _sources(
            {
                "question_id": "Q1",
                "question_number": "1",
                "question_text": BANK_TEXT,
                "answer_text": answer,
                "question_type": "calculation",
            }
        )

    conflicted = _reused(bank, sources_with("7"))
    failure = conflicted["Q1"]
    assert not isinstance(failure, DeferredCombinedAnalysisItem)

    # sources.py has already written the teacher-chosen bank answer onto the
    # block, so reuse proceeds with zero model calls.
    overridden = _reused(bank, sources_with(BANK_ANSWER))
    item = overridden["Q1"]
    assert isinstance(item, DeferredCombinedAnalysisItem)
    assert item.reused_from_question_id == 1


def test_confirmed_same_decision_key_reaches_importer_end_to_end(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normalized ``Q5`` decision key must hit the parsed ``question_number``.

    Feeds ``_intake_confirmed_duplicates`` output straight into the real
    importer against a parsed paper, so the key contract between the two
    modules is exercised in one test rather than two stubbed halves.
    """
    _seed_bank(bank)
    text = "5. 求长方形面积是多少\n答案：\n5. B"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(text))
    source = tmp_path / "e.docx"
    source.write_bytes(b"content-of-e")
    confirmed = _intake_confirmed_duplicates(
        [
            QuestionDecision(
                question_id="Q5", excluded=False,
                bank_match="same", bank_question_id=2,
            ),
        ]
    )
    assert confirmed == {"5": 2}

    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="docx", title="e")],
        bank["db"],
        data_root=bank["data_root"],
        archive_sources=False,
        confirmed_duplicates=confirmed,
    )
    imported = result.files[0]
    assert imported.question_count == 1
    assert imported.exact_duplicate_count == 1
    assert imported.analysis_reused_count == 1
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id, occ.question_number
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'e'
            """
        ).fetchone()
        inserted = conn.execute(
            """
            SELECT COUNT(*) FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE p.title = 'e'
            """
        ).fetchone()[0]
    assert occurrence is not None
    assert int(occurrence["question_id"]) == 2
    assert occurrence["question_number"] == "5"
    assert inserted == 0


def test_reanalyze_fresh_analysis_adopts_onto_canonical(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``reanalyze`` decision analyzes fresh and the new analysis lands on
    the canonical bank question rather than a second row."""
    _seed_bank(bank)
    install_current_knowledge(bank["db"])
    # The adoption seam stamps the current knowledge release id; give the
    # fixture's empty resolver that attribute (its ``resolve`` stays unused
    # because the synthetic evidence carries no fine-term links).
    release_resolver = type("_ReleaseEmptyResolver", (_EmptyResolver,), {
        "release_id": "test-release",
    })
    monkeypatch.setattr(
        CurrentFineTermResolver,
        "from_active_database",
        classmethod(lambda cls, _db_path: release_resolver()),
    )
    # Canonical question 3 ("解方程3x+5=11") has no analysis: the teacher
    # picks 重新分析, so the reuse map must not claim it.
    sources = _sources(
        {
            "question_id": "Q3",
            "question_number": "3",
            "question_text": "解方程3x+5=11",
            "answer_text": "x=2",
            "question_type": "calculation",
        }
    )
    decided = _reused(
        bank,
        sources,
        decisions=[
            QuestionDecision(
                question_id="Q3", excluded=False, bank_match="reanalyze",
            ),
        ],
    )
    assert "Q3" not in decided

    # The model runs once for the reanalyzed question (empty fine-term links
    # keep the evidence payload resolvable with the fixture's empty resolver).
    source_question = question_analysis_input_from_config_source(
        {
            "question_id": "Q3",
            "question_text": "解方程3x+5=11",
            "answer_text": "x=2",
            "question_type": "calculation",
        },
        question_id=3,
        curriculum_volume_id="bnu24-math-g7-upper",
        taxonomy_contract=_deferred_sync_contract(),
    )
    gateway = _DeferredSyncGateway(empty_links=True)
    bundle = DeferredCombinedQuestionAnalysisModule(gateway=gateway).analyze(
        operation_id="config:test:reanalyze",
        curriculum_volume_id="bnu24-math-g7-upper",
        sources=(ConfigQuestionAnalysisSource("Q3", source_question),),
    )
    assert gateway.calls == [(3,)]
    assert bundle.items[0].reused_from_question_id is None

    # Intake maps the paper's question 3 to the canonical row by exact key —
    # an occurrence record, no second question row.
    text = "3. 解方程3x+5=11\n答案：\n3. x=2"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(text))
    source_file = tmp_path / "d.docx"
    source_file.write_bytes(b"content-of-d")
    imported = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source_file), file_type="docx", title="d")],
        bank["db"],
        data_root=bank["data_root"],
        archive_sources=False,
    ).files[0]
    assert imported.exact_duplicate_count == 1
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id, occ.question_number
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'd'
            """
        ).fetchone()
        inserted = conn.execute(
            """
            SELECT COUNT(*) FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE p.title = 'd'
            """
        ).fetchone()[0]
    assert occurrence is not None
    assert int(occurrence["question_id"]) == 3
    assert inserted == 0

    # The sync-side adoption seam writes the new analysis onto canonical 3.
    artifact = DeferredAnalysisArtifactStore(tmp_path / "analysis-artifacts").save(
        artifact_id="d" * 32,
        session_id=7,
        source_id="b" * 32,
        source_revision="c" * 64,
        curriculum_volume_id="bnu24-math-g7-upper",
        bundle=bundle,
    )
    adoption = _adopt_deferred_analysis_with_links(
        artifact=artifact,
        session_id=7,
        question_bank_db_path=bank["db"],
        data_root=bank["data_root"],
        links={"Q3": {"bank_question_id": 3}},
        ai_service_factory=lambda: _DeferredAdoptionTaggingService(),
        taxonomy_governance=_PassThroughTaxonomyGovernance(),
    )
    assert adoption["outcome"] == "complete"
    assert adoption["successful_question_ids"] == [3]
    entry = adoption["adoption_results"][0]
    assert entry["question_id"] == 3
    assert entry["tag_status"] == "succeeded"
    assert entry["evidence_status"] == "succeeded"
    assert SolutionEvidenceRepository(bank["db"]).latest(3) is not None


def test_confirmed_duplicate_stale_bank_id_falls_back_to_normal_insert(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_bank(bank)
    variant_text = "1. 求长方形面积是多少\n答案：\n1. B"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(variant_text))
    source = tmp_path / "b.docx"
    source.write_bytes(b"content-of-b")
    result = batch_importer.import_scanned_papers(
        [ScannedPaper(source_file=str(source), file_type="docx", title="b")],
        bank["db"],
        data_root=bank["data_root"],
        archive_sources=False,
        confirmed_duplicates={"1": 999},
    )
    imported = result.files[0]
    # The stale decision falls back to a normal row rather than a dangling link.
    assert _single_question_id(bank["db"], "b") > 0
    assert imported.exact_duplicate_count == 0
