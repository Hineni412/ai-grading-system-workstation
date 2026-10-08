"""Explicit, previewed question-bank gaps; never submit the whole library."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from question_bank.database.schema import connect
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    _ACTIVE_READ_SCOPE,
    _CACHE_MISS,
    _read_result_cache_get,
    _read_result_cache_put,
    _source_generation_token,
    _taxonomy_generation_token,
)
from question_bank.solution_evidence.part_assessments import load_profiles
from question_bank.taxonomy.governance import get_taxonomy_governance
from question_bank.training_criteria import QuestionAnalysisInputLoader

from .manager import JobContext
from .tagging_sync import _failure, _load_analysis_gaps, _load_tag_source_currentness, _load_tagging_candidates


def _skill_candidate_state_token() -> tuple[object, ...]:
    base = get_taxonomy_governance().state_path.resolve(strict=False)
    path = base.with_name(f"{base.stem}.skill_candidates{base.suffix or '.json'}")
    try:
        info = path.stat()
    except (FileNotFoundError, OSError):
        return (str(path), None)
    return (str(path), int(info.st_size), int(info.st_mtime_ns))


def repair_preview(service: QuestionBankReadService, volume_id: str, kind: str,
                   question_ids: list[int] | None = None) -> dict[str, Any]:
    if kind not in {'skills', 'analysis', 'all'}:
        raise ValueError('请选择有效的补齐范围')
    # Whole-volume previews are read-only and expensive; reuse them until any
    # question-bank, taxonomy or skill-candidate write shifts a generation
    # token. Subset previews (job runs) stay uncached.
    generation = (
        None
        if question_ids is not None or _ACTIVE_READ_SCOPE.get() is not None
        else _source_generation_token(service.db_path)
    )
    if generation is None:
        return _compute_repair_preview(service, volume_id, kind, question_ids)
    key = ('repair_preview', generation, service._cache_data_root,
           _taxonomy_generation_token(), _skill_candidate_state_token(),
           volume_id, kind)
    cached = _read_result_cache_get(key)
    if cached is not _CACHE_MISS:
        return cached  # type: ignore[return-value]
    result = _compute_repair_preview(service, volume_id, kind, question_ids)
    # Re-check the token: a write that landed mid-scan must not be masked.
    if generation == _source_generation_token(service.db_path):
        _read_result_cache_put(key, result)
    return result


def _compute_repair_preview(service: QuestionBankReadService, volume_id: str, kind: str,
                   question_ids: list[int] | None = None) -> dict[str, Any]:
    if kind not in {'skills', 'analysis', 'all'}:
        raise ValueError('请选择有效的补齐范围')
    snapshot = service._skill_snapshot()
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    if curriculum_volume(volume_id=volume_id) is None:
        raise ValueError('请选择有效的教学学期')
    scoped = set(snapshot['volumes'].get(volume_id, ()))
    if question_ids is not None:
        if not set(question_ids).issubset(scoped):
            raise ValueError('补齐题目已不在当前教学学期')
        scoped.intersection_update(question_ids)
    if kind == 'skills':
        scoped.intersection_update(snapshot['unlinked'])
    ids = sorted(scoped)
    db = service.db_path
    root = service.data_root or db.parent.parent
    if not ids:
        return _preview(volume_id, kind, [], [])
    loader = QuestionAnalysisInputLoader(db_path=db, data_root=root)
    load_failures: dict[int, str] = {}
    inputs = {q.question_id: q for q in loader.load(ids,
        curriculum_volume_id=volume_id, load_failures=load_failures)}
    gaps = _load_analysis_gaps(db, ids, current_inputs=inputs, data_root=root,
                               curriculum_volume_id=volume_id)
    _, tag_complete, _ = _load_tagging_candidates(db, ids, curriculum_volume_id=volume_id, require_difficulty=True)
    tags_ready = set(tag_complete)
    tags_ready.difference_update(qid for qid, current in
        _load_tag_source_currentness(db, current_inputs=list(inputs.values())).items() if not current)
    with connect(db) as conn:
        profiles = load_profiles(db, ids, connection=conn, data_root=root, question_inputs=inputs)
        marks = ','.join('?' for _ in ids)
        rows = {int(row['id']): dict(row) for row in conn.execute(
            f'SELECT q.*,p.title AS paper_title FROM questions q JOIN papers p ON p.id=q.paper_id WHERE q.id IN ({marks})', ids)}
        teacher = {str(row[0]) for row in conn.execute(
            f"SELECT DISTINCT evidence_version_id FROM evidence_point_knowledge_links WHERE question_id IN ({marks}) AND source_kind='teacher'", ids)}
        heads = {int(row['question_id']): dict(row) for row in conn.execute(
            f'SELECT * FROM training_criterion_heads WHERE question_id IN ({marks})', ids)}
    items = []
    for qid in ids:
        missing = []
        if qid not in tags_ready:
            missing.append('tags')
        profile = profiles.get(qid, {})
        if not profile.get('available') or not snapshot['point_counts'].get(qid):
            missing.append('evidence')
        if not gaps.get(qid, {}).get('criteria_ready'):
            missing.append('criteria')
        if qid in snapshot['unlinked']:
            missing.append('skills')
        if qid not in snapshot.get('primary_types', {}):
            missing.append('types')
        if not snapshot.get('knowledge_by_question', {}).get(qid):
            missing.append('knowledge_points')
        if not missing or (kind == 'skills' and 'skills' not in missing) or (kind == 'analysis' and not set(missing) - {'skills'}):
            continue
        row = rows[qid]
        blocked = ''
        if qid in load_failures:
            blocked = str(_failure(qid, 'validation', reason_code=load_failures[qid])['message'])
        elif qid not in inputs or not inputs[qid].has_required_images:
            blocked = '题目内容或图片无法读取，请先检查原题'
        elif not snapshot['release']:
            blocked = '当前技能标准不可用'
        elif any(part in missing for part in ('skills', 'knowledge_points')) and str(profile.get('evidence_version_id', '')) in teacher:
            blocked = '教师已确认关联，请打开题目人工核对'
        type_pending = '本章题型尚未整理，暂时待归类' if 'types' in missing and qid in inputs and inputs[qid].taxonomy_contract.get('question_type_mode') is not True else ''
        if type_pending and missing == ['types']:
            blocked = type_pending
        item = {'id': qid, 'question_number': str(row['question_number']), 'paper_title': row['paper_title'],
                'missing': missing, 'blocked_reason': blocked, 'type_pending_reason': type_pending}
        if qid in load_failures:
            item['blocked_reason_code'] = load_failures[qid]
        # Internal revision has no question body and is never returned in job diagnostics.
        revision = [dict(row), heads.get(qid), profile.get('evidence_version_id'),
                    inputs[qid].source_content_hash if qid in inputs else '', snapshot['release'],
                    snapshot.get('primary_types', {}).get(qid), sorted(snapshot.get('knowledge_by_question', {}).get(qid, ()))]
        item['revision'] = sha256(json.dumps(revision, sort_keys=True, default=str).encode()).hexdigest()
        items.append(item)
    return _preview(volume_id, kind, items, ids)


def _preview(volume: str, kind: str, items: list[dict[str, Any]], scoped: list[int]) -> dict[str, Any]:
    digest = sha256(json.dumps([volume, kind, items], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'curriculum_volume_id': volume, 'kind': kind, 'fingerprint': digest,
            'scanned_count': len(scoped), 'question_count': len(items),
            'repairable_count': sum(not item['blocked_reason'] for item in items),
            'counts': {part: sum(part in item['missing'] for item in items)
                       for part in ('tags', 'evidence', 'criteria', 'skills', 'types', 'knowledge_points')},
            'items': items, 'model_calls': 0}


def run_question_bank_repair_job(*, context: JobContext, question_bank_db_path: Path,
        data_root: Path, tagging_runner: Callable, link_runner: Callable,
        ai_service_factory: Callable, link_gateway_factory: Callable,
        taxonomy_governance: Any = None) -> dict[str, Any]:
    payload = context.payload
    ids = payload.get('question_ids')
    if not isinstance(ids, list) or not ids:
        raise ValueError('补齐任务必须指定题目')
    service = QuestionBankReadService(question_bank_db_path, data_root=data_root)
    preview = repair_preview(service, payload['curriculum_volume_id'], payload['kind'], ids)
    expected = payload.get('revisions', {})
    candidates = [item for item in preview['items'] if not item['blocked_reason'] and
                  item['revision'] == expected.get(str(item['id']))]
    analysis_ids = [item['id'] for item in candidates if set(item['missing']) & {'tags', 'evidence', 'criteria'}]
    type_ids = []
    type_candidates = [item['id'] for item in candidates if 'types' in item['missing']]
    if type_candidates:
        questions = QuestionAnalysisInputLoader(db_path=question_bank_db_path, data_root=data_root).load(
            type_candidates, curriculum_volume_id=payload['curriculum_volume_id'])
        type_ids = [question.question_id for question in questions if question.taxonomy_contract.get('question_type_mode') is True]
    analysis_ids = list(dict.fromkeys([*analysis_ids, *type_ids]))
    result: dict[str, Any] = {'requested_count': len(ids), 'analysis_count': len(analysis_ids),
                              'skill_count': 0, 'completed_count': 0, 'remaining': []}
    context.raise_if_cancelled()
    if analysis_ids:
        child = replace(context, payload={'question_ids': analysis_ids,
            'curriculum_volume_id': payload['curriculum_volume_id'], 'repair_missing_only': True,
            'repair_type_question_ids': type_ids})
        tagging_runner(context=child, question_bank_db_path=question_bank_db_path,
            data_root=data_root, ai_service_factory=ai_service_factory,
            taxonomy_governance=taxonomy_governance)
    context.raise_if_cancelled()
    after = repair_preview(service, payload['curriculum_volume_id'], 'all',
                           [item['id'] for item in candidates]) if candidates else {'items': []}
    # A failed earlier stage never adds a second paid request to the same question.
    link_ids = [item['id'] for item in after['items'] if not item['blocked_reason']
                and 'skills' in item['missing'] and not set(item['missing']) & {'tags', 'evidence', 'criteria'}]
    knowledge_ids = [item['id'] for item in after['items'] if not item['blocked_reason']
                     and 'knowledge_points' in item['missing'] and not set(item['missing']) & {'tags', 'evidence', 'criteria'}
                     and item['id'] not in analysis_ids and item['id'] not in link_ids]
    result['skill_count'] = len(link_ids)
    if link_ids:
        child = replace(context, payload={'question_ids': link_ids, 'mode': 'missing_skills'})
        link_runner(context=child, question_bank_db_path=question_bank_db_path,
                    data_root=data_root, link_gateway=link_gateway_factory(),
                    taxonomy_governance=taxonomy_governance)
    if knowledge_ids:
        child = replace(context, payload={'question_ids': knowledge_ids, 'mode': 'missing_knowledge'})
        link_runner(context=child, question_bank_db_path=question_bank_db_path,
                    data_root=data_root, link_gateway=link_gateway_factory(),
                    taxonomy_governance=taxonomy_governance)
    context.raise_if_cancelled()
    final = repair_preview(service, payload['curriculum_volume_id'], 'all', ids)
    result['remaining'] = [{'id': item['id'], 'question_number': item['question_number'],
        'missing': item['missing'], 'reason': item['blocked_reason'] or item.get('type_pending_reason') or
        ('题目已变化，请重新查看补齐清单' if item['id'] not in {i['id'] for i in candidates}
         else '本次未补齐，请打开题目核对；再次请求需要重新确认')}
        for item in final['items']]
    result['completed_count'] = len(ids) - len(final['items'])
    result['outcome'] = 'partial' if final['items'] else 'complete'
    context.report(1.0, 'question_bank_repair', f"补齐 {result['completed_count']}/{len(ids)} 题")
    return result
