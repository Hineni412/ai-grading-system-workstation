from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.repositories.access import GradingRepositoryAccess
from question_bank.services.source_question_link_service import SourceQuestionLinkService
from question_id_contract import question_id_coordinates


def confirmed_bank_question_links(
    question_bank_db_path: Path, session_ids: list[int],
) -> dict[tuple[int, str], int]:
    path = Path(question_bank_db_path)
    if not session_ids or not path.exists():
        return {}
    links: dict[tuple[int, str], int] = {}
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except (OSError, sqlite3.Error):
        return {}
    try:
        conn.row_factory = sqlite3.Row
        service = SourceQuestionLinkService(path, external_connection=conn)
        for session_id in session_ids:
            try:
                session_links = service.list_links(session_id)
            except (OSError, sqlite3.Error, ValueError):
                continue
            for link in session_links:
                source_id = str(link.get("source_question_id") or "").strip()
                bank_id = link.get("bank_question_id")
                if link.get("status") == "confirmed" and source_id and bank_id is not None:
                    links[(session_id, source_id)] = int(bank_id)
    finally:
        conn.close()
    return links


def lookup_bank_question_id(links, session_id: int, question_id: str) -> int | None:
    bank_id = links.get((session_id, question_id))
    if bank_id is not None:
        return bank_id
    coordinates = question_id_coordinates(question_id)
    if coordinates is None or coordinates[1] is None:
        return None
    return links.get((session_id, f"Q{coordinates[0]}"))


def student_exam_evidence(
    db: GradingRepositoryAccess,
    question_bank_db_path: Path,
    student_ids: list[int],
    curriculum_volume_id: str | None = None,
) -> list[dict]:
    if not student_ids:
        return []
    rows = db.results.get_active_assessment_evidence(student_ids=student_ids)
    if curriculum_volume_id is not None:
        session_ids = {
            int(item["id"]) for item in db.sessions.list_grading_sessions()
            if curriculum_volume_id and item.get("curriculum_volume_id") == curriculum_volume_id
            and not item.get("is_deleted")
        }
        rows = [row for row in rows if int(row["session_id"]) in session_ids]
    links = confirmed_bank_question_links(
        question_bank_db_path, sorted({int(row["session_id"]) for row in rows}),
    )
    return [dict(row, bank_question_id=lookup_bank_question_id(
        links, int(row["session_id"]), str(row.get("question_id") or ""),
    )) for row in rows]
