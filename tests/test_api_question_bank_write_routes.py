from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from question_bank.database.schema import initialize_database
from question_bank.services.question_write_service import ConfirmedQuestionTag, QuestionBankWriteService, QuestionWriteConflict

from question_bank.database.schema import connect
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import PaperPermanentDeleteSelection
from tests.current_knowledge_support import install_current_knowledge


from question_bank.importers import batch_importer
from question_bank.importers.batch_importer import ScannedPaper
from question_bank.importers.types import ExtractedDocument


@pytest.fixture
def write_seed(
    tmp_path: Path,
) -> tuple[QuestionBankWriteService, int, str]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, import_status)
            VALUES (1, 'Paper', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_text
            ) VALUES (1, 1, '1', 'Question')
            """
        )
        conn.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source, model_name
            ) VALUES (1, 'knowledge_point', '旧标签', 0.7, 'ai', 'model-x')
            """
        )
        conn.commit()
    service = QuestionBankWriteService(
        db_path,
        data_root=tmp_path / "data",
    )
    return service, 1, service.get_revision(1)


def _load_tags(db_path: Path, question_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT tag_type, tag_value, confidence, source, model_name
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id
            """,
            (question_id,),
        ).fetchall()
    finally:
        conn.close()


def test_replace_tags_conflicts_instead_of_overwriting_newer_state(
    write_seed,
) -> None:
    service, question_id, revision = write_seed
    service.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[ConfirmedQuestionTag("knowledge_point", "一次函数", 1.0)],
    )

    with pytest.raises(QuestionWriteConflict) as caught:
        service.replace_tags(
            question_id,
            expected_revision=revision,
            tags=[ConfirmedQuestionTag("knowledge_point", "二次函数", 1.0)],
        )

    assert caught.value.current_revision != revision
    assert _load_tags(service.db_path, question_id)[0]["tag_value"] == "一次函数"


def _write_client(service: QuestionBankWriteService) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import get_question_bank_write_service

    app = create_app()
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app)


def test_question_delete_and_restore_routes(write_seed) -> None:
    service, question_id, revision = write_seed
    client = _write_client(service)

    deleted = client.request(
        "DELETE",
        f"/api/question-bank/questions/{question_id}",
        json={"expected_revision": revision},
    )
    restored = client.post(
        f"/api/question-bank/questions/{question_id}/restore",
        json={"expected_revision": deleted.json()["revision"]},
    )

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False


def test_teacher_type_edit_updates_all_current_point_copies_and_keeps_other_links(write_seed):
    import json
    from tests.current_knowledge_support import install_current_knowledge
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.question_types import is_type_key
    from question_bank.database.schema import connect
    from question_bank.services.question_write_service import sync_question_type_labels
    from question_bank.solution_evidence.knowledge_links import load_point_links
    service, qid, _ = write_seed
    release_id = install_current_knowledge(service.db_path, taxonomy_revision=11)
    resolver = CurrentKnowledgeResolver.from_active_database(service.db_path)
    types = [node.stable_key for node in resolver.nodes if is_type_key(node.stable_key)][:2]
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    from question_bank.training_criteria import QuestionAnalysisInputLoader, solution_evidence_source_content_hash
    volume = curriculum_volume(volume_id='bnu24-math-g8-upper')
    with connect(service.db_path) as conn:
        conn.execute('UPDATE papers SET grade=?,semester=?,textbook_version=? WHERE id=1', (volume['grade'], volume['semester'], volume['textbook_version']))
    question = QuestionAnalysisInputLoader(db_path=service.db_path, data_root=service.data_root).load([qid])[0]
    source_hash = solution_evidence_source_content_hash(question)
    # A successful no-match survives reopening and unrelated standard releases.
    # It is reconsidered only when the question or offered type definitions change.
    from dataclasses import replace
    from types import SimpleNamespace
    from backend.jobs.question_bank_repair import _with_type_contracts, _current_type_review, repair_preview
    from question_bank.taxonomy.governance import TaxonomyGovernance
    from question_bank.training_criteria.adapters import ExistingTagProjectionWriter
    governance = TaxonomyGovernance(knowledge_graph_db_path=service.db_path)
    scoped_question = replace(question, tagging_context=replace(question.tagging_context, curriculum_volume_id=volume['id']))
    typed_question = _with_type_contracts([scoped_question], service.db_path, governance)[0]
    tag_writer = ExistingTagProjectionWriter(write_service=service, tagging_service=SimpleNamespace())
    no_match = {'primary_type_id': '', 'secondary_type_ids': [],
                'proposed_type_name': '', 'reason': 'TEST-当前候选不覆盖本题核心任务'}
    tag_writer.write_question_type_labels(typed_question, no_match, model_name='TEST-model', operation_id='TEST-no-match')
    with connect(service.db_path) as conn:
        saved = json.loads(conn.execute("SELECT tag_value FROM question_tags WHERE question_id=? AND tag_type='tag_status'", (qid,)).fetchone()[0])
        assert saved['reason'] == no_match['reason']
        assert saved['review_source'] == 'model_result'
        assert saved['operation_id'] == 'TEST-no-match'
        saved['release_id'] = 'TEST-previous-release-with-same-types'
        conn.execute("UPDATE question_tags SET tag_value=? WHERE question_id=? AND tag_type='tag_status'", (json.dumps(saved), qid))
    reopened = QuestionAnalysisInputLoader(db_path=service.db_path, data_root=service.data_root).load([qid], curriculum_volume_id=volume['id'])[0]
    reopened = _with_type_contracts([reopened], service.db_path, governance)[0]
    assert _current_type_review(json.dumps(saved), reopened)
    # An old unclassified marker is not evidence of a model no-match decision.
    legacy = {key: value for key, value in saved.items() if key not in {'reason', 'review_source', 'operation_id'}}
    assert not _current_type_review(json.dumps(legacy), reopened)
    assert not _current_type_review(json.dumps({**legacy, 'reason': 'TEST-未归类备注'}), reopened)
    assert not _current_type_review(json.dumps({**saved, 'reason': '  '}), reopened)
    with connect(service.db_path) as conn:
        conn.execute("UPDATE question_tags SET tag_value=? WHERE question_id=? AND tag_type='tag_status'", (json.dumps(legacy), qid))
    legacy_preview = repair_preview(QuestionBankReadService(service.db_path, data_root=service.data_root), volume['id'], 'all', [qid])
    assert legacy_preview['repairable_count'] == 1
    assert legacy_preview['items'][0]['type_pending_reason'] == ''
    assert legacy_preview['items'][0]['blocked_reason'] == ''
    with connect(service.db_path) as conn:
        conn.execute("UPDATE question_tags SET tag_value=? WHERE question_id=? AND tag_type='tag_status'", (json.dumps(saved), qid))
    preview = repair_preview(QuestionBankReadService(service.db_path, data_root=service.data_root), volume['id'], 'all')
    assert '暂无合适题型' in preview['items'][0]['type_pending_reason']
    # Missing analysis remains eligible while the type keeps its manual-review reason.
    assert preview['repairable_count'] == 1
    assert not preview['items'][0]['blocked_reason']
    changed_question = replace(reopened, tagging_context=replace(reopened.tagging_context, question_text='TEST-新的核心任务'))
    assert not _current_type_review(json.dumps(saved), changed_question)
    changed_contract = reopened.taxonomy_snapshot.to_dict()
    changed_type = next(row for row in changed_contract['candidates']['knowledge'] if is_type_key(row['id']))
    changed_type['definition'] += ' TEST-扩展适用范围'
    assert not _current_type_review(json.dumps(saved), replace(reopened, taxonomy_contract=changed_contract))
    evidence = {'parts': [{'part_id': 'part-1', 'evidence_points': [
        {'evidence_point_id': 'p1', 'target': '原判定点一'}, {'evidence_point_id': 'p2', 'target': '原判定点二'}]}]}
    with connect(service.db_path) as conn:
        conn.execute('INSERT INTO question_scope_summary(question_id,primary_section_id,direct_section_ids_json) VALUES(?,?,?)', (qid, types[0].rsplit('_t', 1)[0], '[]'))
        conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) VALUES(?,?,?,'question-solution-evidence-v2','cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',?,'approved','combined_model','TEST-type-edit','test',?)",
            ('a' * 64, qid, source_hash, json.dumps(evidence), release_id))
        for point in ('p1', 'p2'):
            conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,weight,source_kind) VALUES(?,?,'part-1',?,?,'direct','sk_TEST','sk_TEST','resolved',0.75,'link_job')", ('a' * 64, qid, point, release_id))
    revision = service.get_revision(qid)
    response = _write_client(service).put(f'/api/question-bank/questions/{qid}/question-types', json={
        'expected_revision': revision, 'primary_type_key': types[0], 'secondary_type_keys': [types[1]]})
    assert response.status_code == 200
    assert response.json()['revision'] != revision
    with connect(service.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE question_id=? AND tag_type='tag_status'", (qid,)).fetchone()[0] == 0
        links = load_point_links(service.db_path, ['a' * 64], release_id, connection=conn)['a' * 64]
        assert all({link.stable_key for link in point_links} == {'sk_TEST', types[0]} for point_links in links.values())
        assert all(next(link.weight for link in point_links if link.stable_key == 'sk_TEST') == pytest.approx(0.75 / 1.75) for point_links in links.values())
        assert json.loads(conn.execute('SELECT evidence_json FROM question_solution_evidence_versions').fetchone()[0]) == evidence
        assert conn.execute('SELECT COUNT(*) FROM question_solution_evidence_versions').fetchone()[0] == 1
        sync_question_type_labels(conn, qid, types[1], [], resolver, source='ai')
        assert conn.execute("SELECT tag_value FROM question_tags WHERE tag_type='knowledge_point' AND tag_value LIKE '%_t%' ").fetchone()[0] == types[0]
    conflict = _write_client(service).put(f'/api/question-bank/questions/{qid}/question-types', json={
        'expected_revision': revision, 'primary_type_key': types[1], 'secondary_type_keys': []})
    assert conflict.status_code == 409
    invalid = _write_client(service).put(f'/api/question-bank/questions/{qid}/question-types', json={
        'expected_revision': response.json()['revision'], 'primary_type_key': types[0], 'secondary_type_keys': [types[0]]})
    assert invalid.status_code == 422

    with connect(service.db_path) as conn:
        conn.execute("UPDATE question_solution_evidence_versions SET source_content_hash=? WHERE evidence_version_id=?", ('f' * 64, 'a' * 64))
        conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) SELECT ?,question_id,?,schema_version,content_hash,evidence_json,'proposed',source_kind,'TEST-current-proposed',created_by,graph_release_id FROM question_solution_evidence_versions WHERE evidence_version_id=?", ('d' * 64, source_hash, 'a' * 64))
        old = [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE evidence_version_id=?', ('a' * 64,))]
        old_versions = [tuple(row) for row in conn.execute('SELECT * FROM question_solution_evidence_versions')]
    updated = _write_client(service).put(f'/api/question-bank/questions/{qid}/question-types', json={
        'expected_revision': service.get_revision(qid), 'primary_type_key': types[1], 'secondary_type_keys': []})
    assert updated.status_code == 200, updated.text
    new_links = load_point_links(service.db_path, ['d' * 64], release_id)['d' * 64]
    assert all({link.stable_key for link in rows} == {types[1]} for rows in new_links.values())
    with connect(service.db_path) as conn:
        assert [row[0] for row in conn.execute("SELECT tag_value FROM question_tags WHERE tag_type='knowledge_point' AND tag_value LIKE '%_t%' ORDER BY tag_value")] == [types[1]]
        assert [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE evidence_version_id=?', ('a' * 64,))] == old
        assert [tuple(row) for row in conn.execute('SELECT * FROM question_solution_evidence_versions')] == old_versions

    with connect(service.db_path) as conn:
        protected_before = [tuple(row) for row in conn.execute("SELECT tag_type,tag_value,confidence,source,model_name FROM question_tags WHERE tag_type IN ('knowledge_point','secondary_type','prerequisite') ORDER BY tag_type,tag_value")]
        links_before = [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links')]
    service.replace_tags(qid, expected_revision=service.get_revision(qid), tags=[
        ConfirmedQuestionTag('ability', '运算能力'), ConfirmedQuestionTag('knowledge_point', 'TEST-非法整题知识点')])
    with connect(service.db_path) as conn:
        assert [tuple(row) for row in conn.execute("SELECT tag_type,tag_value,confidence,source,model_name FROM question_tags WHERE tag_type IN ('knowledge_point','secondary_type','prerequisite') ORDER BY tag_type,tag_value")] == protected_before
        assert [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links')] == links_before

    # 关键步骤词表规则：客观题判定点不产生缺口、不计入“缺关键步骤”。
    from question_bank.services.question_skill_index import build_skill_snapshot
    from question_bank.solution_evidence.knowledge_links import project_embedded_links
    with connect(service.db_path) as conn:
        for new_id, number in ((2, '2'), (3, '3')):
            conn.execute("INSERT INTO questions (id, paper_id, question_number, question_text) VALUES (?, 1, ?, 'KeyStepRule')", (new_id, number))
        conn.execute(
            "INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by) VALUES(?,2,?, 'question-solution-evidence-v2',?,?,'approved','combined_model','TEST-skill-gaps','test')",
            ('e' * 64, 'a' * 64, 'b' * 64, json.dumps({'parts': [
                {'part_id': 'part-1', 'response_mode': 'exact_objective',
                 'evidence_points': [{'evidence_point_id': 'o2', 'target': '作答为'}]},
                {'part_id': 'part-2',
                 'evidence_points': [{'evidence_point_id': 's2', 'target': '写出过程'}]},
            ]})),
        )
        conn.execute(
            "INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by) VALUES(?,3,?, 'question-solution-evidence-v2',?,?,'approved','combined_model','TEST-skill-gaps','test')",
            ('f' * 64, 'a' * 64, 'b' * 64, json.dumps({'parts': [
                {'part_id': 'part-1', 'response_mode': 'exact_objective',
                 'evidence_points': [{'evidence_point_id': 'o3', 'target': '作答为'}]},
            ]})),
        )
        snapshot = build_skill_snapshot(conn, service.db_path, service.data_root)
    assert qid not in snapshot['unlinked']
    assert 2 in snapshot['unlinked']
    assert 3 not in snapshot['unlinked']
    assert [gap['point_id'] for gap in snapshot['gap_points'][2]] == ['s2']
    assert 3 not in snapshot['gap_points']

    # 内嵌挂接写入同样遵守：客观题点不落 sk、每点最多一条 direct sk。
    embedded = {'parts': [
        {'part_id': 'obj', 'response_mode': 'exact_objective', 'evidence_points': [
            {'evidence_point_id': 'eo', 'fine_term_links': [
                {'fine_term_id': 'sk_TEST', 'role': 'direct',
                 'core_resolution': {'status': 'resolved', 'stable_keys': ['sk_TEST']}}]}]},
        {'part_id': 'subj', 'evidence_points': [
            {'evidence_point_id': 'es', 'fine_term_links': [
                {'fine_term_id': 'sk_TEST', 'role': 'direct',
                 'core_resolution': {'status': 'resolved', 'stable_keys': ['sk_TEST']}},
                {'fine_term_id': 'sk_TEST2', 'role': 'direct',
                 'core_resolution': {'status': 'resolved', 'stable_keys': ['sk_TEST2']}}]}]},
    ]}
    with connect(service.db_path) as conn:
        project_embedded_links(conn, evidence_version_id='f' * 64, question_id=3,
                               evidence_payload=embedded, default_release_id=release_id)
        rows = conn.execute(
            "SELECT part_id,evidence_point_id,stable_key FROM evidence_point_knowledge_links "
            "WHERE evidence_version_id=? AND role='direct' AND stable_key LIKE 'sk_%' ORDER BY rowid",
            ('f' * 64,)).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [('subj', 'es', 'sk_TEST')]

    # sk_ 链接不参与小节归属：增删 direct sk 后重算，scope summary 不变。
    from question_bank.solution_evidence.knowledge_links import refresh_question_scope_summary
    scope_fields = ("SELECT question_id,evidence_version_id,graph_release_id,primary_section_id,"
                    "direct_section_ids_json,supporting_max_volume_order,has_cross_chapter_direct"
                    " FROM question_scope_summary WHERE question_id=?")
    with connect(service.db_path) as conn:
        refresh_question_scope_summary(conn, qid)
        scope_row = tuple(conn.execute(scope_fields, (qid,)).fetchone())
        conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,weight,source_kind) VALUES(?,?,'part-1','p1',?,'direct','sk_NEW','sk_NEW','resolved',9.0,'link_job')", ('a' * 64, qid, release_id))
        conn.execute("DELETE FROM evidence_point_knowledge_links WHERE stable_key='sk_TEST' AND role='direct'")
        refresh_question_scope_summary(conn, qid)
        assert tuple(conn.execute(scope_fields, (qid,)).fetchone()) == scope_row


question_bank_paper_trash__CURRENT_PAPER_QUESTION_FK_CHILDREN = {
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


def question_bank_paper_trash__paper_question_fk_children(conn) -> set[tuple[str, str, str]]:
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


def question_bank_paper_trash__seed_complete_analysis_dependencies(
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


def question_bank_paper_trash__seed_paper(
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
    writer, _reader, paper_id, version, source_path = question_bank_paper_trash__seed_paper(tmp_path)
    with connect(writer.db_path) as conn:
        question_id = int(
            conn.execute(
                "SELECT id FROM questions WHERE paper_id = ? ORDER BY id LIMIT 1",
                (paper_id,),
            ).fetchone()[0]
        )
        assert question_bank_paper_trash__paper_question_fk_children(conn) == (
            question_bank_paper_trash__CURRENT_PAPER_QUESTION_FK_CHILDREN
        )
        question_bank_paper_trash__seed_complete_analysis_dependencies(
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
    writer, _reader, paper_id, version, _source_path = question_bank_paper_trash__seed_paper(tmp_path)
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
                ) VALUES ('2025深圳中考留存卷', 'other-source.docx', 'completed', ?)
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

    starred = writer.set_exam_reference(surviving_paper_id,
        expected_updated_at="2026-07-30 12:00:00.000000", starred=True)
    assert writer.set_exam_reference(surviving_paper_id,
        expected_updated_at="2026-07-30 12:00:00.000000", starred=True) == starred
    # Reopening the library retains the star, inferred year and paper occurrence.
    reopened = QuestionBankReadService(writer.db_path, data_root=writer.data_root)
    reference = reopened.entrance_exam_profile()
    assert reference["paper_count"] == 1
    assert reference["papers"][0]["year"] == 2025
    assert reopened.exam_references().assess(canonical_id)["status"] == "reference"

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


question_import_duplicates_QUESTION_TEXT = "这是长度足够的测试题目"


question_import_duplicates_ANSWER_TEXT = "42"


question_import_duplicates_PAPER_TEXT = f"1. {question_import_duplicates_QUESTION_TEXT}\n答案：\n1. {question_import_duplicates_ANSWER_TEXT}"


def question_import_duplicates__fake_extract(text: str):
    def extract(path, **_kwargs):
        return ExtractedDocument(
            source_file=str(path),
            page_range="document",
            text=text,
        )

    return extract


def question_import_duplicates__import_paper(
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


def question_import_duplicates__single_question_id(db_path: Path, paper_title: str) -> int:
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


def question_import_duplicates__seed_tags(db_path: Path, question_id: int) -> None:
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


@pytest.fixture()
def question_import_duplicates_bank(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    return {"db": db_path, "data_root": data_root}


def test_identical_stem_with_different_answer_preserves_both_sources_for_review(
    question_import_duplicates_bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(batch_importer, "_extract_paper", question_import_duplicates__fake_extract(question_import_duplicates_PAPER_TEXT))
    question_import_duplicates__import_paper(
        tmp_path, question_import_duplicates_bank["db"], question_import_duplicates_bank["data_root"], name="a.docx", text=question_import_duplicates_PAPER_TEXT
    )
    source_id = question_import_duplicates__single_question_id(question_import_duplicates_bank["db"], "a")
    question_import_duplicates__seed_tags(question_import_duplicates_bank["db"], source_id)

    changed = f"1. {question_import_duplicates_QUESTION_TEXT}\n答案：\n1. 43"
    monkeypatch.setattr(batch_importer, "_extract_paper", question_import_duplicates__fake_extract(changed))
    second = question_import_duplicates__import_paper(
        tmp_path, question_import_duplicates_bank["db"], question_import_duplicates_bank["data_root"], name="b.docx", text=changed
    )

    assert second.question_count == 1
    assert second.exact_duplicate_count == 0
    hint = second.near_duplicate_hints[0]
    assert hint["match_kind"] == "answer_conflict"
    assert hint["matched_paper_title"] == "a"
    assert hint["matched_question_number"] == "1"
    new_id = question_import_duplicates__single_question_id(question_import_duplicates_bank["db"], "b")
    assert hint["question_id"] == new_id
    with connect(question_import_duplicates_bank["db"]) as conn:
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
    with connect(question_import_duplicates_bank["db"]) as conn:
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


def test_exact_duplicate_entries_report_matched_paper_and_number(
    question_import_duplicates_bank: dict[str, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    from unittest.mock import Mock

    from backend.jobs.manager import JobContext
    from backend.jobs.question_import import run_question_import_job
    from backend.jobs.store import JobStore

    monkeypatch.setattr(batch_importer, "_extract_paper", question_import_duplicates__fake_extract(question_import_duplicates_PAPER_TEXT))
    question_import_duplicates__import_paper(
        tmp_path, question_import_duplicates_bank["db"], question_import_duplicates_bank["data_root"], name="a.docx", text=question_import_duplicates_PAPER_TEXT
    )
    source_id = question_import_duplicates__single_question_id(question_import_duplicates_bank["db"], "a")

    data_root = question_import_duplicates_bank["data_root"]
    db_path = question_import_duplicates_bank["db"]
    service = QuestionBankWriteService(db_path, data_root=data_root)
    upload = service.stage_upload(filename="b.docx", content=b"synthetic-paper-b")
    request = service.create_import_request(upload_id=upload.upload_id)
    staged_source = service.load_import_resource(request.request_id).source_path
    store = JobStore(tmp_path / "jobs.db")
    payload = {"request_id": request.request_id}
    failed_job = store.create_job("question_import", payload)
    with monkeypatch.context() as failing:
        failing.setattr(batch_importer, "_extract_paper", Mock(side_effect=RuntimeError("synthetic import failure")))
        failed = run_question_import_job(
            context=JobContext(failed_job.id, "question_import", payload, store),
            question_bank_db_path=db_path, data_root=data_root, write_service=service,
        )
    assert failed["outcome"] == "failed"
    assert failed["retryable"] is True
    assert staged_source.read_bytes() == b"synthetic-paper-b"
    assert service.load_import_resource(request.request_id).source_path == staged_source

    retry_job = store.create_job("question_import", payload)
    second = run_question_import_job(
        context=JobContext(retry_job.id, "question_import", payload, store),
        question_bank_db_path=db_path, data_root=data_root, write_service=service,
    )

    assert second["outcome"] == "complete"
    assert second["exact_duplicate_count"] == 1
    assert second["exact_duplicates"] == [
        {
            "question_number": "1",
            "matched_question_id": source_id,
            "matched_paper_title": "a",
            "matched_question_number": "1",
        }
    ]
    assert not staged_source.exists()
    reopened_service = QuestionBankWriteService(db_path, data_root=data_root)
    resource = reopened_service.load_import_resource(request.request_id)
    assert resource.source_path.parent == data_root / "question_bank" / "raw_papers"
    assert resource.source_path.read_bytes() == b"synthetic-paper-b"
    assert resource.filename == "b.docx"
    assert reopened_service.create_import_request(upload_id=upload.upload_id) == request
    read_service = QuestionBankReadService(db_path, data_root=data_root)
    assert read_service.import_task_filename(request.request_id) == "b.docx"
    with connect(db_path) as conn:
        imported_source = conn.execute(
            "SELECT source_file FROM papers WHERE id = ?",
            (second["imported_paper_ids"][0],),
        ).fetchone()[0]
    assert (data_root / imported_source).read_bytes() == b"synthetic-paper-b"
    preview = read_service.get_question_for_preview(source_id)
    assert Path(preview["source_file"]).read_bytes() == b"content-of-a.docx"

    replay_job = store.create_job("question_import", payload)
    with monkeypatch.context() as replaying:
        extraction = Mock(side_effect=AssertionError("completed import must not extract again"))
        replaying.setattr(batch_importer, "_extract_paper", extraction)
        replay = run_question_import_job(
            context=JobContext(replay_job.id, "question_import", payload, store),
            question_bank_db_path=db_path, data_root=data_root, write_service=reopened_service,
        )
    assert replay["outcome"] == "complete"
    assert replay["imported_papers"] == 0
    extraction.assert_not_called()
