from __future__ import annotations

import math
from pathlib import Path

from backend.repositories.access import GradingRepositoryAccess
from backend.students.exam_evidence import student_exam_evidence
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
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
        session_id = int(row["session_id"])
        session_map[session_id] = {
            "session_id": session_id,
            "session_name": row["session_name"],
            "exam_created_at": row.get("exam_created_at"),
        }
    sessions = sorted(session_map.values(), key=lambda row: (row["exam_created_at"] or "", row["session_id"]))
    selected = set(session_map) if session_ids is None else set(session_ids)
    if not selected.issubset(session_map):
        raise ValueError("所选考试不属于当前学期的学生成绩范围，请刷新考试列表")
    rows = [row for row in rows if int(row["session_id"]) in selected]
    rows.sort(key=lambda row: (
        row.get("exam_created_at") or "", int(row["session_id"]),
        _question_order(str(row["question_id"])), int(row["detail_id"]),
    ))
    bank_ids = sorted({int(row["bank_question_id"]) for row in rows if row.get("bank_question_id") is not None})
    available = {
        int(row["id"]) for row in QuestionBankReadService(question_bank_db_path).get_questions(bank_ids)
    } if bank_ids and question_bank_db_path.exists() else set()
    books = {int(student["id"]): dict(student=student, sections=[], missing_items=[], wrong_count=0) for student in students}
    seen = {student_id: set() for student_id in books}
    seen_source = set()
    for row in rows:
        awarded = row.get("score_awarded")
        maximum = row.get("max_score")
        # Exclude unknown scores instead of interpreting them as zero.
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
        book = books[student_id]
        bank_id = row.get("bank_question_id")
        if bank_id not in available:
            book["wrong_count"] += 1
            book["missing_items"].append({
                "student_id": student_id, "student_name": book["student"]["name"],
                "session_id": session_id, "session_name": row["session_name"], "question_id": parent_id,
            })
            continue
        if bank_id in seen[student_id]:
            continue
        seen[student_id].add(bank_id)
        book["wrong_count"] += 1
        if not book["sections"] or book["sections"][-1]["session_id"] != session_id:
            book["sections"].append(dict(session_map[session_id], question_ids=[]))
        book["sections"][-1]["question_ids"].append(bank_id)
    return {
        "students": students, "sessions": sessions,
        "session_ids": [row["session_id"] for row in sessions if row["session_id"] in selected],
        "semester_label": str(volume.get("label") or volume.get("title") or f"{volume['grade']} {volume['semester']}"),
        "books": list(books.values()),
        "question_count": sum(len(ids) for ids in seen.values()),
        "missing_items": [item for book in books.values() for item in book["missing_items"]],
    }
