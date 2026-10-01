"""Skill browsing projections from the authoritative current evidence readers."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from question_bank.solution_evidence.knowledge_links import load_point_links
from question_bank.solution_evidence.part_assessments import load_profiles
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_ancestors, curriculum_knowledge_node, curriculum_volume,
)


def short_node_name(value: str) -> str:
    return value.replace("|", "｜").split("｜")[-1].strip().removeprefix("技能·")


def build_skill_snapshot(conn: sqlite3.Connection, db_path: Path, data_root: Path | None) -> dict[str, Any]:
    active = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()
    release = str(active[0]) if active else None
    nodes = {str(row["stable_key"]): dict(row) for row in conn.execute(
        "SELECT * FROM knowledge_graph_node_profiles WHERE release_id=? AND status='active'", (release,))}
    questions = {int(row["id"]): dict(row) for row in conn.execute(
        "SELECT q.id,q.question_type,q.difficulty FROM questions q "
        "JOIN papers p ON p.id=q.paper_id WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'")}
    members: dict[int, set[int]] = defaultdict(set)
    volumes: dict[str, set[int]] = defaultdict(set)
    for row in conn.execute(
        "SELECT DISTINCT p.id,p.grade,p.semester,p.textbook_version,q.id AS question_id FROM papers p "
        "JOIN (SELECT id,paper_id FROM questions UNION SELECT question_id,paper_id FROM paper_question_occurrences) m "
        "ON m.paper_id=p.id JOIN questions q ON q.id=m.id "
        "WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'"):
        qid = int(row["question_id"])
        if qid not in questions:
            continue
        members[int(row["id"])].add(qid)
        volume = curriculum_volume(grade=row["grade"], semester=row["semester"], textbook_version=row["textbook_version"])
        if volume:
            volumes[volume["id"]].add(qid)
    profiles = load_profiles(db_path, list(questions), connection=conn, data_root=data_root)
    usable = {qid: profile for qid, profile in profiles.items() if profile.get("available")}
    links = load_point_links(db_path, [profile["evidence_version_id"] for profile in usable.values()], release, connection=conn)
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
        by_question[qid] = skills
    topics: dict[str, set[int]] = defaultdict(set)
    sections: dict[str, set[int]] = defaultdict(set)
    for row in conn.execute("SELECT question_id,tag_type,tag_value FROM question_tags WHERE tag_type IN ('knowledge_point','curriculum_section','exam_scope')"):
        qid = int(row["question_id"])
        if qid not in questions:
            continue
        if row["tag_type"] == "knowledge_point":
            topics[str(row["tag_value"])].add(qid)
        else:
            sections[str(row["tag_value"])].add(qid)
    return {"release": release, "nodes": nodes, "questions": questions, "members": dict(members),
            "volumes": dict(volumes), "by_skill": dict(by_skill), "by_question": by_question,
            "no_usable": set(questions) - set(usable),
            "unlinked": {qid for qid in questions if not by_question.get(qid)},
            "topics": dict(topics), "sections": dict(sections)}


def skill_index(snapshot: dict[str, Any], volume_id: str, review_ids: set[int]) -> dict[str, Any]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError("请选择有效的教学学期")
    ids = snapshot["volumes"].get(volume_id, set())
    anchor_names = {chapter['display_name']: chapter['knowledge_id'] for chapter in volume['chapters']}
    for chapter in volume['chapters']:
        for section in chapter['sections']:
            anchor_names[section['display_name']] = section['knowledge_id']
            for point in section['knowledge_points']:
                anchor_names[point['display_name']] = point['id']

    def anchor_key(value: str) -> str:
        if curriculum_knowledge_node(value):
            return value
        path = value.replace('/', '｜').replace('|', '｜')
        names = [name for name in anchor_names if path == name or path.startswith(name + '｜')]
        return anchor_names[max(names, key=len)] if names else ''

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
        anchors = []
        for anchor in json.loads(node["curriculum_anchors_json"]):
            anchor = anchor_key(anchor)
            target = curriculum_knowledge_node(anchor)
            if not target or target["volume_id"] != volume_id:
                continue
            if target["level"] == 3:
                anchor = target["parent_id"]
            if anchor not in anchors:
                anchors.append(anchor)
        for anchor in anchors:
            skill_rows[anchor].append({"stable_key": key, "display_name": short_node_name(node["display_name"]),
                "full_name": node["display_name"], **stats(snapshot["by_skill"].get(key, set())),
                "cross_section": len(anchors) > 1,
                "definition": {name: node[name] for name in ("observable_evidence", "include_scope", "exclude_scope")}})
    topic_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value, question_ids in snapshot["topics"].items():
        # Preserve the actual stored filter value; public names are only presentation.
        node = next((node for node in snapshot["nodes"].values() if node["display_name"] == value), None)
        anchors = [anchor_key(anchor) for anchor in json.loads(node["curriculum_anchors_json"])] if node else []
        for chapter in volume["chapters"]:
            for section in chapter["sections"]:
                if (section["knowledge_id"] in anchors or value == section["display_name"]
                    or value.startswith(section["display_name"] + "｜")
                    or any(section["knowledge_id"] in curriculum_knowledge_ancestors(anchor) for anchor in anchors)):
                    topic_rows[section["knowledge_id"]].append({"filter_value": value, "display_name": short_node_name(value), **stats(question_ids)})
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
