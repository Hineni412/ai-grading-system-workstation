from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict
from pathlib import Path

from backend.repositories.access import GradingRepositoryAccess
from backend.students.exam_evidence import student_exam_evidence
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
from question_bank.document_pipeline.legacy_exports import data_root_for_database
from question_bank.services.knowledge_order import (
    OrderEntry, knowledge_sections, parsed_difficulty, question_primary_skills,
    section_placements, skill_placements,
)
from question_id_contract import question_id_coordinates


def _question_order(question_id: str) -> tuple:
    coordinates = question_id_coordinates(question_id)
    return (coordinates[0], coordinates[1] or 0, question_id) if coordinates else (10**9, 0, question_id)


def _parent_id(question_id: str) -> str:
    coordinates = question_id_coordinates(question_id)
    return f"Q{coordinates[0]}" if coordinates else question_id


def build_wrong_question_books(
    db: GradingRepositoryAccess,
    question_bank_db_path: Path,
    student_ids: list[int],
    curriculum_volume_id: str,
    session_ids: list[int] | None = None,
    scope_keys=(),
) -> dict:
    volume = curriculum_volume(volume_id=curriculum_volume_id)
    if volume is None:
        raise ValueError("请先选择有效的教学学期")
    requested = set(student_ids)
    students = [row for row in db.students.list_students() if int(row["id"]) in requested]
    if not requested or len(students) != len(requested):
        raise ValueError("所选学生不存在，请刷新学生名单")
    rows = student_exam_evidence(db, question_bank_db_path, sorted(requested), curriculum_volume_id)
    session_map = {}
    for row in rows:
        sid = int(row["session_id"])
        session_map[sid] = dict(session_id=sid, session_name=row["session_name"],
                                exam_created_at=row.get("exam_created_at"))
    sessions = sorted(session_map.values(), key=lambda row: (row["exam_created_at"] or "", row["session_id"]))
    selected = set(session_map) if session_ids is None else set(session_ids)
    if not selected.issubset(session_map):
        raise ValueError("所选考试不属于当前学期的学生成绩范围，请刷新考试列表")
    rows.sort(key=lambda row: (row.get("exam_created_at") or "", int(row["session_id"]),
                               _question_order(str(row["question_id"])), int(row["detail_id"])))
    bank_ids = sorted({int(row["bank_question_id"]) for row in rows if row.get("bank_question_id") is not None})
    questions = {int(row["id"]): row for row in QuestionBankReadService(question_bank_db_path).get_questions(bank_ids)}         if bank_ids and question_bank_db_path.exists() else {}
    placements = {}
    if questions:
        conn = sqlite3.connect(question_bank_db_path.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            skills = question_primary_skills(conn, question_bank_db_path,
                                            data_root_for_database(question_bank_db_path), list(questions))
            located = skill_placements(conn, [key for key in skills.values() if key], curriculum_volume_id)
            fallback = section_placements(conn, [qid for qid in questions if not skills.get(qid)], curriculum_volume_id)
            placements = {qid: located.get(skills[qid]) if skills.get(qid) else fallback.get(qid) for qid in questions}
        finally:
            conn.close()
    books = {int(student["id"]): dict(student=student, sections=[], notes={}, sources={},
              missing_items=[], wrong_count=0, out_of_scope_count=0) for student in students}
    seen_source = set()
    seen_questions = {student_id: set() for student_id in books}
    counts = {sid: set() for sid in session_map}
    scope = set(scope_keys)
    for row in rows:
        awarded, maximum = row.get("score_awarded"), row.get("max_score")
        if awarded is None or maximum is None:
            continue
        if not math.isfinite(float(awarded)) or not math.isfinite(float(maximum)) or float(awarded) >= float(maximum):
            continue
        student_id, session_id = int(row["student_id"]), int(row["session_id"])
        parent_id = _parent_id(str(row["question_id"]))
        source_key = student_id, session_id, parent_id
        if source_key in seen_source:
            continue
        seen_source.add(source_key)
        bank_id = row.get("bank_question_id")
        placement = placements.get(bank_id)
        in_scope = not scope or placement is not None and bool({placement.section_id, placement.chapter_id} & scope)
        if bank_id in questions and in_scope:
            counts[session_id].add((student_id, bank_id))
        if session_id not in selected:
            continue
        book = books[student_id]
        if bank_id not in questions:
            book["wrong_count"] += 1
            book["missing_items"].append(dict(student_id=student_id, student_name=book["student"]["name"],
                session_id=session_id, session_name=row["session_name"], question_id=parent_id))
            continue
        if bank_id not in seen_questions[student_id]:
            seen_questions[student_id].add(bank_id)
            book["wrong_count"] += 1
            if not in_scope:
                book["out_of_scope_count"] += 1
        if not in_scope:
            continue
        coordinates = question_id_coordinates(parent_id)
        label = f"第{coordinates[0]}题" if coordinates else parent_id
        book["sources"].setdefault(bank_id, []).append(dict(session_map[session_id], question_id=parent_id, question_label=label))
    empty_students = []
    for book in books.values():
        entries = [OrderEntry(qid, parsed_difficulty(questions[qid].get('difficulty')),
                             str(questions[qid].get('question_type') or ''),
                             str(questions[qid].get('question_text') or '')) for qid in book['sources']]
        book['sections'] = [asdict(section) for section in knowledge_sections(entries, placements)]
        for qid, sources in book['sources'].items():
            placement = placements.get(qid)
            skill_note = f"技能：{placement.skill_name} · " if placement and placement.skill_name else ''
            source_note = '、'.join(f"{source['session_name']} {source['question_label']}" for source in sources)
            book['notes'][qid] = f"{skill_note}来源：{source_note}"
        if not entries:
            reason = '没有错题' if not book['wrong_count'] else '错题均不在所选章节'                 if book['out_of_scope_count'] else '错题均缺少题库原题'
            empty_students.append(dict(student_id=book['student']['id'], student_name=book['student']['name'], reason=reason))
    return {
        "students": students, "sessions": sessions,
        "session_ids": [row["session_id"] for row in sessions if row["session_id"] in selected],
        "semester_label": str(volume.get("label") or volume.get("title") or f"{volume['grade']} {volume['semester']}"),
        "books": list(books.values()),
        "question_count": sum(len(book['sources']) for book in books.values()),
        "missing_items": [item for book in books.values() for item in book['missing_items']],
        "session_wrong_counts": {str(sid): len(items) for sid, items in counts.items()},
        "out_of_scope_count": sum(book['out_of_scope_count'] for book in books.values()),
        "empty_students": empty_students,
    }
