"""Read-only class evidence -> a small, teacher-selected question shortlist."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from statistics import median
from typing import Any, Mapping, Sequence

from question_bank.database.schema import connect
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.recommendation.recommendation_engine import normalize_question_text, text_similarity
from question_bank.services.duplicate_analysis_copy_service import exact_identity_map
from question_bank.services.question_read_service import QuestionBankReadService, QuestionReadFilters
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
from question_bank.recommendation.personalized import (
    _loss_difficulty, _loss_refs, _training_tasks, _practice_part_fits, _task_matched_part,
)
from question_bank.recommendation.target_matching import load_question_facets, match_target, target_index

# Stems this alike inside one difficulty band are shown once; alternates stay
# reachable through the representative card instead of crowding the shortlist.
SIMILAR_FOLD_THRESHOLD = 0.9


def class_weaknesses(diagnosis: Mapping[str, Any], *, volume_id: str, chapter_id: str,
                    resolver: Any = None) -> list[dict[str, Any]]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError("Unknown teaching term")
    chapters = [chapter for chapter in volume["chapters"] if not chapter_id or chapter["id"] == chapter_id]
    if not chapters:
        raise ValueError("Chapter is outside teaching term")
    allowed = {point["id"] for chapter in chapters for section in chapter["sections"] for point in section["knowledge_points"]}
    allowed.update(str(edge["skill_key"]) for edge in diagnosis.get("knowledge_associations", ())
                   if edge.get("topic_key") in allowed and edge.get("same_part_question_count", 0) > 0)
    sections = {section["knowledge_id"] for chapter in chapters for section in chapter["sections"]}
    if resolver is not None:
        from question_bank.recommendation.target_matching import target_index
        allowed.update(key for key, node in target_index(resolver).items()
                       if node["kind"] == "skill" and node["section"] in sections)
    else:
        # The diagnosis already carries the active standard, including skills.
        allowed.update(str(node["knowledge_key"]) for node in diagnosis.get("knowledge_catalog", ())
                       if str(node.get("knowledge_key", "")).startswith("sk_")
                       and node.get("parent_knowledge_key") in sections)
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
        evidence = list(members.get(key, {}).values())
        weak = sum(float(item["mastery"]) < .8 for item in evidence)
        if not evidence or point.get("mastery") is None:
            continue
        score = sum(float(item.get("score_sum", 0)) for item in evidence)
        full = sum(float(item.get("full_score_sum", 0)) for item in evidence)
        result.append({
            "knowledge_key": key, "knowledge_point": point["knowledge_point"],
            "mastery": point["mastery"], "weak_student_count": weak,
            "evidence_student_count": len(evidence),
            "exam_score_rate": round(score / full, 4) if full > 0 else None,
            "evidence_count": int(point.get("evidence_count", 0)),
            "candidate_count": None,
            "target_difficulty": None,
        })
    return sorted(result, key=lambda item: (item["mastery"] >= .8, -item["weak_student_count"], item["mastery"], -item["evidence_student_count"], item["knowledge_key"]))


def _question_ids(service: QuestionBankReadService, filters: QuestionReadFilters) -> list[int]:
    ids = []
    page = 1
    while True:
        result = service.list_question_refs(replace(filters, page=page, page_size=500))
        ids.extend(int(item["id"]) for item in result.items)
        if not result.items or page >= result.total_pages:
            return ids
        page += 1


def shortlist_candidates(
    *, diagnosis: Mapping[str, Any], read_service: QuestionBankReadService,
    volume_id: str, chapter_id: str, target_keys: list[str] | None,
    question_type: str, difficulty_min: int, difficulty_max: int,
    excluded_question_ids: set[int], limit: int | None = None,
) -> dict[str, Any]:
    resolver = read_service.current_knowledge or CurrentKnowledgeResolver.from_active_database(read_service.db_path)
    weaknesses = class_weaknesses(diagnosis, volume_id=volume_id, chapter_id=chapter_id,
                                resolver=resolver)
    by_key = {item["knowledge_key"]: item for item in weaknesses}
    selected = list(dict.fromkeys(target_keys)) if target_keys is not None else [item["knowledge_key"] for item in weaknesses[:1]]
    if any(key not in by_key for key in selected):
        raise ValueError("Selected weakness is no longer in the current class scope")
    students = diagnosis.get("students", [])
    exam_scores = [float(student["score_rate"]) for student in students if student.get("score_rate") is not None]
    output = {
        "student_count": len(students), "exam_student_count": len(exam_scores),
        "evidence_student_count": sum(any(p.get("mastery") is not None and p.get("evidence_count", 0) > 0 for p in student.get("weak_points", [])) for student in students),
        "exam_score_rate": round(sum(exam_scores) / len(exam_scores), 4) if exam_scores else None,
        "exam_count": len(diagnosis.get("exam_scope", {}).get("session_ids", [])),
        "weaknesses": weaknesses, "selected_target_keys": selected,
        "candidate_total": 0, "candidates": [],
    }
    if not selected:
        return output
    filters = QuestionReadFilters(
        knowledge_points=tuple(by_key[key]["knowledge_point"] for key in selected),
        question_types=(question_type,) if question_type else (),
        difficulty_min=difficulty_min, difficulty_max=difficulty_max,
        curriculum_volume_ids=(volume_id,),
        collapse_duplicates=False,
        scope_mode="primary",
    )
    # Identity-only queries rank the pool before loading rich content for the
    # final shortlist. Duplicate folding and governed tag aliases stay shared
    # with the existing question browser.
    # Query each selected point once. The union then bounds image decoding;
    # unrelated questions never enter the expensive exact-content comparison.
    pools = {key: _question_ids(read_service, replace(filters, knowledge_points=(by_key[key]["knowledge_point"],))) for key in selected}
    index = target_index(resolver)
    facets = load_question_facets(read_service.db_path, resolver)
    volume = curriculum_volume(volume_id=volume_id)
    allowed_chapters = {chapter["knowledge_id"] for chapter in volume["chapters"]
                        if not chapter_id or chapter["id"] == chapter_id}
    # The manual assistant retains the teacher's difficulty bounds. Candidate
    # context must remain inside the selected chapter(s), including all parts.
    eligible_ids = _question_ids(read_service, replace(filters, knowledge_points=()))
    details: dict[tuple[str, int], dict[str, Any]] = {}
    for key in selected:
        source_parts = []
        source_tasks = []
        aims = []
        for student in students:
            for point in student.get("weak_points", []):
                if point["knowledge_key"] != key:
                    continue
                for ref in _loss_refs(point):
                    if float(ref.get("score_awarded") or 0) >= float(ref.get("full_score") or 0):
                        continue
                    assessment = ref.get("assessment") or {}
                    if assessment.get("eligible") is False or float(assessment.get("evidence_weight", 1)) < .999:
                        continue
                    aim = _loss_difficulty(ref, student.get("score_rate"), difficulty_max)
                    if aim is not None:
                        aims.append(aim)
                    part_id = assessment.get("evidence_part_id") or assessment.get("part_id")
                    source_facet = facets.get(int(ref.get("bank_question_id") or 0), {})
                    parts = [part for part in source_facet.get("parts", [])
                             if (not part_id or part["part_id"] == part_id) and key in part["direct_keys"]]
                    source_parts.extend(parts)
                    enriched = {**ref, "practice_observations_by_key": {
                        key: [part for part in source_facet.get("practice_observations_by_key", {}).get(key, [])
                              if not part_id or part["part_id"] == part_id]},
                        "task_evidence_version_matches": bool(assessment.get("evidence_version_id")) and
                        assessment["evidence_version_id"] == source_facet.get("evidence_version_id")}
                    source_tasks.append((enriched, parts, _training_tasks({"stable_key": key, "source_question_refs": [enriched]})))
        by_key[key]["target_difficulty"] = round(median(aims), 1) if aims else None
        if not source_parts:
            continue  # Existing exact-tag selection remains available without a wrong-question anchor.
        ranked = []
        for qid in eligible_ids:
            parts = facets.get(qid, {}).get("parts", [])
            chapters = {chapter for part in parts for chapter in part["chapter_keys"]}
            if not chapters or not chapters <= allowed_chapters:
                continue
            options = []
            candidate = {"practice_observations_by_key": facets.get(qid, {}).get("practice_observations_by_key", {})}
            for ref, anchors, tasks in source_tasks:
                for part in parts:
                    match = match_target(key, anchors, [part], index)
                    if match is None:
                        continue
                    direct = match["match_level"] <= 2 or (not key.startswith("sk_") and key in part["direct_keys"])
                    full_response = not tasks or any(p["part_id"] == part["part_id"] and _practice_part_fits(p, tasks)
                                                     for p in candidate["practice_observations_by_key"].get(key, []))
                    task_match = None
                    if not direct and any(set(anchor["chapter_keys"]) & set(part["chapter_keys"])
                                          and (not anchor["topic_keys"] or set(anchor["topic_keys"]) <= set(part["topic_keys"]))
                                          for anchor in anchors):
                        task_match = _task_matched_part(candidate, key, ref, tasks, {part["part_id"]})
                    kind = "direct" if direct else "task_matched" if task_match else "supplement"
                    label = ("同技能环节练习（不代替完整书写）" if direct and not full_response else
                             "原小问环节匹配（不代替完整书写）" if task_match and task_match.get("practice_role") == "step_practice" else
                             "原小问任务匹配（已有解题步骤）" if task_match else
                             "同技能，作答要求不足（仅作补充）" if not direct and key in part["direct_keys"] else match["match_label"])
                    options.append({**match, "selection_kind": kind, "match_label": label})
            if options:
                match = min(options, key=lambda m: (m["selection_kind"] == "supplement", m["match_level"]))
                details[key, qid] = match
                ranked.append(qid)
        pools[key] = sorted(ranked, key=lambda qid: (details[key, qid]["selection_kind"] == "supplement", details[key, qid]["match_level"], qid))
    pool_ids = set(qid for ids in pools.values() for qid in ids)
    with connect(read_service.db_path) as connection:
        identities = exact_identity_map(connection, data_root=read_service.data_root or read_service.db_path.parent.parent,
                                        question_ids=sorted(pool_ids | excluded_question_ids))
        rows = connection.execute(
            "SELECT id, difficulty, question_type, question_text FROM questions WHERE is_deleted=0").fetchall()
        difficulties = {int(row["id"]): float(row["difficulty"]) for row in rows
                        if int(row["id"]) in pool_ids and row["difficulty"] is not None
                        and 1 <= float(row["difficulty"]) <= 10}
        texts = {int(row["id"]): (str(row["question_text"] or ""), str(row["question_type"] or ""))
                 for row in rows if int(row["id"]) in pool_ids}
    excluded_keys = {identities[qid] for qid in excluded_question_ids if identities.get(qid)}
    available: set[int] = set()
    seen: set[str] = set()
    for qid in sorted(pool_ids):
        identity = identities.get(qid, "")
        if qid in excluded_question_ids or (identity and (identity in excluded_keys or identity in seen)):
            continue
        available.add(qid)
        if identity:
            seen.add(identity)
    queues: dict[str, deque[int]] = {}
    matches: dict[int, list[str]] = defaultdict(list)
    for key in selected:
        queues[key] = deque(qid for qid in pools[key] if qid in available)
        by_key[key]["candidate_count"] = len(queues[key])
        for qid in queues[key]:
            matches[qid].append(key)
    chosen = []
    # Alternate between chosen weaknesses so a large question pool for one
    # point cannot crowd every other classroom weakness out of the shortlist.
    priority_keys = [point["knowledge_key"] for point in weaknesses if point["knowledge_key"] in selected]
    chosen_ids = set()
    while True:
        before = len(chosen)
        for key in priority_keys:
            while queues[key] and queues[key][0] in chosen_ids:
                queues[key].popleft()
            if queues[key]:
                qid = queues[key].popleft()
                chosen.append(qid)
                chosen_ids.add(qid)
        if len(chosen) == before:
            break
    def foundation(qid: int) -> bool:
        return difficulties.get(qid, 10) <= 4 or all(by_key[key]["mastery"] >= .8 for key in matches[qid])
    def match_details(qid: int) -> dict[str, Any]:
        options = [details[key, qid] for key in matches[qid] if (key, qid) in details]
        return min(options, key=lambda item: (item.get("selection_kind") == "supplement", item["match_level"])) if options else {}
    def band(qid: int) -> tuple[str, float | None]:
        difficulty = difficulties.get(qid)
        aims = [by_key[key]["target_difficulty"] for key in matches[qid]
                if by_key[key].get("target_difficulty") is not None]
        if difficulty is None or not aims:
            return "unknown", None
        aim = min(aims, key=lambda value: abs(difficulty - value))
        gap = abs(difficulty - aim)
        if gap <= 1:
            return "suitable", gap
        return ("lower" if difficulty < aim else "higher"), gap
    bands = {qid: band(qid) for qid in chosen}
    band_order = {"suitable": 0, "lower": 1, "higher": 2, "unknown": 3}
    chosen.sort(key=lambda qid: (match_details(qid).get("selection_kind") == "supplement",
                                 band_order[bands[qid][0]], match_details(qid).get("match_level", 5),
                                 bands[qid][1] if bands[qid][1] is not None else 0, qid))
    similar_members = _fold_similar(chosen, bands=bands, difficulties=difficulties,
                                    facets=facets, texts=texts)
    if similar_members:
        folded = {member for members in similar_members.values() for member in members}
        chosen = [qid for qid in chosen if qid not in folded]
    output["candidate_total"] = len(chosen)
    if limit is not None:
        chosen = chosen[:limit]
    output["candidates"] = [{"question_id": qid, "target_keys": matches[qid],
                             "practice_kind": "foundation" if foundation(qid) else "focus",
                             "match_level": match_details(qid).get("match_level"),
                             "match_label": match_details(qid).get("match_label", "按已选目标关联"),
                             "selection_kind": match_details(qid).get("selection_kind"),
                             "difficulty": difficulties.get(qid),
                             "difficulty_band": bands[qid][0],
                             "similar_question_ids": similar_members.get(qid, []),
                             "direct_target_keys": [key for key in matches[qid]
                                                    if details.get((key, qid), {}).get("selection_kind", "direct") == "direct"],
                             } for qid in chosen]
    return output


def _fold_similar(chosen: Sequence[int], *, bands: Mapping[int, tuple[str, Any]],
                  difficulties: Mapping[int, float], facets: Mapping[int, Mapping[str, Any]],
                  texts: Mapping[int, tuple[str, str]]) -> dict[int, list[int]]:
    """Fold near-identical questions inside one difficulty band.

    Same band + type + difficulty + same knowledge/skill signature bucket
    first; inside a bucket a normalized-stem similarity at or above
    SIMILAR_FOLD_THRESHOLD folds the later item under the earlier (better
    ranked) representative. Members stay retrievable by id.
    """
    normalized = {qid: normalize_question_text(texts.get(qid, ("", ""))[0]) for qid in chosen}
    buckets: dict[tuple[Any, ...], list[int]] = {}
    for qid in chosen:
        facet = facets.get(qid, {})
        buckets.setdefault((bands[qid][0], texts.get(qid, ("", ""))[1],
                            int(difficulties.get(qid) or 0),
                            tuple(facet.get("skill_keys") or ()),
                            tuple(facet.get("topic_keys") or ())), []).append(qid)
    members: dict[int, list[int]] = {}
    for ids in buckets.values():
        representatives: list[int] = []
        for qid in ids:
            text = normalized.get(qid) or ""
            for rep in representatives:
                if text and text_similarity(text, normalized[rep]) >= SIMILAR_FOLD_THRESHOLD:
                    members.setdefault(rep, []).append(qid)
                    break
            else:
                representatives.append(qid)
    return members
