"""One read-only knowledge placement and ordering contract for print materials."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.question_types import is_training_target
from question_bank.services.question_skill_index import (
    build_skill_snapshot, short_node_name, skill_anchor_ids,
)
from question_bank.services.similarity_service import text_similarity
from question_bank.taxonomy.curriculum_catalog import curriculum_volume


@dataclass(frozen=True)
class Placement:
    chapter_id: str
    chapter_order: int
    chapter_label: str
    section_id: str
    section_order: int
    section_label: str
    skill_key: str
    skill_name: str


@dataclass(frozen=True)
class OrderEntry:
    question_id: int
    difficulty: float | None
    question_type: str
    question_text: str


@dataclass(frozen=True)
class KnowledgeSection:
    title: str
    section_id: str | None
    question_ids: list[int]


def _volume_placements(volume) -> dict[str, Placement]:
    result = {}
    for chapter in volume['chapters']:
        base = dict(chapter_id=chapter['knowledge_id'], chapter_order=chapter['order'],
                    chapter_label=chapter['label'], skill_key='', skill_name='')
        result[chapter['knowledge_id']] = Placement(
            **base, section_id='', section_order=10**9, section_label='本章综合')
        for section in chapter['sections']:
            result[section['knowledge_id']] = Placement(
                **base, section_id=section['knowledge_id'], section_order=section['order'],
                section_label=section['label'])
    return result


def skill_placements(conn: sqlite3.Connection, skill_keys, volume_id: str) -> dict[str, Placement]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError('请选择有效的教学学期')
    anchors = _volume_placements(volume)
    result = {}
    keys = sorted(set(skill_keys))
    for offset in range(0, len(keys), 400):
        batch = keys[offset:offset + 400]
        placeholders = ','.join('?' for _ in batch)
        rows = conn.execute(
            f"SELECT stable_key,display_name,curriculum_anchors_json FROM knowledge_graph_node_profiles "
            f"WHERE release_id=(SELECT release_id FROM knowledge_graph_releases WHERE status='active') "
            f"AND status='active' AND stable_key IN ({placeholders})", batch)
        for row in rows:
            ids = skill_anchor_ids(row, volume)
            anchor = next((key for key in ids if anchors[key].section_id), ids[0] if ids else None)
            if anchor is None:
                continue
            placement = anchors[anchor]
            result[row['stable_key']] = Placement(
                chapter_id=placement.chapter_id, chapter_order=placement.chapter_order,
                chapter_label=placement.chapter_label, section_id=placement.section_id,
                section_order=placement.section_order, section_label=placement.section_label,
                skill_key=row['stable_key'], skill_name=short_node_name(row['display_name']))
    return result


def question_primary_skills(conn: sqlite3.Connection, db_path: Path, data_root: Path | None,
                            question_ids) -> dict[int, str]:
    if not question_ids:
        return {}
    snapshot = build_skill_snapshot(conn, db_path, data_root,
                                    question_ids=tuple(sorted(set(question_ids))))
    result = {qid: next((key for key in skills if key.startswith('sk_')), '')
              for qid, skills in snapshot['by_question'].items()}
    try:
        resolver = CurrentKnowledgeResolver.from_connection(conn)
    except Exception:
        resolver = None
    if resolver is not None:
        result = {qid: key if is_training_target(key, resolver) else "" for qid, key in result.items()}
        # On a type release the primary training target is the question's type;
        # skill_placements still places it through its section anchor.
        ids = sorted(set(question_ids))
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            placeholders = ','.join('?' for _ in batch)
            for row in conn.execute(
                    f"SELECT question_id,tag_value FROM question_tags WHERE tag_type='knowledge_point' "
                    f"AND question_id IN ({placeholders}) ORDER BY id", batch):
                value = str(row['tag_value'])
                qid = int(row['question_id'])
                if is_training_target(value, resolver) and not is_training_target(result.get(qid, ''), resolver):
                    result[qid] = value
    return result


def section_placements(conn: sqlite3.Connection, question_ids, volume_id: str) -> dict[int, Placement]:
    volume = curriculum_volume(volume_id=volume_id)
    if volume is None:
        raise ValueError('请选择有效的教学学期')
    anchors = _volume_placements(volume)
    names = {section['display_name']: anchors[section['knowledge_id']]
             for chapter in volume['chapters'] for section in chapter['sections']}
    result = {}
    ids = sorted(set(question_ids))
    for offset in range(0, len(ids), 400):
        batch = ids[offset:offset + 400]
        placeholders = ','.join('?' for _ in batch)
        for row in conn.execute(
            f"SELECT question_id,tag_value FROM question_tags WHERE tag_type='curriculum_section' "
            f"AND question_id IN ({placeholders}) ORDER BY id", batch):
            if row['tag_value'] in names:
                result.setdefault(int(row['question_id']), names[row['tag_value']])
    return result


def parsed_difficulty(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and 1 <= parsed <= 10 else None


def _question_sort(entry: OrderEntry):
    kind = {'选择题': 0, '多选题': 0, 'choice': 0, 'single_choice': 0,
            'multiple_choice': 0, 'multi_choice': 0,
            '填空题': 1, 'fill_blank': 1, 'blank': 1}.get(entry.question_type, 2)
    return (parsed_difficulty(entry.difficulty) or math.inf, kind, entry.question_id)


def _similar_neighbors(entries: list[OrderEntry]) -> list[OrderEntry]:
    remaining = sorted(entries, key=_question_sort)
    for index in range(len(remaining) - 1):
        current = remaining[index]
        scores = [(text_similarity(current.question_text, entry.question_text), position)
                  for position, entry in enumerate(remaining[index + 1:], index + 1)]
        score, position = max(scores, key=lambda pair: (pair[0], -pair[1]))
        if score >= .7:
            remaining.insert(index + 1, remaining.pop(position))
    return remaining


def knowledge_sections(entries: Sequence[OrderEntry],
                       placements: Mapping[int, Placement | None]) -> list[KnowledgeSection]:
    sections = defaultdict(list)
    section_info = {}
    for entry in entries:
        placement = placements.get(entry.question_id)
        key = (placement.chapter_order, placement.section_order, placement.chapter_id,
               placement.section_id) if placement else (math.inf, math.inf, '', '')
        sections[key].append(entry)
        section_info[key] = placement
    result = []
    for key in sorted(sections):
        groups = defaultdict(list)
        for entry in sections[key]:
            placement = placements.get(entry.question_id)
            groups[(placement.skill_key, placement.skill_name) if placement else ('', '')].append(entry)
        def group_order(pair):
            skill, name = pair
            return (not bool(skill), min(_question_sort(entry)[0] for entry in groups[pair]), name, skill)
        ordered = [entry.question_id for group in sorted(groups, key=group_order)
                   for entry in _similar_neighbors(groups[group])]
        placement = section_info[key]
        result.append(KnowledgeSection(
            title=f'{placement.chapter_label} · {placement.section_label}' if placement else '未归入章节',
            section_id=(placement.section_id or placement.chapter_id) if placement else None,
            question_ids=ordered))
    return result
