"""Skill browsing projections from the authoritative current evidence readers."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import MutableMapping
from pathlib import Path
from statistics import median
from typing import Any
from question_bank.current_knowledge import CurrentKnowledgeResolver, CurrentKnowledgeUnavailable

from question_bank.solution_evidence.knowledge_links import load_point_links
from question_bank.solution_evidence.part_assessments import SourceHash, load_profiles
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_ancestors, curriculum_knowledge_node, curriculum_volume,
)


def short_node_name(value: str) -> str:
    return value.replace("/", "｜").replace("|", "｜").split("｜")[-1].strip().removeprefix("技能·")


def _anchor_key(value: str, volume: dict[str, Any]) -> str:
    if curriculum_knowledge_node(value):
        return value
    names = {chapter['display_name']: chapter['knowledge_id'] for chapter in volume['chapters']}
    for chapter in volume['chapters']:
        for section in chapter['sections']:
            names[section['display_name']] = section['knowledge_id']
            names.update({point['display_name']: point['id'] for point in section['knowledge_points']})
    path = value.replace('/', '｜').replace('|', '｜')
    matches = [name for name in names if path == name or path.startswith(name + '｜')]
    return names[max(matches, key=len)] if matches else ''


def skill_anchor_ids(node_row, volume: dict[str, Any]) -> list[str]:
    """Resolve this volume's anchors, promoting knowledge points to sections."""
    anchors = []
    for raw in json.loads(node_row['curriculum_anchors_json']):
        anchor = _anchor_key(raw, volume)
        target = curriculum_knowledge_node(anchor)
        if not target or target['volume_id'] != volume['id']:
            continue
        if target['level'] == 3:
            anchor = target['parent_id']
        if anchor not in anchors:
            anchors.append(anchor)
    return anchors


def load_skill_inventory(conn: sqlite3.Connection) -> dict[str, Any]:
    """只读题目归属和节点；不打开题目正文或图片。"""
    active = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()
    release = str(active[0]) if active else None
    nodes = {str(row["stable_key"]): dict(row) for row in conn.execute(
        "SELECT * FROM knowledge_graph_node_profiles WHERE release_id=? AND status='active'", (release,))}
    questions = {int(row["id"]): dict(row) for row in conn.execute(
        "SELECT q.id,q.question_type,q.difficulty FROM questions q "
        "JOIN papers p ON p.id=q.paper_id WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'")}
    members: dict[int, set[int]] = defaultdict(set)
    volumes: dict[str, set[int]] = defaultdict(set)
    paper_volumes: dict[int, str | None] = {}
    for row in conn.execute(
        "SELECT DISTINCT p.id,p.grade,p.semester,p.textbook_version,q.id AS question_id FROM papers p "
        "JOIN (SELECT id,paper_id FROM questions UNION SELECT question_id,paper_id FROM paper_question_occurrences) m "
        "ON m.paper_id=p.id JOIN questions q ON q.id=m.id "
        "WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'"):
        qid = int(row["question_id"])
        if qid not in questions:
            continue
        paper_id = int(row["id"])
        members[paper_id].add(qid)
        if paper_id not in paper_volumes:
            volume = curriculum_volume(grade=row["grade"], semester=row["semester"], textbook_version=row["textbook_version"])
            paper_volumes[paper_id] = volume["id"] if volume else None
        volume_id = paper_volumes[paper_id]
        if volume_id:
            volumes[volume_id].add(qid)
    return {"release": release, "nodes": nodes, "questions": questions,
            "members": dict(members), "volumes": dict(volumes)}


def build_skill_snapshot(
    conn: sqlite3.Connection, db_path: Path, data_root: Path | None,
    *, inventory: dict[str, Any] | None = None, question_ids: tuple[int, ...] | None = None,
    source_hashes: MutableMapping[int, SourceHash] | None = None,
) -> dict[str, Any]:
    inventory = inventory if inventory is not None else load_skill_inventory(conn)
    release, nodes = inventory["release"], inventory["nodes"]
    questions = inventory["questions"] if question_ids is None else {
        qid: inventory["questions"][qid] for qid in question_ids if qid in inventory["questions"]}
    selected = set(questions)
    members = {key: ids & selected for key, ids in inventory["members"].items()}
    volumes = {key: ids & selected for key, ids in inventory["volumes"].items()}
    profiles = load_profiles(db_path, list(questions), connection=conn, data_root=data_root,
                             source_hashes=source_hashes)
    usable = {qid: profile for qid, profile in profiles.items() if profile.get("available")}
    links = load_point_links(db_path, [profile["evidence_version_id"] for profile in usable.values()], release, connection=conn)
    point_counts: dict[int, int] = {}
    by_skill: dict[str, set[int]] = defaultdict(set)
    by_question: dict[int, dict[str, list[dict[str, str]]]] = {}
    for qid, profile in usable.items():
        skills: dict[str, list[dict[str, str]]] = {}
        point_links = links.get(profile["evidence_version_id"], {})
        number = 0
        for part in profile["evidence"].get("parts", []):
            for point in part.get("evidence_points", []):
                number += 1
                point_id = str(point["evidence_point_id"])
                for link in point_links.get(point_id, ()):
                    if link.role == "direct" and link.resolution_status == "resolved" and link.stable_key.startswith("sk_"):
                        hit = {"point_id": point_id, "point_label": f"判定点 {number}：{point.get('target', '')}"}
                        if hit not in skills.setdefault(link.stable_key, []):
                            skills[link.stable_key].append(hit)
                        by_skill[link.stable_key].add(qid)
        point_counts[qid] = number
        by_question[qid] = skills
    topics: dict[str, set[int]] = defaultdict(set)
    topic_keys: dict[str, str] = {}
    sections: dict[str, set[int]] = defaultdict(set)
    try:
        resolver = CurrentKnowledgeResolver.from_connection(conn)
    except CurrentKnowledgeUnavailable:
        resolver = None
    resolved_topics: dict[str, tuple[str, str] | None] = {}
    for row in conn.execute("SELECT question_id,tag_type,tag_value FROM question_tags WHERE tag_type IN ('knowledge_point','curriculum_section','exam_scope')"):
        qid = int(row["question_id"])
        if qid not in questions:
            continue
        if row["tag_type"] == "knowledge_point":
            value = str(row['tag_value'])
            if value not in resolved_topics:
                term = resolver.canonical_term(value) if resolver else None
                targets = resolver.resolve(value) if resolver else ()
                target = next((item for item in targets if item.stable_key.startswith('kp_')), None)
                resolved_topics[value] = (term[1] if term else value, target.stable_key) if target else None
            resolved = resolved_topics[value]
            if resolved:
                value, topic_key = resolved
                topic_keys[value] = topic_key
                topics[value].add(qid)
        else:
            sections[str(row["tag_value"])].add(qid)
    return {"release": release, "nodes": nodes, "questions": questions, "members": dict(members),
            "volumes": dict(volumes), "by_skill": dict(by_skill), "by_question": by_question,
            "point_counts": point_counts, "no_usable": set(questions) - set(usable),
            "unlinked": {qid for qid in questions if not by_question.get(qid)},
            "topics": dict(topics), "topic_keys": topic_keys, "sections": dict(sections)}


def skill_index(snapshot: dict[str, Any], volume_id: str, review_ids: set[int]) -> dict[str, Any]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError("请选择有效的教学学期")
    ids = snapshot["volumes"].get(volume_id, set())
    def stats(question_ids: set[int]) -> dict[str, Any]:
        selected = question_ids & ids
        types = Counter(snapshot["questions"][qid]["question_type"] for qid in selected)
        difficulties = []
        for qid in selected:
            try:
                difficulty = float(snapshot["questions"][qid]["difficulty"])
            except (ValueError, TypeError):
                continue
            if 1 <= difficulty <= 10:
                difficulties.append(difficulty)
        return {"question_count": len(selected),
                "type_counts": {kind: types[kind] for kind in ("选择题", "多选题", "填空题", "解答题")},
                "difficulty": {"min": min(difficulties), "median": median(difficulties), "max": max(difficulties)} if difficulties else None,
                "criteria_needs_review_count": len(selected & review_ids)}

    skill_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, node in snapshot["nodes"].items():
        if not key.startswith("sk_"):
            continue
        anchors = skill_anchor_ids(node, volume)
        for anchor in anchors:
            skill_rows[anchor].append({"stable_key": key, "display_name": short_node_name(node["display_name"]),
                "full_name": node["display_name"], **stats(snapshot["by_skill"].get(key, set())),
                "cross_section": len(anchors) > 1 or any(curriculum_knowledge_node(key)["level"] == 1 for key in anchors),
                "definition": {name: node[name] for name in ("observable_evidence", "include_scope", "exclude_scope")}})
    topic_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value, question_ids in snapshot["topics"].items():
        # Preserve the actual stored filter value; public names are only presentation.
        node = snapshot['nodes'].get(snapshot['topic_keys'].get(value, ''))
        anchors = [_anchor_key(anchor, volume) for anchor in json.loads(node["curriculum_anchors_json"])] if node else []
        for chapter in volume["chapters"]:
            for section in chapter["sections"]:
                if (section["knowledge_id"] in anchors or value == section["display_name"]
                    or value.startswith(section["display_name"] + "｜")
                    or any(section["knowledge_id"] in curriculum_knowledge_ancestors(anchor) for anchor in anchors)):
                    topic_rows[section["knowledge_id"]].append({"stable_key": snapshot['topic_keys'][value], "filter_value": value, "display_name": short_node_name(value), **stats(question_ids)})
    chapters = []
    for chapter in volume["chapters"]:
        chapter_ids: set[int] = set()
        section_rows = []
        for section in chapter["sections"]:
            key = section["knowledge_id"]
            section_ids: set[int] = set()
            for skill in skill_rows[key]:
                section_ids.update(snapshot["by_skill"].get(skill["stable_key"], set()))
            for topic in topic_rows[key]:
                section_ids.update(snapshot["topics"].get(topic["filter_value"], set()))
            section_ids.update(snapshot["sections"].get(section["display_name"], set()))
            chapter_ids.update(section_ids)
            section_rows.append({"id": key, "label": section["label"], "question_count": len(section_ids & ids),
                                 "skills": skill_rows[key], "topics": topic_rows[key]})
        key = chapter["knowledge_id"]
        for skill in skill_rows[key]:
            chapter_ids.update(snapshot["by_skill"].get(skill["stable_key"], set()))
        for value in chapter["exam_scope_values"]:
            chapter_ids.update(snapshot["sections"].get(value, set()))
        chapters.append({"id": key, "label": chapter["label"], "question_count": len(chapter_ids & ids),
                         "cross_section_skills": skill_rows[key], "sections": section_rows})
    return {"graph_release_id": snapshot["release"], "curriculum_volume_id": volume_id, "model_calls": 0,
            "question_count": len(ids), "unlinked": {"no_usable_evidence": len(ids & snapshot["no_usable"]),
            "no_skill_link": len((ids & snapshot["unlinked"]) - snapshot["no_usable"])}, "chapters": chapters}
