"""Content identity, stale index and source restoration regression cases."""
import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from backend.jobs.config_generation import _reused_analysis_items
from question_bank.database.schema import connect, initialize_database
from question_bank.services.duplicate_analysis_copy_service import exact_question_key, ensure_content_index
from question_bank.services.rich_content_service import save_question_rich_content
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.question_read_service import QuestionBankReadService, QuestionReadFilters
from question_bank.parsers.type_detector import subq_mark_labels
from question_bank.training_criteria import ConfigQuestionAnalysisSource, InMemoryCombinedQuestionAnalysisModule
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
from tests.test_question_import_duplicates import _fake_extract, _import_paper, _seed_analysis
from question_bank.importers import batch_importer


def formula(toggle="1", degree="", style=""):
    return {"question_blocks": [{"xml": '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:oMath><m:rad><m:radPr><m:degHide m:val="' + toggle + '"/></m:radPr><m:deg>' + degree + '</m:deg><m:e><m:r>' + style + '<m:t>x</m:t></m:r></m:e></m:rad></m:oMath></w:p>'}]}


def test_identity_ignores_printing_changes_but_keeps_mathematical_changes(tmp_path):
    def key(text="求根式的值", number="1", rich=None, images=None):
        return exact_question_key({"question_text": text, "question_number": number,
                                   "image_paths": images or []}, data_root=tmp_path, rich_content=rich or {})
    assert key("1.（3分）计算5−2的值") == key("8.（5分）计算５－２的值", "8")
    assert key("计算5−2的值") != key("计算5+2的值")
    assert key("计算x²") != key("计算x2")
    assert key(rich=formula("1")) == key(rich=formula("on", style='<m:rPr><m:sty m:val="p"/></m:rPr>'))
    assert key(rich=formula("1")) != key(rich=formula("0", '<m:r><m:t>3</m:t></m:r>'))
    picture = Image.new("RGB", (64, 64), "white")
    ImageDraw.Draw(picture).line([(10, 10), (10, 50), (50, 50), (10, 10)], fill="black", width=2)
    picture.save(tmp_path / "base.png")
    picture.resize((128, 128), Image.Resampling.NEAREST).save(tmp_path / "scaled.png")
    ImageDraw.Draw(picture).text((25, 10), "3", fill="black")
    picture.save(tmp_path / "changed.png")
    assert key(images=[str(tmp_path / "base.png")]) == key(images=[str(tmp_path / "scaled.png")])
    assert key(images=[str(tmp_path / "base.png")]) != key(images=[str(tmp_path / "changed.png")])


def test_index_refreshes_formula_text_and_image_changes_without_decoding_unchanged_bank(tmp_path):
    db = tmp_path / "bank.db"; initialize_database(db)
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (32, 32), "white").save(image_path)
    import json
    with connect(db) as conn:
        qid = conn.execute("INSERT INTO questions(question_number,question_text,question_type,image_paths) VALUES ('1','计算根式','解答题',?)", (json.dumps([str(image_path)]),)).lastrowid
        ensure_content_index(conn, data_root=tmp_path)
    def indexed():
        with connect(db) as conn:
            ensure_content_index(conn, data_root=tmp_path)
            return conn.execute("SELECT content_key FROM question_content_index WHERE question_id=?", (qid,)).fetchone()[0]
    before = indexed()
    save_question_rich_content(qid, question_blocks=formula()["question_blocks"], root=tmp_path / "question_bank" / "rich_content")
    after_formula = indexed(); assert after_formula != before
    with patch("PIL.Image.open", side_effect=AssertionError("unchanged bank image decoded")):
        assert indexed() == after_formula
    picture = Image.new("RGB", (32, 32), "white"); ImageDraw.Draw(picture).line([(2, 2), (20, 20)], fill="black"); picture.save(image_path)
    after_image = indexed(); assert after_image != after_formula
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='计算另一个根式' WHERE id=?", (qid,))
    assert indexed() != after_image


def test_duplicate_missing_analysis_never_enters_model_and_stays_pending_on_retry(tmp_path):
    db = tmp_path / "bank.db"; initialize_database(db)
    with connect(db) as conn:
        qid = conn.execute("INSERT INTO questions(question_number,question_text,question_type) VALUES ('1','求两条边长并写出过程','解答题')").lastrowid
    question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load((qid,))[0]
    question = dataclasses.replace(question, tagging_context=dataclasses.replace(question.tagging_context, curriculum_volume_id="pep-7-up"))
    source = ConfigQuestionAnalysisSource("Q1", question)
    reused = _reused_analysis_items(db, data_root=tmp_path, sources=(source,), operation_id="synthetic-acceptance")
    module = InMemoryCombinedQuestionAnalysisModule(gateway=SimpleNamespace())
    with patch.object(module, "_run", side_effect=lambda **kwargs: kwargs):
        run = module.analyze(operation_id="synthetic-acceptance", curriculum_volume_id="pep-7-up", sources=(source,), reused_items=reused)
    assert run["selected_sources"] == ()
    assert run["base_requests"] == ()
    assert run["base_failures"][0].category == "duplicate_analysis_missing"
    bundle = module.analyze(operation_id="synthetic-acceptance", curriculum_volume_id="pep-7-up", sources=(source,), reused_items=reused)
    assert not bundle.requests and len(bundle.failures) == 1


def test_two_occurrences_import_and_host_restore_keep_original_numbers(tmp_path):
    db = tmp_path / "bank.db"; initialize_database(db)
    text = "1. 这是足够长的合成题目，请计算5减2的值。\n答案：\n1. 3"
    repeated = "1. 这是足够长的合成题目，请计算5减2的值。\n2. 这是足够长的合成题目，请计算5减2的值。\n答案：\n1. 3\n2. 3"
    with patch.object(batch_importer, "_extract_paper", _fake_extract(text)):
        first = _import_paper(tmp_path, db, tmp_path, name="synthetic-A.docx", text=text)
    with patch.object(batch_importer, "_extract_paper", _fake_extract(repeated)):
        second = _import_paper(tmp_path, db, tmp_path, name="synthetic-B.docx", text=repeated)
    assert second.question_count == 2 and second.exact_duplicate_count == 2
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 1
        version = conn.execute("SELECT updated_at FROM papers WHERE id=?", (first.paper_id,)).fetchone()[0]
    writer = QuestionBankWriteService(db, data_root=tmp_path)
    deleted = writer.set_paper_deleted(first.paper_id, expected_updated_at=version, deleted=True)
    writer.set_paper_deleted(first.paper_id, expected_updated_at=deleted.updated_at, deleted=False)
    reader = QuestionBankReadService(db, data_root=tmp_path)
    assert reader.list_questions(QuestionReadFilters(paper_ids=(first.paper_id,))).total == 1
    with connect(db) as conn:
        assert conn.execute("SELECT question_number FROM paper_question_occurrences WHERE paper_id=?", (first.paper_id,)).fetchone()[0] == "1"
        appearances = conn.execute("SELECT question_number FROM questions WHERE paper_id=? UNION ALL SELECT question_number FROM paper_question_occurrences WHERE paper_id=?", (second.paper_id, second.paper_id)).fetchall()
        assert sorted(row[0] for row in appearances) == ["1", "2"]


def test_spaced_formula_markers_are_not_subquestions():
    for value in ("求 f (1) 与 f (2)", "S_ (1) 和 S_ (2)", "图 (1) 与图 (2)"):
        assert subq_mark_labels(value) == ()
    assert subq_mark_labels("计算 f\n（1）求值；（2）证明") == ("1", "2")


def test_format_variant_reuses_actual_stored_analysis_without_model_requests(tmp_path, monkeypatch):
    from question_bank.current_knowledge import CurrentFineTermResolver
    # This synthetic evidence has no knowledge links; identity/reuse use real storage.
    monkeypatch.setattr(CurrentFineTermResolver, "from_active_database",
                        classmethod(lambda cls, path: SimpleNamespace()))
    db = tmp_path / "bank.db"; initialize_database(db)
    with connect(db) as conn:
        qid = conn.execute("INSERT INTO questions(question_number,question_text,question_type) VALUES ('1','计算50−8的值并写出过程','解答题')").lastrowid
    _seed_analysis(db, tmp_path, qid)
    question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load((qid,))[0]
    question = dataclasses.replace(question,
        tagging_context=dataclasses.replace(question.tagging_context,
            question_text="计算５０－８的值并写出过程", curriculum_volume_id="pep-7-up"))
    source = ConfigQuestionAnalysisSource("Q1", question)
    reused = _reused_analysis_items(db, data_root=tmp_path, sources=(source,), operation_id="synthetic-reuse")
    bundle = InMemoryCombinedQuestionAnalysisModule(gateway=SimpleNamespace()).analyze(
        operation_id="synthetic-reuse", curriculum_volume_id="pep-7-up", sources=(source,), reused_items=reused)
    assert len(bundle.items) == 1 and not bundle.failures and not bundle.requests
    assert bundle.items[0].solution_evidence.parts[0].canonical_answer == "42"


def test_content_revision_migration_preserves_old_index_and_occurrences(tmp_path):
    import shutil
    from pathlib import Path
    from update_tools.migrate_db import run_migrations
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "question_bank"
    old_manifest = tmp_path / "old_manifest"; old_manifest.mkdir()
    for source in migrations.glob("*.sql"):
        if source.name < "038":
            shutil.copy2(source, old_manifest / source.name)
    db = tmp_path / "synthetic_upgrade.db"
    first = run_migrations("question_bank", db_path=db, migrations_dir=old_manifest)
    assert not first.error
    with connect(db) as conn:
        paper = conn.execute("INSERT INTO papers(title) VALUES ('合成迁移验收')").lastrowid
        qid = conn.execute("INSERT INTO questions(question_number,question_text,question_type) VALUES ('1','合成迁移验收题','解答题')").lastrowid
        conn.execute("INSERT INTO question_content_index(question_id,content_key) VALUES (?, 'old-key')", (qid,))
        conn.execute("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES (?,?,'2')", (paper,qid))
    report = run_migrations("question_bank", db_path=db, migrations_dir=migrations)
    assert not report.error
    assert list((tmp_path / "backups").glob("*.db"))
    with connect(db) as conn:
        assert tuple(conn.execute("SELECT question_id,content_key,source_revision FROM question_content_index").fetchone()) == (qid,"old-key","")
        assert tuple(conn.execute("SELECT paper_id,question_id,question_number FROM paper_question_occurrences").fetchone()) == (paper,qid,"2")
        ensure_content_index(conn, data_root=tmp_path)
        assert conn.execute("SELECT source_revision FROM question_content_index").fetchone()[0]


def test_uncertain_resampled_diagram_stays_local_pending_without_model(tmp_path):
    import io
    import json
    from question_bank.training_criteria import QuestionAnalysisImage
    db = tmp_path / "bank.db"; initialize_database(db)
    picture = Image.new("RGB", (64,64), "white")
    ImageDraw.Draw(picture).line([(10,10),(10,50),(50,50),(10,10)],fill="black",width=2)
    image_path = tmp_path / "diagram.png"; picture.save(image_path)
    with connect(db) as conn:
        qid = conn.execute("INSERT INTO questions(question_number,question_text,question_type,has_images,image_paths) VALUES ('1','根据图中条件求阴影部分面积','解答题',1,?)", (json.dumps([str(image_path)]),)).lastrowid
    source_question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load((qid,))[0]
    output = io.BytesIO(); picture.resize((90,90),Image.Resampling.LANCZOS).save(output,format="JPEG",quality=80)
    question = dataclasses.replace(source_question,
        images=(QuestionAnalysisImage(role="question",mime_type="image/jpeg",content=output.getvalue()),),
        tagging_context=dataclasses.replace(source_question.tagging_context,curriculum_volume_id="pep-7-up"))
    source = ConfigQuestionAnalysisSource("Q1",question)
    reused = _reused_analysis_items(db,data_root=tmp_path,sources=(source,),operation_id="synthetic-diagram")
    bundle = InMemoryCombinedQuestionAnalysisModule(gateway=SimpleNamespace()).analyze(
        operation_id="synthetic-diagram",curriculum_volume_id="pep-7-up",sources=(source,),reused_items=reused)
    assert not bundle.items and not bundle.requests
    assert bundle.failures[0].category == "duplicate_content_uncertain"
