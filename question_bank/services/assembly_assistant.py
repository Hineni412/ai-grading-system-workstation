"""Teacher shortlist: aggregate the same per-student matching used by training."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from statistics import median
from typing import Any

from question_bank.question_types import (
    is_training_target,
    type_keys_active,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
    _group_rank,
    _is_core,
    _task_need_ids,
    _task_priorities,
    paper_similarity_allowed,
    resolve_practice_scope,
)
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.taxonomy.curriculum_catalog import curriculum_volume


def class_weaknesses(diagnosis: Mapping[str, Any], *, volume_id: str, chapter_id: str,
                    resolver: Any = None, scope_keys: Sequence[str] = ()) -> list[dict[str, Any]]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError("Unknown teaching term")
    chapters = [chapter for chapter in volume["chapters"] if not chapter_id or chapter["id"] == chapter_id]
    if scope_keys:
        chapters = [chapter for chapter in chapters if chapter['knowledge_id'] in scope_keys]
    if not chapters:
        raise ValueError("Chapter is outside teaching term")
    # Topics decide scope; the selected volume decides the training target kind.
    topics = {point["id"] for chapter in chapters for section in chapter["sections"] for point in section["knowledge_points"]}
    allowed = {str(edge["skill_key"]) for edge in diagnosis.get("knowledge_associations", ())
               if edge.get("topic_key") in topics and edge.get("same_part_question_count", 0) > 0}
    sections = {section["knowledge_id"] for chapter in chapters for section in chapter["sections"]}
    if resolver is not None:
        from question_bank.recommendation.target_matching import target_index
        allowed.update(key for key, node in target_index(resolver).items()
                       if node["kind"] == "skill" and node["section"] in sections)
        if type_keys_active(resolver, volume_id):
            allowed.update(key for key, node in target_index(resolver).items()
                           if node["kind"] == "type" and node["section"] in sections)
    else:
        # The diagnosis already carries the active standard, including skills.
        allowed.update(str(node["knowledge_key"]) for node in diagnosis.get("knowledge_catalog", ())
                       if str(node.get("knowledge_key", "")).startswith("sk_")
                       and node.get("parent_knowledge_key") in sections)
    allowed = {key for key in allowed
               if (is_training_target(key, resolver, volume_id) if resolver is not None
                   else str(key).startswith("sk_"))}
    members: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for student in diagnosis.get("students", []):
        for point in student.get("weak_points", []):
            key = point["knowledge_key"]
            if key in allowed and point.get("mastery") is not None and point.get("evidence_count", 0) > 0:
                members[key][student["student_id"]] = point
    result = []
    # Reuse the current knowledge graph's evidence-weighted aggregate; never
    # reinterpret missing student evidence as zero or recompute mastery here.
    for point in diagnosis.get("group_weak_points", []):
        key = point["knowledge_key"]
        if not (is_training_target(key, resolver, volume_id) if resolver is not None
                else str(key).startswith("sk_")):
            continue
        evidence = list(members.get(key, {}).values())
        weak = sum(item.get("tier") in {"weak", "unsteady"} for item in evidence)
        clearly_weak = sum(item.get("tier") == "weak" for item in evidence)
        if not evidence or point.get("mastery") is None:
            continue
        score = sum(float(item.get("score_sum", 0)) for item in evidence)
        full = sum(float(item.get("full_score_sum", 0)) for item in evidence)
        result.append({
            "knowledge_key": key, "knowledge_point": point["knowledge_point"],
            "mastery": point["mastery"], "weak_student_count": weak, "weak_tier_student_count": clearly_weak, "tier": point.get("tier", "insufficient"),
            "evidence_student_count": len(evidence),
            "exam_score_rate": round(score / full, 4) if full > 0 else None,
            "evidence_count": int(point.get("evidence_count", 0)),
            "candidate_count": None,
            "target_difficulty": None,
        })
    present = {item["knowledge_key"] for item in result}
    names = {point["id"]: point["display_name"] for chapter in chapters for section in chapter["sections"] for point in section["knowledge_points"]}
    for key in sorted(allowed - present):
        node = resolver.node(key) if resolver else None
        result.append({"knowledge_key": key, "knowledge_point": node.display_name if node else names.get(key, key),
                       "mastery": None, "weak_student_count": 0, "weak_tier_student_count": 0, "tier": "insufficient", "evidence_student_count": 0,
                       "exam_score_rate": None, "evidence_count": 0, "candidate_count": None, "target_difficulty": None})
    return sorted(result, key=lambda item: (item["weak_student_count"] == 0, -item["weak_tier_student_count"], -item["weak_student_count"], item["mastery"] if item["mastery"] is not None else 1, item["knowledge_key"]))



def shortlist_candidates(
    *, diagnosis: Mapping[str, Any], read_service: QuestionBankReadService,
    volume_id: str, chapter_id: str, target_keys: list[str] | None,
    question_type: str, difficulty_min: float, difficulty_max: float,
    excluded_question_ids: set[int], limit: int | None = None,
    cache_scope: object = None, recommendations: PersonalizedRecommendationModule | None = None,
    teaching_progress_chapter_id: str = '',
    graded_activities: Sequence[Mapping[str, Any]] | None = None,
    recent_activity_count: int = 3, purpose: str = 'training',
) -> dict[str, Any]:
    module = recommendations or PersonalizedRecommendationModule(db_path=read_service.db_path,
        data_root=read_service.data_root or read_service.db_path.parent.parent)
    resolver = module.current_knowledge
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None or (chapter_id and not any(c['id'] == chapter_id for c in volume['chapters'])):
        raise ValueError('Chapter is outside teaching term')
    scope = tuple(c['knowledge_id'] for c in volume['chapters'] if c['id'] == chapter_id)
    config = resolve_practice_scope(PersonalizedRecommendationConfig(scope_keys=scope,
        curriculum_volume_id=volume_id, difficulty_max=float(difficulty_max), purpose=purpose,
        recent_activity_count=recent_activity_count,
        teaching_progress_chapter_id=teaching_progress_chapter_id), diagnosis, resolver)
    weaknesses = class_weaknesses(diagnosis, volume_id=volume_id, chapter_id=chapter_id,
                                 resolver=resolver, scope_keys=config.scope_keys)
    by_key = {item["knowledge_key"]: item for item in weaknesses}
    selected = [key for key in dict.fromkeys(target_keys) if is_training_target(key, resolver, volume_id)] if target_keys is not None else [item["knowledge_key"] for item in weaknesses[:1]]
    if any(not str(key).startswith("sk_") and resolver.node(str(key)) is None for key in target_keys or ()):
        raise ValueError("Selected target is no longer in the current class scope")
    if any(key not in by_key for key in selected):
        raise ValueError("Selected target is no longer in the current class scope")
    students = diagnosis.get("students", [])
    scores = [float(p["score_rate"]) for p in students if p.get("score_rate") is not None]
    output = {"student_count": len(students), "exam_student_count": len(scores),
        "evidence_student_count": sum(any(p.get("mastery") is not None and p.get("evidence_count", 0) > 0 for p in student.get("weak_points", [])) for student in students),
        "exam_score_rate": round(sum(scores)/len(scores), 4) if scores else None,
        "exam_count": len(diagnosis.get("exam_scope", {}).get("session_ids", [])),
        "weaknesses": weaknesses, "selected_target_keys": selected, "candidate_total": 0, "candidates": [],
        "target_kind": "type" if type_keys_active(resolver, volume_id) else "skill"}
    if not selected or not students:
        return output
    config = replace(config, paper_mode='shared', target_keys=tuple(selected))
    evaluated = module.evaluate_candidates(diagnosis=diagnosis, config=config, excluded=excluded_question_ids,
                                           graded_activities=graded_activities, core_only=True)
    groups = defaultdict(list)
    for entries in evaluated["pools"].values():
        for entry in entries:
            c = entry["candidate"]
            if c["difficulty"] >= difficulty_min and c["difficulty"] <= difficulty_max and (not question_type or c["question_type"] == question_type):
                groups[c["question_id"]].append(entry)
    for key in selected:
        entries = [e for group in groups.values() for e in group if e["key"] == key]
        by_key[key]["candidate_count"] = len({e["candidate"]["question_id"] for e in entries})
        by_key[key]["target_difficulty"] = median(e["target"]["target_difficulty"] for e in entries) if entries else None
    candidates = []
    for qid, group in groups.items():
        members = {}
        for e in sorted(group, key=lambda e: (e["practice_purpose"] != "remediation", e["selection_kind"] == "supplement", e["match_level"], e["key"])):
            members.setdefault(e["student_id"], e)
        best = min(members.values(), key=lambda e: (e["practice_purpose"] != "remediation", e["match_level"], e["distance"], -e["preference"]))
        purposes = {kind: sum(e["practice_purpose"] == kind for e in members.values()) for kind in ("remediation", "consolidation", "new")}
        candidates.append({"question_id": qid, "target_keys": sorted({e["key"] for e in group}),
            "practice_kind": "focus" if purposes["remediation"] else "foundation", "match_level": best["match_level"],
            "match_label": best["match_label"], "selection_kind": best["selection_kind"],
            "difficulty": best["candidate"]["difficulty"], "difficulty_band": "suitable",
            "suitable_student_count": len(members), "remediation_student_count": purposes["remediation"],
            "consolidation_student_count": purposes["consolidation"], "new_practice_student_count": purposes["new"],
            "uncertain_student_count": sum(e["evidence_confidence"] != "repeated" for e in members.values()),
            "difficulty_basis": best["difficulty_basis"], "similar_question_ids": [],
            "direct_target_keys": sorted({e["key"] for e in group if e["selection_kind"] == "direct"})})
    memo: dict = {}
    priorities = _task_priorities(groups, memo)
    needs = {qid: set().union(*(_task_need_ids(e, memo) for e in group
                              if _is_core(e) and e.get("practice_purpose", "remediation") == "remediation"))
             for qid, group in groups.items()}
    candidates.sort(key=lambda item: _group_rank(groups[item["question_id"]], needs[item["question_id"]], priorities))
    # Fold only genuine similarity using the same comparator. Written count is
    # a selected-paper rule, so comparing two candidates never imposes a quota.
    descriptors = {q["question_id"]: q for q in evaluated["candidates"]}
    folded = []
    for item in candidates:
        representative = next((other for other in folded if not paper_similarity_allowed(descriptors[item["question_id"]], [descriptors[other["question_id"]]])), None)
        if representative:
            representative["similar_question_ids"].append(item["question_id"])
        else:
            folded.append(item)
    output["candidate_total"] = len(folded)
    output["candidates"] = folded if limit is None else folded[:limit]
    return output
