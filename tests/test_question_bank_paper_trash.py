from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import (
    PaperPermanentDeleteSelection,
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
    ("authoring_works", "questions", "source_question_id"),
    ("evidence_point_knowledge_links", "questions", "question_id"),
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
    ("question_error_patterns", "questions", "question_id"),
    ("question_fingerprints", "questions", "question_id"),
    ("question_frequency_cache", "questions", "question_id"),
    ("question_part_difficulty_features", "questions", "question_id"),
    ("question_previews", "questions", "question_id"),
    ("question_scope_summary", "questions", "question_id"),
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
        "INSERT INTO authoring_works (work_id, kind, source_question_id, source_snapshot_json) "
        "VALUES ('TEST-kept-authoring', 'adapt', ?, '{\"question\":\"TEST-frozen-source\"}')",
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
        INSERT INTO question_part_difficulty_features (
            question_id, part_id, features_json, formula_difficulty,
            formula_version, source_content_hash
        ) VALUES (?, 'part-1', '{}', 3.0, 'std-difficulty-v1', 'synthetic')
        """,
        (question_id,),
    )
    kept_question_id = int(
        conn.execute(
            "INSERT INTO questions (question_number, question_text) "
            "VALUES ('kept-outside-paper', '独立保留的合成题目')"
        ).lastrowid
    )
    other_deleted_id = int(
        conn.execute(
            "SELECT id FROM questions WHERE paper_id = ? AND id != ? ORDER BY id LIMIT 1",
            (paper_id, question_id),
        ).fetchone()[0]
    )
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
        writer.data_root / "question_bank" / "rich_content" / "question_1.json"
    )
    rich_content_path.parent.mkdir(parents=True, exist_ok=True)
    rich_content_path.write_text('{"type":"doc"}', encoding="utf-8")
    publication_asset_path = rich_content_path.with_suffix(".png")
    publication_asset_path.write_bytes(b"published-asset")

    impact = writer.preview_paper_permanent_delete(
        [PaperPermanentDeleteSelection(paper_id, version)]
    )
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
        result.removed_knowledge_graph_link_count == impact.knowledge_graph_link_count
    )
    assert result.deleted_file_count == impact.owned_file_count
    assert result.skipped_shared_file_count == impact.shared_file_count
    assert not source_path.exists()
    assert rich_content_path.read_text(encoding="utf-8") == '{"type":"doc"}'
    assert publication_asset_path.read_bytes() == b"published-asset"
    with connect(writer.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM questions WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT bank_question_id FROM training_task_items "
                "WHERE task_item_code = 'delete-snapshot-item'"
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM training_attempts "
                "WHERE task_item_code = 'delete-snapshot-item'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT bank_question_id FROM question_document_items "
                "WHERE operation_id = 'delete-document-publication'"
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT paper_id FROM question_document_publications "
                "WHERE operation_id = 'delete-document-publication'"
            ).fetchone()[0]
            is None
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM question_part_difficulty_features"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM question_duplicate_links").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM questions WHERE question_number = 'kept-outside-paper'"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        authoring = conn.execute(
            "SELECT source_question_id, source_snapshot_json FROM authoring_works "
            "WHERE work_id='TEST-kept-authoring'"
        ).fetchone()
        assert tuple(authoring) == (None, '{"question":"TEST-frozen-source"}')


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
        assert (
            conn.execute("SELECT COUNT(*) FROM paper_question_occurrences").fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM questions WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()[0]
            == 0
        )
