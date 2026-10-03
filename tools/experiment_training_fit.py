"""Read-only chapter training fit experiment; saves aggregate counts only.

Reuses the current matcher and selector. No draft creation, criterion preparation,
database copies, profile persistence, model requests, or student rows in reports.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.repositories.grading_database import open_grading_repositories
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database import schema
from question_bank.recommendation import personalized as engine


@contextmanager
def readonly_runtime():
    """Use original files in place and reject every attempted SQLite write."""
    original_sqlite = sqlite3.connect
    original_schema = schema.connect

    def open_ro(database, *args, **kwargs):
        source = str(database)
        if source == ':memory:':
            raise RuntimeError('The experiment does not create database copies')
        if source.startswith('file:'):
            source = source.split('?')[0] + '?mode=ro'
        else:
            source = Path(source).resolve().as_uri() + '?mode=ro'
        kwargs['uri'] = True
        conn = original_sqlite(source, *args, **kwargs)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        return conn

    @contextmanager
    def connect_ro(db_path, *, external_connection=None):
        if external_connection is not None:
            if external_connection.execute('PRAGMA query_only').fetchone()[0] != 1:
                raise RuntimeError('A read-only connection is required')
            yield external_connection
            return
        conn = open_ro(db_path)
        conn.execute('BEGIN DEFERRED')
        try:
            yield conn
        finally:
            conn.close()

    with ExitStack() as stack:
        stack.enter_context(patch('sqlite3.connect', open_ro))
        # Imported aliases otherwise keep schema's default writable connection.
        for module in list(sys.modules.values()):
            if module and getattr(module, 'connect', None) is original_schema:
                stack.enter_context(patch.object(module, 'connect', connect_ro))
        yield connect_ro


def weak_only_selection(pools, config, needs=None):
    """Keep whole questions helping at least one actual loss, with all-member fit.

For a shared paper, preserve consolidation entries of other members: requiring
every member to remediate every question would change the product's meaning.
"""
    entries = [e for values in pools.values() for e in values]
    if config.paper_mode == 'shared':
        entries = engine._common_entries(entries, list(pools))
    usable_ids = {e['candidate']['question_id'] for e in entries
                  if engine._is_core(e) and e.get('practice_purpose') == 'remediation'
                  and (needs is None or e['key'] in needs.get(e['student_id'], {}))}
    entries = [e for e in entries if e['candidate']['question_id'] in usable_ids]
    selection_config = replace(config, remediation_only=config.paper_mode == 'individual')
    return engine._choose_practice_entries(entries, config.question_count, selection_config)


def member_metrics(selected, sid, needed, question_count):
    own = [e for _, group in selected for e in group if e['student_id'] == sid]
    core = [e for e in own if engine._is_core(e)
            and e.get('practice_purpose') == 'remediation' and e['key'] in needed]
    covered = {e['key'] for e in core} & set(needed)
    own_qids = {e['candidate']['question_id'] for e in own}
    all_qids = {e['candidate']['question_id'] for e, _ in selected}
    return {
        'question_count': len(selected), 'requested_count': question_count,
        'fit_questions': len(own_qids),
        'missing_member_fit': len(all_qids - own_qids),
        'remediation_questions': len({e['candidate']['question_id'] for e in core}),
        'need_count': len(needed), 'covered_need_count': len(covered),
        'coverage': len(covered)/len(needed) if needed else None,
        'fully_covered_need_count': len({e['key'] for e in core if e.get('practice_role') == 'full_response'}),
        'supplement_only_questions': len(all_qids)-len({e['candidate']['question_id'] for e in core}),
        'full_response_questions': len({e['candidate']['question_id'] for e in core
                                        if e.get('practice_role') == 'full_response'}),
        'part_practice_questions': len({e['candidate']['question_id'] for e in core
                                       if e.get('practice_role') == 'step_practice'}),
    }


def summarize(rows):
    supported = [r for r in rows if r['need_count']]
    return {
        'students': len(rows), 'students_with_needs': len(supported),
        'students_without_remediation': sum(r['remediation_questions'] == 0 for r in supported),
        'mean_questions': round(mean(r['question_count'] for r in rows), 3) if rows else None,
        'complete_papers': sum(r['question_count'] >= r['requested_count'] for r in rows),
        'mean_coverage': round(mean(r['coverage'] for r in supported), 4) if supported else None,
        'min_coverage': min((r['coverage'] for r in supported), default=None),
        'need_count': sum(r['need_count'] for r in supported),
        'covered_need_count': sum(r['covered_need_count'] for r in supported),
        'fully_covered_need_count': sum(r['fully_covered_need_count'] for r in supported),
        'member_fit_violations': sum(r['missing_member_fit'] for r in rows),
        'printed_slots': sum(r['question_count'] for r in rows),
        'remediation_slots': sum(r['remediation_questions'] for r in rows),
        'full_response_slots': sum(r['full_response_questions'] for r in rows),
        'part_practice_slots': sum(r['part_practice_questions'] for r in rows),
    }


def progress(phase, **counts):
    print(json.dumps({'phase': phase, **counts}, ensure_ascii=False), flush=True)


def evidence_view(rows, mode, early_session=None):
    """Sensitivity inputs only; never fill in unobserved steps or alter scores."""
    if mode not in {'current', 'coarsened_steps', 'without_total_only', 'steps_only', 'without_earliest',
                    'effective_results_only'}:
        raise ValueError('Unknown evidence sensitivity condition')
    result = []
    for original in rows:
        assessment = original.get('assessment') or {}
        if mode == 'without_total_only' and assessment.get('reason') == 'teacher_final_without_step_attribution':
            continue
        if mode == 'steps_only' and (not original.get('point_observations') or assessment.get('eligible') is False):
            continue
        if mode == 'without_earliest' and original.get('session_id') == early_session:
            continue
        if mode == 'effective_results_only' and (assessment.get('eligible') is False
            or assessment.get('granularity') not in {'part', 'step'}
            or float(assessment.get('evidence_weight', 1)) < .999):
            continue
        row = deepcopy(original)
        if mode == 'coarsened_steps':
            had_attribution = bool(row.get('point_observations') or row.get('target_contributions'))
            row.pop('point_observations', None)
            row.pop('target_contributions', None)
            if had_attribution and not (row.get('source_practice_metadata') or {}).get('is_single_result'):
                row['assessment'].update(granularity='whole_question', reason='part_total_without_step_attribution',
                    evidence_weight=1/max(len((row.get('question_tags') or {}).get('knowledge_point', ())), 1))
        result.append(row)
    return result


def selection_change(before, after):
    """Keep paired identities in memory; export anonymous counts only."""
    students = set(before) | set(after)
    return {'students': len(students),
            'changed_students': sum(before.get(s, set()) != after.get(s, set()) for s in students),
            'removed_slots': sum(len(before.get(s, set()) - after.get(s, set())) for s in students),
            'added_slots': sum(len(after.get(s, set()) - before.get(s, set())) for s in students)}


def match_rejection(entry, recent, maximum=8):
    """Explain the original gate on a diagnostic broad-match entry."""
    candidate = entry['candidate']
    if candidate['difficulty'] > maximum:
        return 'paper_difficulty_ceiling'
    plan = entry['target']['difficulty_plan']
    if candidate['difficulty'] < plan['audit_minimum']:
        return 'below_student_window'
    if candidate['difficulty'] > plan['audit_maximum']:
        return 'above_student_window'
    if candidate['question_id'] in recent:
        return 'recent_original'
    return None


def unselected_rejections(candidate, printed, config):
    """Report overlapping whole-paper constraints, not an invented single cause."""
    reasons = []
    if engine._paper_skill_limit_exceeded(candidate, printed, config):
        reasons.append('same_skill_quota')
    if engine._is_written_question(candidate) and sum(engine._is_written_question(q) for q in printed) >= config.max_written_questions:
        reasons.append('written_quota')
    if not engine.paper_similarity_allowed(candidate, printed):
        reasons.append('similar_or_duplicate')
    if engine.paper_task_duplicates(candidate, printed):
        reasons.append('same_task_quota')
    if len(printed) >= config.question_count:
        reasons.append('paper_full')
    return reasons or ['ranking_tradeoff']


def run_match_audit(data_root: Path, volume: str):
    """Trace candidate gates in memory; body inspection remains at the source."""
    start = time.perf_counter()
    grading_path, bank_path = (data_root / 'databases' / name for name in ('grading_system.db', 'question_bank.db'))
    with readonly_runtime() as connect_ro:
        with connect_ro(grading_path) as grading, connect_ro(bank_path) as bank:
            read_versions = {name: conn.execute('PRAGMA data_version').fetchone()[0]
                             for name, conn in [('grading', grading), ('bank', bank)]}
            service = DiagnosisProfileService(grading_path, bank_path,
                grading_db=open_grading_repositories(grading_path, external_connection=grading),
                question_bank_connection=bank, data_root=data_root, persist_snapshots=False)
            with patch.object(service, '_read_local_profile', return_value=None):
                diagnosis = service.build_profiles(scope={'mode': 'all', 'use_historical_fallback': False},
                    exam_scope={'mode': 'semester', 'curriculum_volume_id': volume})
            module = engine.PersonalizedRecommendationModule(db_path=bank_path,
                data_root=data_root, semester_mastery=service.semester_mastery)
            normalized = engine._normalize_diagnosis(diagnosis)
            mastery = module._mastery_snapshot(normalized)
            keys = tuple(f'kp_{volume.replace("-", "_")}_{c}' for c in (1, 2))
            config = engine.PersonalizedRecommendationConfig(scope_keys=keys, curriculum_volume_id=volume)
            broad_config = replace(config, difficulty_max=10)
            relations = tuple({'relation_type': r.relation_type, 'source_key': r.source_key,
                               'target_key': r.target_key} for r in module.current_knowledge.relations)
            leaves = engine._scope_leaves(keys, diagnosis=normalized, relations=relations)
            owned = engine._scope_descendants(frozenset(keys), module.current_knowledge)
            needs = {sid: {key: value for key, value in points.items() if key in owned}
                     for sid, points in engine._group_needs(normalized, leaves, cap=8).items()}
            candidates, _, _ = module._source_snapshot(knowledge_keys=leaves, candidate_config=broad_config)
            activities = service.graded_activities(tuple(s['student_id'] for s in normalized['students']))
            recent = module._recent_question_ids(tuple(s['student_id'] for s in normalized['students']),
                diagnosis=normalized, graded_activities=activities, recent_activity_count=3)
            original_plan = engine._difficulty_plan
            metadata = module._source_practice_metadata(normalized)
            source_links = module._links_for_metadata(metadata)
            def broad_plan(ref, score_rate, cap, target=None, profile=None):
                plan = original_plan(ref, score_rate, 8, target, profile)
                return {**plan, 'audit_minimum': plan['minimum'], 'audit_maximum': plan['maximum'],
                        'minimum': 1, 'maximum': 10}
            with patch.object(engine, '_difficulty_plan', broad_plan):
                evaluated = module.evaluate_candidates(diagnosis=normalized, config=broad_config,
                    candidates=candidates, mastery=mastery, source_metadata=metadata,
                    recent={sid: set() for sid in recent}, graded_activities=activities)
            progress('broad_matching_ready', candidates=len(candidates))
            core_gap, paper_gap, examples, personal = Counter(), Counter(), {}, []
            selection_difficulty = Counter()
            confidence = Counter()
            context_detail = Counter()
            missing_targets = Counter()
            inspected_context = set()
            known = sorted((s for s in normalized['students'] if isinstance(s.get('score_rate'), (int, float))),
                           key=lambda s: (s['score_rate'], s['student_id']))
            tiers = {s['student_id']: min(3, i * 4 // len(known)) for i, s in enumerate(known)}
            randomizer = random.Random(20261002)
            sampled = set()
            for tier in range(4):
                ids = [s['student_id'] for s in known if tiers[s['student_id']] == tier]
                sampled.update(randomizer.sample(ids, min(3, len(ids))))
            all_same_targets = {key: [c for c in candidates if key in c['stable_keys']] for key in
                                {key for points in needs.values() for key in points}}
            allowed = engine._allowed_keys_for_config(broad_config, module.current_knowledge)
            for profile in normalized['students']:
                sid = profile['student_id']
                broad_pool = evaluated['pools'][sid]
                pool = [e for e in broad_pool if match_rejection(e, recent.get(sid, set())) is None]
                selected = weak_only_selection({sid: pool}, config, needs)
                personal.append(member_metrics(selected, sid, needs.get(sid, {}), config.question_count))
                native = engine._choose_practice_entries(pool, config.question_count, config)
                for entry, _ in native:
                    confidence[entry.get('evidence_confidence')] += 1
                    selection_difficulty[engine.standard_difficulty.difficulty_level(entry['candidate']['difficulty'])] += 1
                if sid in sampled:
                    ordered = sorted(native, key=lambda pair: pair[0]['candidate']['difficulty'])
                    inspected = [ordered[0], ordered[-1]] if len(ordered) > 1 else ordered
                    print(json.dumps({'native_level_inspection': tiers[sid], 'questions': [
                        {'question_id': e['candidate']['question_id'], 'question_number': e['candidate']['question_number'],
                         'difficulty': e['candidate']['difficulty'], 'target': module.current_knowledge.node(e['key']).display_name,
                         'student_window': [e['target']['difficulty_plan']['audit_minimum'], e['target']['difficulty_plan']['audit_maximum']],
                         'purpose': e['practice_purpose'], 'confidence': e['evidence_confidence'], 'role': e.get('practice_role')}
                        for e, _ in inspected]}, ensure_ascii=True), flush=True)
                covered = {e['key'] for _, group in selected for e in group if engine._is_core(e)
                           and e.get('practice_purpose') == 'remediation'}
                printed = [e['candidate'] for e, _ in selected]
                for key in needs.get(sid, {}):
                    if key in covered:
                        continue
                    matched = [e for e in broad_pool if e['key'] == key and engine._is_core(e)
                               and e.get('practice_purpose') == 'remediation']
                    current = [e for e in matched if match_rejection(e, recent.get(sid, set())) is None]
                    if current:
                        reasons = {reason for e in current for reason in unselected_rejections(e['candidate'], printed, config)}
                        paper_gap.update(reasons)
                        reason = 'candidate_present_but_not_selected'
                        chosen = min(current, key=lambda e: e['distance'])
                    elif matched:
                        reasons = {match_rejection(e, recent.get(sid, set())) for e in matched}
                        if 'recent_original' in reasons:
                            reason = 'recent_original'
                        elif 'below_student_window' in reasons:
                            reason = 'below_student_window'
                        elif 'above_student_window' in reasons:
                            reason = 'above_student_window'
                        else:
                            reason = 'paper_difficulty_ceiling'
                        core_gap[reason] += 1
                        chosen = min((e for e in matched if match_rejection(e, recent.get(sid, set())) == reason), key=lambda e: e['distance'])
                    else:
                        direct = all_same_targets[key]
                        scoped = [c for c in direct if engine._question_scope_allowed(c, broad_config, allowed, module.current_knowledge)]
                        reason = ('scope_or_incomplete_attribution' if direct and not scoped
                                  else 'target_context_or_task_match' if scoped else 'no_usable_same_target_or_task')
                        core_gap[reason] += 1
                        if reason == 'no_usable_same_target_or_task':
                            missing_targets[key] += 1
                        chosen = None
                        if scoped:
                            target = next(t for t in evaluated['targets'][sid] if t['stable_key'] == key)
                            refs = [module._enrich_source_ref(r, metadata, source_links)
                                    for r in target.get('source_question_refs', [])]
                            losses = engine._loss_refs({**target, 'source_question_refs': refs})
                            sources = [part for r in (losses or refs) for part in r.get('target_facets', [])]
                            index = engine.target_index(module.current_knowledge)
                            direct_matches = [c for c in scoped if any(
                                (m := engine.match_target(key, sources, [part], index))
                                and (m['match_level'] <= 2 or not key.startswith('sk_'))
                                for part in c.get('target_facets', []))]
                            detail = ('no_loss_in_recomputed_mastery' if not losses
                                      else 'source_part_attribution_missing' if not sources
                                      else 'target_missing_from_current_source_parts'
                                      if key.startswith('sk_') and not any(key in part['direct_keys'] for part in sources)
                                      else 'source_candidate_context_mismatch' if not direct_matches
                                      else 'other_candidate_eligibility')
                            context_detail[detail] += 1
                            if detail not in inspected_context:
                                inspected_context.add(detail)
                                plan = original_plan({}, profile.get('score_rate'), 8, target, profile)
                                locators = sorted(scoped, key=lambda c: abs(c['difficulty'] - plan['aim']))[:3]
                                print(json.dumps({'context_inspection': detail,
                                    'target': module.current_knowledge.node(key).display_name,
                                    'student_window': [plan['minimum'], plan['maximum']],
                                    'loss_refs': len(losses), 'source_facets': sources,
                                    'source_locators': [{k: r.get(k) for k in ('session_id', 'question_id', 'bank_question_id')}
                                                       for r in losses],
                                    'candidates': [{'question_id': c['question_id'],
                                        'question_number': c['question_number'], 'difficulty': c['difficulty'],
                                        'facets': c.get('target_facets', [])} for c in locators]},
                                    ensure_ascii=True), flush=True)
                    if reason not in examples and chosen is not None:
                        e = chosen
                        examples[reason] = {'target': module.current_knowledge.node(key).display_name,
                            'candidate_difficulty': e['candidate']['difficulty'],
                            'student_window': [e['target']['difficulty_plan']['audit_minimum'], e['target']['difficulty_plan']['audit_maximum']],
                            'role': e.get('practice_role'), 'confidence': e.get('evidence_confidence'),
                            'paper_constraints': unselected_rejections(e['candidate'], printed, config) if current else []}
                        # Task-requested question locator only; no answer, body or student identity.
                        print(json.dumps({'inspection_reason': reason, 'target': examples[reason]['target'],
                            'question_id': e['candidate']['question_id'], 'question_number': e['candidate']['question_number'],
                            'student_window': examples[reason]['student_window'],
                            'candidate_difficulty': e['candidate']['difficulty'],
                            'tasks': [t['label'] for t in e['target'].get('training_tasks', [])]}, ensure_ascii=True), flush=True)
                        if current:
                            exceeded = engine._paper_skill_limit_exceeded(e['candidate'], printed, config)
                            print(json.dumps({'quota_blockers': [{'question_id': c['question_id'],
                                'question_number': c['question_number'], 'difficulty': c['difficulty'],
                                'skills': [module.current_knowledge.node(k).display_name
                                           for k in set(c['stable_keys']) & exceeded],
                                'covers_requested_target': any(member['key'] == key and engine._is_core(member)
                                    for _, group in selected for member in group
                                    if member['candidate']['question_id'] == c['question_id'])}
                                for c in printed if set(c['stable_keys']) & exceeded]}, ensure_ascii=True), flush=True)
            from question_bank.services.question_skill_index import build_skill_snapshot
            inventory = build_skill_snapshot(bank, bank_path, data_root)
            volume_ids = inventory['volumes'].get(volume, set())
            missing_inventory = []
            for key, count in missing_targets.most_common():
                ids = (inventory['by_skill'].get(key, set()) if key.startswith('sk_') else
                       set().union(*(ids for value, ids in inventory['topics'].items()
                                     if inventory['topic_keys'].get(value) == key))) & volume_ids
                missing_inventory.append({'target': module.current_knowledge.node(key).display_name,
                    'need_pairs': count, 'current_index_same_target_questions': len(ids),
                    'questions_without_current_profile': len(ids & inventory['no_usable']),
                    'valid_same_target_candidates_in_scope': len(all_same_targets.get(key, []))})
                if len(missing_inventory) <= 5 and ids:
                    print(json.dumps({'missing_target_inspection': missing_inventory[-1],
                        'question_locators': [dict(bank.execute('SELECT id,question_number,question_type,difficulty FROM questions WHERE id=?',
                                                (qid,)).fetchone()) for qid in sorted(ids)[:3]]}, ensure_ascii=True), flush=True)
            return {'model_requests': 0, 'database_writes': 0, 'personal': summarize(personal),
                'same_read_versions': read_versions == {name: conn.execute('PRAGMA data_version').fetchone()[0]
                                                        for name, conn in [('grading', grading), ('bank', bank)]},
                'valid_candidates_to_level_10': len(candidates), 'unmatched_need_primary_reasons': dict(core_gap),
                'same_target_unmatched_detail': dict(context_detail),
                'unselected_need_overlapping_constraints': dict(paper_gap), 'examples_without_identity_or_body': examples,
                'missing_target_inventory': missing_inventory,
                'native_selected_level_counts': dict(selection_difficulty), 'native_selected_confidence_counts': dict(confidence),
                'seconds': round(time.perf_counter() - start, 2),
                'interpretation': 'Diagnostic broad matching preserves source and scope checks. Rejection counts are need-pair counts; quota reasons overlap.'}


def audit_exam_evidence(service, resolved, projections, rows, resolver):
    from integration.diagnosis_profile_service import _teacher_step_records
    from question_bank.mastery.model import build_exam_observations
    sessions = sorted(resolved.sessions, key=lambda s: (str(s.get('created_at') or ''), int(s['id'])))
    student_ids = tuple(str(s['id']) for s in resolved.students)
    raw = service.db.results.get_active_assessment_evidence(
        student_ids=student_ids, session_ids=tuple(int(s['id']) for s in sessions))
    observations = build_exam_observations(rows, resolver,
        service.mastery_session_times(exam_scope={'mode': 'semester'}))
    report = []
    for number, session in enumerate(sessions, 1):
        session_id = int(session['id'])
        items = projections[session_id].items
        by_item = {item.item_ref: item for item in items}
        source = [r for r in raw if r['session_id'] == session_id]
        projected = [r for r in rows if r['session_id'] == session_id]
        usable = [r for r in projected if (r.get('assessment') or {}).get('eligible') is not False]
        fine = [r for r in usable if r.get('point_observations')]
        multistep = {item.item_ref for item in items if len(item.steps) > 1}
        multi_usable = [r for r in usable if r['question_id'] in multistep]
        multi_missing = [r for r in multi_usable if not r.get('point_observations')]
        finalized = [r for r in source if r.get('teacher_final_revision') is not None]
        total_only = [r for r in finalized if by_item.get(r['question_id']) is None
                      or _teacher_step_records(by_item[r['question_id']], r) is None]
        teacher_steps = len(finalized) - len(total_only)
        observed = [o for o in observations if o.get('session') == session_id]
        report.append({'exam': 'E' + str(number),
            'scored_students': len({r['student_id'] for r in source}),
            'rubric_items': len(items), 'graph_covered_items': sum(i.is_graph_eligible for i in items),
            'unmapped_item_reasons': dict(Counter(i.missing_reason for i in items if not i.is_graph_eligible)),
            'raw_rows': len(source), 'projected_rows': len(projected), 'eligible_rows': len(usable),
            'excluded_reasons': dict(Counter(r['assessment'].get('reason') for r in projected
                                           if r['assessment'].get('eligible') is False)),
            'eligible_rows_with_points': len(fine),
            'eligible_rows_without_points': len(usable) - len(fine),
            'single_step_items': sum(len(i.steps) == 1 for i in items),
            'multi_step_items': len(multistep),
            'multi_step_eligible_rows': len(multi_usable),
            'multi_step_eligible_rows_with_points': len(multi_usable) - len(multi_missing),
            'multi_step_eligible_rows_without_points': len(multi_missing),
            'multi_step_loss_rows_without_points': sum(float(r.get('score_awarded') or 0) < float(r.get('full_score') or 0)
                                                      for r in multi_missing),
            'multi_step_partial_rows_without_points': sum(0 < float(r.get('score_awarded') or 0) < float(r.get('full_score') or 0)
                                                         for r in multi_missing),
            'multi_step_teacher_total_only_rows': sum(r['question_id'] in multistep for r in total_only),
            'eligible_skill_rows': sum(any(k.startswith('sk_') for k in r['question_tags']['knowledge_point']) for r in usable),
            'teacher_final_rows': len(finalized), 'valid_teacher_step_rows': teacher_steps,
            'effective_single_result_rows': sum(bool((r.get('source_practice_metadata') or {}).get('is_single_result')) for r in usable),
            'teacher_single_result_rows': sum(r['assessment'].get('reason') == 'teacher_final_single_result' for r in usable),
            'coarse_part_total_rows': sum(r['assessment'].get('reason') == 'part_total_without_step_attribution' for r in usable),
            'teacher_total_only_rows': len(finalized) - teacher_steps,
            'superseded_ai_step_rows': sum(bool((r.get('assessment_state') or {}).get('step_assessments'))
                and _teacher_step_records(by_item[r['question_id']], r) is None
                for r in finalized if r['question_id'] in by_item),
            'observations': len(observed),
            'point_observations': sum(len(o['item']) == 3 and not str(o['item'][2]).startswith('t:') for o in observed),
            'skill_observations': sum(any(k.startswith('sk_') for k in o['links']) for o in observed),
            'skill_students': len({o['student'] for o in observed if any(k.startswith('sk_') for k in o['links'])}),
            'known_difficulty_rows': sum(r['assessment'].get('part_difficulty') is not None for r in usable)})
    return report


def run_evidence_loss(data_root: Path, volume: str):
    """Measure observed granularity and paired sensitivity, not missing-data truth."""
    from integration.evidence_scope import EvidenceScopeResolver
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.mastery.model import build_exam_observations
    start = time.perf_counter()
    grading_path = data_root / 'databases/grading_system.db'
    bank_path = data_root / 'databases/question_bank.db'
    scope = {'mode': 'semester', 'curriculum_volume_id': volume}
    report = {'interpretation': 'Evidence completeness and sensitivity; no reconstructed step truth or causal loss estimate.',
        'volume': volume, 'model_requests': 0, 'database_writes': 0, 'raw_student_exports': 0,
        'conditions': [], 'fixed': ['scores', 'student population', 'candidate bank', 'standard', 'parameters',
                                  'recent actual participation', 'paper configuration'],
        'pending': ['independent teacher reconstruction of missing step facts',
                    'candidate/paper suitability for changed group compositions']}
    with readonly_runtime() as connect_ro:
        with connect_ro(grading_path) as grading, connect_ro(bank_path) as bank:
            before = {name: conn.execute('PRAGMA data_version').fetchone()[0]
                      for name, conn in [('grading', grading), ('bank', bank)]}
            service = DiagnosisProfileService(grading_path, bank_path,
                grading_db=open_grading_repositories(grading_path, external_connection=grading),
                question_bank_connection=bank, data_root=data_root, persist_snapshots=False)
            resolved = EvidenceScopeResolver(service.db).resolve(
                scope={'mode': 'all', 'use_historical_fallback': False}, exam_scope=scope)
            sessions = sorted(resolved.sessions, key=lambda s: (str(s.get('created_at') or ''), int(s['id'])))
            session_ids = tuple(int(s['id']) for s in sessions)
            student_ids = tuple(str(s['id']) for s in resolved.students)
            projections = service._tag_projections(session_ids)
            original_rows = service._projected_tag_evidence(student_ids=student_ids,
                session_ids=session_ids, projection_by_session=projections)
            resolver = engine.CurrentKnowledgeResolver.from_active_database(bank_path)
            report['exams'] = audit_exam_evidence(service, resolved, projections, original_rows, resolver)
            progress('evidence_inventory_ready', exams=len(sessions), rows=len(original_rows))
            relations = tuple({'relation_type': r.relation_type, 'source_key': r.source_key,
                               'target_key': r.target_key} for r in resolver.relations)
            scope_keys = tuple(f'kp_{volume.replace("-", "_")}_{c}' for c in (1, 2))
            config = engine.PersonalizedRecommendationConfig(scope_keys=scope_keys, curriculum_volume_id=volume)
            owned = engine._scope_descendants(frozenset(scope_keys), resolver)
            activities = service.graded_activities(student_ids)
            source_snapshot = None
            baseline = None
            clock = datetime.now(UTC)
            for mode in ('current', 'coarsened_steps', 'without_total_only', 'steps_only', 'without_earliest', 'effective_results_only'):
                rows = evidence_view(original_rows, mode, session_ids[0] if session_ids else None)
                observations = build_exam_observations(rows, resolver, service.mastery_session_times(exam_scope=scope))
                calculator = CurrentMasteryCalculator(bank_path, resolver, data_root=data_root, clock=lambda: clock)
                values = calculator.calculate({'exam_scope': scope, '_mastery_observations': observations,
                    '_mastery_session_times': service.mastery_session_times(exam_scope=scope)})
                progress('evidence_mastery_ready', condition=mode, observations=len(observations))
                # Per-condition supplied observations bypass persistent and shared caches.
                with patch.object(service, '_projected_tag_evidence', return_value=rows), \
                     patch.object(service, '_tag_projections', return_value=projections), \
                     patch.object(service, 'semester_mastery', return_value=values):
                    diagnosis, _ = service._compute_tag_profiles(
                        scope={'mode': 'all', 'use_historical_fallback': False}, exam_scope=scope)
                normalized = engine._normalize_diagnosis(diagnosis)
                module = engine.PersonalizedRecommendationModule(db_path=bank_path,
                    data_root=data_root, semester_mastery=lambda *a, **kw: values)
                mastery = module._mastery_snapshot(normalized)
                leaves = engine._scope_leaves(scope_keys, diagnosis=normalized, relations=relations)
                group_needs = engine._group_needs(normalized, leaves, cap=config.difficulty_max)
                needs = {sid: {key: value for key, value in points.items() if key in owned}
                         for sid, points in group_needs.items()}
                if source_snapshot is None:
                    source_snapshot = module._source_snapshot(knowledge_keys=leaves, candidate_config=config)
                    fixed_leaves = leaves
                if set(leaves) != set(fixed_leaves):
                    raise RuntimeError('A sensitivity condition changed the candidate scope')
                candidates, _, _ = source_snapshot
                recent = module._recent_question_ids(student_ids, diagnosis=normalized,
                    graded_activities=activities, recent_activity_count=config.recent_activity_count)
                metadata = module._source_practice_metadata(normalized)
                memo = {}
                evaluated = module.evaluate_candidates(diagnosis=normalized, config=config,
                    candidates=candidates, mastery=mastery, source_metadata=metadata,
                    recent=recent, graded_activities=activities, evaluation_memo=memo)
                progress('evidence_personal_pool_ready', condition=mode, candidates=len(candidates))
                selections, native_selections, metrics = {}, {}, []
                gap_counts = Counter()
                for profile in normalized['students']:
                    sid = profile['student_id']
                    pool = evaluated['pools'][sid]
                    selected = weak_only_selection({sid: pool}, config, needs)
                    selections[sid] = {e['candidate']['question_id'] for e, _ in selected}
                    native = engine._choose_practice_entries(pool, config.question_count, config)
                    native_selections[sid] = {e['candidate']['question_id'] for e, _ in native}
                    metrics.append(member_metrics(selected, sid, needs.get(sid, {}), config.question_count))
                    covered = {e['key'] for _, entries in selected for e in entries if engine._is_core(e)
                               and e.get('practice_purpose') == 'remediation'}
                    available = {e['key'] for e in pool if engine._is_core(e) and e.get('practice_purpose') == 'remediation'}
                    for key in needs.get(sid, {}):
                        if key not in available:
                            gap_counts['no_matched_candidate'] += 1
                        elif key not in covered:
                            gap_counts['candidate_present_but_not_selected'] += 1
                need_ids = {(sid, key) for sid, points in needs.items() for key in points}
                need_plans = {(sid, key): point['difficulty_plan']['aim']
                              for sid, points in needs.items() for key, point in points.items()}
                point_loss_pairs = set()
                for sid, points in needs.items():
                    for key, point in points.items():
                        for ref in point['source_question_refs']:
                            a = ref.get('assessment') or {}
                            if (a.get('eligible') is not False and a.get('point_observations')
                                and float(ref.get('full_score') or 0) > float(ref.get('score_awarded') or 0)):
                                point_loss_pairs.add((sid, key))
                # Reuse the native composition step. Availability/ready flags require
                # a separate common candidate evaluation, not inferred from membership.
                groups_as_sets = {frozenset(group) for group in engine._chapter_group_members(group_needs)}
                group_mates = {sid: group for group in groups_as_sets for sid in group}
                direct_pairs = {(sid, key) for (sid, key), value in values.items()
                                if key in owned and value.direct_evidence_count > 0}
                condition = {'condition': mode, 'input_rows': len(rows), 'observations': len(observations),
                    'valid_candidates': len(candidates), 'personal': summarize(metrics), 'gaps': dict(gap_counts),
                    'need_pairs_with_point_loss_support': len(point_loss_pairs),
                    'need_pairs_without_point_loss_support': len(need_ids - point_loss_pairs),
                    'formed_groups': len(groups_as_sets), 'group_sizes': sorted(len(g) for g in groups_as_sets),
                    'grouped_students': len(group_mates),
                    'direct_target_pairs': len(direct_pairs)}
                if baseline is None:
                    baseline = {'needs': need_ids, 'plans': need_plans, 'selections': selections,
                                'native': native_selections, 'values': values, 'pairs': direct_pairs,
                                'mates': group_mates}
                else:
                    pairs = baseline['pairs']
                    common_needs = baseline['needs'] & need_ids
                    condition['change_from_current'] = {
                        'removed_need_pairs': len(baseline['needs'] - need_ids), 'added_need_pairs': len(need_ids - baseline['needs']),
                        'needs_aim_changed_at_least_half': sum(abs(baseline['plans'][p] - need_plans[p]) >= .5 for p in common_needs),
                        'direct_pairs_losing_evidence': sum(p not in direct_pairs for p in pairs),
                        'tier_changes_on_baseline_direct_pairs': sum((baseline['values'][p].tier != values[p].tier)
                            if p in values else baseline['values'][p].tier != 'insufficient' for p in pairs),
                        'mean_absolute_mastery_change': round(mean(abs(baseline['values'][p].value - values[p].value)
                            for p in pairs if p in values), 4) if pairs and values else None,
                        'weak_only_selection': selection_change(baseline['selections'], selections),
                        'native_selection': selection_change(baseline['native'], native_selections),
                        'students_with_changed_group_mates': sum(baseline['mates'].get(s) != group_mates.get(s) for s in student_ids)}
                report['conditions'].append(condition)
                progress('evidence_condition_done', condition=mode, needs=len(need_ids),
                         seconds=round(time.perf_counter() - start, 2))
            report['same_read_versions'] = before == {name: conn.execute('PRAGMA data_version').fetchone()[0]
                for name, conn in [('grading', grading), ('bank', bank)]}
    report['seconds'] = round(time.perf_counter() - start, 2)
    return report


def run(data_root: Path, volume: str, chapters: tuple[int, ...], seed: int):
    start = time.perf_counter()
    grading_path = data_root / 'databases/grading_system.db'
    bank_path = data_root / 'databases/question_bank.db'
    report = {'interpretation': 'Current matching/selection audit, not teacher gold labels or learning efficacy.',
              'volume': volume, 'chapters': [], 'seed': seed,
              'coverage_definition': 'Distinct current-standard skill/topic identities linked to actual direct losses; not independent teacher task coverage.',
              'pending': ['teacher blind assessment', 'independent training outcomes'],
              'model_requests': 0, 'database_writes': 0, 'raw_student_exports': 0}
    with readonly_runtime() as connect_ro:
        with connect_ro(grading_path) as grading, connect_ro(bank_path) as bank:
            before = {name: conn.execute('PRAGMA data_version').fetchone()[0]
                      for name, conn in [('grading', grading), ('bank', bank)]}
            service = DiagnosisProfileService(grading_path, bank_path,
                grading_db=open_grading_repositories(grading_path, external_connection=grading),
                question_bank_connection=bank, data_root=data_root, persist_snapshots=False)
            # Recompute for this process rather than consume a stored profile.
            with patch.object(service, '_read_local_profile', return_value=None):
                diagnosis = service.build_profiles(scope={'mode': 'all', 'use_historical_fallback': False},
                    exam_scope={'mode': 'semester', 'curriculum_volume_id': volume})
            module = engine.PersonalizedRecommendationModule(db_path=bank_path,
                data_root=data_root, semester_mastery=service.semester_mastery)
            normalized = engine._normalize_diagnosis(diagnosis)
            mastery = module._mastery_snapshot(normalized)
            students = normalized['students']
            progress('diagnosis_ready', students=len(students), seconds=round(time.perf_counter()-start, 2))
            known = sorted((s for s in students if isinstance(s.get('score_rate'), (int, float))),
                           key=lambda s: (s['score_rate'], s['student_id']))
            tiers = {s['student_id']: min(3, i*4//len(known)) for i, s in enumerate(known)}
            sampled = set()
            randomizer = random.Random(seed)
            for tier in range(4):
                ids = [s['student_id'] for s in known if tiers[s['student_id']] == tier]
                sampled.update(randomizer.sample(ids, min(3, len(ids))))
            activities = service.graded_activities(tuple(s['student_id'] for s in students))
            report.update(students=len(students), students_with_score=len(known), sample_students=len(sampled),
                          engine_version=engine.ENGINE_VERSION, grouping_version=engine.GROUPING_VERSION,
                          knowledge_release=module.current_knowledge.release_id)
            for chapter_numbers in [(c,) for c in chapters] + [chapters]:
                chapter = chapter_numbers[0] if len(chapter_numbers) == 1 else '+'.join(map(str,chapter_numbers))
                scope_keys = tuple(f'kp_{volume.replace("-", "_")}_{c}' for c in chapter_numbers)
                nodes = [module.current_knowledge.node(key) for key in scope_keys]
                if not all(nodes):
                    raise ValueError('The requested chapter is absent from the current standard')
                config = engine.PersonalizedRecommendationConfig(scope_keys=scope_keys, curriculum_volume_id=volume,
                    remediation_only=True)
                leaves = engine._scope_leaves(scope_keys, diagnosis=normalized,
                    relations=tuple({'relation_type': r.relation_type, 'source_key': r.source_key,
                                     'target_key': r.target_key} for r in module.current_knowledge.relations))
                owned = engine._scope_descendants(frozenset(scope_keys), module.current_knowledge)
                needs = {sid: {key: value for key,value in points.items() if key in owned}
                         for sid,points in engine._group_needs(normalized, leaves, cap=config.difficulty_max).items()}
                candidates, relations, _ = module._source_snapshot(knowledge_keys=leaves, candidate_config=config)
                progress('candidate_pool_ready', chapter=chapter, candidates=len(candidates))
                recent = module._recent_question_ids(tuple(s['student_id'] for s in students),
                    diagnosis=normalized, graded_activities=activities, recent_activity_count=3)
                metadata = module._source_practice_metadata(normalized)
                memo = {}
                evaluated = module.evaluate_candidates(diagnosis=normalized, config=config,
                    candidates=candidates, mastery=mastery, source_metadata=metadata,
                    recent=recent, graded_activities=activities, evaluation_memo=memo)
                native_rows, strict_rows, per_student = [], [], {}
                gap_counts = Counter()
                skills = Counter()
                for profile in students:
                    sid = profile['student_id']
                    pool = evaluated['pools'][sid]
                    native = engine._choose_practice_entries(pool, config.question_count, config)
                    strict = weak_only_selection({sid: pool}, config, needs)
                    needed = needs.get(sid, {})
                    native_metrics = member_metrics(native, sid, needed, config.question_count)
                    strict_metrics = member_metrics(strict, sid, needed, config.question_count)
                    native_rows.append((sid, native_metrics)); strict_rows.append((sid, strict_metrics))
                    per_student[sid] = (native, strict, strict_metrics)
                    candidate_covered = {e['key'] for e in pool if engine._is_core(e)
                                         and e.get('practice_purpose') == 'remediation'}
                    selected_covered = {e['key'] for _, entries in strict for e in entries
                                       if engine._is_core(e) and e.get('practice_purpose') == 'remediation'}
                    for key in needed:
                        if key not in candidate_covered:
                            gap_counts['no_matched_candidate'] += 1
                        elif key not in selected_covered:
                            gap_counts['candidate_present_but_not_selected'] += 1
                        skills[key] += 1
                parity_draft = module._build_draft(diagnosis=deepcopy(normalized), config=config,
                    candidates=candidates, relations=relations, mastery=mastery, recent=recent, excluded_question_ids=set())
                parity = all({item['question_id'] for item in p['items']} ==
                             {e['candidate']['question_id'] for e,_ in per_student[p['student_id']][0]}
                             for p in parity_draft['students'])
                if not parity:
                    raise RuntimeError('Experimental native selection differs from the current draft builder')
                chapter_report = {'chapter': chapter, 'name': ' + '.join(node.display_name for node in nodes),
                    'native_draft_parity': parity, 'config': config.to_dict(),
                    'associated_targets_outside_chapters': len(set(leaves)-owned),
                    'valid_candidates': len(candidates), 'native': summarize([r for _, r in native_rows]),
                    'weak_only': summarize([r for _, r in strict_rows]), 'gap_counts': dict(gap_counts),
                    'by_tier': [{ 'tier': ('low','lower_middle','upper_middle','high')[tier],
                        'native': summarize([r for sid,r in native_rows if tiers.get(sid)==tier]),
                        'weak_only': summarize([r for sid,r in strict_rows if tiers.get(sid)==tier]),
                        'sample': summarize([r for sid,r in strict_rows if tiers.get(sid)==tier and sid in sampled])}
                        for tier in range(4)],
                    'need_distribution': [{'target': module.current_knowledge.node(k).display_name,
                        'kind': 'skill' if k.startswith('sk_') else 'topic', 'students': n}
                        for k,n in skills.most_common()], 'groups': []}
                grouping = module.chapter_groups(diagnosis=diagnosis, config=replace(config, paper_mode='shared',
                    group_scope_keys=scope_keys, remediation_only=False),
                    graded_activities=activities)
                chapter_report['grouping_summary'] = grouping.get('summary')
                chapter_report['students_with_owned_needs'] = sum(bool(v) for v in needs.values())
                chapter_report['unassigned_reason_counts'] = dict(Counter(u['reason_kind'] for u in grouping.get('unassigned', [])))
                for group in grouping.get('groups', []):
                    member_ids = [m['student_id'] for m in group['members']]
                    shared_config = replace(config, paper_mode='shared',
                        target_keys=tuple(t['knowledge_key'] for t in group['targets']))
                    scoped = {**normalized, 'students': [s for s in students if s['student_id'] in member_ids]}
                    shared_eval = module.evaluate_candidates(diagnosis=scoped, config=shared_config,
                        candidates=candidates, mastery=mastery, source_metadata=metadata, recent=recent,
                        graded_activities=activities, evaluation_memo=memo)
                    common = engine._common_entries([e for pool in shared_eval['pools'].values() for e in pool], member_ids)
                    native = engine._choose_practice_entries(common, config.question_count, shared_config)
                    strict = weak_only_selection(shared_eval['pools'], shared_config, needs)
                    rows = [member_metrics(strict,sid,needs.get(sid,{}),config.question_count) for sid in member_ids]
                    losses = [per_student[sid][2]['coverage'] - r['coverage']
                              for sid,r in zip(member_ids,rows) if r['coverage'] is not None
                              and per_student[sid][2]['coverage'] is not None]
                    chapter_report['groups'].append({'members':len(member_ids), 'ready':group['ready'],
                        'native_questions':len(native), 'weak_only_questions':len(strict),
                        'member_summary':summarize(rows),
                        'max_coverage_drop_from_personal':round(max(losses),4) if losses else None,
                        'members_losing_over_20pp':sum(d>.2+1e-9 for d in losses),
                        'tier_counts':dict(Counter(str(tiers.get(sid,'unknown')) for sid in member_ids))})
                report['chapters'].append(chapter_report)
                progress('chapter_done', chapter=chapter, groups=len(chapter_report['groups']),
                         seconds=round(time.perf_counter()-start,2))
            report['same_read_versions'] = before == {name: conn.execute('PRAGMA data_version').fetchone()[0]
                for name,conn in [('grading',grading),('bank',bank)]}
    report['seconds'] = round(time.perf_counter()-start,2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=ROOT/'user_data')
    parser.add_argument('--volume', default='bnu24-math-g8-upper')
    parser.add_argument('--seed', type=int, default=20261002)
    parser.add_argument('--evidence-loss', action='store_true', help='Audit evidence granularity and paired sensitivity')
    parser.add_argument('--match-audit', action='store_true', help='Trace matching and selection rejection gates')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Only anonymous aggregates may leave the in-memory evaluation.
    report = (run_match_audit(args.data_root.resolve(), args.volume) if args.match_audit
              else run_evidence_loss(args.data_root.resolve(), args.volume) if args.evidence_loss
              else run(args.data_root.resolve(), args.volume, (1,2), args.seed))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    progress('report_saved', sections=len(report.get('chapters', report.get('conditions', []))), seconds=report['seconds'])


if __name__ == '__main__':
    main()
