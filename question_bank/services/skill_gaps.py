"""Skill-gap projection over the cached skill snapshot.

A 判定点 (evidence point) is a skill gap when its effective knowledge links
carry no resolved
direct ``sk_`` link.  These helpers turn the shared snapshot projection into
per-point gap items for one curriculum volume; they never write and never
call a model.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_ancestors,
    curriculum_knowledge_node,
)


def teacher_protected_versions(connection: sqlite3.Connection) -> set[str]:
    """Evidence versions a teacher touched; AI must not rewrite their links."""
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT evidence_version_id "
            "FROM evidence_point_knowledge_links WHERE source_kind = 'teacher'"
        )
    }


def gap_items(
    snapshot: Mapping[str, Any],
    volume_id: str,
    protected: set[str],
    volume: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Ordered gap items for one volume; protected versions excluded."""
    sections: dict[str, tuple[str, Mapping[str, Any]]] = {}
    tag_section: dict[str, str] = {}
    tag_chapter: dict[str, str] = {}
    for chapter in volume["chapters"]:
        chapter_key = str(chapter["knowledge_id"])
        for value in (
            chapter["id"],
            chapter["knowledge_id"],
            chapter["display_name"],
            *chapter["exam_scope_values"],
        ):
            tag_chapter.setdefault(str(value), chapter_key)
        for section in chapter["sections"]:
            section_key = str(section["knowledge_id"])
            sections[section_key] = (chapter_key, section)
            for value in (
                section["id"],
                section["knowledge_id"],
                section["display_name"],
            ):
                tag_section.setdefault(str(value), section_key)
    tags_by_question: dict[int, list[str]] = {}
    for value, question_ids in snapshot["sections"].items():
        for qid in question_ids:
            tags_by_question.setdefault(int(qid), []).append(str(value))
    items: list[dict[str, Any]] = []
    for qid in sorted(snapshot["volumes"].get(volume_id, ())):
        version = str(snapshot["evidence_versions"].get(qid) or "")
        if not version or version in protected:
            continue
        tags = tags_by_question.get(qid, [])
        for point in snapshot["gap_points"].get(qid, []):
            section_key, chapter_key = _locate(
                point, tags, tag_section, sections, tag_chapter
            )
            items.append(
                {
                    "gap_key": f"{version}:{point['point_id']}",
                    "question_id": int(qid),
                    "evidence_version_id": version,
                    "point_id": str(point["point_id"]),
                    "part_id": str(point["part_id"]),
                    "number": int(point["number"]),
                    "target": str(point["target"]),
                    "observable_evidence": str(point["observable_evidence"]),
                    "chapter_key": chapter_key,
                    "section_key": section_key,
                    "direct_keys": list(point["direct_keys"]),
                }
            )
    return items


def _locate(
    point: Mapping[str, Any],
    tags: list[str],
    tag_section: Mapping[str, str],
    sections: Mapping[str, tuple[str, Mapping[str, Any]]],
    tag_chapter: Mapping[str, str],
) -> tuple[str, str]:
    """(section_key, chapter_key); ``("", "")`` means the point is unlocated."""
    for key in point.get("direct_keys", []):
        if key in sections:
            return str(key), sections[str(key)][0]
        node = curriculum_knowledge_node(key)
        if node is not None and int(node.get("level") or 0) >= 3:
            ancestors = curriculum_knowledge_ancestors(key)
            if ancestors and ancestors[0] in sections:
                return str(ancestors[0]), sections[str(ancestors[0])][0]
    for value in tags:
        section_key = tag_section.get(value)
        if section_key is not None:
            return section_key, sections[section_key][0]
    for value in tags:
        chapter_key = tag_chapter.get(value)
        if chapter_key is not None:
            return "", chapter_key
    return "", ""


__all__ = ["gap_items", "teacher_protected_versions"]
