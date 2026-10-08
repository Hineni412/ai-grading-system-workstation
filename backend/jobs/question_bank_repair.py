"""Explicit, previewed question-bank gaps; never submit the whole library."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from integration.result_cache import ResultCache
from question_bank.database.schema import connect
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    _ACTIVE_READ_SCOPE,
    _CACHE_MISS,
    _read_connection,
    _read_result_cache_get,
    _read_result_cache_put,
    _source_generation_token,
    _taxonomy_generation_token,
)
from question_bank.solution_evidence.part_assessments import load_profiles
from question_bank.taxonomy.governance import TaxonomyGovernance, get_taxonomy_governance
from question_bank.training_criteria import QuestionAnalysisInputLoader
from question_bank.training_criteria.analysis import source_content_hash_reuse
from question_bank.training_criteria.versioning import TrainingCriterionModule, usable_training_criterion

from .manager import JobContext
from .tagging_sync import _failure, _load_tag_source_currentness, _load_tagging_candidates, _plan_taxonomy


_REPAIR_PREVIEW_FLIGHTS = ResultCache(limit=1, max_bytes=0)


def _with_type_contracts(questions, db_path: Path, governance=None):
    governance = governance or TaxonomyGovernance(
        state_path=get_taxonomy_governance().state_path, knowledge_graph_db_path=db_path)
    contracts, _revision = _plan_taxonomy(None, governance,
        contexts={question.question_id: question.tagging_context for question in questions})
    return [replace(question, taxonomy_contract=contracts.get(question.question_id, {}))
            for question in questions]


def _type_candidate_fingerprint(question) -> str:
    from question_bank.question_types import is_type_key
    candidates = sorted((row for row in question.taxonomy_contract.get('candidates', {}).get('knowledge', [])
                         if is_type_key(row.get('id'))), key=lambda row: row['id'])
    return sha256(json.dumps(candidates, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode()).hexdigest()


def _current_type_review(value: str, question, release: str) -> bool:
    from question_bank.training_criteria.analysis import compatible_source_content_hashes
    try:
        review = json.loads(value)
    except (TypeError, ValueError):
        return False
    return bool(isinstance(review, dict) and review.get('status') == 'type_unclassified'
                and review.get('release_id') == release
                and review.get('source_hash') in compatible_source_content_hashes(question, kind='tag')
                and review.get('type_candidate_fingerprint') == _type_candidate_fingerprint(question))


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
    asset_generation, _ = service._asset_generation()
    if asset_generation is None:
        return _compute_repair_preview(service, volume_id, kind, question_ids)
    shared_kind = 'all' if kind == 'analysis' else kind
    key = ('repair_preview', generation, service._cache_data_root, asset_generation,
           _taxonomy_generation_token(), _skill_candidate_state_token(),
           volume_id, shared_kind)

    def compute():
        cached = _read_result_cache_get(key)
        if cached is not _CACHE_MISS:
            return cached
        result = _compute_repair_preview(service, volume_id, shared_kind, question_ids)
        if (generation == _source_generation_token(service.db_path)
                and asset_generation == service._asset_generation(refresh=True)[0]):
            _read_result_cache_put(key, result)
        return result

    return _preview_kind(_REPAIR_PREVIEW_FLIGHTS.get_or_compute(key, compute), kind)


def _preview_kind(preview: dict[str, Any], kind: str) -> dict[str, Any]:
    if preview['kind'] == kind:
        return preview
    items = [item for item in preview['items']
             if set(item['missing']) & {'tags', 'evidence', 'criteria'}]
    return _preview(preview['curriculum_volume_id'], kind, items, preview['scanned_count'])


def _compute_repair_preview(service: QuestionBankReadService, volume_id: str, kind: str,
                   question_ids: list[int] | None = None) -> dict[str, Any]:
    if kind not in {'skills', 'analysis', 'all'}:
        raise ValueError('请选择有效的补齐范围')
    from question_bank.services.question_skill_index import build_skill_snapshot, load_skill_inventory
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    if curriculum_volume(volume_id=volume_id) is None:
        raise ValueError('请选择有效的教学学期')
    db = service.db_path
    root = service.data_root or db.parent.parent
    snapshot_generation = _source_generation_token(db)
    asset_generation, asset_manifest = service._asset_generation()
    cached = (_read_result_cache_get(('skill_snapshot', snapshot_generation,
               service._cache_data_root, asset_generation), shared=True)
              if snapshot_generation is not None and asset_generation is not None else _CACHE_MISS)
    retained_snapshot = cached if isinstance(cached, dict) else None
    with _read_connection(db) as conn:
        inventory = load_skill_inventory(conn)
        if (retained_snapshot is None and service.persist_skill_snapshots
                and service._cache_data_root and snapshot_generation is not None
                and asset_generation is not None):
            retained_snapshot = service._read_local_skill_snapshot(
                snapshot_generation, inventory, asset_manifest, connection=conn)
    scoped = set(inventory['volumes'].get(volume_id, ()))
    if question_ids is not None:
        if not set(question_ids).issubset(scoped):
            raise ValueError('补齐题目已不在当前教学学期')
        scoped.intersection_update(question_ids)
    if kind == 'skills' and retained_snapshot is not None:
        scoped.intersection_update(retained_snapshot['unlinked'])
    ids = sorted(scoped)
    if not ids:
        return _preview(volume_id, kind, [], 0)
    loader = QuestionAnalysisInputLoader(db_path=db, data_root=root)
    _, tag_complete, _ = _load_tagging_candidates(db, ids, curriculum_volume_id=volume_id, require_difficulty=True)
    tags_ready = set(tag_complete)
    with connect(db) as conn:
        marks = ','.join('?' for _ in ids)
        rows = {int(row['id']): dict(row) for row in conn.execute(
            f'SELECT q.*,p.title AS paper_title FROM questions q JOIN papers p ON p.id=q.paper_id WHERE q.id IN ({marks})', ids)}
        teacher = {str(row[0]) for row in conn.execute(
            f"SELECT DISTINCT evidence_version_id FROM evidence_point_knowledge_links WHERE question_id IN ({marks}) AND source_kind='teacher'", ids)}
        type_reviews = {}
        for review in conn.execute(
                f"SELECT question_id,tag_value FROM question_tags WHERE question_id IN ({marks}) "
                "AND tag_type='tag_status' ORDER BY id", ids):
            type_reviews.setdefault(int(review[0]), []).append(str(review[1]))
        heads = {int(row['question_id']): dict(row) for row in conn.execute(
            f'SELECT * FROM training_criterion_heads WHERE question_id IN ({marks})', ids)}
    items = []
    scanned_count = 0
    module = TrainingCriterionModule(db, data_root=root)
    for start in range(0, len(ids), 64):
        batch = ids[start:start + 64]
        with source_content_hash_reuse():
            load_failures: dict[int, str] = {}
            inputs = {q.question_id: q for q in loader.load(batch,
                curriculum_volume_id=volume_id, load_failures=load_failures)}
            retained = (retained_snapshot.get('profiles', {}) if retained_snapshot is not None
                        and snapshot_generation == _source_generation_token(db) else {})
            profiles = {qid: retained[qid] for qid, question in inputs.items()
                        if qid in retained and retained[qid].get('current_source_content_hash')
                        == question.source_content_hash}
            unchecked = [qid for qid in batch if qid not in profiles]
            if unchecked:
                profiles.update(load_profiles(db, unchecked, data_root=root, question_inputs=inputs))
            if retained_snapshot is not None and not unchecked:
                snapshot = retained_snapshot
            else:
                with _read_connection(db) as conn:
                    snapshot = build_skill_snapshot(conn, db, root, inventory=inventory,
                                                    question_ids=tuple(batch), profiles=profiles)
            type_questions = _with_type_contracts(
                [question for qid, question in inputs.items() if qid not in snapshot.get('primary_types', {})], db)
            inputs.update({question.question_id: question for question in type_questions})
            workspaces = module.read_many(list(inputs.values()), profiles=profiles)
            tags_ready.difference_update(qid for qid, current in
                _load_tag_source_currentness(db, current_inputs=list(inputs.values())).items() if not current)
            selected = [qid for qid in batch if kind != 'skills' or qid in snapshot['unlinked']]
            scanned_count += len(selected)
            for qid in selected:
                missing = []
                if qid not in tags_ready:
                    missing.append('tags')
                profile = profiles.get(qid, {})
                if not profile.get('available') or not snapshot['point_counts'].get(qid):
                    missing.append('evidence')
                if usable_training_criterion(workspaces.get(qid, {})) is None:
                    missing.append('criteria')
                if qid in snapshot['unlinked']:
                    missing.append('skills')
                if qid not in snapshot.get('primary_types', {}):
                    missing.append('types')
                if not snapshot.get('knowledge_coverage_by_question', snapshot.get('knowledge_by_question', {})).get(qid):
                    missing.append('knowledge_points')
                if not missing or (kind == 'skills' and 'skills' not in missing) or (kind == 'analysis' and not set(missing) & {'tags', 'evidence', 'criteria'}):
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
                if workspaces.get(qid, {}).get('state') == 'rejected':
                    blocked = '训练判定已拒绝，请先核对题面或人工修正'
                type_pending = '本章题型尚未整理，暂时待归类' if 'types' in missing and qid in inputs and inputs[qid].taxonomy_contract.get('question_type_mode') is not True else ''
                if 'types' in missing and qid in inputs and any(
                        _current_type_review(value, inputs[qid], str(snapshot['release']))
                        for value in type_reviews.get(qid, ())):
                    type_pending = '已核对当前候选，暂无适合题型，待标准整理'
                if type_pending and missing == ['types']:
                    blocked = type_pending
                item = {'id': qid, 'question_number': str(row['question_number']), 'paper_title': row['paper_title'],
                        'missing': missing, 'blocked_reason': blocked, 'type_pending_reason': type_pending}
                if qid in load_failures:
                    item['blocked_reason_code'] = load_failures[qid]
                # Internal revision has no question body and is never returned in job diagnostics.
                revision = [dict(row), heads.get(qid), profile.get('evidence_version_id'),
                            inputs[qid].source_content_hash if qid in inputs else '', snapshot['release'],
                            snapshot.get('primary_types', {}).get(qid), sorted(snapshot.get('knowledge_coverage_by_question', {}).get(qid, ())),
                            inputs[qid].taxonomy_contract.get('candidate_fingerprint') if qid in inputs else '',
                            type_reviews.get(qid, ())]
                item['revision'] = sha256(json.dumps(revision, sort_keys=True, default=str).encode()).hexdigest()
                items.append(item)
    return _preview(volume_id, kind, items, scanned_count)


def _preview(volume: str, kind: str, items: list[dict[str, Any]], scanned_count: int) -> dict[str, Any]:
    digest = sha256(json.dumps([volume, kind, items], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'curriculum_volume_id': volume, 'kind': kind, 'fingerprint': digest,
            'scanned_count': scanned_count, 'question_count': len(items),
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
    type_candidates = [item['id'] for item in candidates if 'types' in item['missing'] and not item.get('type_pending_reason')]
    if type_candidates:
        questions = QuestionAnalysisInputLoader(db_path=question_bank_db_path, data_root=data_root).load(
            type_candidates, curriculum_volume_id=payload['curriculum_volume_id'])
        questions = _with_type_contracts(questions, question_bank_db_path, taxonomy_governance)
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
