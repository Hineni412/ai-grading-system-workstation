from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import PaperMetadata
from question_bank.importers.types import ExtractedDocument
from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import (
    PaperPermanentDeleteConflict,
    PaperPermanentDeleteSelection,
    PaperPermanentDeleteStorageIncomplete,
    PaperStateConflict,
    QuestionBankWriteService,
)
from question_bank.taxonomy.governance import TaxonomyGovernance
from tests.current_knowledge_support import install_current_knowledge


_TAXONOMY_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


_CURRENT_PAPER_QUESTION_FK_CHILDREN = {
    ("grading_question_links", "questions", "bank_question_id"),
    ("paper_question_occurrences", "papers", "paper_id"),
    ("paper_question_occurrences", "questions", "question_id"),
    ("question_analysis_items", "questions", "question_id"),
    ("question_content_index", "questions", "question_id"),
    ("question_content_revisions", "questions", "question_id"),
    ("question_document_items", "questions", "bank_question_id"),
    ("question_document_publications", "papers", "paper_id"),
    ("question_duplicate_links", "questions", "question_id"),
    ("question_duplicate_links", "questions", "duplicate_of_question_id"),
    ("question_fingerprints", "questions", "question_id"),
    ("question_frequency_cache", "questions", "question_id"),
    ("question_part_assessment_profiles", "questions", "question_id"),
    ("question_previews", "questions", "question_id"),
    ("question_solution_evidence_versions", "questions", "question_id"),
    ("question_tags", "questions", "question_id"),
    ("questions", "papers", "paper_id"),
    ("taxonomy_review_applications", "questions", "question_id"),
    ("training_criterion_backfill_items", "questions", "question_id"),
    ("training_criterion_events", "questions", "question_id"),
    ("training_criterion_heads", "questions", "question_id"),
    ("training_criterion_versions", "questions", "question_id"),
    ("training_set_items", "questions", "question_id"),
    ("training_task_items", "questions", "bank_question_id"),
}


def _paper_question_fk_children(conn) -> set[tuple[str, str, str]]:
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    ]
    return {
        (table, str(foreign_key[2]), str(foreign_key[3]))
        for table in tables
        for foreign_key in conn.execute(
            f'PRAGMA foreign_key_list("{table}")'
        ).fetchall()
        if str(foreign_key[2]) in {"papers", "questions"}
    }


def _seed_complete_analysis_dependencies(
    conn,
    *,
    paper_id: int,
    question_id: int,
) -> None:
    tag_id = int(
        conn.execute(
            "SELECT id FROM question_tags WHERE question_id = ? ORDER BY id LIMIT 1",
            (question_id,),
        ).fetchone()[0]
    )
    stable_key = str(
        conn.execute(
            "SELECT stable_key FROM knowledge_tag_identities ORDER BY stable_key LIMIT 1"
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO knowledge_tag_identity_mappings (
            question_tag_id, stable_key, source_value_snapshot
        ) VALUES (?, ?, '全等三角形')
        """,
        (tag_id, stable_key),
    )
    conn.execute(
        """
        INSERT INTO question_fingerprints (
            question_id, base_fingerprint
        ) VALUES (?, 'synthetic-fingerprint')
        """,
        (question_id,),
    )
    conn.execute(
        "INSERT INTO question_frequency_cache (question_id) VALUES (?)",
        (question_id,),
    )
    conn.execute(
        """
        INSERT INTO question_previews (
            question_id, preview_type, source_file, image_path
        ) VALUES (?, 'image', 'question_bank/raw_papers/source.docx',
                  'question_bank/previews/q1.png')
        """,
        (question_id,),
    )
    conn.execute(
        """
        INSERT INTO grading_question_links (
            grading_session_id, source_question_id, bank_question_id,
            link_method, status
        ) VALUES ('synthetic-session', 'Q1', ?, 'manual', 'confirmed')
        """,
        (question_id,),
    )

    training_set_id = int(
        conn.execute(
            "INSERT INTO training_sets (name) VALUES ('完整分析测试训练集')"
        ).lastrowid
    )
    conn.execute(
        """
        INSERT INTO training_set_items (training_set_id, question_id)
        VALUES (?, ?)
        """,
        (training_set_id, question_id),
    )
    task_id = int(
        conn.execute(
            "INSERT INTO training_tasks (task_code) VALUES ('delete-snapshot-task')"
        ).lastrowid
    )
    variant_id = int(
        conn.execute(
            """
            INSERT INTO training_variants (
                task_id, variant_key, variant_type
            ) VALUES (?, 'student-variant', 'individual')
            """,
            (task_id,),
        ).lastrowid
    )
    conn.execute(
        """
        INSERT INTO training_task_items (
            variant_id, task_item_code, bank_question_id, stage,
            question_snapshot_json
        ) VALUES (?, 'delete-snapshot-item', ?, 'direct', '{"question":"snapshot"}')
        """,
        (variant_id, question_id),
    )
    conn.execute(
        """
        INSERT INTO training_attempts (task_item_code, student_id)
        VALUES ('delete-snapshot-item', 'synthetic-student')
        """
    )

    analysis_operation = "delete-complete-analysis"
    conn.execute(
        """
        INSERT INTO question_analysis_operations (
            operation_id, input_fingerprint, contract_version,
            requested_projection, status
        ) VALUES (?, ?, 'combined-v2', 'both', 'succeeded')
        """,
        (analysis_operation, "1" * 64),
    )
    conn.execute(
        """
        INSERT INTO question_analysis_items (
            operation_id, question_id, source_content_hash,
            tag_status, criteria_status, tag_payload_hash,
            criteria_payload_json
        ) VALUES (?, ?, ?, 'succeeded', 'succeeded', ?, '{}')
        """,
        (analysis_operation, question_id, "2" * 64, "3" * 64),
    )
    conn.execute(
        """
        INSERT INTO question_analysis_requests (
            request_id, operation_id, projection, batch_hash,
            question_ids_json, status
        ) VALUES (?, ?, 'both', ?, ?, 'succeeded')
        """,
        ("4" * 64, analysis_operation, "5" * 64, f"[{question_id}]"),
    )

    criterion_version_id = "6" * 64
    conn.execute(
        """
        INSERT INTO training_criterion_versions (
            version_id, question_id, version_number, source_content_hash,
            schema_version, status, source_kind, source_reference,
            criteria_json, criteria_hash, quality_status, created_by,
            decision_by, decided_at
        ) VALUES (?, ?, 1, ?, 'training-criteria-draft-v1', 'approved',
                  'combined_model', 'synthetic-analysis', '{}', ?, 'passed',
                  'test', 'teacher', '2026-08-09 10:00:00')
        """,
        (criterion_version_id, question_id, "7" * 64, "8" * 64),
    )
    conn.execute(
        """
        INSERT INTO training_criterion_heads (
            question_id, current_version_id, approved_version_id,
            current_source_hash
        ) VALUES (?, ?, ?, ?)
        """,
        (question_id, criterion_version_id, criterion_version_id, "7" * 64),
    )
    conn.execute(
        """
        INSERT INTO training_criterion_events (
            question_id, version_id, event_type, actor_ref, reason,
            to_status, resulting_revision
        ) VALUES (?, ?, 'approved', 'teacher', 'synthetic approval',
                  'approved', 1)
        """,
        (question_id, criterion_version_id),
    )
    backfill_run_id = "9" * 64
    conn.execute(
        """
        INSERT INTO training_criterion_backfill_runs (
            run_id, request_token, input_fingerprint, question_ids_json,
            status
        ) VALUES (?, ?, ?, ?, 'succeeded')
        """,
        (backfill_run_id, "a" * 32, "b" * 64, f"[{question_id}]"),
    )
    conn.execute(
        """
        INSERT INTO training_criterion_backfill_items (
            run_id, question_id, status, version_id, attempt_count
        ) VALUES (?, ?, 'succeeded', ?, 1)
        """,
        (backfill_run_id, question_id, criterion_version_id),
    )

    publication_operation = "delete-document-publication"
    conn.execute(
        """
        INSERT INTO question_document_publications (
            operation_id, source_revision, snapshot_revision, state,
            manifest_sha256, paper_id, question_ids_json
        ) VALUES (?, ?, ?, 'published', ?, ?, ?)
        """,
        (
            publication_operation,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            paper_id,
            f"[{question_id}]",
        ),
    )
    conn.execute(
        """
        INSERT INTO question_document_items (
            operation_id, source_question_id, content_revision,
            bank_question_id, rich_content_path, asset_paths_json, state
        ) VALUES (?, 'Q1', ?, ?,
                  'question_bank/rich_content/question_1.json',
                  '["question_bank/rich_content/question_1.png"]',
                  'published')
        """,
        (publication_operation, "f" * 64, question_id),
    )
    conn.execute(
        """
        INSERT INTO question_content_revisions (
            question_id, content_revision, source_revision,
            source_regions_json, rich_content_path
        ) VALUES (?, ?, ?, '[]',
                  'question_bank/rich_content/question_1.json')
        """,
        (question_id, "f" * 64, "c" * 64),
    )
    conn.execute(
        """
        INSERT INTO question_document_publication_outbox (
            outbox_id, operation_id, payload_sha256, payload_json,
            status
        ) VALUES (?, ?, ?, '{}', 'delivered')
        """,
        ("0" * 64, publication_operation, "1" * 64),
    )
    conn.execute(
        """
        INSERT INTO taxonomy_review_applications (
            operation_id, proposal_id, question_id, question_tag_id,
            tag_type, tag_value, write_status, undo_status
        ) VALUES ('delete-taxonomy-operation', 'proposal-1', ?, ?,
                  'knowledge_point', '全等三角形', 'written', 'available')
        """,
        (question_id, tag_id),
    )
    conn.execute(
        """
        INSERT INTO question_solution_evidence_versions (
            evidence_version_id, question_id, source_content_hash,
            schema_version, content_hash, evidence_json, status,
            source_kind, source_reference, created_by,
            decision_by, decided_at
        ) VALUES (?, ?, ?, 'question-solution-evidence-v2', ?, '{}',
                  'approved', 'combined_model', 'synthetic-analysis', 'test',
                  'teacher', '2026-08-09 10:00:00')
        """,
        ("2" * 64, question_id, "3" * 64, "4" * 64),
    )
    conn.execute(
        """
        INSERT INTO question_part_assessment_profiles (
            question_id, evidence_version_id, current_source_content_hash,
            parts_json, revision, created_by
        ) VALUES (?, ?, ?, '[]', 1, 'test')
        """,
        (question_id, "2" * 64, "3" * 64),
    )
    kept_question_id = int(conn.execute(
        "INSERT INTO questions (question_number, question_text) "
        "VALUES ('kept-outside-paper', '独立保留的合成题目')"
    ).lastrowid)
    other_deleted_id = int(conn.execute(
        "SELECT id FROM questions WHERE paper_id = ? AND id != ? ORDER BY id LIMIT 1",
        (paper_id, question_id),
    ).fetchone()[0])
    # Cover both foreign-key directions with the other endpoint outside the paper.
    conn.executemany(
        "INSERT INTO question_duplicate_links "
        "(question_id, duplicate_of_question_id, signature) VALUES (?, ?, 'synthetic')",
        [(question_id, kept_question_id), (kept_question_id, other_deleted_id)],
    )
    conn.execute(
        """
        INSERT INTO question_content_index (question_id, content_key)
        VALUES (?, 'synthetic-content-key')
        """,
        (question_id,),
    )
    conn.execute(
        """
        INSERT INTO paper_question_occurrences (
            paper_id, question_id, question_number, signature
        ) VALUES (?, ?, '9', 'synthetic-content-key')
        """,
        (paper_id, question_id),
    )


def _seed_paper(
    tmp_path: Path,
) -> tuple[QuestionBankWriteService, QuestionBankReadService, int, str, Path]:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    install_current_knowledge(db_path)
    source_path = data_root / "question_bank" / "raw_papers" / "source.docx"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"source-stays")
    with connect(db_path) as conn:
        paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, updated_at
                ) VALUES (?, ?, 'completed', ?)
                """,
                ("测试卷", str(source_path), "2026-07-29 12:00:00.000000"),
            ).lastrowid
        )
        first_id = int(
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text, is_deleted
                ) VALUES (?, '1', '第一题', 0)
                """,
                (paper_id,),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_text, is_deleted
            ) VALUES (?, '2', '第二题', 0)
            """,
            (paper_id,),
        )
        previously_deleted_id = int(
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text,
                    is_deleted, deleted_at
                ) VALUES (?, '3', '此前单独删除的题', 1, ?)
                """,
                (paper_id, "2026-07-28 09:00:00"),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', '全等三角形', 'manual')
            """,
            (first_id,),
        )
    return (
        QuestionBankWriteService(db_path, data_root=data_root),
        QuestionBankReadService(db_path, data_root=data_root),
        paper_id,
        "2026-07-29 12:00:00.000000",
        source_path,
    )


def test_restoring_a_trashed_paper_only_restores_questions_moved_with_it(
    tmp_path: Path,
) -> None:
    writer, reader, paper_id, version, source_path = _seed_paper(tmp_path)
    original_tags = reader.get_question(1)["tags"]

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert trashed.deleted is True
    assert trashed.affected_question_count == 2
    assert restored.deleted is False
    assert restored.affected_question_count == 2
    assert reader.get_question(1)["question_text"] == "第一题"
    assert reader.get_question(1)["tags"] == original_tags
    assert reader.get_question(3) is None
    assert source_path.read_bytes() == b"source-stays"


def test_import_collision_ignores_trashed_original_and_dedupes_new_active_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    fingerprint = batch_importer._file_fingerprint(source_path)
    with connect(writer.db_path) as conn:
        conn.execute(
            "UPDATE papers SET content_fingerprint = ? WHERE id = ?",
            (fingerprint, paper_id),
        )
    upload_copy = tmp_path / "same-paper.docx"
    upload_copy.write_bytes(source_path.read_bytes())
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 回收站旧卷不阻止全新入库",
        ),
    )

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    collision = batch_importer._import_scanned_paper(
        upload_copy,
        writer.db_path,
        stored_source_file="question_bank/raw_papers/same-paper.docx",
        source_title="same-paper",
        metadata=PaperMetadata(),
        question_range=None,
    )
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("active fingerprint collision should skip parsing")
        ),
    )
    active_collision = batch_importer._import_scanned_paper(
        upload_copy,
        writer.db_path,
        stored_source_file="question_bank/raw_papers/same-paper.docx",
        source_title="same-paper",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert collision.status == "needs_review"
    assert collision.paper_id != paper_id
    assert active_collision.status == "duplicate"
    assert active_collision.paper_id == collision.paper_id
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT import_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()[0] == "deleted"


def test_reupload_after_direct_permanent_delete_creates_a_new_independent_paper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    upload_copy = tmp_path / "same-paper-again.docx"
    upload_copy.write_bytes(source_path.read_bytes())
    writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, version)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="9" * 32,
    )
    monkeypatch.setattr(
        batch_importer,
        "_extract_paper",
        lambda path, **_kwargs: ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text="1. 删除后重新上传会生成新的题库记录",
        ),
    )

    imported = batch_importer._import_scanned_paper(
        upload_copy,
        writer.db_path,
        stored_source_file="question_bank/raw_papers/same-paper-again.docx",
        source_title="same-paper-again",
        metadata=PaperMetadata(),
        question_range=None,
    )

    assert imported.status == "needs_review"
    assert imported.paper_id != paper_id
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?", (imported.paper_id,)
        ).fetchone()[0] == 1


def test_permanent_delete_removes_owned_file_tags_and_statistic_links(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    with connect(writer.db_path) as conn:
        question_id = int(conn.execute(
            "SELECT id FROM questions WHERE paper_id = ? ORDER BY id LIMIT 1",
            (paper_id,),
        ).fetchone()[0])
        conn.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, status
            ) VALUES ('7', 'Q1', ?, 'manual', 'confirmed')
            """,
            (question_id,),
        )
        training_set_id = int(conn.execute(
            "INSERT INTO training_sets (name) VALUES ('临时训练')"
        ).lastrowid)
        conn.execute(
            """
            INSERT INTO training_set_items (training_set_id, question_id)
            VALUES (?, ?)
            """,
            (training_set_id, question_id),
        )

    impact = writer.preview_paper_permanent_delete([
        PaperPermanentDeleteSelection(paper_id, trashed.updated_at)
    ])
    result = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="a" * 32,
    )
    repeated = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="a" * 32,
    )

    assert impact.tag_count == 1
    assert impact.training_link_count == 1
    assert impact.knowledge_graph_link_count == 1
    assert result.deleted_paper_ids == (paper_id,)
    assert repeated == result
    assert not source_path.exists()
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM grading_question_links"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM training_set_items"
        ).fetchone()[0] == 0

    with pytest.raises(PaperPermanentDeleteConflict):
        writer.permanently_delete_papers(
            [
                PaperPermanentDeleteSelection(
                    paper_id,
                    trashed.updated_at + "-changed",
                )
            ],
            confirmation_phrase="彻底删除 1 份试卷",
            request_token="a" * 32,
        )


def test_permanent_delete_handles_every_current_fk_child_of_a_complete_analysis(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        question_id = int(
            conn.execute(
                "SELECT id FROM questions WHERE paper_id = ? ORDER BY id LIMIT 1",
                (paper_id,),
            ).fetchone()[0]
        )
        assert _paper_question_fk_children(conn) == (
            _CURRENT_PAPER_QUESTION_FK_CHILDREN
        )
        _seed_complete_analysis_dependencies(
            conn,
            paper_id=paper_id,
            question_id=question_id,
        )
    rich_content_path = (
        writer.data_root
        / "question_bank"
        / "rich_content"
        / "question_1.json"
    )
    rich_content_path.parent.mkdir(parents=True, exist_ok=True)
    rich_content_path.write_text('{"type":"doc"}', encoding="utf-8")
    publication_asset_path = rich_content_path.with_suffix(".png")
    publication_asset_path.write_bytes(b"published-asset")

    impact = writer.preview_paper_permanent_delete([
        PaperPermanentDeleteSelection(paper_id, version)
    ])
    result = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, version)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="f" * 32,
    )

    assert impact.question_count == 3
    assert impact.analysis_record_count == 6
    assert impact.tag_count == 1
    assert impact.training_link_count == 2
    assert impact.knowledge_graph_link_count == 1
    assert impact.owned_file_count == 1
    assert impact.shared_file_count == 1
    assert result.deleted_paper_ids == (paper_id,)
    assert result.deleted_question_count == impact.question_count
    assert result.deleted_tag_count == impact.tag_count
    assert result.deleted_analysis_record_count == impact.analysis_record_count
    assert result.removed_training_link_count == impact.training_link_count
    assert (
        result.removed_knowledge_graph_link_count
        == impact.knowledge_graph_link_count
    )
    assert result.deleted_file_count == impact.owned_file_count
    assert result.skipped_shared_file_count == impact.shared_file_count
    assert not source_path.exists()
    assert rich_content_path.read_text(encoding="utf-8") == '{"type":"doc"}'
    assert publication_asset_path.read_bytes() == b"published-asset"
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM questions WHERE paper_id = ?", (paper_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT bank_question_id FROM training_task_items "
            "WHERE task_item_code = 'delete-snapshot-item'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM training_attempts "
            "WHERE task_item_code = 'delete-snapshot-item'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT bank_question_id FROM question_document_items "
            "WHERE operation_id = 'delete-document-publication'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT paper_id FROM question_document_publications "
            "WHERE operation_id = 'delete-document-publication'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM question_part_assessment_profiles"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM question_duplicate_links"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM questions WHERE question_number = 'kept-outside-paper'"
        ).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_pending_delete_recovery_rejects_absolute_manifest_paths(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, _source_path = _seed_paper(tmp_path)
    operation = (
        writer.data_root
        / "question_bank"
        / ".paper-delete-staging"
        / ("e" * 32)
    )
    staged = operation / "00000" / "outside.txt"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"untrusted replacement")
    outside = tmp_path / "must-not-be-overwritten.txt"
    outside.write_bytes(b"safe")
    (operation / "manifest.json").write_text(
        json.dumps({
            "paper_ids": [paper_id],
            "entries": [{
                "source": str(outside.resolve()),
                "staged": str(staged.resolve()),
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(PaperPermanentDeleteStorageIncomplete):
        writer.preview_paper_permanent_delete([
            PaperPermanentDeleteSelection(paper_id, version)
        ])

    assert outside.read_bytes() == b"safe"
    assert staged.read_bytes() == b"untrusted replacement"


def test_pending_delete_recovery_rejects_non_asset_paths_inside_data_root(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, _source_path = _seed_paper(tmp_path)
    operation = (
        writer.data_root
        / "question_bank"
        / ".paper-delete-staging"
        / ("c" * 32)
    )
    staged = operation / "00000" / "unexpected.db"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"must-not-be-restored")
    forbidden_source = writer.data_root / "databases" / "unexpected.db"
    (operation / "manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "paper_ids": [paper_id],
            "entries": [{
                "source_relative": "databases/unexpected.db",
                "staged_relative": "00000/unexpected.db",
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(PaperPermanentDeleteStorageIncomplete):
        writer.preview_paper_permanent_delete([
            PaperPermanentDeleteSelection(paper_id, version)
        ])

    assert not forbidden_source.exists()
    assert staged.read_bytes() == b"must-not-be-restored"


def test_permanent_delete_never_treats_the_database_as_a_paper_file(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, _source_path = _seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        conn.execute(
            "UPDATE papers SET source_file = ? WHERE id = ?",
            (str(writer.db_path), paper_id),
        )

    impact = writer.preview_paper_permanent_delete([
        PaperPermanentDeleteSelection(paper_id, version)
    ])

    assert impact.owned_file_count == 0
    assert impact.shared_file_count == 0
    with connect(writer.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()[0] == 1


def test_pending_delete_recovery_accepts_controlled_relative_manifest_paths(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, source_path = _seed_paper(tmp_path)
    operation = (
        writer.data_root
        / "question_bank"
        / ".paper-delete-staging"
        / ("d" * 32)
    )
    staged = operation / "00000" / source_path.name
    staged.parent.mkdir(parents=True)
    source_path.replace(staged)
    (operation / "manifest.json").write_text(
        json.dumps({
            "manifest_version": 1,
            "paper_ids": [paper_id],
            "entries": [{
                "source_relative": "question_bank/raw_papers/source.docx",
                "staged_relative": "00000/source.docx",
            }],
        }),
        encoding="utf-8",
    )

    impact = writer.preview_paper_permanent_delete([
        PaperPermanentDeleteSelection(paper_id, version)
    ])

    assert impact.paper_count == 1
    assert source_path.read_bytes() == b"source-stays"
    assert not operation.exists()


def test_paper_state_change_rejects_the_opposite_action_from_a_stale_card(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)
    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )

    with pytest.raises(PaperStateConflict) as caught:
        writer.set_paper_deleted(
            paper_id,
            expected_updated_at=version,
            deleted=False,
        )

    assert caught.value.current_updated_at == trashed.updated_at
    assert caught.value.deleted is True


def test_repeated_paper_state_request_is_idempotent_and_returns_real_status(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    repeated_trash = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )
    repeated_restore = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert trashed.import_status == "deleted"
    assert repeated_trash == trashed
    assert restored.import_status == "completed"
    assert repeated_restore.deleted is False
    assert repeated_restore.import_status == "completed"
    assert repeated_restore.updated_at == restored.updated_at


def test_restore_falls_back_to_needs_review_when_old_status_is_blank(
    tmp_path: Path,
) -> None:
    writer, _, paper_id, version, _ = _seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        conn.execute(
            "UPDATE papers SET import_status = '' WHERE id = ?",
            (paper_id,),
        )

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    restored = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=trashed.updated_at,
        deleted=False,
    )

    assert restored.import_status == "needs_review"
    with connect(writer.db_path) as conn:
        row = conn.execute(
            "SELECT import_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
    assert row is not None
    assert row["import_status"] == "needs_review"


def _taxonomy_governance_for_test(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=_TAXONOMY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "not-created-question-bank.db",
    )


def _propose_new_term(
    governance: TaxonomyGovernance,
    *,
    name: str,
    question_id: int,
    token: str,
) -> None:
    governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "model",
                    "name": name,
                    "definition": f"{name}的定义",
                    "reason": "现有候选词不能准确表达",
                    "nearest_id": "",
                    "why_not_reuse": "语义边界不同",
                }
            ],
        },
        context={
            "persist_proposals": True,
            "question_id": question_id,
            "model": "paper-delete-test-model",
            "request_token": token,
        },
    )


def _proposal_by_name(
    governance: TaxonomyGovernance,
    name: str,
) -> dict | None:
    state = governance._read_state()
    return next(
        (
            proposal
            for proposal in state["proposals"]
            if proposal["proposed_name"] == name
        ),
        None,
    )


def test_permanent_delete_prunes_orphan_pending_taxonomy_proposals(
    tmp_path: Path,
) -> None:
    writer, _reader, paper_id, version, _source_path = _seed_paper(tmp_path)
    governance = _taxonomy_governance_for_test(tmp_path)
    writer = QuestionBankWriteService(
        writer.db_path,
        data_root=writer.data_root,
        taxonomy_governance=governance,
    )
    with connect(writer.db_path) as conn:
        deleted_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM questions "
                "WHERE paper_id = ? AND COALESCE(is_deleted, 0) = 0 ORDER BY id",
                (paper_id,),
            ).fetchall()
        ]
        paper_question_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM questions WHERE paper_id = ? ORDER BY id",
                (paper_id,),
            ).fetchall()
        ]
        other_paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, updated_at
                ) VALUES ('保留的试卷', 'kept.docx', 'completed',
                          '2026-07-29 12:00:00.000000')
                """
            ).lastrowid
        )
        other_question_id = int(
            conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_text, is_deleted
                ) VALUES (?, '1', '保留卷第一题', 0)
                """,
                (other_paper_id,),
            ).lastrowid
        )
    deleted_q1, deleted_q2 = deleted_ids

    # 仅引用被删题的待审提案（应整条移除）
    _propose_new_term(
        governance, name="删除联动孤儿词", question_id=deleted_q1,
        token="prune-orphan-1",
    )
    _propose_new_term(
        governance, name="删除联动孤儿词", question_id=deleted_q2,
        token="prune-orphan-2",
    )
    # 混合引用：被删题 + 保留题（应只修剪引用）
    _propose_new_term(
        governance, name="删除联动混合词", question_id=deleted_q1,
        token="prune-mixed-1",
    )
    _propose_new_term(
        governance, name="删除联动混合词", question_id=other_question_id,
        token="prune-mixed-2",
    )
    # 已被拒的提案是词决策历史（应保留，只修剪引用）
    _propose_new_term(
        governance, name="删除联动被拒词", question_id=deleted_q1,
        token="prune-rejected-1",
    )
    rejected = _proposal_by_name(governance, "删除联动被拒词")
    assert rejected is not None
    governance.review_proposal(
        proposal_id=rejected["id"],
        decision="reject",
        expected_revision=governance._read_state()["revision"],
        request_token="prune-reject-review",
    )
    # 已被合并的提案同样保留
    _propose_new_term(
        governance, name="删除联动被并词", question_id=deleted_q1,
        token="prune-merged-1",
    )
    merged = _proposal_by_name(governance, "删除联动被并词")
    assert merged is not None
    model_terms = governance.snapshot()["terms_by_dimension"]["model"]
    governance.review_proposal(
        proposal_id=merged["id"],
        decision="merge",
        target_term_id=model_terms[0]["id"],
        expected_revision=governance._read_state()["revision"],
        request_token="prune-merge-review",
    )
    approved_before = governance._read_state()["approved_terms"]

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    selections = [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)]

    impact = writer.preview_paper_permanent_delete(selections)
    result = writer.permanently_delete_papers(
        selections,
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="d" * 32,
    )
    repeated = writer.permanently_delete_papers(
        selections,
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="d" * 32,
    )

    assert impact.taxonomy_proposal_count == 1
    assert repeated == result

    assert _proposal_by_name(governance, "删除联动孤儿词") is None

    mixed = _proposal_by_name(governance, "删除联动混合词")
    assert mixed is not None
    assert mixed["status"] == "pending"
    assert mixed["question_refs"] == [str(other_question_id)]
    assert mixed["occurrences"] == 1

    rejected_after = _proposal_by_name(governance, "删除联动被拒词")
    assert rejected_after is not None
    assert rejected_after["status"] == "rejected"
    assert rejected_after["question_refs"] == []
    assert rejected_after["occurrences"] == 1

    merged_after = _proposal_by_name(governance, "删除联动被并词")
    assert merged_after is not None
    assert merged_after["status"] == "merged"
    assert merged_after["question_refs"] == []

    assert governance._read_state()["approved_terms"] == approved_before

    # 同一 token 重放修剪：返回已记录的结果，不重复移除或计数
    revision_before_replay = governance._read_state()["revision"]
    replayed = governance.prune_proposals_for_deleted_questions(
        paper_question_ids,
        question_exists=writer._live_question_exists,
        request_token="d" * 32,
    )
    assert replayed["removed_pending_proposals"] == 1
    assert _proposal_by_name(governance, "删除联动孤儿词") is None
    assert governance._read_state()["revision"] == revision_before_replay

    # 重放后再次预览式 dry-run：已无可清理的孤儿待审提案
    follow_up = governance.prune_proposals_for_deleted_questions(
        paper_question_ids,
        question_exists=writer._live_question_exists,
        dry_run=True,
    )
    assert follow_up["removed_pending_proposals"] == 0


def test_paper_delete_rehomes_canonical_question_to_surviving_paper(
    tmp_path: Path,
) -> None:
    """A canonical question referenced by another paper is never deleted."""
    writer, _reader, paper_id, version, _source_path = _seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        canonical_id = int(
            conn.execute(
                "SELECT id FROM questions WHERE paper_id = ? ORDER BY id LIMIT 1",
                (paper_id,),
            ).fetchone()[0]
        )
        surviving_paper_id = int(
            conn.execute(
                """
                INSERT INTO papers (
                    title, source_file, import_status, updated_at
                ) VALUES ('留存卷', 'other-source.docx', 'completed', ?)
                """,
                ("2026-07-30 12:00:00.000000",),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO paper_question_occurrences (
                paper_id, question_id, question_number, signature
            ) VALUES (?, ?, '7', 'shared-content-key')
            """,
            (surviving_paper_id, canonical_id),
        )

    trashed = writer.set_paper_deleted(
        paper_id,
        expected_updated_at=version,
        deleted=True,
    )
    with connect(writer.db_path) as conn:
        row = conn.execute(
            "SELECT paper_id, question_number, is_deleted FROM questions WHERE id = ?",
            (canonical_id,),
        ).fetchone()
        assert int(row["paper_id"]) == surviving_paper_id
        assert str(row["question_number"]) == "7"
        assert int(row["is_deleted"] or 0) == 0

    result = writer.permanently_delete_papers(
        [PaperPermanentDeleteSelection(paper_id, trashed.updated_at)],
        confirmation_phrase="彻底删除 1 份试卷",
        request_token="b" * 32,
    )
    assert result.deleted_paper_ids == (paper_id,)
    with connect(writer.db_path) as conn:
        row = conn.execute(
            "SELECT paper_id, question_number FROM questions WHERE id = ?",
            (canonical_id,),
        ).fetchone()
        assert row is not None, "canonical question must survive its host paper"
        assert int(row["paper_id"]) == surviving_paper_id
        assert str(row["question_number"]) == "7"
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_question_occurrences"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM questions WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()[0] == 0
