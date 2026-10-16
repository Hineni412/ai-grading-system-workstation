"""Semester mastery overview, using backend posterior tiers and observed students."""

from __future__ import annotations

from collections.abc import Mapping
from collections import defaultdict
import pickle
import re
import sqlite3
from typing import Any

from integration.result_cache import ResultCache
from question_bank.question_types import (
    chapter_target_kind, chapter_target_kinds, target_kind_for_key,
)
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
from question_bank.current_knowledge import CurrentKnowledgeResolver, CurrentKnowledgeUnavailable
from question_bank.recommendation.target_matching import knowledge_skill_associations, load_question_facets

_TIER_FIELDS = ("weak", "unsteady", "stable", "insufficient")
_ITEM_KINDS = ("topic", "skill")
_QUESTION_LABEL = re.compile(r"^[Qq]")
_PART_SUFFIX = re.compile(r"\(P(\d+)\)")


def _has_evidence(entry):
    return entry is not None and entry.get("mastery") is not None and int(entry.get("evidence_count") or 0) > 0


def _tier(entry):
    return entry.get("tier") if entry.get("tier") in _TIER_FIELDS else "insufficient"


def _printed_label(question_id: object) -> str:
    """``Q10(P2)`` -> ``10(2)``, matching the printed question number."""
    text = _QUESTION_LABEL.sub("", str(question_id or "").strip())
    return _PART_SUFFIX.sub(r"(\1)", text)


def _exam_refs(point: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """Non-training source refs of one student's weak point."""
    if not isinstance(point, Mapping):
        return []
    return [
        ref
        for ref in point.get("source_question_refs") or ()
        if isinstance(ref, Mapping) and ref.get("source_kind") != "training"
    ]


def _exam_question_key(ref):
    bank_id = int(ref.get("bank_question_id") or 0)
    root_id = _PART_SUFFIX.sub("", str(ref.get("question_id") or ""))
    return int(ref.get("session_id") or 0), str(bank_id) if bank_id else root_id


def _exam_question_totals(
    students: list[Mapping[str, Any]],
    weak_points_by_student: Mapping[str, Mapping[str, Mapping[str, Any]]],
):
    totals: dict[tuple[int, str], dict[str, Any]] = {}
    for student in students:
        student_id = str(student.get("student_id") or "")
        points = weak_points_by_student.get(student_id, {}).values()
        seen: set[tuple[int, str]] = set()
        for ref in (ref for point in points for ref in _exam_refs(point)):
            assessment = ref.get("assessment") or {}
            if (
                assessment.get("eligible") is False
                or float(assessment.get("evidence_weight", 1.0) or 0) < 0.999
            ):
                continue
            if ref.get("combined_group_id"):
                # 合并给分成员的得分率只按组展示，不进入本场原题示例。
                continue
            try:
                session_id = int(ref.get("session_id") or 0)
                score = float(ref.get("question_score_awarded", ref.get("score_awarded")))
                full = float(ref.get("question_full_score", ref.get("full_score")))
            except (TypeError, ValueError):
                continue
            question_id = str(ref.get("question_id") or "")
            if not session_id or not question_id or full <= 0:
                continue
            pair = (session_id, question_id)
            if pair in seen:
                continue
            seen.add(pair)
            total_key = _exam_question_key(ref)
            bucket = totals.get(total_key)
            if bucket is None:
                bucket = totals[total_key] = {
                    "session_id": session_id,
                    "session_name": str(ref.get("session_name") or ""),
                    "question_label": _printed_label(_PART_SUFFIX.sub("", question_id)),
                    "bank_question_id": int(ref.get("bank_question_id") or 0),
                    "score": 0.0,
                    "full": 0.0,
                }
            bucket["score"] += score
            bucket["full"] += full
    return totals


def _type_exam_questions(
    key: str,
    students: list[Mapping[str, Any]],
    weak_points_by_student: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *, question_totals=None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    totals = question_totals if question_totals is not None else _exam_question_totals(students, weak_points_by_student)
    relevant = {
        _exam_question_key(ref)
        for student in students
        for ref in _exam_refs(weak_points_by_student.get(str(student.get("student_id") or ""), {}).get(key))
    }
    ranked = sorted(
        (item for pair, item in totals.items() if pair in relevant),
        key=lambda item: (
            item["score"] / item["full"],
            item["bank_question_id"] or 1 << 60,
            item["question_label"],
        ),
    )
    typical = None
    others: list[dict[str, Any]] = []
    for index, item in enumerate(ranked):
        entry = {
            "session_id": item["session_id"],
            "session_name": item["session_name"],
            "question_label": item["question_label"],
            "class_rate": round(item["score"] / item["full"], 4),
            "bank_question_id": item["bank_question_id"],
        }
        if index == 0:
            typical = entry
        else:
            others.append(entry)
    return typical, others


def build_mastery_overview(
    diagnosis: Mapping[str, Any], *, volume_id: str, associations=None, resolver=None
) -> dict[str, Any]:
    """Build the overview payload for one curriculum volume.

    ``diagnosis`` is the dict returned by
    ``DiagnosisProfileService.build_profiles``. ``volume_id`` must resolve to a
    bundled curriculum volume; an empty or unknown id raises ``ValueError``.

    ``resolver`` is the active knowledge release when available; a release
    with question-type nodes switches the item column from knowledge points
    and skills to question types, carrying ``typical_question`` details.
    """

    clean_id = str(volume_id or "").strip()
    if not clean_id:
        raise ValueError("overview requires a curriculum volume")
    volume = curriculum_volume(volume_id=clean_id)
    if volume is None:
        raise ValueError("unknown curriculum volume")

    stored_volume = str((diagnosis.get("exam_scope") or {}).get("curriculum_volume_id") or "").strip()
    target_kind = (chapter_target_kind(resolver, clean_id) if resolver is not None
                   else str(diagnosis.get("target_kind") or "skill")
                   if not stored_volume or stored_volume == clean_id else "skill")
    chapter_kinds = (chapter_target_kinds(resolver, clean_id) if resolver is not None
                     else dict(diagnosis.get("chapter_target_kinds") or
                               {str(chapter["knowledge_id"]): target_kind for chapter in volume["chapters"]}))
    type_mode = target_kind == "type"
    has_types = type_mode or "type" in chapter_kinds.values()
    item_kinds = ("type",) if type_mode else ("topic",) if target_kind == "knowledge" else ("topic", "skill", "type") if target_kind == "mixed" else _ITEM_KINDS

    catalog: dict[str, Mapping[str, Any]] = {}
    for item in diagnosis.get("knowledge_catalog") or []:
        if isinstance(item, Mapping) and item.get("knowledge_key"):
            catalog[str(item["knowledge_key"])] = item

    # Node set: chapter → section → item nodes anchored to the section. Item
    # anchoring mirrors target_index: the parent relation lands on a section
    # or on a topic whose section owns the node. In type mode the item column
    # is question types only; topic/skill catalog nodes stay out of the set.
    section_keys: set[str] = set()
    chapter_by_section: dict[str, str] = {}
    topic_anchor: dict[str, tuple[str, str]] = {}
    for chapter in volume["chapters"]:
        chapter_key = str(chapter["knowledge_id"])
        for section in chapter["sections"]:
            section_key = str(section["knowledge_id"])
            section_keys.add(section_key)
            chapter_by_section[section_key] = chapter_key
            for point in section["knowledge_points"]:
                topic_anchor[str(point["id"])] = (chapter_key, section_key)

    items_by_section: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for key, item in catalog.items():
        # Topics always come from the volume's knowledge_points; only the
        # training-target kind (skill or type) is anchored through parents.
        if item.get("node_kind") not in {"skill", "type"}:
            continue
        parent = str(item.get("parent_knowledge_key") or "")
        if parent in section_keys:
            anchor = parent
        elif parent in topic_anchor:
            anchor = topic_anchor[parent][1]
        else:
            continue
        chapter_kind = chapter_kinds.get(chapter_by_section[anchor], target_kind)
        if item.get("node_kind") != ("type" if chapter_kind == "type" else "skill") or chapter_kind == "knowledge":
            continue
        items_by_section.setdefault(anchor, []).append((key, item))
    for anchored in items_by_section.values():
        anchored.sort(key=lambda pair: pair[0])

    node_specs: list[tuple[str, str, str, str]] = []
    for chapter in volume["chapters"]:
        chapter_key = str(chapter["knowledge_id"])
        if chapter_key in catalog:
            node_specs.append((chapter_key, "chapter", chapter_key, ""))
        for section in chapter["sections"]:
            section_key = str(section["knowledge_id"])
            if section_key in catalog:
                node_specs.append(
                    (section_key, "section", chapter_key, section_key)
                )
            if chapter_kinds.get(chapter_key, target_kind) != "type":
                for point in section["knowledge_points"]:
                    point_key = str(point["id"])
                    if point_key in catalog:
                        node_specs.append(
                            (point_key, "topic", chapter_key, section_key)
                        )
            for item_key, _item in items_by_section.get(section_key, []):
                node_specs.append(
                    (item_key, str(_item.get("node_kind") or "skill"), chapter_key, section_key)
                )

    group_mastery_by_key = {
        str(item["knowledge_key"]): item
        for item in diagnosis.get("group_weak_points") or []
        if isinstance(item, Mapping) and item.get("knowledge_key")
    }

    students = [
        student
        for student in diagnosis.get("students") or []
        if isinstance(student, Mapping)
    ]
    weak_points_by_student: dict[str, dict[str, Mapping[str, Any]]] = {}
    for student in students:
        index: dict[str, Mapping[str, Any]] = {}
        for point in student.get("weak_points") or []:
            if isinstance(point, Mapping) and point.get("knowledge_key"):
                index[str(point["knowledge_key"])] = point
        weak_points_by_student[str(student.get("student_id") or "")] = index

    question_totals = _exam_question_totals(students, weak_points_by_student) if has_types else {}
    node_keys = [spec[0] for spec in node_specs]
    topic_keys = [key for key, kind, _c, _s in node_specs if kind == "topic"]
    skill_keys = [key for key, kind, _c, _s in node_specs if kind == "skill"]
    type_keys = [key for key, kind, _c, _s in node_specs if kind == "type"]
    in_volume_keys = set(node_keys)
    for key, item in catalog.items():
        kind = item.get("node_kind")
        if key in in_volume_keys or kind not in item_kinds:
            continue
        if not any(_has_evidence(index.get(key)) for index in weak_points_by_student.values()):
            continue
        chapter_key = section_key = ""
        parent = str(item.get("parent_knowledge_key") or "")
        seen = {key}
        while parent in catalog and parent not in seen:
            seen.add(parent)
            ancestor = catalog[parent]
            if ancestor.get("node_kind") == "section":
                section_key = parent
            if ancestor.get("node_kind") == "chapter":
                chapter_key = parent
            parent = str(ancestor.get("parent_knowledge_key") or "")
        node_specs.append((key, kind, chapter_key, section_key))

    nodes: list[dict[str, Any]] = []
    for key, kind, chapter_key, section_key in node_specs:
        distribution = {name: 0 for name in _TIER_FIELDS}
        evidenced: list[dict[str, Any]] = []
        for student in students:
            student_id = str(student.get("student_id") or "")
            entry = weak_points_by_student.get(student_id, {}).get(key)
            if not _has_evidence(entry):
                continue
            mastery = float(entry["mastery"])
            distribution[_tier(entry)] += 1
            evidenced.append({"student_id": student_id, "mastery": mastery, "tier": _tier(entry),
                "observation_count": int(entry.get("observation_count") or entry.get("evidence_count") or 0),
                "full_correct_count": int(entry.get("full_correct_count") or 0),
                **{field: entry.get(field) for field in ("interval_low", "interval_high", "recent_trend")}})
        evidenced.sort(key=lambda item: (item["mastery"], item["student_id"]))
        node = {
            "knowledge_key": key,
            "display_name": str(catalog[key].get("knowledge_point") or key),
            "kind": kind,
            "chapter_key": chapter_key,
            "section_key": section_key,
            "target_kind": (chapter_kinds[chapter_key] if chapter_key in chapter_kinds
                            else target_kind_for_key(key, resolver, clean_id)),
            "definition": str(catalog[key].get("definition") or ""),
            "in_volume": key in in_volume_keys,
            "group_mastery": (group_mastery_by_key.get(key) or {}).get("mastery"),
            "group_interval_low": (group_mastery_by_key.get(key) or {}).get("interval_low"),
            "group_interval_high": (group_mastery_by_key.get(key) or {}).get("interval_high"),
            "tier": (group_mastery_by_key.get(key) or {}).get("tier", "insufficient"),
            "evidence_student_count": len(evidenced),
            "distribution": distribution,
            "students": evidenced,
        }
        if kind == "type":
            node["typical_question"], node["other_questions"] = (
                _type_exam_questions(key, students, weak_points_by_student, question_totals=question_totals)
            )
        nodes.append(node)

    def _count_tiers(keys: list[str], index: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
        counts = {"weak": 0, "unsteady": 0, "stable": 0, "insufficient": 0, "evidence": 0}
        for key in keys:
            entry = index.get(key)
            if not _has_evidence(entry):
                continue
            counts["evidence"] += 1
            counts[_tier(entry)] += 1
        return counts

    student_rows: list[dict[str, Any]] = []
    for student in students:
        student_id = str(student.get("student_id") or "")
        index = weak_points_by_student.get(student_id, {})
        score_rate = student.get("score_rate")
        row = {
            "student_id": student_id,
            "student_code": str(student.get("student_code") or ""),
            "student_name": str(student.get("student_name") or ""),
            "class_id": str(student.get("class_id") or ""),
            "score_rate": score_rate if score_rate is not None else None,
            "score_rate_source": str(
                student.get("score_rate_source") or "none"
            ),
            "topics": _count_tiers(topic_keys, index),
            "skills": _count_tiers(skill_keys, index),
        }
        if has_types:
            row["types"] = _count_tiers(type_keys, index)
        student_rows.append(row)

    evidenced_student_ids = {
        student_id
        for node in nodes
        if node["in_volume"]
        for student_id in (
            entry["student_id"] for entry in node["students"]
        )
    }
    score_rates = [
        float(student["score_rate"])
        for student in students
        if student.get("score_rate") is not None
    ]
    summary = {
        "student_count": len(students),
        "evidence_student_count": len(evidenced_student_ids),
        "exam_student_count": len(score_rates),
        "exam_score_rate": (
            round(sum(score_rates) / len(score_rates), 4)
            if score_rates
            else None
        ),
        "topic_count": len(topic_keys),
        "skill_count": len(skill_keys),
        "weak_topic_count": sum(
            1
            for node in nodes
            if node["kind"] == "topic"
            and node["in_volume"]
            and node["distribution"]["weak"] > 0
        ),
        "weak_skill_count": sum(
            1
            for node in nodes
            if node["kind"] == "skill"
            and node["in_volume"]
            and node["distribution"]["weak"] > 0
        ),
    }
    summary["target_count"] = len(topic_keys) + len(skill_keys) + len(type_keys)
    if has_types:
        summary["type_count"] = len(type_keys)
        summary["weak_type_count"] = sum(
            1
            for node in nodes
            if node["kind"] == "type"
            and node["in_volume"]
            and node["distribution"]["weak"] > 0
        )

    returned_keys = {node["knowledge_key"] for node in nodes}
    # Type nodes have no knowledge-skill associations; association lines are
    # a skill-mode concept.
    links = [] if type_mode else (
        associations
        if associations is not None
        else diagnosis.get("knowledge_associations") or []
    )
    return {
        "scope": dict(diagnosis.get("scope") or {}),
        "exam_scope": dict(diagnosis.get("exam_scope") or {}),
        "warnings": list(diagnosis.get("warnings") or []),
        "target_kind": target_kind,
        "chapter_target_kinds": chapter_kinds,
        "nodes": nodes,
        "students": student_rows,
        "summary": summary,
        "associations": [dict(link) for link in links
                         if link.get("topic_key") in returned_keys and link.get("skill_key") in returned_keys],
    }


_OVERVIEW_CACHE = ResultCache(limit=32)


def _semester_overview_population(service, exam_scope, resolver):
    """Project only overview fields; fit the same complete semester population.

    The overview never displays a prior without evidence or diagnostic error
    details. Keep source question references for its examples and retain the
    same scope, score, coverage and mastery contracts as the full diagnosis.
    """
    from integration.evidence_scope import EvidenceScopeResolver
    from integration.diagnosis_profile_service import _number
    from question_bank.mastery.current import aggregate_current_mastery
    from question_bank.mastery.model import build_exam_observations
    from question_bank.recommendation.target_matching import target_index

    scope = {'mode': 'all', 'use_historical_fallback': False}
    resolved = EvidenceScopeResolver(service.db).resolve(scope=scope, exam_scope=exam_scope)
    session_ids = [int(s['id']) for s in resolved.sessions]
    student_ids = [str(s['id']) for s in resolved.students]
    projections = service._tag_projections(session_ids)
    rows = service._projected_tag_evidence(student_ids=student_ids, session_ids=session_ids,
                                          projection_by_session=projections, include_diagnostics=False)
    times = service.mastery_session_times(exam_scope=exam_scope)
    profile = {'exam_scope': dict(exam_scope), '_mastery_session_times': times,
               '_mastery_population': set(student_ids) == {str(s['id']) for s in service.db.students.list_students()},
               '_mastery_observations': build_exam_observations(rows, resolver, times)}
    selected = set(student_ids)
    mastery = {identity: value for identity, value in service.semester_mastery(
        profile, include_unobserved=False).items() if identity[0] in selected}
    parents = {r.source_key: r.target_key for r in resolver.relations if r.relation_type == 'parent'}
    nodes = {n.stable_key: n for n in resolver.nodes}
    kinds = target_index(resolver)
    catalog = [{'knowledge_key': key, 'knowledge_point': n.display_name,
                'definition': n.definition, 'parent_knowledge_key': parents.get(key),
                'node_kind': kinds.get(key, {}).get('kind', 'topic')}
               for key, n in nodes.items()]
    points = defaultdict(dict)
    # Keep the first source per question, matching governed-point deduplication.
    # A reference may belong to several targets, but its total is counted once.
    for row in rows:
        awarded = max(_number(row.get('score_awarded')), 0.0)
        full = max(_number(row.get('full_score')), 0.0)
        contributions = row.get('target_contributions')
        targets = (contributions if isinstance(contributions, Mapping)
                   else (row.get('question_tags') or {}).get('knowledge_point', ()))
        sid, qid = int(row.get('session_id') or 0), str(row.get('question_id') or '')
        ref = {'session_id': sid, 'session_name': str(row.get('session_name') or ''),
               'question_id': qid, 'bank_question_id': int(row.get('bank_question_id') or 0),
               'question_score_awarded': awarded, 'question_full_score': full}
        if row.get('combined_group_id'):
            ref['combined_group_id'] = row['combined_group_id']
        student_points = points[str(row.get('student_id') or '')]
        for raw_key in targets:
            key = str(raw_key or '').strip()
            if key not in nodes:
                matches = resolver.resolve(key)
                key = matches[0].stable_key if matches else f'knowledge_point:{raw_key}'
            item = student_points.get(key)
            if item is None:
                item = student_points[key] = {
                    'knowledge_key': key, 'mastery': None, 'evidence_count': 0, 'refs': {}}
            item['refs'].setdefault((sid, qid), ref)
    for (student, key), current in mastery.items():
        if key not in nodes:
            continue
        item = points[student].setdefault(key, {'knowledge_key': key, 'refs': {}})
        item.update(mastery=current.value, evidence_count=current.evidence_count,
                    observation_count=current.observation_count, tier=current.tier,
                    full_correct_count=current.full_correct_count, recent_trend=current.recent_trend,
                    interval_low=current.interval_low, interval_high=current.interval_high)
    parent_keys = set(parents.values())
    def point_order(item):
        key = item['knowledge_key']
        name = nodes[key].display_name if key in nodes else key.removeprefix('knowledge_point:')
        parent = nodes.get(parents.get(key))
        return (parent.display_name if parent else name, key not in parent_keys, name)
    students = []
    for student in resolved.students:
        sid = str(student['id'])
        values = sorted(points[sid].values(), key=point_order)
        for point in values:
            refs = point.pop('refs')
            point['source_question_refs'] = [refs[key] for key in sorted(refs)]
        students.append({'student_id': sid, 'student_code': str(student.get('student_code') or ''),
                         'student_name': str(student.get('name') or ''), 'class_id': str(student.get('class_name') or ''),
                         **resolved.score_profiles.get(sid, {}), 'weak_points': values})
    warnings = list(resolved.warnings)
    covered = sum(p.covered_items for p in projections.values())
    total = sum(p.total_items for p in projections.values())
    if any(p.missing_items for p in projections.values()):
        warnings.append(f'知识图谱仅覆盖 {covered}/{total} 个评分题；缺失题目已列出。')
    if not any(s['weak_points'] for s in students):
        warnings.append('所选范围内没有已关联且带知识点标签的诊断证据。')
    return {'scope': resolved.normalized_scope(scope),
            'exam_scope': {'mode': 'semester', 'curriculum_volume_id': str(exam_scope.get('curriculum_volume_id') or ''),
                           'session_ids': session_ids,
                           'sessions': [{'session_id': int(s['id']), 'session_name': str(s.get('session_name') or '')}
                                        for s in resolved.sessions]},
            'students': students, 'knowledge_catalog': catalog, 'warnings': list(dict.fromkeys(warnings)),
            'group_weak_points': [{'knowledge_key': key, 'mastery': value.value,
                                   'interval_low': value.interval_low, 'interval_high': value.interval_high,
                                   'tier': value.tier}
                                  for key, value in aggregate_current_mastery(mastery).items() if value.value is not None]}


def _semester_overview_input(service, scope, exam_scope, resolver):
    from integration.evidence_scope import EvidenceScopeResolver
    from integration.diagnosis_profile_service import _normalized_profile_scope
    from question_bank.mastery.current import aggregate_current_mastery

    population_scope = {'mode': 'all', 'use_historical_fallback': False}
    population_key = service.tag_profile_cache_key(scope=population_scope, exam_scope=exam_scope)
    # Reuse the existing bounded overview cache for the small semester input.
    # A first class selection only selects students and recomputes group means.
    population = _OVERVIEW_CACHE.get_or_compute(('overview-population-v1', population_key),
        lambda: _semester_overview_population(service, exam_scope, resolver))
    scope = _normalized_profile_scope(scope, exam_scope)
    # The unrestricted semester request already resolved these students and
    # scores while building population. Do not read the same exam totals again.
    if (str(scope.get('mode') or 'all') == 'all'
            and not any(scope.get(key) for key in ('student_ids', 'class_id', 'class_name',
                        'class_ids', 'include_student_ids', 'exclude_student_ids'))
            and all(scope.get(key) is None for key in ('score_rate_min', 'score_rate_max'))):
        return population
    resolved = EvidenceScopeResolver(service.db).resolve(scope=scope, exam_scope=exam_scope)
    normalized = resolved.normalized_scope(scope)
    if normalized == population['scope']:
        return population
    by_student = {s['student_id']: s for s in population['students']}
    students = [by_student[str(s['id'])] for s in resolved.students]
    selected = set(s['student_id'] for s in students)
    mastery = {identity: value for identity, value in service.semester_mastery(
        {'exam_scope': dict(exam_scope)}, include_unobserved=False).items() if identity[0] in selected}
    no_evidence = '所选范围内没有已关联且带知识点标签的诊断证据。'
    warnings = [w for w in population['warnings'] if w != no_evidence]
    warnings.extend(resolved.warnings)
    if not any(s['weak_points'] for s in students):
        warnings.append(no_evidence)
    return {**population, 'scope': normalized, 'students': students,
            'warnings': list(dict.fromkeys(warnings)),
            'group_weak_points': [{'knowledge_key': key, 'mastery': value.value,
                                   'interval_low': value.interval_low, 'interval_high': value.interval_high,
                                   'tier': value.tier}
                                  for key, value in aggregate_current_mastery(mastery).items() if value.value is not None]}


def overview_payload(
    service: Any,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    volume_id: str,
) -> dict[str, Any]:
    """Cached overview for one (scope, exam_scope, volume) request.

    Keyed on the exact diagnosis cache key so it shares invalidation with
    the profile cache; results are stored as pickle bytes and each hit is a
    fresh object.
    """

    profile_key = service.tag_profile_cache_key(scope=scope, exam_scope=exam_scope)
    key = (
        "mastery-overview-v4",
        profile_key,
        str(volume_id),
    )
    local_key = (*profile_key, 'overview-payload-v4', str(volume_id))
    def compute():
        reader = getattr(service, '_read_local_profile', None)
        entry = reader(local_key) if callable(reader) else None
        if entry is not None:
            payload = pickle.loads(entry[0]).get('overview')
            if isinstance(payload, dict):
                return payload
        resolver = None
        try:
            resolver = CurrentKnowledgeResolver.from_active_database(service.question_bank_db_path)
        except (CurrentKnowledgeUnavailable, OSError, sqlite3.Error, TypeError, ValueError):
            pass
        diagnosis = None
        from integration.diagnosis_profile_service import DiagnosisProfileService
        if resolver is not None and exam_scope.get('mode') == 'semester' and isinstance(service, DiagnosisProfileService):
            try:
                diagnosis = _semester_overview_input(service, scope, exam_scope, resolver)
            except (CurrentKnowledgeUnavailable, OSError, sqlite3.Error, TypeError, ValueError):
                pass  # Preserve the existing evidence-only fallback when mastery is unavailable.
        if diagnosis is None:
            diagnosis = getattr(service, 'build_summary_profiles', service.build_profiles)(scope=scope, exam_scope=exam_scope)
        payload = build_mastery_overview(diagnosis, volume_id=volume_id, associations=[], resolver=resolver)
        returned_keys = {node['knowledge_key'] for node in payload['nodes']}
        skill_keys = [node['knowledge_key'] for node in payload['nodes'] if node['kind'] == 'skill']
        associations = []
        if resolver is not None and skill_keys and payload['target_kind'] != 'type':
            try:
                associations = knowledge_skill_associations(load_question_facets(
                    service.question_bank_db_path, resolver,
                    include_practice_observations=False, association_skill_keys=skill_keys))
            except (OSError, sqlite3.Error, TypeError, ValueError):
                payload['warnings'].append('知识点与技能关联暂不可用')
        elif resolver is None:
            payload['warnings'].append('知识点与技能关联暂不可用')
        payload['associations'] = [dict(link) for link in associations
                                   if link['topic_key'] in returned_keys and link['skill_key'] in returned_keys]
        return payload
    payload = _OVERVIEW_CACHE.get_or_compute(key, compute)
    if getattr(service, 'persist_snapshots', False):
        service._save_local_profile(local_key, (pickle.dumps({'overview': payload}, pickle.HIGHEST_PROTOCOL),
                                               pickle.dumps({}, pickle.HIGHEST_PROTOCOL)))
    return payload


def clear_overview_caches() -> None:
    _OVERVIEW_CACHE.clear()


__all__ = ["build_mastery_overview", "clear_overview_caches", "overview_payload"]
