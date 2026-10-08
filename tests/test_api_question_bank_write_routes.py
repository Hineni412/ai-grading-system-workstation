from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from question_bank.database.schema import initialize_database
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
    QuestionWriteConflict,
    QuestionImportTooLarge,
)


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

    assert len(caught.value.current_revision) == 64
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


def test_question_import_upload_and_request_routes(write_seed) -> None:
    service, _, _ = write_seed
    client = _write_client(service)

    uploaded = client.post(
        "/api/question-bank/import-uploads",
        params={"filename": "paper.pdf"},
        content=b"%PDF-api",
        headers={"content-type": "application/octet-stream"},
    )
    requested = client.post(
        "/api/question-bank/import-requests",
        json={"upload_id": uploaded.json()["upload_id"]},
    )

    assert uploaded.status_code == 201
    assert "path" not in uploaded.text.casefold()
    assert requested.status_code == 201
    assert requested.json()["status"] == "pending"


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
