"""Import-time cross-paper duplicate detection and analysis reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import ScannedPaper
from question_bank.importers.types import ExtractedDocument
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.solution_evidence.repository import SolutionEvidenceRepository
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
from question_bank.training_criteria.analysis import (
    JUDGMENT_POINTS_SCHEMA,
    solution_evidence_source_content_hash,
)
from question_bank.training_criteria.versioning import TrainingCriterionModule


QUESTION_TEXT = "这是长度足够的测试题目"
ANSWER_TEXT = "42"
PAPER_TEXT = f"1. {QUESTION_TEXT}\n答案：\n1. {ANSWER_TEXT}"


def _fake_extract(text: str):
    def extract(path, **_kwargs):
        return ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text=text,
        )

    return extract


def _import_paper(
    tmp_path: Path,
    db_path: Path,
    data_root: Path,
    *,
    name: str,
    text: str,
) -> batch_importer.PaperImportFileResult:
    source = tmp_path / name
    source.write_bytes(f"content-of-{name}".encode("utf-8"))
    result = batch_importer.import_scanned_papers(
        [
            ScannedPaper(
                source_file=str(source), file_type="docx", title=Path(name).stem
            )
        ],
        db_path,
        data_root=data_root,
        archive_sources=False,
    )
    assert result.failed_files == 0
    assert len(result.files) == 1
    return result.files[0]


def _single_question_id(db_path: Path, paper_title: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT q.id
            FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE p.title = ?
            """,
            (paper_title,),
        ).fetchone()
    assert row is not None
    return int(row["id"])


def _seed_tags(db_path: Path, question_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source)
            VALUES (?, 'knowledge_point', '一元一次方程', 0.9, 'test'),
                   (?, 'ability', '运算能力', 0.8, 'test'),
                   (?, 'exam_scope', '中考', 0.7, 'test')
            """,
            (question_id, question_id, question_id),
        )
        conn.execute(
            "UPDATE questions SET difficulty = '4', reason = '基础题' WHERE id = ?",
            (question_id,),
        )


def _seed_analysis(db_path: Path, data_root: Path, question_id: int) -> None:
    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=data_root)
    question_input = loader.load((question_id,))[0]
    resolver = CurrentFineTermResolver.from_active_database(db_path)
    evidence = QuestionSolutionEvidence.from_model_dict(
        {
            "schema_version": "question-solution-evidence-v2",
            "question_id": question_id,
            "parts": [
                {
                    "part_id": "part-1",
                    "label": "主问",
                    "response_mode": "process_required",
                    "canonical_answer": ANSWER_TEXT,
                    "accepted_forms": [ANSWER_TEXT],
                    "full_answer": "完整解答",
                    "proof_obligations": [],
                    "visual_requirements": [],
                    "deduction_policy": ["缺少某台阶只影响该台阶"],
                    "allow_alternative_methods": True,
                    "evidence_points": [
                        {
                            "evidence_point_id": "part-1-step-1",
                            "step_index": 1,
                            "target": "写出答案",
                            "justification": "直接计算",
                            "answer_anchor": ANSWER_TEXT,
                            "observable_evidence": f"写出 {ANSWER_TEXT}",
                            "depends_on": [],
                            "fine_term_links": [],
                            "equivalent_rules": [],
                            "counterexamples": [],
                        }
                    ],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "测试证据",
            "confidence": 0.9,
        },
        question_id=question_id,
        source_content_hash=solution_evidence_source_content_hash(question_input),
        resolver=resolver,
    )
    SolutionEvidenceRepository(db_path).save(
        evidence,
        source_kind="combined_model",
        source_reference="test-seed",
        created_by="tester",
    )
    TrainingCriterionModule(db_path).propose(
        question=question_input,
        draft={
            "schema_version": JUDGMENT_POINTS_SCHEMA,
            "question_id": question_id,
            "points": [
                {
                    "point_id": "jp-1",
                    "target": "写出正确答案",
                    "observable_evidence": f"答案为 {ANSWER_TEXT}",
                    "equivalent_rules": [],
                    "counterexamples": [],
                    "depends_on": [],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "测试判定点",
            "confidence": 0.9,
        },
        source_kind="combined_model",
        source_reference="test-seed",
        actor_ref="tester",
        reason="seed",
    )


@pytest.fixture()
def bank(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    return {"db": db_path, "data_root": data_root}


def test_identical_stem_with_different_answer_preserves_both_sources_for_review(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))
    _import_paper(
        tmp_path, bank["db"], bank["data_root"], name="a.docx", text=PAPER_TEXT
    )
    source_id = _single_question_id(bank["db"], "a")
    _seed_tags(bank["db"], source_id)

    changed = f"1. {QUESTION_TEXT}\n答案：\n1. 43"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(changed))
    second = _import_paper(
        tmp_path, bank["db"], bank["data_root"], name="b.docx", text=changed
    )

    assert second.question_count == 1
    assert second.exact_duplicate_count == 0
    assert second.near_duplicate_hints[0]["match_kind"] == "answer_conflict"
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()
        tags = conn.execute(
            "SELECT COUNT(*) AS n FROM question_tags WHERE question_id = ?",
            (source_id,),
        ).fetchone()
    # 答案冲突不能悄悄用旧答案覆盖新来源，也不能传播尚未核对的分析。
    assert occurrence is None
    assert int(tags["n"]) == 3
    with connect(bank["db"]) as conn:
        assert (
            conn.execute(
                "SELECT answer_text FROM questions WHERE id=?", (source_id,)
            ).fetchone()[0]
            == "42"
        )
        imported = conn.execute(
            "SELECT q.answer_text,q.needs_review FROM questions q JOIN papers p ON q.paper_id=p.id WHERE p.title='b'"
        ).fetchone()
        assert tuple(imported) == ("43", 1)


def test_exact_identity_checks_pixels_and_preserves_figure_positions(tmp_path):
    from PIL import Image
    from question_bank.services.duplicate_analysis_copy_service import (
        exact_question_key,
    )

    root = tmp_path / "data"
    for name, color in (("a.png", "red"), ("b.png", "red"), ("c.png", "blue")):
        path = root / "question_bank" / "extracted_images" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 20), color).save(path)

    def key(name, prefix="求线段长度"):
        return exact_question_key(
            {
                "question_text": prefix
                + f"[[IMAGE:question_bank/extracted_images/{name}]]",
                "has_images": True,
            },
            data_root=root,
        )

    assert key("a.png") and key("a.png") == key("b.png")
    assert key("a.png") != key("c.png")
    assert not key("missing.png")
    first = {
        "question_text": "图甲[[IMAGE:question_bank/extracted_images/a.png]]图乙[[IMAGE:question_bank/extracted_images/c.png]]"
    }
    swapped = {
        "question_text": "图甲[[IMAGE:question_bank/extracted_images/c.png]]图乙[[IMAGE:question_bank/extracted_images/a.png]]"
    }
    assert exact_question_key(first, data_root=root) != exact_question_key(
        swapped, data_root=root
    )
