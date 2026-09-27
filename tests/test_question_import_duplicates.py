"""Import-time cross-paper duplicate detection and analysis reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.jobs.tagging_sync import _load_analysis_gaps
from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import ScannedPaper
from question_bank.importers.types import ExtractedDocument
from question_bank.models.question import duplicate_question_key
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
        [ScannedPaper(source_file=str(source), file_type="docx", title=Path(name).stem)],
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


def test_duplicate_key_rejects_unresolved_image_markers() -> None:
    base = duplicate_question_key(
        {"question_text": "求阴影部分面积", "answer_text": "面积为 12"}
    )
    with_images = duplicate_question_key(
        {
            "question_text": (
                "求阴影部分面积"
                "[[IMAGE:question_bank/extracted_images/paper-a/q1.png]]"
            ),
            "answer_text": (
                "[[IMAGE:question_bank/extracted_images/paper-a/a1.png]]"
                "面积为 12"
            ),
        }
    )
    other_source_images = duplicate_question_key(
        {
            "question_text": (
                "求阴影部分面积"
                "[[IMAGE:question_bank/extracted_images/paper-b/q1.png]]"
            ),
            "answer_text": (
                "面积为 12"
                "[[IMAGE:question_bank/extracted_images/paper-b/a1.png]]"
            ),
        }
    )

    assert base
    assert not with_images
    assert not other_source_images


def test_image_markers_do_not_defeat_exact_duplicate_linking(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    from PIL import Image
    for name in ("a/q1.png", "b/q1.png", "b/a1.png"):
        path = bank["data_root"] / "question_bank" / "extracted_images" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 12), "red").save(path)
    first_text = (
        f"1. {QUESTION_TEXT}"
        "[[IMAGE:question_bank/extracted_images/a/q1.png]]\n"
        f"答案：\n1. {ANSWER_TEXT}[[IMAGE:question_bank/extracted_images/b/a1.png]]"
    )
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(first_text))
    _import_paper(tmp_path, bank["db"], bank["data_root"], name="a.docx", text=first_text)
    source_id = _single_question_id(bank["db"], "a")

    # 同题跨卷再次导入，图片路径按 source 变化，判重键必须不受影响。
    second_text = (
        f"1. {QUESTION_TEXT}"
        "[[IMAGE:question_bank/extracted_images/b/q1.png]]\n"
        f"答案：\n1. {ANSWER_TEXT}"
        "[[IMAGE:question_bank/extracted_images/b/a1.png]]"
    )
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(second_text))
    second = _import_paper(tmp_path, bank["db"], bank["data_root"], name="b.docx", text=second_text)

    assert second.question_count == 1
    assert second.exact_duplicate_count == 1
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id, occ.question_number
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()
        assert conn.execute(
            """
            SELECT COUNT(*) FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()[0] == 0
    assert occurrence is not None
    assert int(occurrence["question_id"]) == source_id
    assert str(occurrence["question_number"]) == "1"


def test_exact_duplicate_links_and_reuses_analysis(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _EmptyResolver:
        def resolve(self, fine_term_id: str) -> object:
            raise KeyError(fine_term_id)

    monkeypatch.setattr(
        CurrentFineTermResolver,
        "from_active_database",
        classmethod(lambda cls, _db_path: _EmptyResolver()),
    )
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))
    first = _import_paper(tmp_path, bank["db"], bank["data_root"], name="a.docx", text=PAPER_TEXT)
    assert first.status in {"imported", "needs_review"}
    source_id = _single_question_id(bank["db"], "a")
    _seed_tags(bank["db"], source_id)
    _seed_analysis(bank["db"], bank["data_root"], source_id)

    second = _import_paper(tmp_path, bank["db"], bank["data_root"], name="b.docx", text=PAPER_TEXT)

    assert second.status in {"imported", "needs_review"}
    assert second.question_count == 1
    assert second.exact_duplicate_count == 1
    assert second.analysis_reused_count == 1
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id, occ.question_number
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()
        tags = conn.execute(
            "SELECT tag_type, tag_value FROM question_tags WHERE question_id = ? ORDER BY tag_type",
            (source_id,),
        ).fetchall()
        question = conn.execute(
            "SELECT difficulty, reason FROM questions WHERE id = ?",
            (source_id,),
        ).fetchone()
        assert conn.execute(
            """
            SELECT COUNT(*) FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()[0] == 0
    # 出现记录保留本卷题号，标签/难度/理由共享规范题而不再复制。
    assert occurrence is not None
    assert int(occurrence["question_id"]) == source_id
    assert str(occurrence["question_number"]) == "1"
    assert [(row["tag_type"], row["tag_value"]) for row in tags] == [
        ("ability", "运算能力"),
        ("exam_scope", "中考"),
        ("knowledge_point", "一元一次方程"),
    ]
    assert question["difficulty"] == "4"
    assert question["reason"] == "基础题"
    gaps = _load_analysis_gaps(
        bank["db"],
        [source_id],
        data_root=bank["data_root"],
    )
    assert gaps[source_id] == {"evidence_ready": True, "criteria_ready": True}


def test_identical_stem_with_different_answer_preserves_both_sources_for_review(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))
    _import_paper(tmp_path, bank["db"], bank["data_root"], name="a.docx", text=PAPER_TEXT)
    source_id = _single_question_id(bank["db"], "a")
    _seed_tags(bank["db"], source_id)

    changed = f"1. {QUESTION_TEXT}\n答案：\n1. 43"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(changed))
    second = _import_paper(tmp_path, bank["db"], bank["data_root"], name="b.docx", text=changed)

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
        assert conn.execute("SELECT answer_text FROM questions WHERE id=?", (source_id,)).fetchone()[0] == "42"
        imported = conn.execute("SELECT q.answer_text,q.needs_review FROM questions q JOIN papers p ON q.paper_id=p.id WHERE p.title='b'").fetchone()
        assert tuple(imported) == ("43", 1)


def test_near_variant_imports_normally_with_hint_only(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))
    _import_paper(tmp_path, bank["db"], bank["data_root"], name="a.docx", text=PAPER_TEXT)

    variant_text = f"1. {QUESTION_TEXT}（课堂改编）\n答案：\n1. {ANSWER_TEXT}"
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(variant_text))
    second = _import_paper(tmp_path, bank["db"], bank["data_root"], name="b.docx", text=variant_text)

    assert second.question_count == 1
    assert second.exact_duplicate_count == 0
    assert len(second.near_duplicate_hints) == 1
    assert second.near_duplicate_hints[0]["question_number"] == "1"
    with connect(bank["db"]) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM question_duplicate_links"
        ).fetchone()
    assert int(count["n"]) == 0


def test_exact_duplicate_without_source_analysis_only_links(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))
    _import_paper(tmp_path, bank["db"], bank["data_root"], name="a.docx", text=PAPER_TEXT)
    source_id = _single_question_id(bank["db"], "a")

    second = _import_paper(tmp_path, bank["db"], bank["data_root"], name="b.docx", text=PAPER_TEXT)

    assert second.exact_duplicate_count == 1
    assert second.analysis_reused_count == 0
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()
    assert occurrence is not None
    assert int(occurrence["question_id"]) == source_id


def test_later_file_in_same_batch_matches_earlier_file(
    bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_a = tmp_path / "a.docx"
    source_b = tmp_path / "b.docx"
    source_a.write_bytes(b"content-a")
    source_b.write_bytes(b"content-b")
    monkeypatch.setattr(batch_importer, "_extract_paper", _fake_extract(PAPER_TEXT))

    result = batch_importer.import_scanned_papers(
        [
            ScannedPaper(source_file=str(source_a), file_type="docx", title="a"),
            ScannedPaper(source_file=str(source_b), file_type="docx", title="b"),
        ],
        bank["db"],
        data_root=bank["data_root"],
        archive_sources=False,
    )

    assert result.failed_files == 0
    first_id = _single_question_id(bank["db"], "a")
    with connect(bank["db"]) as conn:
        occurrence = conn.execute(
            """
            SELECT occ.question_id
            FROM paper_question_occurrences occ
            JOIN papers p ON p.id = occ.paper_id
            WHERE p.title = 'b'
            """,
        ).fetchone()
    assert occurrence is not None
    assert int(occurrence["question_id"]) == first_id



def test_exact_identity_checks_pixels_and_preserves_figure_positions(tmp_path):
    from PIL import Image
    from question_bank.services.duplicate_analysis_copy_service import exact_question_key
    root = tmp_path / "data"
    for name, color in (("a.png","red"),("b.png","red"),("c.png","blue")):
        path = root / "question_bank" / "extracted_images" / name
        path.parent.mkdir(parents=True,exist_ok=True)
        Image.new("RGB",(20,20),color).save(path)
    def key(name, prefix="求线段长度"):
        return exact_question_key({"question_text": prefix+f"[[IMAGE:question_bank/extracted_images/{name}]]", "has_images": True},data_root=root)
    assert key("a.png") and key("a.png") == key("b.png")
    assert key("a.png") != key("c.png")
    assert not key("missing.png")
    first = {"question_text":"图甲[[IMAGE:question_bank/extracted_images/a.png]]图乙[[IMAGE:question_bank/extracted_images/c.png]]"}
    swapped = {"question_text":"图甲[[IMAGE:question_bank/extracted_images/c.png]]图乙[[IMAGE:question_bank/extracted_images/a.png]]"}
    assert exact_question_key(first,data_root=root) != exact_question_key(swapped,data_root=root)


def test_exact_identity_keeps_numbers_options_and_formula_content(tmp_path):
    from question_bank.services.duplicate_analysis_copy_service import exact_question_key
    root = tmp_path / "data"
    def key(stem, number="1", **extra):
        return exact_question_key(dict(question_text=stem,question_number=number,**extra),data_root=root)
    assert key("1. 求3的平方") == key("27. 求3的平方", "27")
    assert key("求3的平方") != key("求4的平方")
    assert key("3.5的平方", "3") != key("5的平方", "3")
    assert key("选择结果",options=["A.3","B.4"]) != key("选择结果",options=["A.4","B.3"])
    def formula(latex):
        return exact_question_key({"question_text":"求下式的值"},data_root=root,
            rich_content={"math_expressions":[{"restricted_latex":latex}]})
    assert formula("x^2+1") != formula("x^2-1")
    assert formula("x^2+1") == formula("x^2+1")


def test_manual_add_reuses_existing_labels_without_changing_source_occurrence(bank):
    from question_bank.models.question import QuestionCreate, TagCreate
    from question_bank.services.question_write_service import QuestionBankWriteService
    service = QuestionBankWriteService(bank["db"], data_root=bank["data_root"])
    source = service.add_question(QuestionCreate(question_number="1",question_text="解方程3x+5=11",answer_text="x=2",difficulty="5",
        tags=[TagCreate("knowledge_point","一元一次方程"),TagCreate("method","等式变形")]))
    target = service.add_question(QuestionCreate(question_number="19",question_text="解方程3x+5=11",answer_text="x=2"))
    with connect(bank["db"]) as conn:
        assert conn.execute("SELECT duplicate_of_question_id FROM question_duplicate_links WHERE question_id=?",(target,)).fetchone()[0] == source
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE question_id=?",(target,)).fetchone()[0] == 2
        row=conn.execute("SELECT question_number,difficulty,answer_text FROM questions WHERE id=?",(target,)).fetchone()
        assert tuple(row) == ("19","5","x=2")
    conflict = service.add_question(QuestionCreate(question_number="20",question_text="解方程3x+5=11",answer_text="x=3"))
    with connect(bank["db"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE question_id=?",(conflict,)).fetchone()[0] == 0
        assert conn.execute("SELECT needs_review FROM questions WHERE id=?",(conflict,)).fetchone()[0] == 1
    manual = service.add_question(QuestionCreate(question_number="21",question_text="解方程3x+5=11",answer_text="x=2",difficulty="3",
        tags=[TagCreate("method","人工选择的方法")]))
    with connect(bank["db"]) as conn:
        assert conn.execute("SELECT difficulty FROM questions WHERE id=?",(manual,)).fetchone()[0] == "3"
        assert conn.execute("SELECT needs_review FROM questions WHERE id=?",(manual,)).fetchone()[0] == 1
        assert [row[0] for row in conn.execute("SELECT tag_value FROM question_tags WHERE question_id=? AND tag_type='method'",(manual,))] == ["人工选择的方法"]
    from question_bank.services.duplicate_analysis_copy_service import copy_duplicate_analysis
    assert copy_duplicate_analysis(bank["db"], source_question_id=source, target_question_id=manual, data_root=bank["data_root"]) == {"evidence":False,"criteria":False}


def test_current_filter_keeps_its_duplicate_occurrence_when_source_is_outside(bank):
    from question_bank.services.question_read_service import QuestionBankReadService, QuestionReadFilters
    with connect(bank["db"]) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES (1,'原卷','ready'),(2,'第二卷','ready')")
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(1,1,'1','求3的平方'),(2,2,'9','求3的平方')")
    service=QuestionBankReadService(bank["db"],data_root=bank["data_root"])
    page=service.list_questions(QuestionReadFilters(paper_ids=(2,),collapse_duplicates=True))
    assert [item["id"] for item in page.items] == [2]
