from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_question_bank_write_service,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.services.question_write_service import QuestionBankWriteService


VOLUME_ID = "bnu24-math-g7-lower"


def _client(tmp_path: Path):
    service = QuestionBankWriteService(
        tmp_path / "data" / "databases" / "question_bank.db",
        data_root=tmp_path / "data",
    )
    upload = service.stage_upload(filename="测试试卷.docx", content=b"fake-docx")
    request = service.create_import_request(upload_id=upload.upload_id)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def import_handler(context):
        return {
            "request_id": context.payload["request_id"],
            "outcome": "complete",
            "imported_papers": 1,
            "question_count": 2,
            "failed_count": 0,
            "successful_question_ids": [11, 12],
            "failed_question_ids": [],
            "failure_category": "",
            "retryable": False,
            "internal_path": str(tmp_path / "must-not-leak.docx"),
        }

    def tagging_handler(context):
        ids = [int(item) for item in context.payload["question_ids"]]
        failed = [item for item in ids if item == 12]
        succeeded = [item for item in ids if item != 12]
        return {
            "outcome": "partial" if failed else "complete",
            "requested_count": len(ids),
            "skipped_complete_count": 0,
            "tagged_count": len(succeeded),
            "failed_count": len(failed),
            "successful_question_ids": succeeded,
            "failed_question_ids": failed,
            "failures": [
                {
                    "question_id": item,
                    "category": "timeout",
                    "message": "AI tagging request timed out.",
                }
                for item in failed
            ],
            "retryable": bool(failed),
            "question_text": "must not leak",
        }

    manager.register("question_import", import_handler)
    manager.register("tagging_sync", tagging_handler)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app), manager, service, request


def test_tagging_retry_marks_evidence_only_ids_without_losing_tag_success(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "partial",
            "successful_question_ids": [11],
            "failed_question_ids": [11],
            "evidence_failed_question_ids": [11],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [11]},
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "question_ids": [11],
        "retry_evidence_question_ids": [11],
        "retry_of_job_id": source.id,
        "curriculum_volume_id": VOLUME_ID,
    }


def test_duplicate_tagging_retry_requests_keep_same_safe_logical_payload(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11, 12], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "partial",
            "failed_question_ids": [12],
            "retryable": True,
        },
    )

    first = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [12]},
    )
    second = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [12]},
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert (
        first.json()["payload"]
        == second.json()["payload"]
        == {
            "question_ids": [12],
            "retry_of_job_id": source.id,
            "curriculum_volume_id": VOLUME_ID,
        }
    )


def test_repair_api_requires_current_preview_and_recovers_same_task(tmp_path):
    from backend.api.dependencies import get_question_bank_read_service
    from backend.jobs.question_bank_repair import repair_preview
    from tests.test_question_bank_read_cache import _seed_skill_bank
    service, db, _ = _seed_skill_bank(tmp_path)
    manager = JobManager(JobStore(tmp_path / 'TEST-repair-jobs.db'), max_workers=1)
    calls = []
    def repair(context):
        calls.append(context.payload)
        return {'outcome': 'partial', 'requested_count': 1, 'completed_count': 0, 'remaining': [],
                'question_text': 'TEST-do-not-expose'}
    manager.register('question_bank_repair', repair)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_question_bank_read_service] = lambda: service
    with TestClient(app) as client:
        preview = client.get('/api/question-bank/repair-preview', params={
            'curriculum_volume_id': 'bnu24-math-g8-upper', 'kind': 'skills'})
        assert preview.status_code == 200
        assert not calls
        body = {'curriculum_volume_id': 'bnu24-math-g8-upper', 'kind': 'skills', 'question_ids': [4],
                'fingerprint': preview.json()['fingerprint'], 'client_request_token': 'f' * 32}
        invalid = client.post('/api/question-bank/repair-jobs', json={**body, 'question_ids': [1]})
        assert invalid.status_code == 409 and not calls
        first = client.post('/api/question-bank/repair-jobs', json=body)
        assert first.status_code == 202, first.text
        manager.wait(first.json()['id'], timeout=5)
        second = client.post('/api/question-bank/repair-jobs', json=body)
        assert second.status_code == 202 and second.json()['id'] == first.json()['id']
        assert len(calls) == 1 and calls[0]['question_ids'] == [4]
        assert 'revisions' not in second.json()['payload']
        assert 'question_text' not in second.json()['result']
        assert client.post('/api/question-bank/repair-jobs', json={**body, 'kind': 'analysis'}).status_code == 409
        assert client.post('/api/question-bank/repair-jobs', json={**body, 'fingerprint': '0' * 64, 'client_request_token': 'e' * 32}).status_code == 409
    manager.shutdown()


def test_task_context_is_read_only_and_keeps_content_out_of_public_jobs(tmp_path, question_bank_database):
    import sqlite3
    from backend.api.dependencies import get_question_bank_read_service
    from question_bank.services.question_read_service import QuestionBankReadService

    client, manager, writer, request = _client(tmp_path)
    question_bank_database(writer.db_path)
    with sqlite3.connect(writer.db_path) as conn:
        conn.executemany(
            "INSERT INTO papers (id, title, source_file, import_status) VALUES (?, ?, ?, ?)",
            [(1, '合成原卷', 'private/original.docx', 'success'), (2, '合成关联卷', 'private/linked.docx', 'success'),
             (3, '合成已移除卷', 'private/removed.docx', 'deleted')],
        )
        conn.execute("INSERT INTO questions (id, paper_id, question_number, question_text) VALUES (11, 1, '1', '不可进入任务中心的题干')")
        conn.executemany("INSERT INTO paper_question_occurrences (paper_id, question_id, question_number) VALUES (?, 11, '1')", [(2,), (3,)])
    reader = QuestionBankReadService(writer.db_path, data_root=writer.data_root)
    client.app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    before = writer.db_path.read_bytes()
    job = manager.store.create_job('tagging_sync', {'question_ids': [11, 999]})
    import_job = manager.store.create_job('question_import', {'request_id': request.request_id})
    other_job = manager.store.create_job('ops_backup', {'question_ids': [11]})
    try:
        response = client.get(f'/api/question-bank/task-context/{job.id}')
        assert response.status_code == 200
        assert response.json() == {'papers': [{'id': 1, 'title': '合成原卷'}, {'id': 2, 'title': '合成关联卷'}], 'source_filename': None}
        assert '不可进入任务中心的题干' not in response.text
        assert 'private' not in response.text
        importing = client.get(f'/api/question-bank/task-context/{import_job.id}')
        assert importing.status_code == 200
        assert importing.json() == {'papers': [], 'source_filename': '测试试卷.docx'}
        public_job = client.get(f'/api/jobs/{import_job.id}').json()
        assert '测试试卷.docx' not in str(public_job['payload'])
        assert client.get(f'/api/question-bank/task-context/{other_job.id}').status_code == 404
        assert client.get('/api/question-bank/task-context/99999').status_code == 404
        assert writer.db_path.read_bytes() == before
    finally:
        manager.shutdown()


def test_task_context_uses_imported_papers_and_handles_missing_sources(tmp_path, question_bank_database):
    import sqlite3
    from backend.api.dependencies import get_question_bank_read_service
    from question_bank.services.question_read_service import QuestionBankReadService

    client, manager, writer, _request = _client(tmp_path)
    question_bank_database(writer.db_path)
    with sqlite3.connect(writer.db_path) as conn:
        conn.execute("INSERT INTO papers (id, title, source_file) VALUES (7, '合成新导入卷', 'private/new.docx')")
    reader = QuestionBankReadService(writer.db_path, data_root=writer.data_root)
    client.app.dependency_overrides[get_question_bank_read_service] = lambda: reader
    job = manager.store.create_job('question_import', {'request_id': 'a' * 32})
    manager.store.mark_running(job.id)
    manager.store.finish(job.id, 'succeeded', result={'imported_paper_ids': [7], 'outcome': 'complete'})
    missing = manager.store.create_job('tagging_sync', {'question_ids': [999]})
    try:
        response = client.get(f'/api/question-bank/task-context/{job.id}')
        assert response.json() == {'papers': [{'id': 7, 'title': '合成新导入卷'}], 'source_filename': None}
        assert client.get(f'/api/question-bank/task-context/{missing.id}').json() == {'papers': [], 'source_filename': None}
        assert reader.import_task_filename('../outside') is None
        absent_root = tmp_path / 'test-missing-root'
        assert QuestionBankReadService(writer.db_path, data_root=absent_root).import_task_filename('b' * 32) is None
        assert not absent_root.exists()
    finally:
        manager.shutdown()


def test_tagging_type_cost_preview_binds_exact_authorized_plan(tmp_path, monkeypatch):
    from backend.api.dependencies import get_question_bank_read_service
    from question_bank.services.question_read_service import QuestionBankReadService
    import question_bank.services.chapter_type_service as organizer
    client, manager, writer, _ = _client(tmp_path)
    client.app.dependency_overrides[get_question_bank_read_service] = lambda: QuestionBankReadService(writer.db_path, data_root=writer.data_root)
    plan = {'base_release_id': 'kgr_TEST', 'input_fingerprint': 'a' * 64, 'volume_id': VOLUME_ID,
            'chapters': [{'chapter_id': 'kp_TEST_1', 'label': 'TEST第一章', 'question_count': 30,
                          'paper_count': 3, 'unclassified_count': 30, 'planned_requests': 1}],
            'planned_requests': 1, 'model_calls': 0}
    monkeypatch.setattr(organizer, 'preview_chapter_type_plan', lambda **kwargs: dict(plan))
    request = {'question_ids': [11], 'curriculum_volume_id': VOLUME_ID, 'client_request_token': 'd' * 32}
    preview = client.post('/api/question-bank/tagging-jobs/preview', json=request)
    assert preview.status_code == 200 and preview.json() == plan
    assert manager.store.list_jobs() == ([], 0)
    authorization = {**preview.json(), 'confirmed': True, 'request_limit': 1}
    rejected = client.post('/api/question-bank/tagging-jobs', json={**request,
        'chapter_type_authorization': {**authorization, 'request_limit': 2}})
    assert rejected.status_code == 409
    accepted = client.post('/api/question-bank/tagging-jobs', json={**request, 'chapter_type_authorization': authorization})
    assert accepted.status_code == 202
    manager.wait(accepted.json()['id'], timeout=5)
    assert manager.store.get_job(accepted.json()['id']).payload['chapter_type_authorization'] == authorization
    replay = client.post('/api/question-bank/tagging-jobs', json={**request, 'chapter_type_authorization': authorization})
    assert replay.status_code == 202 and replay.json()['id'] == accepted.json()['id']
    plan['input_fingerprint'] = 'b' * 64
    changed = client.post('/api/question-bank/tagging-jobs', json={**request, 'client_request_token': 'e' * 32,
        'chapter_type_authorization': authorization})
    assert changed.status_code == 409
    manager.shutdown()


def test_tagging_one_volume_batch_authorizes_union_without_expanding_analysis(tmp_path, monkeypatch):
    from backend.api.dependencies import get_question_bank_read_service
    from question_bank.services.question_read_service import QuestionBankReadService
    import question_bank.services.chapter_type_service as organizer
    client, manager, writer, _ = _client(tmp_path)
    client.app.dependency_overrides[get_question_bank_read_service] = lambda: QuestionBankReadService(writer.db_path, data_root=writer.data_root)
    scopes = []
    def preview(**kwargs):
        scopes.append(kwargs['question_ids'])
        return {'base_release_id': 'kgr_TEST', 'input_fingerprint': 'a' * 64,
                'volume_id': VOLUME_ID, 'pending_question_ids': list(kwargs['question_ids']),
                'chapters': [], 'planned_requests': 1, 'model_calls': 0}
    monkeypatch.setattr(organizer, 'preview_chapter_type_plan', preview)
    first = {'question_ids': [11], 'curriculum_volume_id': VOLUME_ID}
    second = {'question_ids': [12], 'curriculum_volume_id': VOLUME_ID}
    try:
        invalid = client.post('/api/question-bank/tagging-jobs/preview', json={**first,
            'chapter_type_scope_question_ids': [12]})
        assert invalid.status_code == 422
        response = client.post('/api/question-bank/tagging-jobs/preview', json={**second,
            'chapter_type_scope_question_ids': [11, 12]})
        assert response.status_code == 200
        authorization = {**response.json(), 'confirmed': True, 'request_limit': 1}
        first_job = client.post('/api/question-bank/tagging-jobs', json=first)
        last_job = client.post('/api/question-bank/tagging-jobs', json={**second,
            'chapter_type_authorization': authorization})
        assert first_job.status_code == last_job.status_code == 202
        manager.wait(last_job.json()['id'], timeout=5)
        assert manager.store.get_job(first_job.json()['id']).payload['question_ids'] == [11]
        assert 'chapter_type_authorization' not in manager.store.get_job(first_job.json()['id']).payload
        saved = manager.store.get_job(last_job.json()['id']).payload
        assert saved['question_ids'] == [12]
        assert saved['chapter_type_authorization']['pending_question_ids'] == [11, 12]
        assert scopes == [(11, 12), (11, 12)]
        outside = client.post('/api/question-bank/tagging-jobs', json={**first,
            'chapter_type_authorization': {**authorization, 'pending_question_ids': [12]}})
        assert outside.status_code == 422
        too_many = client.post('/api/question-bank/tagging-jobs', json={**first, 'question_ids': list(range(1, 502))})
        assert too_many.status_code == 422
    finally:
        manager.shutdown()
